"""Stdlib-only, injection-safe UTF-8 SRT to ASS conversion for local libass.

Use source width/height and exactly the same options as build_video_graph.
PlayRes matches the graph's returned canvas, including vertical reframing. The
font size is in source pixels and follows the fitted foreground scale. Local
font fallback (notably CJK) is performed by libass from the supplied fonts_dir;
no fonts or optional AI runtimes are imported/downloaded by this module.

Supported SRT: positive numeric cue IDs, HH:MM:SS,mmm (or .mmm), multiline text,
UTF-8/BOM, LF/CRLF. Empty/malformed/reversed/out-of-order cues fail before any
output is replaced. Overlapping cues are retained. SRT HTML is displayed as
literal text, not executable ASS formatting. ASS has centisecond precision;
starts round down and ends round up so a short valid cue never disappears.

Subtitle animation modes:
- ``static``: classic static cues (100% legacy compatible).
- ``karaoke_highlight``: smooth ASS ``\\kf`` sweep from ``text_color``
  (SecondaryColour) to ``subtitle_highlight_color`` (PrimaryColour) paced by
  Vietnamese syllable/punctuation weights and optional fitted TTS durations.
- ``word_pop``: TikTok/CapCut-style chunked captions (``subtitle_max_words_per_chunk``)
  with inline ``\\1c&HBBGGRR&`` highlight and subtle scale pop animation on the
  active word, preserving full ASS injection escaping on every token.
"""
from collections.abc import Mapping
import math
import os
from pathlib import Path
import re
import tempfile

from .filters import _canvas_crop, _decimal, _dimensions, _layout, _validated_options

__all__ = ["write_ass"]

_TIMING = re.compile(r"(\d{1,3}):([0-5]\d):([0-5]\d)[,.](\d{3})\s+-->\s+"
                     r"(\d{1,3}):([0-5]\d):([0-5]\d)[,.](\d{3})\Z")
_PRESETS = {
    "standard": {"bold": 0, "border": 1, "spacing": 0},
    "anime": {"bold": -1, "border": 1, "spacing": 0.4},
    "social": {"bold": -1, "border": 3, "spacing": 0},
}
_STYLE_FORMAT = ("Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                 "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
                 "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
_STRONG_PUNCT = frozenset(".!?。！？…")
_SOFT_PUNCT = frozenset(",;:،、—–")


def _parse_srt(path):
    with open(path, "rb") as handle:
        raw = handle.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("SRT exceeds the 8 MiB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("SRT must be UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if any(ord(char) < 32 and char not in "\n\t" for char in text) or "\x7f" in text:
        raise ValueError("SRT contains control characters")
    if not text.strip():
        raise ValueError("SRT has no cues")
    cues, seen = [], set()
    previous_start = -1
    for block in re.split(r"\n(?:[ \t]*\n)+", text.strip()):
        lines = block.split("\n")
        if len(lines) < 3 or not re.fullmatch(r"[0-9]+", lines[0].strip()):
            raise ValueError("SRT cue requires a numeric ID, timing and nonempty text")
        if len(lines[0].strip()) > 12:
            raise ValueError("SRT cue ID is too long")
        cue_id = int(lines[0].strip())
        if cue_id <= 0 or cue_id in seen:
            raise ValueError("SRT cue IDs must be positive and unique")
        seen.add(cue_id)
        match = _TIMING.fullmatch(lines[1].strip())
        if not match:
            raise ValueError(f"Invalid SRT timing in cue {cue_id}")
        values = tuple(map(int, match.groups()))
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        if end <= start or start < previous_start:
            raise ValueError(f"Reversed or out-of-order SRT timing in cue {cue_id}")
        body = "\n".join(lines[2:]).strip()
        if not body or len(body) > 10000 or any(_TIMING.fullmatch(line.strip()) for line in lines[2:]):
            raise ValueError(f"Invalid SRT text or missing cue separator in cue {cue_id}")
        cues.append((cue_id, start, end, body))
        previous_start = start
        if len(cues) > 100000:
            raise ValueError("SRT exceeds the cue limit")
    return cues


def _escape_text(text):
    # libass explicitly supports \{ and \}. It has no '\\' escape: blindly
    # doubling a backslash still lets the second slash in input '\N' create a
    # linebreak. A zero-width WORD JOINER after literal slashes prevents \N,
    # \n and \h interpretation while keeping the visible ASCII slash glyph.
    escaped = []
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if char == "\\":
            escaped.append("\\\u2060")
        elif char in "{}":
            escaped.append("\\" + char)
        elif char == "\n":
            escaped.append(r"\N")
        elif char == "\t":
            escaped.append("    ")
        elif ord(char) < 32 or ord(char) == 127:
            raise ValueError("Subtitle text contains control characters")
        else:
            escaped.append(char)
    return "".join(escaped)


def _ass_color(color, alpha=0):
    """ASS Style color: &HAABBGGRR (alpha + BGR)."""
    return f"&H{alpha:02X}{color[5:7]}{color[3:5]}{color[1:3]}".upper()


def _ass_inline_color(color):
    """ASS inline override color for \\1c: &HBBGGRR& (6 BGR hex digits, no alpha)."""
    return f"&H{color[5:7]}{color[3:5]}{color[1:3]}&".upper()


def _timestamp_cs(centiseconds):
    centiseconds = max(0, int(centiseconds))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def _timestamp(milliseconds, *, ceil=False):
    centiseconds = (milliseconds + 9) // 10 if ceil else milliseconds // 10
    return _timestamp_cs(centiseconds)


def _style(name, opts, font_size, scale, *, banner=False):
    preset = _PRESETS[opts["subtitle_preset"]]
    # Social's backplate uses the configured back_color at 75% opacity. With
    # normal/anime borders that same color supplies outline and drop shadow.
    back = _ass_color(opts["back_color"], 64 if preset["border"] == 3 or banner else 0)
    if not banner and opts["subtitle_animation"] == "karaoke_highlight":
        # In ASS \kf karaoke, libass sweeps from SecondaryColour (base text_color)
        # to PrimaryColour (subtitle_highlight_color).
        primary = _ass_color(opts["subtitle_highlight_color"])
        secondary = _ass_color(opts["text_color"])
    else:
        primary = _ass_color(opts["text_color"])
        secondary = _ass_color(opts["text_color"])
    values = [name, opts["font"], _decimal(font_size), primary, secondary, back, back,
              -1 if banner else preset["bold"], 0, 0, 0, 100, 100,
              _decimal(preset["spacing"] * scale), 0, 3 if banner else preset["border"],
              _decimal(opts["outline"] * scale), _decimal(opts["shadow"] * scale),
              5, 0, 0, 0, 1]
    return "Style: " + ",".join(map(str, values))


def _event(layer, start, end, style, tags, text, *, margins=(0, 0), pre_escaped=False):
    left, right = margins
    body = text if pre_escaped else _escape_text(text)
    return (f"Dialogue: {layer},{start},{end},{style},,{left},{right},0,,"
            f"{{{tags}}}{body}")


def _placement(opts, width, height, layout, font_size):
    out_w, out_h = layout["width"], layout["height"]
    position = opts["subtitle_position"]
    # Old projects without an OCR ROI still render with the normalized default
    # 'crop' preference. There is no region to follow: use a safe bottom anchor.
    if position == "crop" and opts["crop_box"] is None:
        position = "bottom"
    if position == "crop":
        box = _canvas_crop(opts["crop_box"], width, height, opts, layout)
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        left, right = math.floor(box["x"]), math.floor(out_w - box["x"] - box["width"])
        alignment = 5
    else:
        x = out_w / 2
        safe = min(out_h / 4, max(4, font_size * 0.6, out_h * 0.035))
        y = safe if position == "top" else out_h - safe
        alignment = 8 if position == "top" else 2
        left = right = round(out_w * 0.04)
    return f"\\an{alignment}\\pos({_decimal(x)},{_decimal(y)})", (max(0, left), max(0, right))


def _tokenize_cue(text):
    """Split cue text into word tokens while recording explicit line breaks."""
    tokens = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    has_prior_token = False
    for line in lines:
        words = re.findall(r"\S+", line)
        if not words:
            continue
        for idx, word in enumerate(words):
            tokens.append({
                "text": word,
                "escaped": _escape_text(word),
                "newline_before": has_prior_token and idx == 0,
            })
        has_prior_token = True
    return tokens


def _token_weight(word):
    """Vietnamese syllable/character + trailing punctuation pause weight."""
    core_len = sum(1 for ch in word if ch.isalnum())
    weight = 1.0 + 0.35 * min(max(1, core_len), 12)
    stripped = word.rstrip("\"'”’)]}》」")
    if stripped.endswith("...") or stripped.endswith("…"):
        weight += 1.6
    elif stripped and stripped[-1] in _STRONG_PUNCT:
        weight += 1.3
    elif stripped and stripped[-1] in _SOFT_PUNCT:
        weight += 0.75
    return weight


def _allocate_centiseconds(weights, total_cs):
    """Distribute ``total_cs`` across tokens via Largest Remainder without drift."""
    count = len(weights)
    if count == 0:
        return []
    total_cs = max(1, int(total_cs))
    if total_cs < count:
        return [1 if idx < total_cs else 0 for idx in range(count)]

    # Guarantee at least 1 cs per token, then apportion the remainder proportionally.
    alloc = [1] * count
    remaining = total_cs - count
    if remaining == 0:
        return alloc
    weight_sum = sum(weights) or float(count)
    exact = [remaining * (w / weight_sum) for w in weights]
    floors = [math.floor(val) for val in exact]
    for idx in range(count):
        alloc[idx] += floors[idx]
    leftover = remaining - sum(floors)
    order = sorted(range(count), key=lambda i: (exact[i] - floors[i], weights[i]), reverse=True)
    for idx in order[:leftover]:
        alloc[idx] += 1
    return alloc


def _chunk_tokens(tokens, max_words):
    """Group tokens into balanced visual chunks for ``word_pop`` captions."""
    if not tokens:
        return []
    chunks = []
    current = []
    min_soft_break = max(2, max_words - 1)
    for tok in tokens:
        if current and (len(current) >= max_words or tok["newline_before"]):
            chunks.append(current)
            current = []
        current.append(tok)
        stripped = tok["text"].rstrip("\"'”’)]}》」")
        ends_clause = bool(stripped) and (
            stripped.endswith("...") or stripped[-1] in _STRONG_PUNCT or stripped[-1] in _SOFT_PUNCT
        )
        if ends_clause and len(current) >= min_soft_break:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)

    # Avoid an orphan single word at the end if the preceding chunk has >= 3 words
    # and shares no explicit forced newline break.
    if len(chunks) >= 2 and len(chunks[-1]) == 1 and len(chunks[-2]) >= 3 and not chunks[-1][0]["newline_before"]:
        moved = chunks[-2].pop()
        chunks[-1].insert(0, moved)
    return chunks


def _speech_end_cs(vis_start, vis_end, speed, tts_duration_ms):
    vis_start_cs = vis_start // 10
    vis_end_cs = max(vis_start_cs + 1, (vis_end + 9) // 10)
    if tts_duration_ms is not None and not isinstance(tts_duration_ms, bool):
        try:
            dur = float(tts_duration_ms)
        except (TypeError, ValueError):
            dur = 0.0
        if math.isfinite(dur) and dur > 0:
            speech_end_ms = min(vis_end, vis_start + max(10, round(dur / speed)))
            speech_cs = max(vis_start_cs + 1, min(vis_end_cs, (speech_end_ms + 9) // 10))
            return vis_start_cs, vis_end_cs, speech_cs
    return vis_start_cs, vis_end_cs, vis_end_cs


def _karaoke_events(start_ms, end_ms, text, tags, margins, speed, tts_duration_ms):
    vis_start = math.floor(start_ms / speed)
    vis_end = math.ceil(end_ms / speed)
    vis_start_cs, vis_end_cs, speech_cs = _speech_end_cs(vis_start, vis_end, speed, tts_duration_ms)
    tokens = _tokenize_cue(text)
    if not tokens:
        return [_event(1, _timestamp_cs(vis_start_cs), _timestamp_cs(vis_end_cs), "Subtitle", tags, text, margins=margins)]
    weights = [_token_weight(tok["text"]) for tok in tokens]
    durations = _allocate_centiseconds(weights, speech_cs - vis_start_cs)
    parts = []
    for idx, (tok, dur_cs) in enumerate(zip(tokens, durations)):
        if idx > 0:
            parts.append(r"\N" if tok["newline_before"] else " ")
        parts.append(f"{{\\kf{dur_cs}}}{tok['escaped']}")
    body = "".join(parts)
    return [_event(1, _timestamp_cs(vis_start_cs), _timestamp_cs(vis_end_cs), "Subtitle", tags, body,
                   margins=margins, pre_escaped=True)]


def _word_pop_events(start_ms, end_ms, text, tags, margins, opts, tts_duration_ms):
    speed = opts["speed"]
    vis_start = math.floor(start_ms / speed)
    vis_end = math.ceil(end_ms / speed)
    vis_start_cs, vis_end_cs, speech_cs = _speech_end_cs(vis_start, vis_end, speed, tts_duration_ms)
    tokens = _tokenize_cue(text)
    if not tokens:
        return [_event(1, _timestamp_cs(vis_start_cs), _timestamp_cs(vis_end_cs), "Subtitle", tags, text, margins=margins)]

    weights = [_token_weight(tok["text"]) for tok in tokens]
    durations = _allocate_centiseconds(weights, speech_cs - vis_start_cs)
    chunks = _chunk_tokens(tokens, opts["subtitle_max_words_per_chunk"])
    base_col = _ass_inline_color(opts["text_color"])
    hi_col = _ass_inline_color(opts["subtitle_highlight_color"])

    # Assign contiguous [start_cs, end_cs] slots across all tokens.
    cursor = vis_start_cs
    slots = []
    for dur_cs in durations:
        slots.append([cursor, cursor + dur_cs])
        cursor += dur_cs
    # Hold the final spoken word visible until the full cue end (no premature flash-off).
    last_nonzero = next((i for i in range(len(slots) - 1, -1, -1) if slots[i][1] > slots[i][0]), None)
    if last_nonzero is not None and slots[last_nonzero][1] < vis_end_cs:
        slots[last_nonzero][1] = vis_end_cs

    events = []
    tok_offset = 0
    for chunk in chunks:
        chunk_len = len(chunk)
        chunk_events_before = len(events)
        for local_idx in range(chunk_len):
            s_cs, e_cs = slots[tok_offset + local_idx]
            if e_cs <= s_cs:
                continue
            pop_ms = min(80, max(20, (e_cs - s_cs) * 10))
            parts = []
            for idx, tok in enumerate(chunk):
                if idx > 0:
                    parts.append(r"\N" if tok["newline_before"] else " ")
                if idx == local_idx:
                    parts.append(
                        f"{{\\1c{hi_col}\\fscx114\\fscy114\\t(0,{pop_ms},\\fscx106\\fscy106)}}"
                        f"{tok['escaped']}"
                        f"{{\\1c{base_col}\\fscx100\\fscy100}}"
                    )
                else:
                    parts.append(tok["escaped"])
            events.append(_event(
                1,
                _timestamp_cs(s_cs),
                _timestamp_cs(e_cs),
                "Subtitle",
                tags,
                "".join(parts),
                margins=margins,
                pre_escaped=True,
            ))
        tok_offset += chunk_len
        # Safety net: if every token in the only chunk had 0 cs, emit one full cue dialogue.
        if len(chunks) == 1 and len(events) == chunk_events_before:
            body = " ".join(tok["escaped"] for tok in chunk)
            events.append(_event(1, _timestamp_cs(vis_start_cs), _timestamp_cs(vis_end_cs), "Subtitle", tags,
                                 f"{{\\1c{hi_col}}}{body}{{\\1c{base_col}}}", margins=margins, pre_escaped=True))
    return events


def write_ass(srt_path, output_path, width, height, options, *, cue_timings=None):
    """Atomically write an ASS sidecar and return ``str(output_path)``.

    ``width``/``height`` are SOURCE pixels, not the graph's output size. crop
    coordinates remain normalized source geometry. ``font_size``, ``outline``
    and ``shadow`` scale with the fitted source in vertical mode; subtitle top/
    bottom and fixed banners are positioned on the final canvas. Crop placement
    centers captions in the padded ROI after the source-to-output transform;
    projects without a crop fall back to bottom placement, never a fake ROI.

    ``cue_timings`` optionally maps ``cue_index -> duration_ms`` (e.g. from TTS
    alignment) to pace ``karaoke_highlight`` / ``word_pop`` word timing accurately.
    """
    width, height = _dimensions(width, height)
    opts = _validated_options(options)
    layout = _layout(width, height, opts)
    if srt_path is None:
        if opts["subtitle_enabled"]:
            raise ValueError("Enabled subtitles require srt_path")
        cues = []
    else:
        if os.path.normcase(os.path.abspath(srt_path)) == os.path.normcase(os.path.abspath(output_path)):
            raise ValueError("ASS output cannot replace the input SRT")
        cues = _parse_srt(srt_path)
    timings = cue_timings if isinstance(cue_timings, Mapping) else {}
    scale = min(layout["foreground_width"] / width, layout["foreground_height"] / height)
    font_size = max(1, opts["font_size"] * scale)
    tags, margins = _placement(opts, width, height, layout, font_size) if opts["subtitle_enabled"] else ("", (0, 0))
    rows = ["[Script Info]", "ScriptType: v4.00+", "WrapStyle: 0", "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709", f"PlayResX: {layout['width']}", f"PlayResY: {layout['height']}",
            "", "[V4+ Styles]", "Format: " + _STYLE_FORMAT,
            _style("Subtitle", opts, font_size, scale), _style("Banner", opts, font_size, scale, banner=True),
            "", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    if opts["subtitle_enabled"]:
        animation = opts["subtitle_animation"]
        for cue_id, start, end, text in cues:
            tts_dur = timings.get(cue_id)
            if animation == "karaoke_highlight":
                rows.extend(_karaoke_events(start, end, text, tags, margins, opts["speed"], tts_dur))
            elif animation == "word_pop":
                rows.extend(_word_pop_events(start, end, text, tags, margins, opts, tts_dur))
            else:
                scaled_start = math.floor(start / opts["speed"])
                scaled_end = math.ceil(end / opts["speed"])
                rows.append(_event(1, _timestamp(scaled_start), _timestamp(scaled_end, ceil=True),
                                   "Subtitle", tags, text, margins=margins))
    if opts["banner_text"].strip():
        top = opts["banner_position"] == "top"
        margin = min(layout["height"] / 4, max(4, layout["height"] * 0.035, font_size * 0.6))
        banner_tags = (f"\\an{8 if top else 2}\\pos({_decimal(layout['width'] / 2)},"
                       f"{_decimal(margin if top else layout['height'] - margin)})")
        rows.append(_event(2, "0:00:00.00", "999:59:59.99", "Banner", banner_tags,
                           opts["banner_text"], margins=(round(layout["width"] * 0.04),) * 2))
    payload = "\n".join(rows) + "\n"
    # Everything is validated before creating a temporary file in the target
    # directory; replace is atomic and a failed replace preserves old output.
    destination = Path(output_path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False,
                                         prefix=".ass-", suffix=".tmp", dir=destination.parent) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return str(output_path)
