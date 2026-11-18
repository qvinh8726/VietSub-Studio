"""Shared types, errors and URL helpers for the video sources."""

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
)

# URLs only contain ASCII characters, so Chinese text glued to a share link
# ("…/AbC123/复制此链接") is not swallowed into the path.
URL_PATTERN = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:!?)]}'\""


class SourceError(RuntimeError):
    """A download source failed; the message is shown to the user."""


class DownloadCancelledError(RuntimeError):
    """The caller asked the download to stop."""


@dataclass(frozen=True)
class SourceRequest:
    """One exact video to download.

    site:       "douyin", "tiktok", "youtube" or "bilibili"
    url:        normalized URL handed to the downloader
    video_id:   site video ID when the URL already contains it
    short_link: True when the URL still has to be resolved (v.douyin.com, b23.tv...)
    """

    site: str
    url: str
    video_id: str = ""
    short_link: bool = False


def find_urls(text):
    """Return the http(s) URLs in pasted text, in order and without duplicates."""
    if not isinstance(text, str):
        return []
    urls = []
    for match in URL_PATTERN.finditer(text):
        candidate = match.group(0).rstrip(TRAILING_PUNCTUATION)
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


def url_host(url):
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def host_matches(host, domains):
    """True for the domain itself or any subdomain; lookalikes such as douyin.com.evil.test fail."""
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Returning None makes urllib raise the 3xx as HTTPError, so each hop's
        # Location is checked before anything is requested from it.
        return None


def build_no_redirect_opener():
    return urllib.request.build_opener(_NoRedirect)


def follow_redirects(
    url,
    *,
    allowed_host,
    label,
    accept,
    opener=None,
    timeout=10,
    max_hops=5,
    user_agent=DEFAULT_USER_AGENT,
):
    """Follow a share link over HTTP one hop at a time.

    Every Location must satisfy allowed_host(host); accept(url) returns the result
    as soon as a URL is good enough (for example it contains the video ID), or None
    to keep following. Raises SourceError when the chain leaves the platform, loops,
    or ends without an accepted URL; callers can then fall back to the browser.
    """
    opener = opener or build_no_redirect_opener()
    current = url
    seen = set()
    for _ in range(max(1, int(max_hops))):
        if current in seen:
            break
        seen.add(current)
        request = urllib.request.Request(
            current,
            headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,*/*"},
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                final_url = response.geturl()
        except urllib.error.HTTPError as error:
            if not 300 <= error.code < 400:
                raise SourceError(f"{label} trả lỗi HTTP {error.code} khi giải mã link share.") from error
            location = error.headers.get("Location") if error.headers else None
            if not location:
                raise SourceError(f"{label} chuyển hướng nhưng không cho biết địa chỉ đích.") from error
            next_url = urljoin(current, location)
            if urlparse(next_url).scheme not in ("http", "https") or not allowed_host(url_host(next_url)):
                raise SourceError(
                    f"Link share chuyển hướng ra ngoài {label}; app đã dừng để an toàn."
                ) from error
            result = accept(next_url)
            if result:
                return result
            current = next_url
            continue
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise SourceError(f"Không kết nối được {label} để giải mã link share: {error}") from error
        result = accept(final_url)
        if result:
            return result
        raise SourceError(f"Link share {label} không dẫn tới video nào.")
    raise SourceError(f"Link share {label} chuyển hướng quá nhiều lần.")


def normalize_media_url(url):
    """Make a media URL absolute (https for //host/...) and drop anything that is not http(s)."""
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    return url
