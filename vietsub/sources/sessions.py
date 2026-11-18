"""Login sessions of the Chinese platforms in the automation browser's own profile.

The user signs in once per platform in the browser profile that VietSub Studio
controls (Google Chrome by default); downloads then reuse those cookies. A Cookie header pasted in Settings
can replace the matching cookies for one platform.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionSite:
    key: str
    label: str
    home_url: str
    cookie_domain: str
    cookie_urls: tuple
    # Cookies that only exist after a login. Empty when the platform gives guests
    # the same cookies, so the login cannot be told from cookies alone.
    login_cookies: tuple


SESSION_SITES = {
    "douyin": SessionSite(
        "douyin", "Douyin", "https://www.douyin.com/", ".douyin.com",
        ("https://www.douyin.com/",), ("sessionid", "sessionid_ss"),
    ),
    "bilibili": SessionSite(
        "bilibili", "Bilibili", "https://www.bilibili.com/", ".bilibili.com",
        ("https://www.bilibili.com/",), ("SESSDATA",),
    ),
    "xiaohongshu": SessionSite(
        # Guests also receive web_session, so cookies cannot prove a login here.
        "xiaohongshu", "Xiaohongshu", "https://www.xiaohongshu.com/explore", ".xiaohongshu.com",
        ("https://www.xiaohongshu.com/",), (),
    ),
    "kuaishou": SessionSite(
        "kuaishou", "Kuaishou", "https://www.kuaishou.com/", ".kuaishou.com",
        ("https://www.kuaishou.com/", "https://id.kuaishou.com/"),
        ("passToken", "userId", "kuaishou.server.web_st", "kuaishou.server.webday7_st"),
    ),
}


def all_cookie_urls():
    urls = []
    for site in SESSION_SITES.values():
        urls.extend(url for url in site.cookie_urls if url not in urls)
    return urls


def cookies_of_site(site_key, browser_cookies):
    """Browser cookies that belong to this platform's domain."""
    domain = SESSION_SITES[site_key].cookie_domain.lstrip(".")
    result = []
    for item in browser_cookies or []:
        if not isinstance(item, dict):
            continue
        cookie_domain = str(item.get("domain") or "").lower().lstrip(".")
        if cookie_domain == domain or cookie_domain.endswith("." + domain):
            result.append(item)
    return result


def login_state(site_key, browser_cookies):
    """True or False from the platform's login cookie; None when cookies cannot tell."""
    site = SESSION_SITES[site_key]
    if not site.login_cookies:
        return None
    return any(
        isinstance(item, dict) and item.get("name") in site.login_cookies and item.get("value")
        for item in browser_cookies or []
    )
