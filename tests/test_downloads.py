"""Tests for cookies, the streaming downloader, the Douyin page capture, yt-dlp and local files."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.cookiejar import MozillaCookieJar
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from . import _isolation  # noqa: F401  python -m unittest tests.test_downloads
except ImportError:
    import _isolation  # noqa: F401  python -m unittest discover -s tests

from vietsub.sources import base, browser_capture, cookies, douyin, http_download, local, ytdlp_source

AWEME_ID = "7312345678901234567"
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 400


class CookieTests(unittest.TestCase):
    def test_cookie_header_is_parsed_and_validated(self):
        header = "Cookie: ttwid=1%7Cabc; sessionid=xyz ;s_v_web_id=verify_123"
        self.assertEqual(
            cookies.parse_cookie_header(header),
            [("ttwid", "1%7Cabc"), ("sessionid", "xyz"), ("s_v_web_id", "verify_123")],
        )
        self.assertEqual(
            cookies.normalize_cookie_header(header),
            "ttwid=1%7Cabc; sessionid=xyz; s_v_web_id=verify_123",
        )
        for invalid in ("bad name=1", "a=1\r\nX-Injected: 1", "novalue", "x" * 20_000 + "=1"):
            with self.subTest(invalid=invalid[:20]), self.assertRaises(ValueError):
                cookies.parse_cookie_header(invalid)

    def test_browser_cookies_stay_scoped_to_their_domain(self):
        jar = cookies.jar_from_browser_cookies([
            {"name": "sessionid", "value": "abc", "domain": ".douyin.com", "path": "/",
             "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"},
            {"name": "local", "value": "1", "domain": "127.0.0.1", "path": "/",
             "expires": time.time() + 3600, "httpOnly": False, "secure": False},
        ])
        douyin_request = urllib.request.Request("https://www.douyin.com/video/1")
        jar.add_cookie_header(douyin_request)
        self.assertEqual(douyin_request.get_header("Cookie"), "sessionid=abc")
        cdn_request = urllib.request.Request("https://v3-web.douyinvod.com/video.mp4")
        jar.add_cookie_header(cdn_request)
        self.assertIsNone(cdn_request.get_header("Cookie"))
        plain_request = urllib.request.Request("http://www.douyin.com/")
        jar.add_cookie_header(plain_request)
        self.assertIsNone(plain_request.get_header("Cookie"))

    def test_manual_cookie_header_overrides_browser_cookies(self):
        jar = cookies.jar_from_browser_cookies([
            {"name": "sessionid", "value": "old", "domain": ".douyin.com", "path": "/", "secure": True},
        ])
        cookies.add_header_cookies(jar, "sessionid=new; ttwid=t", ".douyin.com")
        request = urllib.request.Request("https://www.douyin.com/")
        jar.add_cookie_header(request)
        self.assertEqual(sorted(request.get_header("Cookie").split("; ")), ["sessionid=new", "ttwid=t"])
        browser = cookies.browser_cookies_from_header("sessionid=new", ".douyin.com")
        self.assertEqual(browser[0]["domain"], ".douyin.com")
        self.assertEqual(browser[0]["path"], "/")
        self.assertTrue(browser[0]["secure"])

    def test_netscape_file_round_trips_and_detects_login(self):
        browser = [
            {"name": "sessionid", "value": "abc", "domain": ".douyin.com", "path": "/",
             "expires": -1, "httpOnly": True, "secure": True},
            {"name": "ttwid", "value": "t", "domain": ".douyin.com", "path": "/",
             "expires": 2_000_000_000, "httpOnly": False, "secure": True},
        ]
        self.assertTrue(cookies.has_douyin_login(browser))
        self.assertFalse(cookies.has_douyin_login([{"name": "ttwid", "value": "t"}]))
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "cookies.txt"
            cookies.write_netscape_cookie_file(browser, path)
            jar = cookies.load_cookie_file(path)
            self.assertEqual(sorted(cookie.name for cookie in jar), ["sessionid", "ttwid"])

    def test_cookie_files_are_validated_before_use(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            missing = Path(temporary_dir) / "missing.txt"
            with self.assertRaises(ValueError):
                cookies.load_cookie_file(missing)
            not_netscape = Path(temporary_dir) / "notes.txt"
            not_netscape.write_text("hello world\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                cookies.load_cookie_file(not_netscape)
            huge = Path(temporary_dir) / "huge.txt"
            huge.write_bytes(b"# Netscape HTTP Cookie File\n" + b"#" * (cookies.MAX_COOKIE_FILE_BYTES + 1))
            with self.assertRaises(ValueError):
                cookies.load_cookie_file(huge)


class MediaHandler(BaseHTTPRequestHandler):
    """Serves scripted media responses for the downloader tests."""

    routes = {}
    seen = []

    def do_GET(self):
        type(self).seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
        route = type(self).routes.get(self.path.split("?", 1)[0])
        if route is None:
            self.send_error(404)
            return
        route(self)

    def log_message(self, *_args):
        pass


def serve_bytes(handler, data, *, honor_range=True, content_type="video/mp4"):
    range_header = handler.headers.get("Range")
    start = 0
    if honor_range and range_header and range_header.startswith("bytes="):
        start = int(range_header[6:].split("-", 1)[0])
    body = data[start:]
    handler.send_response(206 if start else 200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Accept-Ranges", "bytes")
    if start:
        handler.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
    handler.end_headers()
    handler.wfile.write(body)


class StreamDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MediaHandler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MediaHandler.routes = {}
        MediaHandler.seen = []
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.destination = Path(self.temporary_dir.name) / "video.mp4"

    def tearDown(self):
        self.temporary_dir.cleanup()

    def test_downloads_with_referer_user_agent_scoped_cookies_and_progress(self):
        MediaHandler.routes["/video.mp4"] = lambda handler: serve_bytes(handler, MP4_BYTES)
        jar = cookies.jar_from_browser_cookies([
            {"name": "cdn", "value": "1", "domain": "127.0.0.1", "path": "/", "secure": False},
            {"name": "sessionid", "value": "secret", "domain": ".douyin.com", "path": "/", "secure": True},
        ])
        updates = []
        result = http_download.stream_download(
            [f"{self.base}/video.mp4"],
            self.destination,
            headers={"User-Agent": "EdgeUA/1", "Referer": "https://www.douyin.com/"},
            cookie_jar=jar,
            on_progress=lambda downloaded, total, speed: updates.append((downloaded, total)),
            validate=local.require_mp4_header,
            progress_interval=0,
        )
        self.assertEqual(self.destination.read_bytes(), MP4_BYTES)
        self.assertEqual(result["bytes"], len(MP4_BYTES))
        headers = MediaHandler.seen[0]["headers"]
        self.assertEqual(headers["referer"], "https://www.douyin.com/")
        self.assertEqual(headers["user-agent"], "EdgeUA/1")
        self.assertEqual(headers.get("cookie"), "cdn=1")
        self.assertEqual(updates[-1], (len(MP4_BYTES), len(MP4_BYTES)))
        self.assertEqual(list(Path(self.temporary_dir.name).iterdir()), [self.destination])

    def test_forbidden_url_falls_back_to_the_next_candidate(self):
        MediaHandler.routes["/forbidden.mp4"] = lambda handler: handler.send_error(403)
        MediaHandler.routes["/video.mp4"] = lambda handler: serve_bytes(handler, MP4_BYTES)
        result = http_download.stream_download(
            [f"{self.base}/forbidden.mp4", f"{self.base}/video.mp4"],
            self.destination,
            validate=local.require_mp4_header,
        )
        self.assertEqual(result["url"], f"{self.base}/video.mp4")
        self.assertEqual(self.destination.read_bytes(), MP4_BYTES)

    def test_a_dropped_connection_resumes_with_a_range_request(self):
        attempts = {"count": 0}

        def flaky(handler):
            attempts["count"] += 1
            if attempts["count"] == 1:
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(MP4_BYTES)))
                handler.send_header("Accept-Ranges", "bytes")
                handler.end_headers()
                handler.wfile.write(MP4_BYTES[:40_000])
                handler.wfile.flush()
                handler.close_connection = True
                return
            serve_bytes(handler, MP4_BYTES)

        MediaHandler.routes["/flaky.mp4"] = flaky
        http_download.stream_download(
            [f"{self.base}/flaky.mp4"],
            self.destination,
            validate=local.require_mp4_header,
            retry_delays=(0, 0),
        )
        self.assertEqual(self.destination.read_bytes(), MP4_BYTES)
        self.assertEqual(MediaHandler.seen[1]["headers"].get("range"), "bytes=40000-")

    def test_cancel_stops_a_stalled_download_quickly_and_removes_the_partial_file(self):
        release = threading.Event()

        def stalled(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(MP4_BYTES) * 10))
            handler.end_headers()
            handler.wfile.write(MP4_BYTES[:1000])
            handler.wfile.flush()
            release.wait(10)

        MediaHandler.routes["/stalled.mp4"] = stalled
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        started = time.monotonic()
        try:
            with self.assertRaises(base.DownloadCancelledError):
                http_download.stream_download(
                    [f"{self.base}/stalled.mp4"],
                    self.destination,
                    is_cancelled=cancel.is_set,
                    timeout=30,
                )
        finally:
            release.set()
        self.assertLess(time.monotonic() - started, 5)
        time.sleep(0.3)
        self.assertEqual(list(Path(self.temporary_dir.name).iterdir()), [])

    def test_responses_that_are_not_video_are_rejected(self):
        MediaHandler.routes["/page.mp4"] = lambda handler: serve_bytes(
            handler, b"<html>blocked</html>", content_type="text/html"
        )
        with self.assertRaisesRegex(base.SourceError, "MP4"):
            http_download.stream_download(
                [f"{self.base}/page.mp4"], self.destination, validate=local.require_mp4_header
            )
        self.assertFalse(self.destination.exists())


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeResponse:
    def __init__(self, url, payload=None, resource_type="xhr", content_type="application/json"):
        self.url = url
        self.payload = payload
        self.headers = {"content-type": content_type}
        self.request = type("Request", (), {"resource_type": resource_type})()

    def json(self):
        return self.payload


class FakePage:
    def __init__(self, clock, *, deliveries=None, probes=None, dom_urls=None, render_data="", url=None):
        self.clock = clock
        self.url = url
        self.deliveries = deliveries or {}
        self.probes = list(probes or [])
        self.dom_urls = dom_urls or []
        self.render_data = render_data
        self.handlers = {}
        self.waits = 0
        self.reloads = 0
        self.closed = False
        self.init_scripts = []
        self.visited = []

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def on(self, event, handler):
        self.handlers[event] = handler

    def _deliver(self, key):
        for response in self.deliveries.get(key, []):
            self.handlers["response"](response)

    def goto(self, url, **_kwargs):
        self.visited.append(url)
        self._deliver("goto")

    def reload(self, **_kwargs):
        self.reloads += 1
        self._deliver("reload")

    def wait_for_timeout(self, milliseconds):
        self.waits += 1
        self.clock.now += milliseconds / 1000
        self._deliver(self.waits)

    def evaluate(self, script):
        if script == douyin.CAPTCHA_PROBE_SCRIPT:
            return self.probes.pop(0) if self.probes else {"captcha": False, "title": "Video - 抖音"}
        if script == douyin.RENDER_DATA_SCRIPT:
            return self.render_data
        # Douyin reads only the video of its modal; other sites read every <video> on the page.
        if script in (douyin.DOM_VIDEO_SCRIPT, browser_capture.DOM_VIDEO_SCRIPT):
            return self.dom_urls
        return "FakeEdge/1.0"

    def title(self):
        return "Video thử - 抖音"

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page


def detail_response(aweme_id=AWEME_ID):
    return FakeResponse(
        f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={aweme_id}&a_bogus=x",
        {"aweme_detail": {"aweme_id": aweme_id, "desc": "Từ API", "video": {
            "play_addr": {"url_list": ["https://v3-web.douyinvod.com/a.mp4"]},
        }}},
    )


class DouyinCaptureTests(unittest.TestCase):
    def test_the_detail_response_of_the_requested_video_is_captured(self):
        clock = FakeClock()
        page = FakePage(clock, deliveries={"goto": [detail_response("999999999")], 2: [detail_response()]})
        result = douyin.capture_video_from_page(FakeContext(page), AWEME_ID, clock=clock)
        self.assertEqual(result.detail["desc"], "Từ API")
        self.assertEqual(result.user_agent, "FakeEdge/1.0")
        # The video opens as a modal on the feed page, which also works for guests.
        self.assertEqual(page.visited, [f"https://www.douyin.com/jingxuan?modal_id={AWEME_ID}"])
        self.assertIn(douyin.MUTE_MEDIA_SCRIPT, page.init_scripts)
        self.assertTrue(page.closed)

    def test_a_captcha_extends_the_wait_and_reports_start_and_end(self):
        clock = FakeClock()
        captcha = {"captcha": True, "title": "验证码中间页"}
        page = FakePage(clock, probes=[captcha] * 20, deliveries={"reload": [detail_response()]})
        events = []
        result = douyin.capture_video_from_page(
            FakeContext(page),
            AWEME_ID,
            clock=clock,
            load_timeout=5,
            on_captcha=lambda active, _page: events.append(active),
        )
        self.assertEqual(events, [True, False])
        self.assertTrue(result.captcha_seen)
        self.assertEqual(page.reloads, 1)
        self.assertEqual(result.detail["aweme_id"], AWEME_ID)

    def test_background_prefetch_gives_up_on_a_captcha(self):
        clock = FakeClock()
        page = FakePage(clock, probes=[{"captcha": True, "title": ""}])
        with self.assertRaisesRegex(base.SourceError, "captcha"):
            douyin.capture_video_from_page(
                FakeContext(page), AWEME_ID, clock=clock, allow_captcha_wait=False
            )
        self.assertTrue(page.closed)

    def test_the_video_element_is_the_fallback_when_no_json_arrives(self):
        clock = FakeClock()
        page = FakePage(clock, dom_urls=["blob:https://www.douyin.com/1", "//v5-web.douyinvod.com/dom.mp4"])
        result = douyin.capture_video_from_page(FakeContext(page), AWEME_ID, clock=clock, load_timeout=3)
        self.assertIsNone(result.detail)
        self.assertEqual(result.dom_urls, ["https://v5-web.douyinvod.com/dom.mp4"])

        empty = FakePage(FakeClock())
        with self.assertRaisesRegex(base.SourceError, "Không đọc được dữ liệu video"):
            douyin.capture_video_from_page(FakeContext(empty), AWEME_ID, clock=empty.clock, load_timeout=3)

    def test_cancel_stops_the_capture_and_clears_the_captcha_prompt(self):
        clock = FakeClock()
        page = FakePage(clock, probes=[{"captcha": True, "title": ""}] * 5)
        events = []
        with self.assertRaises(base.DownloadCancelledError):
            douyin.capture_video_from_page(
                FakeContext(page),
                AWEME_ID,
                clock=clock,
                is_cancelled=lambda: page.waits >= 2,
                on_captcha=lambda active, _page: events.append(active),
            )
        self.assertEqual(events, [True, False])
        self.assertTrue(page.closed)


MODAL_URL = f"https://www.douyin.com/jingxuan?modal_id={AWEME_ID}"


class DouyinLoginWallTests(unittest.TestCase):
    def capture(self, page, clock, **kwargs):
        return douyin.capture_video_from_page(FakeContext(page), AWEME_ID, clock=clock, **kwargs)

    def test_a_redirect_to_jingxuan_without_the_video_stops_at_once(self):
        clock = FakeClock()
        page = FakePage(clock, url="https://www.douyin.com/jingxuan")
        with self.assertRaises(douyin.LoginRequiredError) as raised:
            self.capture(page, clock, load_timeout=45)
        self.assertEqual(raised.exception.reason, "redirect")
        self.assertLess(clock.now - 1000.0, 2, "must not wait for the 45 s deadline")
        self.assertEqual(page.visited, [MODAL_URL])
        self.assertTrue(page.closed)

    def test_another_video_in_the_modal_counts_as_a_redirect(self):
        clock = FakeClock()
        page = FakePage(clock, url=(
            "https://www.douyin.com/jingxuan?previous_page=web_video_404_link&modal_id=7693052223881891122"))
        with self.assertRaises(douyin.LoginRequiredError):
            self.capture(page, clock)

    def test_the_login_popup_without_video_data_stops_the_wait(self):
        clock = FakeClock()
        page = FakePage(clock, probes=[{"captcha": False, "login": True, "title": "抖音"}] * 40, url=MODAL_URL)
        with self.assertRaises(douyin.LoginRequiredError) as raised:
            self.capture(page, clock, load_timeout=45)
        self.assertEqual(raised.exception.reason, "login")
        self.assertLess(clock.now - 1000.0, 5)
        self.assertTrue(page.closed)

    def test_video_data_wins_over_the_login_popup_guests_always_see(self):
        clock = FakeClock()
        render = quote(json.dumps({"app": {"videoDetail": {"awemeId": AWEME_ID, "desc": "SSR", "video": {
            "playAddr": [{"src": "https://v1.douyinvod.com/a.mp4"}]}}}}))
        page = FakePage(clock, probes=[{"captcha": False, "login": True, "title": ""}] * 5,
                        render_data=render, url=MODAL_URL)
        self.assertEqual(self.capture(page, clock).detail["desc"], "SSR")

    def test_a_feed_video_behind_the_modal_is_never_taken_as_the_requested_one(self):
        clock = FakeClock()
        media = FakeResponse("https://v5-dy-ov.zjcdn.com/feed.mp4", resource_type="media", content_type="video/mp4")
        page = FakePage(clock, deliveries={1: [media]}, url=MODAL_URL)
        with self.assertRaises(base.SourceError):
            self.capture(page, clock, load_timeout=3)


KS_URL = "https://www.kuaishou.com/short-video/3xg9avuebhtfcbu"


class BrowserCaptureTests(unittest.TestCase):
    def test_a_direct_video_source_on_the_page_is_captured(self):
        clock = FakeClock()
        page = FakePage(clock, dom_urls=["blob:https://www.kuaishou.com/x", "https://v2.kwaicdn.com/a.mp4?pkey=1"])
        result = browser_capture.capture_media_from_page(FakeContext(page), KS_URL, clock=clock)
        self.assertEqual(result.media_urls, ["https://v2.kwaicdn.com/a.mp4?pkey=1"])
        self.assertEqual(result.title, "Video thử")
        self.assertEqual(result.user_agent, "FakeEdge/1.0")
        self.assertEqual(page.visited, [KS_URL])
        self.assertTrue(page.closed)

    def test_video_network_responses_are_captured_when_the_element_uses_a_blob(self):
        clock = FakeClock()
        media = FakeResponse("https://sns-video-bd.xhscdn.com/stream/1.mp4", resource_type="media",
                             content_type="video/mp4")
        page = FakePage(clock, deliveries={1: [media]}, dom_urls=["blob:https://www.xiaohongshu.com/1"])
        result = browser_capture.capture_media_from_page(FakeContext(page), KS_URL, clock=clock)
        self.assertEqual(result.media_urls, ["https://sns-video-bd.xhscdn.com/stream/1.mp4"])

    def test_edge_is_shown_when_the_page_needs_a_login_or_captcha(self):
        clock = FakeClock()
        page = FakePage(clock)
        events = []

        def on_attention(active, _page):
            events.append(active)
            if active:
                page.dom_urls = ["https://v2.kwaicdn.com/after-login.mp4"]

        result = browser_capture.capture_media_from_page(
            FakeContext(page), KS_URL, clock=clock, load_timeout=3, on_attention=on_attention
        )
        self.assertEqual(events, [True, False])
        self.assertEqual(result.media_urls, ["https://v2.kwaicdn.com/after-login.mp4"])

    def test_background_prefetch_does_not_wait_for_the_user(self):
        clock = FakeClock()
        page = FakePage(clock)
        with self.assertRaisesRegex(base.SourceError, "Không tìm thấy video"):
            browser_capture.capture_media_from_page(
                FakeContext(page), KS_URL, clock=clock, load_timeout=3, allow_attention_wait=False,
                on_attention=lambda *_args: self.fail("prefetch must not show Edge"),
            )
        self.assertTrue(page.closed)

    def test_cancel_stops_the_wait_and_clears_the_prompt(self):
        clock = FakeClock()
        page = FakePage(clock)
        events = []
        with self.assertRaises(base.DownloadCancelledError):
            browser_capture.capture_media_from_page(
                FakeContext(page), KS_URL, clock=clock, load_timeout=1,
                is_cancelled=lambda: page.waits >= 4,
                on_attention=lambda active, _page: events.append(active),
            )
        self.assertEqual(events, [True, False])
        self.assertTrue(page.closed)


class FakeYoutubeDL:
    instances = []
    cancel_on_progress = False

    def __init__(self, params):
        self.params = params
        FakeYoutubeDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        if self.params.get("cookiefile"):
            # yt-dlp writes cookies back to the cookie file when it closes.
            Path(self.params["cookiefile"]).write_text("# dumped by yt-dlp\n", encoding="utf-8")
        return False

    def extract_info(self, url, download=True):
        template = self.params["outtmpl"]["default"]
        path = Path(template.replace("%(id)s", "dQw4w9WgXcQ").replace("%(ext)s", "mp4"))
        path.parent.mkdir(parents=True, exist_ok=True)
        hook = self.params["progress_hooks"][0]
        hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "speed": 25.0,
              "filename": str(path)})
        path.write_bytes(MP4_BYTES)
        hook({"status": "finished", "downloaded_bytes": 100, "total_bytes": 100, "filename": str(path)})
        return {"id": "dQw4w9WgXcQ", "title": "Demo YouTube", "duration": 12, "ext": "mp4",
                "requested_downloads": [{"filepath": str(path)}]}


class YtDlpTests(unittest.TestCase):
    def setUp(self):
        FakeYoutubeDL.instances = []
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)

    def tearDown(self):
        self.temporary_dir.cleanup()

    def test_options_select_one_h264_mp4_without_remote_components(self):
        options = ytdlp_source.build_ytdlp_options(
            self.root / "cache", site="youtube", ffmpeg_path=r"C:\ffmpeg\bin\ffmpeg.exe",
            js_runtimes={"deno": {"path": r"C:\deno\deno.exe"}}, progress_hook=lambda _d: None,
        )
        self.assertTrue(options["noplaylist"])
        self.assertEqual(options["merge_output_format"], "mp4")
        self.assertIn("vcodec^=avc1", options["format"])
        self.assertEqual(options["ffmpeg_location"], r"C:\ffmpeg\bin\ffmpeg.exe")
        self.assertEqual(options["js_runtimes"], {"deno": {"path": r"C:\deno\deno.exe"}})
        self.assertNotIn("remote_components", options)
        # Cookies always come from a file, never from yt-dlp reading a browser's cookie store
        # (Chrome's App-Bound Encryption breaks that on Windows).
        self.assertNotIn("cookiesfrombrowser", options)
        self.assertTrue(options["outtmpl"]["default"].startswith(str(self.root / "cache" / "youtube-")))
        without_ffmpeg = ytdlp_source.build_ytdlp_options(self.root, site="tiktok", ffmpeg_path="")
        self.assertNotIn("+", without_ffmpeg["format"])
        self.assertNotIn("merge_output_format", without_ffmpeg)

    def test_download_reports_progress_and_never_rewrites_the_users_cookie_file(self):
        cookie_file = self.root / "cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        updates = []
        result = ytdlp_source.download_with_ytdlp(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            self.root / "cache",
            site="youtube",
            cookie_file=str(cookie_file),
            on_progress=lambda downloaded, total, speed: updates.append((downloaded, total, speed)),
            ydl_class=FakeYoutubeDL,
        )
        self.assertEqual(result["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(result["title"], "Demo YouTube")
        self.assertEqual(Path(result["path"]).read_bytes(), MP4_BYTES)
        self.assertEqual(updates[0], (50, 100, 25.0))
        used_cookie_file = FakeYoutubeDL.instances[0].params["cookiefile"]
        self.assertNotEqual(os.path.abspath(used_cookie_file), os.path.abspath(cookie_file))
        self.assertFalse(os.path.exists(used_cookie_file))
        self.assertEqual(cookie_file.read_text(encoding="utf-8"), "# Netscape HTTP Cookie File\n")

    def test_cancel_from_the_progress_hook_raises_download_cancelled(self):
        calls = {"count": 0}

        def cancelled_after_start():
            calls["count"] += 1
            return calls["count"] > 1

        with self.assertRaises(base.DownloadCancelledError):
            ytdlp_source.download_with_ytdlp(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                self.root / "cache",
                site="youtube",
                is_cancelled=cancelled_after_start,
                ydl_class=FakeYoutubeDL,
            )
        self.assertEqual(len(FakeYoutubeDL.instances), 1)

    def test_playlists_and_ytdlp_errors_become_source_errors(self):
        class PlaylistYDL(FakeYoutubeDL):
            def extract_info(self, url, download=True):
                return {"_type": "playlist", "entries": []}

        with self.assertRaisesRegex(base.SourceError, "playlist"):
            ytdlp_source.download_with_ytdlp(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", self.root, site="youtube", ydl_class=PlaylistYDL
            )

        from yt_dlp.utils import DownloadError

        class FailingYDL(FakeYoutubeDL):
            def extract_info(self, url, download=True):
                raise DownloadError("\x1b[0;31mERROR:\x1b[0m [youtube] dQw4w9WgXcQ: Video unavailable")

        with self.assertRaisesRegex(base.SourceError, "Video unavailable") as raised:
            ytdlp_source.download_with_ytdlp(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", self.root, site="youtube", ydl_class=FailingYDL
            )
        self.assertNotIn("\x1b", str(raised.exception))


class LocalVideoTests(unittest.TestCase):
    def test_mp4_and_mkv_files_are_accepted_by_their_header(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            mp4 = Path(temporary_dir) / "clip.MP4"
            mp4.write_bytes(MP4_BYTES)
            mkv = Path(temporary_dir) / "clip.mkv"
            mkv.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
            self.assertEqual(local.validate_local_video_path(str(mp4))["container"], "mp4")
            info = local.validate_local_video_path(str(mkv))
            self.assertEqual(info["container"], "mkv")
            self.assertEqual(info["path"], os.path.abspath(mkv))
            self.assertEqual(info["title"], "clip")

    def test_unsupported_missing_relative_or_fake_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            fake = Path(temporary_dir) / "fake.mp4"
            fake.write_bytes(b"not a video at all")
            avi = Path(temporary_dir) / "clip.avi"
            avi.write_bytes(MP4_BYTES)
            for path in (str(fake), str(avi), str(Path(temporary_dir) / "missing.mp4"), "relative.mp4"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    local.validate_local_video_path(path)


if __name__ == "__main__":
    unittest.main()
