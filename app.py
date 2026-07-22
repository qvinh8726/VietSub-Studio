import os
import sys
import re
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
import webbrowser
import hashlib
import tempfile
import zipfile
from queue import Empty, Queue
from threading import Event, Lock, Thread
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
APP_HOME = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SOURCE_DIR
if getattr(sys, "frozen", False):
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
ICON_PATH = os.path.join(RESOURCE_DIR, "avatar.ico")
app = Flask(__name__, template_folder=os.path.join(RESOURCE_DIR, "templates"))

# Paths
APPDATA = os.environ.get("APPDATA")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA")

SIDECAR_BIN = os.path.join(LOCALAPPDATA, "Programs", "Douzy", "resources", "sidecar", "win32-x64", "douyin-dl-sidecar.exe")
DOUZY_CONFIG = os.path.join(APPDATA, "douyin-downloader-desktop", "config.yml")
DOUZY_DB = os.path.join(APPDATA, "douyin-downloader-desktop", "dy_downloader.db")

VIDEOCR_CLI = r"C:\Program Files\VideOCR\videocr-cli.exe"
VIDEOCR_CONFIG = os.path.join(APPDATA, "VideOCR", "videocr_gui_config.ini")

USER_DATA_DIR_DEBUG = os.path.join(LOCALAPPDATA, "Microsoft", "Edge", "User Data Debug")
CONFIG_DIR = (
    os.path.join(APPDATA, "VietSub Studio")
    if getattr(sys, "frozen", False) and APPDATA
    else APP_HOME
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "app_config.json")
QUEUE_PATH = os.path.join(CONFIG_DIR, "workflow_queue.json")
LOCAL_VIDEO_DIR = os.path.join(CONFIG_DIR, "local_videos")

SUPPORTED_OCR_LANGS = {"ch", "en", "ja", "ko", "vi"}
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
MAX_LOG_LINES = 2_000
MAX_PROJECT_NAME_LENGTH = 100
PREVIEW_TTL_SECONDS = 6 * 60 * 60
MAX_PERSISTED_JOBS = 500
ALLOWED_LOCAL_VIDEO_EXTENSIONS = {".mp4"}
ALLOWED_THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_THUMBNAIL_BYTES = 20 * 1024 * 1024
APP_VERSION = "1.4.0"
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

# Global status state
progress_status = {
    "status": "idle",  # "idle" | "running" | "success" | "error" | "cancelled"
    "step": "resolve",  # "resolve" | "download" | "ocr" | "translate" | "done"
    "logs": [],
    "log_base_index": 0,
    "error": "",
    "job_id": "",
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
update_cache_lock = Lock()
update_cache = {"checked_at": 0.0, "status": None}


class WorkflowCancelled(RuntimeError):
    pass


class EdgeLoginRequired(RuntimeError):
    pass


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
            "automatic_update_supported": is_packaged_app() and install_assets_available,
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


def update_progress(*, status=None, step=None, error=None, result=None):
    with status_lock:
        if status is not None:
            progress_status["status"] = status
        if step is not None:
            progress_status["step"] = step
        if error is not None:
            progress_status["error"] = error
        if result:
            progress_status["result"].update(result)
    if current_job_id:
        with queue_state_lock:
            for job in workflow_jobs:
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
                persist_queue_state_locked()
                break


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
            "result": dict(progress_status["result"])
        }
    snapshot["result_ready"] = (
        snapshot["status"] == "success" and project_result_ready(snapshot["result"])
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


def validate_ocr_lang(value):
    if value not in SUPPORTED_OCR_LANGS:
        raise ValueError("Ngôn ngữ OCR này chưa được hỗ trợ.")
    return value


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
    with active_process_lock:
        active_processes[name] = proc


def unregister_active_process(name, proc=None):
    with active_process_lock:
        if proc is None or active_processes.get(name) is proc:
            active_processes.pop(name, None)


def check_cancel_requested():
    if cancel_event.is_set():
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

def load_app_config():
    default_config = {
        "notebook_url": "",
        "ocr_lang": "ch",
        "output_dir": "",
        "crop_coords": None,
        "edge_background": True,
        "desktop_shortcut_initialized": False,
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return {**default_config, **json.load(f)}
        except Exception:
            pass
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
            "powershell.exe",
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
    if not is_packaged_app():
        raise RuntimeError("Tự cập nhật chỉ hỗ trợ bản VietSub Studio EXE.")

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
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
        Move-Item -LiteralPath $target -Destination $backup -Force
        try {
            Move-Item -LiteralPath $staged -Destination $target -Force
        } catch {
            Move-Item -LiteralPath $backup -Destination $target -Force
            throw
        }
        $replaced = $true
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $replaced) {
    if (Test-Path -LiteralPath $target) {
        Start-Process -FilePath $target -WorkingDirectory $targetDir
    }
    exit 1
}
try {
    Start-Process -FilePath $target -WorkingDirectory $targetDir
} catch {
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    if (Test-Path -LiteralPath $backup) {
        Move-Item -LiteralPath $backup -Destination $target -Force
        Start-Process -FilePath $target -WorkingDirectory $targetDir
    }
    exit 1
}
Start-Sleep -Seconds 2
if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
if (Test-Path -LiteralPath $updateTemp) { Remove-Item -LiteralPath $updateTemp -Recurse -Force }
"""
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    return subprocess.Popen(
        [
            "powershell.exe",
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

def log_to_app(msg, log_type="system"):
    console_message = f"[{log_type.upper()}] {msg}"
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

def check_edge_status():
    try:
        req = urllib.request.Request("http://127.0.0.1:9222/json/version")
        with urllib.request.urlopen(req, timeout=1.5) as response:
            return True
    except Exception:
        return False

def ensure_edge_running(notebook_url, background=None):
    check_cancel_requested()
    if check_edge_status():
        log_to_app("Trình duyệt Edge debug đang mở sẵn.")
        return True

    if background is None:
        background = bool(load_app_config().get("edge_background", True))

    log_to_app("Trình duyệt Edge debug chưa mở. Đang tự động kích hoạt...")
    edge_bin = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(edge_bin):
        edge_bin = "msedge.exe"

    cmd = [
        edge_bin,
        "--remote-debugging-port=9222",
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
        for _ in range(10):
            check_cancel_requested()
            time.sleep(1.0)
            if check_edge_status():
                log_to_app("Kích hoạt Edge thành công và đã kết nối cổng debug.")
                return True
        raise RuntimeError("Trình duyệt đã bật nhưng cổng 9222 không phản hồi.")
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


def show_edge_for_login(notebook_url):
    notebook_url = validate_notebook_url(notebook_url)
    if not ensure_edge_running(notebook_url, background=False):
        raise RuntimeError("Không thể mở Edge để đăng nhập.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
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
            output_queue.put(line)
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

    output_queue = Queue()
    Thread(target=queue_process_output, args=(proc.stdout, output_queue), daemon=True).start()
    recent_output = []
    deadline = time.monotonic() + 15
    port = None
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
            unregister_active_process("sidecar Douzy", proc)
            details = "\n".join(recent_output)
            raise RuntimeError(f"Sidecar dừng đột ngột (code {proc.returncode}). {details}")

    if port is None:
        stop_process(proc, "sidecar Douzy")
        unregister_active_process("sidecar Douzy", proc)
        raise TimeoutError("Sidecar Douzy khởi động quá thời gian chờ (15s).")

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

        time.sleep(2.5)

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
        if mp4_files:
            return max(mp4_files, key=os.path.getmtime)
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


def prepare_project_thumbnail(source_video, video_id, project_dir, project_name):
    source_thumbnail = find_douzy_thumbnail_file(source_video)
    destination_base = os.path.join(project_dir, project_name + ".thumbnail")
    try:
        if source_thumbnail:
            extension = os.path.splitext(source_thumbnail)[1].lower()
            destination = destination_base + (".jpg" if extension == ".jpeg" else extension)
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
        return
    try:
        conn = sqlite3.connect(DOUZY_DB, timeout=3)
        try:
            conn.execute(
                "UPDATE aweme SET file_path = ? WHERE id = "
                "(SELECT id FROM aweme WHERE aweme_id = ? ORDER BY download_time DESC LIMIT 1)",
                (destination_video, video_id),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as error:
        log_to_app(f"Không thể cập nhật đường dẫn mới trong lịch sử Douzy: {error}", "error")


def replace_source_video_references(old_path, new_path):
    old_path = os.path.abspath(old_path)
    with preview_registry_lock:
        for preview in preview_registry.values():
            if os.path.normcase(str(preview.get("source_video") or "")) == os.path.normcase(old_path):
                preview["source_video"] = new_path
    with queue_state_lock:
        for job in workflow_jobs:
            if os.path.normcase(str(job.get("source_video") or "")) == os.path.normcase(old_path):
                job["source_video"] = new_path
        try:
            persist_queue_state_locked()
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
    dedicated_source_dir = os.path.basename(os.path.normpath(source_dir)) == source_stem
    candidates = []
    for name in os.listdir(source_dir):
        candidate = os.path.join(source_dir, name)
        if os.path.normcase(candidate) == os.path.normcase(project["project_dir"]):
            continue
        candidate_stem = os.path.splitext(name)[0]
        if os.path.isfile(candidate) and (dedicated_source_dir or candidate_stem.startswith(source_stem)):
            candidates.append(candidate)

    # Move the large MP4 last so an ancillary-asset error leaves the retry source intact.
    candidates.sort(key=lambda path: os.path.normcase(path) == os.path.normcase(source_video))
    log_to_app("Đang chuyển video và asset Douzy vào thư mục dự án...")
    moved_video = ""
    for candidate in candidates:
        destination = project_asset_destination(candidate, source_video, project)
        moved_path = move_file_safely(candidate, destination)
        if os.path.normcase(candidate) == os.path.normcase(source_video):
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

def get_video_resolution(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
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

    cmd = [VIDEOCR_CLI, "--video_path", video_path]
    video_dir = os.path.dirname(video_path)
    video_name_no_ext = os.path.splitext(os.path.basename(video_path))[0]
    output_srt = output_srt or os.path.join(video_dir, video_name_no_ext + ".raw.srt")
    os.makedirs(os.path.dirname(os.path.abspath(output_srt)), exist_ok=True)
    cmd += ["--output", output_srt]
    cmd += ["--lang", ocr_lang]

    if coords and res:
        width, height = res
        crop_x = int(coords["crop_x"] * width)
        crop_y = int(coords["crop_y"] * height)
        crop_width = int(coords["crop_width"] * width)
        crop_height = int(coords["crop_height"] * height)
        log_to_app(f"Tọa độ crop: x={crop_x}, y={crop_y}, w={crop_width}, h={crop_height}")
        cmd += [
            "--crop_x", str(crop_x),
            "--crop_y", str(crop_y),
            "--crop_width", str(crop_width),
            "--crop_height", str(crop_height)
        ]
    else:
        log_to_app("Sử dụng cấu hình quét toàn màn hình mặc định.")
        cmd += ["--use_fullframe", "true"]

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

    cmd += ["--use_gpu", use_gpu]
    cmd += ["--use_server_model", use_server_model]
    cmd += ["--normalize_to_simplified_chinese", normalize_to_simplified]

    log_to_app("Khởi động tiến trình VideOCR CLI...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    register_active_process("VideOCR", proc)

    output_queue = Queue()
    Thread(target=queue_process_output, args=(proc.stdout, output_queue), daemon=True).start()
    deadline = time.monotonic() + 30 * 60
    try:
        while proc.poll() is None:
            check_cancel_requested()
            if time.monotonic() >= deadline:
                stop_process(proc, "VideOCR")
                raise TimeoutError("VideOCR quá thời gian chờ (30 phút).")
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
            raise RuntimeError(f"Lỗi tiến trình VideOCR (Mã thoát {proc.returncode})")
    except WorkflowCancelled:
        stop_process(proc, "VideOCR")
        raise
    finally:
        unregister_active_process("VideOCR", proc)

    if not os.path.exists(output_srt):
        raise FileNotFoundError(f"Không tìm thấy file phụ đề thô sau quét: {output_srt}")

    log_to_app(f"Quét phụ đề thành công: {output_srt}")
    return output_srt

def resolve_url_via_edge(short_url, notebook_url):
    check_cancel_requested()
    # Ensure Edge is running
    if not ensure_edge_running(notebook_url):
        log_to_app("Không kết nối được Edge để giải mã URL.", "error")
        return None

    log_to_app(f"Giải mã URL di động bằng Edge: {short_url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError("Không tìm thấy Context trình duyệt.")
            context = contexts[0]
            page = context.new_page()
            try:
                page.goto(short_url, wait_until="commit", timeout=15000)
                page.wait_for_timeout(2500)
                check_cancel_requested()
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


def build_translation_prompt(srt_content):
    return (
        "Dịch phụ đề SRT sau sang tiếng Việt. Chỉ trả về một khối mã SRT hoàn chỉnh; "
        "không giải thích, không tóm tắt. Giữ nguyên từng số thứ tự và từng mốc thời gian, "
        "chỉ dịch nội dung thoại.\n\n"
        "```srt\n"
        f"{srt_content}\n"
        "```"
    )


def validate_translated_srt(source_srt, translated_srt):
    source_timecodes = SRT_TIMECODE_PATTERN.findall(source_srt)
    translated_timecodes = SRT_TIMECODE_PATTERN.findall(translated_srt)
    if not source_timecodes:
        raise ValueError("SRT gốc không có mốc thời gian hợp lệ.")
    if source_timecodes != translated_timecodes:
        raise ValueError(
            "Phản hồi Gemini không phải SRT đầy đủ hoặc đã thay đổi mốc thời gian; không lưu file."
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


def translate_srt_via_gemini_edge(srt_path, notebook_url, output_srt=None):
    check_cancel_requested()
    log_to_app("Đang đọc nội dung phụ đề thô...")
    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read().strip()

    if not srt_content:
        raise ValueError("File phụ đề trống. Không có gì để dịch.")

    notebook_url = validate_notebook_url(notebook_url)
    edge_background = bool(load_app_config().get("edge_background", True))

    # Ensure Edge is running
    if not ensure_edge_running(notebook_url):
        raise RuntimeError("Trình duyệt Microsoft Edge chưa được chạy ở chế độ debug.")

    log_to_app("Kết nối Playwright vào Edge để thực hiện dịch phụ đề...")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("Không tìm thấy profile Edge đang hoạt động.")
        context = contexts[0]

        # The URL was validated above, so its final path segment is the notebook ID.
        notebook_id = urlparse(notebook_url).path.rstrip("/").rsplit("/", 1)[-1]

        gemini_page = None
        for page in context.pages:
            if notebook_id in page.url:
                gemini_page = page
                break

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
        if edge_background:
            set_edge_window_visibility(context, gemini_page, False)
        else:
            set_edge_window_visibility(context, gemini_page, True)

        response_selectors = [
            "message-content",
            ".model-response",
            ".assistant-message",
            "[data-message-author='assistant']",
            "div.markdown",
            ".chat-message"
        ]

        previous_response = find_last_response(gemini_page, response_selectors)
        previous_response_text = previous_response.inner_text() if previous_response else ""

        log_to_app("Nạp phụ đề gốc và yêu cầu dịch có ràng buộc SRT...")
        prompt_input.focus()
        prompt_input.fill(build_translation_prompt(srt_content))

        # Submit button
        submit_selectors = [
            "button.send-button",
            "button[aria-label*='Send']",
            "button[aria-label*='Gửi']",
            "button[aria-label*='send']",
            "button[aria-label*='gửi']"
        ]

        submit_btn = first_visible_locator(
            gemini_page,
            submit_selectors,
            require_enabled=True,
        )

        if not submit_btn:
            if edge_background:
                set_edge_window_visibility(context, gemini_page, True)
                raise EdgeLoginRequired(
                    "Không tìm thấy nút gửi khi Edge chạy ẩn. Edge đã được mở để kiểm tra Notebook; xong hãy bấm Chạy lại."
                )
            raise RuntimeError("Không tìm thấy nút gửi của Gemini; không thể gửi an toàn.")

        log_to_app("Gửi lệnh dịch sang NotebookLM...")
        submit_btn.click()

        log_to_app("Chờ phản hồi dịch thuật hoàn tất...")
        for _ in range(10):
            check_cancel_requested()
            time.sleep(0.5)

        start_wait = time.monotonic()
        last_length = 0
        stable_count = 0
        while True:
            if cancel_event.is_set():
                stop_gemini_generation(gemini_page)
                raise WorkflowCancelled("Đã huỷ quy trình theo yêu cầu.")
            if time.monotonic() - start_wait > 300:
                raise TimeoutError("Thời gian dịch của Gemini quá lâu (quá 5 phút).")

            current_response = find_last_response(gemini_page, response_selectors)

            if current_response:
                try:
                    text = current_response.inner_text()
                    length = len(text)
                    if is_gemini_generating(gemini_page):
                        stable_count = 0
                    elif text and text != previous_response_text and length == last_length:
                        stable_count += 1
                        if stable_count >= 5:  # 10 seconds stability
                            log_to_app("Dịch thuật hoàn tất (Nội dung chữ đã dừng cập nhật).")
                            break
                    else:
                        stable_count = 0
                        last_length = length
                except Exception:
                    pass

            time.sleep(2)

        # Extract content
        last_response = find_last_response(gemini_page, response_selectors)

        if not last_response:
            raise RuntimeError("Không tìm thấy câu trả lời của AI để trích xuất phụ đề.")

        # Check if pre blocks are found. If not, fallback to code blocks.
        code_blocks = last_response.locator("pre")
        if code_blocks.count() == 0:
            code_blocks = last_response.locator("code")

        if code_blocks.count() > 0:
            log_to_app("Tìm thấy khối mã code. Tiến hành trích xuất...")
            translated_srt = ""
            for k in range(code_blocks.count()):
                translated_srt += code_blocks.nth(k).inner_text() + "\n"
        else:
            log_to_app("Trích xuất văn bản thô từ hội thoại...")
            translated_srt = last_response.inner_text()

        translated_srt = re.sub(r'^```[a-zA-Z0-9]*\n', '', translated_srt, flags=re.MULTILINE)
        translated_srt = re.sub(r'\n```$', '', translated_srt)
        translated_srt = normalize_translated_srt(srt_content, translated_srt.strip())

        video_dir = os.path.dirname(srt_path)
        video_name_no_ext = os.path.splitext(os.path.basename(srt_path))[0]
        if video_name_no_ext.endswith(".raw"):
            video_name_no_ext = video_name_no_ext[:-4]
        output_translated_srt = output_srt or os.path.join(video_dir, video_name_no_ext + ".vi.srt")
        os.makedirs(os.path.dirname(os.path.abspath(output_translated_srt)), exist_ok=True)
        temporary_path = f"{output_translated_srt}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as f:
                f.write(translated_srt)
            os.replace(temporary_path, output_translated_srt)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

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
    if workflow_lock.locked():
        raise RuntimeError("Hãy chờ hàng đợi hiện tại chạy xong trước khi tạo preview mới.")
    if not preview_lock.acquire(blocking=False):
        raise RuntimeError("Một preview khác đang được chuẩn bị.")
    cancel_event.clear()
    try:
        config = load_app_config()
        notebook_url = validate_notebook_url(config.get("notebook_url"))
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


def create_local_video_preview(upload):
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
            if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) == 0:
                raise ValueError("File video đang trống hoặc không đọc được.")
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
        job.get("status") == "success" and project_result_ready(serialized.get("result"))
    )
    return serialized


PERSISTED_JOB_KEYS = (
    "id", "source_url", "video_id", "video_title", "source_video", "project_name",
    "ocr_lang", "crop_coords", "video_resolution", "preview_id", "use_saved_crop",
    "source_type", "managed_source", "status", "step", "error", "result",
    "needs_edge_login", "resume_step", "created_at", "started_at", "finished_at",
)


def persisted_job(job):
    return {key: job.get(key) for key in PERSISTED_JOB_KEYS}


def write_queue_file(path, jobs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = f"{path}.{uuid.uuid4().hex}.tmp"
    payload = {"version": 1, "jobs": [persisted_job(job) for job in jobs[-MAX_PERSISTED_JOBS:]]}
    try:
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def persist_queue_state_locked():
    if app.config.get("TESTING"):
        return
    write_queue_file(QUEUE_PATH, workflow_jobs)


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
        "needs_edge_login": bool(value.get("needs_edge_login", False)),
        "resume_step": value.get("resume_step") if value.get("resume_step") in {"download", "ocr", "translate"} else "download",
        "created_at": value.get("created_at") or int(time.time()),
        "started_at": value.get("started_at"),
        "finished_at": value.get("finished_at"),
    }


def load_queue_file(path):
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    values = payload.get("jobs", []) if isinstance(payload, dict) else []
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


def initialize_queue_state():
    global queue_state_initialized
    with queue_state_lock:
        if queue_state_initialized:
            return
        workflow_jobs[:] = load_queue_file(QUEUE_PATH)
        queue_state_initialized = True
        persist_queue_state_locked()


def queue_snapshot():
    with queue_state_lock:
        return [serialize_job(job) for job in workflow_jobs]


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
        "needs_edge_login": False,
        "resume_step": "download",
        "created_at": int(time.time()),
        "started_at": None,
        "finished_at": None,
    }
    with queue_state_lock:
        workflow_jobs.append(job)
        persist_queue_state_locked()
    if autostart:
        start_workflow_queue()
    return serialize_job(job)


def queue_workflow(video_url, ocr_lang, project_name=None):
    enqueue_workflow(video_url, ocr_lang, project_name, autostart=False)
    return start_workflow_queue()


def start_workflow_queue():
    global queue_worker_thread
    with queue_state_lock:
        has_queued = any(job["status"] == "queued" for job in workflow_jobs)
        if not has_queued:
            raise RuntimeError("Hàng đợi chưa có video nào sẵn sàng.")
        if queue_worker_thread and queue_worker_thread.is_alive():
            return queue_worker_thread
        queue_worker_thread = Thread(target=run_queue_worker, daemon=True)
        queue_worker_thread.start()
        return queue_worker_thread


def run_queue_worker():
    global current_job_id, queue_worker_thread
    while True:
        with queue_state_lock:
            job = next((item for item in workflow_jobs if item["status"] == "queued"), None)
            if not job:
                current_job_id = ""
                queue_worker_thread = None
                return
            job["status"] = "running"
            job["error"] = ""
            job["needs_edge_login"] = False
            job["started_at"] = int(time.time())
            current_job_id = job["id"]
            job_data = dict(job)
            persist_queue_state_locked()
        workflow_lock.acquire()
        cancel_event.clear()
        reset_progress(job_data["id"])
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
        with queue_state_lock:
            for item in workflow_jobs:
                if item["id"] == job_data["id"]:
                    item["finished_at"] = int(time.time())
                    break
            current_job_id = ""
            persist_queue_state_locked()


def remove_queued_job(job_id):
    removed = None
    with queue_state_lock:
        for index, job in enumerate(workflow_jobs):
            if job["id"] != job_id:
                continue
            if job["status"] != "queued":
                raise RuntimeError("Chỉ có thể xoá video đang chờ trong hàng đợi.")
            removed = workflow_jobs.pop(index)
            persist_queue_state_locked()
            break
    if removed:
        remove_managed_source_if_unused(removed.get("source_video"))
        return serialize_job(removed)
    raise ValueError("Không tìm thấy video trong hàng đợi.")


def clear_finished_jobs():
    removed_sources = []
    with queue_state_lock:
        removable = {"success", "error", "cancelled"}
        removed_sources = [
            job.get("source_video") for job in workflow_jobs
            if job["status"] in removable and job.get("managed_source")
        ]
        workflow_jobs[:] = [job for job in workflow_jobs if job["status"] not in removable]
        persist_queue_state_locked()
    for source_path in removed_sources:
        remove_managed_source_if_unused(source_path)


def retry_queue_job(job_id):
    with queue_state_lock:
        job = next((item for item in workflow_jobs if item["id"] == job_id), None)
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
            "needs_edge_login": False,
            "resume_step": resume_step,
            "started_at": None,
            "finished_at": None,
        })
        persist_queue_state_locked()
        return serialize_job(job)


def update_current_job_metadata(**values):
    if not current_job_id:
        return
    with queue_state_lock:
        for job in workflow_jobs:
            if job["id"] == current_job_id:
                job.update(values)
                persist_queue_state_locked()
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
        notebook_url = validate_notebook_url(config.get("notebook_url"))
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
        translated_srt = translate_srt_via_gemini_edge(
            raw_srt,
            notebook_url,
            output_srt=project["translated_srt"],
        )
        update_progress(result={"translated_srt": translated_srt})
        project["raw_srt"] = raw_srt
        project["translated_srt"] = translated_srt
        project = finalize_project_assets(project, video_id)
        update_progress(result=project)
        validate_project_result(project)
        write_project_manifest(
            project,
            video_id=video_id,
            source_url=source_url,
            status="success",
        )

        update_progress(status="success", step="done")
        log_to_app("Success! Hoàn thành toàn bộ quy trình.")
    except WorkflowCancelled as e:
        update_progress(status="cancelled", error=str(e))
        log_to_app(str(e), "error")
        if project:
            write_project_manifest(
                project,
                video_id=video_id,
                source_url=source_url,
                status="cancelled",
                error=str(e),
            )
    except EdgeLoginRequired as e:
        update_progress(status="error", error=str(e))
        update_current_job_metadata(needs_edge_login=True)
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

# Web routes
@app.route('/')
def index():
    return render_template('index.html', app_version=APP_VERSION)


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
    if workflow_lock.locked():
        return jsonify({"error": "Hãy chờ job hiện tại chạy xong rồi cập nhật app."}), 409

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
        return jsonify({"error": str(error)}), 400

    schedule_app_exit()
    return jsonify({
        "status": "installing",
        "version": update_package["version"],
        "message": "Đã xác minh bản cập nhật. App sẽ tự khởi động lại.",
    }), 202


@app.route('/api/shortcut', methods=['POST'])
def create_shortcut():
    try:
        shortcut_path = create_desktop_shortcut()
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
            "ok": bool(shutil.which("ffprobe")),
            "required": False,
            "detail": "Tuỳ chọn; giúp đổi vùng crop OCR chính xác theo độ phân giải video.",
        },
        "notebook": {
            "ok": notebook_ok,
            "required": True,
            "detail": "Dán link Gemini Notebook của bạn trong phần Thiết lập.",
        },
        "edge": {
            "ok": check_edge_status(),
            "required": False,
            "detail": "Edge chạy nền; app chỉ hiện cửa sổ khi cần đăng nhập hoặc kiểm tra Notebook.",
        },
    }
    ready = all(item["ok"] for item in checks.values() if item["required"])
    return jsonify({"ready": ready, "checks": checks})


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
        return jsonify({
            "jobs": queue_snapshot(),
            "running": workflow_lock.locked(),
            "current_job_id": current_job_id,
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

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "JSON body is required"}), 400
        config = load_app_config()
        try:
            if "notebook_url" in data:
                config["notebook_url"] = validate_notebook_url(
                    data["notebook_url"], allow_empty=True
                )
            if "ocr_lang" in data:
                config["ocr_lang"] = validate_ocr_lang(data["ocr_lang"])
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
        return jsonify({"ok": True, "config": config})
    else:
        # GET request
        config = load_app_config()
        crop = get_saved_crop_coords()
        down_dir = get_douzy_download_dir()
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
            "ocr_lang": config.get("ocr_lang"),
            "output_dir": config.get("output_dir", ""),
            "default_output_dir": get_default_output_directory(),
            "edge_background": bool(config.get("edge_background", True)),
            "desktop_shortcut_supported": shortcut_supported,
            "desktop_shortcut_exists": shortcut_exists,
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
    app.run(host="127.0.0.1", port=5000, debug=False)


class DesktopApi:
    def __init__(self, webview_module):
        self.webview = webview_module
        self.window = None

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
        selected = self.window.create_file_dialog(
            self.webview.FileDialog.FOLDER,
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
        desktop_api.window = window
        webview.start(
            gui="edgechromium",
            debug=False,
            icon=ICON_PATH if os.path.isfile(ICON_PATH) else None,
        )
    finally:
        server.shutdown()


if __name__ == '__main__':
    if "--server" in sys.argv:
        run_web_server()
    else:
        run_desktop_app()
