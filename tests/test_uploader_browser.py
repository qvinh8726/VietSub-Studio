"""Mock-only uploader browser runtime tests; no live browser/platform required."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from vietsub.uploader import browser
from vietsub.uploader.profiles import ProfileBusy, ProfileStore, UnsafeProfilePath


class UploadError(RuntimeError):
    pass


class VerificationRequired(UploadError):
    pass


class UploadCancelled(UploadError):
    pass


class UnsupportedMode(UploadError):
    pass


class FakePage:
    def __init__(self):
        self.navigations = []
        self.closed = False
        self.waits = []
        self.on_wait = None
        self.screenshots = []
        self.url = "about:blank"

    def goto(self, url, **kwargs):
        self.navigations.append((url, kwargs))
        self.url = url

    def is_closed(self):
        return self.closed

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        if self.on_wait:
            self.on_wait()

    def screenshot(self, **kwargs):
        self.screenshots.append(kwargs)
        Path(kwargs["path"]).write_bytes(b"\x89PNG\r\n\x1a\nprivate-local-page")


class FakeContext:
    def __init__(self, page=None, *, no_pages=False):
        self.initial = page or FakePage()
        self.pages = [] if no_pages else [self.initial]
        self.new_pages = 0
        self.closed = 0
        self.snapshots = 0
        self.storage = {"cookies": [{"name": "sid", "value": "secret-cookie"}], "origins": []}

    def set_default_timeout(self, milliseconds):
        self.default_timeout = milliseconds

    def set_default_navigation_timeout(self, milliseconds):
        self.navigation_timeout = milliseconds

    def new_page(self):
        self.new_pages += 1
        self.pages.append(self.initial)
        return self.initial

    def storage_state(self):
        self.snapshots += 1
        return self.storage

    def close(self):
        self.closed += 1


class FakePlaywright:
    def __init__(self, contexts):
        self.contexts = list(contexts)
        self.launches = []
        self.entered = self.exited = 0
        self.chromium = self

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *_args):
        self.exited += 1

    def launch_persistent_context(self, **kwargs):
        self.launches.append(kwargs)
        return self.contexts.pop(0)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = ProfileStore(self.root / "uploader")
        self.account = self.store.create("tiktok", "Account")
        self.identifier = self.account["id"]
        self.logs = []
        self.runtime = browser.BrowserRuntime(self.store, lambda: "C:/Browser/chrome.exe", log=lambda *args: self.logs.append(args))
        self.adapter = SimpleNamespace(
            platform="tiktok", login_url="https://www.tiktok.com/login", hosts=("www.tiktok.com",),
            probe=Mock(return_value={"logged_in": True, "reason": "secret-cookie"}),
            upload=Mock(return_value={"status": "draft", "url": "", "message": "secret-cookie"}),
        )
        self.adapters = SimpleNamespace(adapter_for=Mock(return_value=self.adapter), UploadError=UploadError,
                                        VerificationRequired=VerificationRequired, UploadCancelled=UploadCancelled,
                                        UnsupportedMode=UnsupportedMode)
        self.enterContext(patch.object(browser, "_adapters", return_value=self.adapters))

    def playwright(self, *contexts):
        manager = FakePlaywright(contexts)
        self.enterContext(patch.object(browser, "get_sync_playwright", return_value=lambda: manager))
        return manager

    def finish(self):
        event = threading.Event()
        event.set()
        return event

    def upload(self, **kwargs):
        return self.runtime.upload(self.identifier, str(self.root / "input.mp4"),
                                   {"title": "Title", "description": "Description", "hashtags": ["#tag"]},
                                   visibility="draft", **kwargs)

    def assert_closed(self, manager, *contexts):
        self.assertEqual(manager.entered, manager.exited)
        for context in contexts:
            self.assertEqual(context.closed, 1)
        with self.store.lease(self.identifier):
            pass

    def test_headed_login_explicit_finish_saves_private_snapshot_reuses_initial_tab(self):
        context = FakeContext()
        manager = self.playwright(context)
        result = self.runtime.login(self.identifier, finish=self.finish())
        self.assertEqual(result["session_status"], "alive")
        self.assertFalse(manager.launches[0]["headless"])
        self.assertEqual(manager.launches[0]["user_data_dir"], self.store.profile_dir(self.identifier))
        self.assertEqual(manager.launches[0]["executable_path"], "C:/Browser/chrome.exe")
        self.assertEqual(context.new_pages, 0)
        self.assertEqual(context.initial.navigations[0][0], self.adapter.login_url)
        self.adapter.probe.assert_called_once_with(context.initial)
        self.assertEqual(context.snapshots, 1)
        self.assertEqual(json.loads(Path(self.store.state_path(self.identifier)).read_text(encoding="utf-8")), context.storage)
        self.assertNotIn("secret-cookie", json.dumps([result, self.logs, self.store.list()]))
        self.assert_closed(manager, context)

    def test_finish_is_explicit_login_does_not_probe_before_finish(self):
        context = FakeContext()
        finished = threading.Event()
        def on_wait():
            self.adapter.probe.assert_not_called()
            finished.set()
        context.initial.on_wait = on_wait
        manager = self.playwright(context)
        self.assertEqual(self.runtime.login(self.identifier, finish=finished)["session_status"], "alive")
        self.assertEqual(context.initial.waits, [200])
        self.assert_closed(manager, context)

    def test_login_cancel_before_start_never_imports_or_opens_browser(self):
        with patch.object(browser, "get_sync_playwright") as lazy:
            result = self.runtime.login(self.identifier, cancel=self.finish())
        self.assertEqual(result["status"], "cancelled")
        self.adapters.adapter_for.assert_not_called()
        lazy.assert_not_called()

    def test_login_cancel_during_wait_closes_context_no_snapshot(self):
        context = FakeContext()
        cancel = threading.Event()
        context.initial.on_wait = cancel.set
        manager = self.playwright(context)
        result = self.runtime.login(self.identifier, cancel=cancel, finish=threading.Event())
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(context.snapshots, 0)
        self.adapter.probe.assert_not_called()
        self.assert_closed(manager, context)

    def test_login_is_capped_at_ten_minutes_without_sleeping_for_deadline(self):
        self.assertEqual(browser.MANUAL_LOGIN_SECONDS, 600)
        context = FakeContext()
        manager = self.playwright(context)
        with patch.object(browser.time, "monotonic", side_effect=[0, 0, 600]):
            result = self.runtime.login(self.identifier)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["session_detail"], "login_timeout")
        self.assertEqual(context.initial.waits, [200])
        self.assertTrue(Path(result["screenshot_path"]).is_file())
        self.assertEqual(context.snapshots, 0)
        self.assert_closed(manager, context)

    def test_login_page_closed_by_user_is_cancelled_and_does_not_probe(self):
        context = FakeContext()
        context.initial.closed = True
        manager = self.playwright(context)
        result = self.runtime.login(self.identifier, finish=self.finish())
        self.assertEqual(result["status"], "cancelled")
        self.adapter.probe.assert_not_called()
        self.assert_closed(manager, context)

    def test_probe_alive_expired_and_challenge_do_not_save_or_restore_storage_state(self):
        self.store.save_storage_state(self.identifier, {"cookies": [], "origins": []})
        original = Path(self.store.state_path(self.identifier)).read_bytes()
        contexts = [FakeContext(), FakeContext(), FakeContext()]
        manager = self.playwright(*contexts)
        for value, expected in ((True, "alive"), (False, "expired"), (None, "needs_verification")):
            self.adapter.probe.return_value = {"logged_in": value, "reason": "secret-cookie"}
            result = self.runtime.probe(self.identifier)
            self.assertEqual(result["session_status"], expected)
            self.assertNotIn("reason", result)
        for launch in manager.launches:
            self.assertTrue(launch["headless"])
            self.assertIn("--headless=new", launch["args"])
            self.assertIn("--disable-blink-features=AutomationControlled", launch["args"])
            self.assertNotIn("storage_state", launch)
            self.assertNotIn("9222", json.dumps(launch))
        self.assertEqual(Path(self.store.state_path(self.identifier)).read_bytes(), original)
        self.assertEqual([item.snapshots for item in contexts], [0, 0, 0])
        self.assert_closed(manager, *contexts)

    def test_two_accounts_launch_distinct_persistent_contexts_and_profiles(self):
        second = self.store.create("youtube", "Second")
        first_context, second_context = FakeContext(), FakeContext()
        manager = self.playwright(first_context, second_context)
        self.runtime.probe(self.identifier)
        self.runtime.probe(second["id"])
        self.assertNotEqual(manager.launches[0]["user_data_dir"], manager.launches[1]["user_data_dir"])
        self.assertEqual(second_context.new_pages, 0)
        self.assertEqual([call.args[0] for call in self.adapters.adapter_for.call_args_list], ["tiktok", "youtube"])
        self.assert_closed(manager, first_context, second_context)
        self.assertFalse(any(isinstance(value, (FakeContext, FakePage, FakePlaywright)) for value in vars(self.runtime).values()))

    def test_missing_initial_page_creates_only_one_page(self):
        context = FakeContext(no_pages=True)
        manager = self.playwright(context)
        self.runtime.probe(self.identifier)
        self.assertEqual(context.new_pages, 1)
        self.assert_closed(manager, context)

    def test_probe_and_login_failure_sanitized_and_closed(self):
        contexts = [FakeContext(), FakeContext()]
        manager = self.playwright(*contexts)
        self.adapter.probe.side_effect = RuntimeError("Cookie: secret-cookie https://host/?token=secret-token")
        results = [self.runtime.probe(self.identifier), self.runtime.login(self.identifier, finish=self.finish())]
        for result in results:
            self.assertEqual(result["session_status"], "error")
            self.assertTrue(Path(result["screenshot_path"]).is_file())
        self.assertNotIn("secret-cookie", json.dumps([results, self.logs, self.store.list()]))
        self.assert_closed(manager, *contexts)

    def test_busy_profile_refuses_login_probe_upload_before_browser_import(self):
        with self.store.lease(self.identifier), patch.object(browser, "get_sync_playwright") as lazy:
            for operation in (lambda: self.runtime.login(self.identifier), lambda: self.runtime.probe(self.identifier), lambda: self.upload()):
                with self.assertRaises(ProfileBusy):
                    operation()
        lazy.assert_not_called()

    def test_upload_calls_adapter_once_passes_checkpoint_progress_and_saves_snapshot(self):
        context = FakeContext()
        manager = self.playwright(context)
        progress = Mock()
        checkpoint = Mock()
        cancel = threading.Event()
        def upload(page, video_path, metadata, **kwargs):
            self.assertIs(page, context.initial)
            self.assertEqual(metadata["title"], "Title")
            self.assertEqual(kwargs["visibility"], "draft")
            self.assertIs(kwargs["cancel"], cancel)
            kwargs["progress"](45, "secret-cookie")
            kwargs["before_publish"]()
            return {"status": "draft", "url": "https://www.tiktok.com/@user/video/12?token=secret-cookie#secret-token",
                    "message": "secret-cookie", "cookies": "secret-cookie"}
        self.adapter.upload.side_effect = upload
        result = self.upload(progress=progress, before_publish=checkpoint, cancel=cancel)
        checkpoint.assert_called_once_with()
        progress.assert_called_once_with({"phase": "uploading", "percent": 45})
        self.adapter.upload.assert_called_once()
        self.assertEqual(result, {"status": "draft", "url": "https://www.tiktok.com/@user/video/12", "message": "Draft save confirmed."})
        self.assertEqual(self.store.get(self.identifier)["session_status"], "alive")
        self.assertEqual(context.snapshots, 1)
        self.assertNotIn("secret-cookie", json.dumps([result, self.logs, self.store.list(), progress.call_args.args]))
        self.assert_closed(manager, context)

    def test_upload_verification_raises_correct_class_with_exact_task_owned_local_screenshot(self):
        context = FakeContext()
        manager = self.playwright(context)
        artifact_dir = self.root / "artifacts" / "task-owned"
        self.adapter.upload.side_effect = VerificationRequired("Captcha cookie=secret-cookie url=https://x?token=secret-token")
        with self.assertRaises(VerificationRequired) as caught:
            self.upload(artifact_dir=str(artifact_dir))
        screenshot = Path(caught.exception.screenshot_path)
        self.assertEqual(screenshot.parent, artifact_dir)
        self.assertTrue(screenshot.is_file())
        self.assertIn("verification-", screenshot.name)
        self.assertNotIn("secret-cookie", str(caught.exception) + json.dumps(self.logs))
        self.assertEqual(self.store.get(self.identifier)["session_status"], "needs_verification")
        self.assertEqual(context.snapshots, 0)
        self.assert_closed(manager, context)

    def test_upload_failure_cancel_and_invalid_confirmation_close_and_sanitize(self):
        for failure in (RuntimeError("secret-cookie"), UploadCancelled("secret-token"), None):
            with self.subTest(failure=failure):
                context = FakeContext()
                manager = self.playwright(context)
                self.adapter.upload.side_effect = failure
                self.adapter.upload.return_value = {"status": "unconfirmed", "cookies": "secret-cookie"}
                with self.assertRaises(UploadError) as caught:
                    self.upload(artifact_dir=self.root / "tasks")
                self.assertNotIn("secret-cookie", str(caught.exception))
                self.assertTrue(Path(caught.exception.screenshot_path).is_file())
                self.assert_closed(manager, context)

    def test_precancelled_upload_never_launches_browser(self):
        with patch.object(browser, "get_sync_playwright") as lazy:
            with self.assertRaises(UploadCancelled):
                self.upload(cancel=self.finish())
        lazy.assert_not_called()
        self.adapter.upload.assert_not_called()

    def test_cancelled_after_launch_closes_owned_context(self):
        context = FakeContext()
        cancel = threading.Event()
        manager = self.playwright(context)
        original_launch = manager.launch_persistent_context
        def launch(**kwargs):
            result = original_launch(**kwargs)
            cancel.set()
            return result
        manager.launch_persistent_context = launch
        with self.assertRaises(UploadCancelled):
            self.upload(cancel=cancel)
        self.adapter.upload.assert_not_called()
        self.assert_closed(manager, context)

    def test_confirmed_upload_survives_snapshot_metadata_log_failure_without_retry(self):
        context = FakeContext()
        manager = self.playwright(context)
        self.runtime.log = Mock(side_effect=RuntimeError("logging failed"))
        with patch.object(self.store, "save_storage_state", side_effect=OSError("disk full")), \
                patch.object(self.store, "update_session", side_effect=OSError("disk full")):
            result = self.upload()
        self.assertEqual(result["status"], "draft")
        self.adapter.upload.assert_called_once()
        self.assert_closed(manager, context)

    def test_browser_setup_failure_still_closes_owned_context_and_driver(self):
        context = FakeContext()
        context.set_default_timeout = Mock(side_effect=RuntimeError("secret-cookie"))
        manager = self.playwright(context)
        self.assertEqual(self.runtime.probe(self.identifier)["session_status"], "error")
        self.assert_closed(manager, context)

    def test_executable_unavailable_is_sanitized_and_releases_profile_lease(self):
        self.runtime.executable_resolver = lambda: ""
        with patch.object(browser, "get_sync_playwright") as lazy:
            result = self.runtime.probe(self.identifier)
            self.assertEqual(result["session_status"], "error")
            with self.assertRaises(UploadError):
                self.upload()
        lazy.assert_not_called()
        with self.store.lease(self.identifier):
            pass

    def test_task_artifact_symlink_is_refused_before_browser_launch(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "linked-artifacts"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Symlink creation unavailable: " + str(error))
        with patch.object(browser, "get_sync_playwright") as lazy:
            with self.assertRaises(UnsafeProfilePath):
                self.upload(artifact_dir=link / "task")
        lazy.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows directory junction test")
    def test_task_artifact_junction_is_refused_without_writing_outside(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "linked-artifacts"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            self.skipTest("Junction creation unavailable: " + result.stderr)
        with patch.object(browser, "get_sync_playwright") as lazy:
            with self.assertRaises(UnsafeProfilePath):
                self.upload(artifact_dir=link / "task")
        lazy.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_verification_class_and_screenshot_survive_session_metadata_failure(self):
        context = FakeContext()
        manager = self.playwright(context)
        self.adapter.upload.side_effect = VerificationRequired("secret-cookie")
        with patch.object(self.store, "update_session", side_effect=OSError("disk full")):
            with self.assertRaises(VerificationRequired) as caught:
                self.upload(artifact_dir=self.root / "task")
        self.assertTrue(Path(caught.exception.screenshot_path).is_file())
        self.assert_closed(manager, context)

    def test_screenshot_failure_never_hides_original_error_and_cleanup(self):
        context = FakeContext()
        context.initial.screenshot = Mock(side_effect=RuntimeError("secret-cookie"))
        manager = self.playwright(context)
        self.adapter.upload.side_effect = VerificationRequired("secret-cookie")
        with self.assertRaises(VerificationRequired) as caught:
            self.upload(artifact_dir=self.root / "task")
        self.assertEqual(caught.exception.screenshot_path, "")
        self.assertNotIn("secret-cookie", str(caught.exception) + json.dumps(self.logs))
        self.assert_closed(manager, context)

    def test_result_url_preserves_youtube_id_but_never_token_or_fragment(self):
        adapter = SimpleNamespace(platform="youtube")
        result = browser.BrowserRuntime._result(adapter, {
            "status": "success", "url": "https://www.youtube.com/watch?v=abcdefghijk&token=secret-cookie#secret-token"})
        self.assertEqual(result["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        for url in ("https://evil.test/watch?v=abcdefghijk", "https://name:secret@www.youtube.com/watch?v=abcdefghijk"):
            self.assertEqual(browser.BrowserRuntime._result(adapter, {"status": "success", "url": url})["url"], "")


class LazyImportTests(unittest.TestCase):
    def test_import_profiles_and_browser_does_not_import_browser_engines_app_or_adapters(self):
        script = """
import sys
import vietsub.uploader.profiles
import vietsub.uploader.browser
assert 'app' not in sys.modules
assert 'vietsub.uploader.adapters' not in sys.modules
assert not any(name == 'playwright' or name.startswith('playwright.') or name == 'patchright' or name.startswith('patchright.') for name in sys.modules)
print('lightweight')
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(result.stdout.strip(), "lightweight")

    def test_lazy_patchright_preferred_playwright_fallback_only_when_absent(self):
        factory = object()
        module = SimpleNamespace(sync_playwright=factory)
        with patch.object(importlib, "import_module", return_value=module) as importer:
            self.assertIs(browser.get_sync_playwright(), factory)
            importer.assert_called_once_with("patchright.sync_api")
        absent = ModuleNotFoundError("absent", name="patchright")
        with patch.object(importlib, "import_module", side_effect=[absent, module]) as importer:
            self.assertIs(browser.get_sync_playwright(), factory)
            self.assertEqual(importer.call_args_list[-1].args, ("playwright.sync_api",))
        broken = ModuleNotFoundError("broken dependency", name="greenlet")
        with patch.object(importlib, "import_module", side_effect=broken) as importer:
            with self.assertRaises(ModuleNotFoundError):
                browser.get_sync_playwright()
            self.assertEqual(importer.call_count, 1)


if __name__ == "__main__":
    unittest.main()
