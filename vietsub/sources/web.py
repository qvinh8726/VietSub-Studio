"""Links of video sites other than Douyin.

TikTok, YouTube, Bilibili and Xiaohongshu are downloaded by yt-dlp; Kuaishou has
no yt-dlp extractor and is captured in the user's Edge profile. Only one exact
video is accepted per link: playlists, channels and profiles fail, so a queue item
never expands into many downloads.
"""

import re
from urllib.parse import parse_qs, quote, urlparse

from .base import DEFAULT_USER_AGENT, SourceRequest, follow_redirects, host_matches, url_host

WEB_SITES = ("tiktok", "youtube", "bilibili", "xiaohongshu", "kuaishou")
YOUTUBE_DOMAINS = ("youtube.com", "youtube-nocookie.com")
YOUTUBE_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
TIKTOK_DOMAINS = ("tiktok.com",)
TIKTOK_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
BILIBILI_DOMAINS = ("bilibili.com",)
BILIBILI_SHORT_HOSTS = {"b23.tv", "www.b23.tv"}
XIAOHONGSHU_DOMAINS = ("xiaohongshu.com",)
XIAOHONGSHU_SHORT_HOSTS = {"xhslink.com", "www.xhslink.com"}
KUAISHOU_DOMAINS = ("kuaishou.com", "chenzhongtech.com", "gifshow.com")
KUAISHOU_SHORT_HOSTS = {"v.kuaishou.com"}
SITE_LABELS = {
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "bilibili": "Bilibili",
    "xiaohongshu": "Xiaohongshu",
    "kuaishou": "Kuaishou",
}

YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
YOUTUBE_PATH_ID = re.compile(r"/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})/?$")
TIKTOK_VIDEO_PATH = re.compile(r"/(@[A-Za-z0-9._-]{1,64})/video/(\d{6,25})/?$")
BILIBILI_VIDEO_PATH = re.compile(r"/video/(BV[0-9A-Za-z]{10}|av\d{1,15})/?$", re.IGNORECASE)
BILIBILI_EPISODE_PATH = re.compile(r"/bangumi/play/(ep\d{1,12})/?$")
XIAOHONGSHU_NOTE_PATH = re.compile(
    r"/(?:explore|discovery/item|user/profile/[0-9a-f]{24})/([0-9a-f]{24})/?$"
)
# Query parameters a Xiaohongshu note needs to open; tracking parameters are dropped.
XIAOHONGSHU_KEPT_QUERY = ("xsec_token", "xsec_source")
KUAISHOU_VIDEO_PATH = re.compile(r"/(?:short-video|fw/photo)/([0-9A-Za-z_-]{5,40})/?$")
SHORT_CODE = re.compile(r"/[A-Za-z0-9_-]{3,64}/?")
XIAOHONGSHU_SHORT_PATH = re.compile(r"/(?:[am]/)?[A-Za-z0-9_-]{3,64}/?")


def site_for_host(host):
    host = (host or "").lower()
    if host in YOUTUBE_SHORT_HOSTS or host_matches(host, YOUTUBE_DOMAINS):
        return "youtube"
    if host_matches(host, TIKTOK_DOMAINS):
        return "tiktok"
    if host in BILIBILI_SHORT_HOSTS or host_matches(host, BILIBILI_DOMAINS):
        return "bilibili"
    if host in XIAOHONGSHU_SHORT_HOSTS or host_matches(host, XIAOHONGSHU_DOMAINS):
        return "xiaohongshu"
    if host_matches(host, KUAISHOU_DOMAINS):
        return "kuaishou"
    return None


def _single_video_error(site_label):
    return ValueError(
        f"Chỉ hỗ trợ một video {site_label} riêng lẻ; playlist, kênh và trang hồ sơ không được chấp nhận."
    )


def _parse_youtube(parsed, host):
    if host in YOUTUBE_SHORT_HOSTS:
        video_id = (parsed.path or "").strip("/")
    elif parsed.path in ("/watch", "/watch/"):
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        match = YOUTUBE_PATH_ID.fullmatch(parsed.path or "")
        video_id = match.group(1) if match else ""
    if not YOUTUBE_ID.fullmatch(video_id or ""):
        raise _single_video_error("YouTube")
    return SourceRequest("youtube", f"https://www.youtube.com/watch?v={video_id}", video_id)


def _parse_tiktok(parsed, host):
    if host in TIKTOK_SHORT_HOSTS or (parsed.path or "").startswith("/t/"):
        if not SHORT_CODE.fullmatch((parsed.path or "").replace("/t/", "/", 1)):
            raise ValueError("Link rút gọn TikTok không hợp lệ.")
        return SourceRequest("tiktok", f"https://{host}{parsed.path}", "", short_link=True)
    match = TIKTOK_VIDEO_PATH.fullmatch(parsed.path or "")
    if not match:
        raise _single_video_error("TikTok")
    user, video_id = match.groups()
    return SourceRequest("tiktok", f"https://www.tiktok.com/{user}/video/{video_id}", video_id)


def _parse_bilibili(parsed, host):
    if host in BILIBILI_SHORT_HOSTS:
        if not SHORT_CODE.fullmatch(parsed.path or ""):
            raise ValueError("Link rút gọn Bilibili không hợp lệ.")
        return SourceRequest("bilibili", f"https://b23.tv{parsed.path.rstrip('/')}", "", short_link=True)
    if host not in ("bilibili.com", "www.bilibili.com", "m.bilibili.com"):
        raise _single_video_error("Bilibili")
    match = BILIBILI_VIDEO_PATH.fullmatch(parsed.path or "")
    if match:
        video_id = match.group(1)
        if video_id.lower().startswith("bv"):
            video_id = "BV" + video_id[2:]
        url = f"https://www.bilibili.com/video/{video_id}"
        part = (parse_qs(parsed.query).get("p") or [""])[0]
        if part.isdigit() and 0 < int(part) < 10_000:
            url += f"?p={int(part)}"
        return SourceRequest("bilibili", url, video_id)
    episode = BILIBILI_EPISODE_PATH.fullmatch(parsed.path or "")
    if episode:
        return SourceRequest("bilibili", f"https://www.bilibili.com/bangumi/play/{episode.group(1)}", episode.group(1))
    raise _single_video_error("Bilibili")


def _parse_xiaohongshu(parsed, host):
    if host in XIAOHONGSHU_SHORT_HOSTS:
        if not XIAOHONGSHU_SHORT_PATH.fullmatch(parsed.path or ""):
            raise ValueError("Link rút gọn Xiaohongshu không hợp lệ.")
        return SourceRequest("xiaohongshu", f"https://xhslink.com{parsed.path.rstrip('/')}", "", short_link=True)
    if host not in ("xiaohongshu.com", "www.xiaohongshu.com"):
        raise _single_video_error("Xiaohongshu")
    match = XIAOHONGSHU_NOTE_PATH.fullmatch(parsed.path or "")
    if not match:
        raise _single_video_error("Xiaohongshu")
    note_id = match.group(1)
    query = parse_qs(parsed.query)
    kept = [
        f"{key}={quote(query[key][0], safe='')}"
        for key in XIAOHONGSHU_KEPT_QUERY
        if query.get(key) and query[key][0]
    ]
    url = f"https://www.xiaohongshu.com/explore/{note_id}"
    if kept:
        url += "?" + "&".join(kept)
    return SourceRequest("xiaohongshu", url, note_id)


def _parse_kuaishou(parsed, host):
    path = parsed.path or ""
    if host in KUAISHOU_SHORT_HOSTS:
        if not SHORT_CODE.fullmatch(path):
            raise ValueError("Link rút gọn Kuaishou không hợp lệ.")
        return SourceRequest("kuaishou", f"https://v.kuaishou.com{path.rstrip('/')}", "", short_link=True)
    if host in ("kuaishou.com", "www.kuaishou.com") and path.startswith("/f/"):
        if not SHORT_CODE.fullmatch(path[2:]):
            raise ValueError("Link rút gọn Kuaishou không hợp lệ.")
        return SourceRequest("kuaishou", f"https://www.kuaishou.com{path.rstrip('/')}", "", short_link=True)
    if host.startswith("live."):
        raise ValueError("Không hỗ trợ livestream hoặc trang hồ sơ Kuaishou; hãy dán link của một video.")
    match = KUAISHOU_VIDEO_PATH.fullmatch(path)
    if not match:
        raise _single_video_error("Kuaishou")
    photo_id = match.group(1)
    return SourceRequest("kuaishou", f"https://www.kuaishou.com/short-video/{photo_id}", photo_id)


PARSERS = {
    "youtube": _parse_youtube,
    "tiktok": _parse_tiktok,
    "bilibili": _parse_bilibili,
    "xiaohongshu": _parse_xiaohongshu,
    "kuaishou": _parse_kuaishou,
}
SITE_DOMAINS = {
    "xiaohongshu": XIAOHONGSHU_DOMAINS + tuple(XIAOHONGSHU_SHORT_HOSTS),
    "kuaishou": KUAISHOU_DOMAINS,
}


def parse_web_url(url):
    """Validate a supported non-Douyin video URL and reduce it to one video."""
    if not isinstance(url, str):
        raise ValueError("Link video không hợp lệ.")
    try:
        parsed = urlparse(url.strip())
    except ValueError as error:
        raise ValueError("Link video không hợp lệ.") from error
    host = (parsed.hostname or "").lower().rstrip(".")
    site = site_for_host(host)
    if parsed.scheme not in ("http", "https") or not site:
        raise ValueError("Link không thuộc TikTok, YouTube, Bilibili, Xiaohongshu hoặc Kuaishou.")
    return PARSERS[site](parsed, host)


def resolve_short_link(request, *, opener=None, timeout=10, max_hops=5, user_agent=DEFAULT_USER_AGENT):
    """Resolve a Xiaohongshu or Kuaishou share link to its video over HTTP.

    yt-dlp resolves TikTok and Bilibili short links itself, so other sites are
    returned unchanged. Raises SourceError when the link leaves the platform or
    does not lead to a single video; the caller can then open it in Edge.
    """
    if not request.short_link or request.site not in SITE_DOMAINS:
        return request
    domains = SITE_DOMAINS[request.site]

    def video_request(candidate):
        try:
            resolved = parse_web_url(candidate)
        except ValueError:
            return None
        if resolved.site != request.site or resolved.short_link:
            return None
        return resolved

    return follow_redirects(
        request.url,
        allowed_host=lambda host: host_matches(host, domains),
        label=SITE_LABELS[request.site],
        accept=video_request,
        opener=opener,
        timeout=timeout,
        max_hops=max_hops,
        user_agent=user_agent,
    )


def site_of_url(url):
    return site_for_host(url_host(url))
