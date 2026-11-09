"""Validation and file-safety helpers for VietSub Studio."""

import hashlib
import ipaddress
import os
import re
import unicodedata
from urllib.parse import parse_qs, urlparse

from vietsub.constants import (
    ALLOWED_DOUYIN_THUMBNAIL_DOMAINS,
    ALLOWED_DOUYIN_VIDEO_DOMAINS,
    APP_VERSION,
    DEFAULT_TRANSLATION_INSTRUCTIONS,
    GITHUB_REPOSITORY,
    MAX_LOCAL_VIDEO_BYTES,
    MAX_OCR_FRAMES_TO_SKIP,
    MAX_PROJECT_NAME_LENGTH,
    MAX_THUMBNAIL_BYTES,
    MAX_TRANSLATION_INSTRUCTIONS_CHARS,
    SOURCE_LANGUAGE_LABELS,
    SUPPORTED_OCR_LANGS,
    SUPPORTED_OCR_MODEL_MODES,
    SUPPORTED_TRANSLATION_PROVIDERS,
    TRANSLATION_PROVIDER_PRESETS,
    URL_PATTERN,
    WINDOWS_RESERVED_NAMES,
)


def parse_release_version(value):
    if not isinstance(value, str):
        raise ValueError("Phiên bản release không hợp lệ.")
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", value.strip())
    if not match:
        raise ValueError("Phiên bản release không đúng định dạng.")
    return tuple(int(part) for part in match.groups())


def is_newer_release(latest_version, current_version=APP_VERSION):
    return parse_release_version(latest_version) > parse_release_version(current_version)


def validate_release_url(value):
    parsed = urlparse(value or "")
    expected_prefix = f"/{GITHUB_REPOSITORY.lower()}/releases/"
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
        raise ValueError("Link cập nhật không thuộc GitHub chính thức.")
    if not parsed.path.lower().startswith(expected_prefix):
        raise ValueError("Link cập nhật không thuộc repository chính thức.")
    return value


def validate_release_download_url(value, version):
    parsed = urlparse(value or "")
    expected_prefix = f"/{GITHUB_REPOSITORY.lower()}/releases/download/v{version}/"
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
        raise ValueError("File cập nhật không thuộc GitHub chính thức.")
    if not parsed.path.lower().startswith(expected_prefix):
        raise ValueError("File cập nhật không thuộc đúng release chính thức.")
    return value


def parse_update_checksum(text, expected_filename):
    for line in str(text or "").splitlines():
        match = re.fullmatch(r"\s*([0-9A-Fa-f]{64})\s+\*?(.+?)\s*", line)
        if match and match.group(2) == expected_filename:
            return match.group(1).lower()
    raise ValueError("File checksum của release không hợp lệ.")


def validate_notebook_url(value, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError("Link Notebook không hợp lệ.")
    normalized = value.strip()
    if not normalized and allow_empty:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or parsed.hostname != "gemini.google.com":
        raise ValueError("Link Notebook phải dùng HTTPS trên gemini.google.com.")
    if not re.fullmatch(r"(?:/u/\d+)?/notebook/[A-Za-z0-9_-]+/?", parsed.path):
        raise ValueError("Link Notebook không đúng định dạng /notebook/<id>.")
    return normalized


def validate_translation_provider(value):
    if value in (None, ""):
        return "notebook"
    if not isinstance(value, str) or value not in SUPPORTED_TRANSLATION_PROVIDERS:
        raise ValueError("Nhà cung cấp dịch không hợp lệ.")
    return value


def validate_translation_api_base_url(value, provider):
    provider = validate_translation_provider(provider)
    if provider == "notebook":
        return ""
    preset = TRANSLATION_PROVIDER_PRESETS[provider]
    if value not in (None, "") and not isinstance(value, str):
        raise ValueError("Base URL API không hợp lệ.")
    normalized = (value or preset["base_url"]).strip().rstrip("/")
    if not normalized:
        raise ValueError("Hãy nhập Base URL cho API dịch.")
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    local_http = parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not local_http:
        raise ValueError("Base URL API phải dùng HTTPS; HTTP chỉ được phép với localhost.")
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL API không hợp lệ.")
    return normalized


def validate_translation_api_model(value, provider):
    provider = validate_translation_provider(provider)
    if provider == "notebook":
        return ""
    preset = TRANSLATION_PROVIDER_PRESETS[provider]
    if value not in (None, "") and not isinstance(value, str):
        raise ValueError("Tên model API không hợp lệ.")
    normalized = (value or preset["model"]).strip()
    if not normalized:
        raise ValueError("Hãy nhập tên model API dùng để dịch.")
    if len(normalized) > 200 or any(ord(character) < 32 for character in normalized):
        raise ValueError("Tên model API không hợp lệ.")
    return normalized


def validate_translation_api_key(value, allow_empty=True):
    if value in (None, "") and allow_empty:
        return ""
    if not isinstance(value, str):
        raise ValueError("API key không hợp lệ.")
    normalized = value.strip()
    if not normalized and allow_empty:
        return ""
    if not normalized or len(normalized) > 4096 or any(ord(character) < 32 for character in normalized):
        raise ValueError("API key không hợp lệ.")
    return normalized


def validate_translation_instructions(value):
    if value in (None, ""):
        return DEFAULT_TRANSLATION_INSTRUCTIONS
    if not isinstance(value, str):
        raise ValueError("Cách dịch mong muốn không hợp lệ.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return DEFAULT_TRANSLATION_INSTRUCTIONS
    if len(normalized) > MAX_TRANSLATION_INSTRUCTIONS_CHARS:
        raise ValueError(
            f"Cách dịch mong muốn không được vượt quá {MAX_TRANSLATION_INSTRUCTIONS_CHARS:,} ký tự."
        )
    if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
        raise ValueError("Cách dịch mong muốn chứa ký tự điều khiển không hợp lệ.")
    return normalized


def validate_ocr_lang(value):
    if not isinstance(value, str) or value not in SUPPORTED_OCR_LANGS:
        raise ValueError("Ngôn ngữ OCR này chưa được hỗ trợ.")
    return value


def source_language_label(ocr_lang):
    """Return the Vietnamese name of an OCR source language for prompts and logs."""
    return SOURCE_LANGUAGE_LABELS.get(ocr_lang, "ngôn ngữ gốc")


def validate_ocr_model_mode(value):
    if value in (None, ""):
        return "auto"
    if not isinstance(value, str) or value not in SUPPORTED_OCR_MODEL_MODES:
        raise ValueError("Chế độ model OCR không hợp lệ.")
    return value


def validate_ocr_frames_to_skip(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Số khung hình bỏ qua phải là số nguyên.")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Số khung hình bỏ qua phải là số nguyên.") from error
    if not 0 <= number <= MAX_OCR_FRAMES_TO_SKIP:
        raise ValueError(f"Số khung hình bỏ qua phải từ 0 đến {MAX_OCR_FRAMES_TO_SKIP}.")
    return number


def validate_output_dir(value):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("Thư mục xuất không hợp lệ.")
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    if not os.path.isabs(expanded):
        raise ValueError("Thư mục xuất phải là đường dẫn tuyệt đối.")
    return os.path.abspath(expanded)


def validate_crop_coords(value, allow_none=True):
    if value is None and allow_none:
        return None
    if not isinstance(value, dict):
        raise ValueError("Vùng OCR không hợp lệ.")
    required = ("crop_x", "crop_y", "crop_width", "crop_height")
    try:
        coords = {key: round(float(value[key]), 6) for key in required}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Vùng OCR phải có đủ tọa độ x, y, rộng và cao.") from error
    if not (
        0 <= coords["crop_x"] < 1
        and 0 <= coords["crop_y"] < 1
        and 0 < coords["crop_width"] <= 1 - coords["crop_x"] + 1e-6
        and 0 < coords["crop_height"] <= 1 - coords["crop_y"] + 1e-6
    ):
        raise ValueError("Vùng OCR phải nằm hoàn toàn bên trong khung video.")
    return coords


def validate_video_resolution(value, allow_none=True):
    if value is None and allow_none:
        return None
    if not isinstance(value, dict):
        raise ValueError("Độ phân giải video không hợp lệ.")
    try:
        width = int(value["width"])
        height = int(value["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Độ phân giải video phải có chiều rộng và chiều cao.") from error
    if not 16 <= width <= 16384 or not 16 <= height <= 16384:
        raise ValueError("Độ phân giải video nằm ngoài giới hạn hỗ trợ.")
    return width, height


def sanitize_project_name(value, fallback="Du an Douyin"):
    if value is not None and not isinstance(value, str):
        raise ValueError("Tên bộ file không hợp lệ.")
    raw_name = (value or "").strip() or fallback
    name = unicodedata.normalize("NFKC", raw_name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = unicodedata.normalize("NFKC", fallback or "Du an Douyin")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
        name = re.sub(r"\s+", " ", name).strip(" .") or "Du an Douyin"
    if len(name) > MAX_PROJECT_NAME_LENGTH:
        name = name[:MAX_PROJECT_NAME_LENGTH].rstrip(" .")
    if name.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        name = f"Video {name}"
    return name


def validate_douyin_url(value):
    if not isinstance(value, str):
        raise ValueError("Link Douyin không hợp lệ.")
    candidates = URL_PATTERN.findall(value.strip()) or [value.strip()]
    for candidate in candidates:
        candidate = candidate.rstrip(".,;:!?)]}，。！？、")
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"}:
            continue
        if host not in {"douyin.com", "iesdouyin.com"} and not host.endswith(
            (".douyin.com", ".iesdouyin.com")
        ):
            continue
        has_modal_id = any(
            re.fullmatch(r"\d+", item.strip())
            for item in parse_qs(parsed.query).get("modal_id", [])
        )
        if (parsed.path and parsed.path != "/") or has_modal_id:
            return candidate
    raise ValueError("Không tìm thấy link video Douyin trong nội dung đã dán.")


def get_video_id(video_url):
    parsed = urlparse(video_url)
    match = re.search(r"/(?:video|note)/(\d+)", parsed.path)
    if match:
        return match.group(1)
    for modal_id in parse_qs(parsed.query).get("modal_id", []):
        if re.fullmatch(r"\d+", modal_id.strip()):
            return modal_id.strip()
    raise ValueError("Không tìm thấy ID video trong link Douyin đã giải mã.")


def validate_douyin_thumbnail_url(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    allowed_host = any(
        host == domain or host.endswith("." + domain)
        for domain in ALLOWED_DOUYIN_THUMBNAIL_DOMAINS
    )
    if parsed.scheme != "https" or not allowed_host:
        return ""
    return value.strip()


def validate_douyin_video_stream_url(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = value.strip()
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    elif candidate.startswith("http://"):
        candidate = "https://" + candidate.removeprefix("http://")
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    allowed_host = any(
        host == domain or host.endswith("." + domain)
        for domain in ALLOWED_DOUYIN_VIDEO_DOMAINS
    )
    if parsed.scheme != "https" or not allowed_host or parsed.username or parsed.password:
        return ""
    return candidate


def validate_public_https_url(value):
    """Accept only HTTPS URLs on public DNS names (no IP literals, localhost or credentials)."""
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = value.strip()
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return ""
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        return ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return candidate
    return ""


def validate_douyin_stream_redirect_url(value):
    """Validate a CDN redirect target for a Douyin video stream.

    Known Douyin/ByteDance CDN hosts are accepted (HTTP is upgraded to HTTPS).
    Other hosts must still be public HTTPS names, which blocks redirects to
    localhost, IP literals and plaintext downgrades without breaking new CDNs.
    """
    return validate_douyin_video_stream_url(value) or validate_public_https_url(value)


def thumbnail_extension(data):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def validate_local_thumbnail_file(path):
    size = os.path.getsize(path)
    if size <= 0 or size > MAX_THUMBNAIL_BYTES:
        raise ValueError("Thumbnail local trống hoặc vượt quá giới hạn 20 MB.")
    with open(path, "rb") as handle:
        header = handle.read(16)
    detected_extension = thumbnail_extension(header)
    if not detected_extension:
        raise ValueError("Thumbnail local không phải ảnh JPG, PNG hoặc WebP hợp lệ.")
    return detected_extension


def validate_local_mp4_file(path):
    size = os.path.getsize(path)
    if size <= 0:
        raise ValueError("File video đang trống hoặc không đọc được.")
    if size > MAX_LOCAL_VIDEO_BYTES:
        raise ValueError("File MP4 vượt quá giới hạn 8 GiB của ứng dụng.")
    with open(path, "rb") as handle:
        header = handle.read(12)
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise ValueError("File đã chọn không có cấu trúc MP4 hợp lệ.")
    return size


def path_is_within(path, directory):
    if not path or not directory:
        return False
    try:
        common = os.path.commonpath((os.path.abspath(path), os.path.abspath(directory)))
        return os.path.normcase(common) == os.path.normcase(os.path.abspath(directory))
    except (OSError, ValueError):
        return False


def files_have_same_content(first_path, second_path):
    if os.path.getsize(first_path) != os.path.getsize(second_path):
        return False
    first_hash = hashlib.sha256()
    second_hash = hashlib.sha256()
    with open(first_path, "rb") as first, open(second_path, "rb") as second:
        while True:
            first_chunk = first.read(1024 * 1024)
            second_chunk = second.read(1024 * 1024)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                break
            first_hash.update(first_chunk)
            second_hash.update(second_chunk)
    return first_hash.digest() == second_hash.digest()
