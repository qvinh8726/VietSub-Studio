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
from queue import Empty, Queue
from threading import Event, Lock, Thread
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
APP_HOME = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SOURCE_DIR
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

SUPPORTED_OCR_LANGS = {"ch", "en", "ja", "ko", "vi"}
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
MAX_LOG_LINES = 2_000
MAX_PROJECT_NAME_LENGTH = 100
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
    "result": {
        "project_name": "",
        "project_dir": "",
        "video": "",
        "source_video": "",
        "raw_srt": "",
        "translated_srt": ""
    }
}
status_lock = Lock()
workflow_lock = Lock()
cancel_event = Event()
active_process_lock = Lock()
active_processes = {}


class WorkflowCancelled(RuntimeError):
    pass


def reset_progress():
    with status_lock:
        progress_status.update({
            "status": "running",
            "step": "resolve",
            "logs": [],
            "log_base_index": 0,
            "error": "",
            "result": {
                "project_name": "",
                "project_dir": "",
                "video": "",
                "source_video": "",
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


def progress_snapshot(after_log_index=0):
    with status_lock:
        after_log_index = max(after_log_index, progress_status["log_base_index"])
        start_index = min(
            len(progress_status["logs"]),
            max(0, after_log_index - progress_status["log_base_index"]),
        )
        return {
            "status": progress_status["status"],
            "step": progress_status["step"],
            "logs": list(progress_status["logs"][start_index:]),
            "log_base_index": progress_status["log_base_index"],
            "next_log_index": progress_status["log_base_index"] + len(progress_status["logs"]),
            "error": progress_status["error"],
            "result": dict(progress_status["result"])
        }


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

def ensure_edge_running(notebook_url):
    check_cancel_requested()
    if check_edge_status():
        log_to_app("Trình duyệt Edge debug đang mở sẵn.")
        return True

    log_to_app("Trình duyệt Edge debug chưa mở. Đang tự động kích hoạt...")
    edge_bin = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(edge_bin):
        edge_bin = "msedge.exe"

    cmd = [
        edge_bin,
        "--remote-debugging-port=9222",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={USER_DATA_DIR_DEBUG}",
        notebook_url
    ]
    try:
        # Launch Edge in background
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0)
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


def prepare_project_files(source_video, requested_name, video_id, video_title="", output_dir=""):
    check_cancel_requested()
    fallback_name = video_title or f"Douyin {video_id}"
    sanitized_name = sanitize_project_name(requested_name, fallback=fallback_name)
    default_root = os.path.join(os.path.dirname(source_video), "VietSub Studio")
    output_root = validate_output_dir(output_dir) or default_root
    project_name, project_dir = allocate_project_directory(output_root, sanitized_name)
    video_path = os.path.join(project_dir, project_name + ".mp4")
    raw_srt_path = os.path.join(project_dir, project_name + ".raw.srt")
    translated_srt_path = os.path.join(project_dir, project_name + ".vi.srt")

    log_to_app(f"Tạo bộ file đồng bộ với tên: {project_name}")
    log_to_app("Đang sao chép video vào thư mục kết quả, cache Douzy vẫn được giữ nguyên...")
    try:
        check_cancel_requested()
        copy_file_atomic(source_video, video_path)
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
        "source_video": source_video,
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
            return normalized
    return None

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

def run_videocr(video_path, ocr_lang="ch", output_srt=None):
    check_cancel_requested()
    if not os.path.isfile(VIDEOCR_CLI):
        raise FileNotFoundError(f"Không tìm thấy VideOCR CLI tại: {VIDEOCR_CLI}")
    validate_ocr_lang(ocr_lang)
    log_to_app("Cấu hình toạ độ nhận diện chữ phụ đề...")
    coords = get_videocr_crop_coords()
    res = get_video_resolution(video_path)

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
        gemini_page.bring_to_front()

        response_selectors = [
            "message-content",
            ".model-response",
            ".assistant-message",
            "[data-message-author='assistant']",
            "div.markdown",
            ".chat-message"
        ]

        log_to_app("Tìm khung nhập liệu chat...")
        check_cancel_requested()
        input_selectors = [
            "div.ql-editor",
            "div[contenteditable='true']",
            "[role='textbox']",
            "textarea"
        ]

        prompt_input = None
        for sel in input_selectors:
            loc = gemini_page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                prompt_input = loc.first
                break

        if not prompt_input:
            raise RuntimeError("Không tìm thấy khung nhập liệu chat của Gemini.")

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

        submit_btn = None
        for sel in submit_selectors:
            loc = gemini_page.locator(sel)
            if loc.count() > 0 and loc.first.is_enabled():
                submit_btn = loc.first
                break

        if not submit_btn:
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

# Background processing thread wrapper
def queue_workflow(video_url, ocr_lang, project_name=None):
    video_url = validate_douyin_url(video_url)
    ocr_lang = validate_ocr_lang(ocr_lang)
    if project_name is not None and not isinstance(project_name, str):
        raise ValueError("Tên bộ file không hợp lệ.")
    if not workflow_lock.acquire(blocking=False):
        raise RuntimeError("Một video khác đang được xử lý. Hãy chờ quy trình hiện tại hoàn tất.")

    cancel_event.clear()
    reset_progress()
    try:
        thread = Thread(
            target=run_workflow_thread,
            args=(video_url, ocr_lang, project_name),
            daemon=True,
        )
        thread.start()
        return thread
    except Exception:
        workflow_lock.release()
        update_progress(status="error", error="Không thể khởi tạo tác vụ nền.")
        raise


def run_workflow_thread(video_url, ocr_lang, project_name=None):
    sidecar_proc = None
    project = None
    video_id = ""
    source_url = video_url
    try:
        check_cancel_requested()
        config = load_app_config()
        notebook_url = validate_notebook_url(config.get("notebook_url"))
        output_dir = validate_output_dir(config.get("output_dir", ""))
        if not output_dir:
            douzy_download_dir = get_douzy_download_dir()
            if douzy_download_dir:
                output_dir = os.path.join(douzy_download_dir, "VietSub Studio")

        source_url = validate_douyin_url(video_url)
        is_short_url = urlparse(source_url).hostname.lower() == "v.douyin.com"
        video_url_for_id = source_url
        if is_short_url:
            # Douzy receives the original share link, while Edge gives us a stable aweme ID.
            video_url_for_id = resolve_url_via_edge(source_url, notebook_url)
            if not video_url_for_id:
                raise ValueError("Không thể giải mã link share Douyin thành link video.")
            video_url_for_id = validate_douyin_url(video_url_for_id)

        source_path = urlparse(video_url_for_id).path.lower()
        if "/mix/" in source_path or "/user/" in source_path:
            raise ValueError("Tool chỉ hỗ trợ xử lý một video; không hỗ trợ link mix hoặc user.")
        video_id = get_video_id(video_url_for_id)

        update_progress(step="download")
        sidecar_proc, port, token = start_douzy_sidecar()
        start_time_epoch = time.time()
        if is_short_url:
            log_to_app("Gửi link share ngắn trực tiếp cho Douzy để tải video.")
        else:
            log_to_app(f"Sử dụng link video: {source_url}")
        job_id = trigger_download(port, token, source_url, scope="single")
        job = poll_download_job(port, token, job_id)
        if job.get("skipped"):
            log_to_app("Douzy đã có sẵn video này, dùng lại file đã tải.")

        # Douzy writes its database asynchronously after the job reports success.
        video_file = wait_for_downloaded_video_file(
            start_time_epoch,
            video_id,
            allow_existing=bool(job.get("skipped")),
        )
        update_progress(result={"source_video": video_file})

        stop_process(sidecar_proc, "sidecar Douzy")
        unregister_active_process("sidecar Douzy", sidecar_proc)
        sidecar_proc = None

        video_title = get_video_title(video_id)
        project = prepare_project_files(
            video_file,
            project_name,
            video_id,
            video_title=video_title,
            output_dir=output_dir,
        )
        update_progress(result=project)
        write_project_manifest(
            project,
            video_id=video_id,
            source_url=source_url,
            status="processing",
        )

        update_progress(step="ocr")
        raw_srt = run_videocr(
            project["video"],
            ocr_lang=ocr_lang,
            output_srt=project["raw_srt"],
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
        workflow_lock.release()

# Web routes
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/avatar.jpg')
def avatar_image():
    return send_from_directory(RESOURCE_DIR, "avatar.jpg", mimetype="image/jpeg")

@app.route('/api/check-edge')
def check_edge():
    connected = check_edge_status()
    return jsonify({"connected": connected})


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
            "required": True,
            "detail": "Cài Douzy đúng thư mục mặc định rồi mở ứng dụng ít nhất một lần.",
        },
        "douzy_config": {
            "ok": os.path.isfile(DOUZY_CONFIG),
            "required": True,
            "detail": "Mở Douzy và chọn thư mục tải video để tạo cấu hình.",
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
            "detail": "App sẽ tự mở Edge; hãy đăng nhập Gemini trong cửa sổ đó.",
        },
    }
    ready = all(item["ok"] for item in checks.values() if item["required"])
    return jsonify({"ready": ready, "checks": checks})

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
            save_app_config(config)
        except (OSError, ValueError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, "config": config})
    else:
        # GET request
        config = load_app_config()
        crop = get_videocr_crop_coords()
        down_dir = get_douzy_download_dir()
        return jsonify({
            "crop_coords": crop,
            "download_dir": down_dir,
            "notebook_url": config.get("notebook_url"),
            "ocr_lang": config.get("ocr_lang"),
            "output_dir": config.get("output_dir", ""),
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
    current_result = progress_snapshot()["result"]
    allowed_paths = {
        value for key, value in current_result.items()
        if key in {"project_dir", "video", "raw_srt", "translated_srt"} and value
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
    log_to_app("Mở local web server tại http://127.0.0.1:5000...")
    app.run(host="127.0.0.1", port=5000, debug=False)


def run_desktop_app():
    try:
        import webview
    except ImportError as error:
        raise RuntimeError("Không tìm thấy pywebview. Hãy cài lại ứng dụng.") from error

    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        webview.create_window(
            "VietSub Studio",
            f"http://127.0.0.1:{port}",
            width=1320,
            height=880,
            min_size=(960, 680),
            background_color="#f5f1e8",
        )
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
