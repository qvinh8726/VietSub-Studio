"""Streaming HTTP download with parallel byte ranges, progress, resume, URL fallback and fast cancel.

Each URL is probed first with "Range: bytes=0-0". When the server answers 206 with the
file size and the file is larger than PARALLEL_MIN_BYTES, the file is fetched over
PARALLEL_CONNECTIONS connections at once, like a download manager: each connection
fetches one byte range and writes it in place into a pre-sized .part file. CDNs cap
the speed of each connection, so the connections add up. When the server does not
serve ranges, or a range connection fails for good, the URL is fetched over a single
connection with resume, as before.

The transfer runs in a helper thread. When the caller cancels, stream_download
returns at once and every connection stops at its next read. A read blocked on a
silent server cannot be interrupted on Windows, so the helper finishes on its own at
the next byte or socket timeout and then deletes its partial file. Each call writes
to its own .part name, so a quick retry never collides with that file.
"""

import http.client
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from .base import DownloadCancelledError, SourceError

# Small reads keep the progress smooth and let every connection see a cancel quickly.
DEFAULT_CHUNK_SIZE = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
RETRY_DELAYS = (1, 3)
NON_RETRYABLE_STATUS = {400, 401, 403, 404, 405, 410, 451}
PARALLEL_CONNECTIONS = 6
PARALLEL_MIN_BYTES = 2 * 1024 * 1024
PROBE_RANGE = "bytes=0-0"
CONTENT_RANGE_PATTERN = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+)", re.IGNORECASE)
CANCELLED_MESSAGE = "Đã huỷ tải video."


class _IncompleteTransfer(Exception):
    pass


class _RangeUnavailable(Exception):
    """The parallel transfer cannot finish; the URL is fetched over one connection instead."""


class _Stopped(Exception):
    """Another range connection failed for good, so this one stops too."""


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


def _content_range(value):
    """(first, last, total) of a "bytes first-last/total" header, or None."""
    match = CONTENT_RANGE_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        return None
    first, last, total = (int(group) for group in match.groups())
    return (first, last, total) if first <= last < total else None


def split_ranges(total, parts):
    """Split bytes [0, total) into up to `parts` contiguous inclusive (first, last) ranges."""
    total = int(total)
    parts = max(1, min(int(parts), total))
    size, extra = divmod(total, parts)
    ranges = []
    first = 0
    for index in range(parts):
        last = first + size + (1 if index < extra else 0) - 1
        ranges.append((first, last))
        first = last + 1
    return ranges


class _Progress:
    """Byte counter shared by every connection of one download; reports at most every interval.

    Speed is measured over windows of at least one second across all connections.
    """

    def __init__(self, on_progress, interval):
        self._on_progress = on_progress
        self._interval = interval
        self._lock = threading.Lock()
        self.downloaded = 0
        self.total = None
        self._last_report = 0.0
        self._window_start = time.monotonic()
        self._window_bytes = 0
        self._speed = None

    def restart(self, total=None):
        with self._lock:
            self.downloaded = 0
            self.total = total
            self._window_start = time.monotonic()
            self._window_bytes = 0
            self._speed = None

    def add(self, count, total=None):
        with self._lock:
            self.downloaded += count
            if total is not None:
                self.total = total
            self._report_locked(False)

    def set(self, downloaded, total=None, *, force=False):
        with self._lock:
            if downloaded < self.downloaded:
                # The server ignored Range on a resume, so the transfer starts over.
                self._window_start = time.monotonic()
                self._window_bytes = downloaded
            self.downloaded = downloaded
            if total is not None:
                self.total = total
            self._report_locked(force)

    def _report_locked(self, force):
        now = time.monotonic()
        elapsed = now - self._window_start
        if elapsed >= 1.0:
            self._speed = max(0.0, (self.downloaded - self._window_bytes) / elapsed)
            self._window_start = now
            self._window_bytes = self.downloaded
        if not self._on_progress or not (force or now - self._last_report >= self._interval):
            return
        self._last_report = now
        try:
            # Called under the lock so the UI never sees the byte count go backwards.
            self._on_progress(self.downloaded, self.total, self._speed)
        except Exception:
            pass


def stream_download(
    urls,
    destination,
    *,
    headers=None,
    cookie_jar=None,
    on_progress=None,
    on_notice=None,
    on_connections=None,
    is_cancelled=None,
    timeout=DEFAULT_TIMEOUT_SECONDS,
    chunk_size=DEFAULT_CHUNK_SIZE,
    validate=None,
    retry_delays=RETRY_DELAYS,
    progress_interval=0.25,
    opener=None,
    connections=PARALLEL_CONNECTIONS,
    parallel_min_bytes=PARALLEL_MIN_BYTES,
):
    """Download the first working URL to destination; return {"path", "url", "bytes", "connections"}.

    on_progress(downloaded_bytes, total_bytes_or_None, bytes_per_second_or_None) is
    called from the download threads with the bytes of every connection added up.
    on_notice(message) says how the file is fetched (in parallel or over one connection).
    on_connections(count) reports the selected connection count, including fallback to one.
    validate(path) may raise ValueError to reject a finished file (for example an HTML
    error page) and try the next URL.
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
    state = {"aborted": False}
    active = set()
    active_lock = threading.Lock()
    outcome = {}
    done = threading.Event()
    tracker = _Progress(on_progress, progress_interval)

    def cancelled():
        return state["aborted"]

    def notice(message):
        if on_notice:
            try:
                on_notice(message)
            except Exception:
                pass

    def report_connections(count):
        if on_connections and not cancelled():
            try:
                on_connections(count)
            except Exception:
                pass

    def track(response):
        with active_lock:
            active.add(response)

    def untrack(response):
        with active_lock:
            active.discard(response)

    def close_active():
        with active_lock:
            responses = list(active)
        for response in responses:
            _close_in_background(response)

    def wait(seconds, stop=None):
        deadline = time.monotonic() + seconds
        while True:
            if cancelled():
                raise DownloadCancelledError(CANCELLED_MESSAGE)
            if stop is not None and stop.is_set():
                raise _Stopped()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def open_url(url, extra_headers=None):
        request = urllib.request.Request(url, headers={**base_headers, **(extra_headers or {})})
        return opener.open(request, timeout=timeout)

    def probe(url):
        """(address to fetch, size) when the URL serves byte ranges, else None.

        A 206 answer to "Range: bytes=0-0" with "Content-Range: bytes 0-0/<size>" proves
        range support (Accept-Ranges is optional); the size comes from Content-Range,
        since Content-Length of that answer is 1. Any error leaves the URL to the
        single-connection path, which reports it.
        """
        try:
            response = open_url(url, {"Range": PROBE_RANGE})
        except urllib.error.HTTPError as error:
            _close_quietly(error)
            return None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
            return None
        track(response)
        try:
            if cancelled():
                raise DownloadCancelledError(CANCELLED_MESSAGE)
            served = _content_range(response.headers.get("Content-Range"))
            if (getattr(response, "status", 200) or 200) != 206 or not served or served[0] != 0:
                return None
            if str(response.headers.get("Accept-Ranges") or "").strip().lower() == "none":
                return None
            # The file's own address after redirects (Douyin's /play/ address redirects to a
            # CDN node), so every range is read from the same copy of the file.
            return (response.geturl() or url), served[2]
        finally:
            untrack(response)
            _close_quietly(response)

    def download_one(url, handle):
        downloaded = 0
        total = None
        attempt = 0
        tracker.restart()
        while True:
            if cancelled():
                raise DownloadCancelledError(CANCELLED_MESSAGE)
            try:
                response = open_url(url, {"Range": f"bytes={downloaded}-"} if downloaded else None)
            except urllib.error.HTTPError as error:
                _close_quietly(error)
                if error.code == 416 and total is not None and downloaded >= total:
                    return downloaded
                if error.code in NON_RETRYABLE_STATUS or attempt >= len(retry_delays):
                    raise SourceError(f"máy chủ trả HTTP {error.code}") from error
                attempt += 1
                wait(retry_delays[attempt - 1])
                continue
            except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                if attempt >= len(retry_delays):
                    raise SourceError(f"không kết nối được ({error})") from error
                attempt += 1
                wait(retry_delays[attempt - 1])
                continue
            track(response)
            try:
                if cancelled():
                    raise DownloadCancelledError(CANCELLED_MESSAGE)
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
                tracker.set(downloaded, total)
                while True:
                    chunk = response.read(chunk_size)
                    if cancelled():
                        raise DownloadCancelledError(CANCELLED_MESSAGE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    tracker.set(downloaded, total)
                if total is not None and downloaded < total:
                    raise _IncompleteTransfer(f"{downloaded}/{total} byte")
                tracker.set(downloaded, total if total is not None else downloaded, force=True)
                return downloaded
            except DownloadCancelledError:
                raise
            except (_IncompleteTransfer, OSError, http.client.HTTPException) as error:
                if cancelled():
                    raise DownloadCancelledError(CANCELLED_MESSAGE) from error
                if attempt >= len(retry_delays):
                    raise SourceError(f"kết nối bị ngắt khi đã tải {downloaded:,} byte ({error})") from error
                attempt += 1
                wait(retry_delays[attempt - 1])
            finally:
                untrack(response)
                _close_quietly(response)

    def fetch_range(url, total, first, last, stop):
        """Write bytes first..last of the file at their place in part_path."""
        position = first
        attempt = 0

        def retry_or_give_up(error, reason):
            nonlocal attempt
            if attempt >= len(retry_delays):
                raise _RangeUnavailable(f"đoạn {first:,}-{last:,}: {reason}") from error
            attempt += 1
            wait(retry_delays[attempt - 1], stop)

        # Each connection has its own handle, so seek and write never race between threads.
        with open(part_path, "r+b") as handle:
            while position <= last:
                if cancelled():
                    raise DownloadCancelledError(CANCELLED_MESSAGE)
                if stop.is_set():
                    raise _Stopped()
                try:
                    response = open_url(url, {"Range": f"bytes={position}-{last}"})
                except urllib.error.HTTPError as error:
                    _close_quietly(error)
                    if error.code in NON_RETRYABLE_STATUS:
                        raise _RangeUnavailable(f"đoạn {first:,}-{last:,}: HTTP {error.code}") from error
                    retry_or_give_up(error, f"HTTP {error.code}")
                    continue
                except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                    retry_or_give_up(error, f"không kết nối được ({error})")
                    continue
                track(response)
                try:
                    served = _content_range(response.headers.get("Content-Range"))
                    if (
                        (getattr(response, "status", 200) or 200) != 206
                        or not served
                        or served[0] != position
                        or served[2] != total
                    ):
                        raise _RangeUnavailable("máy chủ không trả đúng đoạn byte đã yêu cầu")
                    # A server may answer with a shorter range; the loop asks for the rest.
                    end = min(last, served[1])
                    handle.seek(position)
                    while position <= end:
                        chunk = response.read(min(chunk_size, end - position + 1))
                        if cancelled():
                            raise DownloadCancelledError(CANCELLED_MESSAGE)
                        if stop.is_set():
                            raise _Stopped()
                        if not chunk:
                            break
                        handle.write(chunk)
                        position += len(chunk)
                        tracker.add(len(chunk))
                    if position <= end:
                        raise _IncompleteTransfer(f"{position - first:,}/{last - first + 1:,} byte")
                except (_IncompleteTransfer, OSError, http.client.HTTPException) as error:
                    if cancelled():
                        raise DownloadCancelledError(CANCELLED_MESSAGE) from error
                    retry_or_give_up(error, f"kết nối bị ngắt ({error})")
                finally:
                    untrack(response)
                    _close_quietly(response)
        return last - first + 1

    def download_parallel(url, total):
        ranges = split_ranges(total, connections)
        with open(part_path, "wb") as handle:
            handle.truncate(total)
        tracker.restart(total)
        stop = threading.Event()
        errors = []
        executor = ThreadPoolExecutor(max_workers=len(ranges), thread_name_prefix="vietsub-range")
        try:
            futures = [executor.submit(fetch_range, url, total, first, last, stop) for first, last in ranges]
            for future in as_completed(futures):
                error = future.exception()
                if error is not None:
                    # One range failed for good: stop the others at their next read.
                    stop.set()
                    close_active()
                    errors.append(error)
        finally:
            # Wait for every connection, so their file handles are closed before the
            # .part file is replaced or deleted.
            executor.shutdown(wait=True)
        if cancelled() or any(isinstance(error, DownloadCancelledError) for error in errors):
            raise DownloadCancelledError(CANCELLED_MESSAGE)
        failures = [error for error in errors if not isinstance(error, _Stopped)]
        if failures:
            if isinstance(failures[0], _RangeUnavailable):
                raise failures[0]
            raise _RangeUnavailable(str(failures[0])) from failures[0]
        tracker.set(total, total, force=True)
        return total

    def fetch_url(url):
        """(size, connections used) after url is fully written to part_path."""
        ranged = probe(url) if connections > 1 else None
        if ranged is not None and ranged[1] > parallel_min_bytes:
            source, total = ranged
            count = len(split_ranges(total, connections))
            report_connections(count)
            notice(f"Máy chủ hỗ trợ tải theo đoạn: tải song song {count} kết nối "
                   f"({total / (1024 * 1024):,.1f} MB).")
            try:
                return download_parallel(source, total), count
            except _RangeUnavailable as error:
                notice(f"Tải song song không ổn định ({error}); tải lại bằng 1 kết nối.")
                _remove_quietly(part_path)
        report_connections(1)
        with open(part_path, "wb") as handle:
            return download_one(url, handle), 1

    def worker():
        errors = []
        try:
            for url in candidates:
                try:
                    size, used = fetch_url(url)
                    if validate is not None:
                        validate(part_path)
                    if cancelled():
                        raise DownloadCancelledError(CANCELLED_MESSAGE)
                    os.replace(part_path, destination)
                    outcome["value"] = {"path": destination, "url": url, "bytes": size, "connections": used}
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
            close_active()
            done.wait(1.0)
            raise DownloadCancelledError(CANCELLED_MESSAGE)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]
