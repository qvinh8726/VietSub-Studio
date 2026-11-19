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
"""
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
        cues.append((start, end, body))
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
    return f"&H{alpha:02X}{color[5:7]}{color[3:5]}{color[1:3]}".upper()


def _timestamp(milliseconds, *, ceil=False):
    centiseconds = (milliseconds + 9) // 10 if ceil else milliseconds // 10
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def _style(name, opts, font_size, scale, *, banner=False):
    preset = _PRESETS[opts["subtitle_preset"]]
    # Social's backplate uses the configured back_color at 75% opacity. With
    # normal/anime borders that same color supplies outline and drop shadow.
    back = _ass_color(opts["back_color"], 64 if preset["border"] == 3 or banner else 0)
    values = [name, opts["font"], _decimal(font_size), _ass_color(opts["text_color"]),
              _ass_color(opts["text_color"]), back, back,
              -1 if banner else preset["bold"], 0, 0, 0, 100, 100,
              _decimal(preset["spacing"] * scale), 0, 3 if banner else preset["border"],
              _decimal(opts["outline"] * scale), _decimal(opts["shadow"] * scale),
              5, 0, 0, 0, 1]
    return "Style: " + ",".join(map(str, values))


def _event(layer, start, end, style, tags, text, *, margins=(0, 0)):
    left, right = margins
    return (f"Dialogue: {layer},{start},{end},{style},,{left},{right},0,,"
            f"{{{tags}}}{_escape_text(text)}")


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


def write_ass(srt_path, output_path, width, height, options):
    """Atomically write an ASS sidecar and return ``str(output_path)``.

    ``width``/``height`` are SOURCE pixels, not the graph's output size. crop
    coordinates remain normalized source geometry. ``font_size``, ``outline``
    and ``shadow`` scale with the fitted source in vertical mode; subtitle top/
    bottom and fixed banners are positioned on the final canvas. Crop placement
    centers captions in the padded ROI after the source-to-output transform;
    projects without a crop fall back to bottom placement, never a fake ROI.

    For banner-only renders set subtitle_enabled=False and pass srt_path=None.
    A supplied SRT is always validated, even with subtitles disabled. Fixed
    banners run from 0 through ASS's 999-hour supported timeline (not merely to
    the last cue), and NEVER become a drawtext/FFmpeg expression. Overflow text
    wraps in libass; unusually long text/very small ROIs may exceed their area.
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
        for start, end, text in cues:
            rows.append(_event(1, _timestamp(start), _timestamp(end, ceil=True), "Subtitle", tags, text, margins=margins))
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
