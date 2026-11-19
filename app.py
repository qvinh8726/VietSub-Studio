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
import http.client
import webbrowser
import hashlib
import hmac
import secrets
import tempfile
import zipfile
from collections import deque
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread, current_thread, local
from urllib.parse import parse_qs, urlparse
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from werkzeug.exceptions import HTTPException, InternalServerError
from werkzeug.serving import make_server

from vietsub import storage, publish
from vietsub import manifest as project_manifest
from vietsub.sources import browser as browser_source
from vietsub.sources import browser_capture
from vietsub.sources import cookies as cookie_utils
from vietsub.sources import douyin as douyin_source
from vietsub.sources import http_download
from vietsub.sources import local as local_source
from vietsub.sources import registry as source_registry
from vietsub.sources import sessions as session_sites
from vietsub.sources import web as web_source
from vietsub.sources import ytdlp_source
from vietsub.sources.base import DownloadCancelledError, SourceError, find_urls, url_host

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
HOME_DIR = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA") or os.path.join(HOME_DIR, "AppData", "Roaming")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(HOME_DIR, "AppData", "Local")


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
# System tools are always started by absolute path so a planted copy in the
# current or application directory (for example Downloads) is never executed.
EXPLORER_EXE = os.path.join(WINDOWS_DIRECTORY, "explorer.exe")
TASKKILL_EXE = os.path.join(WINDOWS_DIRECTORY, "System32", "taskkill.exe")
NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

SIDECAR_BIN = os.path.join(LOCALAPPDATA, "Programs", "Douzy", "resources", "sidecar", "win32-x64", "douyin-dl-sidecar.exe")
DOUZY_CONFIG = os.path.join(APPDATA, "douyin-downloader-desktop", "config.yml")
DOUZY_DB = os.path.join(APPDATA, "douyin-downloader-desktop", "dy_downloader.db")

VIDEOCR_CLI = r"C:\Program Files\VideOCR\videocr-cli.exe"
VIDEOCR_CONFIG = os.path.join(APPDATA, "VideOCR", "videocr_gui_config.ini")

# Profile of the v1.6.0 Edge automation browser; only used to recognize (and close) it.
USER_DATA_DIR_DEBUG = os.path.join(LOCALAPPDATA, "Microsoft", "Edge", "User Data Debug")
BROWSER_DEBUG_PORT = browser_source.DEBUG_PORT
BROWSER_OWNER_CACHE_SECONDS = 10 * 60
BROWSER_COLD_START_POLLS = round(browser_source.COLD_START_SECONDS / browser_source.POLL_SECONDS)
# VIETSUB_STUDIO_CONFIG_DIR lets tests and portable setups keep settings, queue,
# managed videos, logs and the instance lock in an explicit directory.
CONFIG_DIR_OVERRIDE = os.environ.get("VIETSUB_STUDIO_CONFIG_DIR", "").strip()
if CONFIG_DIR_OVERRIDE:
    CONFIG_DIR = os.path.abspath(CONFIG_DIR_OVERRIDE)
elif getattr(sys, "frozen", False):
    CONFIG_DIR = os.path.join(APPDATA, "VietSub Studio")
else:
    CONFIG_DIR = APP_HOME
CONFIG_PATH = os.path.join(CONFIG_DIR, "app_config.json")
QUEUE_PATH = os.path.join(CONFIG_DIR, "workflow_queue.json")
LOCAL_VIDEO_DIR = os.path.join(CONFIG_DIR, "local_videos")
INSTANCE_LOCK_PATH = os.path.join(CONFIG_DIR, "instance.lock")
LOG_PATH = os.path.join(CONFIG_DIR, "vietsub-studio.log")
# VIETSUB_STUDIO_DOWNLOAD_DIR moves the folder where videos wait before they are
# moved into a project; by default it sits inside the output directory.
DOWNLOAD_DIR_OVERRIDE = os.environ.get("VIETSUB_STUDIO_DOWNLOAD_DIR", "").strip()
DOWNLOAD_CACHE_DIRNAME = ".vietsub-downloads"

SUPPORTED_OCR_LANGS = {"ch", "en", "ja", "ko", "vi"}
SUPPORTED_OCR_MODEL_MODES = {"auto", "fast", "accurate"}
MAX_OCR_FRAMES_TO_SKIP = 10
VIDEOCR_CHUNK_SECONDS = 10 * 60
VIDEOCR_CHUNK_THRESHOLD_SECONDS = 45 * 60
# Minimum wall-clock limit for one VideOCR process; longer spans get more time.
VIDEOCR_PROCESS_TIMEOUT_SECONDS = 30 * 60
VIDEOCR_UNKNOWN_SPAN_TIMEOUT_SECONDS = 4 * 60 * 60
VIDEOCR_TRANSCODE_TIMEOUT_SECONDS = 15 * 60
PROCESS_FAILURE_TAIL_LINES = 20
# A Douzy download only times out when its job payload stops changing.
DOUZY_STALL_TIMEOUT_SECONDS = 5 * 60
DOUZY_MAX_DOWNLOAD_SECONDS = 6 * 60 * 60
DOUZY_VOLATILE_FIELD_WORDS = {
    "time", "timestamp", "ts", "at", "updated", "heartbeat", "elapsed",
    "eta", "remaining", "now", "date", "uptime", "clock",
}
TRANSLATION_CHUNK_THRESHOLD_SECONDS = 45 * 60
TRANSLATION_CHUNK_TARGET_SECONDS = 30 * 60
TRANSLATION_CHUNK_MAX_BLOCKS = 400
TRANSLATION_CHUNK_MAX_CHARS = 24_000
# Socket timeout for each read; the whole request also has an overall deadline.
TRANSLATION_API_TIMEOUT_SECONDS = 180
TRANSLATION_API_REQUEST_DEADLINE_SECONDS = 30 * 60
TRANSLATION_API_MAX_ATTEMPTS = 3
TRANSLATION_API_RETRY_DELAYS = (2, 4, 8)
TRANSLATION_API_MAX_RETRY_AFTER_SECONDS = 120
TRANSLATION_API_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TRANSLATION_INSTRUCTIONS_CHARS = 16_000
TRANSLATION_PROMPT_VERSION = "vi-v5-source-language"
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
# Machine-readable reasons for a terminal job error, so retry logic and the UI do
# not depend on the wording of Vietnamese messages.
JOB_ERROR_CODES = {"project_missing", "translation_invalid"}
MAX_PERSISTED_JOBS = 500
MAX_IN_MEMORY_JOBS = MAX_PERSISTED_JOBS
ALLOWED_LOCAL_VIDEO_EXTENSIONS = {".mp4"}
# Files picked in the native dialog are used in place, so MKV works there too.
LOCAL_VIDEO_FILE_TYPES = ("Video (*.mp4;*.mkv)", "Tất cả file (*.*)")
COOKIE_FILE_TYPES = ("Cookies (*.txt)", "Tất cả file (*.*)")
BROWSER_FILE_TYPES = ("Trình duyệt (*.exe)",)
DOWNLOAD_SOURCE_TYPES = source_registry.DOWNLOADABLE_SOURCE_TYPES
WEB_SOURCE_TYPES = frozenset(web_source.WEB_SITES)
SOURCE_LABELS = source_registry.SITE_LABELS
DOUYIN_HOME_URL = "https://www.douyin.com/"
# Platforms whose login session lives in the automation browser's profile (Settings shows them).
SESSION_SITES = session_sites.SESSION_SITES
# The browser's cookies are handed to yt-dlp for these sites only. YouTube is left out on
# purpose: yt-dlp warns that using account cookies can get a Google account flagged,
# and the debug profile is usually signed in to the user's Gemini account.
SOURCE_COOKIE_URLS = {
    "tiktok": ("https://www.tiktok.com/",),
    **{key: site.cookie_urls for key, site in SESSION_SITES.items()},
}
# How each non-Douyin site is downloaded, in order of preference.
WEB_DOWNLOAD_STRATEGY = {
    "tiktok": ("ytdlp",),
    "youtube": ("ytdlp",),
    "bilibili": ("ytdlp",),
    "xiaohongshu": ("ytdlp", "browser"),
    "kuaishou": ("browser",),  # yt-dlp has no Kuaishou extractor
}
# Sources whose main download method opens a page in the automation browser.
BROWSER_SOURCE_TYPES = frozenset({"douyin", "kuaishou"})
YTDLP_SOURCE_TYPES = frozenset(
    site for site, tiers in WEB_DOWNLOAD_STRATEGY.items() if tiers[0] == "ytdlp"
)
NO_VIDEO_PATTERN = re.compile(r"No video formats found", re.IGNORECASE)
DOWNLOAD_FOLDER_PATTERN = re.compile(
    r"(?:douyin|tiktok|youtube|bilibili|xiaohongshu|kuaishou)-[A-Za-z0-9_-]{1,64}"
)
JS_RUNTIME_CACHE_SECONDS = 10 * 60
ALLOWED_THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_THUMBNAIL_BYTES = 20 * 1024 * 1024
MAX_LOCAL_VIDEO_BYTES = 8 * 1024 * 1024 * 1024
APP_VERSION = "2.0.0"
GITHUB_REPOSITORY = "qvinh8726/VietSub-Studio"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
LATEST_RELEASE_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
UPDATE_CACHE_TTL_SECONDS = 15 * 60
UPDATE_DOWNLOAD_MAX_BYTES = 512 * 1024 * 1024
# Every portable build since v1.3.0 extracts exactly this member from the release ZIP.
UPDATE_ZIP_EXE_MEMBER = "VietSub Studio/VietSub Studio.exe"
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
# Only a process that loaded the saved queue may rewrite it. The CLI never loads
# the desktop queue, so its empty in-memory list must never reach the file.
queue_persistence_enabled = False
queue_revision = 0
update_cache_lock = Lock()
update_install_lock = Lock()
operation_admission_lock = Lock()
shutdown_event = Event()
update_cache = {"checked_at": 0.0, "status": None}
browser_status_lock = Lock()
browser_status_cache = {}
# One automation browser launch at a time, so two requests never open two windows or tabs.
browser_launch_lock = Lock()
config_write_lock = Lock()
log_file_lock = Lock()
# Per-thread workflow context: the prefetch thread runs download helpers with its own
# cancel event, without touching the visible job console or the cancellable process list.
workflow_context = local()
prefetch_thread = None
prefetch_cancel_event = Event()
prefetch_job_id = ""
instance_lock_handle = None
# Live progress of the visible download (preview or job); background prefetch never writes it.
download_progress_lock = Lock()
download_progress = {}
js_runtime_cache = {"checked_at": 0.0, "value": None}


class WorkflowCancelled(RuntimeError):
    pass


class VideoDownloadError(RuntimeError):
    """Every download method failed; the message lists each reason."""


class EdgeLoginRequired(RuntimeError):
    """Gemini Notebook needs the user to sign in to Google in the automation browser."""


class BrowserStartError(RuntimeError):
    """The automation browser could not be started or reached; the message says why."""


class SourceLoginRequired(VideoDownloadError):
    """The platform only shows this video to signed-in users."""


class PostHasNoVideo(VideoDownloadError):
    """The post is a photo set; no other download method can find a video."""


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


def idle_download_progress():
    return {
        "active": False,
        "source": "",
        "phase": "",
        "downloaded": 0,
        "total": None,
        "percent": None,
        "speed": None,
        "connections": None,
        "notice": "",
        "logged_bucket": -1,
    }


def download_progress_is_silent():
    # The background prefetch must not overwrite what the visible job or preview shows.
    return bool(getattr(workflow_context, "quiet", False))


def reset_download_progress(source=""):
    if download_progress_is_silent():
        return
    with download_progress_lock:
        download_progress.clear()
        download_progress.update(idle_download_progress())
        download_progress.update({"active": True, "source": source, "phase": "start"})


def clear_download_progress():
    if download_progress_is_silent():
        return
    with download_progress_lock:
        download_progress.clear()
        download_progress.update(idle_download_progress())


def set_download_progress(**values):
    if download_progress_is_silent():
        return
    with download_progress_lock:
        download_progress.update(values)


def format_megabytes(value):
    return f"{(value or 0) / (1024 * 1024):,.1f} MB"


def report_download_progress(downloaded, total=None, speed=None):
    """Progress callback for every downloader: updates /api/progress and logs each 10%."""
    if download_progress_is_silent():
        return
    downloaded = max(0, int(downloaded or 0))
    total = int(total) if total else None
    percent = min(100, int(downloaded * 100 / total)) if total else None
    with download_progress_lock:
        download_progress.update({
            "active": True,
            "phase": "download",
            "downloaded": downloaded,
            "total": total,
            "percent": percent,
            "speed": float(speed) if speed else None,
        })
        bucket = percent // 10 if percent is not None else None
        should_log = bucket is not None and bucket > download_progress.get("logged_bucket", -1)
        if should_log:
            download_progress["logged_bucket"] = bucket
    if should_log:
        speed_text = f", {format_megabytes(speed)}/s" if speed else ""
        log_to_app(
            f"Tải video: {percent}% ({format_megabytes(downloaded)}/{format_megabytes(total)}{speed_text})",
            durable=False,
        )


def bound_download_callbacks():
    """(on_progress, on_notice) for stream_download, bound to the calling thread's context.

    stream_download calls them from its own download threads, where the thread-local
    workflow_context is empty; without this the background prefetch would overwrite the
    progress and console of the visible job.
    """
    quiet = download_progress_is_silent()

    def on_progress(downloaded, total=None, speed=None):
        if not quiet:
            report_download_progress(downloaded, total, speed)

    def on_notice(message):
        log_to_app(message, quiet=quiet)

    return on_progress, on_notice


def bound_download_connections_callback():
    """Keep worker-thread connection reports tied to the originating foreground job."""
    quiet = download_progress_is_silent()

    def on_connections(count):
        if not quiet:
            with download_progress_lock:
                download_progress["connections"] = count if type(count) is int and count > 0 else None

    return on_connections


def log_download_summary(result, started, label="video"):
    """One line with the size, time and average speed of a finished stream_download."""
    if not isinstance(result, dict) or not result.get("bytes"):
        return
    elapsed = max(0.001, time.monotonic() - started)
    connections = int(result.get("connections") or 1)
    how = f"{connections} kết nối song song" if connections > 1 else "1 kết nối"
    log_to_app(
        f"Đã tải {format_megabytes(result['bytes'])} {label} trong {elapsed:,.1f} giây "
        f"({format_megabytes(result['bytes'] / elapsed)}/s, {how})."
    )


def download_progress_snapshot():
    with download_progress_lock:
        snapshot = dict(download_progress) or idle_download_progress()
    snapshot.pop("logged_bucket", None)
    return snapshot


download_progress.update(idle_download_progress())


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
    snapshot["download"] = download_progress_snapshot()
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


def detect_video_source(text):
    """SourceRequest for the first supported video link in pasted text; ValueError otherwise."""
    return source_registry.detect_source(text)


def source_type_for_url(url, default="douyin"):
    try:
        return detect_video_source(url).site
    except ValueError:
        return default


def validate_douyin_url(value):
    if not isinstance(value, str):
        raise ValueError("Link Douyin không hợp lệ.")
    # ASCII-only URL matching: Chinese text glued to a share link is not part of it.
    candidates = find_urls(value.strip()) or [value.strip()]
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
    """Stop a child process and, on Windows, every process it started.

    Popen.terminate() and kill() only end the direct child on Windows, so
    VideOCR, FFmpeg or Douzy helpers could keep running and hold the GPU or files.
    A child that ignores the request is logged instead of raising, so the
    original error (cancel, timeout) is not replaced.
    """
    if not proc or proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                [TASKKILL_EXE, "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=NO_WINDOW_FLAGS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        log_to_app(f"Tiến trình {name} chưa thoát sau khi đã yêu cầu dừng.", "error")
        return
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


def cancel_requested():
    event = getattr(workflow_context, "cancel_event", None) or cancel_event
    return event.is_set() or shutdown_event.is_set()


def check_cancel_requested():
    if shutdown_event.is_set():
        raise WorkflowCancelled("App đang đóng nên job đã dừng; mở lại app rồi bấm Chạy lại.")
    if cancel_requested():
        raise WorkflowCancelled("Đã huỷ quy trình theo yêu cầu.")


def queue_worker_active():
    worker = queue_worker_thread
    return bool(worker and worker.is_alive())


def prefetch_active():
    thread = prefetch_thread
    return bool(thread and thread.is_alive())


def request_workflow_cancel():
    # current_job_id is read without queue_state_lock: this runs from HTTP threads
    # while the worker may hold that lock, and the read of a str is atomic.
    job_starting = bool(current_job_id) and queue_worker_active()
    # A preview download holds preview_lock; its downloader checks cancel_event too.
    preview_downloading = preview_lock.locked()
    if not workflow_lock.locked() and not job_starting and not preview_downloading:
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
    adapter = globals().get("editor_api")
    if adapter:
        adapter.jobs.shutdown()
    upload = globals().get("uploader_api")
    if upload:
        upload.shutdown()
    with active_process_lock:
        processes = list(active_processes.items())
    for name, proc in processes:
        try:
            stop_process(proc, name)
        except Exception:
            pass
    current = current_thread()
    thread = prefetch_thread
    if thread and thread.is_alive() and thread is not current:
        thread.join(timeout=5)
    # The worker checks shutdown_event before claiming another job; wait for it so a
    # job that starts during shutdown cannot leave VideOCR or Douzy running.
    worker = queue_worker_thread
    if worker and worker.is_alive() and worker is not current:
        worker.join(timeout=15)
    # Ownership/PID/profile/CDP checks stay in the browser cleanup helper. CDP
    # teardown can stall, so a closing GUI waits only a bounded extra interval.
    def cleanup_browser():
        if not shutdown_automation_browser():
            log_to_app("Trình duyệt của app còn bận; bỏ qua đóng lúc thoát, không tác động trình duyệt cá nhân.", "error")

    cleanup = Thread(target=cleanup_browser, name="browser-shutdown", daemon=True)
    cleanup.start()
    cleanup.join(timeout=2)
    if cleanup.is_alive():
        log_to_app("Đóng trình duyệt của app chưa phản hồi; không tiếp tục chờ khi thoát.", "error")


def protect_config_secret(secret):
    secret = validate_translation_api_key(secret, allow_empty=True)
    return protect_text_secret(secret)


def protect_text_secret(secret):
    """Encrypt any secret text (API key, Douyin cookie) for the current Windows user."""
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


def url_origin(url):
    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def translation_api_key_slot(provider, base_url):
    return f"{provider}|{url_origin(base_url)}"


def configured_translation_api_slot(config):
    """Key slot for the provider and base URL origin currently in the settings."""
    provider = validate_translation_provider(config.get("translation_provider"))
    if provider == "notebook":
        return ""
    base_url = validate_translation_api_base_url(config.get("translation_api_base_url", ""), provider)
    return translation_api_key_slot(provider, base_url)


def legacy_translation_api_slot(config):
    """Slot for a key saved before v1.5.4, when one key served every provider."""
    try:
        if validate_translation_provider(config.get("translation_provider")) != "notebook":
            return configured_translation_api_slot(config)
    except ValueError:
        return ""
    base_url = str(config.get("translation_api_base_url") or "").strip()
    origin = url_origin(base_url)
    if not origin:
        return ""
    for provider, preset in TRANSLATION_PROVIDER_PRESETS.items():
        if preset["base_url"] and url_origin(preset["base_url"]) == origin:
            return translation_api_key_slot(provider, base_url)
    return translation_api_key_slot("custom", base_url)


def get_translation_api_key(config):
    """Return the key saved for the configured provider and base URL origin only.

    A key never follows a switch to another provider or host, so it cannot be sent
    to a gateway it was not created for.
    """
    slot = configured_translation_api_slot(config)
    if not slot:
        return ""
    keys = config.get("translation_api_keys_protected")
    if isinstance(keys, dict) and keys.get(slot):
        return validate_translation_api_key(unprotect_config_secret(keys[slot]), allow_empty=True)
    if (config.get("translation_api_key_protected") or config.get("translation_api_key")) and (
        slot == legacy_translation_api_slot(config)
    ):
        protected = config.get("translation_api_key_protected", "")
        if protected:
            return validate_translation_api_key(unprotect_config_secret(protected), allow_empty=True)
        # Development configs created before DPAPI storage kept the key in plain text.
        return validate_translation_api_key(config.get("translation_api_key", ""), allow_empty=True)
    return ""


def migrate_legacy_translation_api_key(config):
    """Bind a pre-v1.5.4 single key to the provider it was saved for, then drop it."""
    legacy_protected = config.get("translation_api_key_protected", "") or ""
    legacy_plain = config.get("translation_api_key", "") or ""
    if not legacy_protected and not legacy_plain:
        return
    try:
        slot = legacy_translation_api_slot(config)
    except ValueError:
        slot = ""
    if not slot:
        # Keep the old key untouched until it can be tied to a provider.
        return
    config.pop("translation_api_key_protected", None)
    config.pop("translation_api_key", None)
    keys = config.get("translation_api_keys_protected")
    keys = dict(keys) if isinstance(keys, dict) else {}
    if slot not in keys:
        keys[slot] = legacy_protected or protect_config_secret(legacy_plain)
    config["translation_api_keys_protected"] = keys


def read_text_file(path):
    # utf-8-sig accepts files saved with a UTF-8 BOM by Notepad and other editors.
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def read_text_file_with_retry(path, attempts=3, delay_seconds=0.25):
    """Read a state file, retrying short locks held by antivirus, backup or OneDrive."""
    for attempt in range(1, attempts + 1):
        try:
            return read_text_file(path)
        except FileNotFoundError:
            raise
        except OSError:
            if attempt >= attempts:
                raise
            time.sleep(delay_seconds * attempt)
    raise OSError(f"Không đọc được file: {path}")


def validate_workbench_layout(value):
    """Validate the versioned UI layout; null clears the saved layout preference."""
    if value is None:
        return None
    fields = {"version", "sidebar_ratio", "console_ratio", "console_collapsed"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Bố cục workbench không hợp lệ.")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Phiên bản bố cục workbench không được hỗ trợ.")
    for field in ("sidebar_ratio", "console_ratio"):
        ratio = value[field]
        if type(ratio) not in (int, float) or not 0 < ratio < 1 or not math.isfinite(ratio):
            raise ValueError("Tỷ lệ khung workbench phải là số hữu hạn lớn hơn 0 và nhỏ hơn 1.")
    if type(value["console_collapsed"]) is not bool:
        raise ValueError("Trạng thái thu gọn console không hợp lệ.")
    return dict(value)


def workbench_settings_fields(config):
    """Expose safe preferences without rewriting malformed or older saved settings."""
    enabled = config.get("sakuga_vfx_enabled", True)
    try:
        layout = validate_workbench_layout(config.get("workbench_layout"))
    except ValueError:
        layout = None
    return {
        "sakuga_vfx_enabled": enabled if type(enabled) is bool else True,
        "workbench_layout": layout,
    }


def load_app_config(strict=False):
    """Return the saved settings merged over defaults.

    A file that exists but cannot be read right now is never treated as corrupt.
    With strict=True that case raises OSError, so read-modify-write callers never
    save defaults over the real settings. Only invalid JSON is quarantined.
    """
    default_config = {
        "notebook_url": "",
        "translation_provider": "notebook",
        "translation_api_base_url": "",
        "translation_api_model": "",
        "translation_api_keys_protected": {},
        "translation_instructions": "",
        "ocr_lang": "ch",
        "ocr_model_mode": "auto",
        "ocr_frames_to_skip": None,
        "output_dir": "",
        "crop_coords": None,
        "edge_background": True,
        "sakuga_vfx_enabled": True,
        "workbench_layout": None,
        "browser_kind": browser_source.DEFAULT_KIND,
        "browser_path": "",
        "desktop_shortcut_initialized": False,
        "source_cookies_protected": {},
        "cookies_file": "",
    }
    if not os.path.exists(CONFIG_PATH):
        return default_config
    try:
        text = read_text_file_with_retry(CONFIG_PATH)
    except FileNotFoundError:
        return default_config
    except OSError as error:
        if strict:
            raise
        log_to_app(f"Chưa đọc được file cấu hình, tạm dùng giá trị mặc định: {error}", "error")
        return default_config
    try:
        saved_config = json.loads(text)
        if not isinstance(saved_config, dict):
            raise ValueError("Cấu hình phải là một JSON object.")
    except ValueError:
        # json.JSONDecodeError and UnicodeDecodeError are both ValueError subclasses.
        quarantine_corrupt_file(CONFIG_PATH)
        return default_config
    merged_config = {**default_config, **saved_config}
    # Migrate legacy aliases to the current quality-first DeepSeek model.
    if (
        merged_config.get("translation_provider") == "deepseek"
        and str(merged_config.get("translation_api_model") or "").strip().lower()
        in {"deepseek-chat", "deepseek-v4-flash"}
    ):
        merged_config["translation_api_model"] = "deepseek-v4-pro"
    return merged_config

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
    try:
        config = load_app_config(strict=True)
    except OSError as error:
        log_to_app(f"Chưa đọc được cấu hình nên tạm bỏ qua shortcut ngoài Desktop: {error}", "error")
        return ""
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


def find_update_executable_member(archive):
    members = {
        item.filename.replace("\\", "/"): item
        for item in archive.infolist()
        if not item.is_dir()
    }
    member = members.get(UPDATE_ZIP_EXE_MEMBER)
    if member is None or member.file_size <= 0:
        raise RuntimeError("ZIP cập nhật không chứa đúng file VietSub Studio.exe.")
    if member.file_size > UPDATE_DOWNLOAD_MAX_BYTES:
        raise RuntimeError("File EXE cập nhật vượt quá kích thước cho phép.")
    return member


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
        try:
            with zipfile.ZipFile(zip_path) as archive:
                member = find_update_executable_member(archive)
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
    # PyInstaller >= 6.9 treats a process started from the same one-file executable
    # as a worker that reuses the parent's unpacked files. The updated app must
    # outlive this process, so it needs a fresh bootloader environment.
    helper_env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("_PYI_") and key.upper() != "_MEIPASS2"
    }
    helper_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
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
            # One entry per line: text from APIs or tools cannot fake extra log entries.
            safe_message = str(message).replace("\r", "\\r").replace("\n", "\\n")
            with open(LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} {safe_message}\n")
    except OSError:
        pass


def log_to_app(msg, log_type="system", *, durable=True, quiet=None):
    """Show a line in the app console; durable=False keeps it out of the log file.

    quiet=None takes the flag of this thread's workflow context (True for the prefetch).
    """
    quiet = getattr(workflow_context, "quiet", False) if quiet is None else bool(quiet)
    prefix = "[PREFETCH] " if quiet else ""
    console_message = f"{prefix}[{log_type.upper()}] {msg}"
    if durable:
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

from vietsub.sources.browser_lifecycle import BrowserLifecycle, HeadedBrowserRequired

# Browser work is serialized, including manual verification. The timer cannot
# close or restart a browser while a page, cookie export or translation owns it.
browser_lifecycle = BrowserLifecycle(lambda instance: close_owned_browser(instance))
browser_session_cookies = {}
browser_operation_state = local()


def browser_automation(function):
    """Retry visibly once, then restore headless at the outer successful boundary.

    Nested decorators share the override and let the outermost call handle the
    challenge: no browser restart can happen while an outer Playwright scope is
    still live. A failed/cancelled/login-needed retry stays visible.
    """
    from functools import wraps

    @wraps(function)
    def run(*args, **kwargs):
        with browser_lifecycle.lease():
            if getattr(browser_operation_state, "operation", None) is not None:
                return function(*args, **kwargs)
            browser_lifecycle.clear_manual_login()
            previous = getattr(browser_operation_state, "headless", None)
            operation = {"explicit_headed": previous is False, "opening_challenge": False}
            browser_operation_state.operation = operation
            try:
                try:
                    return function(*args, **kwargs)
                except HeadedBrowserRequired as challenge:
                    check_cancel_requested()
                    log_to_app("Trang yêu cầu xác minh; mở lại cùng profile để bạn thao tác trong trình duyệt.")
                    browser_operation_state.headless = False
                    operation["opening_challenge"] = True
                    try:
                        ensure_browser_running(background=False, headless=False, start_url=challenge.url)
                    finally:
                        operation["opening_challenge"] = False
                    result = function(*args, **kwargs)
                    # Function return unwound every page/context first. Do not hide
                    # a login failure, a timeout/empty result, or a pending cancel.
                    if result and not cancel_requested() and not operation["explicit_headed"]:
                        browser_operation_state.headless = previous
                        try:
                            if bool(load_app_config().get("edge_background", True)) and browser_verification_finished():
                                ensure_browser_running(headless=True)
                        except Exception as error:
                            # The captured/downloaded result remains valid even if
                            # Chrome cannot restart; report it rather than retry it.
                            log_to_app(f"Đã hoàn tất thao tác nhưng chưa trở lại headless: {error}", "error")
                    return result
            finally:
                browser_operation_state.headless = previous
                browser_operation_state.operation = None
    return run


def browser_verification_finished():
    """Fail closed before hiding a successful retry that may still show login UI.

    Some compatibility helpers return a URL even on a challenge timeout. A
    truthy return alone therefore cannot prove that verification was completed.
    This read-only check has its own short-lived Playwright scope, fully unwound
    before the caller restarts the same persistent profile.
    """
    instance = browser_lifecycle.instance or {}
    if instance.get("headless") or not instance.get("session"):
        return False
    with get_sync_playwright()() as playwright:
        browser = playwright.chromium.connect_over_cdp(instance["session"])
        if not browser.contexts or not browser.contexts[0].pages:
            return False
        for page in browser.contexts[0].pages:
            if page.url in BLANK_PAGE_URLS:
                continue
            probe = page.evaluate(browser_capture.ATTENTION_PROBE_SCRIPT)
            douyin_probe = page.evaluate(douyin_source.CAPTCHA_PROBE_SCRIPT)
            host = (urlparse(page.url).hostname or "").lower()
            google_login = (host == "gemini.google.com" or host == "accounts.google.com"
                            or host.endswith(".accounts.google.com")) and edge_page_needs_login(page)
            if (not isinstance(probe, dict) or not isinstance(douyin_probe, dict)
                    or browser_capture.is_attention_probe(probe)
                    or douyin_source.is_captcha_probe(douyin_probe)
                    or douyin_source.is_login_probe(douyin_probe)
                    or google_login):
                return False
    return True


def require_visible_browser(context, page):
    """Unwind headless page work before a same-profile restart for manual input."""
    if (browser_lifecycle.instance or {}).get("headless"):
        raise HeadedBrowserRequired(page.url)
    # Captures release their tab in finally, including login/error/timeouts.
    # Keep the visible verification tab until the successful outer boundary.
    try:
        page._vietsub_manual_verification = True
    except (AttributeError, TypeError):
        pass  # Compatibility test doubles may not expose Playwright attributes.
    if not set_browser_window_visibility(context, page, True):
        try:
            page.bring_to_front()
        except Exception:
            pass


def shutdown_automation_browser():
    """CLI/shutdown cleanup of the verified automation browser only."""
    try:
        return browser_lifecycle.shutdown()
    except Exception:
        return False


def get_browser_debug_port():
    return BROWSER_DEBUG_PORT


def browser_debug_url(path=""):
    return f"http://127.0.0.1:{get_browser_debug_port()}{path}"


def get_browser_kind(config=None):
    config = load_app_config() if config is None else config
    try:
        return browser_source.validate_kind(config.get("browser_kind"))
    except ValueError:
        return browser_source.DEFAULT_KIND


def browser_label(kind=None, *, short=False):
    labels = browser_source.SHORT_LABELS if short else browser_source.LABELS
    return labels[kind or get_browser_kind()]


def browser_profile_dir(kind=None):
    """Profile folder of the automation browser: one per browser kind, inside CONFIG_DIR."""
    return browser_source.profile_dir(CONFIG_DIR, kind or get_browser_kind())


def resolve_chrome_executable():
    # PATH is searched last and only in absolute directories, never the current folder.
    return browser_source.find_chrome(path_lookup=resolve_path_executable)


def resolve_browser_executable(config=None):
    """Executable of the selected automation browser, or "" when it is not installed."""
    config = load_app_config() if config is None else config
    kind = get_browser_kind(config)
    if kind == "edge":
        return resolve_edge_executable()
    if kind == "custom":
        try:
            return browser_source.validate_custom_executable(config.get("browser_path") or "")
        except ValueError:
            return ""
    return resolve_chrome_executable()


def missing_browser_message(kind):
    if kind == "custom":
        return "Chưa chọn file .exe hợp lệ cho trình duyệt tuỳ chọn trong Thiết lập."
    other = "Microsoft Edge" if kind == "chrome" else "Google Chrome"
    return (
        f"Không tìm thấy {browser_label(kind)} trên máy. Hãy cài {browser_label(kind)} hoặc chọn {other} "
        "hay một file .exe khác ở mục Trình duyệt tự động hoá trong Thiết lập."
    )


def is_valid_browser_debug_payload(payload, kind=None):
    return browser_source.is_valid_debug_payload(payload, kind, get_browser_debug_port())


# The debug endpoint is on this machine: never send it through a system or VPN proxy.
LOCAL_URL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_debug_payload():
    """The /json/version answer on the debug port, or None when nothing answers."""
    if not debug_port_listening():
        return None
    try:
        with LOCAL_URL_OPENER.open(browser_debug_url("/json/version"), timeout=1.5) as response:
            payload = json.load(response)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


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


def command_line_profile(command_line):
    """The single --user-data-dir of a browser command line, or "" (none, empty or several)."""
    values = []
    arguments = split_windows_command_line(command_line)
    index = 1
    while index < len(arguments):
        lowered = arguments[index].lower()
        if lowered == "--user-data-dir":
            if index + 1 >= len(arguments):
                return ""
            values.append(arguments[index + 1])
            index += 2
            continue
        if lowered.startswith("--user-data-dir="):
            values.append(arguments[index].split("=", 1)[1])
        index += 1
    if len(values) != 1 or not values[0]:
        return ""
    return os.path.abspath(os.path.normpath(values[0]))


def same_path(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def browser_command_line_uses_profile(command_line, expected_profile):
    return same_path(command_line_profile(command_line), expected_profile)


def browser_owner_matches(owner, executable, profile):
    """True when the process owning the debug port is this browser with this profile."""
    if not isinstance(owner, dict) or not executable:
        return False
    return (
        str(owner.get("name") or "").lower() == os.path.basename(executable).lower()
        and same_path(str(owner.get("path") or ""), executable)
        and browser_command_line_uses_profile(owner.get("command_line"), profile)
    )


def native_listening_pid(port):
    """PID listening on 127.0.0.1:port (or 0.0.0.0:port) from the Windows TCP table, else None."""
    import ctypes
    from ctypes import wintypes

    class TcpRow(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in (
            "state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]

    iphlpapi = ctypes.WinDLL("iphlpapi")
    for _ in range(3):
        size = wintypes.DWORD(0)
        # AF_INET (2) and TCP_TABLE_OWNER_PID_LISTENER (3).
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
        table = ctypes.create_string_buffer(max(size.value, 4) + 1024)
        size.value = len(table)
        result = iphlpapi.GetExtendedTcpTable(table, ctypes.byref(size), False, 2, 3, 0)
        if result == 0:
            break
        # ERROR_INSUFFICIENT_BUFFER (122): the table grew between the two calls.
        if result != 122:
            raise OSError(f"GetExtendedTcpTable failed ({result})")
    else:
        raise OSError("GetExtendedTcpTable failed")
    count = wintypes.DWORD.from_buffer(table).value
    rows = ctypes.cast(ctypes.addressof(table) + ctypes.sizeof(wintypes.DWORD), ctypes.POINTER(TcpRow))
    return next(
        (
            rows[index].pid for index in range(count)
            if ((rows[index].local_port & 0xFF) << 8 | (rows[index].local_port >> 8) & 0xFF) == port
            and rows[index].local_addr in (0x0100007F, 0)
        ),
        None,
    )


def debug_port_listening():
    """False only when the Windows TCP table shows no listener on the debug port.

    A refused connection to 127.0.0.1 takes about 2 s on Windows (it retries the SYN), so
    status checks and cold-start polling read the TCP table first (about 1 ms).
    """
    if os.name != "nt":
        return True
    try:
        return native_listening_pid(get_browser_debug_port()) is not None
    except Exception:
        return True


def native_debug_port_owner(port):
    """Process that listens on 127.0.0.1:port, read with the Windows API (about 2 ms)."""
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wintypes.USHORT), ("maximum", wintypes.USHORT), ("buffer", ctypes.c_void_p)]

    kernel32 = ctypes.WinDLL("kernel32")
    ntdll = ctypes.WinDLL("ntdll")
    pid = native_listening_pid(port)
    if pid is None:
        return None
    # PROCESS_QUERY_LIMITED_INFORMATION is enough for a process of the same user.
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return {"pid": pid, "name": "", "path": "", "command_line": ""}
    try:
        path_buffer = ctypes.create_unicode_buffer(32768)
        path_length = wintypes.DWORD(len(path_buffer))
        kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(path_length))
        needed = wintypes.ULONG(0)
        # ProcessCommandLineInformation (60), Windows 8.1 and later.
        ntdll.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(needed))
        command_line = ""
        if needed.value:
            buffer = ctypes.create_string_buffer(needed.value)
            if ntdll.NtQueryInformationProcess(handle, 60, buffer, needed, ctypes.byref(needed)) == 0:
                text = UnicodeString.from_buffer(buffer)
                command_line = ctypes.wstring_at(text.buffer, text.length // 2) if text.buffer else ""
    finally:
        kernel32.CloseHandle(handle)
    path = path_buffer.value
    return {"pid": pid, "name": os.path.basename(path), "path": path, "command_line": command_line}


def powershell_debug_port_owner(port):
    script = (
        f"$connection = Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 "
        f"-LocalPort {int(port)} -ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if (-not $connection) { exit 1 }; "
        "$process = Get-CimInstance Win32_Process -Filter "
        "(\"ProcessId=\" + $connection.OwningProcess) -ErrorAction SilentlyContinue; "
        "if (-not $process.CommandLine) { exit 1 }; "
        "[PSCustomObject]@{ ProcessId = $process.ProcessId; Name = $process.Name; "
        "ExecutablePath = $process.ExecutablePath; CommandLine = $process.CommandLine } | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [POWERSHELL_EXE, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace", timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        metadata = json.loads(completed.stdout or "")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if completed.returncode != 0 or not isinstance(metadata, dict):
        return None
    return {
        "pid": int(metadata.get("ProcessId") or 0),
        "name": str(metadata.get("Name") or ""),
        "path": str(metadata.get("ExecutablePath") or ""),
        "command_line": str(metadata.get("CommandLine") or ""),
    }


def get_debug_port_owner():
    """{"pid", "name", "path", "command_line"} of the debug port's owner, or None."""
    if os.name != "nt":
        return None
    port = get_browser_debug_port()
    try:
        return native_debug_port_owner(port)
    except Exception:
        # PowerShell and WMI are slower (about 1 s) but work where the Windows API call fails.
        return powershell_debug_port_owner(port)


def browser_debug_owner_uses_profile(config=None):
    config = load_app_config() if config is None else config
    kind = get_browser_kind(config)
    return browser_owner_matches(
        get_debug_port_owner(), resolve_browser_executable(config), browser_profile_dir(kind)
    )


def reset_browser_status_cache():
    with browser_status_lock:
        browser_status_cache.clear()


def check_browser_status(fresh=False):
    """True when the selected browser, started with the app's own profile, owns the debug port.

    Status polling from the UI reuses the owner check while the same browser session
    (webSocketDebuggerUrl) keeps answering; automation passes fresh=True.
    """
    payload = read_debug_payload()
    kind = get_browser_kind()
    if not is_valid_browser_debug_payload(payload, kind):
        reset_browser_status_cache()
        return False
    session_key = f"{kind}|{payload.get('webSocketDebuggerUrl')}"
    now = time.monotonic()
    with browser_status_lock:
        cached = dict(browser_status_cache)
    if (
        not fresh
        and cached.get("session_key") == session_key
        and now - float(cached.get("checked_at") or 0.0) < BROWSER_OWNER_CACHE_SECONDS
    ):
        return bool(cached.get("owner_ok"))
    try:
        owner_ok = bool(browser_debug_owner_uses_profile())
    except Exception:
        owner_ok = False
    with browser_status_lock:
        browser_status_cache.update({"session_key": session_key, "owner_ok": owner_ok, "checked_at": now})
    return owner_ok


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


def browser_profile_in_use(profile):
    """True while a browser runs with this profile: Chromium keeps its lockfile open."""
    lock_path = os.path.join(profile, "lockfile")
    if not os.path.exists(lock_path):
        return False
    try:
        with open(lock_path, "rb"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def is_app_browser_profile(profile):
    """A profile this app created: one per browser kind, plus the v1.6.0 Edge profile."""
    managed = [browser_source.profile_dir(CONFIG_DIR, kind) for kind in browser_source.KINDS]
    return any(same_path(profile, item) for item in managed + [USER_DATA_DIR_DEBUG])


def owned_browser_instance(config=None):
    """Capture both process and CDP identity; a profile path alone grants no ownership."""
    config = load_app_config() if config is None else config
    kind = get_browser_kind(config)
    executable = resolve_browser_executable(config)
    profile = browser_profile_dir(kind)
    owner = get_debug_port_owner()
    payload = read_debug_payload()
    if not browser_owner_matches(owner, executable, profile) or not is_valid_browser_debug_payload(payload, kind):
        return None
    arguments = split_windows_command_line(owner.get("command_line"))
    return {
        "pid": owner["pid"], "executable": executable, "profile": profile,
        "session": payload["webSocketDebuggerUrl"],
        "headless": any(arg == "--headless" or arg.startswith("--headless=") for arg in arguments),
    }


def close_owned_browser(instance):
    """Gracefully close an identity-checked app browser, retaining session cookies.

    A changed PID, profile, executable or CDP session means the browser has gone
    away. Never terminate a process by name, or send Browser.close to its successor.
    """
    owner = get_debug_port_owner()
    payload = read_debug_payload()
    if (
        not owner or owner.get("pid") != instance.get("pid")
        or not browser_owner_matches(owner, instance["executable"], instance["profile"])
        or not is_app_browser_profile(instance["profile"])
        or not payload or payload.get("webSocketDebuggerUrl") != instance.get("session")
    ):
        return True
    with get_sync_playwright()() as playwright:
        browser = playwright.chromium.connect_over_cdp(instance["session"])
        # Session cookies may otherwise be discarded on a clean Chrome exit.
        # Keep them only in memory; the persistent profile stores the rest.
        if browser.contexts:
            pending = browser_session_cookies.get(instance["profile"])
            if pending:
                browser.contexts[0].add_cookies(pending)
            browser_session_cookies[instance["profile"]] = browser.contexts[0].cookies()
        session = browser.new_browser_cdp_session()
        try:
            session.send("Browser.close")
        except Exception:
            # Chrome can close the transport before replying to Browser.close.
            pass
    deadline = time.monotonic() + browser_source.COLD_START_SECONDS
    for _ in range(BROWSER_COLD_START_POLLS):
        current = read_debug_payload()
        if (not current or current.get("webSocketDebuggerUrl") != instance["session"]) and not browser_profile_in_use(instance["profile"]):
            process = instance.get("process")
            if process is not None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    return False
            reset_browser_status_cache()
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(browser_source.POLL_SECONDS)
    return False


def close_debug_browser(owner):
    """Close a verified older app browser gracefully; never kill a personal browser."""
    profile = command_line_profile(owner.get("command_line"))
    executable = str(owner.get("path") or "")
    current = get_debug_port_owner()
    payload = read_debug_payload()
    known_executable = (
        os.path.basename(executable).lower() in ("chrome.exe", "msedge.exe", "chromium.exe")
        or same_path(executable, resolve_browser_executable())
    )
    if (
        not known_executable or not is_app_browser_profile(profile)
        or not browser_owner_matches(current, executable, profile)
        or current.get("pid") != owner.get("pid")
        or not is_valid_browser_debug_payload(payload)
    ):
        raise BrowserStartError("Không thể xác minh trình duyệt cũ thuộc app; giữ nguyên tiến trình đó.")
    instance = {"pid": current["pid"], "executable": executable, "profile": profile,
                "session": payload["webSocketDebuggerUrl"]}
    if not close_owned_browser(instance):
        raise BrowserStartError("Trình duyệt của app chưa đóng xong; hãy thử lại.")


def wait_for_debug_port(accept, polls):
    """Poll /json/version every POLL_SECONDS until accept(payload) is true.

    False after polls tries or polls * POLL_SECONDS of wall time, whichever comes first,
    so slow answers can never stretch the wait past its limit.
    """
    polls = int(polls)
    deadline = time.monotonic() + polls * browser_source.POLL_SECONDS
    for attempt in range(polls):
        check_cancel_requested()
        if accept(read_debug_payload()):
            return True
        if attempt + 1 >= polls or time.monotonic() >= deadline:
            break
        time.sleep(browser_source.POLL_SECONDS)
    return False


def minimize_browser_window():
    try:
        with get_sync_playwright()() as playwright:
            browser = playwright.chromium.connect_over_cdp(browser_debug_url())
            context = browser.contexts[0] if browser.contexts else None
            if context and context.pages:
                set_browser_window_visibility(context, context.pages[0], False)
    except Exception:
        pass


def make_room_on_debug_port(label, port):
    """Close an older automation browser of this app on the port; refuse anything else."""
    owner = get_debug_port_owner()
    profile = command_line_profile((owner or {}).get("command_line"))
    if owner and profile and is_app_browser_profile(profile):
        log_to_app(f"Đóng trình duyệt tự động hoá cũ ({owner.get('name') or 'không rõ'}) đang giữ cổng {port}...")
        close_debug_browser(owner)
        if wait_for_debug_port(lambda payload: payload is None, round(5 / browser_source.POLL_SECONDS)):
            return
        raise BrowserStartError(f"Không đóng được trình duyệt tự động hoá cũ đang giữ cổng {port}.")
    name = (owner or {}).get("name") or "một chương trình khác"
    raise BrowserStartError(
        f"Cổng điều khiển {port} đang được {name} dùng, nên app chưa mở được {label}. "
        f"Hãy đóng chương trình đó (hoặc tắt chế độ remote debugging của nó) rồi thử lại."
    )


@browser_lifecycle.leased()
def ensure_browser_running(background=None, config=None, *, headless=None, start_url="about:blank"):
    """Lazily start or switch the dedicated browser; automation defaults to headless.

    Explicit background=False retains the login-button API for a visible window.
    Mode changes run under the operation lease and reuse the same profile.
    """
    check_cancel_requested()
    browser_lifecycle.clear_manual_login()
    operation = getattr(browser_operation_state, "operation", None)
    if operation is not None and not operation["opening_challenge"]:
        if headless is False or (headless is None and background is False):
            operation["explicit_headed"] = True
    config = load_app_config() if config is None else config
    kind = get_browser_kind(config)
    label = browser_label(kind)
    port = get_browser_debug_port()
    if headless is None:
        override = getattr(browser_operation_state, "headless", None)
        headless = override if override is not None else (
            bool(config.get("edge_background", True)) if background is None else bool(background)
        )
    if operation is not None and not bool(config.get("edge_background", True)):
        operation["explicit_headed"] = True
    with browser_launch_lock:
        if check_browser_status(fresh=True):
            instance = owned_browser_instance(config)
            if instance is None:
                # A successful status probe is not an ownership snapshot: the
                # port may have changed hands between the two reads. Fail closed.
                browser_lifecycle.forget()
                raise BrowserStartError(f"Không thể xác minh chủ sở hữu {label} trên cổng {port}; giữ nguyên trình duyệt đó.")
            previous_instance = browser_lifecycle.instance or {}
            if previous_instance.get("session") == instance["session"]:
                instance["initial_page_pending"] = previous_instance.get("initial_page_pending", False)
                instance["process"] = previous_instance.get("process")
            browser_lifecycle.remember(instance)
            if instance["headless"] == headless:
                return True
            if not close_owned_browser(instance):
                raise BrowserStartError(f"Chưa đóng được {label} để đổi chế độ; hãy thử lại.")
            browser_lifecycle.forget()
        if read_debug_payload() is not None:
            make_room_on_debug_port(label, port)
        executable = resolve_browser_executable(config)
        if not executable:
            raise BrowserStartError(missing_browser_message(kind))
        profile = browser_profile_dir(kind)
        if browser_profile_in_use(profile):
            raise BrowserStartError(
                f"{label} của VietSub Studio đang mở nhưng chưa bật cổng điều khiển {port}. "
                f"Hãy đóng cửa sổ {label} đó rồi thử lại."
            )
        if background is None:
            background = bool(config.get("edge_background", True))
        os.makedirs(profile, exist_ok=True)
        log_to_app(f"Đang mở {label} {'headless' if headless else 'hiển thị'} với profile riêng của app: {profile}")
        process = subprocess.Popen(
            browser_source.launch_command(executable, profile, port=port, background=background,
                                          headless=headless, start_url=start_url),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        if not wait_for_debug_port(lambda payload: is_valid_browser_debug_payload(payload, kind),
                                   BROWSER_COLD_START_POLLS):
            raise BrowserStartError(
                f"{label} đã mở nhưng cổng điều khiển {port} không phản hồi sau "
                f"{browser_source.COLD_START_SECONDS} giây. Hãy đóng cửa sổ {label} của app rồi thử lại."
            )
        if not check_browser_status(fresh=True):
            raise BrowserStartError(f"Cổng {port} đã phản hồi nhưng không thuộc {label} của VietSub Studio.")
        instance = owned_browser_instance(config)
        if instance is None:
            browser_lifecycle.forget()
            raise BrowserStartError(f"Không thể xác minh chủ sở hữu {label} vừa mở trên cổng {port}.")
        instance["initial_page_pending"] = True
        instance["process"] = process
        browser_lifecycle.remember(instance)
        if background and not headless:
            minimize_browser_window()
        log_to_app(f"Đã mở {label} và kết nối cổng điều khiển {port}.")
        return True


def set_browser_window_visibility(context, page, visible):
    # Capture callbacks also run their "attention ended" cleanup on timeout or
    # cancellation. Keep a headed retry visible until the successful outer
    # boundary verifies it, rather than minimizing an unfinished login/challenge.
    if (not visible and getattr(browser_operation_state, "operation", None) is not None
            and getattr(browser_operation_state, "headless", None) is False):
        return True
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


BLANK_PAGE_URLS = browser_source.BLANK_PAGE_URLS


def reuse_blank_page(context):
    """The browser's empty start tab when there is one, else a new tab."""
    return browser_source.reuse_page(context)


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


def browser_context(playwright):
    """The persistent context of the automation browser (its own profile)."""
    endpoint = (browser_lifecycle.instance or {}).get("session") or browser_debug_url()
    browser = playwright.chromium.connect_over_cdp(endpoint)
    if not browser.contexts:
        raise BrowserStartError(f"Không tìm thấy profile {browser_label()} của app đang hoạt động.")
    context = browser.contexts[0]
    instance = browser_lifecycle.instance
    if instance and instance["profile"] in browser_session_cookies:
        cookies = browser_session_cookies[instance["profile"]]
        context.add_cookies(cookies)
        browser_session_cookies.pop(instance["profile"], None)
        for page in context.pages:
            if page.url not in BLANK_PAGE_URLS:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
    if instance and instance.pop("initial_page_pending", False) and context.pages:
        context._vietsub_initial_page = context.pages[0]
    return context


@browser_lifecycle.leased()
def show_edge_for_login(notebook_url):
    """Show Gemini Notebook in the automation browser so the user can sign in to Google."""
    notebook_url = validate_notebook_url(notebook_url)
    ensure_browser_running(background=False, start_url=notebook_url)
    browser_lifecycle.grant_manual_login()
    with get_sync_playwright()() as playwright:
        context = browser_context(playwright)
        notebook_id = urlparse(notebook_url).path.rstrip("/").rsplit("/", 1)[-1]
        page = browser_source.reuse_page(context, notebook_url, matches=lambda item: notebook_id in item.url)
        if notebook_id not in page.url:
            page.goto(notebook_url)
        if not set_browser_window_visibility(context, page, True):
            page.bring_to_front()
    return True

# Legacy Douzy sidecar integration (v1.5.x). Since v1.6.0 nothing calls the sidecar
# functions below: downloads go through vietsub.sources. They stay for the existing
# compatibility tests and for reading an old Douzy library, and can be removed later.
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
                line = output_queue.get(timeout=max(0.0, min(0.25, deadline - time.monotonic())))
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

def is_volatile_douzy_field(key):
    # Split snake_case and camelCase names, e.g. "updatedAt" -> ["updated", "at"].
    words = re.findall(r"[a-z]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key)).lower())
    return any(word in DOUZY_VOLATILE_FIELD_WORDS for word in words)


def douzy_job_signature(job):
    """Text form of a sidecar job payload without fields that change with the clock."""

    def without_timing(value):
        if isinstance(value, dict):
            return {
                str(key): without_timing(item)
                for key, item in value.items()
                if not is_volatile_douzy_field(key)
            }
        if isinstance(value, list):
            return [without_timing(item) for item in value]
        return value

    return json.dumps(without_timing(job), sort_keys=True, ensure_ascii=False, default=str)


def poll_download_job(port, token, job_id):
    log_to_app("Đang chờ quá trình tải video hoàn tất...")
    url = f"http://127.0.0.1:{port}/api/v1/jobs/{job_id}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")

    started_at = time.monotonic()
    last_change_at = started_at
    last_signature = None
    consecutive_failures = 0
    last_status = None
    while True:
        check_cancel_requested()
        now = time.monotonic()
        if now - started_at > DOUZY_MAX_DOWNLOAD_SECONDS:
            raise TimeoutError("Tải video vượt quá giới hạn 6 giờ; đã dừng sidecar Douzy.")
        if now - last_change_at > DOUZY_STALL_TIMEOUT_SECONDS:
            raise TimeoutError(
                "Douzy không báo tiến độ mới trong 5 phút; lượt tải có thể đã bị treo. "
                "Hãy kiểm tra mạng rồi chạy lại."
            )

        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                job = json.loads(response.read().decode("utf-8"))
                consecutive_failures = 0
                # Any change outside timing-only fields counts as progress.
                signature = douzy_job_signature(job)
                if signature != last_signature:
                    last_signature = signature
                    last_change_at = time.monotonic()
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
    """Legacy (v1.5.x) rule for Douzy downloads; v1.6 copies or links old Douzy files instead."""
    download_root = get_douzy_download_dir()
    source_dir = os.path.dirname(os.path.abspath(source_video))
    return (
        path_is_within(source_video, download_root)
        and not os.path.isfile(os.path.join(source_dir, "project.json"))
    )


def get_download_cache_dir(config=None):
    """Folder where downloaded videos wait until their project is finished.

    It sits inside the output directory, so moving a finished video into its project
    folder is a rename on the same drive rather than a multi-GB copy.
    """
    if DOWNLOAD_DIR_OVERRIDE:
        return os.path.abspath(DOWNLOAD_DIR_OVERRIDE)
    config = load_app_config() if config is None else config
    try:
        output_root = validate_output_dir(config.get("output_dir", ""))
    except ValueError:
        output_root = ""
    return os.path.join(output_root or get_default_output_directory(), DOWNLOAD_CACHE_DIRNAME)


def should_move_downloaded_source(source_video):
    """True for a video VietSub Studio downloaded itself: it moves into the project at the end."""
    if not source_video:
        return False
    source_dir = os.path.dirname(os.path.abspath(source_video))
    return (
        path_is_within(source_video, get_download_cache_dir())
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
        raise FileNotFoundError("Video đã tải không còn tồn tại để chuyển vào thư mục dự án.")

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
            raise FileExistsError(f"Hai file đi kèm có cùng đích: {os.path.basename(candidate)}")
        planned_destinations[destination_key] = candidate
        if os.path.exists(destination) and os.path.normcase(destination) != os.path.normcase(candidate):
            if not files_have_same_content(candidate, destination):
                raise FileExistsError(f"Đích asset đã tồn tại và khác nội dung: {destination}")

    # Move the large MP4 last so an ancillary-asset error leaves the retry source intact.
    candidates.sort(key=lambda path: os.path.normcase(path) == source_norm)
    log_to_app("Đang chuyển video và các file đi kèm vào thư mục dự án...")
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
    # Only a file that Douzy itself downloaded has a Douzy history record to update.
    if path_is_within(source_video, get_douzy_download_dir()):
        update_douzy_video_path(video_id, moved_video)
    replace_source_video_references(source_video, moved_video)
    log_to_app(f"Đã gom video và các file đi kèm vào: {project['project_dir']}")
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
    # Keep the container of the source: an MKV picked on this computer stays .mkv.
    video_extension = os.path.splitext(source_video)[1].lower()
    if video_extension not in local_source.LOCAL_VIDEO_EXTENSIONS:
        video_extension = ".mp4"
    video_destination = os.path.join(project_dir, project_name + video_extension)
    video_path = os.path.abspath(source_video) if defer_video_move else video_destination
    raw_srt_path = os.path.join(project_dir, project_name + ".raw.srt")
    translated_srt_path = os.path.join(project_dir, project_name + ".vi.srt")

    log_to_app(f"Tạo bộ file đồng bộ với tên: {project_name}")
    try:
        check_cancel_requested()
        if defer_video_move:
            log_to_app("Dùng video vừa tải trong lúc xử lý; sẽ chuyển vào dự án sau khi dịch xong.")
        elif try_hardlink_video(source_video, video_path):
            # Same drive: the project gets a hard link, so no second copy of a large video.
            log_to_app("Đã liên kết video vào thư mục kết quả (hard link), không cần sao chép lại.")
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
        "publish_metadata": project.get("publish_metadata", ""),
        "source_video_deleted": project.get("source_video_deleted", False),
        "source_cleanup_bytes": project.get("source_cleanup_bytes", 0),
        "source_cleanup_errors": project.get("source_cleanup_errors", []),
        "crop_coords": project.get("crop_coords"),
        "video_resolution": project.get("video_resolution"),
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
    # Add the inter-workspace sidecar without changing the legacy export contract.
    # A locked/corrupt newer sidecar must not turn a valid VietSub export into a failure.
    try:
        target, _ = project_manifest.save_project(
            project, video_id=video_id, source_url=source_url, status=status, error=error,
        )
        project["project_manifest"] = target
    except (OSError, ValueError) as manifest_error:
        log_to_app(f"Bộ file VietSub được giữ nguyên; chưa lưu được manifest Workbench: {manifest_error}", "error")


def read_videocr_gui_settings():
    """Parse videocr_gui_config.ini whatever code page VideOCR used to write it.

    VideOCR writes the file in the Windows ANSI code page, so a profile folder
    such as C:\\Users\\José breaks a strict UTF-8 read. Lines without "=" are
    ignored instead of raising.
    """
    if not os.path.isfile(VIDEOCR_CONFIG):
        return {}
    try:
        with open(VIDEOCR_CONFIG, "rb") as handle:
            raw = handle.read(1024 * 1024)
    except OSError:
        return {}
    text = ""
    for encoding in ("utf-8-sig", "mbcs", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    settings = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip():
            settings[key.strip()] = value.strip()
    return settings


def videocr_ini_flag(settings, key, default="true"):
    value = str(settings.get(key, default)).strip().lower()
    return value if value in {"true", "false"} else default


def get_videocr_crop_coords():
    raw_crop_boxes = read_videocr_gui_settings().get("--saved_crop_boxes")
    if not raw_crop_boxes:
        return None
    try:
        crop_boxes = ast.literal_eval(raw_crop_boxes)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    if isinstance(crop_boxes, (list, tuple)) and crop_boxes and isinstance(crop_boxes[0], dict):
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

def resolve_path_executable(name):
    """Find a tool in absolute PATH directories only.

    shutil.which on Windows searches the current directory before PATH, so a
    file such as Downloads\\ffprobe.exe would run instead of the real tool.
    """
    file_names = [name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        file_names = [name + ".exe"]
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        directory = directory.strip().strip('"')
        if not directory or not os.path.isabs(directory):
            continue
        for file_name in file_names:
            candidate = os.path.abspath(os.path.join(directory, file_name))
            if os.path.isfile(candidate) and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return candidate
    return ""


def resolve_ffprobe_executable():
    return resolve_path_executable("ffprobe")


def resolve_ffmpeg_executable():
    return resolve_path_executable("ffmpeg")


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
        out = subprocess.check_output(
            cmd,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW_FLAGS,
        ).strip()
        parts = out.split("x")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def positive_media_number(value):
    """Parse optional FFprobe scalars without accepting booleans, NaN or infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def parse_video_frame_rate(value):
    if isinstance(value, str) and "/" in value:
        numerator, _, denominator = value.partition("/")
        numerator = positive_media_number(numerator)
        denominator = positive_media_number(denominator)
        return positive_media_number(numerator / denominator) if numerator and denominator else None
    return positive_media_number(value)


def get_video_metadata(video_path):
    """Optional preview telemetry; failure must never prevent preparing or OCRing a video."""
    ffprobe_path = resolve_ffprobe_executable()
    if not ffprobe_path:
        return None
    try:
        data = json.loads(subprocess.check_output(
            [ffprobe_path, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=duration,avg_frame_rate,r_frame_rate,bit_rate:format=duration,bit_rate",
             "-of", "json", video_path],
            text=True, encoding="utf-8", timeout=15,
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW_FLAGS,
        ))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not isinstance(data, dict):
        return None
    streams = data.get("streams")
    stream = streams[0] if isinstance(streams, list) and streams and isinstance(streams[0], dict) else {}
    container = data.get("format") if isinstance(data.get("format"), dict) else {}
    bit_rate = positive_media_number(stream.get("bit_rate")) or positive_media_number(container.get("bit_rate"))
    metadata = {
        "duration": positive_media_number(stream.get("duration")) or positive_media_number(container.get("duration")),
        "frame_rate": parse_video_frame_rate(stream.get("avg_frame_rate")) or parse_video_frame_rate(stream.get("r_frame_rate")),
        "bit_rate": (int(bit_rate) or None) if bit_rate is not None else None,
    }
    return metadata if any(value is not None for value in metadata.values()) else None


def get_video_duration_seconds(video_path):
    ffprobe_path = resolve_ffprobe_executable()
    if not ffprobe_path:
        return None
    cmd = [
        ffprobe_path, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path,
    ]
    try:
        duration = float(subprocess.check_output(
            cmd,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW_FLAGS,
        ).strip())
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


def videocr_timeout_seconds(span_seconds):
    """Wall-clock limit for one VideOCR run that scans span_seconds of video.

    CPU-only machines OCR slower than real time, so the limit grows with the span
    (3x plus 10 minutes of model start-up) and never drops below 30 minutes.
    """
    try:
        span = float(span_seconds)
    except (TypeError, ValueError):
        span = 0.0
    if not math.isfinite(span) or span <= 0:
        return VIDEOCR_UNKNOWN_SPAN_TIMEOUT_SECONDS
    return max(VIDEOCR_PROCESS_TIMEOUT_SECONDS, math.ceil(span * 3) + 600)


def write_process_failure_tail(name, lines):
    # Per-line output only goes to the app console; the durable log keeps the
    # last lines of a failed run so earlier steps are not rotated out of the file.
    if not lines:
        return
    write_durable_log(f"[ERROR] {name}: {len(lines)} dòng output cuối trước khi dừng")
    for line in lines:
        write_durable_log(f"[OCR] {line}")


def execute_videocr_command(cmd, output_srt, *, allow_empty=False, timeout_seconds=None):
    if os.path.exists(output_srt):
        os.remove(output_srt)
    timeout_seconds = timeout_seconds or VIDEOCR_PROCESS_TIMEOUT_SECONDS
    process_started_at = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=NO_WINDOW_FLAGS,
    )
    register_active_process("VideOCR", proc)
    output_queue = Queue(maxsize=1_000)
    Thread(target=queue_process_output, args=(proc.stdout, output_queue), daemon=True).start()
    deadline = time.monotonic() + timeout_seconds
    recent_output = deque(maxlen=PROCESS_FAILURE_TAIL_LINES)

    def show_line(raw_line):
        line_str = raw_line.strip()
        if line_str:
            recent_output.append(line_str)
            log_to_app(line_str, "ocr", durable=False)

    completed = False
    try:
        while proc.poll() is None:
            check_cancel_requested()
            if time.monotonic() >= deadline:
                stop_process(proc, "VideOCR")
                raise TimeoutError(
                    f"VideOCR quá thời gian chờ ({math.ceil(timeout_seconds / 60)} phút) "
                    "cho đoạn video này."
                )
            try:
                show_line(output_queue.get(timeout=0.5))
            except Empty:
                pass

        while True:
            try:
                show_line(output_queue.get_nowait())
            except Empty:
                break

        check_cancel_requested()
        if proc.returncode != 0:
            raise VideoOCRProcessError(proc.returncode)
        completed = True
    except BaseException:
        # Includes WorkflowCancelled and KeyboardInterrupt (Ctrl+C in the CLI).
        stop_process(proc, "VideOCR")
        raise
    finally:
        unregister_active_process("VideOCR", proc)
        if not completed:
            write_process_failure_tail("VideOCR", list(recent_output))

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
    except BaseException:
        # Includes WorkflowCancelled and KeyboardInterrupt (Ctrl+C in the CLI).
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
    # Bind the exact geometry used by this OCR invocation to its workflow thread.
    # Reading Settings again after OCR could pick a different default crop.
    workflow_context.ocr_geometry = {
        "crop_coords": copy.deepcopy(coords) if res else None,
        "video_resolution": {"width": res[0], "height": res[1]} if res else None,
    }
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

    # Defaults come from the VideOCR GUI settings when they are readable.
    videocr_settings = read_videocr_gui_settings()
    use_gpu = videocr_ini_flag(videocr_settings, "--use_gpu")
    use_server_model = videocr_ini_flag(videocr_settings, "--use_server_model")
    normalize_to_simplified = videocr_ini_flag(
        videocr_settings, "--normalize_to_simplified_chinese"
    )

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
        # ignore_cleanup_errors: a segment still held by a dying process must not
        # replace the real error (cancel, timeout) with a PermissionError.
        with tempfile.TemporaryDirectory(
            prefix="VietSub-VideOCR-", ignore_cleanup_errors=True
        ) as temporary_dir:
            for chunk_index in range(chunk_count):
                check_cancel_requested()
                start_seconds = chunk_index * VIDEOCR_CHUNK_SECONDS
                end_seconds = min(duration, start_seconds + VIDEOCR_CHUNK_SECONDS)
                chunk_timeout = videocr_timeout_seconds(end_seconds - start_seconds)
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
                        execute_videocr_command(
                            direct_cmd, direct_srt, allow_empty=True, timeout_seconds=chunk_timeout
                        )
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
                        execute_videocr_command(
                            normalized_cmd,
                            normalized_srt,
                            allow_empty=True,
                            timeout_seconds=chunk_timeout,
                        )
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
        # Without ffprobe the duration is unknown, so the generous 4-hour limit applies.
        execute_videocr_command(cmd, output_srt, timeout_seconds=videocr_timeout_seconds(duration))

    log_to_app(f"Quét phụ đề thành công: {output_srt}")
    return output_srt

@browser_automation
def resolve_url_via_browser(short_url, notebook_url=""):
    """Follow a share link in the automation browser when the HTTP resolution failed."""
    check_cancel_requested()
    label = browser_label(short=True)
    try:
        ensure_browser_running()
    except BrowserStartError as error:
        log_to_app(f"Không mở được {label} để giải mã link share: {error}", "error")
        return None

    log_to_app(f"Giải mã link share bằng {label}: {short_url}")
    try:
        with get_sync_playwright()() as p:
            context = browser_context(p)
            page = browser_source.reuse_page(context, short_url)
            try:
                page.goto(short_url, wait_until="domcontentloaded", timeout=15000)
                # Poll for the redirect; extend only an actual manual challenge.
                deadline = time.monotonic() + 5
                attention = False
                while time.monotonic() < deadline:
                    check_cancel_requested()
                    if browser_capture.page_needs_attention(page):
                        require_visible_browser(context, page)
                        if not attention:
                            attention = True
                            deadline = time.monotonic() + 180
                    elif re.search(r"/(?:video|note)/\d+", urlparse(page.url).path):
                        break
                    page.wait_for_timeout(250)
                resolved_url = page.url
                log_to_app(f"Trình duyệt giải mã thành: {resolved_url}")
                return resolved_url
            finally:
                browser_source.release_page(context, page)
    except (WorkflowCancelled, HeadedBrowserRequired):
        raise
    except Exception as e:
        log_to_app(f"Lỗi giải mã link share qua {label}: {e}", "error")
        return None

SRT_TIMECODE_PATTERN = re.compile(
    r"(?m)^\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?\s*$"
)
SRT_TIMECODE_LINE_PATTERN = re.compile(
    r"^\s*\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*"
    r"\d{2}:\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?\s*$"
)


# Default instructions for community builds: neutral rules for any kind of video.
TRANSLATION_DEFAULT_INSTRUCTIONS = """\
Bạn là biên dịch viên phụ đề chuyên nghiệp, dịch sang tiếng Việt.

QUY TẮC:
- Dịch tự nhiên, sát nghĩa và đúng ngữ cảnh của cả đoạn; không dịch word-by-word.
- Chọn đại từ nhân xưng theo quan hệ và sắc thái nhân vật (tôi/bạn, anh/em, tao/mày,
  ngài/tôi...) và giữ cách xưng hô nhất quán trong toàn bộ đoạn.
- Giữ tên riêng, thương hiệu và thuật ngữ chuyên ngành ở cách gọi phổ biến nhất với người
  Việt; nếu không chắc, giữ nguyên cách viết gốc.
- Câu ngắn gọn, dễ đọc trên màn hình; mỗi block không quá 2 dòng.
- Giữ nguyên tuyệt đối số thứ tự và timestamp của từng block.
- Chỉ trả về đúng một block SRT; không thêm lời giải thích hay ghi chú.
""".strip()

# Optional preset for Delta Force game videos (the v1.5.3 default glossary).
TRANSLATION_PRESET_DELTA_FORCE = """\
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
""".strip()


TRANSLATION_INSTRUCTION_PRESETS = {
    "default": {
        "label": "Mặc định (dùng cho mọi loại video)",
        "instructions": TRANSLATION_DEFAULT_INSTRUCTIONS,
    },
    "delta_force": {
        "label": "Game Delta Force (từ điển tên riêng và xưng hô)",
        "instructions": TRANSLATION_PRESET_DELTA_FORCE,
    },
}


def validate_translation_instructions(value):
    if value in (None, ""):
        return TRANSLATION_DEFAULT_INSTRUCTIONS
    if not isinstance(value, str):
        raise ValueError("Cách dịch mong muốn không hợp lệ.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return TRANSLATION_DEFAULT_INSTRUCTIONS
    if len(normalized) > MAX_TRANSLATION_INSTRUCTIONS_CHARS:
        raise ValueError(
            f"Cách dịch mong muốn không được vượt quá {MAX_TRANSLATION_INSTRUCTIONS_CHARS:,} ký tự."
        )
    if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
        raise ValueError("Cách dịch mong muốn chứa ký tự điều khiển không hợp lệ.")
    return normalized


SOURCE_LANGUAGE_NAMES = {
    "ch": "tiếng Trung",
    "en": "tiếng Anh",
    "ja": "tiếng Nhật",
    "ko": "tiếng Hàn",
    "vi": "tiếng Việt",
}


def source_language_name(source_lang):
    return SOURCE_LANGUAGE_NAMES.get(source_lang, SOURCE_LANGUAGE_NAMES["ch"])


def build_translation_task_rules(source_lang="ch"):
    if source_lang == "vi":
        return (
            "ĐẦU VÀO LÀ PHỤ ĐỀ TIẾNG VIỆT NHẬN DẠNG BẰNG OCR. ĐẦU RA BẮT BUỘC LÀ TIẾNG VIỆT.\n"
            "Chỉ sửa lỗi chính tả, dấu và chữ nhận dạng sai; giữ nguyên ý và cách xưng hô.\n"
            "Giữ nguyên số thứ tự và timestamp; chỉ sửa phần nội dung phụ đề.\n"
            "Đặt toàn bộ SRT đã sửa trong đúng một block code srt.\n\n"
            "Hãy sửa SRT sau đây, không được bỏ sót block nào:\n\n"
        )
    if source_lang == "ch":
        language_rules = (
            "ĐẦU VÀO LÀ TIẾNG TRUNG. ĐẦU RA BẮT BUỘC LÀ TIẾNG VIỆT.\n"
            "Mỗi block chỉ được có nội dung tiếng Việt tự nhiên.\n"
            "Tuyệt đối không chép lại câu tiếng Trung và không xuất song ngữ Trung-Việt.\n"
        )
    else:
        name = source_language_name(source_lang)
        language_rules = (
            f"ĐẦU VÀO LÀ {name.upper()}. ĐẦU RA BẮT BUỘC LÀ TIẾNG VIỆT.\n"
            "Mỗi block chỉ được có nội dung tiếng Việt tự nhiên.\n"
            f"Tuyệt đối không chép lại câu {name} gốc và không xuất song ngữ.\n"
        )
    return (
        language_rules
        + "Giữ nguyên số thứ tự và timestamp; chỉ dịch phần nội dung phụ đề.\n"
        + "Đặt toàn bộ SRT đã dịch trong đúng một block code srt.\n\n"
        + "Hãy dịch SRT sau đây, không được bỏ sót block nào:\n\n"
    )


def build_translation_prompt(srt_content, translation_instructions=None, source_lang="ch"):
    instructions = validate_translation_instructions(translation_instructions)
    return (
        instructions
        + "\n\n"
        + build_translation_task_rules(source_lang)
        + "```srt\n"
        + f"{srt_content}\n"
        + "```"
    )


def build_translation_system_prompt(source_lang="ch"):
    if source_lang == "vi":
        return (
            "Bạn là biên tập viên phụ đề tiếng Việt chuyên nghiệp. Đầu vào là phụ đề tiếng Việt "
            "nhận dạng bằng OCR; hãy sửa lỗi chính tả, dấu và chữ nhận sai mà không đổi ý. "
            "Tuân thủ quy tắc trong yêu cầu người dùng. Giữ nguyên số thứ tự và timestamp, "
            "đặt SRT trong đúng một block code srt."
        )
    if source_lang == "ch":
        direction = "Trung sang Việt"
        echo_rule = "tuyệt đối không chép lại tiếng Trung và không xuất song ngữ"
    else:
        name = source_language_name(source_lang)
        direction = f"{name[len('tiếng '):].capitalize()} sang Việt"
        echo_rule = f"tuyệt đối không chép lại câu {name} gốc và không xuất song ngữ"
    name = source_language_name(source_lang)
    return (
        f"Bạn là biên dịch viên phụ đề {direction} chuyên nghiệp. Đầu vào là {name}, đầu ra chỉ "
        f"được là tiếng Việt; {echo_rule}. Tuân thủ từ điển và quy tắc trong yêu cầu người dùng. "
        "Giữ nguyên số thứ tự và timestamp, đặt SRT trong đúng một block code srt."
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
        raise TranslationApiError("API dịch trả về JSON không hợp lệ.", retryable=True)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TranslationApiError("API dịch không trả về lựa chọn nào.", retryable=True)
    choice = choices[0]
    if not isinstance(choice, dict):
        raise TranslationApiError("API dịch trả về lựa chọn không hợp lệ.", retryable=True)
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else choice.get("text")
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("text")
        )
    if choice.get("finish_reason") == "length":
        raise TranslationApiError("API đã cắt câu trả lời vì chạm giới hạn output token.")
    if not isinstance(content, str) or not content.strip():
        raise TranslationApiError("API dịch trả về nội dung trống.", retryable=True)
    return content.strip()


class TranslationApiError(RuntimeError):
    """A translation API failure that carries its retry decision.

    Retry logic reads these attributes instead of searching the message text.
    """

    def __init__(self, message, *, retryable=False, status=None, stream_unsupported=False, retry_after=None):
        super().__init__(message)
        self.retryable = bool(retryable)
        self.status = status
        self.stream_unsupported = bool(stream_unsupported)
        self.retry_after = retry_after


class RefuseRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow redirects from a translation API.

    urllib would resend the Authorization header to the redirect target, which can
    be another host or plain HTTP.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TranslationApiError(
            f"API dịch chuyển hướng sang {newurl}; app không gửi API key theo chuyển hướng. "
            "Hãy nhập đúng Base URL cuối cùng trong Thiết lập.",
            status=code,
        )


TRANSLATION_API_OPENER = urllib.request.build_opener(RefuseRedirectHandler)


def open_translation_api_request(request_object, timeout):
    return TRANSLATION_API_OPENER.open(request_object, timeout=timeout)


def parse_retry_after_seconds(headers):
    try:
        value = headers.get("Retry-After") if headers is not None else None
        seconds = int(float(str(value).strip()))
    except (AttributeError, TypeError, ValueError):
        return None
    return max(0, min(seconds, TRANSLATION_API_MAX_RETRY_AFTER_SECONDS))


def translation_http_error(error, *, stream_requested):
    try:
        raw_error = error.read(128 * 1024) or b""
    except Exception:
        raw_error = b""
    text = raw_error.decode("utf-8", errors="replace").strip()
    detail = text
    try:
        payload = json.loads(text)
        value = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(value, dict):
            value = value.get("message") or value.get("code") or value
        detail = str(value)
    except ValueError:
        pass
    code = int(error.code or 0)
    return TranslationApiError(
        f"API dịch trả lỗi HTTP {code}: {detail[:500] or error.reason}",
        retryable=code in {408, 409, 425, 429} or code >= 500,
        status=code,
        # Only a 400-class rejection that mentions streaming is a streaming problem;
        # a wrong model name or bad parameter is reported at once.
        stream_unsupported=stream_requested and code in {400, 415, 422} and "stream" in detail.lower(),
        retry_after=parse_retry_after_seconds(getattr(error, "headers", None)),
    )


def read_streaming_chat_completion(response):
    content_parts = []
    received_bytes = 0
    finished = False
    for raw_line in iter(response.readline, b""):
        received_bytes += len(raw_line)
        if received_bytes > TRANSLATION_API_MAX_RESPONSE_BYTES:
            raise TranslationApiError("Phản hồi streaming của API dịch vượt quá giới hạn an toàn 16 MiB.")
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace").strip()
        else:
            line = str(raw_line).strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            finished = True
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("error"):
            detail = payload["error"]
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("code") or str(detail)
            raise TranslationApiError(f"API dịch trả lỗi streaming: {str(detail)[:500]}", retryable=True)
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise TranslationApiError("API đã cắt câu trả lời vì chạm giới hạn output token.")
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
        if finish_reason:
            finished = True
    if not finished:
        raise TranslationApiError(
            "Luồng streaming của API dịch bị ngắt giữa chừng (không có finish_reason hay [DONE]).",
            retryable=True,
        )
    content = "".join(content_parts).strip()
    if not content:
        raise TranslationApiError("API dịch streaming không trả về nội dung.", retryable=True)
    return content


def call_openai_compatible_api(
    runtime_config,
    messages,
    *,
    max_tokens=16_000,
    stream=False,
    response_hook=None,
):
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
        with open_translation_api_request(
            request_object,
            timeout=TRANSLATION_API_TIMEOUT_SECONDS,
        ) as response:
            if response_hook:
                response_hook(response)
            content_type = ""
            headers = getattr(response, "headers", None)
            if headers is not None:
                content_type = str(headers.get("Content-Type", "")).lower()
            if stream and "text/event-stream" in content_type:
                return read_streaming_chat_completion(response)
            raw = response.read(TRANSLATION_API_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise translation_http_error(error, stream_requested=stream) from error
    except urllib.error.URLError as error:
        raise TranslationApiError(
            f"Không kết nối được API dịch: {error.reason}", retryable=True
        ) from error
    except (http.client.HTTPException, OSError) as error:
        # Includes IncompleteRead, reset connections and socket timeouts mid-response.
        raise TranslationApiError(
            f"Kết nối tới API dịch bị gián đoạn ({type(error).__name__}: {error}).",
            retryable=True,
        ) from error
    if len(raw) > TRANSLATION_API_MAX_RESPONSE_BYTES:
        raise TranslationApiError("Phản hồi API dịch vượt quá giới hạn an toàn 16 MiB.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise TranslationApiError("API dịch không trả về JSON UTF-8 hợp lệ.", retryable=True) from error
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
    # Size limits apply to every video so a dense SRT never exceeds one request's
    # output budget; the 30-minute time boundary only applies to long videos.
    use_time_boundaries = declared_duration > TRANSLATION_CHUNK_THRESHOLD_SECONDS
    if not use_time_boundaries and (
        len(blocks) <= TRANSLATION_CHUNK_MAX_BLOCKS
        and len(format_srt_blocks(blocks)) <= TRANSLATION_CHUNK_MAX_CHARS
    ):
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
        # Count the blank-line separator too, so a joined chunk stays within the limit.
        block_chars = len(format_srt_blocks([block])) + 1
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
            if use_time_boundaries:
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
            "hãy chạy lại bước dịch hoặc chọn model dịch mạnh hơn."
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


def srt_block_times(blocks):
    times = []
    for _, timecode, _ in blocks:
        match = SRT_TIMECODE_PATTERN.fullmatch(timecode)
        times.append(
            (srt_timestamp_milliseconds(match.group(1)), srt_timestamp_milliseconds(match.group(2)))
            if match else None
        )
    return times


def normalize_translated_srt(source_srt, translated_srt):
    """Rebuild a translation on the source indexes and timecodes, block by block.

    Models sometimes renumber a chunk from 1 or rewrite timecodes. When the block
    count matches and either the indexes or the times still line up, the source
    values are restored. Glued blocks (a missing blank line) and missing blocks
    are rejected, so a bad chunk is never cached as a resumable part.
    """
    source_blocks = parse_srt_blocks(source_srt)
    translated_blocks = parse_srt_blocks(translated_srt)
    if not source_blocks:
        validate_srt_structure(source_srt, "SRT gốc")
    if not translated_blocks:
        raise ValueError("Phản hồi dịch không phải SRT đầy đủ; không lưu file.")
    for _, _, text in translated_blocks:
        if any(SRT_TIMECODE_LINE_PATTERN.fullmatch(line) for line in text.splitlines()):
            raise ValueError("Bản dịch có block bị dính nhau (thiếu dòng trống giữa hai block).")
    if len(translated_blocks) != len(source_blocks):
        raise ValueError(
            f"Bản dịch có {len(translated_blocks)}/{len(source_blocks)} block so với SRT gốc."
        )
    same_indexes = [block[0] for block in source_blocks] == [block[0] for block in translated_blocks]
    same_times = srt_block_times(source_blocks) == srt_block_times(translated_blocks)
    if not same_indexes and not same_times:
        raise ValueError("Bản dịch lệch cả số thứ tự lẫn mốc thời gian so với SRT gốc.")
    rebuilt = "\n\n".join(
        f"{source_index}\n{source_timecode}\n{translated_text}"
        for (source_index, source_timecode, _), (_, _, translated_text) in zip(
            source_blocks, translated_blocks
        )
    )
    return validate_translated_srt(source_srt, rebuilt)


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
    if result.get("source_video_deleted") is True:
        required_files.pop("video")
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
        for key in (("raw_srt", "translated_srt") if result.get("source_video_deleted") is True
                    else ("video", "raw_srt", "translated_srt"))
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
    # Keep only code blocks that contain subtitles (a term list is ignored) and join
    # them with a blank line so a reply split over two blocks stays valid SRT.
    subtitle_blocks = [
        text.strip()
        for text in (code_blocks.all_inner_texts() if code_blocks.count() > 0 else [])
        if SRT_TIMECODE_PATTERN.search(text)
    ]
    if subtitle_blocks:
        translated_srt = "\n\n".join(subtitle_blocks) + "\n"
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
    source_lang="ch",
):
    previous_response = find_last_response(gemini_page, response_selectors)
    previous_response_text = previous_response.inner_text() if previous_response else ""
    prompt = build_translation_prompt(source_srt, translation_instructions, source_lang=source_lang)
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
        if cancel_requested():
            stop_gemini_generation(gemini_page)
            check_cancel_requested()
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
    for extension in local_source.LOCAL_VIDEO_EXTENSIONS:
        candidate = os.path.join(directory, base_name + extension)
        if os.path.isfile(candidate):
            return candidate
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
    source_lang = runtime_config.get("source_lang") or "ch"
    messages = [
        {
            "role": "system",
            "content": build_translation_system_prompt(source_lang),
        },
        {
            "role": "user",
            "content": build_translation_prompt(
                source_srt,
                runtime_config.get("translation_instructions"),
                source_lang=source_lang,
            ),
        },
    ]
    for attempt in range(1, TRANSLATION_API_MAX_ATTEMPTS + 1):
        check_cancel_requested()
        log_to_app(
            f"Gửi đoạn {chunk_index}/{chunk_count} tới {runtime_config['label']} "
            f"(lần thử {attempt}/{TRANSLATION_API_MAX_ATTEMPTS})..."
        )
        label = runtime_config["label"]
        try:
            use_stream = not runtime_config.get("stream_unsupported")
            try:
                response = request_translation_completion(runtime_config, messages, stream=use_stream)
            except TranslationApiError as error:
                if not (use_stream and error.stream_unsupported):
                    raise
                # Remember the rejection so later chunks of this job go straight to JSON.
                runtime_config["stream_unsupported"] = True
                log_to_app(f"{label} không hỗ trợ streaming; chuyển sang JSON đầy đủ.", "error")
                response = request_translation_completion(runtime_config, messages, stream=False)
            translated = extract_api_translated_srt(response, source_srt)
            log_to_app(f"Đã dịch xong đoạn {chunk_index}/{chunk_count}.")
            return translated
        except TranslationApiError as error:
            if not error.retryable or attempt >= TRANSLATION_API_MAX_ATTEMPTS:
                raise TranslationApiError(
                    f"Dịch đoạn {chunk_index}/{chunk_count} qua {label} thất bại: {error}",
                    status=error.status,
                ) from error
            planned_delay = TRANSLATION_API_RETRY_DELAYS[
                min(attempt - 1, len(TRANSLATION_API_RETRY_DELAYS) - 1)
            ]
            delay = max(planned_delay, error.retry_after or 0)
            log_to_app(
                f"API lỗi ở đoạn {chunk_index}/{chunk_count}; thử lại sau {delay} giây: {error}",
                "error",
            )
        except ValueError as error:
            # The model answered, but the SRT failed validation (missing blocks, changed
            # timestamps, Chinese or bilingual echo); the same request rarely does better.
            raise RuntimeError(
                f"Dịch đoạn {chunk_index}/{chunk_count} qua {label} thất bại: {error}"
            ) from error
        for _ in range(int(delay)):
            check_cancel_requested()
            time.sleep(1)
    raise RuntimeError("Không thể dịch đoạn phụ đề qua API.")


def close_quietly(resource):
    try:
        if resource is not None:
            resource.close()
    except Exception:
        pass


def close_in_background(resource):
    """Close without waiting: HTTPResponse.close() blocks while another thread is in read()."""
    if resource is not None:
        Thread(target=close_quietly, args=(resource,), daemon=True).start()


def run_cancellable(func, *, deadline_seconds, timeout_message):
    """Run a blocking network call in a helper thread; stop waiting on cancel or deadline.

    func receives a callback that records the open response. Closing that response
    unblocks a read stuck on a slow or silent server, so Cancel takes effect within
    about half a second instead of when the server finally finishes.
    """
    state = {"response": None, "aborted": False}
    result = {}
    done = Event()

    def remember_response(response):
        state["response"] = response
        if state["aborted"]:
            close_quietly(response)

    def target():
        try:
            result["value"] = func(remember_response)
        except BaseException as error:
            result["error"] = error
        finally:
            done.set()

    Thread(target=target, daemon=True).start()
    deadline = time.monotonic() + deadline_seconds
    while not done.wait(0.5):
        cancelled = cancel_requested()
        if cancelled or time.monotonic() >= deadline:
            state["aborted"] = True
            close_in_background(state["response"])
            if cancelled:
                check_cancel_requested()
                raise WorkflowCancelled("Đã huỷ quy trình theo yêu cầu.")
            raise TranslationApiError(timeout_message, retryable=True)
    if "error" in result:
        raise result["error"]
    return result.get("value")


def request_translation_completion(runtime_config, messages, *, stream):
    deadline_seconds = TRANSLATION_API_REQUEST_DEADLINE_SECONDS
    return run_cancellable(
        lambda remember_response: call_openai_compatible_api(
            runtime_config,
            messages,
            max_tokens=32_768,
            stream=stream,
            response_hook=remember_response,
        ),
        deadline_seconds=deadline_seconds,
        timeout_message=(
            f"API dịch không trả lời xong trong hạn chót {math.ceil(deadline_seconds / 60)} phút."
        ),
    )


def translation_cache_fingerprint(runtime_config):
    """Separate resumable chunks whenever the translation configuration changes."""
    source_lang = runtime_config.get("source_lang") or "ch"
    instructions = validate_translation_instructions(runtime_config.get("translation_instructions"))
    # Hash the exact prompt templates so any wording change starts fresh parts.
    prompt_template = (
        build_translation_system_prompt(source_lang)
        + build_translation_prompt("{SRT}", instructions, source_lang=source_lang)
    )
    identity = {
        "provider": str(runtime_config.get("provider") or ""),
        "base_url": str(runtime_config.get("base_url") or ""),
        "model": str(runtime_config.get("model") or ""),
        "prompt_version": TRANSLATION_PROMPT_VERSION,
        "source_lang": source_lang,
        "prompt_template": hashlib.sha256(prompt_template.encode("utf-8")).hexdigest(),
        "translation_instructions": instructions,
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def translate_srt_via_api(srt_path, runtime_config, output_srt=None, source_lang="ch"):
    check_cancel_requested()
    # A per-job copy: it carries the source language and remembers whether the
    # gateway rejected streaming, without changing the caller's settings dict.
    runtime_config = {**runtime_config, "source_lang": source_lang or "ch"}
    with open(srt_path, "r", encoding="utf-8-sig") as source_file:
        srt_content = source_file.read().strip()
    if not srt_content:
        raise ValueError("File phụ đề trống. Không có gì để dịch.")

    video_path = infer_translation_video_path(srt_path)
    video_duration = get_video_duration_seconds(video_path) if os.path.isfile(video_path) else None
    translation_chunks, is_chunked = split_srt_for_translation(
        srt_content,
        video_duration_seconds=video_duration,
    )
    if is_chunked:
        log_to_app(
            f"SRT dài hoặc dày; chia thành {len(translation_chunks)} đoạn để dịch qua "
            f"{runtime_config['label']}."
        )
    else:
        log_to_app(f"SRT nằm trong giới hạn một request; gửi một lần qua {runtime_config['label']}.")

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


@browser_automation
def translate_srt_via_gemini_edge(
    srt_path,
    notebook_url,
    output_srt=None,
    translation_instructions=None,
    source_lang="ch",
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
        log_to_app(f"SRT dài hoặc dày; chia thành {len(translation_chunks)} đoạn để dịch.")
    else:
        log_to_app("SRT nằm trong giới hạn một lượt; dịch toàn bộ trong một lần.")

    notebook_url = validate_notebook_url(notebook_url)
    edge_background = bool(load_app_config().get("edge_background", True))
    ensure_browser_running()
    label = browser_label(short=True)

    log_to_app(f"Kết nối Playwright vào {label} để thực hiện dịch phụ đề...")
    with get_sync_playwright()() as p:
        context = browser_context(p)
        notebook_id = urlparse(notebook_url).path.rstrip("/").rsplit("/", 1)[-1]
        gemini_page = next(
            (page for page in context.pages if notebook_id in page.url),
            None,
        )
        if not gemini_page:
            log_to_app(f"Notebook chưa mở. Đang mở Notebook trong {label}: {notebook_url}...")
            # The browser starts on one blank tab; reuse it so Notebook never gets a twin tab.
            gemini_page = reuse_blank_page(context)
            gemini_page.goto(notebook_url)
            gemini_page.wait_for_timeout(5000)

        log_to_app(f"Đã liên kết thành công với tab Notebook: '{gemini_page.title()}'")
        log_to_app("Đang chờ giao diện Notebook sẵn sàng...")
        prompt_input = wait_for_gemini_prompt(gemini_page)
        if prompt_input is None and browser_capture.page_needs_attention(gemini_page):
            require_visible_browser(context, gemini_page)
            log_to_app("Vui lòng hoàn tất xác minh trong trình duyệt; app chờ tối đa 3 phút.")
            prompt_input = wait_for_gemini_prompt(gemini_page, timeout_seconds=180)
        if prompt_input is None and edge_page_needs_login(gemini_page):
            require_visible_browser(context, gemini_page)
            raise EdgeLoginRequired(
                f"Phiên Google đã hết hạn. {label} đã được mở để đăng nhập; đăng nhập xong hãy bấm Chạy lại."
            )
        if prompt_input is None:
            if edge_background:
                require_visible_browser(context, gemini_page)
                raise EdgeLoginRequired(
                    f"App chưa tìm thấy khung chat của Notebook khi {label} chạy ẩn. "
                    f"{label} đã được mở để kiểm tra; khi khung chat hiện ra hãy bấm Chạy lại."
                )
            raise RuntimeError("Không tìm thấy khung nhập liệu chat của Gemini.")
        set_browser_window_visibility(context, gemini_page, not edge_background)

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
                        source_lang=source_lang,
                    )
                )
            except RuntimeError as error:
                if edge_background and "nút gửi" in str(error):
                    set_browser_window_visibility(context, gemini_page, True)
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

def resolve_douyin_aweme_id(request, notebook_url=""):
    """aweme ID of a Douyin SourceRequest: from the URL, over HTTP, or in the browser as a last resort."""
    if request.video_id:
        return request.video_id
    try:
        aweme_id = douyin_source.resolve_short_link(request.url)
        log_to_app(f"Đã giải mã link share Douyin thành video {aweme_id}.")
        return aweme_id
    except SourceError as error:
        log_to_app(
            f"Giải mã link share qua HTTP chưa được ({error}); thử lại bằng {browser_label(short=True)}.",
            "error",
        )
    resolved = resolve_url_via_browser(request.url, notebook_url)
    previous_page = " ".join(parse_qs(urlparse(resolved or "").query).get("previous_page", []))
    if "404" in previous_page:
        # Douyin sends guests from a video it will not show them to the feed, with
        # another video in the modal; that video is not the one the link points to.
        raise ValueError(
            "Douyin không mở được video của link share này (video đã bị xoá, ở chế độ riêng tư "
            "hoặc cần đăng nhập để xem)."
        )
    found = douyin_source.extract_aweme_id(resolved or "")
    if not found:
        raise ValueError("Không thể giải mã link share Douyin thành link video.")
    return found[1]


def resolve_video_request(video_url, notebook_url):
    """Compatibility wrapper (v1.5.x): (source_url, is_short_url, aweme_id) for a Douyin link."""
    request = detect_video_source(video_url)
    if request.site != "douyin":
        raise ValueError("Link này không phải video Douyin.")
    return request.url, request.short_link, resolve_douyin_aweme_id(request, notebook_url)


def get_js_runtime():
    """yt-dlp js_runtimes setting for Deno or Node 22+ in PATH (cached for 10 minutes)."""
    now = time.monotonic()
    if js_runtime_cache["checked_at"] and now - js_runtime_cache["checked_at"] < JS_RUNTIME_CACHE_SECONDS:
        return js_runtime_cache["value"]
    value = ytdlp_source.find_js_runtime(resolve_path_executable)
    js_runtime_cache.update({"checked_at": now, "value": value})
    return value


def saved_source_cookies(config):
    saved = config.get("source_cookies_protected")
    return dict(saved) if isinstance(saved, dict) else {}


def get_source_manual_cookie(site, config=None):
    """Cookie header pasted in Settings for one platform, decrypted; "" when none or unreadable."""
    config = load_app_config() if config is None else config
    protected = saved_source_cookies(config).get(site) or ""
    if not protected:
        return ""
    try:
        return cookie_utils.normalize_cookie_header(unprotect_config_secret(protected))
    except (OSError, UnicodeError, ValueError):
        return ""


def get_douyin_manual_cookie(config=None):
    return get_source_manual_cookie("douyin", config)


def source_cookies_set(config):
    saved = saved_source_cookies(config)
    return {key: bool(saved.get(key)) for key in SESSION_SITES}


def apply_source_cookie_updates(config, updates):
    """Encrypt the Cookie headers posted for each platform; None or "" removes one."""
    if not isinstance(updates, dict):
        raise ValueError("Cookie theo nền tảng phải là một JSON object.")
    saved = saved_source_cookies(config)
    for site, value in updates.items():
        if site not in SESSION_SITES:
            raise ValueError(f"Nền tảng không hỗ trợ cookie thủ công: {site}")
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Cookie {SESSION_SITES[site].label} không hợp lệ.")
        normalized = cookie_utils.normalize_cookie_header(value or "")
        if normalized:
            saved[site] = protect_text_secret(normalized)
        else:
            saved.pop(site, None)
    config["source_cookies_protected"] = saved


def validate_cookies_file_setting(value):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("Đường dẫn cookies.txt không hợp lệ.")
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    if not os.path.isabs(expanded):
        raise ValueError("Đường dẫn cookies.txt phải là đường dẫn tuyệt đối.")
    path = os.path.abspath(expanded)
    cookie_utils.load_cookie_file(path)
    return path


def get_cookies_file(config=None):
    config = load_app_config() if config is None else config
    try:
        return validate_cookies_file_setting(config.get("cookies_file") or "")
    except ValueError as error:
        log_to_app(f"Bỏ qua file cookies.txt trong Thiết lập: {error}", "error")
        return ""


@browser_lifecycle.leased(activity=False)
def read_browser_cookies(urls):
    """Cookies of the automation browser's profile for these URLs (context.cookies() over CDP).

    The browser must already run; yt-dlp never reads a browser cookie store itself, because
    Chrome's App-Bound Encryption makes --cookies-from-browser fail on Windows.
    """
    with get_sync_playwright()() as playwright:
        browser = playwright.chromium.connect_over_cdp(browser_debug_url())
        if not browser.contexts:
            return []
        return list(browser.contexts[0].cookies(list(urls)))


def prepare_ytdlp_cookie_file(site, *, manual_cookie=""):
    """(cookie file path or None, cleanup) for a yt-dlp download of this site.

    A cookies.txt chosen in Settings wins. Otherwise the site's cookies are exported from
    the running automation browser (context.cookies()) into a temporary Netscape file,
    except for YouTube, and a Cookie header pasted for the platform replaces the cookies
    with the same name.
    yt-dlp writes its cookie jar back to the file when it closes, so it always gets
    a temporary file and the user's cookies.txt is never modified.
    """
    configured = get_cookies_file()
    if configured:
        folder = tempfile.mkdtemp(prefix="vietsub-cookies-")
        path = os.path.join(folder, "cookies.txt")
        try:
            shutil.copyfile(configured, path)
            return path, lambda: shutil.rmtree(folder, ignore_errors=True)
        except OSError as error:
            # Same as an unreadable file in get_cookies_file: fall back to the browser cookies.
            shutil.rmtree(folder, ignore_errors=True)
            log_to_app(f"Bỏ qua file cookies.txt trong Thiết lập: {error}", "error")
    browser_cookies = []
    if site in SOURCE_COOKIE_URLS and check_browser_status():
        try:
            browser_cookies = read_browser_cookies(list(SOURCE_COOKIE_URLS[site]))
        except Exception as error:
            log_to_app(
                f"Không đọc được cookie từ {browser_label(short=True)}, tải không kèm cookie: {error}", "error"
            )
    if not browser_cookies and site in SOURCE_COOKIE_URLS:
        # Idle shutdown must not turn the next yt-dlp download into a guest request.
        with browser_lifecycle.lease(activity=False):
            browser_cookies = session_sites.cookies_for_urls(
                browser_session_cookies.get(browser_profile_dir(), []), SOURCE_COOKIE_URLS[site]
            )
    if manual_cookie and site in SESSION_SITES:
        overrides = cookie_utils.browser_cookies_from_header(manual_cookie, SESSION_SITES[site].cookie_domain)
        names = {item["name"] for item in overrides}
        browser_cookies = [item for item in browser_cookies if item.get("name") not in names] + overrides
    if not browser_cookies:
        return None, lambda: None
    folder = tempfile.mkdtemp(prefix="vietsub-cookies-")
    path = os.path.join(folder, "cookies.txt")
    cookie_utils.write_netscape_cookie_file(browser_cookies, path)
    return path, lambda: shutil.rmtree(folder, ignore_errors=True)


def find_cached_download(site, video_id, cache_dir=None):
    """A finished download of this video in the download folder, with its saved title."""
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(video_id)):
        return None
    cache_dir = cache_dir or get_download_cache_dir()
    stem = f"{site}-{video_id}"
    folder = os.path.join(cache_dir, stem)
    for extension in local_source.LOCAL_VIDEO_EXTENSIONS:
        candidate = os.path.join(folder, stem + extension)
        if os.path.isfile(candidate) and local_source.sniff_container(candidate) in ("mp4", "mkv"):
            title = ""
            try:
                metadata = json.loads(read_text_file(os.path.join(folder, stem + "_data.json")))
                title = str(metadata.get("title") or "") if isinstance(metadata, dict) else ""
            except (OSError, ValueError):
                pass
            return {"source_video": candidate, "video_title": title}
    return None


def find_legacy_douzy_video(aweme_id):
    """Path of this video in an old Douzy library, so it is reused instead of downloaded."""
    if not aweme_id or not os.path.isfile(DOUZY_DB):
        return ""
    try:
        connection = sqlite3.connect(DOUZY_DB, timeout=2)
        try:
            row = connection.execute(
                "SELECT file_path FROM aweme WHERE aweme_id = ? ORDER BY download_time DESC LIMIT 1",
                (aweme_id,),
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return ""
        path = resolve_video_file_path(row[0])
        return path if os.path.isfile(path) else ""
    except (OSError, sqlite3.Error):
        return ""


def save_douyin_assets(video, target_path, source_url):
    """Write <stem>_data.json and <stem>_cover.<ext> next to the MP4; failures are only logged."""
    stem = os.path.splitext(target_path)[0]
    metadata = {
        "site": "douyin",
        "video_id": video.aweme_id,
        "title": video.title,
        "author": video.author,
        "duration": video.duration_seconds,
        "source_url": source_url,
        "downloaded_at": int(time.time()),
    }
    try:
        write_text_atomic(stem + "_data.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    except OSError as error:
        log_to_app(f"Chưa lưu được metadata video: {error}", "error")
    for url in video.cover_urls:
        if not validate_douzy_thumbnail_url(url):
            continue
        try:
            download_thumbnail(url, stem + "_cover")
            return
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as error:
            log_to_app(f"Chưa tải được ảnh bìa: {error}", "error")


def raise_if_download_cancelled(error):
    check_cancel_requested()
    raise WorkflowCancelled("Đã huỷ quy trình theo yêu cầu.") from error


def douyin_login_notice(config=None):
    browser = browser_label(get_browser_kind(config) if config is not None else None, short=True)
    return (
        "Douyin yêu cầu đăng nhập để tải video này. "
        f"Hãy bấm 'Mở Douyin trên {browser}' trong Thiết lập, đăng nhập 1 lần rồi thử lại."
    )


def douyin_captcha_notice():
    return douyin_source.CAPTCHA_NOTICE.format(browser=browser_label(short=True))


def browser_attention_notice():
    return browser_capture.ATTENTION_NOTICE.format(browser=browser_label(short=True))


def douyin_download_result(aweme_id, path, title):
    return {
        "source_url": douyin_source.canonical_video_url(aweme_id),
        "video_id": aweme_id,
        "source_video": path,
        "video_title": title,
        "source_type": "douyin",
    }


def stream_douyin_video(video, target_path, headers, cookie_jar):
    set_download_progress(phase="download", notice="", connections=None)
    if getattr(video, "play_label", ""):
        log_to_app(f"Chọn bản {video.play_label} (đủ nét cho OCR, nhẹ hơn bản bitrate cao).")
    on_progress, on_notice = bound_download_callbacks()
    started = time.monotonic()
    try:
        result = http_download.stream_download(
            video.play_urls,
            target_path,
            headers=headers,
            cookie_jar=cookie_jar,
            on_progress=on_progress,
            on_notice=on_notice,
            on_connections=bound_download_connections_callback(),
            is_cancelled=cancel_requested,
            validate=local_source.require_mp4_header,
        )
    except DownloadCancelledError as error:
        raise_if_download_cancelled(error)
    except SourceError as error:
        raise VideoDownloadError(str(error)) from error
    log_download_summary(result, started, "video Douyin")


def download_douyin_via_share_page(aweme_id, target_path):
    """Tier 1: the public share page over HTTP with an iPhone User-Agent; no browser is started."""
    set_download_progress(phase="capture", notice="")
    log_to_app("Đọc trang chia sẻ Douyin qua HTTP (chưa cần mở trình duyệt)...")
    cookie_jar = cookie_utils.jar_from_browser_cookies([])
    try:
        video = douyin_source.video_from_share_page(aweme_id, cookie_jar=cookie_jar)
    except douyin_source.ImagePostError as error:
        raise PostHasNoVideo(str(error)) from error
    except SourceError as error:
        raise VideoDownloadError(str(error)) from error
    headers = {
        "User-Agent": douyin_source.MOBILE_USER_AGENT,
        "Referer": douyin_source.SHARE_REFERER,
        "Accept": "*/*",
    }
    log_to_app(f"Trang chia sẻ có địa chỉ video: {video.title}. Đang tải file MP4 không watermark...")
    stream_douyin_video(video, target_path, headers, cookie_jar)
    save_douyin_assets(video, target_path, douyin_source.canonical_video_url(aweme_id))
    log_to_app(f"Đã tải xong video Douyin: {target_path}")
    return douyin_download_result(aweme_id, target_path, video.title)


@browser_automation
def download_douyin_via_browser(aweme_id, target_path, notebook_url="", *, quiet):
    """Tier 2: Douyin's own page signs its API calls in the automation browser; the app
    reads the video data that page receives and streams the clean MP4.

    A redirect away from the video or a lasting login popup ends the wait at once with
    SourceLoginRequired, instead of waiting for the deadline.
    """
    config = load_app_config()
    edge_background = bool(config.get("edge_background", True))
    manual_cookie = get_douyin_manual_cookie(config)
    label = browser_label(get_browser_kind(config), short=True)
    page_url = douyin_source.jingxuan_url(aweme_id)
    set_download_progress(phase="capture", notice="")
    try:
        ensure_browser_running(config=config)
    except BrowserStartError as error:
        raise VideoDownloadError(str(error)) from error
    log_to_app(f"Mở video trong {label} để đọc dữ liệu: {page_url}")
    notice = douyin_captcha_notice()
    with get_sync_playwright()() as playwright:
        context = browser_context(playwright)
        if manual_cookie:
            # The cookie pasted in Settings replaces the matching cookies of the app's profile.
            context.add_cookies(cookie_utils.browser_cookies_from_header(manual_cookie))

        def on_captcha(active, page):
            if active:
                require_visible_browser(context, page)
                set_download_progress(phase="captcha", notice=notice)
                log_to_app(f"{notice} (app chờ tối đa 3 phút).", "error")
            else:
                set_download_progress(phase="capture", notice="")
                log_to_app("Đã qua bước xác minh; tiếp tục đọc dữ liệu video.")
                if edge_background:
                    set_browser_window_visibility(context, page, False)

        try:
            capture = douyin_source.capture_video_from_page(
                context,
                aweme_id,
                url=page_url,
                is_cancelled=cancel_requested,
                on_captcha=on_captcha,
                allow_captcha_wait=not quiet,
            )
        except DownloadCancelledError as error:
            raise_if_download_cancelled(error)
        except douyin_source.LoginRequiredError as error:
            log_to_app(f"{error} Dừng chờ ngay.", "error")
            raise SourceLoginRequired(douyin_login_notice(config)) from error
        browser_cookies = context.cookies([DOUYIN_HOME_URL])

    if capture.detail is not None:
        try:
            video = douyin_source.parse_aweme_detail(capture.detail)
        except douyin_source.ImagePostError as error:
            raise PostHasNoVideo(str(error)) from error
    else:
        video = douyin_source.DouyinVideo(
            aweme_id=aweme_id,
            title=douyin_source.title_from_page(capture.page_title, aweme_id),
            play_urls=list(capture.dom_urls),
        )
    cookie_jar = cookie_utils.jar_from_browser_cookies(browser_cookies)
    if manual_cookie:
        cookie_utils.add_header_cookies(cookie_jar, manual_cookie)
    headers = {
        "User-Agent": capture.user_agent or douyin_source.DEFAULT_USER_AGENT,
        "Referer": douyin_source.DOUYIN_REFERER,
        "Accept": "*/*",
    }
    log_to_app(f"Đã đọc dữ liệu video: {video.title}. Đang tải file MP4 không watermark...")
    stream_douyin_video(video, target_path, headers, cookie_jar)
    save_douyin_assets(video, target_path, douyin_source.canonical_video_url(aweme_id))
    log_to_app(f"Đã tải xong video Douyin: {target_path}")
    return douyin_download_result(aweme_id, target_path, video.title)


def run_ytdlp_download(url, site, cache_dir, *, manual_cookie=""):
    set_download_progress(connections=None)
    cookie_file, cleanup = prepare_ytdlp_cookie_file(site, manual_cookie=manual_cookie)
    try:
        return ytdlp_source.download_with_ytdlp(
            url,
            cache_dir,
            site=site,
            cookie_file=cookie_file,
            ffmpeg_path=resolve_ffmpeg_executable(),
            js_runtimes=get_js_runtime(),
            on_progress=report_download_progress,
            is_cancelled=cancel_requested,
            log=lambda message: log_to_app(message, "error"),
        )
    except DownloadCancelledError as error:
        raise_if_download_cancelled(error)
    finally:
        cleanup()


def download_douyin_via_ytdlp(aweme_id, cache_dir):
    """Tier 3: yt-dlp with fresh cookies exported from the automation browser through CDP
    (or the pasted cookie / cookies.txt), handed over in a temporary Netscape file."""
    canonical_url = douyin_source.canonical_video_url(aweme_id)
    log_to_app(f"Thử tải Douyin bằng yt-dlp với cookie xuất từ {browser_label(short=True)}...")
    try:
        result = run_ytdlp_download(
            canonical_url, "douyin", cache_dir, manual_cookie=get_douyin_manual_cookie()
        )
    except SourceError as error:
        raise VideoDownloadError(str(error)) from error
    return {
        "source_url": canonical_url,
        "video_id": result["video_id"] or aweme_id,
        "source_video": result["path"],
        "video_title": result["title"] or f"Douyin {aweme_id}",
        "source_type": "douyin",
    }


def download_douyin_video(request, notebook_url):
    """Cache, old Douzy library, then the share page (HTTP), the browser page, and yt-dlp."""
    aweme_id = resolve_douyin_aweme_id(request, notebook_url)
    cache_dir = get_download_cache_dir()
    cached = find_cached_download("douyin", aweme_id, cache_dir)
    if cached:
        log_to_app("Video này đã có trong thư mục tải tạm; dùng lại, không tải lại.")
        return douyin_download_result(
            aweme_id, cached["source_video"], cached["video_title"] or f"Douyin {aweme_id}"
        )
    legacy_video = find_legacy_douzy_video(aweme_id)
    if legacy_video:
        log_to_app("Tìm thấy video này trong thư viện Douzy cũ; dùng lại file đã có.")
        return douyin_download_result(
            aweme_id, legacy_video, get_video_title(aweme_id) or f"Douyin {aweme_id}"
        )
    target_path = os.path.join(cache_dir, f"douyin-{aweme_id}", f"douyin-{aweme_id}.mp4")
    quiet = bool(getattr(workflow_context, "quiet", False))
    tiers = (
        ("HTTP", lambda: download_douyin_via_share_page(aweme_id, target_path)),
        (browser_label(short=True), lambda: download_douyin_via_browser(aweme_id, target_path, notebook_url, quiet=quiet)),
    )
    errors = []
    login_wall = None
    for name, attempt in tiers:
        try:
            return attempt()
        except (WorkflowCancelled, PostHasNoVideo):
            raise
        except SourceLoginRequired as error:
            check_cancel_requested()
            login_wall = error
            break
        except Exception as error:
            check_cancel_requested()
            errors.append(f"{name}: {error}")
            if name == "HTTP":
                # Expected for most videos today: the share page loads its data with JavaScript.
                log_to_app(f"Chưa tải được qua HTTP ({error}); chuyển sang {browser_label(short=True)}.")
            else:
                log_to_app(f"Chưa tải được qua {name} ({error}); thử cách tiếp theo.", "error")
    if login_wall is not None and not (get_douyin_manual_cookie() or get_cookies_file()):
        # yt-dlp would get the same guest cookies; only a pasted login cookie can help.
        raise login_wall
    try:
        return download_douyin_via_ytdlp(aweme_id, cache_dir)
    except WorkflowCancelled:
        raise
    except Exception as error:
        check_cancel_requested()
        errors.append(f"yt-dlp: {error}")
    if login_wall is not None:
        raise SourceLoginRequired(f"{login_wall} ({errors[-1]})")
    raise VideoDownloadError("Không tải được video Douyin. " + " | ".join(errors))


def save_source_metadata(video_path, *, site, video_id, title, source_url, duration=None):
    """<stem>_data.json next to a download: the title survives a cache hit and goes into the project."""
    metadata = {
        "site": site,
        "video_id": video_id,
        "title": title,
        "duration": duration,
        "source_url": source_url,
        "downloaded_at": int(time.time()),
    }
    try:
        write_text_atomic(
            os.path.splitext(video_path)[0] + "_data.json",
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )
    except OSError as error:
        log_to_app(f"Chưa lưu được metadata video: {error}", "error")


@browser_automation
def download_via_browser_capture(request, target_path, notebook_url, *, quiet):
    """Open the post in the automation browser's profile and stream the video file the page plays."""
    site = SESSION_SITES.get(request.site)
    label = SOURCE_LABELS.get(request.site, request.site)
    home_url = site.home_url if site else request.url
    config = load_app_config()
    edge_background = bool(config.get("edge_background", True))
    manual_cookie = get_source_manual_cookie(request.site, config) if site else ""
    browser = browser_label(get_browser_kind(config), short=True)
    set_download_progress(phase="capture", notice="")
    try:
        ensure_browser_running(config=config)
    except BrowserStartError as error:
        raise VideoDownloadError(str(error)) from error
    log_to_app(f"Mở trang {label} trong {browser} để tìm file video: {request.url}")
    notice = browser_attention_notice()
    with get_sync_playwright()() as playwright:
        context = browser_context(playwright)
        if manual_cookie:
            context.add_cookies(cookie_utils.browser_cookies_from_header(manual_cookie, site.cookie_domain))

        def on_attention(active, page):
            if active:
                require_visible_browser(context, page)
                set_download_progress(phase="captcha", notice=notice)
                log_to_app(f"{notice} (app chờ tối đa 3 phút).", "error")
            else:
                set_download_progress(phase="capture", notice="")
                if edge_background:
                    set_browser_window_visibility(context, page, False)

        try:
            capture = browser_capture.capture_media_from_page(
                context,
                request.url,
                is_cancelled=cancel_requested,
                on_attention=on_attention,
                allow_attention_wait=not quiet,
                require_attention_signal=True,
            )
        except DownloadCancelledError as error:
            raise_if_download_cancelled(error)
        cookie_urls = list(site.cookie_urls) if site else [request.url]
        browser_cookies = context.cookies(cookie_urls)

    resolved = request
    if request.short_link and capture.final_url:
        try:
            resolved = web_source.parse_web_url(capture.final_url)
        except ValueError:
            resolved = request
    cookie_jar = cookie_utils.jar_from_browser_cookies(browser_cookies)
    if manual_cookie:
        cookie_utils.add_header_cookies(cookie_jar, manual_cookie, site.cookie_domain)
    headers = {
        "User-Agent": capture.user_agent or douyin_source.DEFAULT_USER_AGENT,
        "Referer": home_url,
        "Accept": "*/*",
    }
    log_to_app(f"Đã tìm thấy file video trên trang {label}. Đang tải...")
    set_download_progress(phase="download", notice="", connections=None)
    on_progress, on_notice = bound_download_callbacks()
    started = time.monotonic()
    try:
        result = http_download.stream_download(
            capture.media_urls,
            target_path,
            headers=headers,
            cookie_jar=cookie_jar,
            on_progress=on_progress,
            on_notice=on_notice,
            on_connections=bound_download_connections_callback(),
            is_cancelled=cancel_requested,
            validate=local_source.require_mp4_header,
        )
    except DownloadCancelledError as error:
        raise_if_download_cancelled(error)
    except SourceError as error:
        raise VideoDownloadError(str(error)) from error
    log_download_summary(result, started, f"video {label}")
    video_id = resolved.video_id or request.video_id
    title = capture.title or f"{label} {video_id}"
    save_source_metadata(target_path, site=request.site, video_id=video_id, title=title, source_url=resolved.url)
    log_to_app(f"Đã tải xong video {label}: {target_path}")
    return {
        "source_url": resolved.url,
        "video_id": video_id,
        "source_video": target_path,
        "video_title": title,
        "source_type": request.site,
    }


def short_link_code(url):
    """Last path segment of a share link, used to name its download folder."""
    code = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return code if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code or "") else uuid.uuid4().hex[:12]


def download_web_video(request, notebook_url=""):
    """TikTok, YouTube, Bilibili and Xiaohongshu through yt-dlp; Kuaishou (and the
    Xiaohongshu fallback) through the post's page in the automation browser."""
    label = SOURCE_LABELS.get(request.site, request.site)
    if request.short_link:
        try:
            request = web_source.resolve_short_link(request)
        except SourceError as error:
            log_to_app(f"Giải mã link share {label} qua HTTP chưa được ({error}); mở thẳng link.", "error")
    cache_dir = get_download_cache_dir()
    if request.video_id:
        cached = find_cached_download(request.site, request.video_id, cache_dir)
        if cached:
            log_to_app("Video này đã có trong thư mục tải tạm; dùng lại, không tải lại.")
            return {
                "source_url": request.url,
                "video_id": request.video_id,
                "source_video": cached["source_video"],
                "video_title": cached["video_title"] or f"{label} {request.video_id}",
                "source_type": request.site,
            }
    errors = []
    for tier in WEB_DOWNLOAD_STRATEGY.get(request.site, ("ytdlp",)):
        try:
            if tier == "browser":
                folder_id = request.video_id or short_link_code(request.url)
                stem = f"{request.site}-{folder_id}"
                target_path = os.path.join(cache_dir, stem, stem + ".mp4")
                quiet = bool(getattr(workflow_context, "quiet", False))
                return download_via_browser_capture(request, target_path, notebook_url, quiet=quiet)
            log_to_app(f"Đang tải video {label} bằng yt-dlp: {request.url}")
            result = run_ytdlp_download(
                request.url,
                request.site,
                cache_dir,
                manual_cookie=get_source_manual_cookie(request.site) if request.site in SESSION_SITES else "",
            )
            video_id = result["video_id"] or request.video_id
            title = result["title"] or f"{label} {video_id}"
            save_source_metadata(
                result["path"], site=request.site, video_id=video_id, title=title,
                source_url=request.url, duration=result.get("duration"),
            )
            return {
                "source_url": request.url,
                "video_id": video_id,
                "source_video": result["path"],
                "video_title": title,
                "source_type": request.site,
            }
        except WorkflowCancelled:
            raise
        except Exception as error:
            # Playwright errors are not RuntimeError subclasses; any failure moves to the next tier.
            check_cancel_requested()
            message = str(error)
            if tier == "ytdlp" and request.site == "youtube" and not get_js_runtime():
                message += " YouTube cần Deno 2.3+ hoặc Node 22+ trong PATH; hãy cài một trong hai rồi chạy lại."
            if tier == "ytdlp" and request.site == "xiaohongshu" and NO_VIDEO_PATTERN.search(message):
                raise VideoDownloadError(
                    "Ghi chú Xiaohongshu này không có video (bài đăng ảnh); không có gì để OCR."
                ) from error
            method = browser_label(short=True) if tier == "browser" else "yt-dlp"
            errors.append(f"{method}: {message}")
            log_to_app(f"Chưa tải được bằng {method}: {message}", "error")
    if len(errors) == 1:
        raise VideoDownloadError(errors[0].split(": ", 1)[1])
    raise VideoDownloadError(f"Không tải được video {label}. " + " | ".join(errors))


def download_video_source(video_url, notebook_url):
    """Prepare the video for a link: {source_url, video_id, source_video, video_title, source_type}.

    Douyin tries the cache, the public share page over HTTP, the video page in the
    automation browser (Chrome by default, with its own profile), then yt-dlp;
    Kuaishou is captured in the browser; TikTok, YouTube, Bilibili and Xiaohongshu
    use yt-dlp, with the browser as the Xiaohongshu fallback.
    """
    request = detect_video_source(video_url)
    if request.site == "douyin":
        return download_douyin_video(request, notebook_url)
    return download_web_video(request, notebook_url)


def prune_download_cache(referenced_paths=None, cache_dir=None):
    """Delete stale, unreferenced download folders; only folders this app names are touched."""
    cache_dir = cache_dir or get_download_cache_dir()
    if not os.path.isdir(cache_dir):
        return 0
    referenced = {
        os.path.normcase(os.path.abspath(path))
        for path in (referenced_paths or [])
        if path
    }
    with preview_registry_lock:
        referenced.update(
            os.path.normcase(os.path.abspath(preview["source_video"]))
            for preview in preview_registry.values()
            if preview.get("source_video")
        )
    cutoff = time.time() - PREVIEW_TTL_SECONDS
    removed = 0
    for name in os.listdir(cache_dir):
        folder = os.path.join(cache_dir, name)
        if not os.path.isdir(folder) or not DOWNLOAD_FOLDER_PATTERN.fullmatch(name):
            continue
        try:
            files = [os.path.join(folder, item) for item in os.listdir(folder)]
            if any(path.endswith(".vietsub-keep") or os.path.normcase(os.path.abspath(path)) in referenced for path in files):
                continue
            newest = max((os.path.getmtime(path) for path in files), default=os.path.getmtime(folder))
        except OSError:
            continue
        if newest >= cutoff:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        if not os.path.exists(folder):
            removed += 1
    return removed


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


def ensure_preview_admission_locked():
    """Raise when a preview must not start; call while holding operation_admission_lock.

    Between two jobs workflow_lock is free while the worker waits for the
    prefetch download, so the worker and prefetch threads are checked too.
    Otherwise a preview could start a second download and clear a cancel.
    """
    if shutdown_event.is_set():
        raise RuntimeError("Ứng dụng đang đóng; không thể tạo preview mới.")
    if update_install_lock.locked():
        raise RuntimeError("Ứng dụng đang chuẩn bị cập nhật; không thể tạo preview mới.")
    if workflow_lock.locked() or queue_worker_active() or prefetch_active():
        raise RuntimeError("Hãy chờ hàng đợi hiện tại chạy xong trước khi tạo preview mới.")
    if not preview_lock.acquire(blocking=False):
        raise RuntimeError("Một preview khác đang được chuẩn bị.")


def preview_payload(preview, resolution=None):
    payload = {
        "id": preview["id"],
        "source_url": preview["source_url"],
        "video_id": preview["video_id"],
        "video_title": preview["video_title"],
        "crop_coords": preview["crop_coords"],
        "source_type": preview["source_type"],
        "video_url": f"/api/previews/{preview['id']}/video",
        "video_metadata": preview.get("video_metadata"),
    }
    if preview.get("original_name"):
        payload["original_name"] = preview["original_name"]
    if resolution:
        # Lets a video the WebView cannot play (H.265, some MKV) still join the queue.
        payload["video_resolution"] = {"width": int(resolution[0]), "height": int(resolution[1])}
    return payload


def create_video_preview(video_url):
    with operation_admission_lock:
        ensure_preview_admission_locked()
    cancel_event.clear()
    try:
        config = load_app_config()
        notebook_url = get_translation_browser_url(config)
        reset_download_progress(source_type_for_url(video_url))
        try:
            preview = download_video_source(video_url, notebook_url)
        finally:
            clear_download_progress()
        preview_id = uuid.uuid4().hex
        preview.update({
            "id": preview_id,
            "created_at": time.time(),
            "crop_coords": get_saved_crop_coords(),
            "source_type": preview.get("source_type", "douyin"),
            "managed_source": False,
        })
        preview["video_metadata"] = get_video_metadata(preview["source_video"])
        with preview_registry_lock:
            preview_registry[preview_id] = preview
        return preview_payload(preview, get_video_resolution(preview["source_video"]))
    finally:
        cancel_event.clear()
        preview_lock.release()


def create_local_path_preview(path):
    """Preview a video picked in the native file dialog. The file is used where it is:
    nothing is uploaded or copied to %TEMP% or the app cache."""
    info = local_source.validate_local_video_path(path)
    with operation_admission_lock:
        ensure_preview_admission_locked()
    try:
        preview_id = uuid.uuid4().hex
        preview = {
            "id": preview_id,
            "created_at": time.time(),
            "source_url": f"local://{preview_id}",
            "video_id": f"local-{preview_id[:12]}",
            "source_video": info["path"],
            "video_title": info["title"],
            "original_name": info["name"],
            "crop_coords": get_saved_crop_coords(),
            "source_type": "local",
            "managed_source": False,
        }
        preview["video_metadata"] = get_video_metadata(preview["source_video"])
        with preview_registry_lock:
            preview_registry[preview_id] = preview
        return preview_payload(preview, get_video_resolution(info["path"]))
    finally:
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
        ensure_preview_admission_locked()
    try:
        original_name = os.path.basename((upload.filename or "").replace("\\", "/"))
        extension = os.path.splitext(original_name)[1].lower()
        if extension not in local_source.LOCAL_VIDEO_EXTENSIONS:
            raise ValueError("Hiện tại video trên máy phải là file MP4 hoặc MKV.")

        preview_id = uuid.uuid4().hex
        os.makedirs(LOCAL_VIDEO_DIR, exist_ok=True)
        destination = os.path.join(LOCAL_VIDEO_DIR, f"{preview_id}{extension}")
        temporary_path = f"{destination}.{uuid.uuid4().hex}.part"
        try:
            upload.save(temporary_path)
            if not os.path.isfile(temporary_path):
                raise ValueError("File video đang trống hoặc không đọc được.")
            if extension == ".mp4":
                validate_local_mp4_file(temporary_path)
            elif local_source.sniff_container(temporary_path) != "mkv":
                raise ValueError("File đã chọn không có cấu trúc MKV hợp lệ.")
            elif os.path.getsize(temporary_path) > MAX_LOCAL_VIDEO_BYTES:
                raise ValueError("Video vượt quá giới hạn 8 GiB.")
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
        preview["video_metadata"] = get_video_metadata(preview["source_video"])
        with preview_registry_lock:
            preview_registry[preview_id] = preview
        return preview_payload(preview)
    finally:
        preview_lock.release()


def get_job_retry_step(job):
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    available_video = result.get("video") or ""
    if not os.path.isfile(str(available_video)):
        available_video = result.get("video_destination") or ""
    if os.path.isfile(str(available_video)):
        translation_failed = job.get("step") == "translate" or job.get("resume_step") == "translate"
        if translation_failed and os.path.isfile(str(result.get("raw_srt") or "")):
            return "translate"
        return "ocr"
    return "download"


def job_disk_bytes(job, keep_srt=True):
    try:
        return storage.reclaimable_bytes(storage.inventory(storage.candidates(job, keep_srt)))
    except OSError:
        return 0


def editor_uses_job(job_id):
    adapter = globals().get("editor_api")
    upload = globals().get("uploader_api")
    return bool((adapter and adapter.jobs.active(job_id)) or (upload and upload.active(job_id)))


def editor_protected_paths():
    adapter = globals().get("editor_api")
    paths = adapter.jobs.protected_paths() if adapter else set()
    upload = globals().get("uploader_api")
    return paths | (upload.protected_paths() if upload else set())


def bulk_delete_jobs(ids, *, delete_files=False, keep_srt=True, dry_run=False):
    if not isinstance(ids, list) or not ids or len(ids) > MAX_IN_MEMORY_JOBS:
        raise ValueError("ids phải là danh sách ID không rỗng trong giới hạn hàng đợi.")
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ValueError("ID job không hợp lệ.")
    if any(type(value) is not bool for value in (delete_files, keep_srt, dry_run)):
        raise ValueError("delete_files, keep_srt và dry_run phải là boolean.")
    ids = list(dict.fromkeys(ids))
    failures, deleted_ids, freed_bytes = [], [], 0
    with operation_admission_lock, queue_state_lock:
        jobs_by_id = {job["id"]: job for job in workflow_jobs}
        selected = []
        for job_id in ids:
            job = jobs_by_id.get(job_id)
            if not job:
                failures.append({"id": job_id, "error": "Không tìm thấy job."})
            elif (job.get("status") == "running" or job_id in {current_job_id, prefetch_job_id}
                  or editor_uses_job(job_id)):
                failures.append({"id": job_id, "error": "Job đang được VietSub/Auto Edit xử lý; chưa thể xoá."})
            else:
                selected.append(job)
        selected_ids = {job["id"] for job in selected}
        plans, planned_items = {}, {}
        protected = editor_protected_paths()
        if delete_files:
            if preview_lock.locked():
                raise RuntimeError("Hãy chờ chuẩn bị preview xong trước khi xoá file.")
            for job in workflow_jobs:
                if job["id"] not in selected_ids:
                    protected.update(storage.candidates(job, keep_srt=False))
            for job in selected:
                paths = set()
                try:
                    paths = storage.candidates(job, keep_srt) - protected
                    planned_items[job["id"]] = storage.inventory(paths)
                    plans[job["id"]] = paths
                except OSError as error:
                    protected.update(paths)
                    failures.append({"id": job["id"], "error": str(error)})
            # A failed inventory protects all its recorded assets, including SRT,
            # from a different selected job. Healthy jobs can still be cleaned.
            for job in selected:
                if job["id"] not in plans:
                    protected.update(storage.video_paths(job))
                    result = job.get("result") or {}
                    protected.update(storage.canonical(result[key]) for key in ("raw_srt", "translated_srt")
                                     if isinstance(result.get(key), str) and result[key])
            plans = {key: paths - protected for key, paths in plans.items()}
        items = {path: info for key, inventory in planned_items.items()
                 for path, info in inventory.items() if path in plans[key]}
        estimate = storage.reclaimable_bytes(items)
        if dry_run:
            eligible = sum(not delete_files or job["id"] in plans for job in selected)
            return {"selected_count": eligible, "reclaimable_bytes": estimate,
                    "failures": failures, "deleted_ids": [], "freed_bytes": 0}
        # Fail before any unlink if durable queue state is unavailable. Records
        # with unlink failures remain retryable; missing files are idempotent.
        if items:
            try:
                persist_queue_state_locked()
            except (OSError, UnicodeError) as error:
                raise QueuePersistenceError(str(error)) from error
        file_errors = []
        if items:
            freed_bytes, file_errors = storage.delete_inventory(items)
        failed_paths = {entry["path"] for entry in file_errors}
        for job in selected:
            job_id = job["id"]
            if delete_files and job_id not in plans:
                continue
            errors = [entry["error"] for entry in file_errors if entry["path"] in plans.get(job_id, ())]
            if errors:
                failures.append({"id": job_id, "error": "; ".join(errors)})
            else:
                deleted_ids.append(job_id)
        if deleted_ids and not delete_files:
            # A record-only choice also survives the next startup cache prune.
            # Pin only app-managed inputs, never write beside external local files.
            for job in selected:
                if job["id"] not in deleted_ids:
                    continue
                for path in storage.video_paths(job):
                    if (is_managed_local_video(path) or path_is_within(path, get_download_cache_dir())) and storage.safe_regular(path):
                        with open(path + ".vietsub-keep", "a", encoding="utf-8"):
                            pass
        if deleted_ids:
            candidate_jobs = [job for job in workflow_jobs if job["id"] not in deleted_ids]
            commit_queue_jobs_locked(candidate_jobs)
    # Expire previews only for paths actually removed. Record-only deletion
    # deliberately leaves managed uploads and all other files untouched.
    if delete_files:
        with preview_registry_lock:
            for preview_id, preview in list(preview_registry.items()):
                path = preview.get("source_video")
                if path and storage.canonical(path) in items and storage.canonical(path) not in failed_paths:
                    preview_registry.pop(preview_id, None)
    return {"deleted_ids": deleted_ids, "freed_bytes": freed_bytes,
            "reclaimable_bytes": estimate, "failures": failures, "jobs": queue_snapshot()}


def serialize_job(job):
    serialized = {
        key: job.get(key)
        for key in (
            "id", "source_url", "video_id", "video_title", "project_name", "ocr_lang",
            "crop_coords", "video_resolution", "preview_id", "status", "step", "error",
            "error_code", "result",
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
    serialized["disk_bytes"] = job_disk_bytes(job)
    serialized["disk_bytes_with_srt"] = job_disk_bytes(job, keep_srt=False)
    return serialized


PERSISTED_JOB_KEYS = (
    "id", "source_url", "video_id", "video_title", "source_video", "project_name",
    "ocr_lang", "crop_coords", "video_resolution", "preview_id", "use_saved_crop",
    "source_type", "managed_source", "status", "step", "error", "error_code", "result", "result_ready",
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
    if app.config.get("TESTING") or not queue_persistence_enabled:
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
    if (source_type == "local" and not result.get("source_video_deleted")
            and not os.path.isfile(source_video) and not os.path.isfile(str(result.get("video") or ""))):
        status = "error"
        error = "Video local không còn tồn tại. Hãy chọn lại file MP4."

    error_code = value.get("error_code") if value.get("error_code") in JOB_ERROR_CODES else ""
    result_ready = bool(value.get("result_ready", False))
    if status == "success" and (
        "result_ready" not in value or result_ready
    ):
        # Recheck successful files so older false-positive translations become retryable.
        result_ready = project_result_ready(result)
        if not result_ready:
            status = "error"
            if project_files_present(result) and os.path.isdir(str(result.get("project_dir") or "")):
                error_code = "translation_invalid"
                error = (
                    "File phụ đề dịch không hợp lệ (thiếu block, sai timestamp hoặc chưa được dịch). "
                    "Hãy chạy lại bước dịch."
                )
            else:
                error_code = "project_missing"
                error = (
                    "Không tìm thấy file dự án ở vị trí cũ; thư mục có thể đã bị di chuyển, "
                    "đổi tên hoặc xoá. Hãy trả thư mục về vị trí cũ rồi mở lại app, hoặc xoá job này."
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
        "error_code": error_code if status in {"error", "cancelled"} else "",
        "needs_edge_login": bool(value.get("needs_edge_login", False)),
        "resume_step": (
            "translate"
            if status == "error"
            # Jobs saved before v1.5.4 only carry the legacy Vietnamese message.
            and (error_code == "translation_invalid" or "tiếng Trung" in error)
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
    # The random suffix keeps two quarantines within the same second apart.
    quarantined = f"{path}.corrupt-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    try:
        os.replace(path, quarantined)
        log_to_app(f"Đã tách file dữ liệu hỏng để không làm mất dữ liệu: {quarantined}", "error")
        return quarantined
    except OSError as error:
        log_to_app(f"Không thể tách file dữ liệu hỏng: {error}", "error")
        return ""


def load_queue_file(path):
    """Load saved jobs. Raises OSError when the file exists but cannot be read now."""
    if not os.path.isfile(path):
        return []
    try:
        text = read_text_file_with_retry(path)
    except FileNotFoundError:
        return []
    try:
        payload = json.loads(text)
    except ValueError:
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
    if (not is_managed_local_video(path) or os.path.exists(path + ".vietsub-keep")
            or storage.canonical(path) in editor_protected_paths()):
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
                and os.path.splitext(name)[1].lower() in local_source.LOCAL_VIDEO_EXTENSIONS
                and not os.path.exists(path + ".vietsub-keep")
                and os.path.normcase(os.path.abspath(path)) not in referenced
                and os.path.getmtime(path) < cutoff
            ):
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def initialize_queue_state():
    global queue_revision, queue_state_initialized, queue_persistence_enabled
    with queue_state_lock:
        if queue_state_initialized:
            return
        queue_state_initialized = True
        try:
            loaded_jobs = load_queue_file(QUEUE_PATH)
        except OSError as error:
            # Keep the unreadable file untouched for this session; never replace it
            # and never prune local videos whose queue references are unknown.
            log_to_app(
                f"Chưa đọc được file hàng đợi ({error}). Phiên này sẽ không ghi đè file cũ; "
                "hãy đóng các chương trình đang giữ file rồi mở lại app.",
                "error",
            )
            queue_revision += 1
            return
        workflow_jobs[:] = loaded_jobs
        queue_revision += 1
        queue_persistence_enabled = True
        referenced_paths = [job.get("source_video") for job in workflow_jobs]
        try:
            persist_queue_state_locked()
        except OSError as error:
            log_to_app(f"Chưa thể chuẩn hoá file hàng đợi: {error}", "error")
    removed = prune_orphaned_local_videos(referenced_paths)
    if removed:
        log_to_app(f"Đã dọn {removed} video cache mồ côi quá hạn.")
    try:
        removed_downloads = prune_download_cache(referenced_paths)
    except OSError as error:
        removed_downloads = 0
        log_to_app(f"Chưa dọn được thư mục tải tạm: {error}", "error")
    if removed_downloads:
        log_to_app(f"Đã dọn {removed_downloads} video tải tạm không còn dùng.")


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
        request = detect_video_source(video_url)
        source_url = request.url
        source_type = request.site
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
        "error_code": "",
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
    source_url = detect_video_source(video_url).url
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
        # Wait until the running job is past its download step so two downloads
        # never run at the same time.
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
                    and job.get("source_type", "douyin") in DOWNLOAD_SOURCE_TYPES
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
    if app.config.get("TESTING") or shutdown_event.is_set():
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
        # This job needs its own download; stop the unrelated prefetch first.
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
    # Douyin and Kuaishou pages open in the automation browser, like Notebook translation.
    needs_browser = translation_config.get("provider") == "notebook" or bool(source_types & BROWSER_SOURCE_TYPES)
    if needs_browser and not resolve_browser_executable():
        missing.append(browser_label())
    if source_types & YTDLP_SOURCE_TYPES and not ytdlp_source.ytdlp_version():
        missing.append("yt-dlp")
    return missing


def start_workflow_queue():
    global queue_worker_thread
    with operation_admission_lock:
        if shutdown_event.is_set():
            raise RuntimeError("Ứng dụng đang đóng; chưa thể chạy hàng đợi.")
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
            picked = None
            if not shutdown_event.is_set():
                picked = next((item for item in workflow_jobs if item["status"] == "queued"), None)
            if not picked:
                current_job_id = ""
                queue_worker_thread = None
                return
            picked_snapshot = {"id": picked["id"], "source_video": picked.get("source_video", "")}
        # Outside the lock: wait for a prefetch of this exact job, or stop an unrelated one.
        coordinate_prefetch_before_job(picked_snapshot)
        with queue_state_lock:
            if shutdown_event.is_set():
                current_job_id = ""
                queue_worker_thread = None
                return
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
                "error_code": "",
                "needs_edge_login": False,
                "result_ready": False,
                "started_at": int(time.time()),
            })
            # Clear the previous job's cancel before this job becomes visible as
            # running, so a cancel sent from here on applies to this job.
            cancel_event.clear()
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
            if job["status"] == "running" or editor_uses_job(job_id):
                raise RuntimeError("Không thể xoá job đang chạy trong VietSub/Auto Edit; hãy huỷ job trước.")
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
        if any(job["status"] in removable and editor_uses_job(job["id"]) for job in workflow_jobs):
            raise RuntimeError("Có dự án đang dùng trong Auto Edit; chờ hoặc huỷ trước khi dọn lịch sử.")
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
        if editor_uses_job(job_id):
            raise RuntimeError("Dự án đang dùng trong Auto Edit; hãy chờ hoặc huỷ tác vụ editor.")
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
                raise FileNotFoundError(
                    "Video trên máy không còn ở vị trí cũ. Hãy chọn lại file MP4 hoặc MKV."
                )
            # A Douyin preview cache may have been cleaned externally; retry by downloading it again.
            job["source_video"] = ""
            job["preview_id"] = ""

        job.update({
            "status": "queued",
            "step": resume_step if resume_step != "download" else "resolve",
            "error": "",
            "error_code": "",
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


@browser_automation
def generate_publish_pack(project, translation_config):
    prompt = publish.build_prompt(read_text_file(project["translated_srt"]), project["project_name"])
    check_cancel_requested()
    if translation_config["provider"] == "notebook":
        response = publish.request_notebook(sys.modules[__name__], translation_config, prompt)
    else:
        response = request_translation_completion(translation_config, [
            {"role": "system", "content": "Bạn là biên tập viên video tiếng Việt. Chỉ trả về JSON hợp lệ."},
            {"role": "user", "content": prompt},
        ], stream=False)
    metadata = publish.parse_response(response)
    check_cancel_requested()
    destination = os.path.join(project["project_dir"], project["project_name"] + ".publish.json")
    write_text_atomic(destination, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    project["publish_metadata"] = destination
    try:
        project_manifest.save_project(project, metadata=metadata, status="success")
    except (OSError, ValueError) as manifest_error:
        log_to_app(f"Publish pack đã lưu; chưa đồng bộ manifest Workbench: {manifest_error}", "error")
    log_to_app(f"Đã tạo gói dữ liệu xuất bản: {destination}")
    return destination


def auto_cleanup_source_video(project, config):
    if config.get("auto_delete_source_video") is not True:
        return 0
    # Revalidate subtitles before any destructive operation, even when called
    # independently. A cleaned result keeps subtitle readiness across restarts.
    validate_project_result(project)
    with queue_state_lock:
        protected = editor_protected_paths()
        for job in workflow_jobs:
            if job["id"] != current_job_id:
                protected.update(storage.video_paths(job))
        paths = storage.video_paths({"result": project}) - protected
        items = storage.inventory(paths)
        freed, errors = storage.delete_inventory(items)
        project["source_video_deleted"] = not os.path.isfile(project["video"])
        project["source_cleanup_bytes"] = freed
        project["source_cleanup_errors"] = errors
    for error in errors:
        log_to_app(f"Chưa dọn được video nguồn: {error['error']}", "error")
    if freed:
        log_to_app(f"Đã dọn video nguồn, giải phóng {format_megabytes(freed)}; giữ phụ đề.")
    return freed


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
    project = None
    video_id = ""
    source_url = video_url
    source_type = (
        prepared_video.get("source_type", "douyin") if prepared_video else source_type_for_url(video_url)
    )
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
                reset_download_progress(source_type)
                try:
                    downloaded = download_video_source(video_url, notebook_url)
                finally:
                    clear_download_progress()
                source_url = downloaded["source_url"]
                video_id = downloaded["video_id"]
                video_file = downloaded["source_video"]
                video_title = downloaded.get("video_title", "")
                source_type = downloaded.get("source_type", source_type)
            update_progress(result={"source_video": video_file})
            project = prepare_project_files(
                video_file,
                project_name,
                video_id,
                video_title=video_title,
                output_dir=output_dir,
                defer_video_move=(
                    source_type in DOWNLOAD_SOURCE_TYPES and should_move_downloaded_source(video_file)
                ),
                include_thumbnail=source_type in DOWNLOAD_SOURCE_TYPES,
            )
            update_progress(result=project)
        if "crop_coords" not in project and not use_saved_crop:
            project["crop_coords"] = validate_crop_coords(crop_coords)
        if "video_resolution" not in project and video_resolution:
            resolution = validate_video_resolution(video_resolution)
            project["video_resolution"] = {"width": resolution[0], "height": resolution[1]}
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
            workflow_context.ocr_geometry = None
            raw_srt = run_videocr(
                project["video"],
                ocr_lang=ocr_lang,
                output_srt=project["raw_srt"],
                crop_coords=crop_coords,
                use_saved_crop=use_saved_crop,
                video_resolution=video_resolution,
            )
            geometry = getattr(workflow_context, "ocr_geometry", None)
            if isinstance(geometry, dict):
                project.update(copy.deepcopy(geometry))
            update_progress(result={**project, "raw_srt": raw_srt})

        update_progress(step="translate")
        if translation_config["provider"] == "notebook":
            translated_srt = translate_srt_via_gemini_edge(
                raw_srt,
                notebook_url,
                output_srt=project["translated_srt"],
                translation_instructions=translation_config["translation_instructions"],
                source_lang=ocr_lang,
            )
        else:
            translated_srt = translate_srt_via_api(
                raw_srt,
                translation_config,
                output_srt=project["translated_srt"],
                source_lang=ocr_lang,
            )
        update_progress(result={"translated_srt": translated_srt})
        project["raw_srt"] = raw_srt
        project["translated_srt"] = translated_srt
        validate_project_result(project)
        project = finalize_project_assets(project, video_id)
        update_progress(result=project)
        validate_project_result(project)
        if config.get("generate_publish_metadata") is True:
            generate_publish_pack(project, translation_config)
        write_project_manifest(
            project,
            video_id=video_id,
            source_url=source_url,
            status="success",
        )
        if config.get("auto_delete_source_video") is True:
            # Persist the completed subtitle export before unlinking media. A crash
            # during cleanup must not turn valid subtitles into a missing-video job.
            project["source_video_deleted"] = True
            update_progress(status="success", step="done", result=project, result_ready=True)
            write_project_manifest(project, video_id=video_id, source_url=source_url, status="success")
            try:
                auto_cleanup_source_video(project, config)
            except OSError as cleanup_error:
                project["source_video_deleted"] = not os.path.isfile(project["video"])
                project["source_cleanup_errors"] = [{"error": str(cleanup_error)}]
                log_to_app(f"Phụ đề đã xuất; chưa dọn được video nguồn: {cleanup_error}", "error")
            update_progress(result=project)
            write_project_manifest(project, video_id=video_id, source_url=source_url, status="success")
        update_progress(result=project)

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
    # Compare bytes: compare_digest raises TypeError on non-ASCII str values.
    if not secrets.compare_digest(
        supplied_token.encode("utf-8", "surrogateescape"),
        API_TOKEN.encode("ascii"),
    ):
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
    if not request.path.startswith("/api/") or request.endpoint in {
        "stream_preview_video", "stream_project_video", "editor.stream_output", "uploader.media_video", "uploader.screenshot",
    }:
        return None
    if not api_request_authorized():
        return jsonify({"error": "Yêu cầu không được ứng dụng VietSub Studio xác thực."}), 403
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        content_type = (request.content_type or "").lower()
        if request.endpoint not in {"prepare_local_preview", "editor.upload_watermark", "uploader.upload_media"} and "application/json" not in content_type:
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


@app.errorhandler(QueuePersistenceError)
def handle_queue_persistence_error(error):
    message = str(error)
    prefix = "Không thể lưu trạng thái hàng đợi"
    if not message.startswith(prefix):
        message = f"{prefix}: {message}"
    return jsonify({"error": message}), 503


@app.errorhandler(HTTPException)
def handle_api_http_error(error):
    if not request.path.startswith("/api/"):
        return error
    if error.code == 413:
        message = "File tải lên vượt quá giới hạn 8 GiB của ứng dụng."
    else:
        message = f"Yêu cầu không hợp lệ (HTTP {error.code}: {error.name})."
    return jsonify({"error": message}), error.code


@app.errorhandler(InternalServerError)
def handle_api_internal_error(error):
    if not request.path.startswith("/api/"):
        return error
    original = getattr(error, "original_exception", None)
    detail = f" ({type(original).__name__}: {original})" if original else ""
    log_to_app(f"Lỗi máy chủ cục bộ ở {request.path}{detail}", "error")
    return jsonify({"error": f"Lỗi nội bộ của VietSub Studio{detail}."}), 500


# Web routes
@app.route('/')
def index():
    return render_template('index.html', app_version=APP_VERSION, api_token=API_TOKEN)


@app.route('/static/<path:filename>')
def static_asset(filename):
    if filename not in {
        "app.js", "editor.js", "uploader.js", "uploader-THIRD-PARTY.txt", "editor-THIRD-PARTY.txt", "style.css", "sakuga-vfx.js", "workbench.js", "fonts.css", "sakuga-LICENSE.txt",
        "fonts/be-vietnam-pro-regular.ttf", "fonts/be-vietnam-pro-medium.ttf",
        "fonts/be-vietnam-pro-semibold.ttf", "fonts/be-vietnam-pro-bold.ttf",
        "fonts/noto-sans-sc.ttf", "fonts/jetbrains-mono.ttf",
        "fonts/be-vietnam-pro-OFL.txt", "fonts/noto-sans-sc-OFL.txt", "fonts/jetbrains-mono-OFL.txt",
    }:
        return jsonify({"error": "Static asset không tồn tại."}), 404
    return send_from_directory(os.path.join(RESOURCE_DIR, "static"), filename)


@app.route('/avatar.jpg')
def avatar_image():
    return send_from_directory(RESOURCE_DIR, "avatar.jpg", mimetype="image/jpeg")


@app.route('/api/check-edge')
def check_edge():
    # Status only: never starts the browser (it opens on demand).
    return jsonify({"connected": check_browser_status(), "browser": browser_label(short=True)})


@app.route('/api/edge/show', methods=['POST'])
def show_edge():
    config = load_app_config()
    try:
        show_edge_for_login(config.get("notebook_url"))
    except (RuntimeError, ValueError, OSError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        # Playwright errors are not RuntimeError subclasses.
        return jsonify({"error": f"Không mở được {browser_label(short=True)}: {error}"}), 400
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
            config = load_app_config(strict=True)
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
            f"Dịch bằng Gemini Notebook qua {browser_label(get_browser_kind(config), short=True)}."
            if runtime_config["provider"] == "notebook"
            else f"{runtime_config['label']} · model {runtime_config['model']}"
        )
    except (OSError, UnicodeError, ValueError) as error:
        translation_ok = False
        translation_detail = str(error)
    browser_kind = get_browser_kind(config)
    browser_name = browser_label(browser_kind)
    browser_executable = resolve_browser_executable(config)
    browser_available = bool(browser_executable)
    ytdlp_version = ytdlp_source.ytdlp_version()
    js_runtime = get_js_runtime()
    js_runtime_name = next(iter(js_runtime), "") if js_runtime else ""
    checks = {
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
        "browser": {
            "ok": browser_available,
            "required": translation_provider == "notebook",
            "connected": check_browser_status(),
            "label": browser_name,
            "detail": (
                f"{browser_name}: {browser_executable}. Chỉ mở khi dịch bằng Notebook, khi tải video cần "
                "trình duyệt hoặc khi bạn bấm mở trang đăng nhập; dùng profile riêng của app."
                if browser_available
                else missing_browser_message(browser_kind)
            ),
        },
        "douyin_source": {
            "ok": browser_available,
            "required": False,
            "detail": (
                f"Douyin thử trang chia sẻ qua HTTP trước, rồi mới mở {browser_name} riêng của app; "
                f"Kuaishou luôn tải qua {browser_name}. Đăng nhập một lần trong Thiết lập để ít gặp "
                "captcha và tải được video cần tài khoản."
            ),
        },
        "ytdlp": {
            "ok": bool(ytdlp_version),
            "required": False,
            "detail": (
                f"yt-dlp {ytdlp_version} tải Xiaohongshu, Bilibili, TikTok và YouTube."
                if ytdlp_version
                else "Thiếu yt-dlp; link Xiaohongshu, Bilibili, TikTok và YouTube sẽ không tải được."
            ),
        },
        "js_runtime": {
            "ok": bool(js_runtime) and ytdlp_source.ejs_available(),
            "required": False,
            "detail": (
                f"YouTube dùng {js_runtime_name} để giải thử thách JavaScript."
                if js_runtime and ytdlp_source.ejs_available()
                else (
                    "Thiếu gói yt-dlp-ejs; hãy cài lại VietSub Studio."
                    if js_runtime
                    else "YouTube cần Deno 2.3+ hoặc Node 22+ trong PATH; nguồn khác không cần."
                )
            ),
        },
    }
    ready_local = all(item["ok"] for item in checks.values() if item["required"])
    ready_browser = ready_local and browser_available
    ready_ytdlp = ready_local and bool(ytdlp_version)
    ready_sources = {"local": ready_local}
    for source in source_registry.SUPPORTED_SITES:
        ready_sources[source] = ready_browser if source in BROWSER_SOURCE_TYPES else ready_ytdlp
    return jsonify({
        "ready": ready_local,
        "ready_local": ready_local,
        "ready_douyin": ready_browser,
        "ready_web": ready_ytdlp,
        "ready_sources": ready_sources,
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
    source_video = preview["source_video"]
    mimetype = "video/x-matroska" if source_video.lower().endswith(".mkv") else "video/mp4"
    return send_file(source_video, conditional=True, mimetype=mimetype)


def project_for_job(job_id):
    """Resolve only a known queue result; no filesystem paths are accepted from HTTP."""
    job = next((item for item in workflow_jobs if item["id"] == job_id), None)
    if job is None:
        raise KeyError("Không tìm thấy dự án trong hàng đợi / lịch sử.")
    result = copy.deepcopy(job.get("result") or {})
    if not result.get("project_dir"):
        raise RuntimeError("Job chưa tạo bộ file dự án.")
    return job, result


def project_geometry_fallback(job):
    # Requested coordinates of use_saved_crop jobs may differ from what OCR used.
    if job.get("use_saved_crop"):
        return {}
    return {key: copy.deepcopy(job[key]) for key in ("crop_coords", "video_resolution")
            if job.get(key) is not None}


def project_media_signature(job_id, path):
    message = f"project-video|{job_id}|{os.path.normcase(os.path.abspath(path))}".encode("utf-8")
    return hmac.new(API_TOKEN.encode("ascii"), message, hashlib.sha256).hexdigest()


def project_handoff_payload(job, project, manifest, workspace=None):
    video = manifest["assets"].get("edited_video") if workspace == "uploader" else ""
    video = video or manifest["assets"]["video"]
    available = {
        "video": bool(video and storage.safe_regular(video) and os.path.getsize(video) > 0),
        "translated_srt": bool(manifest["assets"]["translated_srt"]
                               and storage.safe_regular(manifest["assets"]["translated_srt"])),
    }
    signature = project_media_signature(job["id"], video) if available["video"] else ""
    return {
        "workspace": workspace, "job_id": job["id"], "manifest": manifest,
        "manifest_path": project_manifest.path_for(project), "availability": available,
        "ready": (job.get("status") == "success" and bool(job.get("result_ready"))
                  and job["id"] not in {current_job_id, prefetch_job_id} and all(available.values())),
        "video_url": (f"/api/projects/{job['id']}/video?signature={signature}"
                      f"&asset={'edited' if video == manifest['assets'].get('edited_video') else 'source'}") if signature else "",
    }


@app.route('/api/projects/<job_id>')
def get_project(job_id):
    try:
        with operation_admission_lock, queue_state_lock:
            job, project = project_for_job(job_id)
            value = project_manifest.snapshot(project, video_id=job.get("video_id") or "",
                                              source_url=job.get("source_url") or "",
                                              status=job.get("status") or "error",
                                              geometry_fallback=project_geometry_fallback(job))
            asset = request.args.get("workspace")
            if asset is not None and asset not in {"editor", "uploader"}:
                raise ValueError("Workspace không hợp lệ.")
            return jsonify(project_handoff_payload(job, project, value, asset))
    except KeyError as error:
        return jsonify({"error": error.args[0]}), 404
    except (RuntimeError, ValueError, OSError) as error:
        return jsonify({"error": str(error)}), 409


@app.route('/api/projects/<job_id>/handoff', methods=['POST'])
def handoff_project(job_id):
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or set(data) != {"workspace"}
            or not isinstance(data.get("workspace"), str) or data["workspace"] not in {"editor", "uploader"}):
        return jsonify({"error": "Chỉ nhận workspace editor hoặc uploader; không nhận đường dẫn file."}), 400
    try:
        # The same admission lock used by cleanup/retry makes readiness and saving
        # one atomic operation relative to queue deletion or a new workflow.
        with operation_admission_lock, queue_state_lock:
            job, project = project_for_job(job_id)
            if (job.get("status") != "success" or not job.get("result_ready")
                    or job_id in {current_job_id, prefetch_job_id}):
                raise RuntimeError("Chỉ có thể bàn giao job VietSub đã hoàn tất hợp lệ và không còn được xử lý.")
            with project_manifest.LOCK:
                value = project_manifest.snapshot(project, video_id=job.get("video_id") or "",
                                                  source_url=job.get("source_url") or "", status="success",
                                                  geometry_fallback=project_geometry_fallback(job))
                payload = project_handoff_payload(job, project, value, data["workspace"])
                if not payload["ready"]:
                    raise RuntimeError("Video hoặc sub Việt đã bị dọn / di chuyển. Hãy khôi phục file trước khi chuyển workspace.")
                validate_project_result(project)
                # Persist precisely the validated snapshot; other modules' assets
                # and metadata are retained, never reset by a legacy queue result.
                value["updated_at"] = int(time.time())
                project_manifest.write_atomic(payload["manifest_path"], value)
                log_to_app(f"[Workbench] Đã bàn giao {value['project_name']} sang {data['workspace']}.")
                return jsonify(payload)
    except KeyError as error:
        return jsonify({"error": error.args[0]}), 404
    except (RuntimeError, ValueError, OSError) as error:
        return jsonify({"error": str(error)}), 409


@app.route('/api/projects/<job_id>/video')
def stream_project_video(job_id):
    try:
        with queue_state_lock:
            job, project = project_for_job(job_id)
            value = project_manifest.snapshot(project, status=job.get("status") or "error")
            asset = request.args.get("asset", "source")
            if asset not in {"source", "edited"}:
                raise KeyError("Video dự án không tồn tại.")
            video = value["assets"]["edited_video" if asset == "edited" else "video"]
            expected = project_media_signature(job_id, video)
            supplied = request.args.get("signature", "").encode("utf-8")
            if not secrets.compare_digest(supplied, expected.encode("ascii")):
                return jsonify({"error": "Liên kết video không hợp lệ hoặc phiên ứng dụng đã thay đổi."}), 403
            if not video or os.path.splitext(video)[1].lower() not in {".mp4", ".mkv"} or not storage.safe_regular(video):
                raise KeyError("Video dự án không còn tồn tại.")
            mimetype = "video/x-matroska" if video.lower().endswith(".mkv") else "video/mp4"
            return send_file(video, conditional=True, mimetype=mimetype)
    except KeyError as error:
        return jsonify({"error": error.args[0]}), 404
    except (RuntimeError, ValueError, OSError) as error:
        return jsonify({"error": str(error)}), 409


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


@app.route('/api/queue/bulk-delete', methods=['POST'])
def bulk_delete_queue():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body is required"}), 400
    try:
        return jsonify(bulk_delete_jobs(
            data.get("ids"), delete_files=data.get("delete_files", False),
            keep_srt=data.get("keep_srt", True), dry_run=data.get("dry_run", False),
        ))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except QueuePersistenceError:
        raise
    except (RuntimeError, OSError) as error:
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
    try:
        clear_finished_jobs()
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    return jsonify({"ok": True})

def apply_browser_settings(config, data):
    """Validate the automation browser choice; the custom kind needs an existing .exe."""
    if "browser_kind" not in data and "browser_path" not in data:
        return
    previous_kind = get_browser_kind(config)
    if "browser_kind" in data:
        config["browser_kind"] = browser_source.validate_kind(data["browser_kind"])
    if "browser_path" in data:
        value = data["browser_path"]
        if value is None or (isinstance(value, str) and not value.strip()):
            config["browser_path"] = ""
        else:
            config["browser_path"] = browser_source.validate_custom_executable(value)
    if get_browser_kind(config) == "custom":
        browser_source.validate_custom_executable(config.get("browser_path") or "")
    if get_browser_kind(config) != previous_kind:
        reset_browser_status_cache()


def browser_settings_fields(config):
    kind = get_browser_kind(config)
    return {
        "browser_kind": kind,
        "browser_path": str(config.get("browser_path") or ""),
        "browser_label": browser_label(kind),
        "browser_short_label": browser_label(kind, short=True),
        "browser_profile_dir": browser_profile_dir(kind),
        "browser_executable": resolve_browser_executable(config),
    }


def storage_settings_fields(config):
    return {key: config.get(key) is True for key in (
        "auto_delete_source_video", "generate_publish_metadata",
    )}


def apply_settings_update(data):
    try:
        config = load_app_config(strict=True)
    except OSError as error:
        return jsonify({
            "error": f"Chưa đọc được file cấu hình hiện tại nên chưa lưu thay đổi: {error}"
        }), 503
    try:
        # Bind a pre-v1.5.4 key to its provider before this request changes the provider.
        migrate_legacy_translation_api_key(config)
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
            # Keys are stored per provider and base URL origin (after this request's
            # provider and URL changes), so switching provider never reuses a key.
            slot = configured_translation_api_slot(config)
            saved_keys = config.get("translation_api_keys_protected")
            keys = dict(saved_keys) if isinstance(saved_keys, dict) else {}
            if data["translation_api_key"] is None:
                keys.pop(slot, None)
            else:
                api_key = validate_translation_api_key(
                    data["translation_api_key"],
                    allow_empty=True,
                )
                if api_key:
                    if not slot:
                        raise ValueError("Hãy chọn nhà cung cấp API trước khi lưu API key.")
                    keys[slot] = protect_config_secret(api_key)
            config["translation_api_keys_protected"] = keys
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
                raise ValueError("Tuỳ chọn chạy trình duyệt ẩn không hợp lệ.")
            config["edge_background"] = data["edge_background"]
        if "sakuga_vfx_enabled" in data:
            if type(data["sakuga_vfx_enabled"]) is not bool:
                raise ValueError("Tuỳ chọn hiệu ứng chuyển động phải là true hoặc false.")
            config["sakuga_vfx_enabled"] = data["sakuga_vfx_enabled"]
        if "workbench_layout" in data:
            config["workbench_layout"] = validate_workbench_layout(data["workbench_layout"])
        for key in storage_settings_fields(config):
            if key in data:
                if type(data[key]) is not bool:
                    raise ValueError(f"{key} phải là true hoặc false.")
                config[key] = data[key]
        apply_browser_settings(config, data)
        if "source_cookies" in data:
            apply_source_cookie_updates(config, data["source_cookies"])
        if "cookies_file" in data:
            config["cookies_file"] = validate_cookies_file_setting(data["cookies_file"])
        save_app_config(config)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except OSError as e:
        return jsonify({"error": f"Không thể lưu cấu hình: {e}"}), 503
    try:
        api_key_set = bool(get_translation_api_key(config))
    except (OSError, UnicodeError, ValueError):
        api_key_set = False
    return jsonify({
        "ok": True,
        "translation_api_key_set": api_key_set,
        "source_cookies_set": source_cookies_set(config),
        "cookies_file": config.get("cookies_file") or "",
        **workbench_settings_fields(config),
        **storage_settings_fields(config),
        **browser_settings_fields(config),
    })


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
            translation_instructions = TRANSLATION_DEFAULT_INSTRUCTIONS
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
            "translation_default_instructions": TRANSLATION_DEFAULT_INSTRUCTIONS,
            "translation_instruction_presets": {
                key: {"label": value["label"], "instructions": value["instructions"]}
                for key, value in TRANSLATION_INSTRUCTION_PRESETS.items()
            },
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
            **workbench_settings_fields(config),
            **storage_settings_fields(config),
            **browser_settings_fields(config),
            "desktop_shortcut_supported": shortcut_supported,
            "desktop_shortcut_exists": shortcut_exists,
            "source_cookies_set": source_cookies_set(config),
            "source_session_sites": [
                {"key": site.key, "label": site.label} for site in SESSION_SITES.values()
            ],
            "cookies_file": config.get("cookies_file") or "",
            "download_cache_dir": get_download_cache_dir(config),
            "ytdlp_version": ytdlp_source.ytdlp_version(),
        })


def get_source_session_status():
    """Login state of each platform in the automation browser's profile; cookie values never leave.

    Reading the state never starts the browser: it is only read while the browser runs.
    """
    config = load_app_config()
    manual = source_cookies_set(config)
    browser = browser_label(get_browser_kind(config), short=True)
    status = {
        "browser_connected": False,
        "browser": browser,
        "cookies_file": bool(config.get("cookies_file")),
        "sites": {
            key: {"label": site.label, "logged_in": None, "has_cookies": False, "manual_cookie": manual[key]}
            for key, site in SESSION_SITES.items()
        },
    }
    if not check_browser_status():
        return status
    status["browser_connected"] = True
    try:
        browser_cookies = read_browser_cookies(session_sites.all_cookie_urls())
    except Exception as error:
        status["error"] = f"Không đọc được cookie từ {browser}: {error}"
        return status
    for key in SESSION_SITES:
        site_cookies = session_sites.cookies_of_site(key, browser_cookies)
        status["sites"][key]["has_cookies"] = bool(site_cookies)
        status["sites"][key]["logged_in"] = session_sites.login_state(key, site_cookies)
    return status


@browser_lifecycle.leased()
def open_source_in_browser(site_key):
    """Show the platform in the automation browser's own profile so the user can sign in once."""
    site = SESSION_SITES[site_key]
    ensure_browser_running(background=False, start_url=site.home_url)
    browser_lifecycle.grant_manual_login()
    domain = site.cookie_domain.lstrip(".")
    with get_sync_playwright()() as playwright:
        context = browser_context(playwright)
        page = browser_source.reuse_page(
            context, site.home_url,
            matches=lambda item: url_host(item.url) == domain or url_host(item.url).endswith("." + domain),
        )
        if url_host(page.url) != domain and not url_host(page.url).endswith("." + domain):
            try:
                page.goto(site.home_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
        if not set_browser_window_visibility(context, page, True):
            page.bring_to_front()
    return True


@app.route('/api/sources/sessions')
def source_sessions():
    return jsonify(get_source_session_status())


@app.route('/api/sources/<site_key>/open', methods=['POST'])
def open_source_login(site_key):
    if site_key not in SESSION_SITES:
        return jsonify({"error": "Nền tảng này không có phiên đăng nhập trong trình duyệt tự động hoá."}), 404
    try:
        open_source_in_browser(site_key)
    except (RuntimeError, OSError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return jsonify({"error": f"Không mở được {browser_label(short=True)}: {error}"}), 400
    return jsonify({"ok": True})


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
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("path"), str):
        return jsonify({"error": "Yêu cầu mở thư mục cần JSON có trường path dạng chuỗi."}), 400
    path = data["path"]
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
                subprocess.Popen([EXPLORER_EXE, path])
            else:
                subprocess.Popen([EXPLORER_EXE, f"/select,{path}"])
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Đường dẫn kết quả không còn tồn tại."}), 400

def initialize_uploader_state():
    adapter = globals().get('uploader_api')
    if adapter:
        try:
            adapter.initialize()
        except (OSError, ValueError) as error:
            log_to_app(f'Không nạp được Publish Hub; giữ nguyên dữ liệu: {error}', 'error')


def run_web_server():
    initialize_queue_state()
    initialize_uploader_state()
    log_to_app("Mở local web server tại http://127.0.0.1:5000...")
    try:
        app.run(host="127.0.0.1", port=5000, debug=False)
    finally:
        request_app_shutdown()


def run_browser_app():
    """Serve the UI in the user's browser when embedded WebView2 is unavailable."""
    initialize_queue_state()
    initialize_uploader_state()
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

    def choose_local_video(self):
        """Native file dialog: the chosen MP4/MKV is previewed in place (no upload, no copy)."""
        try:
            selected = self._window.create_file_dialog(
                self._webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=LOCAL_VIDEO_FILE_TYPES,
            )
        except Exception as error:
            return {"error": f"Không mở được hộp thoại chọn file: {error}"}
        if not selected:
            return None
        path = selected[0] if isinstance(selected, (list, tuple)) else selected
        try:
            return create_local_path_preview(str(path))
        except (OSError, RuntimeError, ValueError) as error:
            return {"error": str(error)}

    def choose_cookies_file(self):
        selected = self._window.create_file_dialog(
            self._webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=COOKIE_FILE_TYPES,
        )
        if not selected:
            return ""
        path = selected[0] if isinstance(selected, (list, tuple)) else selected
        return os.path.abspath(str(path))

    def choose_browser_executable(self):
        """Native dialog for a Chromium-based browser .exe (the custom automation browser)."""
        selected = self._window.create_file_dialog(
            self._webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=BROWSER_FILE_TYPES,
        )
        if not selected:
            return ""
        path = selected[0] if isinstance(selected, (list, tuple)) else selected
        return os.path.abspath(str(path))


def run_desktop_app():
    ensure_initial_desktop_shortcut()
    initialize_queue_state()
    initialize_uploader_state()
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
                # Desktop fallback reopens the local app session, not a permanently
                # closed editor queue from the failed embedded window cleanup.
                from vietsub.editor.jobs import EditorJobs
                editor_api.jobs = EditorJobs()
                run_browser_app()
    finally:
        release_instance_lock()
    return 0


# Editor is a separate queue/blueprint; the import is stdlib/FFmpeg-only.
from vietsub.editor.api import register as register_editor_api
editor_api = register_editor_api(sys.modules[__name__])
from vietsub.uploader.api import register as register_uploader_api
uploader_api = register_uploader_api(sys.modules[__name__])


if __name__ == '__main__':
    sys.exit(main())
