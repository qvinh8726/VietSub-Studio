"""Streaming HTTP download with progress, resume, URL fallback and fast cancel.

The transfer runs in a helper thread. When the caller cancels, stream_download
returns at once; a read blocked on a silent server cannot be interrupted on
Windows, so the helper thread finishes on its own at the next byte or socket
timeout and then deletes its partial file. Each call writes to its own .part name,
so a quick retry never collides with that file.
"""

import http.client
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

from .base import DownloadCancelledError, SourceError

DEFAULT_CHUNK_SIZE = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
RETRY_DELAYS = (1, 3)
NON_RETRYABLE_STATUS = {400, 401, 403, 404, 405, 410, 451}


class _IncompleteTransfer(Exception):
    pass


def _close_quietly(resource):
    try:
        if resource is not None:
            resource.close()
    except Exception:
        pass


def _close_in_background(resource):
    # HTTPResponse.close() blocks while another thread is inside read(), so the
    # cancelling thread hands the close to a helper and returns at once.
    if resource is not None:
        threading.Thread(target=_close_quietly, args=(resource,), daemon=True).start()


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _short_url(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:60]}"


def _total_from_content_range(value):
    # "bytes 40000-99999/100000"
    try:
        return int(str(value).rsplit("/", 1)[1])
    except (IndexError, ValueError):
        return None


def stream_download(
    urls,
    destination,
    *,
    headers=None,
    cookie_jar=None,
    on_progress=None,
    is_cancelled=None,
    timeout=DEFAULT_TIMEOUT_SECONDS,
    chunk_size=DEFAULT_CHUNK_SIZE,
    validate=None,
    retry_delays=RETRY_DELAYS,
    progress_interval=0.25,
    opener=None,
):
    """Download the first working URL to destination and return {"path", "url", "bytes"}.

    on_progress(downloaded_bytes, total_bytes_or_None, bytes_per_second_or_None) is
    called from the helper thread. validate(path) may raise ValueError to reject a
    finished file (for example an HTML error page) and try the next URL.
    """
    candidates = []
    for url in urls or []:
        if isinstance(url, str) and url and url not in candidates:
            candidates.append(url)
    if not candidates:
        raise SourceError("Không có địa chỉ video nào để tải.")
    destination = os.path.abspath(os.fspath(destination))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    part_path = f"{destination}.{uuid.uuid4().hex[:12]}.part"
    if opener is None:
        handlers = [urllib.request.HTTPCookieProcessor(cookie_jar)] if cookie_jar is not None else []
        opener = urllib.request.build_opener(*handlers)
    base_headers = dict(headers or {})
    base_headers["Accept-Encoding"] = "identity"
    state = {"aborted": False, "response": None}
    outcome = {}
    done = threading.Event()

    def cancelled():
        return state["aborted"]

    def sleep(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if cancelled():
                raise DownloadCancelledError("Đã huỷ tải video.")
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    progress = {"last_report": 0.0, "window_start": time.monotonic(), "window_bytes": 0, "speed": None}

    def report(downloaded, total, force=False):
        if not on_progress:
            return
        now = time.monotonic()
        elapsed = now - progress["window_start"]
        if elapsed >= 1.0:
            progress["speed"] = max(0.0, (downloaded - progress["window_bytes"]) / elapsed)
            progress["window_start"] = now
            progress["window_bytes"] = downloaded
        if force or now - progress["last_report"] >= progress_interval:
            progress["last_report"] = now
            try:
                on_progress(downloaded, total, progress["speed"])
            except Exception:
                pass

    def download_one(url, handle):
        downloaded = 0
        total = None
        attempt = 0
        while True:
            if cancelled():
                raise DownloadCancelledError("Đã huỷ tải video.")
            request_headers = dict(base_headers)
            if downloaded:
                request_headers["Range"] = f"bytes={downloaded}-"
            request = urllib.request.Request(url, headers=request_headers)
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                _close_quietly(error)
                if error.code == 416 and total is not None and downloaded >= total:
                    return downloaded
                if error.code in NON_RETRYABLE_STATUS or attempt >= len(retry_delays):
                    raise SourceError(f"máy chủ trả HTTP {error.code}") from error
                attempt += 1
                sleep(retry_delays[attempt - 1])
                continue
            except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                if attempt >= len(retry_delays):
                    raise SourceError(f"không kết nối được ({error})") from error
                attempt += 1
                sleep(retry_delays[attempt - 1])
                continue
            state["response"] = response
            try:
                if cancelled():
                    raise DownloadCancelledError("Đã huỷ tải video.")
                status = getattr(response, "status", 200) or 200
                if downloaded and status != 206:
                    # The server ignored Range; start this URL again from byte 0.
                    handle.seek(0)
                    handle.truncate()
                    downloaded = 0
                if status == 206:
                    total = _total_from_content_range(response.headers.get("Content-Range")) or total
                else:
                    length = str(response.headers.get("Content-Length") or "")
                    total = int(length) if length.isdigit() else None
                while True:
                    chunk = response.read(chunk_size)
                    if cancelled():
                        raise DownloadCancelledError("Đã huỷ tải video.")
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    report(downloaded, total)
                if total is not None and downloaded < total:
                    raise _IncompleteTransfer(f"{downloaded}/{total} byte")
                report(downloaded, total if total is not None else downloaded, force=True)
                return downloaded
            except DownloadCancelledError:
                raise
            except (_IncompleteTransfer, OSError, http.client.HTTPException) as error:
                if cancelled():
                    raise DownloadCancelledError("Đã huỷ tải video.") from error
                if attempt >= len(retry_delays):
                    raise SourceError(f"kết nối bị ngắt khi đã tải {downloaded:,} byte ({error})") from error
                attempt += 1
                sleep(retry_delays[attempt - 1])
            finally:
                state["response"] = None
                _close_quietly(response)

    def worker():
        errors = []
        try:
            for url in candidates:
                try:
                    with open(part_path, "wb") as handle:
                        size = download_one(url, handle)
                    if validate is not None:
                        validate(part_path)
                    if cancelled():
                        raise DownloadCancelledError("Đã huỷ tải video.")
                    os.replace(part_path, destination)
                    outcome["value"] = {"path": destination, "url": url, "bytes": size}
                    return
                except DownloadCancelledError:
                    raise
                except (SourceError, ValueError, OSError) as error:
                    errors.append(f"{_short_url(url)}: {error}")
                    _remove_quietly(part_path)
            outcome["error"] = SourceError("Không tải được video. " + " | ".join(errors[-3:]))
        except BaseException as error:
            outcome["error"] = error
        finally:
            _remove_quietly(part_path)
            done.set()

    threading.Thread(target=worker, name="vietsub-download", daemon=True).start()
    while not done.wait(0.2):
        if is_cancelled is not None and is_cancelled():
            state["aborted"] = True
            _close_in_background(state["response"])
            done.wait(1.0)
            raise DownloadCancelledError("Đã huỷ tải video.")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]
