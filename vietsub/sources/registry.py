"""Pick the download source for text pasted by the user."""

from . import douyin, web
from .base import SourceRequest, find_urls, url_host

SUPPORTED_SITES = ("douyin",) + web.WEB_SITES
DOWNLOADABLE_SOURCE_TYPES = frozenset(SUPPORTED_SITES)
SITE_LABELS = {"douyin": "Douyin", **web.SITE_LABELS, "local": "Video trên máy"}
SUPPORTED_SITES_TEXT = "Douyin, TikTok, YouTube, Bilibili, Xiaohongshu hoặc Kuaishou"


def parse_url(url):
    """Return a SourceRequest for one URL, or None when no supported site owns its host."""
    host = url_host(url)
    if douyin.is_douyin_host(host):
        link = douyin.parse_douyin_url(url)
        return SourceRequest("douyin", link.url, link.aweme_id, short_link=link.kind == "short")
    if web.site_for_host(host):
        return web.parse_web_url(url)
    return None


def detect_source(text):
    """Find the first supported video link in pasted text (share messages included).

    A recognized site with an unsupported link (profile, playlist...) reports that
    reason instead of the generic "no link found" message.
    """
    candidates = find_urls(text)
    if not candidates and isinstance(text, str) and text.strip() and " " not in text.strip():
        candidates = ["https://" + text.strip().split("://", 1)[-1]]
    first_error = None
    for candidate in candidates:
        try:
            request = parse_url(candidate)
        except ValueError as error:
            first_error = first_error or error
            continue
        if request:
            return request
    if first_error:
        raise first_error
    raise ValueError(f"Không tìm thấy link video {SUPPORTED_SITES_TEXT} trong nội dung đã dán.")
