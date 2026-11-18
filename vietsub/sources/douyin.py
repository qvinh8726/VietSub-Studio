"""Douyin links, aweme metadata, and the Edge page capture used to download videos.

Douyin signs its web API requests (a_bogus / X-Bogus) in page JavaScript, and the
algorithm changes often. VietSub Studio does not re-implement it. It opens the video
page in the user's own Edge profile and reads the JSON that the page itself receives
from /aweme/v1/web/aweme/detail/, with the page's SSR data and <video> element as
fallbacks.
"""

import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

from .base import (
    DEFAULT_USER_AGENT,
    DownloadCancelledError,
    SourceError,
    follow_redirects,
    host_matches,
    normalize_media_url,
)
from .browser_capture import DOM_VIDEO_SCRIPT, MUTE_MEDIA_SCRIPT, is_media_response

DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")
SHORT_LINK_HOSTS = {"v.douyin.com"}
CANONICAL_VIDEO_URL = "https://www.douyin.com/video/{}"
DETAIL_API_MARKER = "/aweme/v1/web/aweme/detail"
DOUYIN_REFERER = "https://www.douyin.com/"
CAPTCHA_NOTICE = "Vui lòng kéo captcha trên trình duyệt Edge để tiếp tục tải"

_AWEME_ID = r"\d{1,25}"
PATH_ID_PATTERN = re.compile(rf"/(?:share/)?(video|note|slides)/({_AWEME_ID})(?:/|$)")
ID_QUERY_KEYS = ("modal_id", "aweme_id", "item_id", "vid")
CAPTCHA_TITLE_PATTERN = re.compile(r"验证码|安全验证|人机验证|captcha", re.IGNORECASE)
MAX_SEARCH_NODES = 200_000


@dataclass(frozen=True)
class DouyinLink:
    url: str
    aweme_id: str
    kind: str  # "video", "note" or "short"


@dataclass
class DouyinVideo:
    aweme_id: str
    title: str
    author: str = ""
    duration_seconds: float = None
    play_urls: list = field(default_factory=list)
    cover_urls: list = field(default_factory=list)


def is_douyin_host(host):
    return host_matches(host, DOUYIN_DOMAINS)


def extract_aweme_id(url):
    """Return ("video" | "note", aweme_id) for a Douyin page URL, or None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    match = PATH_ID_PATTERN.search(parsed.path or "")
    if match:
        kind = "note" if match.group(1) in ("note", "slides") else "video"
        return kind, match.group(2)
    query = parse_qs(parsed.query)
    for key in ID_QUERY_KEYS:
        for value in query.get(key, []):
            value = value.strip()
            if re.fullmatch(_AWEME_ID, value):
                return "video", value
    return None


def canonical_video_url(aweme_id):
    aweme_id = str(aweme_id or "").strip()
    if not re.fullmatch(_AWEME_ID, aweme_id):
        raise ValueError("ID video Douyin không hợp lệ.")
    return CANONICAL_VIDEO_URL.format(aweme_id)


def parse_douyin_url(url):
    """Validate one Douyin URL and normalize it.

    Video, share-page, note and feed/profile modal links (?modal_id=) become the
    canonical https://www.douyin.com/video/<id>. v.douyin.com share links are kept
    for resolution. Profiles, mixes and collections have no single video and fail.
    """
    if not isinstance(url, str):
        raise ValueError("Link Douyin không hợp lệ.")
    candidate = url.strip()
    try:
        parsed = urlparse(candidate)
    except ValueError as error:
        raise ValueError("Link Douyin không hợp lệ.") from error
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not is_douyin_host(host):
        raise ValueError("Link không thuộc Douyin.")
    if host in SHORT_LINK_HOSTS:
        code = (parsed.path or "").strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", code):
            raise ValueError("Link share Douyin không hợp lệ.")
        return DouyinLink(url=f"https://v.douyin.com/{code}/", aweme_id="", kind="short")
    found = extract_aweme_id(candidate)
    if not found:
        raise ValueError(
            "Chỉ hỗ trợ link video Douyin riêng lẻ; link hồ sơ, mix, collection hoặc playlist "
            "không chứa ID của một video."
        )
    kind, aweme_id = found
    if kind == "note":
        return DouyinLink(url=f"https://www.douyin.com/note/{aweme_id}", aweme_id=aweme_id, kind="note")
    return DouyinLink(url=canonical_video_url(aweme_id), aweme_id=aweme_id, kind="video")


def resolve_short_link(url, *, opener=None, timeout=10, max_hops=5, user_agent=DEFAULT_USER_AGENT):
    """Follow a v.douyin.com share link over HTTP and return the aweme ID.

    Every redirect must stay on a Douyin host. Raises SourceError when the link
    leaves Douyin, loops, or never reaches a video page; the caller can then fall
    back to resolving it in Edge.
    """

    def aweme_id_of(candidate):
        found = extract_aweme_id(candidate)
        return found[1] if found else None

    return follow_redirects(
        url,
        allowed_host=is_douyin_host,
        label="Douyin",
        accept=aweme_id_of,
        opener=opener,
        timeout=timeout,
        max_hops=max_hops,
        user_agent=user_agent,
    )


def _aweme_id_of(node):
    if not isinstance(node, dict):
        return ""
    return str(node.get("aweme_id") or node.get("awemeId") or "").strip()


def find_aweme_detail(payload, aweme_id):
    """Find the post with this aweme ID in an API payload or in page SSR data."""
    aweme_id = str(aweme_id or "").strip()
    if not aweme_id:
        return None
    if isinstance(payload, dict) and _aweme_id_of(payload.get("aweme_detail")) == aweme_id:
        return payload["aweme_detail"]
    stack = [payload]
    visited = 0
    while stack and visited < MAX_SEARCH_NODES:
        node = stack.pop()
        visited += 1
        if isinstance(node, dict):
            if _aweme_id_of(node) == aweme_id and isinstance(node.get("video"), dict):
                return node
            stack.extend(value for value in node.values() if isinstance(value, (dict, list)))
        elif isinstance(node, list):
            stack.extend(value for value in node if isinstance(value, (dict, list)))
    return None


def parse_render_data(text):
    """Decode the URL-encoded JSON in <script id="RENDER_DATA">; None when it is not JSON."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(unquote(text.strip()))
    except ValueError:
        return None


def _flag(value):
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def _number(*values):
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0.0


def _clean_play_url(url):
    url = normalize_media_url(url)
    if not url:
        return ""
    parsed = urlparse(url)
    if parse_qs(parsed.query).get("watermark", ["0"])[0] == "1":
        return ""
    # /playwm/ is the watermarked variant of the same file; /play/ serves the clean one.
    if "/aweme/v1/playwm/" in parsed.path:
        url = url.replace("/aweme/v1/playwm/", "/aweme/v1/play/", 1)
    return url


def _url_list(address):
    """URLs from an API address ({"url_list": [...]}) or an SSR one ([{"src": ...}] or a string)."""
    if isinstance(address, dict):
        items = address.get("url_list") or []
    elif isinstance(address, list):
        items = [item.get("src") if isinstance(item, dict) else item for item in address]
    elif isinstance(address, str):
        items = [address]
    else:
        items = []
    return [item for item in items if isinstance(item, str)]


def _dedupe(urls):
    result = []
    for url in urls:
        if url and url not in result:
            result.append(url)
    return result


def select_play_urls(video):
    """Order watermark-free MP4 URLs from best to worst.

    H.264 renditions come first (highest resolution, then bitrate): VideOCR, FFmpeg
    and the WebView2 preview decode them everywhere. H.265/ByteVC1 renditions and the
    default play address follow as fallbacks.
    """
    if not isinstance(video, dict):
        return []
    ranked = []
    for entry in video.get("bit_rate") or video.get("bitRateList") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("format") or "").lower() == "dash":
            continue
        address = entry.get("play_addr") or entry.get("playAddr")
        urls = [_clean_play_url(url) for url in _url_list(address)]
        urls = [url for url in urls if url]
        if not urls:
            continue
        address = address if isinstance(address, dict) else {}
        width = _number(address.get("width"), entry.get("width"))
        height = _number(address.get("height"), entry.get("height"))
        bitrate = _number(entry.get("bit_rate"), entry.get("bitRate"))
        hevc = any(
            _flag(entry.get(key)) for key in ("is_h265", "isH265", "is_bytevc1", "isBytevc1")
        )
        ranked.append((1 if hevc else 0, -(width * height), -bitrate, urls))
    ranked.sort(key=lambda item: item[:3])

    def clean(address):
        return [url for url in (_clean_play_url(item) for item in _url_list(address)) if url]

    h264 = [url for item in ranked if item[0] == 0 for url in item[3]]
    hevc = [url for item in ranked if item[0] == 1 for url in item[3]]
    return _dedupe(
        h264
        + clean(video.get("play_addr_h264") or video.get("playAddrH264"))
        + hevc
        + clean(video.get("play_addr") or video.get("playAddr"))
    )


def parse_aweme_detail(detail):
    """Turn an aweme_detail (API or SSR shape) into title, author, duration and URLs."""
    if not isinstance(detail, dict):
        raise SourceError("Dữ liệu video Douyin không hợp lệ.")
    aweme_id = _aweme_id_of(detail)
    video = detail.get("video") if isinstance(detail.get("video"), dict) else {}
    author = detail.get("author") or detail.get("authorInfo") or {}
    title = " ".join(str(detail.get("desc") or "").split())
    duration_ms = _number(video.get("duration"), detail.get("duration"))
    play_urls = select_play_urls(video)
    if not play_urls:
        if detail.get("images") or detail.get("image_infos") or detail.get("imageInfos"):
            raise SourceError("Bài đăng Douyin này là ảnh (图文), không có video để OCR.")
        raise SourceError("Douyin không trả về địa chỉ video cho bài đăng này.")
    covers = []
    for key in ("origin_cover", "originCover", "cover", "cover_original_scale"):
        covers.extend(normalize_media_url(url) for url in _url_list(video.get(key)))
    return DouyinVideo(
        aweme_id=aweme_id,
        title=title or (f"Douyin {aweme_id}" if aweme_id else "Video Douyin"),
        author=str(author.get("nickname") or "").strip() if isinstance(author, dict) else "",
        duration_seconds=duration_ms / 1000 if duration_ms else None,
        play_urls=play_urls,
        cover_urls=_dedupe(covers),
    )


def is_captcha_probe(probe):
    """True when the page shows Douyin's captcha (from CAPTCHA_PROBE_SCRIPT)."""
    if not isinstance(probe, dict):
        return False
    return bool(probe.get("captcha")) or bool(CAPTCHA_TITLE_PATTERN.search(str(probe.get("title") or "")))


# Runs in the page. Douyin's slider captcha lives in a container or an iframe from
# its verification center; the title changes to 验证码中间页 on the full-page variant.
CAPTCHA_PROBE_SCRIPT = """() => {
    const selectors = ['#captcha_container', '#captcha-verify-image', '.captcha_verify_container',
        '.captcha-verify-container', '[id^="captcha"]', '[class*="captcha_verify"]',
        '[class*="secsdk-captcha"]', 'iframe[src*="captcha"]', 'iframe[src*="verifycenter"]',
        'iframe[src*="verify.snssdk"]'];
    const visible = element => {
        const box = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const captcha = selectors.some(selector => Array.from(document.querySelectorAll(selector)).some(visible));
    return {captcha, title: document.title || ''};
}"""

RENDER_DATA_SCRIPT = """() => {
    const script = document.getElementById('RENDER_DATA');
    return script ? script.textContent : '';
}"""


@dataclass
class CaptureResult:
    detail: dict = None
    dom_urls: list = field(default_factory=list)
    network_urls: list = field(default_factory=list)
    user_agent: str = ""
    page_title: str = ""
    captcha_seen: bool = False


def capture_video_from_page(
    context,
    aweme_id,
    *,
    url=None,
    is_cancelled=lambda: False,
    on_captcha=None,
    allow_captcha_wait=True,
    load_timeout=45,
    captcha_timeout=180,
    poll_interval_ms=500,
    clock=time.monotonic,
):
    """Open the video page in a new tab of an existing browser context and capture its data.

    Returns a CaptureResult with the aweme_detail JSON, or with media URLs read from
    the page when the API response could not be read. on_captcha(active, page) is
    called when a captcha appears and when it is gone; the wait is then extended to
    captcha_timeout seconds. With allow_captcha_wait=False (background prefetch) a
    captcha stops the capture at once.
    """
    target_url = url or canonical_video_url(aweme_id)
    page = context.new_page()
    detail_responses = []
    network_urls = []

    def on_response(response):
        try:
            response_url = response.url
        except Exception:
            return
        if DETAIL_API_MARKER in response_url:
            detail_responses.append(response)
        elif is_media_response(response):
            media_url = normalize_media_url(response_url)
            if media_url and media_url not in network_urls:
                network_urls.append(media_url)

    result = CaptureResult()
    captcha_active = False
    try:
        try:
            page.add_init_script(MUTE_MEDIA_SCRIPT)
        except Exception:
            pass
        page.on("response", on_response)
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=int(load_timeout * 1000))
        except Exception:
            # A slow page still delivers the API response; keep polling until the deadline.
            pass
        deadline = clock() + load_timeout
        next_ssr_check = 0.0
        reloaded_after_captcha = False
        while True:
            if is_cancelled():
                raise DownloadCancelledError("Đã huỷ tải video Douyin.")
            while detail_responses:
                response = detail_responses.pop(0)
                try:
                    payload = response.json()
                except Exception:
                    continue
                detail = find_aweme_detail(payload, aweme_id)
                if detail:
                    result.detail = detail
                    break
            if result.detail is None and clock() >= next_ssr_check:
                next_ssr_check = clock() + 2
                try:
                    detail = find_aweme_detail(parse_render_data(page.evaluate(RENDER_DATA_SCRIPT)), aweme_id)
                except Exception:
                    detail = None
                if detail:
                    result.detail = detail
            if result.detail is not None:
                break
            try:
                probe = page.evaluate(CAPTCHA_PROBE_SCRIPT)
            except Exception:
                probe = None
            if is_captcha_probe(probe):
                result.captcha_seen = True
                if not allow_captcha_wait:
                    raise SourceError("Douyin yêu cầu kéo captcha; bỏ qua lượt tải chạy ngầm.")
                if not captcha_active:
                    captcha_active = True
                    deadline = clock() + captcha_timeout
                    if on_captcha:
                        on_captcha(True, page)
            elif captcha_active:
                captcha_active = False
                if on_captcha:
                    on_captcha(False, page)
                deadline = max(deadline, clock() + 20)
                if not reloaded_after_captcha:
                    # The page usually reloads itself after the captcha; reload once if not.
                    reloaded_after_captcha = True
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=int(load_timeout * 1000))
                    except Exception:
                        pass
            if clock() >= deadline:
                break
            page.wait_for_timeout(poll_interval_ms)
        if result.detail is None:
            try:
                result.dom_urls = [
                    url for url in (normalize_media_url(item) for item in page.evaluate(DOM_VIDEO_SCRIPT) or [])
                    if url
                ]
            except Exception:
                result.dom_urls = []
        result.network_urls = list(network_urls)
        try:
            result.user_agent = str(page.evaluate("() => navigator.userAgent") or "")
        except Exception:
            result.user_agent = ""
        try:
            result.page_title = str(page.title() or "")
        except Exception:
            result.page_title = ""
        if result.detail is None and not result.dom_urls and not result.network_urls:
            if result.captcha_seen:
                raise SourceError(
                    f"Hết {int(captcha_timeout // 60)} phút chờ giải captcha Douyin; hãy kéo captcha trong Edge "
                    "rồi chạy lại."
                )
            raise SourceError(
                "Không đọc được dữ liệu video từ trang Douyin. Video có thể ở chế độ riêng tư, bị giới hạn "
                "vùng, hoặc Douyin vừa đổi giao diện."
            )
        return result
    finally:
        if captcha_active and on_captcha:
            # Clear the "drag the captcha" prompt even when the capture failed or was cancelled.
            try:
                on_captcha(False, page)
            except Exception:
                pass
        try:
            page.close()
        except Exception:
            pass


def title_from_page(page_title, aweme_id):
    """Fallback title from the browser tab, without the "- 抖音" suffix."""
    title = re.sub(r"\s*[-_|]\s*抖音.*$", "", str(page_title or "")).strip()
    return title or f"Douyin {aweme_id}"
