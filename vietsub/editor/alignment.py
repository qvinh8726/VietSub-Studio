"""Pure, bounded subtitle/audio timeline helpers (no FFmpeg or optional imports).

All cue times are integer milliseconds. ``parse_srt`` takes *contents*, not a
filename, and returns JSON-friendly records. Overlapping subtitles are accepted;
``cue_windows`` clips each speech window at the next cue's start. Speech fitting
never slows a cue down or speeds it up beyond ``max_speed`` (default 1.35x).
"""
from __future__ import annotations

import math
import re
from typing import Mapping, Sequence


class AlignmentError(ValueError):
    """Malformed or excessively large subtitle/timeline input."""


_STAMP = r"(\d{2,3}):([0-5]\d):([0-5]\d)[,.](\d{3})"
_TIMING = re.compile(r"^" + _STAMP + r"[ \t]+-->[ \t]+" + _STAMP + r"$")
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")


def _positive_number(value, name: str) -> float:
    if isinstance(value, bool):
        raise AlignmentError(f"{name} must be a finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AlignmentError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise AlignmentError(f"{name} must be a finite positive number")
    return number


def _milliseconds(value, name: str, *, zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if zero else 1):
        raise AlignmentError(f"{name} must be {'non-negative' if zero else 'positive'} integer milliseconds")
    if value > 86_400_000:
        raise AlignmentError(f"{name} exceeds the 24-hour timeline limit")
    return value


def parse_srt(contents: str, *, max_text: int = 2000, max_cues: int = 1000,
              max_total_text: int = 200_000, max_duration_ms: int = 7_200_000) -> list[dict]:
    """Parse strict SRT, preserving Unicode and multiline text; reject bad input.

    A BOM, CRLF, and dot millisecond separators are accepted. Indices must be
    positive/unique, times chronological, and each end greater than its start.
    Invalid blocks are never silently skipped. ``max_text`` is per-cue characters.
    """
    if not isinstance(contents, str):
        raise AlignmentError("SRT contents must be text")
    for value, name in ((max_text, "max_text"), (max_cues, "max_cues"),
                        (max_total_text, "max_total_text"), (max_duration_ms, "max_duration_ms")):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AlignmentError(f"{name} must be a positive integer")
    if len(contents) > 4 * 1024 * 1024:
        raise AlignmentError("SRT exceeds 4 MiB of text")
    contents = contents.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not contents:
        raise AlignmentError("SRT contains no cues")
    if any((ord(char) < 32 and char not in "\n\t") or 0xD800 <= ord(char) <= 0xDFFF for char in contents):
        raise AlignmentError("SRT contains unsupported control characters or invalid Unicode")
    blocks = re.split(r"\n[ \t]*\n+", contents)
    if len(blocks) > max_cues:
        raise AlignmentError(f"SRT exceeds {max_cues} cues")
    cues, seen, total, previous_start = [], set(), 0, -1
    for position, block in enumerate(blocks, 1):
        lines = block.strip().split("\n")
        if len(lines) < 3 or not re.fullmatch(r"[0-9]{1,9}", lines[0].strip()):
            raise AlignmentError(f"Cue {position}: expected an index, timestamps, and text")
        index = int(lines[0].strip())
        if index <= 0 or index in seen:
            raise AlignmentError(f"Cue {position}: index must be positive and unique")
        seen.add(index)
        timing = _TIMING.fullmatch(lines[1].strip())
        if not timing:
            raise AlignmentError(f"Cue {index}: invalid SRT timestamps")
        numbers = [int(item) for item in timing.groups()]
        start = ((numbers[0] * 60 + numbers[1]) * 60 + numbers[2]) * 1000 + numbers[3]
        end = ((numbers[4] * 60 + numbers[5]) * 60 + numbers[6]) * 1000 + numbers[7]
        if end <= start or start < previous_start or end > max_duration_ms:
            raise AlignmentError(f"Cue {index}: invalid, unordered, or excessive timing")
        text = "\n".join(line.strip() for line in lines[2:]).strip()
        if not text or len(text) > max_text:
            raise AlignmentError(f"Cue {index}: text must contain 1–{max_text} characters")
        total += len(text)
        if total > max_total_text:
            raise AlignmentError(f"SRT exceeds {max_total_text} text characters")
        cues.append({"index": index, "start_ms": start, "end_ms": end, "text": text})
        previous_start = start
    return cues


def atempo_chain(speed: float) -> str:
    """Return an FFmpeg atempo chain whose individual factors lie in [0.5, 2]."""
    remaining = _positive_number(speed, "speed")
    if not 1 / 64 <= remaining <= 64:
        raise AlignmentError("speed must be between 1/64 and 64")
    factors = []
    while remaining > 2.0 + 1e-12:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-12:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.10g}" for factor in factors)


def cue_windows(cues: Sequence[Mapping]) -> list[dict]:
    """Copy cues with ``window_ms``/``window_end_ms``, avoiding the next start."""
    result, previous = [], -1
    for position, cue in enumerate(cues):
        start = _milliseconds(cue["start_ms"], "start_ms")
        end = _milliseconds(cue["end_ms"], "end_ms", zero=False)
        if end <= start or start < previous:
            raise AlignmentError("Cue times must be ordered with end > start")
        if position + 1 < len(cues):
            next_start = _milliseconds(cues[position + 1]["start_ms"], "next start")
            if next_start < start:
                raise AlignmentError("Cue starts must be ordered")
            end = min(end, next_start)
        result.append({**cue, "window_end_ms": end, "window_ms": max(0, end - start)})
        previous = start
    return result


def fit_cue(duration_ms: float, window_ms: int, *, max_speed: float = 1.35) -> dict:
    """Plan modest acceleration and, if still too long, explicit truncation."""
    duration = _positive_number(duration_ms, "duration_ms")
    window = _milliseconds(window_ms, "window_ms")
    limit = _positive_number(max_speed, "max_speed")
    if not 1 <= limit <= 2:
        raise AlignmentError("max_speed must lie between 1 and 2")
    if window == 0:
        return {"speed": 1.0, "trimmed": True, "skip": True, "duration_ms": 0,
                "warnings": ["No speech window before the next cue; cue omitted."]}
    speed = min(limit, max(1.0, duration / window))
    trimmed = duration / speed > window + 0.5
    return {"speed": speed, "trimmed": trimmed, "skip": False,
            "duration_ms": min(float(window), duration / speed),
            "warnings": ([f"Speech exceeds its window at {limit:g}x; tail trimmed to avoid overlap."]
                         if trimmed else [])}


def cue_audio_filter(window_ms: int, *, speed: float = 1.0, start_ms: int = 0) -> str:
    """Fit/pad one cue and align it via adelay (all channels, millisecond time)."""
    window = _milliseconds(window_ms, "window_ms", zero=False)
    start = _milliseconds(start_ms, "start_ms")
    seconds = window / 1000
    return (f"{atempo_chain(speed)},atrim=duration={seconds:.6f},asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={seconds:.6f},atrim=duration={seconds:.6f},"
            f"adelay={start}:all=1")


def build_alignment_filter(cues: Sequence[Mapping], *, output_label: str = "tts",
                           input_offset: int = 0, duration_ms: int | None = None) -> str:
    """Build a bounded adelay/amix graph for already-fitted audio inputs.

    ``fit_speed`` on a cue optionally supplies its modest fitting factor. For
    large SRTs the service streams PCM instead of opening hundreds of inputs.
    """
    if not _LABEL.fullmatch(output_label):
        raise AlignmentError("Invalid output filter label")
    if isinstance(input_offset, bool) or not isinstance(input_offset, int) or input_offset < 0:
        raise AlignmentError("input_offset must be a non-negative integer")
    if not cues or len(cues) > 256:
        raise AlignmentError("Alignment graph requires 1–256 cues")
    windows = cue_windows(cues)
    pieces, labels = [], []
    for position, cue in enumerate(windows):
        if not cue["window_ms"]:
            continue
        label = f"tts_cue_{position}"
        pieces.append(f"[{input_offset + position}:a]" + cue_audio_filter(
            cue["window_ms"], speed=cue.get("fit_speed", 1), start_ms=cue["start_ms"]
        ) + f"[{label}]")
        labels.append(f"[{label}]")
    if not labels:
        raise AlignmentError("No non-empty speech windows")
    duration = (_milliseconds(duration_ms, "duration_ms", zero=False) if duration_ms is not None
                else max(cue["end_ms"] for cue in windows))
    pieces.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,"
                  f"apad=whole_dur={duration / 1000:.6f},atrim=duration={duration / 1000:.6f}[{output_label}]")
    return ";".join(pieces)


def ducking_filter(*, background: str = "0:a", speech: str = "1:a", output_label: str = "mixed",
                   background_gain: float = 1.0, speech_gain: float = 1.0,
                   threshold: float = 0.03, ratio: float = 8.0) -> str:
    """Sidechain-duck BGM under speech and mix without halving voice volume.

    Inputs should cover the same timeline (pad/trim upstream as appropriate).
    Labels are validated; paths, text, and arbitrary filter syntax are rejected.
    """
    for label in (background, speech):
        if not isinstance(label, str) or not re.fullmatch(r"(?:\d+:a(?::\d+)?|[A-Za-z][A-Za-z0-9_]{0,47})", label):
            raise AlignmentError("Invalid audio input label")
    if not _LABEL.fullmatch(output_label):
        raise AlignmentError("Invalid output filter label")
    gain_bg = _positive_number(background_gain, "background_gain")
    gain_speech = _positive_number(speech_gain, "speech_gain")
    thresh = _positive_number(threshold, "threshold")
    compression = _positive_number(ratio, "ratio")
    if gain_bg > 4 or gain_speech > 4 or not 0.000976563 <= thresh <= 1 or not 1 <= compression <= 20:
        raise AlignmentError("Ducking parameters exceed safe bounds")
    return (f"[{speech}]volume={gain_speech:.8g},asplit=2[tts_sc][tts_mix];"
            f"[{background}]volume={gain_bg:.8g}[tts_bg];"
            f"[tts_bg][tts_sc]sidechaincompress=threshold={thresh:.8g}:ratio={compression:.8g}:"
            "attack=20:release=250[tts_ducked];"
            f"[tts_ducked][tts_mix]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[{output_label}]")
