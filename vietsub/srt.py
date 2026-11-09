"""SRT subtitle parsing, validation, normalization, chunking, and VideOCR helpers."""

import itertools
import math
import os
import re
import uuid

from vietsub.constants import (
    CJK_CHAR_PATTERN,
    SRT_TIMECODE_LINE_PATTERN,
    SRT_TIMECODE_PATTERN,
    TRANSLATION_CHUNK_MAX_BLOCKS,
    TRANSLATION_CHUNK_MAX_CHARS,
    TRANSLATION_CHUNK_TARGET_SECONDS,
    TRANSLATION_CHUNK_THRESHOLD_SECONDS,
)
from vietsub.validators import source_language_label, validate_translation_instructions


def format_videocr_time(seconds):
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_srt_timestamp(milliseconds):
    total = max(0, int(milliseconds))
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def align_crop_for_h264(pixel_crop, resolution):
    if not pixel_crop or not resolution:
        return None
    crop_x, crop_y, crop_width, crop_height = pixel_crop
    frame_width, frame_height = resolution
    left = max(0, crop_x - (crop_x % 2))
    top = max(0, crop_y - (crop_y % 2))
    right = min(frame_width, crop_x + crop_width)
    bottom = min(frame_height, crop_y + crop_height)
    right -= right % 2
    bottom -= bottom % 2
    if right - left < 2 or bottom - top < 2:
        return None
    return left, top, right - left, bottom - top


def build_videocr_normalization_filter(normalized_crop, pixel_crop, resolution):
    if normalized_crop and pixel_crop and resolution:
        target_crop = align_crop_for_h264(pixel_crop, resolution)
        if target_crop:
            _, _, target_width, target_height = target_crop
            crop_x = normalized_crop["crop_x"]
            crop_y = normalized_crop["crop_y"]
            crop_width = normalized_crop["crop_width"]
            crop_height = normalized_crop["crop_height"]
            return (
                f"crop=w='max(2,trunc(iw*{crop_width:.9f}/2)*2)'"
                f":h='max(2,trunc(ih*{crop_height:.9f}/2)*2)'"
                f":x='trunc(iw*{crop_x:.9f}/2)*2'"
                f":y='trunc(ih*{crop_y:.9f}/2)*2',"
                f"scale={target_width}:{target_height}:flags=bilinear,setsar=1"
            )
    if resolution:
        target_width = resolution[0] - (resolution[0] % 2)
        target_height = resolution[1] - (resolution[1] % 2)
        return (
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease:flags=bilinear,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
    return ""


def parse_srt_blocks(srt_content):
    blocks = []
    for chunk in re.split(r"\n\s*\n", srt_content.replace("\r\n", "\n").strip()):
        lines = [line.rstrip() for line in chunk.splitlines()]
        if len(lines) < 3 or not lines[0].strip().isdigit():
            return []
        if not SRT_TIMECODE_LINE_PATTERN.fullmatch(lines[1]):
            return []
        text = "\n".join(lines[2:]).strip()
        if not text:
            return []
        blocks.append((lines[0].strip(), lines[1].strip(), text))
    return blocks


def srt_timestamp_milliseconds(value):
    hours, minutes, rest = value.replace(",", ".").split(":")
    seconds, milliseconds = rest.split(".")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(milliseconds)
    )


def parse_timecode_interval(timecode):
    match = SRT_TIMECODE_PATTERN.fullmatch(timecode.strip())
    if not match:
        return None
    return (
        srt_timestamp_milliseconds(match.group(1)),
        srt_timestamp_milliseconds(match.group(2)),
    )


def validate_srt_structure(srt_content, label="SRT"):
    blocks = parse_srt_blocks(srt_content)
    if not blocks:
        raise ValueError(f"{label} không có cấu trúc block hợp lệ.")
    indexes = [int(block[0]) for block in blocks]
    if any(current <= previous for previous, current in itertools.pairwise(indexes)):
        raise ValueError(f"{label} có số thứ tự trùng hoặc không tăng dần.")
    previous_start = -1
    for _, timecode, _ in blocks:
        match = SRT_TIMECODE_PATTERN.fullmatch(timecode)
        if not match:
            raise ValueError(f"{label} có mốc thời gian không hợp lệ.")
        start = srt_timestamp_milliseconds(match.group(1))
        end = srt_timestamp_milliseconds(match.group(2))
        if end <= start:
            raise ValueError(f"{label} có đoạn kết thúc trước hoặc bằng lúc bắt đầu.")
        if start < previous_start:
            raise ValueError(f"{label} có mốc bắt đầu không tăng dần.")
        previous_start = start
    return blocks


def read_videocr_srt_entries(srt_path, *, offset_milliseconds=0):
    with open(srt_path, encoding="utf-8-sig") as source_file:
        content = source_file.read().strip()
    if not content:
        return []
    entries = []
    for chunk in re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip()):
        lines = [line.rstrip() for line in chunk.splitlines()]
        if len(lines) < 2 or not lines[0].strip().isdigit():
            raise RuntimeError(f"VideOCR tạo file SRT không hợp lệ: {srt_path}")
        timecode = lines[1].strip()
        match = SRT_TIMECODE_PATTERN.fullmatch(timecode)
        if not match:
            raise RuntimeError(f"VideOCR tạo file SRT không hợp lệ: {srt_path}")
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue
        start = srt_timestamp_milliseconds(match.group(1)) + offset_milliseconds
        end = srt_timestamp_milliseconds(match.group(2)) + offset_milliseconds
        entries.append({"start": start, "end": end, "text": text})
    return entries


def write_merged_videocr_srt(entries, output_srt):
    merged = []
    for entry in sorted(entries, key=lambda item: (item["start"], item["end"])):
        if entry["end"] <= entry["start"] or not entry["text"]:
            continue
        if (
            merged
            and entry["text"] == merged[-1]["text"]
            and entry["start"] <= merged[-1]["end"] + 250
        ):
            merged[-1]["end"] = max(merged[-1]["end"], entry["end"])
            continue
        merged.append(dict(entry))
    if not merged:
        raise RuntimeError("VideOCR không nhận được phụ đề nào từ video.")
    content = "\n\n".join(
        f"{index}\n{format_srt_timestamp(entry['start'])} --> "
        f"{format_srt_timestamp(entry['end'])}\n{entry['text']}"
        for index, entry in enumerate(merged, 1)
    ) + "\n"
    temporary_path = f"{output_srt}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8", newline="\n") as output_file:
            output_file.write(content)
        os.replace(temporary_path, output_srt)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
    return output_srt


def build_translation_prompt(srt_content, translation_instructions=None, source_lang="ch"):
    instructions = validate_translation_instructions(translation_instructions)
    source_label = source_language_label(source_lang)
    return (
        instructions
        + f"\n\nĐẦU VÀO LÀ {source_label.upper()}. ĐẦU RA BẮT BUỘC LÀ TIẾNG VIỆT.\n"
        + "Mỗi block chỉ được có nội dung tiếng Việt tự nhiên.\n"
        + f"Tuyệt đối không chép lại câu {source_label} gốc và không xuất song ngữ.\n"
        + "Giữ nguyên số thứ tự và timestamp; chỉ dịch phần nội dung phụ đề.\n"
        + "Đặt toàn bộ SRT đã dịch trong đúng một block code srt.\n\n"
        + "Hãy dịch SRT sau đây, không được bỏ sót block nào:\n\n"
        + "```srt\n"
        + f"{srt_content}\n"
        + "```"
    )


def format_srt_blocks(blocks):
    return "\n\n".join(
        f"{index}\n{timecode}\n{text}" for index, timecode, text in blocks
    ) + "\n"


def split_srt_for_translation(srt_content, video_duration_seconds=None):
    """Split an SRT into timestamp-preserving translation requests.

    Every request stays within TRANSLATION_CHUNK_MAX_BLOCKS blocks and
    TRANSLATION_CHUNK_MAX_CHARS characters, whatever the video length, so a dense
    subtitle file cannot overflow the model's output budget. Videos longer than
    TRANSLATION_CHUNK_THRESHOLD_SECONDS are also cut at
    TRANSLATION_CHUNK_TARGET_SECONDS boundaries. Returns ``(chunks, was_split)``.
    """
    blocks = parse_srt_blocks(srt_content)
    if not blocks:
        raise ValueError("SRT gốc không có cấu trúc block hợp lệ.")
    validate_srt_structure(srt_content, "SRT gốc")
    end_milliseconds = max(
        srt_timestamp_milliseconds(SRT_TIMECODE_PATTERN.fullmatch(timecode).group(2))
        for _, timecode, _ in blocks
    )
    try:
        declared_duration = float(video_duration_seconds)
    except (TypeError, ValueError):
        declared_duration = None
    if declared_duration is None or not math.isfinite(declared_duration) or declared_duration <= 0:
        declared_duration = end_milliseconds / 1000
    split_by_time = declared_duration > TRANSLATION_CHUNK_THRESHOLD_SECONDS
    total_chars = sum(len(format_srt_blocks([block])) for block in blocks)
    if (
        not split_by_time
        and len(blocks) <= TRANSLATION_CHUNK_MAX_BLOCKS
        and total_chars <= TRANSLATION_CHUNK_MAX_CHARS
    ):
        return [srt_content], False

    chunks = []
    current = []
    current_chars = 0
    chunk_start_milliseconds = None
    next_time_boundary = None
    target_chunk_milliseconds = TRANSLATION_CHUNK_TARGET_SECONDS * 1000

    for block in blocks:
        _, timecode, _ = block
        start_milliseconds = srt_timestamp_milliseconds(
            SRT_TIMECODE_PATTERN.fullmatch(timecode).group(1)
        )
        block_chars = len(format_srt_blocks([block]))
        reached_time_boundary = (
            split_by_time
            and current
            and next_time_boundary is not None
            and start_milliseconds >= next_time_boundary
        )
        reached_size_boundary = current and (
            len(current) >= TRANSLATION_CHUNK_MAX_BLOCKS
            or current_chars + block_chars > TRANSLATION_CHUNK_MAX_CHARS
        )
        if reached_time_boundary or reached_size_boundary:
            chunks.append(format_srt_blocks(current))
            current = []
            current_chars = 0
            chunk_start_milliseconds = None
            next_time_boundary = None

        if chunk_start_milliseconds is None:
            chunk_start_milliseconds = start_milliseconds
            next_time_boundary = chunk_start_milliseconds + target_chunk_milliseconds
        current.append(block)
        current_chars += block_chars

    if current:
        chunks.append(format_srt_blocks(current))
    if len(chunks) == 1:
        return [srt_content], False
    return chunks, True


def translation_language_stats(srt_content):
    """Return CJK/word counts used to catch a model echoing Chinese or Japanese source text."""
    blocks = parse_srt_blocks(srt_content)
    texts = [text for _, _, text in blocks]
    joined = "\n".join(texts)
    cjk_count = len(CJK_CHAR_PATTERN.findall(joined))
    word_count = sum(character.isalnum() for character in joined)
    cjk_lines = sum(bool(CJK_CHAR_PATTERN.search(text)) for text in texts)
    return cjk_count, word_count, cjk_lines, len(texts)


def validate_translation_language(source_srt, translated_srt):
    """Reject unchanged or bilingual Chinese output before it reaches the result file."""
    source_cjk, source_words, _, _ = translation_language_stats(source_srt)
    if source_cjk < 4 or source_cjk / max(1, source_words) < 0.25:
        return translated_srt

    translated_cjk, translated_words, translated_cjk_lines, translated_lines = (
        translation_language_stats(translated_srt)
    )
    source_text = re.sub(
        r"\s+", "", "\n".join(text for _, _, text in parse_srt_blocks(source_srt))
    )
    translated_text = re.sub(
        r"\s+", "", "\n".join(text for _, _, text in parse_srt_blocks(translated_srt))
    )
    translated_ratio = translated_cjk / max(1, translated_words)
    translated_line_ratio = translated_cjk_lines / max(1, translated_lines)
    unchanged = source_text == translated_text
    bilingual = (
        translated_cjk >= max(8, int(source_cjk * 0.2))
        and (translated_ratio >= 0.12 or translated_line_ratio >= 0.30)
    )
    if unchanged or translated_ratio >= 0.35 or bilingual:
        raise ValueError(
            "Bản dịch vẫn giữ chữ Hán của bản gốc (tiếng Trung/tiếng Nhật) hoặc là song ngữ; "
            "hãy dịch lại hoặc thử model khác."
        )
    return translated_srt


def validate_translated_srt(source_srt, translated_srt):
    source_blocks = validate_srt_structure(source_srt, "SRT gốc")
    translated_blocks = validate_srt_structure(translated_srt, "SRT dịch")
    source_timecodes = SRT_TIMECODE_PATTERN.findall(source_srt)
    translated_timecodes = SRT_TIMECODE_PATTERN.findall(translated_srt)
    if not source_timecodes:
        raise ValueError("SRT gốc không có mốc thời gian hợp lệ.")
    if source_timecodes != translated_timecodes:
        raise ValueError(
            "Bản dịch không phải SRT đầy đủ hoặc đã thay đổi mốc thời gian; không lưu file."
        )
    if [block[0] for block in source_blocks] != [block[0] for block in translated_blocks]:
        raise ValueError("SRT dịch không khớp số thứ tự block với SRT gốc.")
    validate_translation_language(source_srt, translated_srt)
    return translated_srt


def normalize_translated_srt(source_srt, translated_srt):
    """Restore source timing and chunk indexes when the model preserves subtitle blocks."""
    source_blocks = parse_srt_blocks(source_srt)
    translated_blocks = parse_srt_blocks(translated_srt)
    if source_blocks and len(source_blocks) == len(translated_blocks):
        source_indexes = [block[0] for block in source_blocks]
        translated_indexes = [block[0] for block in translated_blocks]
        source_timecodes = [block[1] for block in source_blocks]
        translated_timecodes = [block[1] for block in translated_blocks]
        source_intervals = [parse_timecode_interval(tc) for tc in source_timecodes]
        translated_intervals = [parse_timecode_interval(tc) for tc in translated_timecodes]
        sequential_indexes = [str(index) for index in range(1, len(source_blocks) + 1)]
        if (
            source_indexes == translated_indexes
            or source_timecodes == translated_timecodes
            or (
                translated_indexes == sequential_indexes
                and None not in source_intervals
                and source_intervals == translated_intervals
            )
        ):
            rebuilt = "\n\n".join(
                f"{source_index}\n{source_timecode}\n{translated_text}"
                for (source_index, source_timecode, _), (_, _, translated_text) in zip(
                    source_blocks, translated_blocks, strict=True
                )
            )
            return validate_translated_srt(source_srt, rebuilt)
    return validate_translated_srt(source_srt, translated_srt)


def extract_api_translated_srt(content, source_srt):
    fenced_blocks = re.findall(
        r"```(?:[A-Za-z0-9_-]+)?[ \t]*\r?\n(.*?)```",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    candidates = fenced_blocks or [content]
    translated_srt = max(candidates, key=lambda value: len(SRT_TIMECODE_PATTERN.findall(value)))
    return normalize_translated_srt(source_srt, translated_srt.strip())


def extract_gemini_translated_srt(response, source_srt):
    if not response:
        raise RuntimeError("Không tìm thấy câu trả lời của AI để trích xuất phụ đề.")
    code_blocks = response.locator("pre")
    if code_blocks.count() == 0:
        code_blocks = response.locator("code")
    if code_blocks.count() > 0:
        translated_srt = "\n".join(code_blocks.all_inner_texts()) + "\n"
    else:
        translated_srt = response.inner_text()
    translated_srt = re.sub(r"^```[a-zA-Z0-9]*\n", "", translated_srt, flags=re.MULTILINE)
    translated_srt = re.sub(r"\n```$", "", translated_srt)
    return normalize_translated_srt(source_srt, translated_srt.strip())
