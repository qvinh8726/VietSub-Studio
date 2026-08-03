import os
import sys
import re
import copy
import time
import uuid
import sqlite3
import ast
import shutil
import subprocess
import unicodedata
import urllib.error
import urllib.request
import json
import math
import base64
import webbrowser
import hashlib
import secrets
import tempfile
import zipfile
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread, local
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from werkzeug.serving import make_server

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
APP_HOME = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SOURCE_DIR
if getattr(sys, "frozen", False):
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
ICON_PATH = os.path.join(RESOURCE_DIR, "avatar.ico")
app = Flask(
    __name__,
    template_folder=os.path.join(RESOURCE_DIR, "templates"),
    static_folder=None,
)

# Paths
APPDATA = os.environ.get("APPDATA")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA")


def get_windows_directory():
    if os.name != "nt":
        return ""
    import ctypes

    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    return buffer.value if 0 < length < len(buffer) else r"C:\Windows"


WINDOWS_DIRECTORY = get_windows_directory()
POWERSHELL_EXE = os.path.join(
    WINDOWS_DIRECTORY,
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
)

SIDECAR_BIN = os.path.join(LOCALAPPDATA, "Programs", "Douzy", "resources", "sidecar", "win32-x64", "douyin-dl-sidecar.exe")
DOUZY_CONFIG = os.path.join(APPDATA, "douyin-downloader-desktop", "config.yml")
DOUZY_DB = os.path.join(APPDATA, "douyin-downloader-desktop", "dy_downloader.db")

VIDEOCR_CLI = r"C:\Program Files\VideOCR\videocr-cli.exe"
VIDEOCR_CONFIG = os.path.join(APPDATA, "VideOCR", "videocr_gui_config.ini")

USER_DATA_DIR_DEBUG = os.path.join(LOCALAPPDATA, "Microsoft", "Edge", "User Data Debug")
EDGE_DEBUG_PORT = 9222
CONFIG_DIR = (
    os.path.join(APPDATA, "VietSub Studio")
    if getattr(sys, "frozen", False) and APPDATA
    else APP_HOME
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "app_config.json")
QUEUE_PATH = os.path.join(CONFIG_DIR, "workflow_queue.json")
LOCAL_VIDEO_DIR = os.path.join(CONFIG_DIR, "local_videos")
INSTANCE_LOCK_PATH = os.path.join(CONFIG_DIR, "instance.lock")
LOG_PATH = os.path.join(CONFIG_DIR, "vietsub-studio.log")

SUPPORTED_OCR_LANGS = {"ch", "en", "ja", "ko", "vi"}
SUPPORTED_OCR_MODEL_MODES = {"auto", "fast", "accurate"}
MAX_OCR_FRAMES_TO_SKIP = 10
VIDEOCR_CHUNK_SECONDS = 10 * 60
VIDEOCR_CHUNK_THRESHOLD_SECONDS = 45 * 60
VIDEOCR_PROCESS_TIMEOUT_SECONDS = 30 * 60
VIDEOCR_TRANSCODE_TIMEOUT_SECONDS = 15 * 60
TRANSLATION_CHUNK_THRESHOLD_SECONDS = 45 * 60
TRANSLATION_CHUNK_TARGET_SECONDS = 30 * 60
TRANSLATION_CHUNK_MAX_BLOCKS = 400
TRANSLATION_CHUNK_MAX_CHARS = 24_000
TRANSLATION_API_TIMEOUT_SECONDS = 180
TRANSLATION_API_MAX_ATTEMPTS = 3
TRANSLATION_API_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TRANSLATION_INSTRUCTIONS_CHARS = 16_000
TRANSLATION_PROMPT_VERSION = "zh-vi-v4-deepseek-nonthinking"
SUPPORTED_TRANSLATION_PROVIDERS = {"notebook", "deepseek", "openrouter", "custom"}
TRANSLATION_PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek API",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "requires_key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
        "requires_key": True,
    },
    "custom": {
        "label": "API OpenAI-compatible khác",
        "base_url": "",
        "model": "",
        "requires_key": False,
    },
}
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
MAX_LOG_LINES = 2_000
MAX_LOG_FILE_BYTES = 2 * 1024 * 1024
MAX_LOG_BACKUPS = 2
MAX_PROJECT_NAME_LENGTH = 100
PREVIEW_TTL_SECONDS = 6 * 60 * 60
QUEUE_SCHEMA_VERSION = 2
MAX_PERSISTED_JOBS = 500
MAX_IN_MEMORY_JOBS = MAX_PERSISTED_JOBS
ALLOWED_LOCAL_VIDEO_EXTENSIONS = {".mp4"}
ALLOWED_THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_THUMBNAIL_BYTES = 20 * 1024 * 1024
MAX_LOCAL_VIDEO_BYTES = 8 * 1024 * 1024 * 1024
APP_VERSION = "1.5.3"
GITHUB_REPOSITORY = "qvinh8726/VietSub-Studio"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
LATEST_RELEASE_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
UPDATE_CACHE_TTL_SECONDS = 15 * 60
UPDATE_DOWNLOAD_MAX_BYTES = 512 * 1024 * 1024
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
API_TOKEN = secrets.token_urlsafe(32)
API_TOKEN_HEADER = "X-VietSub-Token"
app.config["MAX_CONTENT_LENGTH"] = MAX_LOCAL_VIDEO_BYTES + 16 * 1024 * 1024

# Global status state
progress_status = {
    "status": "idle",  # "idle" | "running" | "success" | "error" | "cancelled"
    "step": "resolve",  # "resolve" | "download" | "ocr" | "translate" | "done"
    "logs": [],
    "log_base_index": 0,
    "error": "",
    "job_id": "",
    "result_ready": False,
    "result": {
        "project_name": "",
        "project_dir": "",
        "video": "",
        "source_video": "",
        "thumbnail": "",
        "raw_srt": "",
        "translated_srt": ""
    }
}
status_lock = Lock()
workflow_lock = Lock()
cancel_event = Event()
active_process_lock = Lock()
active_processes = {}
preview_lock = Lock()
preview_registry_lock = Lock()
preview_registry = {}
queue_state_lock = Lock()
workflow_jobs = []
queue_worker_thread = None
current_job_id = ""
queue_state_initialized = False
queue_revision = 0
update_cache_lock = Lock()
update_install_lock = Lock()
operation_admission_lock = Lock()
shutdown_event = Event()
update_cache = {"checked_at": 0.0, "status": None}
config_write_lock = Lock()
log_file_lock = Lock()
# Per-thread workflow context: the prefetch thread runs download helpers with its own
# cancel event, without touching the visible job console or the cancellable process list.
workflow_context = local()
prefetch_thread = None
prefetch_cancel_event = Event()
prefetch_job_id = ""
instance_lock_handle = None


class WorkflowCancelled(RuntimeError):
    pass


class EdgeLoginRequired(RuntimeError):
    pass


class QueuePersistenceError(OSError):
    pass


class VideoOCRProcessError(RuntimeError):
    def __init__(self, returncode):
        self.returncode = int(returncode)
        unsigned_code = self.returncode & 0xFFFFFFFF
        if unsigned_code == 0xC0000005:
            detail = " / 0xC0000005: tiến trình native bị access violation"
        else:
            detail = ""
        super().__init__(f"Lỗi tiến trình VideOCR (Mã thoát {self.returncode}{detail})")


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


def fetch_latest_release_payload():
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VietSub-Studio/{APP_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Không thể kiểm tra bản cập nhật từ GitHub.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Dữ liệu release từ GitHub không hợp lệ.")
    return payload


def release_asset_names(version):
    base_name = f"VietSub-Studio-Portable-v{version}.zip"
    return base_name, f"{base_name}.sha256.txt"


def select_update_assets(payload, version):
    zip_name, checksum_name = release_asset_names(version)
    assets = {
        str(item.get("name", "")): item
        for item in payload.get("assets", [])
        if isinstance(item, dict)
    }
    zip_asset = assets.get(zip_name)
    checksum_asset = assets.get(checksum_name)
    if not zip_asset or not checksum_asset:
        raise RuntimeError("Release mới chưa có đủ file ZIP và checksum để tự cập nhật.")
    for asset in (zip_asset, checksum_asset):
        validate_release_download_url(asset.get("browser_download_url"), version)
    return zip_asset, checksum_asset


def get_update_status(force=False):
    now = time.time()
    with update_cache_lock:
        cached = update_cache.get("status")
        if cached and not force and now - update_cache["checked_at"] < UPDATE_CACHE_TTL_SECONDS:
            return dict(cached)

        payload = fetch_latest_release_payload()
        latest_version = str(payload.get("tag_name", "")).strip().lstrip("v")
        parse_release_version(latest_version)
        release_url = validate_release_url(payload.get("html_url") or LATEST_RELEASE_URL)
        try:
            select_update_assets(payload, latest_version)
            install_assets_available = True
        except (RuntimeError, ValueError):
            install_assets_available = False
        status = {
            "current_version": APP_VERSION,
            "latest_version": latest_version,
            "update_available": is_newer_release(latest_version),
            "release_name": str(payload.get("name") or f"VietSub Studio v{latest_version}"),
            "release_url": release_url,
            "automatic_update_supported": supports_in_place_update() and install_assets_available,
        }
        update_cache.update({"checked_at": now, "status": status})
        return dict(status)


def reset_progress(job_id=""):
    with status_lock:
        progress_status.update({
            "status": "running",
            "step": "resolve",
            "logs": [],
            "log_base_index": 0,
            "error": "",
            "job_id": job_id,
            "result_ready": False,
            "result": {
                "project_name": "",
                "project_dir": "",
                "video": "",
                "source_video": "",
                "thumbnail": "",
                "raw_srt": "",
                "translated_srt": "",
            }
        })


def mark_current_job_persistence_error(error):
    message = f"Không thể lưu trạng thái hàng đợi: {error}"
    with queue_state_lock:
        candidate_jobs = copy.deepcopy(workflow_jobs)
        for job in candidate_jobs:
            if job["id"] == current_job_id:
                job.update({"status": "error", "error": message, "result_ready": False})
                break
        replace_queue_jobs_locked(candidate_jobs)
    with status_lock:
        progress_status.update({"status": "error", "error": message, "result_ready": False})
    return QueuePersistenceError(message)


def update_progress(*, status=None, step=None, error=None, result=None, result_ready=None):
    with status_lock:
        if status is not None:
            progress_status["status"] = status
        if step is not None:
            progress_status["step"] = step
        if error is not None:
            progress_status["error"] = error
        if result:
            progress_status["result"].update(result)
        if result_ready is not None:
            progress_status["result_ready"] = bool(result_ready)
    if current_job_id:
        try:
            with queue_state_lock:
                candidate_jobs = copy.deepcopy(workflow_jobs)
                for job in candidate_jobs:
                    if job["id"] != current_job_id:
                        continue
                    if status is not None:
                        job["status"] = status
                    if step is not None:
                        job["step"] = step
                    if error is not None:
                        job["error"] = error
                    if result:
                        job["result"].update(result)
                    if result_ready is not None:
                        job["result_ready"] = bool(result_ready)
                    commit_queue_jobs_locked(candidate_jobs, retain_on_failure=True)
                    break
        except QueuePersistenceError as persist_error:
            raise mark_current_job_persistence_error(persist_error) from persist_error


def progress_snapshot(after_log_index=0):
    with status_lock:
        after_log_index = max(after_log_index, progress_status["log_base_index"])
        start_index = min(
            len(progress_status["logs"]),
            max(0, after_log_index - progress_status["log_base_index"]),
        )
        snapshot = {
            "status": progress_status["status"],
            "step": progress_status["step"],
            "logs": list(progress_status["logs"][start_index:]),
            "log_base_index": progress_status["log_base_index"],
            "next_log_index": progress_status["log_base_index"] + len(progress_status["logs"]),
            "error": progress_status["error"],
            "job_id": progress_status.get("job_id", ""),
            "result_ready": bool(progress_status.get("result_ready")),
            "result": dict(progress_status["result"])
        }
    # Validation already ran when the job succeeded; polling only re-checks file presence.
    snapshot["result_ready"] = (
        snapshot["status"] == "success"
        and snapshot["result_ready"]
        and project_files_present(snapshot["result"])
    )
    return snapshot


def validate_notebook_url(value, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError("Link Notebook không hợp lệ.")
    normalized = value.strip()
    if not normalized and allow_empty:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or parsed.hostname != "gemini.google.com":
        raise ValueError("Link Notebook phải dùng HTTPS trên gemini.google.com.")
    if not re.fullmatch(r"/notebook/[A-Za-z0-9-]+/?", parsed.path):
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


def validate_ocr_lang(value):
    if not isinstance(value, str) or value not in SUPPORTED_OCR_LANGS:
        raise ValueError("Ngôn ngữ OCR này chưa được hỗ trợ.")
    return value


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
    except (TypeError, ValueError):
        raise ValueError("Số khung hình bỏ qua phải là số nguyên.")
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
    except (KeyError, TypeError, ValueError):
        raise ValueError("Vùng OCR phải có đủ tọa độ x, y, rộng và cao.")
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
    except (KeyError, TypeError, ValueError):
        raise ValueError("Độ phân giải video phải có chiều rộng và chiều cao.")
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
        # Share messages commonly put Chinese punctuation immediately after the URL.
        candidate = candidate.rstrip(".,;:!?)]}，。！？、")
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"}:
            continue
        if host != "douyin.com" and not host.endswith(".douyin.com"):
            continue
        if parsed.path and parsed.path != "/":
            return candidate
    raise ValueError("Không tìm thấy link video Douyin trong nội dung đã dán.")


def get_video_id(video_url):
    match = re.search(r"/(?:video|note)/(\d+)", urlparse(video_url).path)
    if not match:
        raise ValueError("Không tìm thấy ID video trong link Douyin đã giải mã.")
    return match.group(1)


def stop_process(proc, name):
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    log_to_app(f"Đã dừng tiến trình {name}.")


def register_active_process(name, proc):
    if getattr(workflow_context, "detached", False):
        return
    with active_process_lock:
        active_processes[name] = proc


def unregister_active_process(name, proc=None):
    if getattr(workflow_context, "detached", False):
        return
    with active_process_lock:
        if proc is None or active_processes.get(name) is proc:
            active_processes.pop(name, None)


def check_cancel_requested():
    event = getattr(workflow_context, "cancel_event", None) or cancel_event
    if event.is_set():
        raise WorkflowCancelled("Đã huỷ quy trình theo yêu cầu.")


def request_workflow_cancel():
    if not workflow_lock.locked():
        return False
    cancel_event.set()
    with active_process_lock:
        processes = list(active_processes.items())
    for name, proc in processes:
        try:
            stop_process(proc, name)
        except Exception:
            pass
    return True

def acquire_instance_lock():
    global instance_lock_handle
    if instance_lock_handle is not None:
        return True
    os.makedirs(CONFIG_DIR, exist_ok=True)
    handle = open(INSTANCE_LOCK_PATH, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        instance_lock_handle = handle
        return True
    except (OSError, BlockingIOError):
        handle.close()
        return False


def release_instance_lock():
    global instance_lock_handle
    handle = instance_lock_handle
    if handle is None:
        return
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()
        instance_lock_handle = None


def request_app_shutdown():
    if shutdown_event.is_set():
        return
    shutdown_event.set()
    cancel_event.set()
    prefetch_cancel_event.set()
    with active_process_lock:
        processes = list(active_processes.items())
    for name, proc in processes:
        try:
            stop_process(proc, name)
        except Exception:
            pass
    thread = prefetch_thread
    if thread and thread.is_alive() and thread is not getattr(workflow_context, "thread", None):
        thread.join(timeout=5)


def protect_config_secret(secret):
    secret = validate_translation_api_key(secret, allow_empty=True)
    if not secret:
        return ""
    raw = secret.encode("utf-8")
    if os.name != "nt":
        return "plain:" + base64.b64encode(raw).decode("ascii")

    import ctypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_byte))]

    input_buffer = ctypes.create_string_buffer(raw)
    input_blob = DataBlob(len(raw), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    ):
        raise OSError("Windows không thể mã hoá API key.")
    try:
        encrypted = ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.data)
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def unprotect_config_secret(protected):
    if not protected:
        return ""
    if not isinstance(protected, str):
        raise ValueError("API key đã lưu không hợp lệ.")
    if protected.startswith("plain:"):
        return base64.b64decode(protected[6:], validate=True).decode("utf-8")
    if not protected.startswith("dpapi:") or os.name != "nt":
        raise ValueError("API key đã lưu không thể giải mã trên máy này.")

    import ctypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_byte))]

    encrypted = base64.b64decode(protected[6:], validate=True)
    input_buffer = ctypes.create_string_buffer(encrypted)
    input_blob = DataBlob(
        len(encrypted),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output_blob = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    ):
        raise OSError("Windows không thể giải mã API key đã lưu.")
    try:
        raw = ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.data)
    return raw.decode("utf-8")


def get_translation_api_key(config):
    protected = config.get("translation_api_key_protected", "")
    if protected:
        return validate_translation_api_key(unprotect_config_secret(protected), allow_empty=True)
    # One-time compatibility for development configs created before DPAPI storage.
    return validate_translation_api_key(config.get("translation_api_key", ""), allow_empty=True)


def load_app_config():
    default_config = {
        "notebook_url": "",
        "translation_provider": "notebook",
        "translation_api_base_url": "",
        "translation_api_model": "",
        "translation_api_key_protected": "",
        "translation_instructions": "",
        "ocr_lang": "ch",
        "ocr_model_mode": "auto",
        "ocr_frames_to_skip": None,
        "output_dir": "",
        "crop_coords": None,
        "edge_background": True,
        "desktop_shortcut_initialized": False,
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved_config = json.load(f)
            if not isinstance(saved_config, dict):
                raise ValueError("Cấu hình phải là một JSON object.")
            merged_config = {**default_config, **saved_config}
            # Migrate legacy aliases to the current quality-first DeepSeek model.
            if (
                merged_config.get("translation_provider") == "deepseek"
                and str(merged_config.get("translation_api_model") or "").strip().lower()
                in {"deepseek-chat", "deepseek-v4-flash"}
            ):
                merged_config["translation_api_model"] = "deepseek-v4-pro"
            return merged_config
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            quarantine_corrupt_file(CONFIG_PATH)
    return default_config

def save_app_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    temporary_path = f"{CONFIG_PATH}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        os.replace(temporary_path, CONFIG_PATH)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def is_packaged_app():
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def supports_in_place_update():
    if not is_packaged_app():
        return False
    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    return not os.path.isdir(os.path.join(executable_dir, "_internal"))


def get_windows_known_folder(csidl, label):
    if os.name != "nt":
        raise RuntimeError(f"Thư mục {label} chỉ hỗ trợ Windows.")
    import ctypes

    folder_path = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(
        None,
        csidl,
        None,
        0,
        folder_path,
    )
    if result != 0 or not folder_path.value:
        raise OSError(result, f"Không xác định được thư mục {label} của Windows.")
    return os.path.abspath(folder_path.value)


def get_desktop_directory():
    # CSIDL_DESKTOPDIRECTORY also follows redirected OneDrive desktops.
    return get_windows_known_folder(0x0010, "Desktop")


def get_documents_directory():
    # CSIDL_PERSONAL follows the user's redirected Documents directory.
    return get_windows_known_folder(0x0005, "Documents")


def is_cloud_synced_path(path):
    if not path:
        return False
    for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(env_name)
        if root and path_is_within(path, root):
            return True
    return "onedrive" in os.path.normcase(os.path.abspath(path))


def get_default_output_directory():
    try:
        base_directory = get_documents_directory()
    except (OSError, RuntimeError):
        try:
            base_directory = get_desktop_directory()
        except (OSError, RuntimeError):
            base_directory = os.path.expanduser("~")
    return os.path.abspath(os.path.join(base_directory, "VietSub Studio"))


def desktop_shortcut_path():
    return os.path.join(get_desktop_directory(), "VietSub Studio.lnk")


def create_desktop_shortcut():
    if not is_packaged_app():
        raise RuntimeError("Shortcut chỉ được tạo từ bản VietSub Studio EXE.")

    target_path = os.path.abspath(sys.executable)
    if not os.path.isfile(target_path):
        raise FileNotFoundError("Không tìm thấy file VietSub Studio.exe hiện tại.")
    shortcut_path = desktop_shortcut_path()
    if not os.path.isdir(os.path.dirname(shortcut_path)):
        raise FileNotFoundError("Không tìm thấy thư mục Desktop của Windows.")

    shortcut_env = os.environ.copy()
    shortcut_env.update({
        "VIETSUB_SHORTCUT_PATH": shortcut_path,
        "VIETSUB_SHORTCUT_TARGET": target_path,
        "VIETSUB_SHORTCUT_WORKDIR": os.path.dirname(target_path),
        "VIETSUB_SHORTCUT_ICON": f"{target_path},0",
    })
    powershell_script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut($env:VIETSUB_SHORTCUT_PATH); "
        "$shortcut.TargetPath = $env:VIETSUB_SHORTCUT_TARGET; "
        "$shortcut.WorkingDirectory = $env:VIETSUB_SHORTCUT_WORKDIR; "
        "$shortcut.IconLocation = $env:VIETSUB_SHORTCUT_ICON; "
        "$shortcut.Description = 'VietSub Studio'; "
        "$shortcut.WindowStyle = 1; "
        "$shortcut.Save()"
    )
    completed = subprocess.run(
        [
            POWERSHELL_EXE,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            powershell_script,
        ],
        env=shortcut_env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if completed.returncode != 0 or not os.path.isfile(shortcut_path):
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(details or "Windows không thể tạo shortcut ngoài Desktop.")
    return shortcut_path


def ensure_initial_desktop_shortcut():
    if not is_packaged_app():
        return ""
    config = load_app_config()
    if config.get("desktop_shortcut_initialized"):
        return ""
    try:
        shortcut_path = create_desktop_shortcut()
        config["desktop_shortcut_initialized"] = True
        save_app_config(config)
        return shortcut_path
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        log_to_app(f"Chưa thể tạo shortcut ngoài Desktop: {error}", "error")
        return ""


def parse_update_checksum(text, expected_filename):
    for line in str(text or "").splitlines():
        match = re.fullmatch(r"\s*([0-9A-Fa-f]{64})\s+\*?(.+?)\s*", line)
        if match and match.group(2) == expected_filename:
            return match.group(1).lower()
    raise ValueError("File checksum của release không hợp lệ.")


def download_release_asset(url, destination, max_bytes):
    request_headers = {
        "Accept": "application/octet-stream",
        "User-Agent": f"VietSub-Studio/{APP_VERSION}",
    }
    digest = hashlib.sha256()
    total_bytes = 0
    req = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response, open(destination, "wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise RuntimeError("File cập nhật vượt quá kích thước cho phép.")
                digest.update(chunk)
                output.write(chunk)
    except OSError as error:
        raise RuntimeError("Không thể tải file cập nhật từ GitHub.") from error
    return digest.hexdigest(), total_bytes


def prepare_update_executable(payload):
    if not supports_in_place_update():
        raise RuntimeError("Tự cập nhật tại chỗ chỉ hỗ trợ bản portable one-file.")

    latest_version = str(payload.get("tag_name", "")).strip().lstrip("v")
    parse_release_version(latest_version)
    if not is_newer_release(latest_version):
        raise RuntimeError("Bạn đang dùng phiên bản mới nhất.")
    zip_asset, checksum_asset = select_update_assets(payload, latest_version)
    zip_name, checksum_name = release_asset_names(latest_version)

    update_dir = tempfile.mkdtemp(prefix="VietSub-Studio-update-")
    staged_path = ""
    try:
        checksum_path = os.path.join(update_dir, checksum_name)
        download_release_asset(
            checksum_asset["browser_download_url"],
            checksum_path,
            1024 * 1024,
        )
        with open(checksum_path, "r", encoding="utf-8-sig") as checksum_file:
            expected_hash = parse_update_checksum(checksum_file.read(), zip_name)

        zip_path = os.path.join(update_dir, zip_name)
        downloaded_hash, downloaded_size = download_release_asset(
            zip_asset["browser_download_url"],
            zip_path,
            UPDATE_DOWNLOAD_MAX_BYTES,
        )
        if downloaded_hash.lower() != expected_hash:
            raise RuntimeError("Checksum ZIP cập nhật không khớp; đã huỷ cài đặt.")
        api_digest = str(zip_asset.get("digest") or "").lower()
        if api_digest and api_digest != f"sha256:{downloaded_hash.lower()}":
            raise RuntimeError("Digest GitHub của file cập nhật không khớp.")
        declared_size = int(zip_asset.get("size") or 0)
        if declared_size and downloaded_size != declared_size:
            raise RuntimeError("Kích thước file cập nhật không khớp với GitHub.")

        extracted_exe = os.path.join(update_dir, "VietSub Studio.exe")
        expected_member = "VietSub Studio/VietSub Studio.exe"
        try:
            with zipfile.ZipFile(zip_path) as archive:
                members = {
                    item.filename.replace("\\", "/"): item
                    for item in archive.infolist()
                    if not item.is_dir()
                }
                member = members.get(expected_member)
                if member is None or member.file_size <= 0:
                    raise RuntimeError("ZIP cập nhật không chứa đúng file VietSub Studio.exe.")
                if member.file_size > UPDATE_DOWNLOAD_MAX_BYTES:
                    raise RuntimeError("File EXE cập nhật vượt quá kích thước cho phép.")
                with archive.open(member) as source, open(extracted_exe, "wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        except (OSError, zipfile.BadZipFile) as error:
            raise RuntimeError("Không thể đọc ZIP cập nhật đã tải.") from error

        with open(extracted_exe, "rb") as executable_file:
            if executable_file.read(2) != b"MZ":
                raise RuntimeError("File EXE cập nhật không hợp lệ.")

        target_path = os.path.abspath(sys.executable)
        if not os.path.isfile(target_path):
            raise FileNotFoundError("Không tìm thấy file VietSub Studio.exe hiện tại.")
        staged_path = os.path.join(
            os.path.dirname(target_path),
            f".VietSub Studio.update-{uuid.uuid4().hex}.exe",
        )
        shutil.copy2(extracted_exe, staged_path)
        return {
            "version": latest_version,
            "target_path": target_path,
            "staged_path": staged_path,
            "update_dir": update_dir,
        }
    except Exception:
        if staged_path and os.path.exists(staged_path):
            try:
                os.remove(staged_path)
            except OSError:
                pass
        shutil.rmtree(update_dir, ignore_errors=True)
        raise


def launch_update_helper(update_package):
    helper_env = os.environ.copy()
    helper_env.update({
        "VIETSUB_UPDATE_PID": str(os.getpid()),
        "VIETSUB_UPDATE_TARGET": update_package["target_path"],
        "VIETSUB_UPDATE_STAGED": update_package["staged_path"],
        "VIETSUB_UPDATE_TEMP": update_package["update_dir"],
    })
    helper_script = r"""
$ErrorActionPreference = 'Stop'
$appPid = [int]$env:VIETSUB_UPDATE_PID
$target = $env:VIETSUB_UPDATE_TARGET
$staged = $env:VIETSUB_UPDATE_STAGED
$updateTemp = $env:VIETSUB_UPDATE_TEMP
$backup = "$target.old"
$targetDir = Split-Path -Parent $target
Wait-Process -Id $appPid -ErrorAction SilentlyContinue
$replaced = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        if (-not (Test-Path -LiteralPath $target)) {
            if (Test-Path -LiteralPath $backup) {
                Move-Item -LiteralPath $backup -Destination $target -Force
            } else {
                throw 'Không còn file ứng dụng gốc để thay thế.'
            }
        }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
        Move-Item -LiteralPath $target -Destination $backup -Force
        try {
            Move-Item -LiteralPath $staged -Destination $target -Force
        } catch {
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
            if (Test-Path -LiteralPath $backup) {
                Move-Item -LiteralPath $backup -Destination $target -Force
            }
            throw
        }
        $replaced = $true
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $replaced) {
    if (-not (Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $target -Force
    }
    if (Test-Path -LiteralPath $target) {
        Start-Process -FilePath $target -WorkingDirectory $targetDir
    }
    exit 1
}
try {
    $newProcess = Start-Process -FilePath $target -WorkingDirectory $targetDir -PassThru
    Start-Sleep -Seconds 8
    if ($newProcess.HasExited) { throw 'Bản cập nhật thoát trước khi khởi động ổn định.' }
} catch {
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    if (Test-Path -LiteralPath $backup) {
        Move-Item -LiteralPath $backup -Destination $target -Force
        Start-Process -FilePath $target -WorkingDirectory $targetDir
    }
    exit 1
}
if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
if (Test-Path -LiteralPath $updateTemp) { Remove-Item -LiteralPath $updateTemp -Recurse -Force }
"""
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    return subprocess.Popen(
        [
            POWERSHELL_EXE,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            helper_script,
        ],
        env=helper_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )


def schedule_app_exit(delay_seconds=1.5):
    def exit_after_response():
        time.sleep(delay_seconds)
        os._exit(0)

    Thread(target=exit_after_response, daemon=True).start()

def get_douzy_download_dir():
    if not os.path.exists(DOUZY_CONFIG):
        return ""
    try:
        with open(DOUZY_CONFIG, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("path:"):
                    return line.split(":", 1)[1].strip().strip('"\'')
    except (OSError, UnicodeError):
        pass
    return ""

def write_durable_log(message):
    try:
        with log_file_lock:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            if os.path.isfile(LOG_PATH) and os.path.getsize(LOG_PATH) >= MAX_LOG_FILE_BYTES:
                oldest = f"{LOG_PATH}.{MAX_LOG_BACKUPS}"
                if os.path.exists(oldest):
                    os.remove(oldest)
                for index in range(MAX_LOG_BACKUPS - 1, 0, -1):
                    source = f"{LOG_PATH}.{index}"
                    if os.path.exists(source):
                        os.replace(source, f"{LOG_PATH}.{index + 1}")
                os.replace(LOG_PATH, f"{LOG_PATH}.1")
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} {message}\n")
    except OSError:
        pass


def log_to_app(msg, log_type="system"):
    quiet = getattr(workflow_context, "quiet", False)
    prefix = "[PREFETCH] " if quiet else ""
    console_message = f"{prefix}[{log_type.upper()}] {msg}"
    write_durable_log(console_message)
    if sys.stdout is not None:
        try:
            print(console_message, flush=True)
        except (OSError, UnicodeError):
            # A legacy Windows console can reject Vietnamese output; logging must not stop a job.
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe_message = console_message.encode(encoding, errors="backslashreplace").decode(encoding)
            try:
                print(safe_message, flush=True)
            except (OSError, UnicodeError):
                pass
    if quiet:
        return
    with status_lock:
        if log_type == "system":
            progress_status["logs"].append(f"[*] {msg}")
        elif log_type == "ocr":
            progress_status["logs"].append(f"[VideOCR] {msg}")
        elif log_type == "error":
            progress_status["logs"].append(f"[!] {msg}")
        overflow = len(progress_status["logs"]) - MAX_LOG_LINES
        if overflow > 0:
            del progress_status["logs"][:overflow]
            progress_status["log_base_index"] += overflow

def get_edge_debug_port():
    return EDGE_DEBUG_PORT


def edge_debug_url(path=""):
    return f"http://127.0.0.1:{get_edge_debug_port()}{path}"


def is_valid_edge_debug_payload(payload):
    if not isinstance(payload, dict):
        return False
    browser = str(payload.get("Browser") or "").lower()
    websocket_url = str(payload.get("webSocketDebuggerUrl") or "")
    try:
        parsed_websocket = urlparse(websocket_url)
        websocket_port = parsed_websocket.port
    except ValueError:
        return False
    return (
        (browser.startswith("edg/") or browser.startswith("edge/"))
        and parsed_websocket.scheme == "ws"
        and parsed_websocket.hostname == "127.0.0.1"
        and websocket_port == get_edge_debug_port()
        and parsed_websocket.username is None
        and parsed_websocket.password is None
        and not parsed_websocket.query
        and not parsed_websocket.fragment
        and re.fullmatch(r"/devtools/browser/[^/]+", parsed_websocket.path) is not None
    )


def split_windows_command_line(command_line):
    if os.name != "nt" or not command_line:
        return []
    import ctypes

    argument_count = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    arguments = command_line_to_argv(command_line, ctypes.byref(argument_count))
    if not arguments:
        return []
    try:
        return [arguments[index] for index in range(argument_count.value)]
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(arguments, ctypes.c_void_p))


def edge_command_line_uses_profile(command_line):
    profile_values = []
    arguments = split_windows_command_line(command_line)
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        lowered = argument.lower()
        if lowered == "--user-data-dir":
            if index + 1 >= len(arguments):
                return False
            profile_values.append(arguments[index + 1])
            index += 2
            continue
        if lowered.startswith("--user-data-dir="):
            profile_values.append(argument.split("=", 1)[1])
        index += 1
    if len(profile_values) != 1 or not profile_values[0]:
        return False
    actual_profile = os.path.abspath(os.path.normpath(profile_values[0]))
    expected_profile = os.path.abspath(os.path.normpath(USER_DATA_DIR_DEBUG))
    return os.path.normcase(actual_profile) == os.path.normcase(expected_profile)


def edge_debug_owner_uses_profile():
    if os.name != "nt":
        return False
    debug_port = get_edge_debug_port()
    powershell_script = (
        f"$connection = Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 "
        f"-LocalPort {debug_port} -ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if (-not $connection) { exit 1 }; "
        "$process = Get-CimInstance Win32_Process -Filter "
        "(\"ProcessId=\" + $connection.OwningProcess) -ErrorAction SilentlyContinue; "
        "if (-not $process.CommandLine) { exit 1 }; "
        "[PSCustomObject]@{ Name = $process.Name; ExecutablePath = $process.ExecutablePath; "
        "CommandLine = $process.CommandLine } | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [
                POWERSHELL_EXE,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                powershell_script,
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        metadata = json.loads(completed.stdout or "")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict):
        return False
    expected_executable = resolve_edge_executable()
    if not expected_executable:
        return False
    expected_executable = os.path.abspath(expected_executable)
    actual_executable = os.path.abspath(str(metadata.get("ExecutablePath") or ""))
    return (
        completed.returncode == 0
        and str(metadata.get("Name") or "").lower() == "msedge.exe"
        and os.path.normcase(actual_executable) == os.path.normcase(expected_executable)
        and edge_command_line_uses_profile(metadata.get("CommandLine"))
    )


def check_edge_status():
    try:
        req = urllib.request.Request(edge_debug_url("/json/version"))
        with urllib.request.urlopen(req, timeout=1.5) as response:
            payload = json.load(response)
        return is_valid_edge_debug_payload(payload) and edge_debug_owner_uses_profile()
    except Exception:
        return False

def edge_executable_candidates():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    if LOCALAPPDATA:
        candidates.append(
            os.path.join(LOCALAPPDATA, "Microsoft", "Edge", "Application", "msedge.exe")
        )
    return candidates


def resolve_edge_executable():
    return next(
        (
            os.path.abspath(candidate)
            for candidate in edge_executable_candidates()
            if os.path.isfile(candidate)
        ),
        "",
    )


def ensure_edge_running(notebook_url, background=None):
    check_cancel_requested()
    if check_edge_status():
        log_to_app("Trình duyệt Edge debug đang mở sẵn.")
        return True

    if background is None:
        background = bool(load_app_config().get("edge_background", True))

    log_to_app("Trình duyệt Edge debug chưa mở. Đang tự động kích hoạt...")
    edge_bin = resolve_edge_executable()
    if not edge_bin:
        log_to_app("Không tìm thấy msedge.exe ở vị trí tin cậy.", "error")
        return False

    debug_port = get_edge_debug_port()
    cmd = [
        edge_bin,
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={USER_DATA_DIR_DEBUG}",
    ]
    if background:
        cmd.extend([
            "--start-minimized",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ])
    cmd.append(notebook_url)
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(cmd, creationflags=creation_flags)
        # Wait for port to bind
        for _ in range(40):
            check_cancel_requested()
            time.sleep(0.25)
            if check_edge_status():
                log_to_app("Kích hoạt Edge thành công và đã kết nối cổng debug.")
                return True
        raise RuntimeError(f"Trình duyệt đã bật nhưng cổng debug {debug_port} không phản hồi.")
    except WorkflowCancelled:
        raise
    except Exception as e:
        log_to_app(f"Lỗi kích hoạt Edge tự động: {e}", "error")
        return False


def set_edge_window_visibility(context, page, visible):
    try:
        session = context.new_cdp_session(page)
        window_id = session.send("Browser.getWindowForTarget")["windowId"]
        if visible:
            session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "normal"}},
            )
            session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {"left": 80, "top": 60, "width": 1280, "height": 900},
                },
            )
            page.bring_to_front()
        else:
            session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "minimized"}},
            )
        session.detach()
        return True
    except Exception:
        return False


GEMINI_INPUT_SELECTORS = (
    "div.ql-editor",
    "div[contenteditable='true']",
    "[role='textbox']",
    "textarea",
)

GEMINI_LOGIN_SELECTORS = (
    "button:has-text('Đăng nhập')",
    "button:has-text('Sign in')",
    "a:has-text('Đăng nhập')",
    "a:has-text('Sign in')",
    "[role='button']:has-text('Đăng nhập')",
    "[role='button']:has-text('Sign in')",
)


def first_visible_locator(page, selectors, *, require_enabled=False):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if not candidate.is_visible():
                    continue
                if require_enabled and not candidate.is_enabled():
                    continue
                return candidate
        except Exception:
            pass
    return None


def edge_page_needs_login(page):
    hostname = (urlparse(page.url).hostname or "").lower()
    if hostname == "accounts.google.com" or hostname.endswith(".accounts.google.com"):
        return True
    return first_visible_locator(page, GEMINI_LOGIN_SELECTORS) is not None


def wait_for_gemini_prompt(page, timeout_seconds=15):
    deadline = time.monotonic() + timeout_seconds
    while True:
        check_cancel_requested()
        prompt_input = first_visible_locator(page, GEMINI_INPUT_SELECTORS)
        if prompt_input is not None or edge_page_needs_login(page):
            return prompt_input
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(500)


def get_sync_playwright():
    # Imported lazily so app startup does not pay the playwright import cost.
    from playwright.sync_api import sync_playwright
    return sync_playwright


def show_edge_for_login(notebook_url):
    notebook_url = validate_notebook_url(notebook_url)
    if not ensure_edge_running(notebook_url, background=False):
        raise RuntimeError("Không thể mở Edge để đăng nhập.")
    with get_sync_playwright()() as playwright:
        browser = playwright.chromium.connect_over_cdp(edge_debug_url())
        if not browser.contexts:
            raise RuntimeError("Không tìm thấy profile Edge đang hoạt động.")
        context = browser.contexts[0]
        notebook_id = urlparse(notebook_url).path.rstrip("/").rsplit("/", 1)[-1]
        page = next((item for item in context.pages if notebook_id in item.url), None)
        if page is None:
            page = context.new_page()
            page.goto(notebook_url)
        if not set_edge_window_visibility(context, page, True):
            page.bring_to_front()
    return True

# Sidecar & automation functions
def queue_process_output(stream, output_queue):
    try:
        for line in iter(stream.readline, ""):
            try:
                output_queue.put(line, timeout=0.1)
            except Full:
                try:
                    output_queue.get_nowait()
                except Empty:
                    pass
                try:
                    output_queue.put_nowait(line)
                except Full:
                    pass
    finally:
        stream.close()


def start_douzy_sidecar():
    check_cancel_requested()
    if not os.path.exists(SIDECAR_BIN):
        raise FileNotFoundError(f"Không tìm thấy sidecar Douzy tại: {SIDECAR_BIN}")
    if not os.path.exists(DOUZY_CONFIG):
        raise FileNotFoundError(f"Không tìm thấy file config Douzy tại: {DOUZY_CONFIG}")

    token = str(uuid.uuid4())
    env = os.environ.copy()
    env["DOUYIN_SIDECAR_TOKEN"] = token
    env["DOUYIN_SIDECAR_MODE"] = "free"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    log_to_app("Đang khởi chạy ngầm sidecar Douzy...")
    proc = subprocess.Popen(
        [SIDECAR_BIN, "--serve", "--serve-port", "0", "--config", DOUZY_CONFIG],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    )
    register_active_process("sidecar Douzy", proc)

    output_queue = Queue(maxsize=1_000)
    Thread(target=queue_process_output, args=(proc.stdout, output_queue), daemon=True).start()
    recent_output = []
    deadline = time.monotonic() + 15
    port = None
    try:
        while time.monotonic() < deadline:
            check_cancel_requested()
            try:
                line = output_queue.get(timeout=min(0.25, deadline - time.monotonic()))
                recent_output.append(line.strip())
                recent_output = recent_output[-20:]
                match = re.match(r"^DOUYIN_SIDECAR_READY port=(\d+)", line.strip())
                if match:
                    port = int(match.group(1))
                    break
            except Empty:
                pass

            if proc.poll() is not None:
                details = "\n".join(recent_output)
                raise RuntimeError(f"Sidecar dừng đột ngột (code {proc.returncode}). {details}")

        if port is None:
            raise TimeoutError("Sidecar Douzy khởi động quá thời gian chờ (15s).")
    except BaseException:
        if proc.poll() is None:
            try:
                stop_process(proc, "sidecar Douzy")
            except Exception:
                pass
        unregister_active_process("sidecar Douzy", proc)
        raise

    log_to_app(f"Sidecar Douzy đã chạy trên port {port} (PID: {proc.pid})")
    return proc, port, token

def trigger_download(port, token, video_url, scope="single"):
    check_cancel_requested()
    log_to_app(f"Gửi yêu cầu tải video đến sidecar...")
    url = f"http://127.0.0.1:{port}/api/v1/download"
    req_data = json.dumps({"url": video_url, "scope": scope}).encode("utf-8")

    req = urllib.request.Request(url, data=req_data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(req, timeout=15) as response:
        res = json.loads(response.read().decode("utf-8"))
        job_id = res.get("job_id")
        if not job_id:
            raise RuntimeError(f"Gửi lệnh tải thất bại: {res}")
        log_to_app(f"Đã tạo lệnh tải thành công. ID Job: {job_id}")
        return job_id

def poll_download_job(port, token, job_id):
    log_to_app("Đang chờ quá trình tải video hoàn tất...")
    url = f"http://127.0.0.1:{port}/api/v1/jobs/{job_id}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")

    start_time = time.monotonic()
    consecutive_failures = 0
    last_status = None
    while True:
        check_cancel_requested()
        if time.monotonic() - start_time > 300:
            raise TimeoutError("Quá trình tải video bị quá thời gian chờ (5 phút).")

        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                job = json.loads(response.read().decode("utf-8"))
                consecutive_failures = 0
                status = job.get("status")
                if status != last_status:
                    log_to_app(f"Trạng thái tải: {status}...")
                    last_status = status
                if status == "success":
                    return job
                elif status in ["failed", "cancelled"]:
                    err = job.get("error", "Lỗi không xác định")
                    raise RuntimeError(f"Tải video thất bại: {err}")
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                raise RuntimeError(f"Mất kết nối sidecar Douzy: {e}") from e
            log_to_app(f"Kết nối sidecar lỗi, đang thử lại ({consecutive_failures}/3): {e}", "error")
            time.sleep(2)
            continue

        time.sleep(1.0)

def get_downloaded_video_file(start_time_epoch, video_id, allow_existing=False):
    log_to_app("Truy vấn đường dẫn lưu video từ cơ sở dữ liệu...")
    if not os.path.exists(DOUZY_DB):
        raise FileNotFoundError(f"Không tìm thấy file database tại: {DOUZY_DB}")

    conn = sqlite3.connect(DOUZY_DB, timeout=2)
    try:
        query = "SELECT file_path FROM aweme WHERE aweme_id = ?"
        parameters = [video_id]
        if not allow_existing:
            query += " AND download_time >= ?"
            parameters.append(int(start_time_epoch))
        query += " ORDER BY download_time DESC LIMIT 1"
        cursor = conn.execute(query, parameters)
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    if not row:
        raise FileNotFoundError("Không tìm thấy bản ghi Douzy của đúng video vừa tải.")

    stored_path = row[0]
    video_file = resolve_video_file_path(stored_path)

    if not os.path.exists(video_file):
        raise FileNotFoundError(f"Không tìm thấy file video tại: {video_file}")

    log_to_app(f"Đã tìm thấy file video: {video_file}")
    return video_file


def resolve_video_file_path(stored_path):
    if not isinstance(stored_path, str) or not stored_path.strip():
        raise FileNotFoundError("Bản ghi Douzy không có đường dẫn video hợp lệ.")
    stored_path = os.path.abspath(stored_path)
    if os.path.isfile(stored_path) and stored_path.lower().endswith(".mp4"):
        return stored_path

    folder_name = os.path.basename(os.path.normpath(stored_path))
    expected_file = os.path.join(stored_path, folder_name + ".mp4")
    if os.path.isfile(expected_file):
        return expected_file
    if os.path.isdir(stored_path):
        mp4_files = [
            os.path.join(stored_path, name)
            for name in os.listdir(stored_path)
            if name.lower().endswith(".mp4") and os.path.isfile(os.path.join(stored_path, name))
        ]
        if len(mp4_files) == 1:
            return mp4_files[0]
        matching = [
            path for path in mp4_files
            if os.path.splitext(os.path.basename(path))[0] == folder_name
            or os.path.splitext(os.path.basename(path))[0].startswith(
                tuple(f"{folder_name}{separator}" for separator in (".", "_", "-"))
            )
        ]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            raise FileNotFoundError("Douzy trả về nhiều file MP4 không thể xác định duy nhất.")
    return expected_file


def get_video_title(video_id):
    if not os.path.exists(DOUZY_DB):
        return ""
    try:
        conn = sqlite3.connect(DOUZY_DB, timeout=2)
        try:
            row = conn.execute(
                "SELECT title FROM aweme WHERE aweme_id = ? ORDER BY download_time DESC LIMIT 1",
                (video_id,),
            ).fetchone()
        finally:
            conn.close()
        return str(row[0] or "").strip() if row else ""
    except (OSError, sqlite3.Error):
        return ""


def find_douzy_thumbnail_file(video_path):
    if not isinstance(video_path, str) or not video_path:
        return ""
    video_path = os.path.abspath(video_path)
    video_dir = os.path.dirname(video_path)
    video_stem = os.path.splitext(os.path.basename(video_path))[0]
    if not os.path.isdir(video_dir):
        return ""

    for suffix in ("_cover", "_thumbnail", "_thumb"):
        for extension in ALLOWED_THUMBNAIL_EXTENSIONS:
            candidate = os.path.join(video_dir, video_stem + suffix + extension)
            if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                return candidate
    return ""


def validate_douzy_thumbnail_url(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    allowed_host = any(
        host == domain or host.endswith("." + domain)
        for domain in ("douyinpic.com", "douyincdn.com")
    )
    if parsed.scheme != "https" or not allowed_host:
        return ""
    return value.strip()


def get_douzy_thumbnail_url(video_id):
    if not os.path.exists(DOUZY_DB):
        return ""
    try:
        conn = sqlite3.connect(DOUZY_DB, timeout=2)
        try:
            row = conn.execute(
                "SELECT metadata FROM aweme WHERE aweme_id = ? ORDER BY download_time DESC LIMIT 1",
                (video_id,),
            ).fetchone()
        finally:
            conn.close()
        metadata = json.loads(row[0]) if row and row[0] else {}
        video = metadata.get("video") if isinstance(metadata, dict) else {}
        if not isinstance(video, dict):
            return ""
        for key in ("cover_original_scale", "origin_cover", "cover"):
            cover = video.get(key)
            urls = cover.get("url_list") if isinstance(cover, dict) else []
            if not isinstance(urls, list):
                continue
            for url in urls:
                validated = validate_douzy_thumbnail_url(url)
                if validated:
                    return validated
    except (OSError, sqlite3.Error, UnicodeError, json.JSONDecodeError):
        pass
    return ""


def thumbnail_extension(data):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def download_thumbnail(url, destination_without_extension):
    url = validate_douzy_thumbnail_url(url)
    if not url:
        raise ValueError("URL thumbnail không thuộc máy chủ ảnh Douyin hợp lệ.")
    request_headers = {"User-Agent": "Mozilla/5.0 VietSub-Studio"}
    request_object = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request_object, timeout=20) as response:
        final_url = response.geturl() if hasattr(response, "geturl") else url
        if not validate_douzy_thumbnail_url(final_url):
            raise ValueError("Thumbnail chuyển hướng ra ngoài máy chủ ảnh Douyin.")
        declared_size = response.headers.get("Content-Length")
        if declared_size and int(declared_size) > MAX_THUMBNAIL_BYTES:
            raise ValueError("Thumbnail vượt quá giới hạn 20 MB.")
        data = response.read(MAX_THUMBNAIL_BYTES + 1)
        if len(data) > MAX_THUMBNAIL_BYTES:
            raise ValueError("Thumbnail vượt quá giới hạn 20 MB.")
        extension = thumbnail_extension(data)
    if not data or not extension:
        raise ValueError("Dữ liệu thumbnail không phải ảnh JPG, PNG hoặc WebP hợp lệ.")

    destination = destination_without_extension + extension
    temporary_path = f"{destination}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "wb") as handle:
            handle.write(data)
        os.replace(temporary_path, destination)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
    return destination


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


def prepare_project_thumbnail(source_video, video_id, project_dir, project_name):
    source_thumbnail = find_douzy_thumbnail_file(source_video)
    destination_base = os.path.join(project_dir, project_name + ".thumbnail")
    try:
        if source_thumbnail:
            detected_extension = validate_local_thumbnail_file(source_thumbnail)
            destination = destination_base + detected_extension
            shutil.copy2(source_thumbnail, destination)
            log_to_app(f"Đã thêm thumbnail vào bộ kết quả: {destination}")
            return destination

        thumbnail_url = get_douzy_thumbnail_url(video_id)
        if thumbnail_url:
            destination = download_thumbnail(thumbnail_url, destination_base)
            log_to_app(f"Đã tải thumbnail vào bộ kết quả: {destination}")
            return destination
    except (OSError, ValueError, urllib.error.URLError, TimeoutError) as error:
        log_to_app(f"Không thể lấy thumbnail, tiếp tục xử lý video: {error}", "error")
    return ""


def wait_for_downloaded_video_file(start_time_epoch, video_id, allow_existing=False, timeout=20):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        check_cancel_requested()
        try:
            return get_downloaded_video_file(start_time_epoch, video_id, allow_existing=allow_existing)
        except FileNotFoundError as error:
            last_error = error
            time.sleep(1)
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                raise
            last_error = error
            log_to_app("Database Douzy đang bận, chờ ghi xong rồi thử lại.")
            time.sleep(1)
    raise last_error or TimeoutError("Douzy chưa ghi xong thông tin video tải về.")


def allocate_project_directory(output_root, project_name):
    os.makedirs(output_root, exist_ok=True)
    for suffix in range(1, 1000):
        final_name = project_name if suffix == 1 else f"{project_name} ({suffix})"
        project_dir = os.path.join(output_root, final_name)
        try:
            os.mkdir(project_dir)
            return final_name, project_dir
        except FileExistsError:
            continue
    raise FileExistsError("Có quá nhiều bộ file trùng tên trong thư mục kết quả.")


def try_hardlink_video(source_path, destination_path):
    # An app-managed upload is immutable, so a same-volume hard link replaces a
    # full data copy with an instant metadata operation.
    try:
        os.link(source_path, destination_path)
        return True
    except OSError:
        return False


def copy_file_atomic(source_path, destination_path):
    temporary_path = f"{destination_path}.{uuid.uuid4().hex}.part"
    total_size = max(os.path.getsize(source_path), 1)
    copied_size = 0
    last_reported_bucket = 0
    try:
        with open(source_path, "rb") as source, open(temporary_path, "wb") as destination:
            while True:
                check_cancel_requested()
                chunk = source.read(4 * 1024 * 1024)
                if not chunk:
                    break
                destination.write(chunk)
                copied_size += len(chunk)
                bucket = min(10, int(copied_size * 10 / total_size))
                if bucket > last_reported_bucket:
                    last_reported_bucket = bucket
                    log_to_app(f"Sao chép video: {bucket * 10}%")
        shutil.copystat(source_path, temporary_path)
        os.replace(temporary_path, destination_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def move_file_safely(source_path, destination_path):
    source_path = os.path.abspath(source_path)
    destination_path = os.path.abspath(destination_path)
    if os.path.normcase(source_path) == os.path.normcase(destination_path):
        return destination_path

    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    try:
        os.replace(source_path, destination_path)
    except OSError:
        copy_file_atomic(source_path, destination_path)
        os.remove(source_path)
    return destination_path


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


def path_is_within(path, directory):
    if not path or not directory:
        return False
    try:
        common = os.path.commonpath((os.path.abspath(path), os.path.abspath(directory)))
        return os.path.normcase(common) == os.path.normcase(os.path.abspath(directory))
    except (OSError, ValueError):
        return False


def should_move_douzy_source(source_video):
    download_root = get_douzy_download_dir()
    source_dir = os.path.dirname(os.path.abspath(source_video))
    return (
        path_is_within(source_video, download_root)
        and not os.path.isfile(os.path.join(source_dir, "project.json"))
    )


def project_asset_destination(source_path, source_video, project):
    filename = os.path.basename(source_path)
    stem, extension = os.path.splitext(filename)
    source_stem = os.path.splitext(os.path.basename(source_video))[0]
    project_name = project["project_name"]

    if os.path.normcase(source_path) == os.path.normcase(source_video):
        return project["video_destination"]
    if stem.startswith(source_stem):
        asset_name = stem[len(source_stem):].strip(" ._-").lower()
        if (
            asset_name in {"cover", "origin_cover", "thumbnail", "thumb"}
            and extension.lower() in ALLOWED_THUMBNAIL_EXTENSIONS
        ):
            normalized_extension = ".jpg" if extension.lower() == ".jpeg" else extension.lower()
            return os.path.join(project["project_dir"], project_name + ".thumbnail" + normalized_extension)
        if asset_name:
            safe_asset_name = sanitize_project_name(asset_name, fallback="asset").replace(" ", "-")
            destination = os.path.join(
                project["project_dir"], f"{project_name}.{safe_asset_name}{extension.lower()}"
            )
            reserved_paths = {
                os.path.normcase(project["raw_srt"]),
                os.path.normcase(project["translated_srt"]),
                os.path.normcase(os.path.join(project["project_dir"], "project.json")),
            }
            if os.path.normcase(destination) in reserved_paths:
                destination = os.path.join(
                    project["project_dir"],
                    f"{project_name}.douzy-{safe_asset_name}{extension.lower()}",
                )
            return destination
    return os.path.join(project["project_dir"], filename)


def update_douzy_video_path(video_id, destination_video):
    if not video_id or not os.path.isfile(DOUZY_DB):
        return False
    try:
        conn = sqlite3.connect(DOUZY_DB, timeout=3)
        try:
            cursor = conn.execute(
                "UPDATE aweme SET file_path = ? WHERE id = "
                "(SELECT id FROM aweme WHERE aweme_id = ? ORDER BY download_time DESC LIMIT 1)",
                (destination_video, video_id),
            )
            conn.commit()
            if cursor.rowcount != 1:
                log_to_app("Douzy không có đúng một bản ghi để cập nhật đường dẫn video.", "error")
                return False
            row = conn.execute(
                "SELECT file_path FROM aweme WHERE aweme_id = ? ORDER BY download_time DESC LIMIT 1",
                (video_id,),
            ).fetchone()
            updated = bool(row and os.path.normcase(os.path.abspath(row[0])) == os.path.normcase(os.path.abspath(destination_video)))
            if not updated:
                log_to_app("Douzy chưa xác nhận đường dẫn video mới sau khi cập nhật.", "error")
            return updated
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        log_to_app(f"Không thể cập nhật đường dẫn mới trong lịch sử Douzy: {error}", "error")
        return False


def replace_source_video_references(old_path, new_path):
    old_path = os.path.abspath(old_path)
    with preview_registry_lock:
        for preview in preview_registry.values():
            if os.path.normcase(str(preview.get("source_video") or "")) == os.path.normcase(old_path):
                preview["source_video"] = new_path
    with queue_state_lock:
        candidate_jobs = copy.deepcopy(workflow_jobs)
        for job in candidate_jobs:
            if os.path.normcase(str(job.get("source_video") or "")) == os.path.normcase(old_path):
                job["source_video"] = new_path
        try:
            commit_queue_jobs_locked(candidate_jobs)
        except OSError as error:
            log_to_app(f"Chưa thể lưu đường dẫn video mới vào hàng đợi: {error}", "error")


def finalize_project_assets(project, video_id=""):
    if not project.get("move_source_video"):
        return project

    source_video = os.path.abspath(project["video"])
    destination_video = os.path.abspath(project["video_destination"])
    if not os.path.isfile(source_video):
        if os.path.isfile(destination_video):
            project.update({
                "video": destination_video,
                "source_video": destination_video,
                "move_source_video": False,
            })
            return project
        raise FileNotFoundError("Video Douzy không còn tồn tại để chuyển vào thư mục dự án.")

    source_dir = os.path.dirname(source_video)
    source_stem = os.path.splitext(os.path.basename(source_video))[0]
    candidates = []
    source_norm = os.path.normcase(source_video)
    for name in os.listdir(source_dir):
        candidate = os.path.join(source_dir, name)
        if not os.path.isfile(candidate):
            continue
        candidate_norm = os.path.normcase(candidate)
        candidate_stem = os.path.splitext(name)[0]
        related_name = (
            candidate_stem == source_stem
            or candidate_stem.startswith(tuple(f"{source_stem}{separator}" for separator in (".", "_", "-")))
        )
        if candidate_norm == source_norm or related_name:
            candidates.append(candidate)

    # Preflight destinations before moving anything to prevent accidental overwrite.
    planned_destinations = {}
    for candidate in candidates:
        destination = os.path.abspath(project_asset_destination(candidate, source_video, project))
        destination_key = os.path.normcase(destination)
        previous = planned_destinations.get(destination_key)
        if previous and os.path.normcase(previous) != os.path.normcase(candidate):
            raise FileExistsError(f"Hai asset Douzy cùng đích: {os.path.basename(candidate)}")
        planned_destinations[destination_key] = candidate
        if os.path.exists(destination) and os.path.normcase(destination) != os.path.normcase(candidate):
            if not files_have_same_content(candidate, destination):
                raise FileExistsError(f"Đích asset đã tồn tại và khác nội dung: {destination}")

    # Move the large MP4 last so an ancillary-asset error leaves the retry source intact.
    candidates.sort(key=lambda path: os.path.normcase(path) == source_norm)
    log_to_app("Đang chuyển video và asset Douzy vào thư mục dự án...")
    moved_video = ""
    for candidate in candidates:
        destination = os.path.abspath(project_asset_destination(candidate, source_video, project))
        if os.path.exists(destination) and os.path.normcase(destination) != os.path.normcase(candidate):
            os.remove(candidate)
            moved_path = destination
        else:
            moved_path = move_file_safely(candidate, destination)
        if os.path.normcase(candidate) == source_norm:
            moved_video = moved_path
        elif ".thumbnail" in os.path.basename(moved_path).lower():
            project["thumbnail"] = moved_path

    if not moved_video:
        moved_video = move_file_safely(source_video, destination_video)
    try:
        os.rmdir(source_dir)
    except OSError:
        pass

    project["original_source_video"] = source_video
    project["video"] = moved_video
    project["source_video"] = moved_video
    project["move_source_video"] = False
    update_douzy_video_path(video_id, moved_video)
    replace_source_video_references(source_video, moved_video)
    log_to_app(f"Đã gom toàn bộ file Douzy vào: {project['project_dir']}")
    return project


def prepare_project_files(
    source_video,
    requested_name,
    video_id,
    video_title="",
    output_dir="",
    defer_video_move=False,
    include_thumbnail=False,
):
    check_cancel_requested()
    if not os.path.isfile(source_video) or os.path.getsize(source_video) <= 0:
        raise FileNotFoundError(f"Không tìm thấy file video hoàn chỉnh: {source_video}")
    fallback_name = video_title or f"Douyin {video_id}"
    sanitized_name = sanitize_project_name(requested_name, fallback=fallback_name)
    default_root = get_default_output_directory()
    output_root = validate_output_dir(output_dir) or default_root
    project_name, project_dir = allocate_project_directory(output_root, sanitized_name)
    video_destination = os.path.join(project_dir, project_name + ".mp4")
    video_path = os.path.abspath(source_video) if defer_video_move else video_destination
    raw_srt_path = os.path.join(project_dir, project_name + ".raw.srt")
    translated_srt_path = os.path.join(project_dir, project_name + ".vi.srt")

    log_to_app(f"Tạo bộ file đồng bộ với tên: {project_name}")
    try:
        check_cancel_requested()
        if defer_video_move:
            log_to_app("Dùng video tại Douzy trong lúc xử lý; sẽ chuyển vào dự án sau khi dịch xong.")
        elif is_managed_local_video(source_video) and try_hardlink_video(source_video, video_path):
            log_to_app("Đã liên kết video local vào thư mục kết quả, không cần sao chép lại.")
        else:
            log_to_app("Đang sao chép video vào thư mục kết quả...")
            copy_file_atomic(source_video, video_path)
        thumbnail_path = (
            prepare_project_thumbnail(source_video, video_id, project_dir, project_name)
            if include_thumbnail else ""
        )
    except Exception:
        try:
            os.rmdir(project_dir)
        except OSError:
            pass
        raise

    return {
        "project_name": project_name,
        "project_dir": project_dir,
        "video": video_path,
        "source_video": os.path.abspath(source_video),
        "video_destination": video_destination,
        "move_source_video": bool(defer_video_move),
        "thumbnail": thumbnail_path,
        "raw_srt": raw_srt_path,
        "translated_srt": translated_srt_path,
    }


def write_project_manifest(project, *, video_id, source_url, status, error=""):
    manifest_path = os.path.join(project["project_dir"], "project.json")
    manifest = {
        "project_name": project["project_name"],
        "video_id": video_id,
        "source_url": source_url,
        "status": status,
        "error": error,
        "video": project["video"],
        "source_video": project["source_video"],
        "original_source_video": project.get("original_source_video", ""),
        "video_destination": project.get("video_destination", project["video"]),
        "move_source_video": bool(project.get("move_source_video")),
        "thumbnail": project.get("thumbnail", ""),
        "raw_srt": project["raw_srt"],
        "translated_srt": project["translated_srt"],
        "updated_at": int(time.time()),
    }
    temporary_path = f"{manifest_path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(temporary_path, manifest_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def get_videocr_crop_coords():
    if not os.path.exists(VIDEOCR_CONFIG):
        return None
    crop_boxes = None
    with open(VIDEOCR_CONFIG, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("--saved_crop_boxes"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    try:
                        crop_boxes = ast.literal_eval(parts[1].strip())
                    except Exception:
                        pass
    if crop_boxes and len(crop_boxes) > 0:
        coords = crop_boxes[0].get("coords")
        required = ("crop_x", "crop_y", "crop_width", "crop_height")
        try:
            normalized = {key: float(coords[key]) for key in required}
        except (KeyError, TypeError, ValueError):
            return None
        if (
            0 <= normalized["crop_x"] < 1
            and 0 <= normalized["crop_y"] < 1
            and 0 < normalized["crop_width"] <= 1 - normalized["crop_x"]
            and 0 < normalized["crop_height"] <= 1 - normalized["crop_y"]
        ):
            return validate_crop_coords(normalized)
    return None


def get_saved_crop_coords():
    configured = load_app_config().get("crop_coords")
    if configured is not None:
        try:
            return validate_crop_coords(configured)
        except ValueError:
            pass
    return get_videocr_crop_coords()

def resolve_ffprobe_executable():
    candidate = shutil.which("ffprobe")
    if not candidate:
        return ""
    candidate = os.path.abspath(candidate)
    return candidate if os.path.isfile(candidate) else ""


def resolve_ffmpeg_executable():
    candidate = shutil.which("ffmpeg")
    if not candidate:
        return ""
    candidate = os.path.abspath(candidate)
    return candidate if os.path.isfile(candidate) else ""


def get_video_resolution(video_path):
    ffprobe_path = resolve_ffprobe_executable()
    if not ffprobe_path:
        return None
    cmd = [
        ffprobe_path, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0",
        video_path
    ]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=15).strip()
        parts = out.split("x")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def get_video_duration_seconds(video_path):
    ffprobe_path = resolve_ffprobe_executable()
    if not ffprobe_path:
        return None
    cmd = [
        ffprobe_path, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path,
    ]
    try:
        duration = float(subprocess.check_output(cmd, text=True, timeout=15).strip())
        return duration if math.isfinite(duration) and duration > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


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


def build_videocr_command(
    video_path,
    output_srt,
    ocr_lang,
    *,
    pixel_crop,
    use_gpu,
    use_server_model,
    normalize_to_simplified,
    frames_to_skip,
    time_start=None,
    time_end=None,
):
    cmd = [
        VIDEOCR_CLI,
        "--video_path", video_path,
        "--output", output_srt,
        "--lang", ocr_lang,
    ]
    if time_start is not None:
        cmd += ["--time_start", format_videocr_time(time_start)]
    if time_end is not None:
        cmd += ["--time_end", format_videocr_time(math.ceil(time_end))]
    if pixel_crop:
        crop_x, crop_y, crop_width, crop_height = pixel_crop
        cmd += [
            "--crop_x", str(crop_x),
            "--crop_y", str(crop_y),
            "--crop_width", str(crop_width),
            "--crop_height", str(crop_height),
        ]
    else:
        cmd += ["--use_fullframe", "true"]
    if frames_to_skip is not None:
        cmd += ["--frames_to_skip", str(frames_to_skip)]
    cmd += ["--use_gpu", use_gpu]
    cmd += ["--use_server_model", use_server_model]
    cmd += ["--normalize_to_simplified_chinese", normalize_to_simplified]
    return cmd


def execute_videocr_command(cmd, output_srt, *, allow_empty=False):
    if os.path.exists(output_srt):
        os.remove(output_srt)
    process_started_at = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    register_active_process("VideOCR", proc)
    output_queue = Queue(maxsize=1_000)
    Thread(target=queue_process_output, args=(proc.stdout, output_queue), daemon=True).start()
    deadline = time.monotonic() + VIDEOCR_PROCESS_TIMEOUT_SECONDS
    try:
        while proc.poll() is None:
            check_cancel_requested()
            if time.monotonic() >= deadline:
                stop_process(proc, "VideOCR")
                raise TimeoutError("VideOCR quá thời gian chờ cho một đoạn video (30 phút).")
            try:
                line_str = output_queue.get(timeout=0.5).strip()
                if line_str:
                    log_to_app(line_str, "ocr")
            except Empty:
                pass

        while True:
            try:
                line_str = output_queue.get_nowait().strip()
                if line_str:
                    log_to_app(line_str, "ocr")
            except Empty:
                break

        check_cancel_requested()
        if proc.returncode != 0:
            raise VideoOCRProcessError(proc.returncode)
    except WorkflowCancelled:
        stop_process(proc, "VideOCR")
        raise
    finally:
        unregister_active_process("VideOCR", proc)

    if not os.path.exists(output_srt):
        raise FileNotFoundError(f"Không tìm thấy file phụ đề sau quét: {output_srt}")
    if os.path.getmtime(output_srt) < process_started_at - 1:
        raise RuntimeError("VideOCR không tạo file phụ đề mới hợp lệ cho lượt chạy này.")
    if not allow_empty and os.path.getsize(output_srt) <= 0:
        raise RuntimeError("VideOCR không nhận được phụ đề nào từ video.")
    return output_srt


def transcode_videocr_chunk(
    video_path,
    output_path,
    start_seconds,
    end_seconds,
    *,
    normalized_crop,
    pixel_crop,
    resolution,
):
    ffmpeg_path = resolve_ffmpeg_executable()
    if not ffmpeg_path:
        raise FileNotFoundError(
            "VideOCR bị crash khi đọc video và không tìm thấy FFmpeg để tự sửa đoạn lỗi. "
            "Hãy cài FFmpeg rồi chạy lại từ bước OCR."
        )
    resolution = resolution or get_video_resolution(video_path)
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "warning",
        "-ss", f"{start_seconds:.3f}",
        "-i", video_path,
        "-t", f"{max(0.001, end_seconds - start_seconds):.3f}",
        "-map", "0:v:0",
        "-an",
    ]
    video_filter = build_videocr_normalization_filter(normalized_crop, pixel_crop, resolution)
    if video_filter:
        cmd += ["-vf", video_filter]
    cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-y", output_path,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    register_active_process("FFmpeg sửa đoạn video", proc)
    output_queue = Queue(maxsize=200)
    Thread(target=queue_process_output, args=(proc.stdout, output_queue), daemon=True).start()
    recent_output = []
    deadline = time.monotonic() + VIDEOCR_TRANSCODE_TIMEOUT_SECONDS
    try:
        while proc.poll() is None:
            check_cancel_requested()
            if time.monotonic() >= deadline:
                stop_process(proc, "FFmpeg sửa đoạn video")
                raise TimeoutError("FFmpeg quá thời gian chuẩn hoá đoạn video (15 phút).")
            try:
                line = output_queue.get(timeout=0.5).strip()
                if line:
                    recent_output.append(line)
                    recent_output = recent_output[-20:]
            except Empty:
                pass
        check_cancel_requested()
        if proc.returncode != 0:
            details = " | ".join(recent_output[-3:])
            suffix = f" Chi tiết: {details}" if details else ""
            raise RuntimeError(f"FFmpeg không chuẩn hoá được đoạn video (mã {proc.returncode}).{suffix}")
    except WorkflowCancelled:
        stop_process(proc, "FFmpeg sửa đoạn video")
        raise
    finally:
        unregister_active_process("FFmpeg sửa đoạn video", proc)
    if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("FFmpeg không tạo được đoạn video thay thế hợp lệ.")
    return output_path


def read_videocr_srt_entries(srt_path, *, offset_milliseconds=0):
    with open(srt_path, "r", encoding="utf-8-sig") as source_file:
        content = source_file.read().strip()
    if not content:
        return []
    blocks = parse_srt_blocks(content)
    if not blocks:
        raise RuntimeError(f"VideOCR tạo file SRT không hợp lệ: {srt_path}")
    entries = []
    for _, timecode, text in blocks:
        match = SRT_TIMECODE_PATTERN.fullmatch(timecode)
        start = srt_timestamp_milliseconds(match.group(1)) + offset_milliseconds
        end = srt_timestamp_milliseconds(match.group(2)) + offset_milliseconds
        entries.append({"start": start, "end": end, "text": text.strip()})
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


def run_videocr(
    video_path,
    ocr_lang="ch",
    output_srt=None,
    crop_coords=None,
    use_saved_crop=True,
    video_resolution=None,
):
    check_cancel_requested()
    if not os.path.isfile(VIDEOCR_CLI):
        raise FileNotFoundError(f"Không tìm thấy VideOCR CLI tại: {VIDEOCR_CLI}")
    validate_ocr_lang(ocr_lang)
    log_to_app("Cấu hình toạ độ nhận diện chữ phụ đề...")
    coords = get_saved_crop_coords() if use_saved_crop else validate_crop_coords(crop_coords)
    res = validate_video_resolution(video_resolution) if video_resolution else get_video_resolution(video_path)
    video_dir = os.path.dirname(video_path)
    video_name_no_ext = os.path.splitext(os.path.basename(video_path))[0]
    output_srt = output_srt or os.path.join(video_dir, video_name_no_ext + ".raw.srt")
    os.makedirs(os.path.dirname(os.path.abspath(output_srt)), exist_ok=True)
    if os.path.exists(output_srt):
        os.remove(output_srt)
    pixel_crop = None
    if coords and res:
        width, height = res
        crop_x = int(coords["crop_x"] * width)
        crop_y = int(coords["crop_y"] * height)
        crop_width = int(coords["crop_width"] * width)
        crop_height = int(coords["crop_height"] * height)
        log_to_app(f"Tọa độ crop: x={crop_x}, y={crop_y}, w={crop_width}, h={crop_height}")
        pixel_crop = crop_x, crop_y, crop_width, crop_height
    else:
        log_to_app("Sử dụng cấu hình quét toàn màn hình mặc định.")

    # Read config defaults
    use_gpu = "true"
    use_server_model = "true"
    normalize_to_simplified = "true"
    if os.path.exists(VIDEOCR_CONFIG):
        with open(VIDEOCR_CONFIG, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("--use_gpu"):
                    use_gpu = line.split("=", 1)[1].strip().lower()
                elif line.startswith("--use_server_model"):
                    use_server_model = line.split("=", 1)[1].strip().lower()
                elif line.startswith("--normalize_to_simplified_chinese"):
                    normalize_to_simplified = line.split("=", 1)[1].strip().lower()

    app_config = load_app_config()
    try:
        model_mode = validate_ocr_model_mode(app_config.get("ocr_model_mode"))
    except ValueError:
        model_mode = "auto"
    if model_mode == "fast":
        use_server_model = "false"
        log_to_app("Dùng model OCR nhanh (mobile) theo Thiết lập.")
    elif model_mode == "accurate":
        use_server_model = "true"
        log_to_app("Dùng model OCR chính xác (server) theo Thiết lập.")
    try:
        frames_to_skip = validate_ocr_frames_to_skip(app_config.get("ocr_frames_to_skip"))
    except ValueError:
        frames_to_skip = None
    if frames_to_skip is not None:
        log_to_app(f"OCR bỏ qua {frames_to_skip} khung hình giữa mỗi lần quét.")

    duration = get_video_duration_seconds(video_path)
    if duration and duration > VIDEOCR_CHUNK_THRESHOLD_SECONDS:
        chunk_count = math.ceil(duration / VIDEOCR_CHUNK_SECONDS)
        log_to_app(
            f"Video dài {format_videocr_time(duration)}; chia thành {chunk_count} đoạn "
            "để tránh VideOCR tích quá nhiều frame rồi bị crash."
        )
        entries = []
        normalize_remaining_chunks = False
        with tempfile.TemporaryDirectory(prefix="VietSub-VideOCR-") as temporary_dir:
            for chunk_index in range(chunk_count):
                check_cancel_requested()
                start_seconds = chunk_index * VIDEOCR_CHUNK_SECONDS
                end_seconds = min(duration, start_seconds + VIDEOCR_CHUNK_SECONDS)
                chunk_label = (
                    f"{format_videocr_time(start_seconds)} - "
                    f"{format_videocr_time(math.ceil(end_seconds))}"
                )
                log_to_app(f"OCR đoạn {chunk_index + 1}/{chunk_count} ({chunk_label})...")
                chunk_entries = None
                if not normalize_remaining_chunks:
                    direct_srt = os.path.join(temporary_dir, f"chunk-{chunk_index + 1:03d}.srt")
                    direct_cmd = build_videocr_command(
                        video_path,
                        direct_srt,
                        ocr_lang,
                        pixel_crop=pixel_crop,
                        use_gpu=use_gpu,
                        use_server_model=use_server_model,
                        normalize_to_simplified=normalize_to_simplified,
                        frames_to_skip=frames_to_skip,
                        time_start=start_seconds,
                        time_end=end_seconds,
                    )
                    try:
                        execute_videocr_command(direct_cmd, direct_srt, allow_empty=True)
                        chunk_entries = read_videocr_srt_entries(direct_srt)
                    except VideoOCRProcessError as error:
                        normalize_remaining_chunks = True
                        log_to_app(
                            f"VideOCR đọc không ổn định đoạn {chunk_label} ({error}). "
                            "Video có thể đã đổi độ phân giải giữa chừng; các đoạn còn lại sẽ được "
                            "chuẩn hoá sang H.264 trước khi OCR.",
                            "error",
                        )
                if chunk_entries is None:
                    normalized_video = os.path.join(
                        temporary_dir, f"chunk-{chunk_index + 1:03d}.normalized.mp4"
                    )
                    transcode_videocr_chunk(
                        video_path,
                        normalized_video,
                        start_seconds,
                        end_seconds,
                        normalized_crop=coords,
                        pixel_crop=pixel_crop,
                        resolution=res,
                    )
                    normalized_srt = os.path.join(
                        temporary_dir, f"chunk-{chunk_index + 1:03d}.normalized.srt"
                    )
                    normalized_cmd = build_videocr_command(
                        normalized_video,
                        normalized_srt,
                        ocr_lang,
                        pixel_crop=None,
                        use_gpu=use_gpu,
                        use_server_model=use_server_model,
                        normalize_to_simplified=normalize_to_simplified,
                        frames_to_skip=frames_to_skip,
                    )
                    try:
                        execute_videocr_command(normalized_cmd, normalized_srt, allow_empty=True)
                        chunk_entries = read_videocr_srt_entries(
                            normalized_srt,
                            offset_milliseconds=round(start_seconds * 1000),
                        )
                    finally:
                        if os.path.exists(normalized_video):
                            try:
                                os.remove(normalized_video)
                            except OSError:
                                pass
                entries.extend(chunk_entries)
                log_to_app(f"Đã hoàn tất OCR đoạn {chunk_index + 1}/{chunk_count}.")
        write_merged_videocr_srt(entries, output_srt)
    else:
        log_to_app("Khởi động tiến trình VideOCR CLI...")
        cmd = build_videocr_command(
            video_path,
            output_srt,
            ocr_lang,
            pixel_crop=pixel_crop,
            use_gpu=use_gpu,
            use_server_model=use_server_model,
            normalize_to_simplified=normalize_to_simplified,
            frames_to_skip=frames_to_skip,
        )
        execute_videocr_command(cmd, output_srt)

    log_to_app(f"Quét phụ đề thành công: {output_srt}")
    return output_srt

def resolve_url_via_edge(short_url, notebook_url):
    check_cancel_requested()
    # API translation does not require a Notebook URL; Edge is only used here
    # to follow Douyin's browser redirect for short share links.
    startup_url = notebook_url or short_url
    if not ensure_edge_running(startup_url):
        log_to_app("Không kết nối được Edge để giải mã URL.", "error")
        return None

    log_to_app(f"Giải mã URL di động bằng Edge: {short_url}")
    try:
        with get_sync_playwright()() as p:
            browser = p.chromium.connect_over_cdp(edge_debug_url())
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError("Không tìm thấy Context trình duyệt.")
            context = contexts[0]
            page = context.new_page()
            try:
                page.goto(short_url, wait_until="commit", timeout=15000)
                # Poll for the redirect instead of a fixed wait; give slow chains up to 5s.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    check_cancel_requested()
                    if re.search(r"/(?:video|note)/\d+", urlparse(page.url).path):
                        break
                    page.wait_for_timeout(250)
                resolved_url = page.url
                log_to_app(f"Trình duyệt giải mã thành: {resolved_url}")
                return resolved_url
            finally:
                page.close()
    except WorkflowCancelled:
        raise
    except Exception as e:
        log_to_app(f"Lỗi giải mã URL qua Edge: {e}", "error")
        return None

SRT_TIMECODE_PATTERN = re.compile(
    r"(?m)^\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?\s*$"
)
SRT_TIMECODE_LINE_PATTERN = re.compile(
    r"^\s*\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*"
    r"\d{2}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?\s*$"
)


TRANSLATION_GLOSSARY_PROMPT = """\
Bạn là chuyên gia dịch phụ đề Trung sang Việt, đặc biệt am hiểu ngữ cảnh game,
phim hành động khoa học viễn tưởng và video cốt truyện.

QUY TẮC BẮT BUỘC:
- Dịch tự nhiên, mượt mà, bám sát ngữ cảnh; không dịch word-by-word.
- Chọn đại từ nhân xưng theo quan hệ và sắc thái nhân vật (tôi/cậu, tao/mày,
  ngài/tôi, sếp/tôi...). Giữ cách xưng hô nhất quán trong đoạn.
- Giữ nguyên tuyệt đối số thứ tự và timestamp của từng block.
- Chỉ được tạo đúng một block SRT ở phần trả lời; không chèn lời giải thích vào block.
- Sau block code, liệt kê ngắn gọn các tên riêng/địa danh, thuật ngữ và đại từ
  nhân xưng đã dùng để hậu kiểm. Phần liệt kê này nằm ngoài block SRT.

TỪ ĐIỂN THUẬT NGỮ VÀ TÊN RIÊNG:
- Nhân vật: Saeed (赛伊德), Kami (卡米), Tiến sĩ Prometheus (罗米修斯博士),
  Zoya (佐娅), Alexei (阿列克谢), Reyes (雷斯), Claire (克莱尔), Raven (渡鸦),
  Luna (露娜), Kai (凯), Landon (兰登), Carl (卡尔), Pete (比特),
  Desmoulin (德穆兰), Hudson (哈德森), Hamuk (哈姆克), Vô Danh (无名),
  T-Boy (T 仔), Echo (回响), Vương Vũ Hạo (王宇昊), Lina (莉娜).
- Thế lực/tổ chức: Haavk (哈夫克), Ahsahra (阿萨拉), G.T.I, Delta (三角洲),
  Vệ binh Ahsahra (阿萨拉卫队), Ổ Chuột Đỏ (红鼠窝), Đội Tăng Tốc (加速部队),
  Lực lượng phản ứng nhanh (快反部队), Phi đội Cánh Đỏ / Red Wing (红翼中队).
- Hệ thống/công nghệ: Thiết bị não bộ Relink (RE LINK 脑机), Alpha (A I pha),
  Skynet (天网), Khối Mandel (曼德尔砖 / 曼德尔单元), Hệ thống Thủy Triều
  (潮汐系统), Thủy Triều Phong Tỏa (潮汐封锁), Hệ thống Cicada (鸣蝉系统),
  Lăng Tiêu (凌霄), Uy Long (威龙), Khóa lượng tử (量子密钥).
- Kế hoạch/vật phẩm: Kế hoạch [Sao Đen] (【暗星】计划), Nhiên liệu Sao Đen
  (暗星燃料), Trái Tim Châu Phi (非洲之心).
- Địa danh: HA AVK CITY, Khách sạn Diamond (钻石酒店), Thung lũng Longbow
  (长弓溪谷), Nhà tù Thủy Triều (潮汐监狱), Đập Zero (零号大坝), Thành phố
  Không gian (航天城), Trạm không gian số 36 (36 号空间站), Kênh đào Urmus
  (鸟姆斯运河), Phòng Xanh (蓝室), Phòng Đen (黑室).
- Meta: Vũ Trụ Tốc Lăng (速凌宇宙), Đột Kích (穿越火线), Ngũ Hiệp Đau Trứng
  (蛋疼五侠), Tư Lệnh (司令).
"""


def validate_translation_instructions(value):
    if value in (None, ""):
        return TRANSLATION_GLOSSARY_PROMPT
    if not isinstance(value, str):
        raise ValueError("Cách dịch mong muốn không hợp lệ.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return TRANSLATION_GLOSSARY_PROMPT
    if len(normalized) > MAX_TRANSLATION_INSTRUCTIONS_CHARS:
        raise ValueError(
            f"Cách dịch mong muốn không được vượt quá {MAX_TRANSLATION_INSTRUCTIONS_CHARS:,} ký tự."
        )
    if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
        raise ValueError("Cách dịch mong muốn chứa ký tự điều khiển không hợp lệ.")
    return normalized


def build_translation_prompt(srt_content, translation_instructions=None):
    instructions = validate_translation_instructions(translation_instructions)
    return (
        instructions
        + "\n\nĐẦU VÀO LÀ TIẾNG TRUNG. ĐẦU RA BẮT BUỘC LÀ TIẾNG VIỆT.\n"
        + "Mỗi block chỉ được có nội dung tiếng Việt tự nhiên.\n"
        + "Tuyệt đối không chép lại câu tiếng Trung và không xuất song ngữ Trung-Việt.\n"
        + "Giữ nguyên số thứ tự và timestamp; chỉ dịch phần nội dung phụ đề.\n"
        + "Đặt toàn bộ SRT đã dịch trong đúng một block code srt.\n\n"
        + "Hãy dịch SRT sau đây, không được bỏ sót block nào:\n\n"
        + "```srt\n"
        + f"{srt_content}\n"
        + "```"
    )


def get_translation_runtime_config(config=None, require_ready=False):
    config = load_app_config() if config is None else config
    provider = validate_translation_provider(config.get("translation_provider"))
    translation_instructions = validate_translation_instructions(
        config.get("translation_instructions")
    )
    if provider == "notebook":
        notebook_url = validate_notebook_url(
            config.get("notebook_url", ""),
            allow_empty=not require_ready,
        )
        return {
            "provider": provider,
            "label": "Gemini Notebook",
            "notebook_url": notebook_url,
            "translation_instructions": translation_instructions,
        }

    base_url = validate_translation_api_base_url(
        config.get("translation_api_base_url", ""),
        provider,
    )
    model = validate_translation_api_model(
        config.get("translation_api_model", ""),
        provider,
    )
    api_key = get_translation_api_key(config)
    requires_key = bool(TRANSLATION_PROVIDER_PRESETS[provider]["requires_key"])
    if require_ready and requires_key and not api_key:
        raise ValueError(f"{TRANSLATION_PROVIDER_PRESETS[provider]['label']} chưa có API key.")
    return {
        "provider": provider,
        "label": TRANSLATION_PROVIDER_PRESETS[provider]["label"],
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "requires_key": requires_key,
        "translation_instructions": translation_instructions,
    }


def get_translation_browser_url(config=None):
    config = load_app_config() if config is None else config
    provider = validate_translation_provider(config.get("translation_provider"))
    if provider != "notebook":
        return ""
    return validate_notebook_url(config.get("notebook_url", ""), allow_empty=True)


def chat_completions_url(base_url):
    normalized = base_url.rstrip("/")
    if normalized.lower().endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def extract_chat_completion_content(payload):
    if not isinstance(payload, dict):
        raise RuntimeError("API dịch trả về JSON không hợp lệ.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("API dịch không trả về lựa chọn nào.")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise RuntimeError("API dịch trả về lựa chọn không hợp lệ.")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else choice.get("text")
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("text")
        )
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("API dịch trả về nội dung trống.")
    if choice.get("finish_reason") == "length":
        raise RuntimeError("API đã cắt câu trả lời vì chạm giới hạn output token.")
    return content.strip()


def read_streaming_chat_completion(response):
    content_parts = []
    for raw_line in iter(response.readline, b""):
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace").strip()
        else:
            line = str(raw_line).strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("error"):
            detail = payload["error"]
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("code") or str(detail)
            raise RuntimeError(f"API dịch trả lỗi streaming: {str(detail)[:500]}")
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        if choice.get("finish_reason") == "length":
            raise RuntimeError("API đã cắt câu trả lời vì chạm giới hạn output token.")
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        piece = delta.get("content")
        if piece is None:
            message = choice.get("message")
            piece = message.get("content") if isinstance(message, dict) else choice.get("text")
        if isinstance(piece, list):
            piece = "\n".join(
                str(item.get("text", ""))
                for item in piece
                if isinstance(item, dict) and item.get("text")
            )
        if isinstance(piece, str):
            content_parts.append(piece)
    content = "".join(content_parts).strip()
    if not content:
        raise RuntimeError("API dịch streaming không trả về nội dung.")
    return content


def call_openai_compatible_api(runtime_config, messages, *, max_tokens=16_000, stream=False):
    body = {
        "model": runtime_config["model"],
        "messages": messages,
        "temperature": 0.1,
        "stream": bool(stream),
        "max_tokens": max_tokens,
    }
    if runtime_config.get("provider") == "deepseek":
        # DeepSeek enables reasoning by default; disable it so the output budget is
        # spent on the translated SRT instead of hidden chain-of-thought tokens.
        body["thinking"] = {"type": "disabled"}
        body["response_format"] = {"type": "text"}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"VietSub-Studio/{APP_VERSION}",
    }
    if runtime_config.get("api_key"):
        headers["Authorization"] = f"Bearer {runtime_config['api_key']}"
    if runtime_config.get("provider") == "openrouter":
        headers["HTTP-Referer"] = f"https://github.com/{GITHUB_REPOSITORY}"
        headers["X-Title"] = "VietSub Studio"
    request_object = urllib.request.Request(
        chat_completions_url(runtime_config["base_url"]),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request_object,
            timeout=TRANSLATION_API_TIMEOUT_SECONDS,
        ) as response:
            content_type = ""
            headers = getattr(response, "headers", None)
            if headers is not None:
                content_type = str(headers.get("Content-Type", "")).lower()
            if stream and "text/event-stream" in content_type:
                return read_streaming_chat_completion(response)
            raw = response.read(TRANSLATION_API_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raw_error = error.read(128 * 1024)
        try:
            error_payload = json.loads(raw_error.decode("utf-8", errors="replace"))
            detail = error_payload.get("error", error_payload)
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("code") or str(detail)
            detail = str(detail)
        except (json.JSONDecodeError, UnicodeError):
            detail = raw_error.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"API dịch trả lỗi HTTP {error.code}: {detail[:500] or error.reason}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Không kết nối được API dịch: {error.reason}") from error
    if len(raw) > TRANSLATION_API_MAX_RESPONSE_BYTES:
        raise RuntimeError("Phản hồi API dịch vượt quá giới hạn an toàn 16 MiB.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise RuntimeError("API dịch không trả về JSON UTF-8 hợp lệ.") from error
    return extract_chat_completion_content(payload)


def extract_api_translated_srt(content, source_srt):
    fenced_blocks = re.findall(
        r"```(?:srt)?\s*\n?(.*?)```",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    candidates = fenced_blocks or [content]
    translated_srt = max(candidates, key=lambda value: len(SRT_TIMECODE_PATTERN.findall(value)))
    return normalize_translated_srt(source_srt, translated_srt.strip())


def format_srt_blocks(blocks):
    return "\n\n".join(
        f"{index}\n{timecode}\n{text}" for index, timecode, text in blocks
    ) + "\n"


def split_srt_for_translation(srt_content, video_duration_seconds=None):
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
    if declared_duration <= TRANSLATION_CHUNK_THRESHOLD_SECONDS:
        return [srt_content], False

    chunks = []
    current = []
    current_chars = 0
    chunk_start_milliseconds = None
    next_time_boundary = None
    target_chunk_milliseconds = TRANSLATION_CHUNK_TARGET_SECONDS * 1000

    for block in blocks:
        _, timecode, text = block
        start_milliseconds = srt_timestamp_milliseconds(
            SRT_TIMECODE_PATTERN.fullmatch(timecode).group(1)
        )
        block_chars = len(format_srt_blocks([block]))
        reached_time_boundary = (
            current
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
    return chunks, True


def validate_translated_srt(source_srt, translated_srt):
    validate_srt_structure(source_srt, "SRT gốc")
    validate_srt_structure(translated_srt, "SRT dịch")
    source_timecodes = SRT_TIMECODE_PATTERN.findall(source_srt)
    translated_timecodes = SRT_TIMECODE_PATTERN.findall(translated_srt)
    if not source_timecodes:
        raise ValueError("SRT gốc không có mốc thời gian hợp lệ.")
    if source_timecodes != translated_timecodes:
        raise ValueError(
            "Phản hồi Gemini không phải SRT đầy đủ hoặc đã thay đổi mốc thời gian; không lưu file."
        )
    validate_translation_language(source_srt, translated_srt)
    return translated_srt


CJK_CHAR_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def translation_language_stats(srt_content):
    """Return CJK/word counts used to catch an API echoing the Chinese source."""
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
            "Model vẫn trả về tiếng Trung hoặc song ngữ Trung-Việt; "
            "hãy dùng model deepseek-v4-pro và dịch lại."
        )
    return translated_srt


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


def validate_srt_structure(srt_content, label="SRT"):
    blocks = parse_srt_blocks(srt_content)
    if not blocks:
        raise ValueError(f"{label} không có cấu trúc block hợp lệ.")
    indexes = [int(block[0]) for block in blocks]
    if any(current <= previous for previous, current in zip(indexes, indexes[1:])):
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


def normalize_translated_srt(source_srt, translated_srt):
    """Restore source timing when Gemini preserves subtitle blocks but rewrites timecodes."""
    source_blocks = parse_srt_blocks(source_srt)
    translated_blocks = parse_srt_blocks(translated_srt)
    if source_blocks and len(source_blocks) == len(translated_blocks):
        source_indexes = [block[0] for block in source_blocks]
        translated_indexes = [block[0] for block in translated_blocks]
        if source_indexes == translated_indexes:
            rebuilt = "\n\n".join(
                f"{source_index}\n{source_timecode}\n{translated_text}"
                for (source_index, source_timecode, _), (_, _, translated_text) in zip(
                    source_blocks, translated_blocks
                )
            )
            return validate_translated_srt(source_srt, rebuilt)
    return validate_translated_srt(source_srt, translated_srt)


def validate_project_result(result):
    if not isinstance(result, dict):
        raise ValueError("Dữ liệu bộ file kết quả không hợp lệ.")

    project_dir = str(result.get("project_dir") or "")
    if not os.path.isdir(project_dir):
        raise FileNotFoundError("Thư mục kết quả chưa được tạo đầy đủ.")

    required_files = {
        "video": "video dự án",
        "raw_srt": "file sub OCR",
        "translated_srt": "file sub Việt",
    }
    for key, label in required_files.items():
        path = str(result.get(key) or "")
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise FileNotFoundError(f"Không tìm thấy {label} hoàn chỉnh: {path or '(trống)'}")

    with open(result["raw_srt"], "r", encoding="utf-8-sig") as source_file:
        source_srt = source_file.read().strip()
    with open(result["translated_srt"], "r", encoding="utf-8-sig") as translated_file:
        translated_srt = translated_file.read().strip()

    validate_translated_srt(source_srt, translated_srt)
    source_blocks = parse_srt_blocks(source_srt)
    translated_blocks = parse_srt_blocks(translated_srt)
    if not source_blocks or len(source_blocks) != len(translated_blocks):
        raise ValueError("File sub Việt chưa đầy đủ số đoạn so với sub OCR.")
    if [block[0] for block in source_blocks] != [block[0] for block in translated_blocks]:
        raise ValueError("File sub Việt đã thay đổi số thứ tự đoạn; không thể báo hoàn thành.")
    return True


def project_result_ready(result):
    try:
        return validate_project_result(result)
    except (OSError, UnicodeError, TypeError, ValueError):
        return False


def project_files_present(result):
    # Cheap stat-only check for polling hot paths; full validation runs once at job success.
    if not isinstance(result, dict):
        return False
    return all(
        os.path.isfile(str(result.get(key) or ""))
        for key in ("video", "raw_srt", "translated_srt")
    )


def find_last_response(page, selectors):
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.last
    return None


def is_gemini_generating(page):
    stop_selectors = [
        "button[aria-label*='Stop']",
        "button[aria-label*='stop']",
        "button[aria-label*='Dừng']",
        "button[aria-label*='dừng']",
        "button.stop-button",
    ]
    for selector in stop_selectors:
        locator = page.locator(selector)
        if locator.count() > 0 and locator.first.is_visible():
            return True
    return False


def stop_gemini_generation(page):
    stop_selectors = [
        "button[aria-label*='Stop']",
        "button[aria-label*='stop']",
        "button[aria-label*='Dừng']",
        "button[aria-label*='dừng']",
        "button.stop-button",
    ]
    for selector in stop_selectors:
        locator = page.locator(selector)
        if locator.count() > 0 and locator.first.is_visible():
            try:
                locator.first.click(timeout=2000)
                return True
            except Exception:
                pass
    return False


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
    translated_srt = re.sub(r'^```[a-zA-Z0-9]*\n', '', translated_srt, flags=re.MULTILINE)
    translated_srt = re.sub(r'\n```$', '', translated_srt)
    return normalize_translated_srt(source_srt, translated_srt.strip())


def translate_gemini_chunk(
    gemini_page,
    prompt_input,
    response_selectors,
    submit_selectors,
    source_srt,
    chunk_index,
    chunk_count,
    translation_instructions=None,
):
    previous_response = find_last_response(gemini_page, response_selectors)
    previous_response_text = previous_response.inner_text() if previous_response else ""
    prompt = build_translation_prompt(source_srt, translation_instructions)
    log_to_app(
        f"Nạp đoạn dịch {chunk_index}/{chunk_count} ({len(source_srt):,} ký tự)..."
    )
    try:
        prompt_input.focus()
        prompt_input.fill(prompt, timeout=120_000)
    except Exception as error:
        raise RuntimeError(
            f"Không thể nạp đoạn dịch {chunk_index}/{chunk_count} "
            f"({len(source_srt):,} ký tự): {type(error).__name__}."
        ) from error

    submit_btn = first_visible_locator(
        gemini_page,
        submit_selectors,
        require_enabled=True,
    )
    if not submit_btn:
        raise RuntimeError("Không tìm thấy nút gửi của Gemini; không thể gửi an toàn.")

    log_to_app(f"Gửi đoạn dịch {chunk_index}/{chunk_count} sang NotebookLM...")
    submit_btn.click()
    log_to_app(f"Chờ Gemini trả lời đoạn {chunk_index}/{chunk_count}...")
    start_wait = time.monotonic()
    last_length = 0
    stable_count = 0
    while True:
        if cancel_event.is_set():
            stop_gemini_generation(gemini_page)
            raise WorkflowCancelled("Đã huỷ quy trình theo yêu cầu.")
        if time.monotonic() - start_wait > 300:
            stop_gemini_generation(gemini_page)
            raise TimeoutError(
                f"Thời gian dịch đoạn {chunk_index}/{chunk_count} của Gemini quá lâu (quá 5 phút)."
            )
        current_response = find_last_response(gemini_page, response_selectors)
        if current_response:
            try:
                text = current_response.inner_text()
                length = len(text)
                if is_gemini_generating(gemini_page):
                    stable_count = 0
                    last_length = length
                elif text and text != previous_response_text and length == last_length:
                    stable_count += 1
                    if stable_count >= 3:
                        break
                else:
                    stable_count = 0
                    last_length = length
            except Exception:
                pass
        time.sleep(1)

    translated = extract_gemini_translated_srt(
        find_last_response(gemini_page, response_selectors),
        source_srt,
    )
    log_to_app(f"Đã dịch xong đoạn {chunk_index}/{chunk_count}.")
    return translated


def infer_translation_video_path(srt_path):
    directory = os.path.dirname(srt_path)
    base_name = os.path.splitext(os.path.basename(srt_path))[0]
    if base_name.endswith(".raw"):
        base_name = base_name[:-4]
    return os.path.join(directory, base_name + ".mp4")


def translation_output_path(srt_path, output_srt=None):
    if output_srt:
        return output_srt
    directory = os.path.dirname(srt_path)
    base_name = os.path.splitext(os.path.basename(srt_path))[0]
    if base_name.endswith(".raw"):
        base_name = base_name[:-4]
    return os.path.join(directory, base_name + ".vi.srt")


def write_text_atomic(path, content):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary_path = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8", newline="\n") as output_file:
            output_file.write(content)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def translate_api_chunk(runtime_config, source_srt, chunk_index, chunk_count):
    messages = [
        {
            "role": "system",
            "content": (
                "Bạn là biên dịch viên phụ đề Trung sang Việt chuyên nghiệp, am hiểu game và phim hành động. "
                "Đầu vào là tiếng Trung, đầu ra chỉ được là tiếng Việt; tuyệt đối không chép lại tiếng Trung "
                "và không xuất song ngữ. Tuân thủ từ điển và quy tắc trong yêu cầu người dùng. "
                "Giữ nguyên số thứ tự và timestamp, đặt SRT trong đúng một block code srt, rồi liệt kê "
                "ngắn gọn tên riêng, thuật ngữ và đại từ đã dùng ở bên ngoài block."
            ),
        },
        {
            "role": "user",
            "content": build_translation_prompt(
                source_srt,
                runtime_config.get("translation_instructions"),
            ),
        },
    ]
    for attempt in range(1, TRANSLATION_API_MAX_ATTEMPTS + 1):
        check_cancel_requested()
        log_to_app(
            f"Gửi đoạn {chunk_index}/{chunk_count} tới {runtime_config['label']} "
            f"(lần thử {attempt}/{TRANSLATION_API_MAX_ATTEMPTS})..."
        )
        try:
            try:
                response = call_openai_compatible_api(
                    runtime_config,
                    messages,
                    max_tokens=32_768,
                    stream=True,
                )
            except RuntimeError as error:
                # Some compatible gateways reject stream=true; retry that request once as JSON.
                if "HTTP 400" not in str(error):
                    raise
                log_to_app(
                    f"{runtime_config['label']} không hỗ trợ streaming; chuyển sang JSON đầy đủ.",
                    "error",
                )
                response = call_openai_compatible_api(
                    runtime_config,
                    messages,
                    max_tokens=32_768,
                    stream=False,
                )
            translated = extract_api_translated_srt(response, source_srt)
            log_to_app(f"Đã dịch xong đoạn {chunk_index}/{chunk_count}.")
            return translated
        except (RuntimeError, OSError, ValueError) as error:
            message = str(error)
            non_retryable = (
                "HTTP 400" in message
                or "HTTP 401" in message
                or "HTTP 403" in message
                or "cắt câu trả lời" in message
                or "SRT" in message
                or "tiếng Trung" in message
                or "song ngữ" in message
            )
            if non_retryable or attempt >= TRANSLATION_API_MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Dịch đoạn {chunk_index}/{chunk_count} qua {runtime_config['label']} thất bại: {message}"
                ) from error
            delay = 2 ** (attempt - 1)
            log_to_app(
                f"API lỗi ở đoạn {chunk_index}/{chunk_count}; thử lại sau {delay} giây: {message}",
                "error",
            )
            for _ in range(delay):
                check_cancel_requested()
                time.sleep(1)
    raise RuntimeError("Không thể dịch đoạn phụ đề qua API.")


def translation_cache_fingerprint(runtime_config):
    """Separate resumable chunks whenever the translation configuration changes."""
    identity = {
        "provider": str(runtime_config.get("provider") or ""),
        "base_url": str(runtime_config.get("base_url") or ""),
        "model": str(runtime_config.get("model") or ""),
        "prompt_version": TRANSLATION_PROMPT_VERSION,
        "translation_instructions": validate_translation_instructions(
            runtime_config.get("translation_instructions")
        ),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def translate_srt_via_api(srt_path, runtime_config, output_srt=None):
    check_cancel_requested()
    with open(srt_path, "r", encoding="utf-8-sig") as source_file:
        srt_content = source_file.read().strip()
    if not srt_content:
        raise ValueError("File phụ đề trống. Không có gì để dịch.")

    video_path = infer_translation_video_path(srt_path)
    video_duration = get_video_duration_seconds(video_path) if os.path.isfile(video_path) else None
    translation_chunks, is_long_video = split_srt_for_translation(
        srt_content,
        video_duration_seconds=video_duration,
    )
    if is_long_video:
        log_to_app(
            f"Video dài hơn 45 phút; chia thành {len(translation_chunks)} đoạn để dịch qua "
            f"{runtime_config['label']}."
        )
    else:
        log_to_app(f"Video không quá 45 phút; gửi SRT một lần qua {runtime_config['label']}.")

    output_translated_srt = translation_output_path(srt_path, output_srt)
    parts_dir = output_translated_srt + ".parts"
    os.makedirs(parts_dir, exist_ok=True)
    translated_chunks = []
    config_hash = translation_cache_fingerprint(runtime_config)
    try:
        for chunk_index, source_chunk in enumerate(translation_chunks, 1):
            source_hash = hashlib.sha256(source_chunk.encode("utf-8")).hexdigest()[:16]
            part_path = os.path.join(
                parts_dir,
                f"part-{chunk_index:04d}-{source_hash}-{config_hash}.srt",
            )
            translated = None
            if os.path.isfile(part_path):
                try:
                    with open(part_path, "r", encoding="utf-8-sig") as cached_file:
                        translated = normalize_translated_srt(source_chunk, cached_file.read().strip())
                    log_to_app(f"Dùng lại đoạn dịch đã lưu {chunk_index}/{len(translation_chunks)}.")
                except (OSError, UnicodeError, ValueError):
                    translated = None
            if translated is None:
                translated = translate_api_chunk(
                    runtime_config,
                    source_chunk,
                    chunk_index,
                    len(translation_chunks),
                )
                write_text_atomic(part_path, translated)
            translated_chunks.append(translated)

        translated_srt = normalize_translated_srt(
            srt_content,
            "\n\n".join(chunk.strip() for chunk in translated_chunks),
        )
        write_text_atomic(output_translated_srt, translated_srt)
    except Exception:
        log_to_app(
            f"Các đoạn dịch tạm được giữ tại: {parts_dir}; lần chạy lại sẽ tiếp tục từ đoạn đã xong.",
            "error",
        )
        raise
    else:
        shutil.rmtree(parts_dir, ignore_errors=True)

    log_to_app(f"Đã tạo file phụ đề dịch thành công: {output_translated_srt}")
    return output_translated_srt


def translate_srt_via_gemini_edge(
    srt_path,
    notebook_url,
    output_srt=None,
    translation_instructions=None,
):
    check_cancel_requested()
    log_to_app("Đang đọc nội dung phụ đề thô...")
    with open(srt_path, "r", encoding="utf-8-sig") as f:
        srt_content = f.read().strip()
    if not srt_content:
        raise ValueError("File phụ đề trống. Không có gì để dịch.")
    translation_instructions = validate_translation_instructions(
        translation_instructions
    )

    video_path = infer_translation_video_path(srt_path)
    video_duration = get_video_duration_seconds(video_path) if os.path.isfile(video_path) else None
    translation_chunks, is_long_video = split_srt_for_translation(
        srt_content,
        video_duration_seconds=video_duration,
    )
    if is_long_video:
        log_to_app(
            f"SRT thuộc video dài hơn 45 phút; chia thành {len(translation_chunks)} đoạn để dịch."
        )
    else:
        log_to_app("Video không quá 45 phút; dịch toàn bộ SRT trong một lần.")

    notebook_url = validate_notebook_url(notebook_url)
    edge_background = bool(load_app_config().get("edge_background", True))
    if not ensure_edge_running(notebook_url):
        raise RuntimeError("Trình duyệt Microsoft Edge chưa được chạy ở chế độ debug.")

    log_to_app("Kết nối Playwright vào Edge để thực hiện dịch phụ đề...")
    with get_sync_playwright()() as p:
        browser = p.chromium.connect_over_cdp(edge_debug_url())
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("Không tìm thấy profile Edge đang hoạt động.")
        context = contexts[0]
        notebook_id = urlparse(notebook_url).path.rstrip("/").rsplit("/", 1)[-1]
        gemini_page = next(
            (page for page in context.pages if notebook_id in page.url),
            None,
        )
        if not gemini_page:
            log_to_app(f"Notebook chưa mở. Đang tự động mở tab mới tới: {notebook_url}...")
            gemini_page = context.new_page()
            gemini_page.goto(notebook_url)
            gemini_page.wait_for_timeout(5000)

        log_to_app(f"Đã liên kết thành công với tab Notebook: '{gemini_page.title()}'")
        log_to_app("Đang chờ giao diện Notebook sẵn sàng...")
        prompt_input = wait_for_gemini_prompt(gemini_page)
        if prompt_input is None and edge_page_needs_login(gemini_page):
            set_edge_window_visibility(context, gemini_page, True)
            raise EdgeLoginRequired(
                "Phiên Google đã hết hạn. Edge đã được mở để đăng nhập; đăng nhập xong hãy bấm Chạy lại."
            )
        if prompt_input is None:
            if edge_background:
                set_edge_window_visibility(context, gemini_page, True)
                raise EdgeLoginRequired(
                    "App chưa tìm thấy khung chat của Notebook khi Edge chạy ẩn. "
                    "Edge đã được mở để kiểm tra; khi khung chat hiện ra hãy bấm Chạy lại."
                )
            raise RuntimeError("Không tìm thấy khung nhập liệu chat của Gemini.")
        set_edge_window_visibility(context, gemini_page, not edge_background)

        response_selectors = [
            "message-content",
            ".model-response",
            ".assistant-message",
            "[data-message-author='assistant']",
            "div.markdown",
            ".chat-message",
        ]
        submit_selectors = [
            "button.send-button",
            "button[aria-label*='Send']",
            "button[aria-label*='Gửi']",
            "button[aria-label*='send']",
            "button[aria-label*='gửi']",
        ]
        translated_chunks = []
        for chunk_index, source_chunk in enumerate(translation_chunks, 1):
            check_cancel_requested()
            try:
                translated_chunks.append(
                    translate_gemini_chunk(
                        gemini_page,
                        prompt_input,
                        response_selectors,
                        submit_selectors,
                        source_chunk,
                        chunk_index,
                        len(translation_chunks),
                        translation_instructions,
                    )
                )
            except RuntimeError as error:
                if edge_background and "nút gửi" in str(error):
                    set_edge_window_visibility(context, gemini_page, True)
                    raise EdgeLoginRequired(str(error)) from error
                raise

    translated_srt = normalize_translated_srt(
        srt_content,
        "\n\n".join(chunk.strip() for chunk in translated_chunks),
    )
    output_translated_srt = translation_output_path(srt_path, output_srt)
    write_text_atomic(output_translated_srt, translated_srt)
    log_to_app(f"Đã tạo file phụ đề dịch thành công: {output_translated_srt}")
    return output_translated_srt

def resolve_video_request(video_url, notebook_url):
    source_url = validate_douyin_url(video_url)
    is_short_url = (urlparse(source_url).hostname or "").lower() == "v.douyin.com"
    video_url_for_id = source_url
    if is_short_url:
        video_url_for_id = resolve_url_via_edge(source_url, notebook_url)
        if not video_url_for_id:
            raise ValueError("Không thể giải mã link share Douyin thành link video.")
        video_url_for_id = validate_douyin_url(video_url_for_id)
    source_path = urlparse(video_url_for_id).path.lower()
    if "/mix/" in source_path or "/user/" in source_path:
        raise ValueError("Tool chỉ hỗ trợ link video riêng lẻ; không hỗ trợ mix hoặc user.")
    return source_url, is_short_url, get_video_id(video_url_for_id)


def download_video_source(video_url, notebook_url):
    sidecar_proc = None
    source_url, is_short_url, video_id = resolve_video_request(video_url, notebook_url)
    try:
        sidecar_proc, port, token = start_douzy_sidecar()
        start_time_epoch = time.time()
        if is_short_url:
            log_to_app("Gửi link share ngắn trực tiếp cho Douzy để tải video.")
        else:
            log_to_app(f"Sử dụng link video: {source_url}")
        download_job_id = trigger_download(port, token, source_url, scope="single")
        download_job = poll_download_job(port, token, download_job_id)
        if download_job.get("skipped"):
            log_to_app("Douzy đã có sẵn video này, dùng lại file đã tải.")
        video_file = wait_for_downloaded_video_file(
            start_time_epoch,
            video_id,
            allow_existing=bool(download_job.get("skipped")),
        )
        return {
            "source_url": source_url,
            "video_id": video_id,
            "source_video": video_file,
            "video_title": get_video_title(video_id),
        }
    finally:
        if sidecar_proc:
            try:
                stop_process(sidecar_proc, "sidecar Douzy")
            except Exception:
                pass
            unregister_active_process("sidecar Douzy", sidecar_proc)


def prune_video_previews():
    cutoff = time.time() - PREVIEW_TTL_SECONDS
    expired_sources = []
    with preview_registry_lock:
        expired = [key for key, item in preview_registry.items() if item["created_at"] < cutoff]
        for key in expired:
            preview = preview_registry.pop(key, None)
            if preview and preview.get("managed_source"):
                expired_sources.append(preview.get("source_video"))
    for source_path in expired_sources:
        remove_managed_source_if_unused(source_path)


def get_video_preview(preview_id):
    prune_video_previews()
    with preview_registry_lock:
        preview = preview_registry.get(preview_id)
        return dict(preview) if preview else None


def create_video_preview(video_url):
    with operation_admission_lock:
        if update_install_lock.locked():
            raise RuntimeError("Ứng dụng đang chuẩn bị cập nhật; không thể tạo preview mới.")
        if workflow_lock.locked():
            raise RuntimeError("Hãy chờ hàng đợi hiện tại chạy xong trước khi tạo preview mới.")
        if not preview_lock.acquire(blocking=False):
            raise RuntimeError("Một preview khác đang được chuẩn bị.")
    cancel_event.clear()
    try:
        config = load_app_config()
        notebook_url = get_translation_browser_url(config)
        preview = download_video_source(video_url, notebook_url)
        preview_id = uuid.uuid4().hex
        preview.update({
            "id": preview_id,
            "created_at": time.time(),
            "crop_coords": get_saved_crop_coords(),
            "source_type": "douyin",
            "managed_source": False,
        })
        with preview_registry_lock:
            preview_registry[preview_id] = preview
        return {
            "id": preview_id,
            "source_url": preview["source_url"],
            "video_id": preview["video_id"],
            "video_title": preview["video_title"],
            "crop_coords": preview["crop_coords"],
            "source_type": preview["source_type"],
            "video_url": f"/api/previews/{preview_id}/video",
        }
    finally:
        cancel_event.clear()
        preview_lock.release()


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


def create_local_video_preview(upload):
    with operation_admission_lock:
        if update_install_lock.locked():
            raise RuntimeError("Ứng dụng đang chuẩn bị cập nhật; không thể tạo preview mới.")
        if workflow_lock.locked():
            raise RuntimeError("Hãy chờ hàng đợi hiện tại chạy xong trước khi tạo preview mới.")
        if not preview_lock.acquire(blocking=False):
            raise RuntimeError("Một preview khác đang được chuẩn bị.")
    try:
        original_name = os.path.basename((upload.filename or "").replace("\\", "/"))
        extension = os.path.splitext(original_name)[1].lower()
        if extension not in ALLOWED_LOCAL_VIDEO_EXTENSIONS:
            raise ValueError("Hiện tại video trên máy phải là file MP4.")

        preview_id = uuid.uuid4().hex
        os.makedirs(LOCAL_VIDEO_DIR, exist_ok=True)
        destination = os.path.join(LOCAL_VIDEO_DIR, f"{preview_id}.mp4")
        temporary_path = f"{destination}.{uuid.uuid4().hex}.part"
        try:
            upload.save(temporary_path)
            if not os.path.isfile(temporary_path):
                raise ValueError("File video đang trống hoặc không đọc được.")
            validate_local_mp4_file(temporary_path)
            os.replace(temporary_path, destination)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

        preview = {
            "id": preview_id,
            "created_at": time.time(),
            "source_url": f"local://{preview_id}",
            "video_id": f"local-{preview_id[:12]}",
            "source_video": destination,
            "video_title": os.path.splitext(original_name)[0] or "Video local",
            "original_name": original_name,
            "crop_coords": get_saved_crop_coords(),
            "source_type": "local",
            "managed_source": True,
        }
        with preview_registry_lock:
            preview_registry[preview_id] = preview
        return {
            "id": preview_id,
            "source_url": preview["source_url"],
            "video_id": preview["video_id"],
            "video_title": preview["video_title"],
            "original_name": preview["original_name"],
            "crop_coords": preview["crop_coords"],
            "source_type": preview["source_type"],
            "video_url": f"/api/previews/{preview_id}/video",
        }
    finally:
        preview_lock.release()


def get_job_retry_step(job):
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    available_video = result.get("video") or ""
    if not os.path.isfile(str(available_video)):
        available_video = result.get("video_destination") or ""
    if os.path.isfile(str(available_video)):
        if job.get("step") == "translate" and os.path.isfile(str(result.get("raw_srt") or "")):
            return "translate"
        return "ocr"
    return "download"


def serialize_job(job):
    serialized = {
        key: job.get(key)
        for key in (
            "id", "source_url", "video_id", "video_title", "project_name", "ocr_lang",
            "crop_coords", "video_resolution", "preview_id", "status", "step", "error", "result",
            "created_at", "started_at", "finished_at", "source_type", "needs_edge_login",
            "resume_step",
        )
    }
    serialized["retry_step"] = (
        get_job_retry_step(job) if job.get("status") in {"error", "cancelled"} else None
    )
    serialized["result_ready"] = (
        job.get("status") == "success"
        and bool(job.get("result_ready"))
        and project_files_present(serialized.get("result"))
    )
    return serialized


PERSISTED_JOB_KEYS = (
    "id", "source_url", "video_id", "video_title", "source_video", "project_name",
    "ocr_lang", "crop_coords", "video_resolution", "preview_id", "use_saved_crop",
    "source_type", "managed_source", "status", "step", "error", "result", "result_ready",
    "needs_edge_login", "resume_step", "created_at", "started_at", "finished_at",
)


def persisted_job(job):
    return {key: job.get(key) for key in PERSISTED_JOB_KEYS}


def write_queue_file(path, jobs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = f"{path}.{uuid.uuid4().hex}.tmp"
    payload = {
        "version": QUEUE_SCHEMA_VERSION,
        "jobs": [persisted_job(job) for job in jobs[-MAX_PERSISTED_JOBS:]],
    }
    try:
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def persist_queue_state_locked(jobs=None):
    if app.config.get("TESTING"):
        return
    write_queue_file(QUEUE_PATH, workflow_jobs if jobs is None else jobs)


def replace_queue_jobs_locked(candidate_jobs):
    global queue_revision
    workflow_jobs[:] = candidate_jobs
    queue_revision += 1


def commit_queue_jobs_locked(candidate_jobs, *, retain_on_failure=False):
    try:
        persist_queue_state_locked(candidate_jobs)
    except (OSError, UnicodeError) as error:
        if retain_on_failure:
            replace_queue_jobs_locked(candidate_jobs)
        if isinstance(error, QueuePersistenceError):
            raise
        raise QueuePersistenceError(str(error)) from error
    replace_queue_jobs_locked(candidate_jobs)


def normalize_persisted_job(value):
    if not isinstance(value, dict):
        return None
    try:
        ocr_lang = validate_ocr_lang(value.get("ocr_lang", "ch"))
        crop_coords = validate_crop_coords(value.get("crop_coords"))
        resolution = validate_video_resolution(value.get("video_resolution"))
    except ValueError:
        return None

    status = value.get("status")
    if status not in {"queued", "running", "success", "error", "cancelled"}:
        status = "error"
    error = str(value.get("error") or "")
    if status == "running":
        status = "error"
        error = "App đã đóng khi job đang chạy. Bấm Chạy lại để tiếp tục từ file đã có."

    source_type = value.get("source_type") if value.get("source_type") in {"douyin", "local"} else "douyin"
    result = value.get("result") if isinstance(value.get("result"), dict) else {}
    source_video = str(value.get("source_video") or "")
    if source_type == "local" and not os.path.isfile(source_video) and not os.path.isfile(str(result.get("video") or "")):
        status = "error"
        error = "Video local không còn tồn tại. Hãy chọn lại file MP4."

    result_ready = bool(value.get("result_ready", False))
    if status == "success" and (
        "result_ready" not in value or result_ready
    ):
        # Recheck successful files so older false-positive translations become retryable.
        result_ready = project_result_ready(result)
        if not result_ready:
            status = "error"
            error = (
                "File sub dịch không hợp lệ hoặc vẫn là tiếng Trung. "
                "Hãy chạy lại bước dịch với model deepseek-v4-pro."
            )

    return {
        "id": str(value.get("id") or uuid.uuid4().hex),
        "source_url": str(value.get("source_url") or ""),
        "video_id": str(value.get("video_id") or ""),
        "video_title": str(value.get("video_title") or ""),
        "source_video": source_video,
        "project_name": str(value.get("project_name") or ""),
        "ocr_lang": ocr_lang,
        "crop_coords": crop_coords,
        "video_resolution": (
            {"width": resolution[0], "height": resolution[1]} if resolution else None
        ),
        "preview_id": str(value.get("preview_id") or ""),
        "use_saved_crop": bool(value.get("use_saved_crop", False)),
        "source_type": source_type,
        "managed_source": bool(value.get("managed_source", source_type == "local")),
        "status": status,
        "step": value.get("step") if value.get("step") in {"resolve", "download", "ocr", "translate", "done"} else "resolve",
        "error": error,
        "result": dict(result),
        "result_ready": result_ready if status == "success" else False,
        "needs_edge_login": bool(value.get("needs_edge_login", False)),
        "resume_step": (
            "translate"
            if status == "error" and "tiếng Trung" in error
            else value.get("resume_step")
            if value.get("resume_step") in {"download", "ocr", "translate"}
            else "download"
        ),
        "created_at": value.get("created_at") or int(time.time()),
        "started_at": value.get("started_at"),
        "finished_at": value.get("finished_at"),
    }


def quarantine_corrupt_file(path):
    if not os.path.isfile(path):
        return ""
    quarantined = f"{path}.corrupt-{int(time.time())}"
    try:
        os.replace(path, quarantined)
        log_to_app(f"Đã tách file dữ liệu hỏng để không làm mất dữ liệu: {quarantined}", "error")
        return quarantined
    except OSError as error:
        log_to_app(f"Không thể tách file dữ liệu hỏng: {error}", "error")
        return ""


def load_queue_file(path):
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        quarantine_corrupt_file(path)
        return []
    if not isinstance(payload, dict):
        quarantine_corrupt_file(path)
        return []
    version = payload.get("version", 1)
    if version not in {1, QUEUE_SCHEMA_VERSION}:
        quarantine_corrupt_file(path)
        return []
    values = payload.get("jobs", [])
    if not isinstance(values, list):
        quarantine_corrupt_file(path)
        return []
    jobs = [normalize_persisted_job(value) for value in values[-MAX_PERSISTED_JOBS:]]
    return [job for job in jobs if job]


def is_managed_local_video(path):
    if not path:
        return False
    try:
        local_root = os.path.normcase(os.path.abspath(LOCAL_VIDEO_DIR))
        candidate = os.path.normcase(os.path.abspath(path))
        return os.path.commonpath([local_root, candidate]) == local_root
    except (OSError, ValueError):
        return False


def remove_managed_source_if_unused(path):
    if not is_managed_local_video(path):
        return
    with queue_state_lock:
        in_use = any(job.get("source_video") == path for job in workflow_jobs)
    if not in_use:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def prune_orphaned_local_videos(referenced_paths=None):
    if not os.path.isdir(LOCAL_VIDEO_DIR):
        return 0
    referenced = {
        os.path.normcase(os.path.abspath(path))
        for path in (referenced_paths or [])
        if path
    }
    cutoff = time.time() - PREVIEW_TTL_SECONDS
    removed = 0
    for name in os.listdir(LOCAL_VIDEO_DIR):
        path = os.path.join(LOCAL_VIDEO_DIR, name)
        try:
            if (
                os.path.isfile(path)
                and name.lower().endswith(".mp4")
                and os.path.normcase(os.path.abspath(path)) not in referenced
                and os.path.getmtime(path) < cutoff
            ):
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def initialize_queue_state():
    global queue_revision, queue_state_initialized
    with queue_state_lock:
        if queue_state_initialized:
            return
        workflow_jobs[:] = load_queue_file(QUEUE_PATH)
        queue_revision += 1
        queue_state_initialized = True
        referenced_paths = [job.get("source_video") for job in workflow_jobs]
        try:
            persist_queue_state_locked()
        except OSError as error:
            log_to_app(f"Chưa thể chuẩn hoá file hàng đợi: {error}", "error")
    removed = prune_orphaned_local_videos(referenced_paths)
    if removed:
        log_to_app(f"Đã dọn {removed} video cache mồ côi quá hạn.")


def queue_snapshot():
    # Copy under the lock, then serialize outside it so polling file checks
    # never block the queue worker's progress updates.
    with queue_state_lock:
        jobs = [{**job, "result": dict(job.get("result") or {})} for job in workflow_jobs]
    return [serialize_job(job) for job in jobs]


def enqueue_workflow(
    video_url,
    ocr_lang,
    project_name=None,
    *,
    crop_coords=None,
    video_resolution=None,
    preview_id="",
    use_saved_crop=True,
    autostart=False,
):
    ocr_lang = validate_ocr_lang(ocr_lang)
    if project_name is not None and not isinstance(project_name, str):
        raise ValueError("Tên bộ file không hợp lệ.")
    preview = get_video_preview(preview_id) if preview_id else None
    if preview_id and not preview:
        raise ValueError("Preview đã hết hạn. Hãy tạo lại preview video.")
    if preview:
        source_url = preview["source_url"]
        if video_url != source_url:
            raise ValueError("Preview không khớp với video đã chọn.")
        source_type = preview.get("source_type", "douyin")
    else:
        source_url = validate_douyin_url(video_url)
        source_type = "douyin"
    normalized_crop = validate_crop_coords(crop_coords)
    normalized_resolution = validate_video_resolution(video_resolution)
    job = {
        "id": uuid.uuid4().hex,
        "source_url": source_url,
        "video_id": preview.get("video_id", "") if preview else "",
        "video_title": preview.get("video_title", "") if preview else "",
        "source_video": preview.get("source_video", "") if preview else "",
        "project_name": project_name or "",
        "ocr_lang": ocr_lang,
        "crop_coords": normalized_crop,
        "video_resolution": (
            {"width": normalized_resolution[0], "height": normalized_resolution[1]}
            if normalized_resolution else None
        ),
        "preview_id": preview_id,
        "use_saved_crop": bool(use_saved_crop),
        "source_type": source_type,
        "managed_source": bool(preview and preview.get("managed_source")),
        "status": "queued",
        "step": "resolve",
        "error": "",
        "result": {},
        "result_ready": False,
        "needs_edge_login": False,
        "resume_step": "download",
        "created_at": int(time.time()),
        "started_at": None,
        "finished_at": None,
    }
    with queue_state_lock:
        if len(workflow_jobs) >= MAX_IN_MEMORY_JOBS:
            raise RuntimeError(
                f"Hàng đợi đã đạt giới hạn {MAX_IN_MEMORY_JOBS} job. Hãy dọn job đã hoàn tất trước."
            )
        candidate_jobs = copy.deepcopy(workflow_jobs)
        candidate_jobs.append(job)
        commit_queue_jobs_locked(candidate_jobs)
    if autostart:
        start_workflow_queue()
    return serialize_job(job)


def queue_workflow(video_url, ocr_lang, project_name=None):
    enqueue_workflow(video_url, ocr_lang, project_name, autostart=False)
    return start_workflow_queue()


def run_single_workflow(video_url, ocr_lang, project_name=None):
    source_url = validate_douyin_url(video_url)
    validate_ocr_lang(ocr_lang)
    if project_name is not None and not isinstance(project_name, str):
        raise ValueError("Tên bộ file không hợp lệ.")
    with operation_admission_lock:
        if update_install_lock.locked():
            raise RuntimeError("Ứng dụng đang chuẩn bị cập nhật; chưa thể chạy workflow CLI.")
        if preview_lock.locked() or not workflow_lock.acquire(blocking=False):
            raise RuntimeError("Một tác vụ VietSub Studio khác đang chạy.")
    cancel_event.clear()
    reset_progress()
    run_workflow_thread(source_url, ocr_lang, project_name)
    return progress_snapshot()


def run_prefetch_worker():
    global prefetch_job_id
    workflow_context.quiet = True
    workflow_context.detached = True
    workflow_context.cancel_event = prefetch_cancel_event
    try:
        # Wait until the running job is past its download step so two Douzy
        # sidecars never run at the same time.
        while not prefetch_cancel_event.is_set():
            with status_lock:
                status = progress_status["status"]
                step = progress_status["step"]
            if status != "running":
                return
            if step in {"ocr", "translate"}:
                break
            time.sleep(2)
        if prefetch_cancel_event.is_set():
            return

        with queue_state_lock:
            target = next(
                (
                    dict(job) for job in workflow_jobs
                    if job["status"] == "queued"
                    and not job.get("source_video")
                    and job.get("source_type", "douyin") == "douyin"
                ),
                None,
            )
        if not target:
            return
        prefetch_job_id = target["id"]
        try:
            notebook_url = get_translation_browser_url(load_app_config())
            downloaded = download_video_source(target["source_url"], notebook_url)
        except WorkflowCancelled:
            return
        except Exception:
            # Best effort only: the queue worker downloads normally on failure.
            return

        with queue_state_lock:
            candidate_jobs = copy.deepcopy(workflow_jobs)
            for job in candidate_jobs:
                if job["id"] == target["id"] and job["status"] == "queued":
                    job["source_url"] = downloaded["source_url"]
                    job["video_id"] = downloaded["video_id"]
                    job["source_video"] = downloaded["source_video"]
                    if not job.get("video_title"):
                        job["video_title"] = downloaded.get("video_title", "")
                    try:
                        commit_queue_jobs_locked(candidate_jobs)
                    except OSError as error:
                        log_to_app(f"Prefetch chưa thể lưu trạng thái hàng đợi: {error}", "error")
                    break
    finally:
        prefetch_job_id = ""
        workflow_context.quiet = False
        workflow_context.detached = False
        workflow_context.cancel_event = None


def start_prefetch_thread():
    global prefetch_thread
    if app.config.get("TESTING"):
        return
    if prefetch_thread and prefetch_thread.is_alive():
        return
    prefetch_cancel_event.clear()
    prefetch_thread = Thread(target=run_prefetch_worker, daemon=True)
    prefetch_thread.start()


def coordinate_prefetch_before_job(job):
    thread = prefetch_thread
    if not thread or not thread.is_alive():
        return
    if prefetch_job_id == job.get("id"):
        # The prefetch is fetching exactly this job; let it finish and reuse the file.
        thread.join()
    elif not job.get("source_video"):
        # This job needs its own sidecar download; stop the unrelated prefetch first.
        prefetch_cancel_event.set()
        thread.join()


def missing_runtime_dependencies(source_types):
    source_types = set(source_types or ())
    missing = []
    if not os.path.isfile(VIDEOCR_CLI):
        missing.append("VideOCR")
    try:
        translation_config = get_translation_runtime_config(require_ready=True)
    except (OSError, UnicodeError, ValueError):
        missing.append("cấu hình dịch")
        translation_config = {"provider": ""}
    if translation_config.get("provider") == "notebook" and not resolve_edge_executable():
        missing.append("Microsoft Edge")
    if "douyin" in source_types:
        if not os.path.isfile(SIDECAR_BIN):
            missing.append("Douzy sidecar")
        if not os.path.isfile(DOUZY_CONFIG):
            missing.append("cấu hình Douzy")
    return missing


def start_workflow_queue():
    global queue_worker_thread
    with operation_admission_lock:
        if update_install_lock.locked():
            raise RuntimeError("Ứng dụng đang chuẩn bị cập nhật; chưa thể chạy hàng đợi.")
        if preview_lock.locked():
            raise RuntimeError("Hãy chờ preview hiện tại chuẩn bị xong trước khi chạy hàng đợi.")
        with queue_state_lock:
            has_queued = any(job["status"] == "queued" for job in workflow_jobs)
            if not has_queued:
                raise RuntimeError("Hàng đợi chưa có video nào sẵn sàng.")
            queued_sources = {
                job.get("source_type", "douyin")
                for job in workflow_jobs
                if job["status"] == "queued"
            }
            missing = [] if app.config.get("TESTING") else missing_runtime_dependencies(queued_sources)
            if missing:
                raise RuntimeError("Thiếu thành phần bắt buộc: " + ", ".join(missing) + ".")
            if queue_worker_thread and queue_worker_thread.is_alive():
                return queue_worker_thread
            queue_worker_thread = Thread(target=run_queue_worker, daemon=True)
            queue_worker_thread.start()
            return queue_worker_thread


def run_queue_worker():
    global current_job_id, queue_worker_thread
    while True:
        with queue_state_lock:
            picked = next((item for item in workflow_jobs if item["status"] == "queued"), None)
            if not picked:
                current_job_id = ""
                queue_worker_thread = None
                return
            picked_snapshot = {"id": picked["id"], "source_video": picked.get("source_video", "")}
        # Outside the lock: wait for a prefetch of this exact job, or stop an unrelated one.
        coordinate_prefetch_before_job(picked_snapshot)
        with queue_state_lock:
            candidate_jobs = copy.deepcopy(workflow_jobs)
            job = next(
                (
                    item for item in candidate_jobs
                    if item["id"] == picked_snapshot["id"] and item["status"] == "queued"
                ),
                None,
            )
            if not job:
                continue
            job.update({
                "status": "running",
                "error": "",
                "needs_edge_login": False,
                "result_ready": False,
                "started_at": int(time.time()),
            })
            current_job_id = job["id"]
            job_data = copy.deepcopy(job)
            try:
                commit_queue_jobs_locked(candidate_jobs)
            except OSError as error:
                current_job_id = ""
                log_to_app(f"Không thể ghi trạng thái bắt đầu job: {error}", "error")
                queue_worker_thread = None
                return
        workflow_lock.acquire()
        cancel_event.clear()
        reset_progress(job_data["id"])
        stop_worker = False
        try:
            start_prefetch_thread()
            prepared_video = None
            if job_data.get("source_video"):
                prepared_video = {
                    "source_url": job_data["source_url"],
                    "video_id": job_data["video_id"],
                    "source_video": job_data["source_video"],
                    "video_title": job_data.get("video_title", ""),
                    "source_type": job_data.get("source_type", "douyin"),
                }
            run_workflow_thread(
                job_data["source_url"],
                job_data["ocr_lang"],
                job_data.get("project_name"),
                prepared_video=prepared_video,
                crop_coords=job_data.get("crop_coords"),
                use_saved_crop=job_data.get("use_saved_crop", True),
                video_resolution=job_data.get("video_resolution"),
                resume_project=job_data.get("result"),
                resume_step=job_data.get("resume_step", "download"),
            )
        except QueuePersistenceError as error:
            stop_worker = True
            log_to_app(str(error), "error")
        except Exception as error:
            try:
                update_progress(status="error", error=str(error))
            except QueuePersistenceError as persist_error:
                error = persist_error
                stop_worker = True
            log_to_app(f"Worker queue gặp lỗi ngoài dự kiến: {error}", "error")
        finally:
            cancel_event.clear()
            if workflow_lock.locked():
                workflow_lock.release()
            with queue_state_lock:
                candidate_jobs = copy.deepcopy(workflow_jobs)
                for item in candidate_jobs:
                    if item["id"] == job_data["id"]:
                        item["finished_at"] = int(time.time())
                        break
                current_job_id = ""
                if stop_worker:
                    replace_queue_jobs_locked(candidate_jobs)
                else:
                    try:
                        commit_queue_jobs_locked(candidate_jobs, retain_on_failure=True)
                    except OSError as error:
                        stop_worker = True
                        log_to_app(f"Không thể lưu thời điểm kết thúc job: {error}", "error")
        if stop_worker:
            queue_worker_thread = None
            return


def remove_queued_job(job_id):
    removed = None
    with queue_state_lock:
        candidate_jobs = copy.deepcopy(workflow_jobs)
        for index, job in enumerate(candidate_jobs):
            if job["id"] != job_id:
                continue
            if job["status"] == "running":
                raise RuntimeError("Không thể xoá job đang chạy; hãy huỷ job trước.")
            removed = candidate_jobs.pop(index)
            commit_queue_jobs_locked(candidate_jobs)
            break
    if removed:
        remove_managed_source_if_unused(removed.get("source_video"))
        return serialize_job(removed)
    raise ValueError("Không tìm thấy video trong hàng đợi.")


def clear_finished_jobs():
    removed_sources = []
    with queue_state_lock:
        removable = {"success"}
        candidate_jobs = copy.deepcopy(workflow_jobs)
        removed_sources = [
            job.get("source_video") for job in candidate_jobs
            if job["status"] in removable and job.get("managed_source")
        ]
        candidate_jobs = [job for job in candidate_jobs if job["status"] not in removable]
        commit_queue_jobs_locked(candidate_jobs)
    for source_path in removed_sources:
        remove_managed_source_if_unused(source_path)


def retry_queue_job(job_id):
    with queue_state_lock:
        candidate_jobs = copy.deepcopy(workflow_jobs)
        job = next((item for item in candidate_jobs if item["id"] == job_id), None)
        if not job:
            raise ValueError("Không tìm thấy video trong hàng đợi.")
        if job["status"] not in {"error", "cancelled"}:
            raise RuntimeError("Chỉ có thể chạy lại job bị lỗi hoặc đã huỷ.")

        resume_step = get_job_retry_step(job)
        source_video = job.get("source_video") or ""
        if resume_step == "download" and source_video and not os.path.isfile(source_video):
            if job.get("source_type") == "local":
                raise FileNotFoundError("Video local không còn tồn tại. Hãy chọn lại file MP4.")
            # A Douyin preview cache may have been cleaned externally; retry by downloading it again.
            job["source_video"] = ""
            job["preview_id"] = ""

        job.update({
            "status": "queued",
            "step": resume_step if resume_step != "download" else "resolve",
            "error": "",
            "result_ready": False,
            "needs_edge_login": False,
            "resume_step": resume_step,
            "started_at": None,
            "finished_at": None,
        })
        commit_queue_jobs_locked(candidate_jobs)
        return serialize_job(job)


def update_current_job_metadata(**values):
    if not current_job_id:
        return
    with queue_state_lock:
        candidate_jobs = copy.deepcopy(workflow_jobs)
        for job in candidate_jobs:
            if job["id"] == current_job_id:
                job.update(values)
                commit_queue_jobs_locked(candidate_jobs)
                break


def run_workflow_thread(
    video_url,
    ocr_lang,
    project_name=None,
    *,
    prepared_video=None,
    crop_coords=None,
    use_saved_crop=True,
    video_resolution=None,
    resume_project=None,
    resume_step="download",
):
    sidecar_proc = None
    project = None
    video_id = ""
    source_url = video_url
    source_type = prepared_video.get("source_type", "douyin") if prepared_video else "douyin"
    try:
        check_cancel_requested()
        config = load_app_config()
        translation_config = get_translation_runtime_config(config, require_ready=True)
        notebook_url = translation_config.get("notebook_url", "")
        output_dir = validate_output_dir(config.get("output_dir", ""))

        if resume_step in {"ocr", "translate"}:
            resume_project = resume_project if isinstance(resume_project, dict) else {}
            required_keys = ("project_name", "project_dir", "video", "raw_srt", "translated_srt")
            if not all(resume_project.get(key) for key in required_keys):
                raise FileNotFoundError("Bộ file cũ không còn đầy đủ để chạy tiếp.")
            project = dict(resume_project)
            if not os.path.isfile(project["video"]) and os.path.isfile(str(project.get("video_destination") or "")):
                project["video"] = project["video_destination"]
                project["move_source_video"] = False
            project["source_video"] = resume_project.get("source_video", project["video"])
            project["thumbnail"] = resume_project.get("thumbnail", "")
            project["video_destination"] = resume_project.get("video_destination", project["video"])
            project["move_source_video"] = bool(resume_project.get("move_source_video"))
            if not os.path.isdir(project["project_dir"]) or not os.path.isfile(project["video"]):
                raise FileNotFoundError("Video dự án không còn tồn tại để chạy tiếp.")
            source_url = prepared_video.get("source_url", video_url) if prepared_video else video_url
            video_id = prepared_video.get("video_id", "") if prepared_video else ""
            log_to_app(
                "Dùng lại bộ file đã có và tiếp tục từ bước dịch."
                if resume_step == "translate"
                else "Dùng lại video dự án và tiếp tục từ bước OCR."
            )
            update_progress(step=resume_step, result=project)
        else:
            update_progress(step="download")
            if prepared_video:
                source_url = prepared_video["source_url"]
                video_id = prepared_video["video_id"]
                video_file = prepared_video["source_video"]
                video_title = prepared_video.get("video_title", "")
                if not os.path.isfile(video_file):
                    raise FileNotFoundError("Video preview không còn tồn tại. Hãy tạo lại preview.")
                log_to_app("Dùng video đã chuẩn bị từ preview, bỏ qua bước tải lại.")
            else:
                downloaded = download_video_source(video_url, notebook_url)
                source_url = downloaded["source_url"]
                video_id = downloaded["video_id"]
                video_file = downloaded["source_video"]
                video_title = downloaded.get("video_title", "")
            update_progress(result={"source_video": video_file})
            project = prepare_project_files(
                video_file,
                project_name,
                video_id,
                video_title=video_title,
                output_dir=output_dir,
                defer_video_move=(
                    source_type == "douyin" and should_move_douzy_source(video_file)
                ),
                include_thumbnail=source_type == "douyin",
            )
            update_progress(result=project)
        write_project_manifest(
            project,
            video_id=video_id,
            source_url=source_url,
            status="processing",
        )

        if resume_step == "translate":
            raw_srt = project["raw_srt"]
            if not os.path.isfile(raw_srt):
                raise FileNotFoundError("File sub OCR không còn tồn tại để chạy tiếp bước dịch.")
        else:
            update_progress(step="ocr")
            raw_srt = run_videocr(
                project["video"],
                ocr_lang=ocr_lang,
                output_srt=project["raw_srt"],
                crop_coords=crop_coords,
                use_saved_crop=use_saved_crop,
                video_resolution=video_resolution,
            )
            update_progress(result={"raw_srt": raw_srt})

        update_progress(step="translate")
        if translation_config["provider"] == "notebook":
            translated_srt = translate_srt_via_gemini_edge(
                raw_srt,
                notebook_url,
                output_srt=project["translated_srt"],
                translation_instructions=translation_config["translation_instructions"],
            )
        else:
            translated_srt = translate_srt_via_api(
                raw_srt,
                translation_config,
                output_srt=project["translated_srt"],
            )
        update_progress(result={"translated_srt": translated_srt})
        project["raw_srt"] = raw_srt
        project["translated_srt"] = translated_srt
        validate_project_result(project)
        project = finalize_project_assets(project, video_id)
        update_progress(result=project)
        validate_project_result(project)
        write_project_manifest(
            project,
            video_id=video_id,
            source_url=source_url,
            status="success",
        )

        update_progress(status="success", step="done", result_ready=True)
        log_to_app("Success! Hoàn thành toàn bộ quy trình.")
    except QueuePersistenceError:
        raise
    except WorkflowCancelled as e:
        update_progress(status="cancelled", error=str(e))
        log_to_app(str(e), "error")
        if project:
            try:
                write_project_manifest(
                    project,
                    video_id=video_id,
                    source_url=source_url,
                    status="cancelled",
                    error=str(e),
                )
            except OSError as manifest_error:
                log_to_app(f"Không thể lưu manifest huỷ job: {manifest_error}", "error")
    except EdgeLoginRequired as e:
        update_progress(status="error", error=str(e))
        try:
            update_current_job_metadata(needs_edge_login=True)
        except QueuePersistenceError as persist_error:
            raise mark_current_job_persistence_error(persist_error) from persist_error
        log_to_app(str(e), "error")
        if project:
            try:
                write_project_manifest(
                    project,
                    video_id=video_id,
                    source_url=source_url,
                    status="error",
                    error=str(e),
                )
            except OSError:
                pass
    except Exception as e:
        update_progress(status="error", error=str(e))
        log_to_app(f"Lỗi quy trình: {e}", "error")
        if project:
            try:
                write_project_manifest(
                    project,
                    video_id=video_id,
                    source_url=source_url,
                    status="error",
                    error=str(e),
                )
            except OSError:
                pass
    finally:
        if sidecar_proc:
            try:
                stop_process(sidecar_proc, "sidecar Douzy")
            except Exception:
                pass
            unregister_active_process("sidecar Douzy", sidecar_proc)
        cancel_event.clear()
        if workflow_lock.locked():
            workflow_lock.release()

def local_request_host_allowed():
    hostname = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def api_request_authorized():
    if app.config.get("TESTING"):
        return True
    supplied_token = request.headers.get(API_TOKEN_HEADER, "")
    if not secrets.compare_digest(supplied_token, API_TOKEN):
        return False
    origin = request.headers.get("Origin")
    if origin:
        parsed_origin = urlparse(origin)
        if parsed_origin.scheme != "http" or parsed_origin.netloc.lower() != request.host.lower():
            return False
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    return fetch_site in {"", "none", "same-origin", "same-site"}


@app.before_request
def protect_local_control_plane():
    if not local_request_host_allowed():
        return jsonify({"error": "Host cục bộ không hợp lệ."}), 403
    if app.config.get("TESTING"):
        return None
    if not request.path.startswith("/api/") or request.endpoint == "stream_preview_video":
        return None
    if not api_request_authorized():
        return jsonify({"error": "Yêu cầu không được ứng dụng VietSub Studio xác thực."}), 403
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        content_type = (request.content_type or "").lower()
        if request.endpoint != "prepare_local_preview" and "application/json" not in content_type:
            return jsonify({"error": "API thay đổi trạng thái yêu cầu JSON body."}), 415
    return None


@app.after_request
def add_response_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    )
    return response


# Web routes
@app.route('/')
def index():
    return render_template('index.html', app_version=APP_VERSION, api_token=API_TOKEN)


@app.route('/static/<path:filename>')
def static_asset(filename):
    if filename not in {"app.js", "style.css"}:
        return jsonify({"error": "Static asset không tồn tại."}), 404
    return send_from_directory(os.path.join(RESOURCE_DIR, "static"), filename)


@app.route('/avatar.jpg')
def avatar_image():
    return send_from_directory(RESOURCE_DIR, "avatar.jpg", mimetype="image/jpeg")


@app.route('/api/check-edge')
def check_edge():
    connected = check_edge_status()
    return jsonify({"connected": connected})


@app.route('/api/edge/show', methods=['POST'])
def show_edge():
    config = load_app_config()
    try:
        show_edge_for_login(config.get("notebook_url"))
    except (RuntimeError, ValueError, OSError) as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"ok": True})


@app.route('/api/update')
def check_update():
    try:
        return jsonify(get_update_status())
    except (RuntimeError, ValueError, urllib.error.URLError):
        return jsonify({
            "current_version": APP_VERSION,
            "latest_version": "",
            "update_available": False,
            "check_failed": True,
        })


@app.route('/api/update/open', methods=['POST'])
def open_update():
    try:
        release_url = get_update_status().get("release_url", LATEST_RELEASE_URL)
    except (RuntimeError, ValueError, urllib.error.URLError):
        release_url = LATEST_RELEASE_URL
    if not webbrowser.open(validate_release_url(release_url), new=2):
        return jsonify({"error": "Không thể mở trang cập nhật trong trình duyệt."}), 500
    return jsonify({"ok": True, "url": release_url})


@app.route('/api/update/install', methods=['POST'])
def install_update():
    with operation_admission_lock:
        if not update_install_lock.acquire(blocking=False):
            return jsonify({"error": "Một lượt cập nhật khác đang được chuẩn bị."}), 409
        with queue_state_lock:
            queue_active = bool(queue_worker_thread and queue_worker_thread.is_alive())
        if workflow_lock.locked() or preview_lock.locked() or queue_active:
            update_install_lock.release()
            return jsonify({"error": "Hãy chờ mọi job và preview hiện tại chạy xong rồi cập nhật app."}), 409

    prefetch_cancel_event.set()
    if prefetch_thread and prefetch_thread.is_alive():
        prefetch_thread.join(timeout=10)
        if prefetch_thread.is_alive():
            prefetch_cancel_event.clear()
            update_install_lock.release()
            return jsonify({"error": "Chưa thể dừng tải trước an toàn để cập nhật."}), 409

    update_package = None
    try:
        payload = fetch_latest_release_payload()
        update_package = prepare_update_executable(payload)
        launch_update_helper(update_package)
    except (OSError, RuntimeError, ValueError, urllib.error.URLError, subprocess.SubprocessError) as error:
        if update_package:
            try:
                if os.path.exists(update_package["staged_path"]):
                    os.remove(update_package["staged_path"])
            except OSError:
                pass
            shutil.rmtree(update_package["update_dir"], ignore_errors=True)
        prefetch_cancel_event.clear()
        update_install_lock.release()
        return jsonify({"error": str(error)}), 400

    schedule_app_exit()
    if app.config.get("TESTING"):
        prefetch_cancel_event.clear()
        update_install_lock.release()
    return jsonify({
        "status": "installing",
        "version": update_package["version"],
        "message": "Đã xác minh bản cập nhật. App sẽ tự khởi động lại.",
    }), 202


@app.route('/api/shortcut', methods=['POST'])
def create_shortcut():
    try:
        shortcut_path = create_desktop_shortcut()
        with config_write_lock:
            config = load_app_config()
            config["desktop_shortcut_initialized"] = True
            save_app_config(config)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"ok": True, "path": shortcut_path})


@app.route('/api/health')
def health():
    config = load_app_config()
    try:
        validate_notebook_url(config.get("notebook_url"))
        notebook_ok = True
    except ValueError:
        notebook_ok = False
    try:
        translation_provider = validate_translation_provider(
            config.get("translation_provider")
        )
    except ValueError:
        translation_provider = "notebook"
    try:
        runtime_config = get_translation_runtime_config(config, require_ready=True)
        translation_ok = True
        translation_detail = (
            "Dịch bằng Gemini Notebook qua Edge."
            if runtime_config["provider"] == "notebook"
            else f"{runtime_config['label']} · model {runtime_config['model']}"
        )
    except (OSError, UnicodeError, ValueError) as error:
        translation_ok = False
        translation_detail = str(error)
    checks = {
        "douzy_sidecar": {
            "ok": os.path.isfile(SIDECAR_BIN),
            "required": False,
            "detail": "Chỉ cần cho link Douyin; MP4 trên máy không dùng Douzy.",
        },
        "douzy_config": {
            "ok": os.path.isfile(DOUZY_CONFIG),
            "required": False,
            "detail": "Chỉ cần cho link Douyin; mở Douzy và chọn thư mục tải để tạo cấu hình.",
        },
        "douzy_database": {
            "ok": os.path.isfile(DOUZY_DB),
            "required": False,
            "detail": "Database sẽ xuất hiện sau khi Douzy tải hoặc ghi nhận video đầu tiên.",
        },
        "videocr": {
            "ok": os.path.isfile(VIDEOCR_CLI),
            "required": True,
            "detail": r"Cài VideOCR tại C:\Program Files\VideOCR.",
        },
        "ffprobe": {
            "ok": bool(resolve_ffprobe_executable()),
            "required": False,
            "detail": "Khuyên dùng; giúp chia video dài và đổi vùng crop OCR chính xác.",
        },
        "ffmpeg": {
            "ok": bool(resolve_ffmpeg_executable()),
            "required": False,
            "detail": "Khuyên dùng cho video dài hoặc video ghép bị đổi độ phân giải giữa chừng.",
        },
        "translation": {
            "ok": translation_ok,
            "required": True,
            "detail": translation_detail,
        },
        "notebook": {
            "ok": notebook_ok,
            "required": translation_provider == "notebook",
            "detail": (
                "Dán link Gemini Notebook của bạn trong phần Thiết lập."
                if translation_provider == "notebook"
                else "Không cần khi đang dịch bằng API."
            ),
        },
        "edge": {
            "ok": bool(resolve_edge_executable()),
            "required": translation_provider == "notebook",
            "connected": check_edge_status(),
            "detail": (
                "Cần Microsoft Edge; app sẽ tự mở profile debug khi bắt đầu dịch."
                if translation_provider == "notebook"
                else "Chỉ dùng khi cần giải mã link share Douyin ngắn."
            ),
        },
    }
    ready_local = all(item["ok"] for item in checks.values() if item["required"])
    ready_douyin = ready_local and checks["douzy_sidecar"]["ok"] and checks["douzy_config"]["ok"]
    return jsonify({
        "ready": ready_local,
        "ready_local": ready_local,
        "ready_douyin": ready_douyin,
        "checks": checks,
    })


@app.route('/api/previews', methods=['POST'])
def prepare_preview():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body is required"}), 400
    try:
        return jsonify(create_video_preview(data.get("url"))), 201
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    except (FileNotFoundError, TimeoutError, OSError) as error:
        return jsonify({"error": str(error)}), 500


@app.route('/api/previews/local', methods=['POST'])
def prepare_local_preview():
    upload = request.files.get("video")
    if upload is None:
        return jsonify({"error": "Hãy chọn một file MP4 trên máy."}), 400
    try:
        return jsonify(create_local_video_preview(upload)), 201
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    except OSError as error:
        return jsonify({"error": f"Không thể lưu video local: {error}"}), 500


@app.route('/api/previews/<preview_id>/video')
def stream_preview_video(preview_id):
    preview = get_video_preview(preview_id)
    if not preview or not os.path.isfile(preview.get("source_video", "")):
        return jsonify({"error": "Preview không còn tồn tại."}), 404
    return send_file(preview["source_video"], conditional=True)


@app.route('/api/queue', methods=['GET', 'POST'])
def handle_queue():
    if request.method == 'GET':
        try:
            client_revision = int(request.args.get("revision", "-1"))
        except ValueError:
            client_revision = -1
        with queue_state_lock:
            current_revision = queue_revision
            active_job_id = current_job_id
        jobs = None if client_revision == current_revision else queue_snapshot()
        return jsonify({
            "jobs": jobs,
            "revision": current_revision,
            "running": workflow_lock.locked(),
            "current_job_id": active_job_id,
        })
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body is required"}), 400
    try:
        job = enqueue_workflow(
            data.get("url"),
            data.get("lang", "ch"),
            data.get("name"),
            crop_coords=data.get("crop_coords"),
            video_resolution=data.get("video_resolution"),
            preview_id=data.get("preview_id", ""),
            use_saved_crop=not bool(data.get("preview_id")),
        )
        return jsonify(job), 201
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409


@app.route('/api/queue/start', methods=['POST'])
def start_queue():
    try:
        start_workflow_queue()
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    return jsonify({"status": "started"}), 202


@app.route('/api/queue/<job_id>', methods=['DELETE'])
def delete_queue_job(job_id):
    try:
        return jsonify(remove_queued_job(job_id))
    except ValueError as error:
        return jsonify({"error": str(error)}), 404
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409


@app.route('/api/queue/<job_id>/retry', methods=['POST'])
def retry_job(job_id):
    try:
        return jsonify(retry_queue_job(job_id))
    except ValueError as error:
        return jsonify({"error": str(error)}), 404
    except FileNotFoundError as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409


@app.route('/api/queue/finished', methods=['DELETE'])
def clear_queue_finished():
    clear_finished_jobs()
    return jsonify({"ok": True})

def apply_settings_update(data):
    config = load_app_config()
    try:
        if "translation_provider" in data:
            config["translation_provider"] = validate_translation_provider(
                data["translation_provider"]
            )
        translation_provider = validate_translation_provider(
            config.get("translation_provider")
        )
        if "notebook_url" in data:
            config["notebook_url"] = validate_notebook_url(
                data["notebook_url"], allow_empty=True
            )
        if translation_provider != "notebook":
            if "translation_api_base_url" in data:
                config["translation_api_base_url"] = validate_translation_api_base_url(
                    data["translation_api_base_url"],
                    translation_provider,
                )
            if "translation_api_model" in data:
                config["translation_api_model"] = validate_translation_api_model(
                    data["translation_api_model"],
                    translation_provider,
                )
        if "translation_api_key" in data:
            if data["translation_api_key"] is None:
                config["translation_api_key_protected"] = ""
            else:
                api_key = validate_translation_api_key(
                    data["translation_api_key"],
                    allow_empty=True,
                )
                if api_key:
                    config["translation_api_key_protected"] = protect_config_secret(api_key)
            config.pop("translation_api_key", None)
        if "translation_instructions" in data:
            config["translation_instructions"] = validate_translation_instructions(
                data["translation_instructions"]
            )
        if "ocr_lang" in data:
            config["ocr_lang"] = validate_ocr_lang(data["ocr_lang"])
        if "ocr_model_mode" in data:
            config["ocr_model_mode"] = validate_ocr_model_mode(data["ocr_model_mode"])
        if "ocr_frames_to_skip" in data:
            config["ocr_frames_to_skip"] = validate_ocr_frames_to_skip(data["ocr_frames_to_skip"])
        if "output_dir" in data:
            config["output_dir"] = validate_output_dir(data["output_dir"])
        if "crop_coords" in data:
            config["crop_coords"] = validate_crop_coords(data["crop_coords"])
        if "edge_background" in data:
            if not isinstance(data["edge_background"], bool):
                raise ValueError("Tuỳ chọn chạy Edge ẩn không hợp lệ.")
            config["edge_background"] = data["edge_background"]
        save_app_config(config)
    except (OSError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "JSON body is required"}), 400
        # Serialize read-modify-write so concurrent saves cannot drop each other's fields.
        with config_write_lock:
            return apply_settings_update(data)
    else:
        # GET request
        config = load_app_config()
        try:
            ocr_model_mode = validate_ocr_model_mode(config.get("ocr_model_mode"))
        except ValueError:
            ocr_model_mode = "auto"
        try:
            ocr_frames_to_skip = validate_ocr_frames_to_skip(config.get("ocr_frames_to_skip"))
        except ValueError:
            ocr_frames_to_skip = None
        try:
            translation_provider = validate_translation_provider(
                config.get("translation_provider")
            )
        except ValueError:
            translation_provider = "notebook"
        preset = TRANSLATION_PROVIDER_PRESETS.get(translation_provider, {})
        raw_api_base_url = config.get("translation_api_base_url", "")
        raw_api_model = config.get("translation_api_model", "")
        if translation_provider == "notebook":
            translation_api_base_url = raw_api_base_url if isinstance(raw_api_base_url, str) else ""
            translation_api_model = raw_api_model if isinstance(raw_api_model, str) else ""
        else:
            try:
                translation_api_base_url = validate_translation_api_base_url(
                    raw_api_base_url,
                    translation_provider,
                )
            except ValueError:
                translation_api_base_url = preset.get("base_url", "")
            try:
                translation_api_model = validate_translation_api_model(
                    raw_api_model,
                    translation_provider,
                )
            except ValueError:
                translation_api_model = preset.get("model", "")
        try:
            translation_api_key_set = bool(get_translation_api_key(config))
        except (OSError, UnicodeError, ValueError):
            translation_api_key_set = False
        try:
            translation_instructions = validate_translation_instructions(
                config.get("translation_instructions")
            )
        except ValueError:
            translation_instructions = TRANSLATION_GLOSSARY_PROMPT
        crop = get_saved_crop_coords()
        down_dir = get_douzy_download_dir()
        try:
            effective_output_dir = validate_output_dir(config.get("output_dir", ""))
        except ValueError:
            effective_output_dir = ""
        effective_output_dir = effective_output_dir or get_default_output_directory()
        shortcut_supported = is_packaged_app()
        shortcut_exists = False
        if shortcut_supported:
            try:
                shortcut_exists = os.path.isfile(desktop_shortcut_path())
            except (OSError, RuntimeError):
                pass
        return jsonify({
            "crop_coords": crop,
            "download_dir": down_dir,
            "notebook_url": config.get("notebook_url"),
            "translation_provider": translation_provider,
            "translation_api_base_url": translation_api_base_url,
            "translation_api_model": translation_api_model,
            "translation_api_key_set": translation_api_key_set,
            "translation_instructions": translation_instructions,
            "translation_default_instructions": TRANSLATION_GLOSSARY_PROMPT,
            "translation_provider_presets": {
                key: {
                    "base_url": value["base_url"],
                    "model": value["model"],
                }
                for key, value in TRANSLATION_PROVIDER_PRESETS.items()
            },
            "ocr_lang": config.get("ocr_lang"),
            "ocr_model_mode": ocr_model_mode,
            "ocr_frames_to_skip": ocr_frames_to_skip,
            "output_dir": config.get("output_dir", ""),
            "default_output_dir": get_default_output_directory(),
            "output_dir_synced": is_cloud_synced_path(effective_output_dir),
            "edge_background": bool(config.get("edge_background", True)),
            "desktop_shortcut_supported": shortcut_supported,
            "desktop_shortcut_exists": shortcut_exists,
        })


@app.route('/api/translation/test', methods=['POST'])
def test_translation_connection():
    try:
        runtime_config = get_translation_runtime_config(require_ready=True)
        if runtime_config["provider"] == "notebook":
            raise ValueError("Chế độ Gemini Notebook không dùng API để kiểm tra.")
        content = call_openai_compatible_api(
            runtime_config,
            [
                {
                    "role": "user",
                    "content": "Chỉ trả lời đúng một từ: OK",
                }
            ],
            max_tokens=16,
        )
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({
        "ok": True,
        "provider": runtime_config["label"],
        "model": runtime_config["model"],
        "response": content[:100],
    })

@app.route('/api/history')
def get_history():
    if not os.path.exists(DOUZY_DB):
        return jsonify([])
    try:
        conn = sqlite3.connect(DOUZY_DB, timeout=2)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT title, download_time, file_path, aweme_id FROM aweme ORDER BY download_time DESC LIMIT 15"
            )
            rows = cursor.fetchall()
            cursor.close()
        finally:
            conn.close()

        history = []
        for r in rows:
            history.append({
                "title": r[0],
                "download_time": r[1],
                "file_path": r[2],
                "aweme_id": r[3]
            })
        return jsonify(history)
    except Exception as e:
        print(f"Error querying history: {e}")
        return jsonify([])

@app.route('/api/process', methods=['POST'])
def process():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body is required"}), 400
    try:
        queue_workflow(data.get("url"), data.get("lang", "ch"), data.get("name"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"status": "started"}), 202


@app.route('/api/cancel', methods=['POST'])
def cancel_process():
    if not request_workflow_cancel():
        return jsonify({"error": "Không có quy trình nào đang chạy."}), 409
    return jsonify({"status": "cancelling"}), 202

@app.route('/api/progress')
def get_progress():
    try:
        after_log_index = int(request.args.get("after", "0"))
    except ValueError:
        after_log_index = 0
    return jsonify(progress_snapshot(after_log_index))

@app.route('/api/open-folder', methods=['POST'])
def open_folder():
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    results = [progress_snapshot()["result"]]
    with queue_state_lock:
        results.extend(dict(job.get("result", {})) for job in workflow_jobs)
    allowed_paths = {
        value
        for result in results
        for key, value in result.items()
        if key in {"project_dir", "video", "thumbnail", "raw_srt", "translated_srt"} and value
    }
    if path not in allowed_paths:
        return jsonify({"error": "Chỉ có thể mở kết quả của quy trình hiện tại."}), 403
    if path and os.path.exists(path):
        try:
            if os.path.isdir(path):
                subprocess.Popen(["explorer.exe", path])
            else:
                subprocess.Popen(["explorer.exe", f"/select,{path}"])
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Đường dẫn kết quả không còn tồn tại."}), 400

def run_web_server():
    initialize_queue_state()
    log_to_app("Mở local web server tại http://127.0.0.1:5000...")
    try:
        app.run(host="127.0.0.1", port=5000, debug=False)
    finally:
        request_app_shutdown()


def run_browser_app():
    """Serve the UI in the user's browser when embedded WebView2 is unavailable."""
    initialize_queue_state()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    url = f"http://127.0.0.1:{port}"
    log_to_app(f"Mở giao diện trên trình duyệt tại {url}...")
    try:
        if not webbrowser.open(url, new=2):
            raise RuntimeError("Không thể mở trình duyệt mặc định.")
        # Keep the local service alive while the browser window is in use.
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        request_app_shutdown()
        server.server_close()


def native_window_watchdog(window, ready_event, failed_event, timeout=20):
    if ready_event.wait(timeout):
        return
    failed_event.set()
    log_to_app("Native WebView không tải giao diện đúng hạn; đang chuyển sang trình duyệt.", "error")
    try:
        window.destroy()
    except Exception:
        pass


class DesktopApi:
    def __init__(self, webview_module):
        # pywebview inspects public bridge attributes recursively. Keeping the
        # module and window private prevents WebView2 startup from deadlocking.
        self._webview = webview_module
        self._window = None

    def select_output_directory(self, initial_directory=""):
        directory = ""
        try:
            validated = validate_output_dir(initial_directory)
            if validated and os.path.isdir(validated):
                directory = validated
        except ValueError:
            pass
        if not directory:
            directory = get_default_output_directory()
            if not os.path.isdir(directory):
                directory = os.path.dirname(directory)
        selected = self._window.create_file_dialog(
            self._webview.FileDialog.FOLDER,
            directory=directory,
            allow_multiple=False,
        )
        return os.path.abspath(selected[0]) if selected else ""


def run_desktop_app():
    ensure_initial_desktop_shortcut()
    initialize_queue_state()
    try:
        import webview
    except ImportError as error:
        raise RuntimeError("Không tìm thấy pywebview. Hãy cài lại ứng dụng.") from error

    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        desktop_api = DesktopApi(webview)
        window = webview.create_window(
            "VietSub Studio",
            f"http://127.0.0.1:{port}",
            js_api=desktop_api,
            width=1320,
            height=880,
            min_size=(960, 680),
            background_color="#f5f1e8",
        )
        desktop_api._window = window
        native_ready = Event()
        native_failed = Event()

        def mark_native_ready(*_args):
            native_ready.set()

        window.events.loaded += mark_native_ready
        Thread(
            target=native_window_watchdog,
            args=(window, native_ready, native_failed),
            daemon=True,
        ).start()
        webview.start(
            gui="edgechromium",
            debug=False,
            icon=ICON_PATH if os.path.isfile(ICON_PATH) else None,
        )
        if native_failed.is_set():
            raise RuntimeError("Native WebView không tải được giao diện trong 20 giây.")
    finally:
        request_app_shutdown()
        server.shutdown()
        server.server_close()


def main():
    if not acquire_instance_lock():
        log_to_app("VietSub Studio đang chạy ở một tiến trình khác.", "error")
        return 2
    try:
        if "--server" in sys.argv:
            run_web_server()
        elif "--browser" in sys.argv:
            run_browser_app()
        else:
            try:
                run_desktop_app()
            except Exception as error:
                log_to_app(f"Native WebView không khởi động được, chuyển sang trình duyệt: {error}", "error")
                shutdown_event.clear()
                cancel_event.clear()
                prefetch_cancel_event.clear()
                run_browser_app()
    finally:
        release_instance_lock()
    return 0


if __name__ == '__main__':
    sys.exit(main())
