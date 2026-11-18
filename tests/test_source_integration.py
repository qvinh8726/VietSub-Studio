"""Integration of the vietsub.sources download layer with app.py (v1.6.0)."""

import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from . import _isolation
except ImportError:
    import _isolation

import app
from vietsub.sources import base, douyin

AWEME_ID = "7312345678901234567"
CANONICAL = f"https://www.douyin.com/video/{AWEME_ID}"
KS_PHOTO_ID = "3xg9avuebhtfcbu"
XHS_NOTE_ID = "6411cf99000000001300b6d9"
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
MB = 1024 * 1024


def douyin_result(path, title="Từ Edge"):
    return {
        "source_url": CANONICAL,
        "video_id": AWEME_ID,
        "source_video": str(path),
        "video_title": title,
        "source_type": "douyin",
    }


class SandboxedCacheTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        self.cache = self.root / "downloads"
        self.config_path = self.root / "app_config.json"
        self.patchers = [
            patch.object(app, "DOWNLOAD_DIR_OVERRIDE", str(self.cache)),
            patch.object(app, "CONFIG_PATH", str(self.config_path)),
            patch.object(app, "DOUZY_DB", str(self.root / "missing-douzy.db")),
        ]
        for patcher in self.patchers:
            patcher.start()
        app.clear_download_progress()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        app.clear_download_progress()
        self.temporary_dir.cleanup()


class SandboxTests(unittest.TestCase):
    def test_the_download_cache_is_inside_the_test_sandbox(self):
        self.assertTrue(app.path_is_within(app.get_download_cache_dir(), _isolation.SANDBOX_DIR))


class HealthTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config["TESTING"] = False

    def test_health_has_no_douzy_items_and_reports_the_download_tools(self):
        with (
            patch.object(app, "resolve_edge_executable", return_value=r"C:\Edge\msedge.exe"),
            patch.object(app.ytdlp_source, "ytdlp_version", return_value="2026.10.26"),
            patch.object(app, "get_js_runtime", return_value={"deno": {"path": r"C:\deno.exe"}}),
            patch.object(app, "check_edge_status", return_value=False),
        ):
            data = self.client.get("/api/health").get_json()
        self.assertFalse([key for key in data["checks"] if key.startswith("douzy")])
        for key in ("douyin_source", "ytdlp", "js_runtime"):
            self.assertIn(key, data["checks"])
            self.assertFalse(data["checks"][key]["required"])
        self.assertIn("2026.10.26", data["checks"]["ytdlp"]["detail"])
        self.assertEqual(data["ready_douyin"], data["ready_local"])
        self.assertEqual(data["ready_web"], data["ready_local"])
        self.assertEqual(
            set(data["ready_sources"]),
            {"local", "douyin", "kuaishou", "xiaohongshu", "tiktok", "youtube", "bilibili"},
        )
        self.assertTrue(all(value == data["ready_local"] for value in data["ready_sources"].values()))

    def test_douyin_and_kuaishou_need_edge_and_other_links_need_ytdlp(self):
        with (
            patch.object(app, "resolve_edge_executable", return_value=""),
            patch.object(app.ytdlp_source, "ytdlp_version", return_value=""),
            patch.object(app, "get_js_runtime", return_value=None),
            patch.object(app, "check_edge_status", return_value=False),
        ):
            data = self.client.get("/api/health").get_json()
        self.assertFalse(data["ready_douyin"])
        self.assertFalse(data["ready_web"])
        ready = data["ready_sources"]
        self.assertEqual(ready["local"], data["ready_local"])
        for source in ("douyin", "kuaishou", "xiaohongshu", "tiktok", "youtube", "bilibili"):
            self.assertFalse(ready[source], source)

    def test_missing_dependencies_never_ask_for_douzy(self):
        with tempfile.NamedTemporaryFile(delete=False) as videocr:
            videocr_path = videocr.name
        try:
            with (
                patch.object(app, "VIDEOCR_CLI", videocr_path),
                patch.object(app, "get_translation_runtime_config", return_value={"provider": "deepseek"}),
                patch.object(app, "resolve_edge_executable", return_value=""),
                patch.object(app.ytdlp_source, "ytdlp_version", return_value=""),
            ):
                missing = app.missing_runtime_dependencies({"douyin", "youtube", "local"})
                kuaishou = app.missing_runtime_dependencies({"kuaishou"})
                xiaohongshu = app.missing_runtime_dependencies({"xiaohongshu"})
        finally:
            os.remove(videocr_path)
        self.assertEqual(missing, ["Microsoft Edge", "yt-dlp"])
        self.assertEqual(kuaishou, ["Microsoft Edge"])
        self.assertEqual(xiaohongshu, ["yt-dlp"])


class DownloadDispatchTests(SandboxedCacheTestCase):
    def fake_edge(self, captured):
        def download(aweme_id, target_path, notebook_url, *, quiet):
            captured.update({"aweme_id": aweme_id, "target": target_path, "quiet": quiet})
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_bytes(MP4_BYTES)
            return douyin_result(target_path)
        return download

    def test_a_share_text_short_link_is_resolved_over_http_and_captured_in_edge(self):
        captured = {}
        with (
            patch.object(app.douyin_source, "resolve_short_link", return_value=AWEME_ID) as resolve,
            patch.object(app, "resolve_url_via_edge") as edge_resolve,
            patch.object(app, "download_douyin_via_edge", side_effect=self.fake_edge(captured)),
        ):
            result = app.download_video_source(
                "4.89 复制打开抖音，看看【作品】 https://v.douyin.com/iRNBho6u/ i@P.Kj 08/05", ""
            )
        resolve.assert_called_once_with("https://v.douyin.com/iRNBho6u/")
        edge_resolve.assert_not_called()
        self.assertEqual(result["video_id"], AWEME_ID)
        self.assertEqual(result["source_url"], CANONICAL)
        self.assertEqual(result["source_type"], "douyin")
        self.assertEqual(Path(captured["target"]).name, f"douyin-{AWEME_ID}.mp4")
        self.assertTrue(app.path_is_within(captured["target"], str(self.cache)))
        self.assertFalse(captured["quiet"])

    def test_short_links_fall_back_to_edge_when_http_resolution_fails(self):
        with (
            patch.object(app.douyin_source, "resolve_short_link", side_effect=base.SourceError("blocked")),
            patch.object(app, "resolve_url_via_edge", return_value=CANONICAL) as edge_resolve,
            patch.object(app, "download_douyin_via_edge", side_effect=self.fake_edge({})),
        ):
            result = app.download_video_source("https://v.douyin.com/abc123/", "")
        edge_resolve.assert_called_once()
        self.assertEqual(result["video_id"], AWEME_ID)

    def test_ytdlp_is_the_fallback_when_the_edge_capture_fails(self):
        target = self.cache / "douyin-fallback.mp4"
        with (
            patch.object(app, "download_douyin_via_edge", side_effect=RuntimeError("Douyin đổi giao diện")),
            patch.object(app, "download_douyin_via_ytdlp", return_value=douyin_result(target, "Từ yt-dlp")) as ytdlp,
        ):
            result = app.download_video_source(CANONICAL, "")
        ytdlp.assert_called_once()
        self.assertEqual(result["video_title"], "Từ yt-dlp")

    def test_both_tiers_failing_reports_each_reason(self):
        with (
            patch.object(app, "download_douyin_via_edge", side_effect=RuntimeError("Douyin đổi giao diện")),
            patch.object(app, "download_douyin_via_ytdlp", side_effect=RuntimeError("Fresh cookies are needed")),
        ):
            with self.assertRaisesRegex(app.VideoDownloadError, "Edge.*đổi giao diện.*yt-dlp.*Fresh cookies"):
                app.download_video_source(CANONICAL, "")

    def test_a_cancel_is_never_turned_into_a_fallback(self):
        with (
            patch.object(app, "download_douyin_via_edge", side_effect=app.WorkflowCancelled("Đã huỷ")),
            patch.object(app, "download_douyin_via_ytdlp") as ytdlp,
        ):
            with self.assertRaises(app.WorkflowCancelled):
                app.download_video_source(CANONICAL, "")
        ytdlp.assert_not_called()

    def test_a_finished_download_in_the_cache_is_reused(self):
        folder = self.cache / f"douyin-{AWEME_ID}"
        folder.mkdir(parents=True)
        (folder / f"douyin-{AWEME_ID}.mp4").write_bytes(MP4_BYTES)
        (folder / f"douyin-{AWEME_ID}_data.json").write_text(json.dumps({"title": "Đã có sẵn"}), encoding="utf-8")
        with patch.object(app, "download_douyin_via_edge") as edge:
            result = app.download_video_source(CANONICAL, "")
        edge.assert_not_called()
        self.assertEqual(result["video_title"], "Đã có sẵn")
        self.assertEqual(Path(result["source_video"]).parent, folder)

    def test_a_video_already_downloaded_by_douzy_is_imported_without_downloading(self):
        video = self.root / "douzy" / "old.mp4"
        video.parent.mkdir()
        video.write_bytes(MP4_BYTES)
        database = self.root / "douzy.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE aweme (id INTEGER PRIMARY KEY, aweme_id TEXT, download_time INTEGER, "
                "file_path TEXT, title TEXT, metadata TEXT)"
            )
            connection.execute(
                "INSERT INTO aweme (aweme_id, download_time, file_path, title) VALUES (?, ?, ?, ?)",
                (AWEME_ID, 100, str(video), "Video Douzy cũ"),
            )
            connection.commit()
        finally:
            connection.close()
        with (
            patch.object(app, "DOUZY_DB", str(database)),
            patch.object(app, "download_douyin_via_edge") as edge,
        ):
            result = app.download_video_source(CANONICAL, "")
        edge.assert_not_called()
        self.assertEqual(result["source_video"], str(video))
        self.assertEqual(result["video_title"], "Video Douzy cũ")

    def fake_ytdlp(self, captured):
        def download(url, cache_root, **kwargs):
            captured.update(kwargs)
            captured["url"] = url
            captured["cache_root"] = str(cache_root)
            cookie_file = kwargs.get("cookie_file")
            captured["cookie_text"] = Path(cookie_file).read_text(encoding="utf-8") if cookie_file else ""
            path = Path(cache_root) / "web.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(MP4_BYTES)
            return {"path": str(path), "video_id": "dQw4w9WgXcQ", "title": "Demo", "duration": 3}
        return download

    def test_web_links_are_downloaded_by_ytdlp_without_the_edge_google_cookies(self):
        captured = {}
        with (
            patch.object(app.ytdlp_source, "download_with_ytdlp", side_effect=self.fake_ytdlp(captured)),
            patch.object(app, "check_edge_status", return_value=True),
            patch.object(app, "read_edge_cookies") as read_cookies,
            patch.object(app, "resolve_ffmpeg_executable", return_value=r"C:\ffmpeg.exe"),
            patch.object(app, "get_js_runtime", return_value={"deno": {"path": r"C:\deno.exe"}}),
        ):
            result = app.download_video_source("https://youtu.be/dQw4w9WgXcQ?si=1", "")
        self.assertEqual(result["source_type"], "youtube")
        self.assertEqual(result["source_url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(captured["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(captured["site"], "youtube")
        self.assertEqual(captured["cache_root"], str(self.cache))
        self.assertEqual(captured["ffmpeg_path"], r"C:\ffmpeg.exe")
        self.assertEqual(captured["js_runtimes"], {"deno": {"path": r"C:\deno.exe"}})
        self.assertIsNone(captured["cookie_file"])
        read_cookies.assert_not_called()

    def test_tiktok_gets_its_own_edge_cookies_through_a_temporary_file(self):
        captured = {}
        tiktok_cookie = {"name": "sid_tt", "value": "abc", "domain": ".tiktok.com", "path": "/",
                         "expires": -1, "httpOnly": True, "secure": True}
        with (
            patch.object(app.ytdlp_source, "download_with_ytdlp", side_effect=self.fake_ytdlp(captured)),
            patch.object(app, "check_edge_status", return_value=True),
            patch.object(app, "read_edge_cookies", return_value=[tiktok_cookie]) as read_cookies,
            patch.object(app, "resolve_ffmpeg_executable", return_value=""),
            patch.object(app, "get_js_runtime", return_value=None),
        ):
            result = app.download_video_source("https://www.tiktok.com/@a.b/video/7301234567890123456", "")
        read_cookies.assert_called_once_with(["https://www.tiktok.com/"])
        self.assertIn("sid_tt", captured["cookie_text"])
        self.assertFalse(os.path.exists(captured["cookie_file"]))
        self.assertEqual(result["source_type"], "tiktok")

    def test_a_configured_cookies_file_is_used_for_youtube_through_a_private_copy(self):
        original = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n"
        cookie_file = self.root / "cookies.txt"
        cookie_file.write_text(original, encoding="utf-8")
        self.config_path.write_text(json.dumps({"cookies_file": str(cookie_file)}), encoding="utf-8")
        captured = {}
        fake = self.fake_ytdlp(captured)

        def download_then_save_cookies(url, cache_root, **kwargs):
            result = fake(url, cache_root, **kwargs)
            # yt-dlp writes its cookie jar back to the cookie file when it closes.
            Path(kwargs["cookie_file"]).write_text("# Netscape HTTP Cookie File\nrewritten\n", encoding="utf-8")
            return result

        with (
            patch.object(app.ytdlp_source, "download_with_ytdlp", side_effect=download_then_save_cookies),
            patch.object(app, "check_edge_status", return_value=True),
            patch.object(app, "read_edge_cookies") as read_cookies,
            patch.object(app, "resolve_ffmpeg_executable", return_value=""),
            patch.object(app, "get_js_runtime", return_value=None),
        ):
            app.download_video_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "")
        self.assertEqual(captured["cookie_text"], original)
        self.assertNotEqual(os.path.normcase(captured["cookie_file"]), os.path.normcase(str(cookie_file)))
        self.assertFalse(os.path.exists(captured["cookie_file"]))
        self.assertEqual(cookie_file.read_text(encoding="utf-8"), original)
        read_cookies.assert_not_called()

    def test_unsupported_links_are_rejected_before_any_download(self):
        with self.assertRaisesRegex(ValueError, "Douyin, TikTok, YouTube"):
            app.download_video_source("https://example.test/video/1", "")

    def fake_browser(self, captured):
        def download(request, target_path, notebook_url, *, quiet):
            captured.update({"request": request, "target": target_path, "quiet": quiet})
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_bytes(MP4_BYTES)
            return {"source_url": request.url, "video_id": request.video_id, "source_video": target_path,
                    "video_title": "Từ trang", "source_type": request.site}
        return download

    def test_kuaishou_share_links_are_resolved_and_captured_in_edge(self):
        captured = {}
        resolved = app.web_source.parse_web_url(f"https://www.kuaishou.com/short-video/{KS_PHOTO_ID}")
        with (
            patch.object(app.web_source, "resolve_short_link", return_value=resolved) as resolve,
            patch.object(app, "download_via_browser_capture", side_effect=self.fake_browser(captured)),
            patch.object(app.ytdlp_source, "download_with_ytdlp") as ytdlp,
        ):
            result = app.download_video_source("https://v.kuaishou.com/AbCd12 复制此消息，打开【快手】直接观看！", "")
        resolve.assert_called_once()
        ytdlp.assert_not_called()
        self.assertEqual(captured["request"].url, f"https://www.kuaishou.com/short-video/{KS_PHOTO_ID}")
        self.assertEqual(Path(captured["target"]).name, f"kuaishou-{KS_PHOTO_ID}.mp4")
        self.assertTrue(app.path_is_within(captured["target"], str(self.cache)))
        self.assertFalse(captured["quiet"])
        self.assertEqual((result["source_type"], result["video_id"]), ("kuaishou", KS_PHOTO_ID))

    def test_xiaohongshu_falls_back_to_edge_when_ytdlp_fails(self):
        captured = {}
        with (
            patch.object(app.ytdlp_source, "download_with_ytdlp",
                         side_effect=base.SourceError("yt-dlp không tải được video: Unable to extract initial state")),
            patch.object(app, "download_via_browser_capture", side_effect=self.fake_browser(captured)),
            patch.object(app, "check_edge_status", return_value=False),
            patch.object(app, "resolve_ffmpeg_executable", return_value=""),
            patch.object(app, "get_js_runtime", return_value=None),
        ):
            result = app.download_video_source(f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}?xsec_token=T", "")
        self.assertEqual(result["video_title"], "Từ trang")
        self.assertEqual(captured["request"].url, f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}?xsec_token=T")

    def test_xiaohongshu_image_notes_do_not_open_edge(self):
        with (
            patch.object(app.ytdlp_source, "download_with_ytdlp",
                         side_effect=base.SourceError("yt-dlp không tải được video: No video formats found!")),
            patch.object(app, "download_via_browser_capture") as browser,
            patch.object(app, "check_edge_status", return_value=False),
            patch.object(app, "resolve_ffmpeg_executable", return_value=""),
            patch.object(app, "get_js_runtime", return_value=None),
        ):
            with self.assertRaisesRegex(app.VideoDownloadError, "không có video"):
                app.download_video_source(f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}", "")
        browser.assert_not_called()

    def test_a_platform_cookie_from_settings_replaces_the_edge_cookie_for_ytdlp(self):
        self.config_path.write_text(json.dumps({
            "source_cookies_protected": {"bilibili": app.protect_text_secret("SESSDATA=manual")},
        }), encoding="utf-8")
        edge_cookies = [
            {"name": "SESSDATA", "value": "from-edge", "domain": ".bilibili.com", "path": "/", "secure": True},
            {"name": "buvid3", "value": "b", "domain": ".bilibili.com", "path": "/", "secure": True},
        ]
        captured = {}
        with (
            patch.object(app.ytdlp_source, "download_with_ytdlp", side_effect=self.fake_ytdlp(captured)),
            patch.object(app, "check_edge_status", return_value=True),
            patch.object(app, "read_edge_cookies", return_value=edge_cookies),
            patch.object(app, "resolve_ffmpeg_executable", return_value=""),
            patch.object(app, "get_js_runtime", return_value=None),
        ):
            app.download_video_source("https://www.bilibili.com/video/BV1xx411c7mD", "")
        self.assertIn("SESSDATA\tmanual", captured["cookie_text"])
        self.assertNotIn("from-edge", captured["cookie_text"])
        self.assertIn("buvid3", captured["cookie_text"])


class FakeContext:
    def __init__(self):
        self.added_cookies = []
        self.pages = []

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    def cookies(self, urls=None):
        return [{"name": "sessionid", "value": "browser", "domain": ".douyin.com", "path": "/",
                 "expires": -1, "httpOnly": True, "secure": True}]


class FakePlaywright:
    def __init__(self, context):
        browser = Mock()
        browser.contexts = [context]
        self.chromium = Mock()
        self.chromium.connect_over_cdp.return_value = browser

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def make_detail():
    return {
        "aweme_id": AWEME_ID,
        "desc": "Video Edge",
        "author": {"nickname": "Tác giả"},
        "video": {
            "duration": 30_000,
            "bit_rate": [{"is_h265": 0, "bit_rate": 1, "play_addr": {
                "url_list": ["https://v3-web.douyinvod.com/avc.mp4"]}}],
            "origin_cover": {"url_list": ["https://p3-sign.douyinpic.com/cover.jpeg"]},
        },
    }


class DouyinEdgeDownloadTests(SandboxedCacheTestCase):
    def run_download(self, capture):
        context = FakeContext()
        streamed = {}

        def fake_stream(urls, destination, **kwargs):
            streamed.update(kwargs)
            streamed["urls"] = list(urls)
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(MP4_BYTES)
            return {"path": str(destination), "url": urls[0], "bytes": len(MP4_BYTES)}

        def fake_thumbnail(url, destination_base):
            Path(destination_base + ".jpg").write_bytes(b"\xff\xd8\xffcover")
            return destination_base + ".jpg"

        target = self.cache / f"douyin-{AWEME_ID}" / f"douyin-{AWEME_ID}.mp4"
        with (
            patch.object(app, "get_sync_playwright", return_value=lambda: FakePlaywright(context)),
            patch.object(app, "ensure_edge_running", return_value=True),
            patch.object(app.douyin_source, "capture_video_from_page", side_effect=capture),
            patch.object(app.http_download, "stream_download", side_effect=fake_stream),
            patch.object(app, "download_thumbnail", side_effect=fake_thumbnail),
            patch.object(app, "set_edge_window_visibility", return_value=True) as visibility,
        ):
            result = app.download_douyin_via_edge(AWEME_ID, str(target), "", quiet=False)
        return result, context, streamed, target, visibility

    def test_the_capture_is_downloaded_with_edge_headers_and_the_manual_cookie_override(self):
        self.config_path.write_text(json.dumps({
            "source_cookies_protected": {"douyin": app.protect_text_secret("sessionid=manual; ttwid=t")},
        }), encoding="utf-8")

        def capture(context, aweme_id, **kwargs):
            self.assertEqual(aweme_id, AWEME_ID)
            self.assertTrue(kwargs["allow_captcha_wait"])
            return douyin.CaptureResult(detail=make_detail(), user_agent="EdgeUA/142")

        result, context, streamed, target, _visibility = self.run_download(capture)
        self.assertEqual(result["video_title"], "Video Edge")
        self.assertEqual(result["source_video"], str(target))
        self.assertEqual(streamed["urls"], ["https://v3-web.douyinvod.com/avc.mp4"])
        self.assertEqual(streamed["headers"]["Referer"], "https://www.douyin.com/")
        self.assertEqual(streamed["headers"]["User-Agent"], "EdgeUA/142")
        request = urllib.request.Request("https://www.douyin.com/")
        streamed["cookie_jar"].add_cookie_header(request)
        self.assertIn("sessionid=manual", request.get_header("Cookie"))
        self.assertEqual({cookie["name"] for cookie in context.added_cookies}, {"sessionid", "ttwid"})
        metadata = json.loads((target.parent / f"douyin-{AWEME_ID}_data.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["title"], "Video Edge")
        self.assertEqual(metadata["author"], "Tác giả")
        self.assertTrue((target.parent / f"douyin-{AWEME_ID}_cover.jpg").is_file())

    def test_a_captcha_shows_edge_and_the_ui_notice_until_it_is_solved(self):
        seen = {}

        def capture(context, aweme_id, *, on_captcha, **kwargs):
            page = object()
            on_captcha(True, page)
            seen["during"] = app.progress_snapshot()["download"]["notice"]
            on_captcha(False, page)
            seen["after"] = app.progress_snapshot()["download"]["notice"]
            return douyin.CaptureResult(detail=make_detail(), user_agent="EdgeUA")

        app.reset_download_progress("douyin")
        _result, _context, _streamed, _target, visibility = self.run_download(capture)
        self.assertEqual(seen["during"], "Vui lòng kéo captcha trên trình duyệt Edge để tiếp tục tải")
        self.assertEqual(seen["after"], "")
        self.assertTrue(visibility.call_args_list[0].args[2])

    def test_page_media_urls_are_used_when_no_json_was_captured(self):
        def capture(context, aweme_id, **kwargs):
            return douyin.CaptureResult(dom_urls=["https://v5-web.douyinvod.com/dom.mp4"], page_title="Tên tab - 抖音")

        result, _context, streamed, _target, _visibility = self.run_download(capture)
        self.assertEqual(streamed["urls"], ["https://v5-web.douyinvod.com/dom.mp4"])
        self.assertEqual(result["video_title"], "Tên tab")


class DownloadProgressTests(unittest.TestCase):
    def tearDown(self):
        app.workflow_context.quiet = False
        app.clear_download_progress()

    def test_progress_is_reported_and_a_background_prefetch_stays_silent(self):
        app.reset_download_progress("douyin")
        app.report_download_progress(50 * MB, 100 * MB, 5 * MB)
        snapshot = app.progress_snapshot()["download"]
        self.assertTrue(snapshot["active"])
        self.assertEqual(snapshot["source"], "douyin")
        self.assertEqual((snapshot["downloaded"], snapshot["total"], snapshot["percent"]), (50 * MB, 100 * MB, 50))
        self.assertEqual(snapshot["speed"], 5 * MB)

        app.workflow_context.quiet = True
        app.report_download_progress(90 * MB, 100 * MB, 5 * MB)
        self.assertEqual(app.progress_snapshot()["download"]["downloaded"], 50 * MB)

        app.workflow_context.quiet = False
        app.clear_download_progress()
        self.assertFalse(app.progress_snapshot()["download"]["active"])


class FakeWebview:
    class FileDialog:
        OPEN = 10
        FOLDER = 20


class LocalPathPreviewTests(SandboxedCacheTestCase):
    def setUp(self):
        super().setUp()
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()
        self.local_video_dir = self.root / "managed"
        self.local_video_dir.mkdir()
        self.extra = [patch.object(app, "LOCAL_VIDEO_DIR", str(self.local_video_dir))]
        for patcher in self.extra:
            patcher.start()
        with app.preview_registry_lock:
            app.preview_registry.clear()
        with app.queue_state_lock:
            app.workflow_jobs.clear()

    def tearDown(self):
        for patcher in reversed(self.extra):
            patcher.stop()
        with app.preview_registry_lock:
            app.preview_registry.clear()
        with app.queue_state_lock:
            app.workflow_jobs.clear()
        app.app.config["TESTING"] = False
        super().tearDown()

    def make_api(self, selection):
        api = app.DesktopApi(FakeWebview)
        api._window = Mock()
        api._window.create_file_dialog.return_value = selection
        return api

    def test_a_file_picked_in_the_native_dialog_is_used_in_place(self):
        video = self.root / "clip.mkv"
        video.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 4096)
        api = self.make_api((str(video),))
        with patch.object(app, "get_video_resolution", return_value=(1920, 1080)):
            data = api.choose_local_video()
        self.assertEqual(data["source_type"], "local")
        self.assertEqual(data["video_title"], "clip")
        self.assertEqual(data["video_resolution"], {"width": 1920, "height": 1080})
        preview = app.get_video_preview(data["id"])
        self.assertEqual(preview["source_video"], str(video))
        self.assertFalse(preview["managed_source"])
        self.assertEqual(list(self.local_video_dir.iterdir()), [])
        dialog_args = api._window.create_file_dialog.call_args
        self.assertEqual(dialog_args.args[0], FakeWebview.FileDialog.OPEN)
        self.assertFalse(dialog_args.kwargs["allow_multiple"])
        self.assertTrue(any("*.mkv" in item for item in dialog_args.kwargs["file_types"]))
        response = self.client.get(data["video_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "video/x-matroska")
        response.close()

    def test_cancelled_and_invalid_selections(self):
        self.assertIsNone(self.make_api(None).choose_local_video())
        fake = self.root / "fake.mp4"
        fake.write_bytes(b"not a video")
        self.assertIn("MP4", self.make_api((str(fake),)).choose_local_video()["error"])

    def test_removing_the_job_never_deletes_the_users_file(self):
        video = self.root / "keep.mp4"
        video.write_bytes(MP4_BYTES)
        with patch.object(app, "get_video_resolution", return_value=None):
            data = app.create_local_path_preview(str(video))
        job = app.enqueue_workflow(data["source_url"], "ch", preview_id=data["id"])
        app.remove_queued_job(job["id"])
        app.prune_video_previews()
        self.assertTrue(video.is_file())

    def test_a_project_keeps_the_mkv_container_of_a_local_file(self):
        video = self.root / "movie.mkv"
        video.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x01" * 1024)
        project = app.prepare_project_files(str(video), "Phim", "local-1", output_dir=str(self.root / "out"))
        self.assertTrue(project["video"].endswith(".mkv"))
        self.assertEqual(Path(project["video"]).read_bytes(), video.read_bytes())
        self.assertTrue(video.is_file())
        self.assertEqual(
            app.infer_translation_video_path(project["raw_srt"]),
            project["video"],
        )


class SettingsAndSessionTests(SandboxedCacheTestCase):
    def setUp(self):
        super().setUp()
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config["TESTING"] = False
        super().tearDown()

    def test_platform_cookies_are_encrypted_and_never_returned(self):
        response = self.client.post("/api/settings", json={"source_cookies": {
            "douyin": "Cookie: sessionid=abc; ttwid=t",
            "bilibili": "SESSDATA=secret-sess",
        }})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["source_cookies_set"],
            {"douyin": True, "bilibili": True, "xiaohongshu": False, "kuaishou": False},
        )
        saved_text = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn("sessionid=abc", saved_text)
        self.assertNotIn("secret-sess", saved_text)
        protected = json.loads(saved_text)["source_cookies_protected"]
        self.assertTrue(protected["douyin"].startswith(("dpapi:", "plain:")))
        settings = self.client.get("/api/settings")
        data = settings.get_json()
        self.assertTrue(data["source_cookies_set"]["douyin"])
        self.assertEqual([site["key"] for site in data["source_session_sites"]],
                         ["douyin", "bilibili", "xiaohongshu", "kuaishou"])
        self.assertNotIn("sessionid=abc", settings.get_data(as_text=True))
        config = app.load_app_config()
        self.assertEqual(app.get_source_manual_cookie("douyin", config), "sessionid=abc; ttwid=t")
        self.assertEqual(app.get_douyin_manual_cookie(config), "sessionid=abc; ttwid=t")

        for invalid in ({"douyin": "bad cookie"}, {"youtube": "a=1"}, ["douyin"]):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/api/settings", json={"source_cookies": invalid}).status_code, 400)
        self.client.post("/api/settings", json={"source_cookies": {"douyin": None, "bilibili": ""}})
        cleared = self.client.get("/api/settings").get_json()["source_cookies_set"]
        self.assertFalse(cleared["douyin"])
        self.assertFalse(cleared["bilibili"])

    def test_the_cookies_file_must_be_a_netscape_file(self):
        notes = self.root / "notes.txt"
        notes.write_text("hello\n", encoding="utf-8")
        valid = self.root / "cookies.txt"
        valid.write_text("# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t0\tttwid\tt\n", encoding="utf-8")
        for value in ("relative.txt", str(notes)):
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/settings", json={"cookies_file": value}).status_code, 400)
        self.assertEqual(self.client.post("/api/settings", json={"cookies_file": str(valid)}).status_code, 200)
        self.assertEqual(self.client.get("/api/settings").get_json()["cookies_file"], str(valid))
        self.client.post("/api/settings", json={"cookies_file": ""})
        self.assertEqual(self.client.get("/api/settings").get_json()["cookies_file"], "")

    def test_the_sessions_endpoint_reports_each_platform_without_cookie_values(self):
        with patch.object(app, "check_edge_status", return_value=False):
            data = self.client.get("/api/sources/sessions").get_json()
        self.assertFalse(data["edge_connected"])
        # JSON objects are sorted by Flask; the UI takes the display order from source_session_sites.
        self.assertEqual(set(data["sites"]), {"douyin", "bilibili", "xiaohongshu", "kuaishou"})
        self.assertTrue(all(site["logged_in"] is None for site in data["sites"].values()))

        cookies = [
            {"name": "sessionid", "value": "very-secret", "domain": ".douyin.com"},
            {"name": "buvid3", "value": "guest", "domain": ".bilibili.com"},
            {"name": "web_session", "value": "guest-or-user", "domain": ".xiaohongshu.com"},
        ]
        with (
            patch.object(app, "check_edge_status", return_value=True),
            patch.object(app, "read_edge_cookies", return_value=cookies) as read_cookies,
        ):
            response = self.client.get("/api/sources/sessions")
        sites = response.get_json()["sites"]
        read_cookies.assert_called_once()
        self.assertEqual(sites["douyin"]["logged_in"], True)
        self.assertEqual(sites["bilibili"]["logged_in"], False)
        self.assertIsNone(sites["xiaohongshu"]["logged_in"])
        self.assertTrue(sites["xiaohongshu"]["has_cookies"])
        self.assertEqual((sites["kuaishou"]["logged_in"], sites["kuaishou"]["has_cookies"]), (False, False))
        body = response.get_data(as_text=True)
        self.assertNotIn("very-secret", body)
        self.assertNotIn("guest-or-user", body)

    def test_each_platform_can_be_opened_in_edge_for_login(self):
        with patch.object(app, "open_source_in_edge", return_value=True) as opener:
            self.assertEqual(self.client.post("/api/sources/kuaishou/open").status_code, 200)
            self.assertEqual(self.client.post("/api/sources/youtube/open").status_code, 404)
        opener.assert_called_once_with("kuaishou")
        with patch.object(app, "open_source_in_edge", side_effect=RuntimeError("Không mở được Edge")):
            response = self.client.post("/api/sources/douyin/open")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Edge", response.get_json()["error"])


class QueueAndProjectTests(SandboxedCacheTestCase):
    def setUp(self):
        super().setUp()
        app.app.config["TESTING"] = True
        with app.queue_state_lock:
            app.workflow_jobs.clear()
            app.current_job_id = ""
            app.queue_worker_thread = None

    def tearDown(self):
        with app.queue_state_lock:
            app.workflow_jobs.clear()
        app.update_progress(status="idle")
        app.prefetch_cancel_event.clear()
        app.app.config["TESTING"] = False
        super().tearDown()

    def test_web_links_can_be_queued_and_are_prefetched(self):
        job = app.enqueue_workflow("https://youtu.be/dQw4w9WgXcQ", "en")
        self.assertEqual(job["source_type"], "youtube")
        self.assertEqual(job["source_url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        app.reset_progress("running-job")
        app.update_progress(status="running", step="translate")
        downloaded = {"source_url": job["source_url"], "video_id": "dQw4w9WgXcQ",
                      "source_video": str(self.cache / "web.mp4"), "video_title": "Demo", "source_type": "youtube"}
        with (
            patch.object(app, "load_app_config", return_value={}),
            patch.object(app, "download_video_source", return_value=downloaded) as download,
        ):
            app.run_prefetch_worker()
        download.assert_called_once()
        with app.queue_state_lock:
            stored = next(item for item in app.workflow_jobs if item["id"] == job["id"])
        self.assertEqual(stored["source_video"], downloaded["source_video"])

    def test_the_cli_accepts_supported_web_links(self):
        with patch.object(app, "run_workflow_thread") as run:
            app.run_single_workflow("https://www.bilibili.com/video/BV1xx411c7mD", "ch")
        self.assertEqual(run.call_args.args[0], "https://www.bilibili.com/video/BV1xx411c7mD")
        with self.assertRaises(ValueError):
            app.run_single_workflow("https://example.test/video", "ch")

    def test_cached_downloads_move_into_the_project_without_touching_douzy_history(self):
        folder = self.cache / f"douyin-{AWEME_ID}"
        folder.mkdir(parents=True)
        source = folder / f"douyin-{AWEME_ID}.mp4"
        source.write_bytes(MP4_BYTES)
        (folder / f"douyin-{AWEME_ID}_cover.jpg").write_bytes(b"\xff\xd8\xffcover")
        (folder / f"douyin-{AWEME_ID}_data.json").write_text('{"title": "x"}', encoding="utf-8")
        self.assertTrue(app.should_move_downloaded_source(str(source)))
        self.assertFalse(app.should_move_downloaded_source(str(self.root / "elsewhere.mp4")))
        project = app.prepare_project_files(
            str(source), "Dự án", AWEME_ID, output_dir=str(self.root / "out"),
            defer_video_move=True, include_thumbnail=True,
        )
        with (
            patch.object(app, "update_douzy_video_path") as update_douzy,
            patch.object(app, "replace_source_video_references"),
        ):
            app.finalize_project_assets(project, AWEME_ID)
        update_douzy.assert_not_called()
        project_dir = Path(project["project_dir"])
        self.assertEqual(Path(project["video"]).read_bytes(), MP4_BYTES)
        self.assertTrue((project_dir / "Dự án.thumbnail.jpg").is_file())
        self.assertTrue((project_dir / "Dự án.data.json").is_file())
        self.assertFalse(folder.exists())

    def test_the_cache_prune_removes_only_stale_unreferenced_downloads(self):
        stale = self.cache / f"douyin-{AWEME_ID}"
        referenced = self.cache / "youtube-dQw4w9WgXcQ"
        fresh = self.cache / "tiktok-7301234567890123456"
        foreign = self.cache / "my notes"
        for folder in (stale, referenced, fresh, foreign):
            folder.mkdir(parents=True)
            (folder / "file.mp4").write_bytes(MP4_BYTES)
        old = time.time() - app.PREVIEW_TTL_SECONDS - 60
        for folder in (stale, referenced, foreign):
            os.utime(folder / "file.mp4", (old, old))
        removed = app.prune_download_cache([str(referenced / "file.mp4")])
        self.assertEqual(removed, 1)
        self.assertFalse(stale.exists())
        self.assertTrue(referenced.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(foreign.exists())


if __name__ == "__main__":
    unittest.main()
