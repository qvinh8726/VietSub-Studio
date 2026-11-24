"""yt-dlp wrapper for TikTok, YouTube, Bilibili (and a last-resort Douyin fallback).

yt-dlp is imported lazily so app startup does not pay for its extractor registry.
YouTube needs an external JavaScript runtime (Deno 2.3+ or Node 22+) together with
the bundled yt-dlp-ejs scripts; remote script downloads stay disabled.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import tempfile

from .base import DownloadCancelledError, SourceError
from .local import sniff_container

FORMAT_WITH_FFMPEG = (
    "bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/"
    "bv*[vcodec^=avc1][height<=1080]+ba/"
    "b[ext=mp4][vcodec^=avc1][height<=1080]/"
    "bv*[height<=1080]+ba/b[height<=1080]/b"
)
FORMAT_WITHOUT_FFMPEG = "b[ext=mp4][vcodec^=avc1]/b[ext=mp4]/b"
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MIN_NODE_MAJOR = 22


def ytdlp_version():
    try:
        from yt_dlp.version import __version__
    except ImportError:
        return ""
    return str(__version__)


def ejs_available():
    try:
        return importlib.util.find_spec("yt_dlp_ejs") is not None
    except (ImportError, ValueError):
        return False


def find_js_runtime(resolve_executable):
    """yt-dlp js_runtimes setting for the first usable runtime in PATH, or None.

    resolve_executable(name) must return an absolute path or "" (it should not
    search the current directory).
    """
    deno = resolve_executable("deno")
    if deno:
        return {"deno": {"path": deno}}
    node = resolve_executable("node")
    if node:
        try:
            completed = subprocess.run(
                [node, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            major = int(completed.stdout.strip().lstrip("v").split(".", 1)[0])
        except (OSError, ValueError, subprocess.SubprocessError):
            major = 0
        if major >= MIN_NODE_MAJOR:
            return {"node": {"path": node}}
    return None


class _Logger:
    """Route yt-dlp messages to the app log; debug and info lines are dropped."""

    def __init__(self, log):
        self.log = log

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        if self.log:
            self.log(f"yt-dlp: {clean_message(message)}")

    def error(self, message):
        if self.log:
            self.log(f"yt-dlp: {clean_message(message)}")


def clean_message(message):
    text = ANSI_PATTERN.sub("", str(message or "")).strip()
    if text.upper().startswith("ERROR:"):
        text = text[6:].strip()
    return text


def build_ytdlp_options(
    cache_root,
    *,
    site,
    ffmpeg_path="",
    js_runtimes=None,
    progress_hook=None,
    cookie_file=None,
    logger=None,
    cache_id=None,
    part=None,
):
    safe_cache_id = (
        str(cache_id)
        if cache_id and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(cache_id))
        else ""
    )
    if safe_cache_id:
        stem = f"{site}-{safe_cache_id}"
    elif site == "bilibili" and isinstance(part, int) and 0 < part < 10_000:
        stem = f"{site}-%(id)s-p{part}"
    else:
        stem = f"{site}-%(id)s"
    folder = os.path.join(os.fspath(cache_root), stem)
    options = {
        "outtmpl": {
            "default": os.path.join(folder, stem + ".%(ext)s"),
            # find_douzy_thumbnail_file() picks up <video stem>_cover.<ext> for the project.
            "thumbnail": os.path.join(folder, stem + "_cover.%(ext)s"),
        },
        "format": FORMAT_WITH_FFMPEG if ffmpeg_path else FORMAT_WITHOUT_FFMPEG,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "writethumbnail": True,
        "progress_hooks": [progress_hook] if progress_hook else [],
    }
    if ffmpeg_path:
        options["ffmpeg_location"] = ffmpeg_path
        options["merge_output_format"] = "mp4"
    if js_runtimes:
        options["js_runtimes"] = js_runtimes
    if cookie_file:
        options["cookiefile"] = cookie_file
    if logger is not None:
        options["logger"] = logger
    return options


def _default_ydl_class():
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise SourceError("Thiếu thư viện yt-dlp; hãy cài lại VietSub Studio.") from error
    return YoutubeDL


def _cancel_exception():
    try:
        from yt_dlp.utils import DownloadCancelled
    except ImportError:
        return DownloadCancelledError("Đã huỷ tải video.")
    return DownloadCancelled("Đã huỷ tải video.")


def download_with_ytdlp(
    url,
    cache_root,
    *,
    site,
    cookie_file=None,
    ffmpeg_path="",
    js_runtimes=None,
    on_progress=None,
    is_cancelled=None,
    log=None,
    ydl_class=None,
    cache_id=None,
):
    """Download one video with yt-dlp and return {"path", "video_id", "title", "duration"}.

    cookie_file is copied first: yt-dlp writes cookies back to its cookie file when
    it closes, and the user's own cookies.txt must stay unchanged.
    """
    if is_cancelled is not None and is_cancelled():
        raise DownloadCancelledError("Đã huỷ tải video.")
    ydl_class = ydl_class or _default_ydl_class()
    temporary_dir = tempfile.mkdtemp(prefix="vietsub-ytdlp-")
    state = {"cancelled": False}

    def hook(progress):
        if is_cancelled is not None and is_cancelled():
            state["cancelled"] = True
            raise _cancel_exception()
        if progress.get("status") == "downloading" and on_progress:
            total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
            speed = progress.get("speed")
            on_progress(
                int(progress.get("downloaded_bytes") or 0),
                int(total) if total else None,
                float(speed) if speed else None,
            )

    try:
        working_cookie_file = None
        if cookie_file:
            working_cookie_file = os.path.join(temporary_dir, "cookies.txt")
            shutil.copyfile(cookie_file, working_cookie_file)
        part = None
        if site == "bilibili" and not cache_id:
            from .web import bilibili_part
            part = bilibili_part(url)
        options = build_ytdlp_options(
            cache_root,
            site=site,
            ffmpeg_path=ffmpeg_path,
            js_runtimes=js_runtimes,
            progress_hook=hook,
            cookie_file=working_cookie_file,
            logger=_Logger(log),
            cache_id=cache_id,
            part=part,
        )
        try:
            with ydl_class(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except DownloadCancelledError:
            raise
        except Exception as error:
            if state["cancelled"] or type(error).__name__ == "DownloadCancelled":
                raise DownloadCancelledError("Đã huỷ tải video.") from error
            raise SourceError(f"yt-dlp không tải được video: {clean_message(error)}") from error
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)

    if not isinstance(info, dict):
        raise SourceError("yt-dlp không trả về thông tin video.")
    if info.get("_type") in ("playlist", "multi_video"):
        raise SourceError("Link này là playlist hoặc nhiều video; hãy dán link của đúng một video.")
    downloads = info.get("requested_downloads") or []
    path = downloads[0].get("filepath") if downloads and isinstance(downloads[0], dict) else ""
    path = path or info.get("filepath") or info.get("_filename") or ""
    if not path or not os.path.isfile(path):
        raise SourceError("yt-dlp báo tải xong nhưng không thấy file video.")
    if sniff_container(path) not in ("mp4", "mkv"):
        raise SourceError("File yt-dlp tải về không phải video MP4 hoặc MKV.")
    return {
        "path": os.path.abspath(path),
        "video_id": str(info.get("id") or ""),
        "title": " ".join(str(info.get("title") or "").split()),
        "duration": info.get("duration"),
    }
