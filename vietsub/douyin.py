"""Native Douyin video resolution, metadata extraction, stream download, and history."""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from vietsub.constants import (
    ALLOWED_THUMBNAIL_EXTENSIONS,
    DOUYIN_HTTP_TIMEOUT_SECONDS,
    DOUYIN_MAX_HTML_BYTES,
    DOUYIN_STREAM_TIMEOUT_SECONDS,
    MAX_DOWNLOAD_HISTORY_ITEMS,
    MAX_LOCAL_VIDEO_BYTES,
)
from vietsub.validators import (
    validate_douyin_thumbnail_url,
    validate_douyin_url,
    validate_douyin_video_stream_url,
    validate_local_mp4_file,
)

MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
)

ROUTER_DATA_PATTERNS = (
    re.compile(
        r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*(?:;|</script>)",
        re.DOTALL,
    ),
    re.compile(
        r"_ROUTER_DATA\s*=\s*(\{.*?\})\s*(?:;|</script>)",
        re.DOTALL,
    ),
)
RENDER_DATA_PATTERN = re.compile(
    r'<script[^>]+id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


class _SafeDouyinRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or (
            host not in {"douyin.com", "iesdouyin.com"}
            and not host.endswith((".douyin.com", ".iesdouyin.com"))
        ):
            raise urllib.error.URLError("Chuyển hướng ra ngoài tên miền Douyin hợp lệ.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def normalize_douyin_stream_url(url):
    """Convert a watermarked Douyin playwm URL into a clean watermark-free stream URL."""
    validated = validate_douyin_video_stream_url(url)
    if not validated:
        return ""
    no_wm = validated.replace("/playwm/", "/play/")
    no_wm = re.sub(r"([?&])watermark=1\b", r"\1watermark=0", no_wm)
    return validate_douyin_video_stream_url(no_wm) or validated


def _collect_url_list(node):
    if isinstance(node, dict):
        urls = node.get("url_list")
        if isinstance(urls, list):
            return [str(item) for item in urls if isinstance(item, str) and item.strip()]
    return []


def _find_aweme_candidates(data):
    """Yield candidate aweme/video item dictionaries from nested Douyin JSON payloads."""
    if isinstance(data, dict):
        if isinstance(data.get("video"), dict) and (
            "play_addr" in data["video"]
            or "bit_rate" in data["video"]
            or "download_addr" in data["video"]
        ):
            yield data
        if isinstance(data.get("aweme_detail"), dict):
            yield from _find_aweme_candidates(data["aweme_detail"])
        if isinstance(data.get("item_list"), list):
            for item in data["item_list"]:
                yield from _find_aweme_candidates(item)
        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from _find_aweme_candidates(value)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                yield from _find_aweme_candidates(item)


def extract_douyin_video_payload(data, expected_video_id=""):
    """Extract (video_id, title, stream_urls, cover_url) from a parsed Douyin JSON object."""
    candidates = list(_find_aweme_candidates(data))
    if not candidates:
        return None

    selected = None
    if expected_video_id:
        for item in candidates:
            item_id = str(item.get("aweme_id") or item.get("id") or "").strip()
            if item_id == str(expected_video_id):
                selected = item
                break
    if selected is None:
        selected = candidates[0]

    video_id = str(
        selected.get("aweme_id") or selected.get("id") or expected_video_id or ""
    ).strip()
    title = str(
        selected.get("desc")
        or selected.get("caption")
        or (selected.get("share_info") or {}).get("share_title")
        or ""
    ).strip()

    video = selected.get("video") if isinstance(selected.get("video"), dict) else {}
    raw_stream_urls = []

    # 1. Prefer highest quality bit_rate streams if present
    bit_rates = video.get("bit_rate")
    if isinstance(bit_rates, list):
        sorted_rates = sorted(
            (item for item in bit_rates if isinstance(item, dict)),
            key=lambda item: (
                int(item.get("FPS") or 0),
                int(item.get("bit_rate") or 0),
            ),
            reverse=True,
        )
        for rate in sorted_rates:
            raw_stream_urls.extend(_collect_url_list(rate.get("play_addr")))

    # 2. Standard play_addr (converted from playwm -> play)
    for key in ("play_addr", "play_addr_h264", "play_addr_265", "download_addr"):
        raw_stream_urls.extend(_collect_url_list(video.get(key)))

    stream_urls = []
    seen = set()
    for raw_url in raw_stream_urls:
        clean_url = normalize_douyin_stream_url(raw_url)
        if clean_url and clean_url not in seen:
            seen.add(clean_url)
            stream_urls.append(clean_url)

    cover_url = ""
    for key in ("cover_original_scale", "origin_cover", "cover", "dynamic_cover"):
        for url in _collect_url_list(video.get(key)):
            validated_cover = validate_douyin_thumbnail_url(url)
            if validated_cover:
                cover_url = validated_cover
                break
        if cover_url:
            break

    if not stream_urls:
        return None

    return {
        "video_id": video_id,
        "video_title": title,
        "stream_urls": stream_urls,
        "cover_url": cover_url,
    }


def extract_douyin_metadata_from_html(html_text, expected_video_id=""):
    """Parse _ROUTER_DATA or RENDER_DATA embedded in a Douyin/iesdouyin HTML page."""
    if not isinstance(html_text, str) or not html_text.strip():
        return None

    json_blobs = []
    for pattern in ROUTER_DATA_PATTERNS:
        for match in pattern.finditer(html_text):
            json_blobs.append(match.group(1).strip())

    for match in RENDER_DATA_PATTERN.finditer(html_text):
        raw = match.group(1).strip()
        if "%" in raw:
            raw = urllib.parse.unquote(raw)
        json_blobs.append(raw)

    for blob in json_blobs:
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        extracted = extract_douyin_video_payload(parsed, expected_video_id=expected_video_id)
        if extracted:
            return extracted

    return None


def resolve_short_url_via_http(short_url, timeout=DOUYIN_HTTP_TIMEOUT_SECONDS):
    """Resolve a v.douyin.com share URL via HTTP redirects without launching a browser."""
    opener = urllib.request.build_opener(_SafeDouyinRedirectHandler())
    request = urllib.request.Request(
        short_url,
        headers={
            "User-Agent": MOBILE_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl() if hasattr(response, "geturl") else short_url
        return validate_douyin_url(final_url)


def fetch_douyin_share_page_metadata(video_id, source_url="", timeout=DOUYIN_HTTP_TIMEOUT_SECONDS):
    """Fetch metadata for a Douyin video from iesdouyin share page or source URL over HTTP."""
    urls_to_try = [f"https://www.iesdouyin.com/share/video/{video_id}/"]
    if source_url and source_url not in urls_to_try:
        urls_to_try.append(source_url)

    opener = urllib.request.build_opener(_SafeDouyinRedirectHandler())
    last_error = None
    for url in urls_to_try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": MOBILE_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.douyin.com/",
            },
        )
        try:
            with opener.open(req, timeout=timeout) as response:
                raw_html = response.read(DOUYIN_MAX_HTML_BYTES).decode("utf-8", errors="replace")
            extracted = extract_douyin_metadata_from_html(raw_html, expected_video_id=video_id)
            if extracted:
                return extracted
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = error
            continue

    if last_error:
        raise RuntimeError(f"Không thể lấy thông tin video Douyin qua HTTP: {last_error}") from last_error
    return None


def find_video_thumbnail_file(video_path):
    """Find a local companion thumbnail file next to a downloaded/cached video."""
    if not isinstance(video_path, str) or not video_path:
        return ""
    video_path = os.path.abspath(video_path)
    video_dir = os.path.dirname(video_path)
    video_stem = os.path.splitext(os.path.basename(video_path))[0]
    if not os.path.isdir(video_dir):
        return ""

    for name in ("cover", "origin_cover", "thumbnail", "thumb"):
        for separator in ("_", ".", "-"):
            for extension in ALLOWED_THUMBNAIL_EXTENSIONS:
                candidate = os.path.join(video_dir, f"{video_stem}{separator}{name}{extension}")
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                    return candidate
    return ""


def get_cached_video_metadata(video_id, download_cache_dir):
    """Read cached metadata JSON for a downloaded Douyin video if available."""
    if not video_id or not download_cache_dir:
        return {}
    meta_path = os.path.join(download_cache_dir, str(video_id), f"{video_id}_data.json")
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def load_download_history(history_path):
    if not history_path or not os.path.isfile(history_path):
        return []
    try:
        with open(history_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        aweme_id = str(item.get("aweme_id") or item.get("video_id") or "").strip()
        if not aweme_id:
            continue
        normalized.append({
            "aweme_id": aweme_id,
            "title": str(item.get("title") or "").strip(),
            "download_time": int(item.get("download_time") or 0),
            "file_path": str(item.get("file_path") or "").strip(),
            "cover_url": validate_douyin_thumbnail_url(str(item.get("cover_url") or "")),
            "source_url": str(item.get("source_url") or "").strip(),
        })
    normalized.sort(key=lambda entry: entry["download_time"], reverse=True)
    return normalized[:MAX_DOWNLOAD_HISTORY_ITEMS]


def save_download_history(history_path, items):
    os.makedirs(os.path.dirname(history_path) or ".", exist_ok=True)
    temporary_path = f"{history_path}.{uuid.uuid4().hex}.tmp"
    payload = {
        "version": 1,
        "items": list(items)[:MAX_DOWNLOAD_HISTORY_ITEMS],
    }
    try:
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temporary_path, history_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def record_download_history(
    history_path,
    *,
    video_id,
    title="",
    file_path="",
    cover_url="",
    source_url="",
):
    if not history_path or not video_id:
        return
    video_id = str(video_id).strip()
    existing = load_download_history(history_path)
    now_epoch = int(time.time())
    updated_entry = {
        "aweme_id": video_id,
        "title": str(title or "").strip(),
        "download_time": now_epoch,
        "file_path": os.path.abspath(file_path) if file_path else "",
        "cover_url": validate_douyin_thumbnail_url(cover_url),
        "source_url": str(source_url or "").strip(),
    }
    filtered = []
    for item in existing:
        if item["aweme_id"] == video_id:
            if not updated_entry["title"] and item.get("title"):
                updated_entry["title"] = item["title"]
            if not updated_entry["cover_url"] and item.get("cover_url"):
                updated_entry["cover_url"] = item["cover_url"]
            if not updated_entry["source_url"] and item.get("source_url"):
                updated_entry["source_url"] = item["source_url"]
            continue
        filtered.append(item)
    filtered.insert(0, updated_entry)
    try:
        save_download_history(history_path, filtered)
    except OSError:
        pass


def update_download_history_path(history_path, video_id, destination_video):
    if not history_path or not video_id or not os.path.isfile(history_path):
        return False
    video_id = str(video_id).strip()
    items = load_download_history(history_path)
    updated = False
    for item in items:
        if item["aweme_id"] == video_id:
            item["file_path"] = os.path.abspath(destination_video)
            updated = True
            break
    if not updated:
        return False
    try:
        save_download_history(history_path, items)
        return True
    except OSError:
        return False


def download_douyin_stream(
    stream_urls,
    destination_path,
    *,
    check_cancel=None,
    log_fn=None,
    timeout=DOUYIN_STREAM_TIMEOUT_SECONDS,
):
    """Download a Douyin MP4 stream from candidate CDN URLs with size & header validation."""
    if isinstance(stream_urls, str):
        stream_urls = [stream_urls]
    candidates = [
        validated
        for url in (stream_urls or [])
        if (validated := validate_douyin_video_stream_url(url))
    ]
    if not candidates:
        raise ValueError("Không tìm thấy URL stream video Douyin hợp lệ.")

    os.makedirs(os.path.dirname(destination_path) or ".", exist_ok=True)
    last_error = None

    for stream_url in candidates:
        if check_cancel:
            check_cancel()
        temporary_path = f"{destination_path}.{uuid.uuid4().hex}.part"
        req = urllib.request.Request(
            stream_url,
            headers={
                "User-Agent": DESKTOP_USER_AGENT,
                "Accept": "*/*",
                "Referer": "https://www.douyin.com/",
            },
        )
        try:
            with (
                urllib.request.urlopen(req, timeout=timeout) as response,
                open(temporary_path, "wb") as output,
            ):
                declared_size = 0
                if hasattr(response, "headers") and response.headers is not None:
                    try:
                        declared_size = int(response.headers.get("Content-Length") or 0)
                    except (TypeError, ValueError):
                        declared_size = 0
                if declared_size > MAX_LOCAL_VIDEO_BYTES:
                    raise ValueError("Video Douyin vượt quá giới hạn 8 GiB của ứng dụng.")

                downloaded = 0
                last_bucket = 0
                while True:
                    if check_cancel:
                        check_cancel()
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_LOCAL_VIDEO_BYTES:
                        raise ValueError("Video Douyin vượt quá giới hạn 8 GiB của ứng dụng.")
                    output.write(chunk)
                    if declared_size > 0 and log_fn:
                        bucket = min(10, int(downloaded * 10 / declared_size))
                        if bucket > last_bucket:
                            last_bucket = bucket
                            log_fn(f"Tải video Douyin: {bucket * 10}%")
                output.flush()

            validate_local_mp4_file(temporary_path)
            os.replace(temporary_path, destination_path)
            return destination_path
        except Exception as error:
            last_error = error
            if os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass
            # Propagate cancellation immediately
            if check_cancel and type(error).__name__ == "WorkflowCancelled":
                raise
            continue

    raise RuntimeError(f"Không thể tải file MP4 từ máy chủ Douyin: {last_error}") from last_error
