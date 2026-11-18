"""The dedicated automation browser: Google Chrome by default, Microsoft Edge or a custom .exe.

The browser always runs with its own --user-data-dir inside the app's config folder,
so it never touches the user's everyday Chrome or Edge profile and runs next to it.
Downloads, Gemini Notebook and the platform logins all use this one profile.
"""

import os
from urllib.parse import urlparse

DEBUG_PORT = 9222
COLD_START_SECONDS = 15
POLL_SECONDS = 0.3
KINDS = ("chrome", "edge", "custom")
DEFAULT_KIND = "chrome"
LABELS = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "custom": "Trình duyệt tuỳ chọn"}
SHORT_LABELS = {"chrome": "Chrome", "edge": "Edge", "custom": "trình duyệt"}
PROFILE_DIRNAMES = {
    "chrome": "chrome_debug_profile",
    "edge": "edge_debug_profile",
    "custom": "custom_browser_profile",
}
# /json/version "Browser" prefixes each kind may report.
PRODUCTS = {
    "chrome": ("chrome/",),
    "edge": ("edg/", "edge/"),
    "custom": ("chrome/", "chromium/", "edg/", "edge/"),
}
APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
CHROME_RELATIVE = os.path.join("Google", "Chrome", "Application", "chrome.exe")


def validate_kind(value):
    if value in (None, ""):
        return DEFAULT_KIND
    if value not in KINDS:
        raise ValueError("Trình duyệt tự động hoá phải là Google Chrome, Microsoft Edge hoặc file .exe tuỳ chọn.")
    return value


def read_app_paths(exe_name):
    """Paths registered for exe_name under App Paths (current user first), or []."""
    try:
        import winreg
    except ImportError:
        return []
    values = []
    for root, view in (
        (winreg.HKEY_CURRENT_USER, 0),
        (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_64KEY", 0)),
        (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_32KEY", 0)),
    ):
        try:
            with winreg.OpenKey(root, f"{APP_PATHS_KEY}\\{exe_name}", 0, winreg.KEY_READ | view) as key:
                value, _kind = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if isinstance(value, str) and value.strip() and value not in values:
            values.append(value)
    return values


def _clean_candidate(value, exe_name):
    path = os.path.expandvars(str(value or "").strip().strip('"'))
    if not path or not os.path.isabs(path) or os.path.basename(path).lower() != exe_name:
        return ""
    return os.path.normpath(path)


def find_chrome(*, app_paths=read_app_paths, environ=None, path_lookup=None, isfile=os.path.isfile):
    """chrome.exe from App Paths, Program Files, Program Files (x86), %LOCALAPPDATA% or PATH.

    path_lookup(name) must only search absolute PATH entries (never the current folder).
    """
    environ = os.environ if environ is None else environ
    candidates = [_clean_candidate(value, "chrome.exe") for value in app_paths("chrome.exe")]
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = environ.get(variable)
        if root:
            candidates.append(os.path.join(root, CHROME_RELATIVE))
    if path_lookup is not None:
        candidates.append(_clean_candidate(path_lookup("chrome"), "chrome.exe"))
    for candidate in candidates:
        if candidate and isfile(candidate):
            return candidate
    return ""


def validate_custom_executable(value):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Hãy chọn file .exe của trình duyệt.")
    path = os.path.expandvars(value.strip().strip('"'))
    if not os.path.isabs(path):
        raise ValueError("Đường dẫn trình duyệt phải là đường dẫn tuyệt đối.")
    if not path.lower().endswith(".exe"):
        raise ValueError("Trình duyệt tuỳ chọn phải là một file .exe.")
    if not os.path.isfile(path):
        raise ValueError("Không tìm thấy file trình duyệt đã chọn.")
    return os.path.normpath(path)


def profile_dir(config_dir, kind):
    return os.path.join(config_dir, PROFILE_DIRNAMES[validate_kind(kind)])


def launch_command(executable, profile, *, port=DEBUG_PORT, background=False, start_url="about:blank", headless=True):
    """Command line of the automation browser.

    --user-data-dir is always set: Chrome refuses remote debugging on its default
    profile, and a separate profile keeps the user's own browser untouched. New
    headless mode is the default. Callers reuse its initial tab; visible login
    launches pass the destination as start_url so no blank window is displayed.
    """
    command = [
        executable,
        f"--remote-debugging-port={int(port)}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ]
    if headless:
        command.append("--headless=new")
    if background:
        command += [
            "--start-minimized",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ]
    command.append(start_url)
    return command


BLANK_PAGE_URLS = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/", "edge://newtab/")


def reuse_page(context, url=None):
    """Reuse the initial tab (or the requested page), without touching other tabs."""
    pages = getattr(context, "pages", ())
    if not isinstance(pages, (list, tuple)):
        pages = ()
    initial = getattr(context, "_vietsub_initial_page", None)
    if initial is not None and initial in pages:
        context._vietsub_initial_page = None
        return initial
    for page in pages:
        try:
            if page.url in BLANK_PAGE_URLS or (url and page.url == url):
                return page
        except Exception:
            continue
    return context.new_page()


def release_page(context, page):
    """Keep the last tab alive so closing a capture cannot discard session cookies."""
    pages = getattr(context, "pages", ())
    if isinstance(pages, (list, tuple)) and len(pages) == 1 and pages[0] is page:
        return
    try:
        page.close()
    except Exception:
        pass


def is_valid_debug_payload(payload, kind=None, port=DEBUG_PORT):
    """True for a /json/version answer of a local browser on this port (of this kind, if given)."""
    if not isinstance(payload, dict):
        return False
    # New headless Chromium normally reports Chrome/, older builds use HeadlessChrome/.
    product = str(payload.get("Browser") or "").lower().replace("headlesschrome/", "chrome/", 1)
    allowed = PRODUCTS[kind] if kind in PRODUCTS else tuple({p for values in PRODUCTS.values() for p in values})
    try:
        websocket = urlparse(str(payload.get("webSocketDebuggerUrl") or ""))
        websocket_port = websocket.port
    except ValueError:
        return False
    path_parts = websocket.path.split("/")
    return (
        product.startswith(allowed)
        and websocket.scheme == "ws"
        and websocket.hostname == "127.0.0.1"
        and websocket_port == int(port)
        and websocket.username is None
        and websocket.password is None
        and not websocket.query
        and not websocket.fragment
        and len(path_parts) == 4 and path_parts[1:3] == ["devtools", "browser"] and bool(path_parts[3])
    )
