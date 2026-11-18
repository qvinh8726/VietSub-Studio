"""Capture a video file URL from any web page opened in the automation browser's profile.

Used for Kuaishou (no yt-dlp extractor) and as the fallback for Xiaohongshu. The
page plays the video itself, so the URL comes from the <video> element or from
the media responses the page receives. When nothing shows up in time the user is
asked to sign in or solve a captcha in that browser.
"""

import re
import time
from dataclasses import dataclass, field

from .base import DownloadCancelledError, SourceError, normalize_media_url
from .browser import release_page, reuse_page

# Check visible challenge UI, not arbitrary body text or hidden captcha scripts.
ATTENTION_PROBE_SCRIPT = """() => {
    const visible = element => {
        const box = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return box.width > 0 && box.height > 0 && style.display !== 'none' &&
            style.visibility !== 'hidden' && style.opacity !== '0';
    };
    const shown = selectors => selectors.some(selector =>
        Array.from(document.querySelectorAll(selector)).some(visible));
    return {
        title: document.title || '',
        captcha: shown(['#captcha_container', '#captcha-verify-image', '[id^="captcha"]',
            '[class*="captcha_verify"]', '[class*="secsdk-captcha"]', '.geetest_panel',
            '.geetest_holder', '.yidun_panel', '#challenge-running', '#challenge-stage',
            'iframe[src*="captcha"]', 'iframe[src*="verifycenter"]',
            'iframe[src*="verify.snssdk"]', 'iframe[src*="challenges.cloudflare.com"]']),
        login: shown(['#douyin-login-new-id', '[data-e2e="login-panel"]',
            '.login-container', '.login-modal', '[role="dialog"] input[type="password"]'])
    };
}"""
CHALLENGE_TITLE = re.compile(
    r"验证码|安全验证|人机验证|captcha|security verification|verify (?:you are|you're) human|just a moment", re.I
)


def is_attention_probe(probe):
    if not isinstance(probe, dict):
        return False
    return bool(probe.get("captcha") or probe.get("login") or
                CHALLENGE_TITLE.search(str(probe.get("title") or "")))


def page_needs_attention(page):
    try:
        return is_attention_probe(page.evaluate(ATTENTION_PROBE_SCRIPT))
    except Exception:
        return False

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
    require_attention_signal=False,
    load_timeout=30,
    attention_timeout=180,
    poll_interval_ms=500,
    clock=time.monotonic,
):
    """Reuse the initial/target tab and return the video URLs the page uses.

    on_attention(active, page) is called when the user is asked to act in the browser
    (sign in or captcha) and again when that is over. With allow_attention_wait=False
    (background prefetch) a detected challenge stops immediately. The app passes
    require_attention_signal=True so missing media alone never opens a visible window.
    """
    page = reuse_page(context, url)
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
            challenge = page_needs_attention(page)
            if challenge and not allow_attention_wait:
                raise SourceError("Trang yêu cầu đăng nhập/xác minh; bỏ qua lượt tải chạy ngầm.")
            if (challenge and not attention) or clock() >= deadline:
                if attention or not allow_attention_wait or (require_attention_signal and not challenge):
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
            page.remove_listener("response", on_response)
        except Exception:
            pass
        release_page(context, page)


def clean_page_title(title):
    """Tab title without the site suffix ("… - 快手", "… - 小红书", "… - 抖音")."""
    text = " ".join(str(title or "").split())
    for separator in (" - ", " | ", " _ "):
        if separator in text:
            head, tail = text.rsplit(separator, 1)
            if len(tail) <= 20:
                text = head
    return text.strip()
