"""Phase 2 Publish Hub reliability tests: selectors, assisted upload, debug trace, YouTube Data API v3."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

try:
    from . import _isolation
except ImportError:
    import _isolation

import app
from vietsub import redaction
from vietsub.uploader import adapters, browser, selector_store, youtube_api
from vietsub.uploader.profiles import ProfileStore
from vietsub.uploader.queue import UploadQueue
from tests.test_uploader_adapters import Node, Page as AdapterPage

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"x" * 64


class FakeTracing:
    def __init__(self):
        self.started = []
        self.stopped = []

    def start(self, **kwargs):
        self.started.append(kwargs)

    def stop(self, **kwargs):
        self.stopped.append(kwargs)
        path = kwargs.get("path")
        if path:
            Path(path).write_bytes(b"PK\x03\x04fake-playwright-trace-payload")


class FakeTracePage:
    def __init__(self):
        self.url = "about:blank"
        self.closed = False

    def is_closed(self):
        return self.closed

    def screenshot(self, **kwargs):
        Path(kwargs["path"]).write_bytes(b"\x89PNG\r\n\x1a\nfake-shot")


class FakeTraceContext:
    def __init__(self):
        self.initial = FakeTracePage()
        self.pages = [self.initial]
        self.tracing = FakeTracing()
        self.closed = 0

    def set_default_timeout(self, _ms):
        pass

    def set_default_navigation_timeout(self, _ms):
        pass

    def storage_state(self):
        return {"cookies": [], "origins": []}

    def close(self):
        self.closed += 1


class FakePlaywrightManager:
    def __init__(self, contexts):
        self.contexts = list(contexts)
        self.launches = []
        self.chromium = self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def launch_persistent_context(self, **kwargs):
        self.launches.append(kwargs)
        return self.contexts.pop(0)


class SelectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vietsub-sel-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(MP4)

    def test_bundled_selectors_load_cleanly_without_override(self):
        data, status = selector_store.load_selectors(self.root)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(set(data["platforms"]), {"tiktok", "youtube", "facebook", "douyin", "bilibili", "xiaohongshu"})
        self.assertFalse(status["override_present"])
        self.assertFalse(status["override_active"])
        self.assertEqual(status["warning"], "")

    def test_valid_partial_override_merges_only_requested_selectors(self):
        override = self.root / "uploader" / "selectors.override.json"
        override.parent.mkdir(parents=True)
        override.write_text(json.dumps({
            "schema_version": 1,
            "platforms": {
                "tiktok": {
                    "actions": {
                        "public": ["button[data-custom-post=\"1\"]"],
                    },
                },
            },
        }), encoding="utf-8")
        data, status = selector_store.load_selectors(self.root)
        self.assertTrue(status["override_present"])
        self.assertTrue(status["override_active"])
        self.assertEqual(status["warning"], "")
        self.assertEqual(data["platforms"]["tiktok"]["actions"]["public"], ['button[data-custom-post="1"]'])
        # Unspecified selectors stay intact from bundled defaults.
        self.assertIn('button[data-e2e="save_draft_button"]', data["platforms"]["tiktok"]["actions"]["draft"])
        self.assertIn("#done-button", data["platforms"]["youtube"]["actions"]["public"])

    def test_invalid_or_security_override_falls_back_with_vietnamese_warning(self):
        override = self.root / "uploader" / "selectors.override.json"
        override.parent.mkdir(parents=True)
        bad_payloads = [
            "{not-json",
            json.dumps({"schema_version": 99}),
            json.dumps({"hosts": ["evil.test"]}),
            json.dumps({"platforms": {"tiktok": {"upload_url": "https://evil.test"}}}),
            json.dumps({"platforms": {"tiktok": {"unknown_key": ["a"]}}}),
            json.dumps({"platforms": {"tiktok": {"file": []}}}),
            json.dumps({"platforms": {"tiktok": {"file": ["bad\x00selector"]}}}),
        ]
        bundled = selector_store.load_bundled_selectors()
        for raw in bad_payloads:
            with self.subTest(raw=raw):
                override.write_text(raw, encoding="utf-8")
                data, status = selector_store.load_selectors(self.root)
                self.assertTrue(status["override_present"])
                self.assertFalse(status["override_active"])
                self.assertTrue(status["warning"])
                self.assertEqual(data, bundled)

    def test_oversized_override_is_rejected(self):
        override = self.root / "uploader" / "selectors.override.json"
        override.parent.mkdir(parents=True)
        override.write_bytes(b" " * (selector_store.MAX_OVERRIDE_BYTES + 32))
        _, status = selector_store.load_selectors(self.root)
        self.assertFalse(status["override_active"])
        self.assertIn("256 KiB", status["warning"])

    def test_symlink_or_junction_override_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "selectors.override.json").write_text('{"schema_version":1}', encoding="utf-8")
        uploader_dir = self.root / "uploader"
        if os.name == "nt":
            import subprocess
            res = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(uploader_dir), str(outside)],
                capture_output=True, text=True, timeout=10,
            )
            if res.returncode != 0:
                self.skipTest(f"Junction unavailable: {res.stderr}")
        else:
            uploader_dir.mkdir(parents=True)
            (uploader_dir / "selectors.override.json").symlink_to(outside / "selectors.override.json")
        _, status = selector_store.load_selectors(self.root)
        self.assertFalse(status["override_active"])
        self.assertIn("liên kết", status["warning"])

    def test_hardened_vietnamese_selectors_for_tiktok_youtube_and_facebook(self):
        # 1) TikTok Vietnamese Studio + Lexical editor
        tk = AdapterPage("tiktok", "private")
        tk.mapping.clear()
        tk.add('input[type="file"][accept*="video"]', "file", visible=False)
        tk.add('div[data-lexical-editor="true"][contenteditable="true"]', "body", visible=lambda: tk.file_selected)
        tk.add('[role="progressbar"]', "progress", visible=lambda: tk.file_selected,
               attrs={"aria-valuenow": "100", "aria-valuemax": "100"})
        tk.add(':text-is("Đã tải lên")', "ready", visible=lambda: tk.file_selected)
        trigger = tk.add('[role="combobox"][aria-label="Ai có thể xem video này"]', "privacy", text="Mọi người")
        tk.add('[role="option"]:text-is("Chỉ mình tôi")', "option-private", click=lambda: setattr(trigger, "text", "Chỉ mình tôi"))
        tk.add('button:text-is("Đăng")', "submit", click=tk.submit)
        comp = tk.add('[role="status"]:text-matches("^(Video của bạn đã được (tải lên|đăng)|Đã đăng video|Đã tải video lên)[.!]?$", "i")',
                      "confirmation", visible=lambda: tk.confirmed)
        tk.add('a[href]', "result-link", parent=comp, attrs={"href": "https://www.tiktok.com/@vn/video/987654"})
        ad = adapters.adapter_for("tiktok")
        ad.form_timeout_ms = ad.processing_timeout_ms = ad.confirmation_timeout_ms = 500
        res = ad.upload(tk, self.video, {"title": "Xin chào", "description": "Mô tả", "hashtags": ["#vn"]}, visibility="private")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["url"], "https://www.tiktok.com/@vn/video/987654")

        # 2) YouTube Vietnamese Studio + still-processing dialog
        yt = AdapterPage("youtube", "private")
        yt.mapping.pop(
            'ytcp-video-upload-progress:text-matches("Upload complete|Checks complete|Finished processing|上传完成|检查完毕", "i")',
            None,
        )
        yt.add(
            'ytcp-video-upload-progress:text-matches("Đã tải lên xong|Đã hoàn tất quy trình kiểm tra|Không phát hiện vấn đề nào|Quá trình xử lý đã hoàn tất|Đã xử lý xong|Hoàn tất tải lên", "i")',
            "ready-vn",
            visible=True,
        )
        yt.mapping.pop("ytcp-video-share-dialog", None)
        comp_yt = yt.add("ytcp-uploads-still-processing-dialog", "confirmation-vn", visible=lambda: yt.confirmed)
        yt.add(':text-is("Video đã được xuất bản")', "heading-vn", parent=comp_yt)
        yt.add("a[href]", "link-vn", parent=comp_yt, attrs={"href": "https://www.youtube.com/shorts/abcdefghijk"})
        ad_yt = adapters.adapter_for("youtube")
        ad_yt.form_timeout_ms = ad_yt.processing_timeout_ms = ad_yt.confirmation_timeout_ms = 500
        res_yt = ad_yt.upload(yt, self.video, {"title": "Short VN", "description": "Mô tả", "hashtags": []}, visibility="private")
        self.assertEqual(res_yt["status"], "success")
        self.assertEqual(res_yt["url"], "https://www.youtube.com/shorts/abcdefghijk")

        # 3) Facebook Vietnamese story switch & reel mutation
        fb = AdapterPage("facebook", "draft")
        fb.mapping.pop('[data-surface$="bizweb_create_reel:left_panel"] input[role="switch"][aria-label="Share to story"]', None)
        fb.add('[data-surface$="bizweb_create_reel:left_panel"] input[role="switch"][aria-label="Chia sẻ lên tin"]', "story-vn", checked=False)
        fb.response.request = type("Req", (), {"post_data": "fb_api_req_friendly_name=BusinessComposerReelCreationMutation"})()
        ad_fb = adapters.adapter_for("facebook")
        ad_fb.form_timeout_ms = ad_fb.processing_timeout_ms = ad_fb.confirmation_timeout_ms = 500
        res_fb = ad_fb.upload(fb, self.video, {"title": "Reel VN", "description": "Mô tả", "hashtags": []}, visibility="draft")
        self.assertEqual(res_fb["status"], "draft")


class AssistedUploadAndTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vietsub-assisted-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(MP4)
        self.meta = {"title": "Video thử", "description": "Mô tả", "hashtags": ["#vietsub"]}

    def _adapter(self, platform="tiktok"):
        ad = adapters.adapter_for(platform)
        ad.form_timeout_ms = ad.processing_timeout_ms = ad.confirmation_timeout_ms = 500
        return ad

    def test_assisted_auto_detects_user_submit_and_emits_7_steps(self):
        page = AdapterPage("tiktok", "public")
        steps = []
        awaiting_called = []
        checkpoints = []

        def user_clicks_on_wait(p):
            self.assertEqual(p.submit_count, 0)  # adapter never clicked submit itself!
            p.confirmed = True

        page.on_wait = user_clicks_on_wait
        result = self._adapter("tiktok").upload(
            page,
            self.video,
            self.meta,
            visibility="public",
            upload_mode="assisted",
            before_publish=lambda: checkpoints.append(True),
            on_awaiting_submit=lambda sec: awaiting_called.append(sec),
            on_step=lambda step: steps.append(step),
            assisted_timeout_ms=1000,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(page.submit_count, 0)
        self.assertEqual(len(awaiting_called), 1)
        self.assertEqual(checkpoints, [True])
        self.assertEqual(steps, list(adapters.UPLOAD_STEPS))

    def test_assisted_manual_confirm_submitted_event(self):
        page = AdapterPage("youtube", "private", no_confirmation=True)
        confirm = threading.Event()
        checkpoints = []
        page.on_wait = lambda _p: confirm.set()
        result = self._adapter("youtube").upload(
            page,
            self.video,
            self.meta,
            visibility="private",
            upload_mode="assisted",
            confirm_submitted=confirm,
            before_publish=lambda: checkpoints.append(True),
            assisted_timeout_ms=1000,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(page.submit_count, 0)
        self.assertEqual(checkpoints, [True])

    def test_assisted_cancel_timeout_and_browser_close_states(self):
        # 1) Cancel
        page = AdapterPage("tiktok", "public", no_confirmation=True)
        cancel = threading.Event()
        page.on_wait = lambda _p: cancel.set()
        with self.assertRaises(adapters.UploadCancelled):
            self._adapter("tiktok").upload(
                page, self.video, self.meta, visibility="public",
                upload_mode="assisted", cancel=cancel, assisted_timeout_ms=1000,
            )
        self.assertEqual(page.submit_count, 0)

        # 2) Timeout -> VerificationRequired (NOT uncertain -> needs_verification)
        page2 = AdapterPage("tiktok", "public", no_confirmation=True)
        with self.assertRaises(adapters.VerificationRequired) as caught_to:
            self._adapter("tiktok").upload(
                page2, self.video, self.meta, visibility="public",
                upload_mode="assisted", assisted_timeout_ms=30,
            )
        self.assertFalse(getattr(caught_to.exception, "uncertain", False))

        # 3) Browser closed while awaiting submit -> VerificationRequired(uncertain=True)
        page3 = AdapterPage("tiktok", "public", no_confirmation=True)
        page3.on_wait = lambda p: setattr(p, "closed", True)
        with self.assertRaises(adapters.VerificationRequired) as caught_close:
            self._adapter("tiktok").upload(
                page3, self.video, self.meta, visibility="public",
                upload_mode="assisted", assisted_timeout_ms=1000,
            )
        self.assertTrue(getattr(caught_close.exception, "uncertain", False))

    def test_queue_assisted_lifecycle_confirm_timeout_and_restart_recovery(self):
        queue_path = self.root / "queue.json"
        entered_awaiting = threading.Event()
        done_first = threading.Event()
        done_second = threading.Event()
        calls = []

        def execute(job, cancel, progress, checkpoint):
            calls.append(job["profile_id"])
            if job["profile_id"] == "p1":
                progress(step="open_studio", phase="uploading", percent=5)
                progress(status="awaiting_user_submit", phase="awaiting_user_submit", step="submit",
                         percent=88, awaiting_deadline=time.time() + 600)
                entered_awaiting.set()
                confirm = queue.confirm_event(job["id"])
                self.assertTrue(confirm.wait(3))
                checkpoint()
                done_first.set()
                return {"status": "success", "url": "https://www.tiktok.com/@u/video/1", "message": "ok"}
            progress(step="fill_metadata", phase="uploading", percent=50)
            done_second.set()
            raise adapters.VerificationRequired("Timed out")

        queue = UploadQueue(queue_path, execute)
        self.addCleanup(queue.shutdown)
        jobs = queue.submit(
            {"video": str(self.video)},
            [{"id": "p1", "platform": "tiktok"}, {"id": "p2", "platform": "tiktok"}],
            self.meta,
            "public",
            None,
            "assisted-req-001",
            upload_mode="assisted",
            debug_mode=True,
        )
        self.assertTrue(entered_awaiting.wait(3))
        self.assertEqual(queue.get(jobs[0]["id"])["status"], "awaiting_user_submit")
        self.assertEqual(queue.get(jobs[0]["id"])["upload_mode"], "assisted")
        self.assertTrue(queue.get(jobs[0]["id"])["debug_mode"])
        queue.confirm_submitted(jobs[0]["id"])
        self.assertTrue(done_first.wait(3))
        self.assertTrue(done_second.wait(3))
        queue.shutdown()
        self.assertEqual(queue.get(jobs[0]["id"])["status"], "success")
        self.assertEqual(queue.get(jobs[1]["id"])["status"], "needs_verification")
        self.assertEqual(queue.get(jobs[1]["id"])["failed_step"], "fill_metadata")
        self.assertEqual(calls, ["p1", "p2"])

        # Restart with awaiting_user_submit persisted -> transitions immediately to uncertain + commit_attempted=True
        raw = json.loads(queue_path.read_text(encoding="utf-8"))
        raw["jobs"][1].update(status="awaiting_user_submit", commit_attempted=False, awaiting_deadline=9999999999.0)
        queue_path.write_text(json.dumps(raw), encoding="utf-8")
        reloaded = UploadQueue(queue_path, lambda *a: None)
        recovered = reloaded.get(jobs[1]["id"])
        self.assertEqual(recovered["status"], "uncertain")
        self.assertTrue(recovered["commit_attempted"])
        self.assertIsNone(recovered["awaiting_deadline"])
        with self.assertRaises(ValueError):
            reloaded.retry(jobs[1]["id"])


class DebugTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vietsub-trace-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ProfileStore(self.root / "uploader")
        self.profile = self.store.create("tiktok", "Trace Account")
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(MP4)
        self.runtime = browser.BrowserRuntime(self.store, lambda: "C:/Browser/chrome.exe")

    def test_debug_mode_forces_headed_discards_on_success_saves_and_prunes_on_failure(self):
        ok_ctx = FakeTraceContext()
        fail_ctx = FakeTraceContext()
        manager = FakePlaywrightManager([ok_ctx, fail_ctx])

        class StubAdapter:
            platform = "tiktok"
            hosts = ("www.tiktok.com",)
            def __init__(self, should_fail):
                self.should_fail = should_fail
            def upload(self, page, video_path, metadata, **kwargs):
                if kwargs.get("on_step"):
                    kwargs["on_step"]("fill_metadata")
                if self.should_fail:
                    raise adapters.VerificationRequired("Captcha")
                kwargs["before_publish"]()
                return {"status": "draft", "url": "", "message": "ok"}

        with patch.object(browser, "get_sync_playwright", return_value=lambda: manager):
            # 1) Success with debug_mode=True -> headed + trace discarded
            with patch.object(adapters, "adapter_for", return_value=StubAdapter(False)):
                res = self.runtime.upload(
                    self.profile["id"], str(self.video),
                    {"title": "T", "description": "D", "hashtags": []},
                    visibility="draft", before_publish=lambda: None,
                    upload_mode="auto", debug_mode=True, job_id="a" * 32,
                )
            self.assertEqual(res["status"], "draft")
            self.assertFalse(manager.launches[0]["headless"])
            self.assertEqual(ok_ctx.tracing.started, [{"screenshots": True, "snapshots": True, "sources": False}])
            self.assertEqual(ok_ctx.tracing.stopped, [{}])

            # 2) Failure with debug_mode=True -> trace .zip saved + failed_step attached
            with patch.object(adapters, "adapter_for", return_value=StubAdapter(True)):
                with self.assertRaises(adapters.VerificationRequired) as caught:
                    self.runtime.upload(
                        self.profile["id"], str(self.video),
                        {"title": "T", "description": "D", "hashtags": []},
                        visibility="draft", before_publish=lambda: None,
                        upload_mode="auto", debug_mode=True, job_id="b" * 32,
                    )
            self.assertEqual(caught.exception.failed_step, "fill_metadata")
            trace_file = Path(caught.exception.trace_path)
            self.assertTrue(trace_file.is_file())
            self.assertEqual(trace_file.parent, Path(self.store.trace_dir(self.profile["id"])))

        # 3) Pruning caps file count to MAX_TRACE_FILES_PER_PROFILE (10)
        traces_dir = Path(self.store.trace_dir(self.profile["id"]))
        for i in range(14):
            f = traces_dir / f"trace-{'0' * 30}{i:02d}-abcd1234.zip"
            f.write_bytes(b"PK\x03\x04" + b"z" * 100)
            os.utime(f, (1000 + i, 1000 + i))
        self.runtime._prune_traces(traces_dir)
        remaining = list(traces_dir.glob("trace-*.zip"))
        self.assertEqual(len(remaining), browser.MAX_TRACE_FILES_PER_PROFILE)

        # 4) Deleting profile with clean_artifacts=True purges traces + oauth file
        oauth_file = Path(self.store.oauth_path(self.profile["id"]))
        oauth_file.write_text('{"schema_version":1}', encoding="utf-8")
        self.store.delete(self.profile["id"], clean_artifacts=True)
        self.assertFalse(traces_dir.exists())
        self.assertFalse(oauth_file.exists())


class FakeHTTPResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._body


class YouTubeDataAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vietsub-ytapi-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.oauth_path = self.root / "youtube_oauth.json"
        self.video = self.root / "clip.mp4"
        # 600 KiB video -> 3 chunks when chunk_size = 256 KiB
        self.video.write_bytes(MP4 + b"A" * (600 * 1024 - len(MP4)))
        self.meta = {"title": "Clip YouTube", "description": "Mô tả chi tiết", "hashtags": ["#VietSub"]}

    def _client(self, request_fn, *, ffprobe_resolver=None, open_browser=None):
        http = Mock()
        http.request.side_effect = request_fn
        return youtube_api.YouTubeAPIClient(
            protect_secret=app.protect_text_secret,
            unprotect_secret=app.unprotect_config_secret,
            ffprobe_resolver=ffprobe_resolver,
            http_session=http,
            open_browser=open_browser,
        )

    def test_parse_client_secret_json_installed_and_rejects_web(self):
        installed = json.dumps({
            "installed": {
                "client_id": "1234567890-abc.apps.googleusercontent.com",
                "client_secret": "GOCSPX-secret-value-12345",
            }
        })
        cid, csec = youtube_api.parse_client_secret_json(installed)
        self.assertEqual(cid, "1234567890-abc.apps.googleusercontent.com")
        self.assertEqual(csec, "GOCSPX-secret-value-12345")

        web_json = json.dumps({
            "web": {
                "client_id": "1234567890-abc.apps.googleusercontent.com",
                "client_secret": "GOCSPX-secret-value-12345",
            }
        })
        with self.assertRaisesRegex(ValueError, "Desktop app"):
            youtube_api.parse_client_secret_json(web_json)

    def test_oauth_pkce_loopback_ignores_favicon_and_closes_socket(self):
        captured = {}

        def fake_http(method, url, **kwargs):
            if url == youtube_api.GOOGLE_TOKEN_URL:
                captured["token_data"] = kwargs["data"]
                return FakeHTTPResponse(200, {
                    "access_token": "ya29.fake-access-token-secret",
                    "refresh_token": "1//fake-refresh-token-secret",
                    "expires_in": 3600,
                    "scope": " ".join(youtube_api.YOUTUBE_SCOPES),
                })
            if url == youtube_api.YOUTUBE_CHANNELS_URL:
                return FakeHTTPResponse(200, {"items": [{"snippet": {"title": "Kênh VietSub Test"}}]})
            raise AssertionError(f"Unexpected URL: {url}")

        def simulate_browser(auth_url):
            captured["auth_url"] = auth_url
            params = parse_qs(urlsplit(auth_url).query)
            redirect_uri = params["redirect_uri"][0]
            state = params["state"][0]
            base = redirect_uri.rsplit("/oauth2callback", 1)[0]

            def hit_loopback():
                # 1) Auxiliary /favicon.ico MUST return 204 and NOT end loop
                with urlopen(f"{base}/favicon.ico", timeout=5) as fav:
                    captured["favicon_status"] = fav.status
                # 2) Real callback
                with urlopen(f"{redirect_uri}?state={state}&code=mock-auth-code-999", timeout=5) as cb:
                    captured["callback_status"] = cb.status

            threading.Thread(target=hit_loopback, daemon=True).start()

        client = self._client(fake_http, open_browser=simulate_browser)
        client.save_client_config(
            self.oauth_path,
            client_id="1234567890-abc.apps.googleusercontent.com",
            client_secret="GOCSPX-secret-value-12345",
        )
        status = client.start_oauth_flow("prof-1", self.oauth_path, timeout_seconds=15)
        self.assertEqual(captured["favicon_status"], 204)
        self.assertEqual(captured["callback_status"], 200)
        self.assertTrue(status["connected"])
        self.assertEqual(status["channel_title"], "Kênh VietSub Test")
        # Verify PKCE S256 challenge matches SHA256(code_verifier)
        auth_params = parse_qs(urlsplit(captured["auth_url"]).query)
        self.assertEqual(auth_params["code_challenge_method"], ["S256"])
        verifier = captured["token_data"]["code_verifier"]
        expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        self.assertEqual(auth_params["code_challenge"][0], expected_challenge)
        # Verify raw secrets are not stored in plaintext on Windows DPAPI & never in status
        status_json = json.dumps(status)
        self.assertNotIn("GOCSPX-secret-value-12345", status_json)
        self.assertNotIn("ya29.fake-access-token-secret", status_json)
        self.assertNotIn("1//fake-refresh-token-secret", status_json)

    def test_resumable_multichunk_upload_checkpoint_retry_and_shorts_detection(self):
        calls = []
        progress_events = []
        checkpoints = []
        total = self.video.stat().st_size
        chunk_size = 256 * 1024
        put_attempt = [0]

        def fake_http(method, url, **kwargs):
            self.assertFalse(kwargs.get("allow_redirects", True))
            calls.append((method, url, kwargs.get("headers", {})))
            if url == youtube_api.YOUTUBE_UPLOAD_INIT_URL:
                # Verify #Shorts appended for vertical short video
                self.assertIn("#Shorts", kwargs["json"]["snippet"]["description"])
                return FakeHTTPResponse(
                    200,
                    {},
                    headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=abc123"},
                )
            if method == "PUT":
                content_range = kwargs["headers"]["Content-Range"]
                if content_range.startswith("bytes */"):
                    return FakeHTTPResponse(308, {}, headers={"Range": f"bytes=0-{chunk_size - 1}"})
                put_attempt[0] += 1
                if put_attempt[0] == 1:
                    return FakeHTTPResponse(308, {}, headers={"Range": f"bytes=0-{chunk_size - 1}"})
                if put_attempt[0] == 2:
                    # Simulate transient 503 on intermediate chunk 2 -> should probe & retry
                    return FakeHTTPResponse(503, {"error": {"message": "Backend transient"}})
                if put_attempt[0] == 3:
                    return FakeHTTPResponse(308, {}, headers={"Range": f"bytes=0-{2 * chunk_size - 1}"})
                # Final chunk: checkpoint MUST already have fired!
                self.assertEqual(checkpoints, [True])
                return FakeHTTPResponse(200, {"id": "dQw4w9WgXcQ"})
            raise AssertionError(f"Unexpected call: {method} {url}")

        ffprobe_json = json.dumps({
            "streams": [{"width": 1080, "height": 1920}],
            "format": {"duration": "45.2"},
        })
        client = self._client(fake_http, ffprobe_resolver=lambda: "ffprobe")
        client.save_client_config(
            self.oauth_path,
            client_id="1234567890-abc.apps.googleusercontent.com",
            client_secret="GOCSPX-secret-value-12345",
        )
        # Seed connected tokens
        raw = json.loads(self.oauth_path.read_text(encoding="utf-8"))
        raw["access_token_protected"] = app.protect_text_secret("ya29.valid-token-xyz123456")
        raw["refresh_token_protected"] = app.protect_text_secret("1//valid-refresh-xyz123456")
        raw["token_expires_at"] = time.time() + 3600
        self.oauth_path.write_text(json.dumps(raw), encoding="utf-8")

        with patch.object(youtube_api.subprocess, "check_output", return_value=ffprobe_json), \
                patch.object(youtube_api.time, "sleep"):
            result = client.upload(
                "prof-1",
                self.oauth_path,
                self.video,
                self.meta,
                visibility="private",
                progress=lambda payload: progress_events.append(payload),
                before_publish=lambda: checkpoints.append(True),
                chunk_size=chunk_size,
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["url"], "https://www.youtube.com/shorts/dQw4w9WgXcQ")
        self.assertEqual(checkpoints, [True])
        steps = [evt["step"] for evt in progress_events]
        self.assertIn("upload_file", steps)
        self.assertIn("submit", steps)
        self.assertEqual(steps[-1], "confirm")

    def test_offhost_location_and_secret_redaction_and_token_refresh(self):
        # 1) Off-host Location header blocked
        client_evil = self._client(
            lambda method, url, **kwargs: FakeHTTPResponse(200, {}, headers={"Location": "https://evil.test/upload"})
        )
        client_evil.save_client_config(
            self.oauth_path,
            client_id="1234567890-abc.apps.googleusercontent.com",
            client_secret="GOCSPX-secret-value-12345",
        )
        raw = json.loads(self.oauth_path.read_text(encoding="utf-8"))
        raw["access_token_protected"] = app.protect_text_secret("ya29.secret-leak-token-99999")
        raw["refresh_token_protected"] = app.protect_text_secret("1//refresh-leak-token-99999")
        raw["token_expires_at"] = time.time() - 10  # expired -> triggers auto refresh!
        self.oauth_path.write_text(json.dumps(raw), encoding="utf-8")

        refreshed = [False]

        def fake_refresh_then_evil(method, url, **kwargs):
            if url == youtube_api.GOOGLE_TOKEN_URL:
                refreshed[0] = True
                return FakeHTTPResponse(200, {"access_token": "ya29.new-fresh-token-88888", "expires_in": 3600})
            return FakeHTTPResponse(200, {}, headers={"Location": "https://evil.test/upload"})

        client_evil._http.request.side_effect = fake_refresh_then_evil
        with self.assertRaisesRegex(youtube_api.YouTubeAPIError, "miền ngoài Google API"):
            client_evil.upload("prof-1", self.oauth_path, self.video, self.meta, visibility="private")
        self.assertTrue(refreshed[0])

        # 2) Secret token echoed in Google API error is redacted
        def fake_error_leak(method, url, **kwargs):
            return FakeHTTPResponse(400, {"error": {"message": "Bad request for ya29.new-fresh-token-88888"}})

        client_evil._http.request.side_effect = fake_error_leak
        with self.assertRaises(youtube_api.YouTubeAPIError) as err:
            client_evil.upload("prof-1", self.oauth_path, self.video, self.meta, visibility="private")
        self.assertNotIn("ya29.new-fresh-token-88888", str(err.exception))


class Phase2APIEndpointsAndSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vietsub-p2-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = app.app.test_client()
        self.enterContext(patch.dict(app.app.config, TESTING=True))
        self.enterContext(patch.object(app, "CONFIG_DIR", str(self.root)))
        self.enterContext(patch.object(app, "CONFIG_PATH", str(self.root / "config.json")))
        self.enterContext(patch.object(app, "log_to_app"))
        self.enterContext(patch.object(app.uploader_api, "instances", {}))
        self.enterContext(patch.object(app.uploader_api, "sessions", {}))
        self.enterContext(patch.object(app.uploader_api, "_yt_client", None))
        self.addCleanup(app.uploader_api.shutdown)
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(MP4)
        self.result = {
            "project_dir": str(self.root),
            "project_name": "clip",
            "video": str(self.video),
            "source_video": str(self.video),
            "raw_srt": str(self.root / "raw.srt"),
            "translated_srt": str(self.root / "vi.srt"),
        }
        Path(self.result["raw_srt"]).write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
        Path(self.result["translated_srt"]).write_text("1\n00:00:00,000 --> 00:00:01,000\nChào\n", encoding="utf-8")
        self.enterContext(patch.object(app, "workflow_jobs", [{
            "id": "proj-1", "status": "success", "result_ready": True,
            "result": self.result, "source_video": str(self.video), "source_type": "local",
        }]))
        self.meta = {"title": "Tiêu đề", "description": "Mô tả", "hashtags": ["#Việt"]}

    def _create_ready_profile(self, platform="youtube"):
        resp = self.client.post("/api/uploader/profiles", json={"platform": platform, "name": "Kênh P2"})
        self.assertEqual(resp.status_code, 201)
        prof = resp.get_json()
        app.uploader_api.services()[0].update_session(prof["id"], "alive", "session_alive")
        return prof

    def test_capabilities_default_upload_mode_and_youtube_api_bypasses_assisted_browser(self):
        caps = self.client.get("/api/uploader/capabilities").get_json()
        self.assertEqual(caps["default_upload_mode"], "assisted")
        self.assertEqual(caps["upload_modes"], ["assisted", "auto"])
        self.assertTrue(caps["debug_trace_supported"])
        self.assertTrue(caps["youtube_api_supported"])
        self.assertIn("selector_override", caps)

        # Backend queue defaults upload_mode to 'auto' when omitted (backward compatibility)
        prof = self._create_ready_profile("tiktok")
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        resp = self.client.post("/api/uploader/queue", json={
            "job_id": "proj-1", "profile_ids": [prof["id"]], "metadata": self.meta,
            "visibility": "draft", "scheduled_at": future, "request_id": "compat-default-01",
        })
        self.assertEqual(resp.status_code, 202)
        job = resp.get_json()["jobs"][0]
        self.assertEqual(job["upload_mode"], "auto")
        self.assertFalse(job["debug_mode"])

        # YouTube API v3 profile queued with upload_mode="assisted" runs API directly (never browser)
        yt_prof = self._create_ready_profile("youtube")
        cfg_resp = self.client.post(f"/api/uploader/profiles/{yt_prof['id']}/youtube-oauth/config", json={
            "client_secret_json": json.dumps({
                "installed": {
                    "client_id": "1234567890-abc.apps.googleusercontent.com",
                    "client_secret": "GOCSPX-secret-value-12345",
                }
            })
        })
        self.assertEqual(cfg_resp.status_code, 200, cfg_resp.get_json())
        self.assertEqual(cfg_resp.get_json()["upload_method"], "youtube_api")
        self.assertNotIn("GOCSPX-secret-value-12345", json.dumps(cfg_resp.get_json()))

        # Seed connected OAuth tokens
        store, br, queue = app.uploader_api.services()
        oauth_file = Path(store.oauth_path(yt_prof["id"]))
        raw = json.loads(oauth_file.read_text(encoding="utf-8"))
        raw["access_token_protected"] = app.protect_text_secret("ya29.fake-connected-token")
        raw["refresh_token_protected"] = app.protect_text_secret("1//fake-connected-refresh")
        raw["token_expires_at"] = time.time() + 3600
        oauth_file.write_text(json.dumps(raw), encoding="utf-8")
        store.update_session(yt_prof["id"], "alive", "oauth_connected")

        api_done = threading.Event()
        def fake_yt_upload(profile_id, oauth_path, video_path, metadata, **kwargs):
            kwargs["before_publish"]()
            api_done.set()
            return {"status": "success", "url": "https://www.youtube.com/watch?v=abcdefghijk", "message": "ok"}

        with patch.object(br, "upload", side_effect=AssertionError("Browser must NOT be called for youtube_api")), \
                patch.object(app.uploader_api._youtube_client(), "upload", side_effect=fake_yt_upload):
            q_resp = self.client.post("/api/uploader/queue", json={
                "job_id": "proj-1", "profile_ids": [yt_prof["id"]], "metadata": self.meta,
                "visibility": "private", "upload_mode": "assisted", "request_id": "yt-api-assisted-01",
            })
            self.assertEqual(q_resp.status_code, 202, q_resp.get_json())
            self.assertTrue(api_done.wait(3))
            time.sleep(0.05)
        final_job = queue.get(q_resp.get_json()["jobs"][0]["id"])
        self.assertEqual(final_job["status"], "success")
        self.assertEqual(final_job["result_url"], "https://www.youtube.com/watch?v=abcdefghijk")

    def test_signed_trace_download_and_testing_false_csrf_enforcement(self):
        prof = self._create_ready_profile("tiktok")
        store, _, queue = app.uploader_api.services()
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        q_resp = self.client.post("/api/uploader/queue", json={
            "job_id": "proj-1", "profile_ids": [prof["id"]], "metadata": self.meta,
            "visibility": "draft", "scheduled_at": future, "debug_mode": True,
            "request_id": "trace-security-01",
        })
        job_id = q_resp.get_json()["jobs"][0]["id"]
        trace_file = Path(store.trace_dir(prof["id"])) / f"trace-{job_id}-12345678.zip"
        trace_file.write_bytes(b"PK\x03\x04trace-zip-content")
        queue._update(job_id, status="error", trace_path=str(trace_file))

        public = self.client.get("/api/uploader/queue").get_json()["jobs"][0]
        self.assertNotIn("trace_path", public)
        self.assertTrue(public["trace_url"].startswith(f"/api/uploader/queue/{job_id}/trace?signature="))

        # Verify TESTING=False security boundary on all new endpoints
        with patch.dict(app.app.config, TESTING=False):
            headers = {app.API_TOKEN_HEADER: app.API_TOKEN}
            # Trace GET works with valid HMAC signature (no token header required), rejects tampered signature
            with self.client.get(public["trace_url"]) as ok_trace:
                self.assertEqual(ok_trace.status_code, 200)
                self.assertEqual(ok_trace.get_data(), b"PK\x03\x04trace-zip-content")
            self.assertEqual(self.client.get(f"/api/uploader/queue/{job_id}/trace?signature=bad").status_code, 403)
            self.assertEqual(self.client.get(f"/api/uploader/queue/{job_id}/trace").status_code, 403)

            # New POST endpoints require X-VietSub-Token + same-origin + JSON
            for path in (
                f"/api/uploader/queue/{job_id}/confirm-submitted",
                f"/api/uploader/profiles/{prof['id']}/config",
                f"/api/uploader/profiles/{prof['id']}/youtube-oauth/config",
                f"/api/uploader/profiles/{prof['id']}/youtube-oauth/start",
                f"/api/uploader/profiles/{prof['id']}/youtube-oauth/disconnect",
            ):
                with self.subTest(path=path):
                    self.assertEqual(self.client.post(path, json={}).status_code, 403)
                    self.assertEqual(
                        self.client.post(path, json={}, headers={**headers, "Origin": "https://evil.test"}).status_code,
                        403,
                    )
                    self.assertEqual(
                        self.client.post(path, data="plain", headers=headers).status_code,
                        415,
                    )


if __name__ == "__main__":
    unittest.main()
