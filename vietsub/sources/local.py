"""Validation of video files picked on this computer."""

import os

LOCAL_VIDEO_EXTENSIONS = {".mp4": "mp4", ".mkv": "mkv"}
MKV_MAGIC = b"\x1a\x45\xdf\xa3"


def sniff_container(path):
    """Return "mp4", "mkv" or "" from the first bytes of a file."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
    except OSError:
        return ""
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return "mp4"
    if header.startswith(MKV_MAGIC):
        return "mkv"
    return ""


def require_mp4_header(path):
    if sniff_container(path) != "mp4":
        raise ValueError("File tải về không phải video MP4 hợp lệ.")


def validate_local_video_path(path):
    """Check a video path chosen in the native file dialog; the file is used in place."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError("Đường dẫn video không hợp lệ.")
    path = path.strip()
    if not os.path.isabs(path):
        raise ValueError("Đường dẫn video phải là đường dẫn tuyệt đối.")
    path = os.path.abspath(path)
    extension = os.path.splitext(path)[1].lower()
    expected = LOCAL_VIDEO_EXTENSIONS.get(extension)
    if not expected:
        raise ValueError("Chỉ hỗ trợ video MP4 hoặc MKV.")
    if not os.path.isfile(path):
        raise ValueError("Không tìm thấy file video đã chọn.")
    size = os.path.getsize(path)
    if size <= 0:
        raise ValueError("File video đang trống hoặc không đọc được.")
    if sniff_container(path) != expected:
        raise ValueError(f"File đã chọn không có cấu trúc {expected.upper()} hợp lệ.")
    name = os.path.basename(path)
    return {
        "path": path,
        "container": expected,
        "size": size,
        "name": name,
        "title": os.path.splitext(name)[0] or "Video trên máy",
    }
