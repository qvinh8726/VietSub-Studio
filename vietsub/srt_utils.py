"""Strict stdlib SRT parser, validator, serializer, atomic writer, and raw-cue matcher.

All cue times are integer milliseconds. Serialized SRT strictly follows the
canonical 4-line block layout (sequential 1..N index, ``HH:MM:SS,mmm --> HH:MM:SS,mmm``,
non-empty text, blank line separator). File I/O refuses symlinks/junctions via
``vietsub.storage.safe_regular`` and writes atomically with optional 1-level ``.bak``.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

__all__ = [
    "MAX_SRT_BYTES",
    "MAX_CUES",
    "MAX_CUE_CHARS",
    "format_timestamp_ms",
    "parse_timestamp_ms",
    "parse_srt_text",
    "validate_cues",
    "serialize_srt",
    "backup_path_for",
    "read_srt_file",
    "write_srt_atomic",
    "restore_srt_backup",
    "match_raw_cues",
]

MAX_SRT_BYTES = 8 * 1024 * 1024
MAX_CUES = 100_000
MAX_CUE_CHARS = 10_000
MAX_TIMELINE_MS = 3_599_999_999  # 999:59:59,999 (ASS/SRT upper bound)
EDITOR_MAX_TIMELINE_MS = 86_400_000  # 24 hours for interactive SRT editing

_STAMP_RE = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d)[,.](\d{3})\Z")
_TIMING_RE = re.compile(
    r"^(\d{1,3}):([0-5]\d):([0-5]\d)[,.](\d{3})[ \t]+-->[ \t]+"
    r"(\d{1,3}):([0-5]\d):([0-5]\d)[,.](\d{3})\Z"
)


def format_timestamp_ms(ms: int) -> str:
    """Format non-negative integer milliseconds as ``HH:MM:SS,mmm``."""
    if isinstance(ms, bool) or not isinstance(ms, int) or ms < 0 or ms > MAX_TIMELINE_MS:
        raise ValueError("Mốc thời gian SRT phải là số nguyên mili-giây không âm hợp lệ.")
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_timestamp_ms(value: str) -> int:
    """Parse ``HH:MM:SS,mmm`` (or ``.mmm``) into integer milliseconds."""
    if not isinstance(value, str):
        raise ValueError("Mốc thời gian SRT phải là chuỗi.")
    match = _STAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Mốc thời gian SRT không đúng chuẩn HH:MM:SS,mmm: {value!r}")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _check_control_chars(text: str, label: str = "SRT") -> None:
    if any((ord(ch) < 32 and ch not in "\n\t") or ord(ch) == 127 or 0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        raise ValueError(f"{label} chứa ký tự điều khiển hoặc Unicode không hợp lệ.")


def parse_srt_text(
    text: str,
    *,
    allow_empty: bool = False,
    max_cues: int = MAX_CUES,
    max_cue_chars: int = MAX_CUE_CHARS,
) -> list[dict]:
    """Strictly parse SRT text into ``[{index, start_ms, end_ms, text}, ...]``."""
    if not isinstance(text, str):
        raise ValueError("Nội dung SRT phải là văn bản UTF-8.")
    if len(text.encode("utf-8", errors="replace")) > MAX_SRT_BYTES:
        raise ValueError("SRT vượt quá giới hạn 8 MiB.")
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    _check_control_chars(normalized, "SRT")
    stripped = normalized.strip()
    if not stripped:
        raise ValueError("SRT không có dòng phụ đề nào.")

    cues: list[dict] = []
    seen: set[int] = set()
    previous_start = -1
    for block in re.split(r"\n(?:[ \t]*\n)+", stripped):
        lines = block.split("\n")
        min_lines = 2 if allow_empty else 3
        if len(lines) < min_lines or not re.fullmatch(r"[0-9]{1,12}", lines[0].strip()):
            raise ValueError("Mỗi block SRT cần số thứ tự dương, dòng mốc thời gian và nội dung.")
        cue_id = int(lines[0].strip())
        if cue_id <= 0 or cue_id in seen:
            raise ValueError(f"Số thứ tự SRT {cue_id} phải dương và không trùng lặp.")
        seen.add(cue_id)
        match = _TIMING_RE.fullmatch(lines[1].strip())
        if not match:
            raise ValueError(f"Mốc thời gian không hợp lệ ở câu {cue_id}.")
        vals = tuple(int(part) for part in match.groups())
        start_ms = ((vals[0] * 60 + vals[1]) * 60 + vals[2]) * 1000 + vals[3]
        end_ms = ((vals[4] * 60 + vals[5]) * 60 + vals[6]) * 1000 + vals[7]
        if end_ms <= start_ms or start_ms < previous_start:
            raise ValueError(f"Mốc thời gian bị đảo ngược hoặc không tăng dần ở câu {cue_id}.")
        body_lines = lines[2:]
        if any(_TIMING_RE.fullmatch(line.strip()) for line in body_lines):
            raise ValueError(f"Thiếu dòng trống ngăn cách block hoặc dòng thời gian lọt trong nội dung ở câu {cue_id}.")
        body = "\n".join(line.rstrip() for line in body_lines).strip()
        if (not body and not allow_empty) or len(body) > max_cue_chars:
            raise ValueError(f"Nội dung câu {cue_id} trống hoặc vượt quá {max_cue_chars} ký tự.")
        cues.append({
            "index": cue_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": body,
        })
        previous_start = start_ms
        if len(cues) > max_cues:
            raise ValueError(f"SRT vượt quá giới hạn {max_cues} câu.")
    return cues


def validate_cues(
    cues,
    *,
    allow_empty: bool = False,
    max_cues: int = MAX_CUES,
    max_cue_chars: int = MAX_CUE_CHARS,
    max_ms: int = EDITOR_MAX_TIMELINE_MS,
) -> list[dict]:
    """Validate an edited cue list and renumber indices sequentially ``1..N``."""
    if not isinstance(cues, list) or not cues:
        raise ValueError("Danh sách phụ đề phải có ít nhất 1 câu.")
    if len(cues) > max_cues:
        raise ValueError(f"Danh sách phụ đề vượt quá giới hạn {max_cues} câu.")
    normalized: list[dict] = []
    previous_start = -1
    for position, item in enumerate(cues, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Câu {position}: dữ liệu không phải đối tượng hợp lệ.")
        start_ms = item.get("start_ms")
        end_ms = item.get("end_ms")
        if isinstance(start_ms, bool) or not isinstance(start_ms, int) or start_ms < 0 or start_ms > max_ms:
            raise ValueError(f"Câu {position}: mốc bắt đầu không hợp lệ.")
        if isinstance(end_ms, bool) or not isinstance(end_ms, int) or end_ms <= start_ms or end_ms > max_ms:
            raise ValueError(f"Câu {position}: mốc kết thúc phải lớn hơn mốc bắt đầu.")
        if start_ms < previous_start:
            raise ValueError(f"Câu {position}: mốc bắt đầu phải không nhỏ hơn câu trước.")
        raw_text = item.get("text")
        if not isinstance(raw_text, str):
            raise ValueError(f"Câu {position}: nội dung phụ đề phải là văn bản.")
        clean = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        _check_control_chars(clean, f"Câu {position}")
        lines = [line.strip() for line in clean.split("\n")]
        # Drop leading/trailing blank lines and collapse internal blank lines that would split SRT blocks.
        non_blank = [line for line in lines if line]
        if any(_TIMING_RE.fullmatch(line) for line in non_blank):
            raise ValueError(f"Câu {position}: nội dung không được chứa dòng mốc thời gian SRT.")
        text = "\n".join(non_blank)
        if (not text and not allow_empty) or len(text) > max_cue_chars:
            raise ValueError(f"Câu {position}: nội dung không được để trống và tối đa {max_cue_chars} ký tự.")
        normalized.append({
            "index": position,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
        })
        previous_start = start_ms
    return normalized


def serialize_srt(cues: list[dict]) -> str:
    """Serialize validated cues into strict 4-line SRT text with blank-line separators."""
    blocks = []
    for position, cue in enumerate(cues, 1):
        index = cue.get("index") if isinstance(cue.get("index"), int) and cue["index"] > 0 else position
        start = format_timestamp_ms(cue["start_ms"])
        end = format_timestamp_ms(cue["end_ms"])
        blocks.append(f"{index}\n{start} --> {end}\n{cue['text']}")
    return "\n\n".join(blocks) + "\n"


def backup_path_for(path) -> str:
    return str(Path(os.path.abspath(os.fspath(path)))) + ".bak"


def _write_bytes_atomic(target: Path, payload: bytes) -> None:
    from vietsub import manifest

    manifest._safe_path(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            prefix="." + target.name + ".",
            suffix=".tmp",
            dir=str(target.parent),
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        manifest._safe_path(str(target))
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def read_srt_file(path, *, allow_empty: bool = False) -> list[dict]:
    """Safely read and parse an SRT file, refusing symlinks/junctions."""
    from vietsub import storage

    resolved = os.path.abspath(os.fspath(path))
    if not storage.safe_regular(resolved):
        raise ValueError("File SRT không tồn tại, là liên kết không an toàn hoặc không phải tệp thường.")
    with open(resolved, "rb") as stream:
        raw = stream.read(MAX_SRT_BYTES + 1)
    if len(raw) > MAX_SRT_BYTES:
        raise ValueError("File SRT vượt quá giới hạn 8 MiB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("File SRT phải mã hoá UTF-8.") from exc
    return parse_srt_text(text, allow_empty=allow_empty)


def write_srt_atomic(path, cues: list[dict], *, backup: bool = True) -> list[dict]:
    """Validate, optionally create 1-level ``.bak``, and atomically overwrite an SRT file."""
    from vietsub import storage

    resolved = Path(os.path.abspath(os.fspath(path)))
    validated = validate_cues(cues)
    payload = serialize_srt(validated).encode("utf-8")
    if len(payload) > MAX_SRT_BYTES:
        raise ValueError("Nội dung SRT vượt quá giới hạn 8 MiB.")
    if resolved.exists() or resolved.is_symlink():
        if not storage.safe_regular(str(resolved)):
            raise ValueError("Đường dẫn SRT không an toàn (liên kết tượng trưng/junction); từ chối ghi.")
        if backup:
            bak = Path(backup_path_for(resolved))
            if (bak.exists() or bak.is_symlink()) and not storage.safe_regular(str(bak)):
                raise ValueError("Đường dẫn bản sao lưu .bak không an toàn; từ chối ghi.")
            with open(resolved, "rb") as current_stream:
                prior_bytes = current_stream.read(MAX_SRT_BYTES + 1)
            if len(prior_bytes) <= MAX_SRT_BYTES:
                _write_bytes_atomic(bak, prior_bytes)
    _write_bytes_atomic(resolved, payload)
    return validated


def restore_srt_backup(path) -> list[dict]:
    """Restore ``<path>`` from its 1-level ``.bak`` file after strict validation."""
    from vietsub import storage

    resolved = Path(os.path.abspath(os.fspath(path)))
    bak = Path(backup_path_for(resolved))
    if not storage.safe_regular(str(bak)):
        raise FileNotFoundError("Chưa có bản sao lưu (.bak) hợp lệ để hoàn tác.")
    if (resolved.exists() or resolved.is_symlink()) and not storage.safe_regular(str(resolved)):
        raise ValueError("Đường dẫn SRT đích không an toàn; từ chối khôi phục.")
    cues = read_srt_file(bak)
    return write_srt_atomic(resolved, cues, backup=False)


def match_raw_cues(vi_cues: list[dict], raw_cues: list[dict]) -> list[str]:
    """Align raw OCR cues to Vietnamese cues by maximum time overlap (with index fallback)."""
    if not raw_cues:
        return ["" for _ in vi_cues]
    matched: list[str] = []
    same_length = len(vi_cues) == len(raw_cues)
    for idx, cue in enumerate(vi_cues):
        v_start, v_end = cue["start_ms"], cue["end_ms"]
        overlaps: list[tuple[int, int, str]] = []
        best_overlap = 0
        best_text = ""
        for raw in raw_cues:
            if raw["end_ms"] <= v_start:
                continue
            if raw["start_ms"] >= v_end:
                break
            ov = min(v_end, raw["end_ms"]) - max(v_start, raw["start_ms"])
            if ov > 0:
                raw_dur = max(1, raw["end_ms"] - raw["start_ms"])
                overlaps.append((ov, raw_dur, raw["text"]))
                if ov > best_overlap:
                    best_overlap = ov
                    best_text = raw["text"]
        if overlaps:
            significant = [text for ov, dur, text in overlaps if ov / dur >= 0.4]
            if len(significant) > 1:
                matched.append(" / ".join(significant[:3]))
            else:
                matched.append(best_text)
        elif same_length:
            matched.append(raw_cues[idx]["text"])
        else:
            matched.append("")
    return matched
