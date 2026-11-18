"""Douyin links, aweme metadata, the public share page and the browser page capture.

Douyin signs its web API requests (a_bogus / X-Bogus) in page JavaScript, and the
algorithm changes often. VietSub Studio does not re-implement it. It first reads the
public share page over HTTP; when that page has no data, it opens the video in the
automation browser's own profile (Chrome by default) and reads the JSON that the page
itself receives from /aweme/v1/web/aweme/detail/, with the page's SSR data and the
modal's <video> element as fallbacks. A login wall stops the capture at once.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

from .base import (
    DEFAULT_USER_AGENT,
    DownloadCancelledError,
    SourceError,
    follow_redirects,
    host_matches,
    normalize_media_url,
    url_host,
)
from .browser_capture import MUTE_MEDIA_SCRIPT

DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")
SHORT_LINK_HOSTS = {"v.douyin.com"}
CANONICAL_VIDEO_URL = "https://www.douyin.com/video/{}"
# The feed page with the video in a modal; it plays for guests where /video/<id> may not.
JINGXUAN_MODAL_URL = "https://www.douyin.com/jingxuan?modal_id={}"
DETAIL_API_MARKER = "/aweme/v1/web/aweme/detail"
DOUYIN_REFERER = "https://www.douyin.com/"
# {browser} is the short name of the automation browser ("Chrome", "Edge"...).
CAPTCHA_NOTICE = "Vui lòng kéo captcha trên trình duyệt {browser} để tiếp tục tải"
# Public share page that the phone app's links open; parsed without a browser.
SHARE_PAGE_URL = "https://www.iesdouyin.com/share/video/{}/"
SHARE_REFERER = "https://www.iesdouyin.com/"
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
MAX_SHARE_PAGE_BYTES = 4 * 1024 * 1024
ROUTER_DATA_PATTERN = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)
RENDER_DATA_PATTERN = re.compile(r'<script[^>]*\bid="RENDER_DATA"[^>]*>(.*?)</script>', re.S)
# Guests see Douyin's login popup on every page; it only counts when no video data came.
LOGIN_GRACE_SECONDS = 1.5
# Rendition choice for OCR: 720p-1080p (smaller side, in pixels) at 1.5-3.5 Mbps first.
OCR_SHORT_SIDE = (720, 1080)
MODERATE_BITRATE = (1_500_000, 3_500_000)
HEAVY_BITRATE = 5_000_000
GEAR_SIZE_PATTERN = re.compile(r"(?:^|_)(\d{3,4})(?:p)?(?:_|$)")

_AWEME_ID = r"\d{1,25}"
PATH_ID_PATTERN = re.compile(rf"/(?:share/)?(video|note|slides)/({_AWEME_ID})(?:/|$)")
ID_QUERY_KEYS = ("modal_id", "aweme_id", "item_id", "vid")
CAPTCHA_TITLE_PATTERN = re.compile(r"验证码|安全验证|人机验证|captcha", re.IGNORECASE)
MAX_SEARCH_NODES = 200_000


class LoginRequiredError(SourceError):
    """Douyin shows this video only to signed-in users.

    reason is "redirect" when the page left the video (for example to the feed) and
    "login" when the login popup stays while no video data arrived.
    """

    def __init__(self, reason="login"):
        self.reason = reason
        super().__init__(
            "Douyin chuyển trang khỏi video này vì cần đăng nhập."
            if reason == "redirect"
            else "Douyin hiện bảng đăng nhập thay vì video này."
        )


class ImagePostError(SourceError):
    """The post is a photo set (图文): there is no video to OCR."""


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
    # "H.264 1080p, 2.4 Mbps" when the first play URL comes from Douyin's rendition list.
    play_label: str = ""


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
    back to resolving it in the automation browser.
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


def _short_side(width, height, gear_name):
    """Smaller side in pixels (720 for 720x1280), or the size in the gear name ("normal_1080_0")."""
    sides = [side for side in (width, height) if side > 0]
    if sides:
        return min(sides)
    match = GEAR_SIZE_PATTERN.search(str(gear_name or ""))
    return float(match.group(1)) if match else 0.0


def _bitrate_class(bitrate):
    """0 moderate, 1 light, 2 unknown, 3 heavy, 4 very heavy (lower is tried first)."""
    if not bitrate:
        return 2
    if bitrate < MODERATE_BITRATE[0]:
        return 1
    if bitrate <= MODERATE_BITRATE[1]:
        return 0
    return 3 if bitrate <= HEAVY_BITRATE else 4


def rendition_rank(hevc, short_side, bitrate):
    """Sort key of one rendition for OCR; smaller keys are tried first.

    H.264 always comes before H.265/ByteVC1, because VideOCR, FFmpeg and the WebView2
    preview decode it everywhere. Then 720p-1080p before other sizes, and within them a
    moderate bitrate (1.5-3.5 Mbps) first: burned-in subtitles stay as sharp for OCR as
    in a 5+ Mbps rendition, while the file is several times smaller. Light renditions
    follow, then heavy ones (lightest first); a higher resolution wins within a class.
    """
    in_band = OCR_SHORT_SIDE[0] <= short_side <= OCR_SHORT_SIDE[1]
    bitrate_class = _bitrate_class(bitrate)
    return (
        1 if hevc else 0,
        0 if in_band else 1,
        bitrate_class,
        -short_side,
        -bitrate if bitrate_class <= 1 else bitrate,
    )


def ranked_renditions(video):
    """Renditions of the bit_rate list, best first for OCR: [{"urls", "hevc", "short_side", "bitrate"}]."""
    if not isinstance(video, dict):
        return []
    renditions = []
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
        renditions.append({
            "urls": urls,
            "hevc": any(
                _flag(entry.get(key)) for key in ("is_h265", "isH265", "is_bytevc1", "isBytevc1")
            ),
            "short_side": _short_side(width, height, entry.get("gear_name") or entry.get("gearName")),
            "bitrate": _number(entry.get("bit_rate"), entry.get("bitRate")),
        })
    # sort() is stable, so equal renditions keep the order Douyin listed them in.
    renditions.sort(key=lambda item: rendition_rank(item["hevc"], item["short_side"], item["bitrate"]))
    return renditions


def describe_rendition(rendition):
    """"H.264 1080p, 2.4 Mbps" for logs."""
    parts = ["H.265" if rendition["hevc"] else "H.264"]
    if rendition["short_side"]:
        parts.append(f"{int(rendition['short_side'])}p")
    text = " ".join(parts)
    if rendition["bitrate"]:
        text += f", {rendition['bitrate'] / 1_000_000:.1f} Mbps"
    return text


def select_play_urls(video):
    """Order watermark-free MP4 URLs from best to worst for OCR (see rendition_rank).

    H.265/ByteVC1 renditions and the default play address follow the H.264 ones as fallbacks.
    """
    if not isinstance(video, dict):
        return []
    renditions = ranked_renditions(video)

    def clean(address):
        return [url for url in (_clean_play_url(item) for item in _url_list(address)) if url]

    h264 = [url for item in renditions if not item["hevc"] for url in item["urls"]]
    hevc = [url for item in renditions if item["hevc"] for url in item["urls"]]
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
    renditions = ranked_renditions(video)
    play_label = (
        describe_rendition(renditions[0])
        if renditions and play_urls and renditions[0]["urls"][0] == play_urls[0]
        else ""
    )
    if not play_urls:
        if detail.get("images") or detail.get("image_infos") or detail.get("imageInfos"):
            raise ImagePostError("Bài đăng Douyin này là ảnh (图文), không có video để OCR.")
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
        play_label=play_label,
    )


def share_page_payloads(html):
    """JSON blobs a share page can carry: window._ROUTER_DATA and <script id="RENDER_DATA">."""
    payloads = []
    for match in ROUTER_DATA_PATTERN.finditer(html or ""):
        try:
            payloads.append(json.loads(match.group(1)))
        except ValueError:
            continue
    for match in RENDER_DATA_PATTERN.finditer(html or ""):
        data = parse_render_data(match.group(1))
        if data is not None:
            payloads.append(data)
    return payloads


def filter_reason(payload, aweme_id):
    """Why Douyin filtered this video out of a share page (videoInfoRes.filter_list), or ""."""
    stack, visited = [payload], 0
    while stack and visited < MAX_SEARCH_NODES:
        node = stack.pop()
        visited += 1
        if isinstance(node, dict):
            for entry in node.get("filter_list") or []:
                if isinstance(entry, dict) and str(entry.get("aweme_id") or aweme_id) == str(aweme_id):
                    reason = entry.get("filter_reason") or entry.get("detail_msg") or entry.get("notice")
                    if reason:
                        return str(reason)
            stack.extend(value for value in node.values() if isinstance(value, (dict, list)))
        elif isinstance(node, list):
            stack.extend(value for value in node if isinstance(value, (dict, list)))
    return ""


def parse_share_page(html, aweme_id):
    """Video of www.iesdouyin.com/share/video/<id>/ from the data the server rendered into it.

    Raises SourceError when the page carries no data for this video; the page often
    loads it with signed JavaScript requests only, and then a browser is needed.
    """
    payloads = share_page_payloads(html)
    for payload in payloads:
        detail = find_aweme_detail(payload, aweme_id)
        if detail:
            return parse_aweme_detail(detail)
    reason = next((found for found in (filter_reason(item, aweme_id) for item in payloads) if found), "")
    if reason:
        raise SourceError(f"Douyin không cho xem video này qua trang chia sẻ ({reason}).")
    raise SourceError("Trang chia sẻ Douyin không có dữ liệu video (trang chỉ tải dữ liệu bằng JavaScript).")


def fetch_share_page(aweme_id, *, opener=None, cookie_jar=None, timeout=15):
    """HTML of the public share page, requested with an iPhone User-Agent."""
    url = SHARE_PAGE_URL.format(extract_aweme_id(canonical_video_url(aweme_id))[1])
    request = urllib.request.Request(url, headers={
        "User-Agent": MOBILE_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    })
    if opener is None:
        handlers = [urllib.request.HTTPCookieProcessor(cookie_jar)] if cookie_jar is not None else []
        opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            body = response.read(MAX_SHARE_PAGE_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise SourceError(f"Không mở được trang chia sẻ Douyin: {error}") from error
    if not is_douyin_host(url_host(final_url)):
        raise SourceError("Trang chia sẻ chuyển hướng ra ngoài Douyin; app đã dừng để an toàn.")
    if len(body) > MAX_SHARE_PAGE_BYTES:
        raise SourceError("Trang chia sẻ Douyin lớn bất thường; app bỏ qua cách tải này.")
    return body.decode("utf-8", "replace")


def video_from_share_page(aweme_id, *, opener=None, cookie_jar=None, timeout=15):
    """Tier 1 of the Douyin download: the share page over HTTP, no browser needed."""
    html = fetch_share_page(aweme_id, opener=opener, cookie_jar=cookie_jar, timeout=timeout)
    return parse_share_page(html, aweme_id)


def jingxuan_url(aweme_id):
    return JINGXUAN_MODAL_URL.format(extract_aweme_id(canonical_video_url(aweme_id))[1])


def page_shows_video(url, aweme_id):
    """False when a Douyin page left this video: another page, or the feed without its modal."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return True
    if not is_douyin_host(parsed.hostname or ""):
        # about:blank while loading, or a verification page handled by the captcha probe.
        return True
    if re.search(rf"/(?:video|note)/{re.escape(str(aweme_id))}(?:/|$)", parsed.path or ""):
        return True
    return str(aweme_id) in parse_qs(parsed.query).get("modal_id", [])


def is_captcha_probe(probe):
    """True when the page shows Douyin's captcha (from CAPTCHA_PROBE_SCRIPT)."""
    if not isinstance(probe, dict):
        return False
    return bool(probe.get("captcha")) or bool(CAPTCHA_TITLE_PATTERN.search(str(probe.get("title") or "")))


def is_login_probe(probe):
    """True when the page shows Douyin's login popup (from CAPTCHA_PROBE_SCRIPT)."""
    return isinstance(probe, dict) and bool(probe.get("login"))


# Runs in the page. Douyin's slider captcha lives in a container or an iframe from
# its verification center; the title changes to 验证码中间页 on the full-page variant.
# #douyin-login-new-id is the "Log in to Douyin / scan to log in" popup shown to guests.
CAPTCHA_PROBE_SCRIPT = """() => {
    const selectors = ['#captcha_container', '#captcha-verify-image', '.captcha_verify_container',
        '.captcha-verify-container', '[id^="captcha"]', '[class*="captcha_verify"]',
        '[class*="secsdk-captcha"]', 'iframe[src*="captcha"]', 'iframe[src*="verifycenter"]',
        'iframe[src*="verify.snssdk"]'];
    const loginSelectors = ['#douyin-login-new-id', '#login-pannel', '#login-panel',
        '[id^="login-full-panel"]', '[data-e2e="login-panel"]'];
    const visible = element => {
        const box = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const shown = list => list.some(selector => Array.from(document.querySelectorAll(selector)).some(visible));
    return {captcha: shown(selectors), login: shown(loginSelectors), title: document.title || ''};
}"""

# The <video> of the requested post only: inside the modal on the feed page, or the
# whole document on a /video/<id> page. A feed video behind the modal is never taken.
DOM_VIDEO_SCRIPT = """() => {
    const modal = document.querySelector('[data-e2e="modal-video-container"], #modal-video-container');
    const root = modal || (/\\/(?:video|note)\\/\\d+/.test(location.pathname) ? document : null);
    const urls = [];
    if (!root) return urls;
    root.querySelectorAll('video, video source').forEach(element => {
        [element.currentSrc, element.src, element.getAttribute && element.getAttribute('src')]
            .forEach(value => { if (value && !urls.includes(value)) urls.push(value); });
    });
    return urls;
}"""

RENDER_DATA_SCRIPT = """() => {
    const script = document.getElementById('RENDER_DATA');
    return script ? script.textContent : '';
}"""


@dataclass
class CaptureResult:
    detail: dict = None
    dom_urls: list = field(default_factory=list)
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
    load_timeout=30,
    captcha_timeout=180,
    poll_interval_ms=500,
    clock=time.monotonic,
):
    """Open the video in a new tab of an existing browser context and capture its data.

    The default page is the feed with the video in a modal (jingxuan?modal_id=<id>).
    Returns a CaptureResult with the aweme_detail JSON of this video (API response or
    the page's RENDER_DATA), or with the modal's <video> URL. Raises LoginRequiredError
    at once when the page leaves the video, or when the login popup stays while no data
    came: waiting would only end at the deadline. on_captcha(active, page) is called
    when a captcha appears and when it is gone; the wait is then extended to
    captcha_timeout seconds. With allow_captcha_wait=False (background prefetch) a
    captcha stops the capture at once.
    """
    target_url = url or jingxuan_url(aweme_id)
    page = context.new_page()
    detail_responses = []

    def on_response(response):
        try:
            response_url = response.url
        except Exception:
            return
        if DETAIL_API_MARKER in response_url:
            detail_responses.append(response)

    result = CaptureResult()
    captcha_active = False
    login_seen_at = None
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
            else:
                if captcha_active:
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
                try:
                    current_url = str(getattr(page, "url", "") or "")
                except Exception:
                    current_url = ""
                if not page_shows_video(current_url, aweme_id):
                    raise LoginRequiredError("redirect")
                if is_login_probe(probe):
                    login_seen_at = clock() if login_seen_at is None else login_seen_at
                    if clock() - login_seen_at >= LOGIN_GRACE_SECONDS:
                        raise LoginRequiredError("login")
                else:
                    login_seen_at = None
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
        try:
            result.user_agent = str(page.evaluate("() => navigator.userAgent") or "")
        except Exception:
            result.user_agent = ""
        try:
            result.page_title = str(page.title() or "")
        except Exception:
            result.page_title = ""
        if result.detail is None and not result.dom_urls:
            if result.captcha_seen:
                raise SourceError(
                    f"Hết {int(captcha_timeout // 60)} phút chờ giải captcha Douyin; hãy kéo captcha trong "
                    "trình duyệt của app rồi chạy lại."
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
