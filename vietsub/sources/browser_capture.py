"""Capture a video file URL from any web page opened in the automation browser's profile.

Used for Kuaishou (no yt-dlp extractor) and as the fallback for Xiaohongshu. The
page plays the video itself, so the URL comes from the <video> element or from
the media responses the page receives. When nothing shows up in time the user is
asked to sign in or solve a captcha in that browser.
"""

import time
from dataclasses import dataclass, field

from .base import DownloadCancelledError, SourceError, normalize_media_url

# {browser} is the short name of the automation browser ("Chrome", "Edge"...).
ATTENTION_NOTICE = "Vui lòng đăng nhập hoặc kéo captcha trên trình duyệt {browser} để tiếp tục tải"

DOM_VIDEO_SCRIPT = """() => {
    const urls = [];
    document.querySelectorAll('video, video source').forEach(element => {
        [element.currentSrc, element.src, element.getAttribute && element.getAttribute('src')]
            .forEach(value => { if (value && !urls.includes(value)) urls.push(value); });
    });
    return urls;
}"""

# Keep the hidden tab silent: video pages autoplay.
MUTE_MEDIA_SCRIPT = """(() => {
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () { this.muted = true; return play.apply(this, arguments); };
})();"""


def is_media_response(response):
    """True for a Playwright response that carries video data."""
    try:
        resource_type = response.request.resource_type
    except Exception:
        resource_type = ""
    try:
        content_type = str((response.headers or {}).get("content-type") or "").lower()
    except Exception:
        content_type = ""
    return resource_type == "media" or content_type.startswith("video/")


@dataclass
class BrowserCapture:
    media_urls: list = field(default_factory=list)
    title: str = ""
    user_agent: str = ""
    final_url: str = ""


def _dedupe(urls):
    result = []
    for url in urls:
        if url and url not in result:
            result.append(url)
    return result


def _page_media_urls(page):
    try:
        values = page.evaluate(DOM_VIDEO_SCRIPT) or []
    except Exception:
        return []
    # blob: URLs belong to the page's media pipeline and cannot be downloaded.
    return [url for url in (normalize_media_url(item) for item in values) if url]


def capture_media_from_page(
    context,
    url,
    *,
    is_cancelled=lambda: False,
    on_attention=None,
    allow_attention_wait=True,
    load_timeout=30,
    attention_timeout=180,
    poll_interval_ms=500,
    clock=time.monotonic,
):
    """Open url in a new tab and return the video URLs the page uses.

    on_attention(active, page) is called when the user is asked to act in the browser
    (sign in or captcha) and again when that is over. With allow_attention_wait=False
    (background prefetch) the capture gives up after load_timeout instead.
    """
    page = context.new_page()
    network_urls = []

    def on_response(response):
        if not is_media_response(response):
            return
        try:
            media_url = normalize_media_url(response.url)
        except Exception:
            return
        if media_url and media_url not in network_urls:
            network_urls.append(media_url)

    attention = False
    try:
        try:
            page.add_init_script(MUTE_MEDIA_SCRIPT)
        except Exception:
            pass
        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=int(load_timeout * 1000))
        except Exception:
            # A slow page may still start the video; keep polling until the deadline.
            pass
        deadline = clock() + load_timeout
        candidates = []
        while True:
            if is_cancelled():
                raise DownloadCancelledError("Đã huỷ tải video.")
            candidates = _dedupe(_page_media_urls(page) + network_urls)
            if candidates:
                break
            if clock() >= deadline:
                if attention or not allow_attention_wait:
                    break
                attention = True
                deadline = clock() + attention_timeout
                if on_attention:
                    on_attention(True, page)
            page.wait_for_timeout(poll_interval_ms)
        if not candidates:
            raise SourceError(
                "Không tìm thấy video trên trang. Bài đăng có thể là ảnh, ở chế độ riêng tư, "
                "hoặc cần đăng nhập/xác minh trong trình duyệt của app."
            )
        result = BrowserCapture(media_urls=candidates)
        try:
            result.user_agent = str(page.evaluate("() => navigator.userAgent") or "")
        except Exception:
            pass
        try:
            result.title = clean_page_title(page.title())
        except Exception:
            pass
        try:
            result.final_url = str(page.url or "")
        except Exception:
            pass
        return result
    finally:
        if attention and on_attention:
            try:
                on_attention(False, page)
            except Exception:
                pass
        try:
            page.close()
        except Exception:
            pass


def clean_page_title(title):
    """Tab title without the site suffix ("… - 快手", "… - 小红书", "… - 抖音")."""
    text = " ".join(str(title or "").split())
    for separator in (" - ", " | ", " _ "):
        if separator in text:
            head, tail = text.rsplit(separator, 1)
            if len(tail) <= 20:
                text = head
    return text.strip()
