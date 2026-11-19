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
from vietsub.sources.browser_lifecycle import BrowserLifecycle, HeadedBrowserRequired, MANUAL_LOGIN_SECONDS

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

    def test_manual_login_grace_is_bounded_and_status_polls_never_extend_it(self):
        with self.lifecycle.lease():
            self.lifecycle.grant_manual_login()
        self.assertEqual(FakeTimer.created[-1].seconds, MANUAL_LOGIN_SECONDS)
        for now in (60, 150, 599):
            self.now = now
            with self.lifecycle.lease(activity=False):
                pass
            FakeTimer.created[-1].callback()
            self.close.assert_not_called()
            self.assertEqual(FakeTimer.created[-1].seconds, MANUAL_LOGIN_SECONDS - now)
        self.now = MANUAL_LOGIN_SECONDS
        FakeTimer.created[-1].callback()
        self.close.assert_called_once_with(self.instance)
        self.assertIsNone(self.lifecycle.manual_login_until)

    def test_resuming_automation_clears_grace_and_restores_sixty_second_idle(self):
        self.lifecycle.grant_manual_login()
        self.now = 90
        with self.lifecycle.lease():
            self.lifecycle.clear_manual_login()
            self.now = 300
        self.assertIsNone(self.lifecycle.manual_login_until)
        self.assertEqual(FakeTimer.created[-1].seconds, 60)
        self.now = 360
        FakeTimer.created[-1].callback()
        self.close.assert_called_once_with(self.instance)

    def test_manual_grace_is_capped_and_does_not_transfer_to_a_successor(self):
        self.lifecycle.grant_manual_login(MANUAL_LOGIN_SECONDS * 100)
        self.assertEqual(FakeTimer.created[-1].seconds, MANUAL_LOGIN_SECONDS)
        self.lifecycle.remember({"pid": 456, "session": "successor"})
        self.assertIsNone(self.lifecycle.manual_login_until)
        self.assertEqual(FakeTimer.created[-1].seconds, 60)

    def test_shutdown_never_waits_for_a_lease_owned_by_another_thread(self):
        entered = threading.Event()
        release = threading.Event()
        returned = threading.Event()
        results = []

        def operation():
            with self.lifecycle.lease():
                entered.set()
                release.wait(2)

        def shutdown():
            results.append(self.lifecycle.shutdown())
            returned.set()

        worker = threading.Thread(target=operation)
        worker.start()
        self.assertTrue(entered.wait(1))
        closer = threading.Thread(target=shutdown)
        closer.start()
        try:
            self.assertTrue(returned.wait(0.5), "shutdown waited for the operation lock")
            self.assertEqual(results, [False])
            self.close.assert_not_called()
        finally:
            release.set()
            worker.join(2)
            closer.join(2)
        self.assertTrue(self.lifecycle.shutdown())
        self.close.assert_called_once_with(self.instance)

    def test_failed_shutdown_close_rearms_idle_cleanup(self):
        self.close.side_effect = RuntimeError("CDP unavailable")
        self.assertFalse(self.lifecycle.shutdown())
        self.assertEqual(FakeTimer.created[-1].seconds, 60)
        self.assertIs(self.lifecycle.instance, self.instance)

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
        app.browser_operation_state.operation = None
        self.verify_finished = app.browser_verification_finished
        verification = patch.object(app, "browser_verification_finished", return_value=True)
        verification.start()
        self.addCleanup(verification.stop)

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
            self.assertTrue(app.browser_lifecycle.instance["headless"])
            self.assertEqual(operation(), "captured")
        self.assertEqual(attempts, [True, False, True])
        self.assertEqual(start.call_args_list, [
            unittest.mock.call(background=False, headless=False, start_url="https://example.com/verify"),
            unittest.mock.call(headless=True),
        ])
        self.assertEqual(app.browser_lifecycle.active, 0)
        self.assertIsNone(app.browser_operation_state.operation)

    def test_verified_retry_restarts_only_after_playwright_scope_is_unwound(self):
        events = []
        state = {"headless": True}

        class PageScope:
            def __enter__(self):
                events.append("enter")

            def __exit__(self, *_args):
                events.append("exit")

        def ensure(**kwargs):
            self.assertEqual(events[-1], "exit")
            state["headless"] = kwargs["headless"]
            events.append("headless" if state["headless"] else "headed")

        @app.browser_automation
        def operation():
            with PageScope():
                if state["headless"]:
                    raise HeadedBrowserRequired("https://example.com/verify")
                return {"source_video": "captured.mp4"}

        with patch.object(app, "ensure_browser_running", side_effect=ensure):
            self.assertEqual(operation(), {"source_video": "captured.mp4"})
        self.assertEqual(events, ["enter", "exit", "headed", "enter", "exit", "headless"])

    def test_retry_failures_cancellation_and_empty_results_stay_headed(self):
        outcomes = [app.EdgeLoginRequired("login"), TimeoutError("timeout"),
                    app.WorkflowCancelled("cancelled"), ValueError("failed"), None, False]
        for outcome in outcomes:
            attempts = []

            @app.browser_automation
            def operation():
                attempts.append(True)
                if len(attempts) == 1:
                    raise HeadedBrowserRequired("https://example.com/verify")
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            with self.subTest(outcome=outcome), patch.object(app, "ensure_browser_running") as start:
                if isinstance(outcome, Exception):
                    with self.assertRaises(type(outcome)):
                        operation()
                else:
                    self.assertIs(operation(), outcome)
            start.assert_called_once_with(background=False, headless=False, start_url="https://example.com/verify")
            self.assertIsNone(app.browser_operation_state.headless)
            self.assertIsNone(app.browser_operation_state.operation)

    def test_failed_headed_retry_cleanup_cannot_minimize_the_verification_window(self):
        attempts = []
        context = Mock()
        page = Mock(url="https://example.com/verify")

        @app.browser_automation
        def operation():
            attempts.append(True)
            if len(attempts) == 1:
                raise HeadedBrowserRequired(page.url)
            # Capture callbacks do this from finally even after a timeout.
            self.assertTrue(app.set_browser_window_visibility(context, page, False))
            raise TimeoutError("verification unfinished")

        with patch.object(app, "ensure_browser_running"):
            with self.assertRaises(TimeoutError):
                operation()
        context.new_cdp_session.assert_not_called()

    def test_unfinished_verification_tab_survives_capture_cleanup_with_other_tabs(self):
        app.browser_lifecycle.instance = {"headless": False}
        page = Mock(url="https://example.com/verify")
        unrelated = Mock(url="https://unrelated.example.test/")
        context = SimpleNamespace(pages=[unrelated, page])
        with patch.object(app, "set_browser_window_visibility", return_value=True):
            app.require_visible_browser(context, page)
        browser.release_page(context, page)
        self.assertTrue(page._vietsub_manual_verification)
        page.close.assert_not_called()
        unrelated.close.assert_not_called()

    def test_success_during_pending_cancel_does_not_hide_the_visible_browser(self):
        attempts = []

        @app.browser_automation
        def operation():
            attempts.append(True)
            if len(attempts) == 1:
                raise HeadedBrowserRequired("https://example.com/verify")
            return "captured"

        with (patch.object(app, "ensure_browser_running") as start,
              patch.object(app, "check_cancel_requested"),
              patch.object(app, "cancel_requested", return_value=True)):
            self.assertEqual(operation(), "captured")
        self.assertEqual(start.call_count, 1)

    def test_failed_headless_restart_never_masks_successful_video(self):
        attempts = []

        @app.browser_automation
        def operation():
            attempts.append(True)
            if len(attempts) == 1:
                raise HeadedBrowserRequired("https://example.com/verify")
            return {"source_video": "captured.mp4"}

        with (patch.object(app, "ensure_browser_running", side_effect=[True, app.BrowserStartError("busy")]),
              patch.object(app, "log_to_app") as log):
            self.assertEqual(operation(), {"source_video": "captured.mp4"})
        self.assertIn("chưa trở lại headless", log.call_args.args[0])
        self.assertEqual(log.call_args.args[1], "error")

    def test_configured_or_explicit_headed_mode_is_not_overridden_after_retry(self):
        for configured, override, explicit in ((False, None, False), (True, False, False), (True, None, True)):
            attempts = []
            app.browser_operation_state.headless = override

            @app.browser_automation
            def operation():
                if explicit:
                    app.ensure_browser_running(background=False)
                attempts.append(True)
                if len(attempts) == 1:
                    raise HeadedBrowserRequired("https://example.com/verify")
                return "captured"

            instance = {"pid": 12, "profile": self.profile, "executable": CHROME,
                        "session": SESSION, "headless": False}
            with (self.subTest(configured=configured, override=override, explicit=explicit),
                  patch.object(app, "load_app_config", return_value={"edge_background": configured}),
                  patch.object(app, "check_browser_status", return_value=True),
                  patch.object(app, "owned_browser_instance", return_value=instance),
                  patch.object(app, "close_owned_browser") as close):
                self.assertEqual(operation(), "captured")
                close.assert_not_called()
            self.assertIs(app.browser_operation_state.headless, override)
            app.browser_operation_state.headless = None

    def test_nested_decorators_retry_only_after_outer_scope_is_unwound(self):
        events = []
        visible = []

        @app.browser_automation
        def inner():
            events.append("inner")
            if not visible:
                raise HeadedBrowserRequired("https://example.com/verify")
            return "captured"

        @app.browser_automation
        def outer():
            events.append("enter")
            try:
                result = inner()
                self.assertFalse(app.browser_operation_state.headless)
                return result
            finally:
                events.append("exit")

        def ensure(**kwargs):
            self.assertEqual(events[-1], "exit")
            visible.append(kwargs["headless"])

        with patch.object(app, "ensure_browser_running", side_effect=ensure):
            self.assertEqual(outer(), "captured")
        self.assertEqual(events, ["enter", "inner", "exit", "enter", "inner", "exit"])
        self.assertEqual(visible, [False, True])
        self.assertIsNone(app.browser_operation_state.operation)

    def test_automation_decorator_clears_manual_login_grace(self):
        app.browser_lifecycle.grant_manual_login()

        @app.browser_automation
        def operation():
            self.assertIsNone(app.browser_lifecycle.manual_login_until)
            return "captured"

        self.assertEqual(operation(), "captured")

    def test_truthy_result_with_unfinished_verification_remains_visible(self):
        attempts = []

        @app.browser_automation
        def operation():
            attempts.append(True)
            if len(attempts) == 1:
                raise HeadedBrowserRequired("https://example.com/verify")
            return "https://example.com/verify"  # Legacy URL helper timed out.

        with (patch.object(app, "browser_verification_finished", return_value=False),
              patch.object(app, "ensure_browser_running") as start):
            self.assertEqual(operation(), "https://example.com/verify")
        self.assertEqual(start.call_count, 1)

    def test_verification_boundary_rejects_captcha_login_and_unreadable_probe(self):
        app.browser_lifecycle.instance = {"headless": False, "session": SESSION}
        for probe, douyin_probe, login, expected in (
            ({}, {}, False, True),
            ({"captcha": True}, {}, False, False),
            ({}, {"login": True}, False, False),
            ({"title": "Verify you are human"}, {}, False, False),
            ({}, {}, True, False),
            (None, {}, False, False),
        ):
            page = Mock(url="https://gemini.google.com/notebook/phase1")
            page.evaluate.side_effect = [probe, douyin_probe]
            playwright = Mock()
            playwright.chromium.connect_over_cdp.return_value.contexts = [SimpleNamespace(pages=[page])]
            manager = Mock(__enter__=Mock(return_value=playwright), __exit__=Mock(return_value=False))
            with (self.subTest(probe=probe, douyin_probe=douyin_probe, login=login),
                  patch.object(app, "get_sync_playwright", return_value=lambda: manager),
                  patch.object(app, "edge_page_needs_login", return_value=login)):
                self.assertEqual(self.verify_finished(), expected)
            manager.__exit__.assert_called_once()

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

    def test_status_success_without_owner_snapshot_fails_closed(self):
        app.browser_lifecycle.instance = {"session": "stale"}
        with (patch.object(app, "check_browser_status", return_value=True),
              patch.object(app, "owned_browser_instance", return_value=None),
              patch.object(app, "close_owned_browser") as close,
              patch.object(app.subprocess, "Popen") as launch):
            with self.assertRaisesRegex(app.BrowserStartError, "xác minh"):
                app.ensure_browser_running()
        self.assertIsNone(app.browser_lifecycle.instance)
        close.assert_not_called()
        launch.assert_not_called()

    def test_new_launch_without_owner_snapshot_fails_closed(self):
        with (patch.object(app, "check_browser_status", side_effect=[False, True]),
              patch.object(app, "owned_browser_instance", return_value=None),
              patch.object(app, "read_debug_payload", return_value=None),
              patch.object(app, "resolve_browser_executable", return_value=CHROME),
              patch.object(app, "browser_profile_in_use", return_value=False),
              patch.object(app, "wait_for_debug_port", return_value=True),
              patch.object(app.subprocess, "Popen") as launch):
            with self.assertRaisesRegex(app.BrowserStartError, "xác minh"):
                app.ensure_browser_running()
        launch.assert_called_once()
        self.assertIsNone(app.browser_lifecycle.instance)

    def test_ensure_resumes_automation_and_clears_manual_grace(self):
        instance = {"pid": 12, "profile": self.profile, "executable": CHROME,
                    "session": SESSION, "headless": True}
        app.browser_lifecycle.remember(instance)
        app.browser_lifecycle.grant_manual_login()
        with (patch.object(app, "check_browser_status", return_value=True),
              patch.object(app, "owned_browser_instance", return_value=instance)):
            self.assertTrue(app.ensure_browser_running())
        self.assertIsNone(app.browser_lifecycle.manual_login_until)

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

    def test_existing_target_wins_over_an_unrelated_blank_without_closing_it(self):
        blank = Mock(url="about:blank")
        target = Mock(url="https://example.com/video")
        context = SimpleNamespace(pages=[blank, target], new_page=Mock())
        self.assertIs(browser.reuse_page(context, target.url), target)
        blank.close.assert_not_called()
        target.close.assert_not_called()
        context.new_page.assert_not_called()

    def test_existing_target_consumes_initial_marker_and_discards_only_owned_blank(self):
        initial = Mock(url="about:blank")
        personal_blank = Mock(url="about:blank")
        unrelated = Mock(url="https://other.example.com/")
        target = Mock(url="https://example.com/video")
        pages = [initial, personal_blank, unrelated, target]
        initial.close.side_effect = lambda: pages.remove(initial)
        context = SimpleNamespace(pages=pages, _vietsub_initial_page=initial, new_page=Mock())
        self.assertIs(browser.reuse_page(context, target.url), target)
        self.assertIsNone(context._vietsub_initial_page)
        self.assertNotIn(initial, context.pages)
        initial.close.assert_called_once()
        personal_blank.close.assert_not_called()
        unrelated.close.assert_not_called()
        self.assertIs(browser.reuse_page(context, target.url), target)

    def test_matching_target_never_closes_an_unrelated_nonblank_initial_tab(self):
        initial = Mock(url="https://other.example.com/")
        target = Mock(url="https://example.com/video")
        context = SimpleNamespace(pages=[initial, target], _vietsub_initial_page=initial, new_page=Mock())
        self.assertIs(browser.reuse_page(context, target.url), target)
        self.assertIsNone(context._vietsub_initial_page)
        initial.close.assert_not_called()

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


class ManualLoginTests(unittest.TestCase):
    def setUp(self):
        FakeTimer.created = []
        self.now = 0.0
        app.browser_lifecycle.forget()
        self.old_clock = app.browser_lifecycle.clock
        self.old_timers = app.browser_lifecycle.timer_factory
        app.browser_lifecycle.clock = lambda: self.now
        app.browser_lifecycle.timer_factory = FakeTimer

    def tearDown(self):
        app.browser_lifecycle.forget()
        app.browser_lifecycle.clock = self.old_clock
        app.browser_lifecycle.timer_factory = self.old_timers

    def test_explicit_notebook_and_source_login_grant_grace_and_consume_initial_tab(self):
        notebook = "https://gemini.google.com/notebook/phase1"
        source = app.SESSION_SITES["douyin"].home_url
        for opener, argument, target_url in ((app.show_edge_for_login, notebook, notebook),
                                             (app.open_source_in_browser, "douyin", source)):
            target = Mock(url=target_url)
            blank = Mock(url="about:blank")
            unrelated = Mock(url="https://unrelated.example.test/")
            pages = [blank, unrelated, target]
            blank.close.side_effect = lambda: pages.remove(blank)
            context = SimpleNamespace(pages=pages, _vietsub_initial_page=blank, new_page=Mock())
            manager = Mock(__enter__=Mock(return_value=Mock()), __exit__=Mock(return_value=False))
            with (self.subTest(opener=opener.__name__),
                  patch.object(app, "ensure_browser_running", return_value=True) as start,
                  patch.object(app, "get_sync_playwright", return_value=lambda: manager),
                  patch.object(app, "browser_context", return_value=context),
                  patch.object(app, "set_browser_window_visibility", return_value=True)):
                self.assertTrue(opener(argument))
            start.assert_called_once_with(background=False, start_url=target_url)
            self.assertEqual(app.browser_lifecycle.manual_login_until, MANUAL_LOGIN_SECONDS)
            self.assertIsNone(context._vietsub_initial_page)
            self.assertNotIn(blank, pages)
            blank.close.assert_called_once()
            target.goto.assert_not_called()
            unrelated.close.assert_not_called()
            app.browser_lifecycle.forget()

    def test_login_grace_survives_navigation_error_but_status_does_not_extend_it(self):
        page = Mock(url="about:blank")
        page.goto.side_effect = TimeoutError("login still loading")
        context = SimpleNamespace(pages=[page], new_page=Mock())
        manager = Mock(__enter__=Mock(return_value=Mock()), __exit__=Mock(return_value=False))
        with (patch.object(app, "ensure_browser_running", return_value=True),
              patch.object(app, "get_sync_playwright", return_value=lambda: manager),
              patch.object(app, "browser_context", return_value=context),
              patch.object(app, "set_browser_window_visibility", return_value=True)):
            self.assertTrue(app.open_source_in_browser("douyin"))
        self.assertEqual(app.browser_lifecycle.manual_login_until, MANUAL_LOGIN_SECONDS)
        self.now = 120
        with (patch.object(app, "read_debug_payload", return_value=PAYLOAD),
              patch.object(app, "browser_debug_owner_uses_profile", return_value=True)):
            self.assertTrue(app.check_browser_status(fresh=True))
        self.assertEqual(app.browser_lifecycle.manual_login_until, MANUAL_LOGIN_SECONDS)


class DesktopCleanupTests(unittest.TestCase):
    def setUp(self):
        app.browser_lifecycle.forget()
        app.shutdown_event.clear()
        app.cancel_event.clear()
        app.prefetch_cancel_event.clear()

    def tearDown(self):
        app.browser_lifecycle.forget()
        app.shutdown_event.clear()
        app.cancel_event.clear()
        app.prefetch_cancel_event.clear()

    def test_gui_shutdown_cancels_and_joins_before_owned_browser_cleanup(self):
        calls = []
        prefetch = Mock()
        worker = Mock()
        prefetch.join.side_effect = lambda timeout: calls.append(("prefetch", timeout))
        worker.join.side_effect = lambda timeout: calls.append(("worker", timeout))

        def close():
            self.assertTrue(app.shutdown_event.is_set())
            self.assertTrue(app.cancel_event.is_set())
            self.assertTrue(app.prefetch_cancel_event.is_set())
            calls.append(("browser", True))
            return True

        with (patch.object(app, "active_processes", {}),
              patch.object(app, "prefetch_thread", prefetch),
              patch.object(app, "queue_worker_thread", worker),
              patch.object(app, "shutdown_automation_browser", side_effect=close)):
            app.request_app_shutdown()
            app.request_app_shutdown()  # Idempotent; no second close.
        self.assertEqual(calls, [("prefetch", 5), ("worker", 15), ("browser", True)])

    def test_gui_browser_cleanup_wait_is_bounded_even_if_cdp_stalls(self):
        cleanup = Mock()
        cleanup.is_alive.return_value = True
        with (patch.object(app, "active_processes", {}),
              patch.object(app, "prefetch_thread", None),
              patch.object(app, "queue_worker_thread", None),
              patch.object(app, "Thread", return_value=cleanup) as thread,
              patch.object(app, "log_to_app") as log):
            app.request_app_shutdown()
        self.assertTrue(thread.call_args.kwargs["daemon"])
        cleanup.start.assert_called_once()
        cleanup.join.assert_called_once_with(timeout=2)
        self.assertIn("không tiếp tục chờ", log.call_args.args[0])

    def test_gui_cleanup_does_not_close_personal_debug_browser(self):
        profile = app.browser_profile_dir("chrome")
        app.browser_lifecycle.remember({"pid": 12, "profile": profile, "executable": CHROME, "session": SESSION})
        personal = {"pid": 12, "name": "chrome.exe", "path": CHROME,
                    "command_line": f'"{CHROME}" --remote-debugging-port=9222'}
        with (patch.object(app, "active_processes", {}),
              patch.object(app, "prefetch_thread", None),
              patch.object(app, "queue_worker_thread", None),
              patch.object(app, "get_debug_port_owner", return_value=personal),
              patch.object(app, "read_debug_payload", return_value=PAYLOAD),
              patch.object(app, "get_sync_playwright") as playwright,
              patch.object(app.subprocess, "run") as kill):
            app.request_app_shutdown()
        playwright.assert_not_called()
        kill.assert_not_called()
        self.assertIsNone(app.browser_lifecycle.instance)


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
                # Exercise the real decorated verification boundary using only a
                # local data page: no platform requests or captcha bypass.
                attempts = []

                @app.browser_automation
                def local_verified_operation():
                    app.ensure_browser_running(start_url=target)
                    with app.get_sync_playwright()() as playwright:
                        context = app.browser_context(playwright)
                        page = browser.reuse_page(context, target)
                        if page.url != target:
                            page.goto(target)
                        attempts.append(app.browser_lifecycle.instance["headless"])
                        if app.browser_lifecycle.instance["headless"]:
                            app.require_visible_browser(context, page)
                        return "local verification completed"

                self.assertEqual(local_verified_operation(), "local verification completed")
                self.assertEqual(attempts, [True, False])
                self.assertTrue(app.browser_lifecycle.instance["headless"])
                with app.browser_lifecycle.lease(), app.get_sync_playwright()() as playwright:
                    context = app.browser_context(playwright)
                    self.assertEqual(len(context.pages), 1)
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
