"""v1.8 browser lifecycle: leases, identity, headed recovery and session continuity."""

import os
import socket
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    from . import _isolation
except ImportError:
    import _isolation

import app
import automate
from vietsub.sources import browser, browser_capture
from vietsub.sources.base import SourceError
from vietsub.sources.browser_lifecycle import BrowserLifecycle, HeadedBrowserRequired

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SESSION = "ws://127.0.0.1:9222/devtools/browser/lifecycle"
PAYLOAD = {"Browser": "Chrome/142.0", "webSocketDebuggerUrl": SESSION}


class FakeTimer:
    created = []

    def __init__(self, seconds, callback):
        self.seconds = seconds
        self.callback = callback
        self.cancelled = False
        self.created.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        FakeTimer.created = []
        self.now = 0.0
        self.close = Mock(return_value=True)
        self.lifecycle = BrowserLifecycle(self.close, clock=lambda: self.now, timer_factory=FakeTimer)
        self.instance = {"pid": 123, "session": SESSION}
        self.lifecycle.remember(self.instance)

    def test_exactly_sixty_idle_seconds_close_the_owned_instance(self):
        self.assertEqual(FakeTimer.created[-1].seconds, 60)
        self.now = 59
        FakeTimer.created[-1].callback()
        self.close.assert_not_called()
        self.assertEqual(FakeTimer.created[-1].seconds, 1)
        self.now = 60
        FakeTimer.created[-1].callback()
        self.close.assert_called_once_with(self.instance)
        self.assertIsNone(self.lifecycle.instance)

    def test_long_operation_and_manual_challenge_do_not_count_as_idle(self):
        old_timer = FakeTimer.created[-1]
        with self.lifecycle.lease():
            self.now = 240
            old_timer.callback()  # An already queued timer cannot close the browser.
            self.assertFalse(self.lifecycle.shutdown())
            self.close.assert_not_called()
        self.assertEqual(FakeTimer.created[-1].seconds, 60)
        self.now = 299
        FakeTimer.created[-1].callback()
        self.close.assert_not_called()
        self.now = 300
        FakeTimer.created[-1].callback()
        self.close.assert_called_once()

    def test_status_polling_does_not_postpone_idle_shutdown(self):
        self.now = 45
        with self.lifecycle.lease(activity=False):
            pass
        self.assertEqual(FakeTimer.created[-1].seconds, 15)
        self.now = 60
        FakeTimer.created[-1].callback()
        self.close.assert_called_once()

    def test_exception_releases_the_lease_and_arms_cleanup(self):
        with self.assertRaisesRegex(ValueError, "cancelled"):
            with self.lifecycle.lease():
                raise ValueError("cancelled")
        self.assertEqual(self.lifecycle.active, 0)
        self.now = 60
        FakeTimer.created[-1].callback()
        self.close.assert_called_once()

    def test_stale_timer_cannot_close_a_new_browser(self):
        stale = FakeTimer.created[-1]
        self.lifecycle.remember({"pid": 456, "session": "new"})
        self.now = 90
        stale.callback()
        self.close.assert_not_called()

    def test_operations_are_serialized_and_cannot_restart_each_others_browser(self):
        entered = threading.Event()
        finished = threading.Event()

        def operation():
            entered.set()
            with self.lifecycle.lease():
                finished.set()

        with self.lifecycle.lease():
            thread = threading.Thread(target=operation)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(finished.is_set())
        thread.join(2)
        self.assertTrue(finished.is_set())


class BrowserModeTests(unittest.TestCase):
    def setUp(self):
        self.profile = app.browser_profile_dir("chrome")
        app.browser_lifecycle.forget()
        app.browser_session_cookies.clear()
        app.browser_operation_state.headless = None

    def tearDown(self):
        app.browser_lifecycle.forget()
        app.browser_session_cookies.clear()
        app.browser_operation_state.headless = None

    def test_launch_is_headless_by_default_and_explicit_headed_omits_flag(self):
        command = browser.launch_command(CHROME, self.profile)
        self.assertIn("--headless=new", command)
        self.assertIn("--disable-blink-features=AutomationControlled", command)
        self.assertIn(f"--user-data-dir={self.profile}", command)
        self.assertNotIn("--headless=new", browser.launch_command(CHROME, self.profile, headless=False))
        self.assertTrue(browser.is_valid_debug_payload({**PAYLOAD, "Browser": "HeadlessChrome/142.0"}, "chrome"))

    def test_running_headed_profile_restarts_headless_for_the_next_automation(self):
        old = {"pid": 12, "profile": self.profile, "executable": CHROME, "session": SESSION, "headless": False}
        new = {**old, "pid": 13, "session": SESSION + "-new", "headless": True}
        with (
            patch.object(app, "check_browser_status", return_value=True),
            patch.object(app, "owned_browser_instance", side_effect=[old, new]),
            patch.object(app, "close_owned_browser", return_value=True) as close,
            patch.object(app, "read_debug_payload", return_value=None),
            patch.object(app, "resolve_browser_executable", return_value=CHROME),
            patch.object(app, "browser_profile_in_use", return_value=False),
            patch.object(app, "wait_for_debug_port", return_value=True),
            patch.object(app.subprocess, "Popen") as launch,
        ):
            self.assertTrue(app.ensure_browser_running())
        close.assert_called_once_with(old)
        self.assertIn("--headless=new", launch.call_args.args[0])
        self.assertIn(f"--user-data-dir={self.profile}", launch.call_args.args[0])

    def test_challenge_retry_is_headed_and_the_next_operation_is_headless(self):
        modes = []
        attempts = []

        def ensure(**kwargs):
            override = getattr(app.browser_operation_state, "headless", None)
            headless = kwargs.get("headless", override if override is not None else True)
            modes.append(headless)
            app.browser_lifecycle.instance = {"headless": headless}

        @app.browser_automation
        def operation():
            ensure()
            attempts.append(app.browser_lifecycle.instance["headless"])
            if len(attempts) == 1:
                app.require_visible_browser(Mock(), SimpleNamespace(url="https://example.com/verify"))
            return "captured"

        with patch.object(app, "ensure_browser_running", side_effect=ensure) as start:
            self.assertEqual(operation(), "captured")
            self.assertEqual(operation(), "captured")
        self.assertEqual(attempts, [True, False, True])
        start.assert_called_once_with(background=False, headless=False, start_url="https://example.com/verify")
        self.assertEqual(app.browser_lifecycle.active, 0)

    def test_cookie_restore_precedes_page_use_and_reuses_redirected_initial_tab(self):
        cookies = [{"name": "sessionid", "value": "session", "domain": ".example.com", "path": "/"}]
        page = Mock(url="https://example.com/verify")
        context = Mock(pages=[page])
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value.contexts = [context]
        app.browser_lifecycle.instance = {"profile": self.profile, "initial_page_pending": True}
        app.browser_session_cookies[self.profile] = cookies
        self.assertIs(app.browser_context(playwright), context)
        context.add_cookies.assert_called_once_with(cookies)
        page.reload.assert_called_once()
        self.assertIs(browser.reuse_page(context, "https://example.com/video"), page)
        context.new_page.assert_not_called()
        self.assertNotIn(self.profile, app.browser_session_cookies)

    def test_idle_close_rechecks_identity_and_never_touches_a_successor(self):
        instance = {"pid": 12, "profile": self.profile, "executable": CHROME, "session": SESSION}
        owner = {"pid": 12, "name": "chrome.exe", "path": CHROME,
                 "command_line": f'"{CHROME}" --user-data-dir="{self.profile}"'}
        for replacement in (None, {**owner, "pid": 99}, {**owner, "path": r"C:\Other\chrome.exe"},
                            {**owner, "command_line": f'"{CHROME}" --remote-debugging-port=9222'}):
            with (
                self.subTest(owner=replacement),
                patch.object(app, "get_debug_port_owner", return_value=replacement),
                patch.object(app, "read_debug_payload", return_value=PAYLOAD),
                patch.object(app, "get_sync_playwright") as playwright,
                patch.object(app.subprocess, "run") as kill,
            ):
                self.assertTrue(app.close_owned_browser(instance))
                playwright.assert_not_called()
                kill.assert_not_called()
        with (
            patch.object(app, "get_debug_port_owner", return_value=owner),
            patch.object(app, "read_debug_payload", return_value={**PAYLOAD, "webSocketDebuggerUrl": SESSION + "-other"}),
            patch.object(app, "get_sync_playwright") as playwright,
        ):
            self.assertTrue(app.close_owned_browser(instance))
            playwright.assert_not_called()

    def test_idle_snapshot_feeds_the_next_download_without_starting_chrome(self):
        app.browser_session_cookies[self.profile] = [
            {"name": "SESSDATA", "value": "login", "domain": ".bilibili.com", "path": "/"},
            {"name": "google", "value": "private", "domain": ".google.com", "path": "/"},
            {"name": "old", "value": "expired", "domain": ".bilibili.com", "path": "/", "expires": 1},
        ]
        with (
            patch.object(app, "get_cookies_file", return_value=""),
            patch.object(app, "check_browser_status", return_value=False),
            patch.object(app, "ensure_browser_running") as start,
        ):
            path, cleanup = app.prepare_ytdlp_cookie_file("bilibili")
            try:
                jar = app.cookie_utils.load_cookie_file(path)
                self.assertEqual({cookie.name for cookie in jar}, {"SESSDATA"})
                youtube, youtube_cleanup = app.prepare_ytdlp_cookie_file("youtube")
                self.assertIsNone(youtube)
                youtube_cleanup()
            finally:
                cleanup()
        start.assert_not_called()

    def test_owned_close_saves_session_cookies_and_uses_graceful_browser_close(self):
        instance = {"pid": 12, "profile": self.profile, "executable": CHROME, "session": SESSION}
        owner = {"pid": 12, "name": "chrome.exe", "path": CHROME,
                 "command_line": f'"{CHROME}" --user-data-dir="{self.profile}"'}
        context = Mock()
        cookies = [{"name": "session", "value": "secret"}]
        context.cookies.return_value = cookies
        remote = Mock(contexts=[context])
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value = remote
        manager = Mock(__enter__=Mock(return_value=playwright), __exit__=Mock(return_value=False))
        with (
            patch.object(app, "get_debug_port_owner", return_value=owner),
            patch.object(app, "read_debug_payload", side_effect=[PAYLOAD, None]),
            patch.object(app, "get_sync_playwright", return_value=lambda: manager),
            patch.object(app, "browser_profile_in_use", return_value=False),
            patch.object(app.subprocess, "run") as kill,
        ):
            self.assertTrue(app.close_owned_browser(instance))
        remote.new_browser_cdp_session.return_value.send.assert_called_once_with("Browser.close")
        self.assertEqual(app.browser_session_cookies[self.profile], cookies)
        kill.assert_not_called()


class TabAndDetectionTests(unittest.TestCase):
    def test_the_first_blank_tab_is_reused_and_last_tab_retains_session(self):
        page = Mock(url="about:blank")
        context = SimpleNamespace(pages=[page], new_page=Mock())
        self.assertIs(browser.reuse_page(context, "https://example.com/video"), page)
        context.new_page.assert_not_called()
        browser.release_page(context, page)
        page.close.assert_not_called()
        context.pages.append(Mock(url="https://example.com/other"))
        browser.release_page(context, page)
        page.close.assert_called_once()

    def test_challenge_detection_requires_visible_signal_or_challenge_title(self):
        for probe in ({"captcha": True}, {"login": True}, {"title": "安全验证"}, {"title": "Verify you are human"}):
            self.assertTrue(browser_capture.is_attention_probe(probe))
        for probe in (None, [], {}, {"captcha": False, "title": "My video"}):
            self.assertFalse(browser_capture.is_attention_probe(probe))

    def test_captcha_is_detected_before_load_timeout_and_prefetch_never_opens_ui(self):
        from tests.test_downloads import FakeClock, FakeContext, FakePage
        clock = FakeClock()
        page = FakePage(clock)
        started = clock()
        attention = Mock(side_effect=HeadedBrowserRequired("https://example.com/video"))
        with patch.object(browser_capture, "page_needs_attention", return_value=True):
            with self.assertRaises(HeadedBrowserRequired):
                browser_capture.capture_media_from_page(FakeContext(page), "https://example.com/video",
                                                       clock=clock, on_attention=attention)
            self.assertLess(clock() - started, 30)
            with self.assertRaises(SourceError):
                browser_capture.capture_media_from_page(FakeContext(page), "https://example.com/video",
                                                       clock=clock, allow_attention_wait=False)

    def test_missing_video_alone_does_not_open_a_headed_window(self):
        from tests.test_downloads import FakeClock, FakeContext, FakePage
        clock = FakeClock()
        page = FakePage(clock)
        attention = Mock()
        with self.assertRaises(SourceError):
            browser_capture.capture_media_from_page(FakeContext(page), "https://example.com/video", clock=clock,
                load_timeout=1, on_attention=attention, require_attention_signal=True)
        attention.assert_not_called()


class CliCleanupTests(unittest.TestCase):
    def test_failure_closes_owned_browser_before_releasing_instance_lock(self):
        calls = []
        with (
            patch.object(sys, "argv", ["automate.py", "--url", "https://example.com/video"]),
            patch.object(automate, "configure_console_encoding"),
            patch.object(automate, "acquire_instance_lock", return_value=True),
            patch.object(automate, "run_single_workflow", side_effect=RuntimeError("failed")),
            patch.object(automate, "shutdown_automation_browser", side_effect=lambda: calls.append("close")),
            patch.object(automate, "release_instance_lock", side_effect=lambda: calls.append("unlock")),
            patch.object(sys, "stderr"),
        ):
            self.assertEqual(automate.main(), 1)
        self.assertEqual(calls, ["close", "unlock"])


@unittest.skipUnless(os.environ.get("VIETSUB_BROWSER_SMOKE") == "1", "opt-in real Chrome smoke test")
class RealChromeLifecycleTests(unittest.TestCase):
    def test_real_chrome_mode_switch_cookies_tab_reuse_and_idle_close(self):
        if not app.resolve_chrome_executable():
            self.skipTest("Google Chrome is not installed")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        target = "data:text/html,<title>Browser lifecycle check</title><p>Local lifecycle test</p>"
        with patch.object(app, "BROWSER_DEBUG_PORT", port):
            app.browser_lifecycle.forget()
            try:
                for headless in (True, False, True):
                    app.ensure_browser_running(headless=headless, background=headless, start_url=target)
                    self.assertEqual(app.browser_lifecycle.instance["headless"], headless)
                    with app.browser_lifecycle.lease(), app.get_sync_playwright()() as playwright:
                        context = app.browser_context(playwright)
                        page = browser.reuse_page(context, target)
                        self.assertEqual(len(context.pages), 1)
                        self.assertIs(page, context.pages[0])
                        self.assertNotEqual(page.url, "about:blank")
                        if not context.cookies(["https://session.example.test/"]):
                            self.assertTrue(headless)
                            self.assertNotIn("seeded", app.browser_session_cookies)
                            context.add_cookies([{"name": "sessionid", "value": "lifecycle-test",
                                                  "domain": "session.example.test", "path": "/"}])
                            app.browser_session_cookies["seeded"] = True
                        self.assertEqual(context.cookies(["https://session.example.test/"])[0]["value"], "lifecycle-test")
                # Exercise the production idle callback without waiting a full minute.
                app.browser_lifecycle.last_activity -= 61
                app.browser_lifecycle._expire(app.browser_lifecycle._generation)
                self.assertIsNone(app.browser_lifecycle.instance)
                self.assertIsNone(app.read_debug_payload())
            finally:
                app.shutdown_automation_browser()
                app.browser_session_cookies.clear()


if __name__ == "__main__":
    unittest.main()
