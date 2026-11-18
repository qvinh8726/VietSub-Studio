"""Cookie header, cookies.txt and browser cookie helpers.

Cookies always live in a CookieJar, so urllib only sends each cookie to the domain
it belongs to: a Douyin session cookie never reaches a CDN host or another site,
even after a redirect.
"""

import http.cookiejar
import os
import re
import time

COOKIE_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
MAX_COOKIE_HEADER_CHARS = 16_384
MAX_COOKIE_FILE_BYTES = 1024 * 1024
DOUYIN_COOKIE_DOMAIN = ".douyin.com"
DOUYIN_LOGIN_COOKIES = ("sessionid", "sessionid_ss")
NETSCAPE_HEADER_PATTERN = re.compile(r"#( Netscape)? HTTP Cookie File")


def parse_cookie_header(text):
    """Parse "name=value; name2=value2" (optionally prefixed by "Cookie:")."""
    if not isinstance(text, str):
        raise ValueError("Chuỗi cookie không hợp lệ.")
    value = text.strip()
    if value[:7].lower() == "cookie:":
        value = value[7:].strip()
    if not value:
        return []
    if len(value) > MAX_COOKIE_HEADER_CHARS:
        raise ValueError(f"Chuỗi cookie dài quá {MAX_COOKIE_HEADER_CHARS:,} ký tự.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Chuỗi cookie chứa ký tự xuống dòng hoặc ký tự điều khiển.")
    pairs = []
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        name, separator, cookie_value = part.partition("=")
        name = name.strip()
        if not separator or not COOKIE_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Cookie không đúng dạng tên=giá trị: {part[:40]}")
        pairs.append((name, cookie_value.strip()))
    return pairs


def normalize_cookie_header(text):
    return "; ".join(f"{name}={value}" for name, value in parse_cookie_header(text))


def make_cookie(name, value, domain, *, path="/", secure=True, expires=None, http_only=False):
    domain = str(domain or "").strip().lower()
    try:
        expires = int(float(expires)) if expires is not None and float(expires) > 0 else None
    except (TypeError, ValueError):
        expires = None
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=domain.startswith("."),
        domain_initial_dot=domain.startswith("."),
        path=path or "/",
        path_specified=True,
        secure=bool(secure),
        expires=expires,
        discard=expires is None,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": None} if http_only else {},
        rfc2109=False,
    )


def jar_from_browser_cookies(browser_cookies):
    """Build a CookieJar from Playwright/CDP cookie dicts."""
    jar = http.cookiejar.CookieJar()
    for item in browser_cookies or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        domain = str(item.get("domain") or "")
        if not COOKIE_NAME_PATTERN.fullmatch(name) or not domain:
            continue
        jar.set_cookie(make_cookie(
            name,
            str(item.get("value") or ""),
            domain,
            path=str(item.get("path") or "/"),
            secure=bool(item.get("secure")),
            expires=item.get("expires"),
            http_only=bool(item.get("httpOnly")),
        ))
    return jar


def add_header_cookies(jar, header_text, domain=DOUYIN_COOKIE_DOMAIN):
    """Add or replace cookies from a Cookie header for one domain (the manual override)."""
    for name, value in parse_cookie_header(header_text):
        jar.set_cookie(make_cookie(name, value, domain, secure=True))
    return jar


def browser_cookies_from_header(header_text, domain=DOUYIN_COOKIE_DOMAIN):
    """Playwright add_cookies() payload for a manual Cookie header."""
    return [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
        }
        for name, value in parse_cookie_header(header_text)
    ]


def has_douyin_login(browser_cookies):
    return any(
        isinstance(item, dict) and item.get("name") in DOUYIN_LOGIN_COOKIES and item.get("value")
        for item in browser_cookies or []
    )


def write_netscape_cookie_file(browser_cookies, path):
    """Write Playwright cookie dicts as a Netscape cookies.txt (the format yt-dlp reads)."""
    lines = ["# Netscape HTTP Cookie File", ""]
    for item in browser_cookies or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        domain = str(item.get("domain") or "")
        if not COOKIE_NAME_PATTERN.fullmatch(name) or not domain:
            continue
        value = str(item.get("value") or "")
        if any(character in value for character in "\t\r\n"):
            continue
        try:
            expires = int(float(item.get("expires") or 0))
        except (TypeError, ValueError):
            expires = 0
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        prefix = "#HttpOnly_" if item.get("httpOnly") else ""
        lines.append("\t".join([
            prefix + domain,
            include_subdomains,
            str(item.get("path") or "/"),
            "TRUE" if item.get("secure") else "FALSE",
            str(max(0, expires)),
            name,
            value,
        ]))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def load_cookie_file(path):
    """Read and validate a Netscape cookies.txt chosen by the user."""
    try:
        path = os.path.abspath(os.fspath(path))
    except TypeError as error:
        raise ValueError("Đường dẫn cookies.txt không hợp lệ.") from error
    if not os.path.isfile(path):
        raise ValueError("Không tìm thấy file cookies.txt đã chọn.")
    if os.path.getsize(path) > MAX_COOKIE_FILE_BYTES:
        raise ValueError("File cookies.txt lớn hơn 1 MiB.")
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        first_line = handle.readline().strip()
    if not NETSCAPE_HEADER_PATTERN.match(first_line):
        raise ValueError("File cookies.txt phải theo định dạng Netscape (dòng đầu: # Netscape HTTP Cookie File).")
    jar = http.cookiejar.MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (http.cookiejar.LoadError, OSError, ValueError, AssertionError) as error:
        raise ValueError(f"Không đọc được file cookies.txt: {error}") from error
    now = time.time()
    for cookie in list(jar):
        # Exporters write 0 for session cookies; keep them for this session.
        if cookie.expires == 0:
            cookie.expires = None
            cookie.discard = True
        elif cookie.expires is not None and cookie.expires < now:
            jar.clear(cookie.domain, cookie.path, cookie.name)
    return jar
