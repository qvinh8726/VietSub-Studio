"""The automation browser (v1.6.1): Google Chrome by default, its own profile, lazy start."""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from . import _isolation
except ImportError:
    import _isolation

import app
from vietsub.sources import browser

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_PAYLOAD = {
    "Browser": "Chrome/157.0.8103.79",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/0b1d2c3e",
}


class ChromeDiscoveryTests(unittest.TestCase):
    def test_the_app_paths_registry_entry_wins(self):
        found = browser.find_chrome(
            app_paths=lambda name: ['"C:\\Apps\\Chrome\\chrome.exe"'] if name == "chrome.exe" else [],
            environ={"ProgramFiles": r"C:\Program Files"},
            path_lookup=lambda name: "",
            isfile=lambda path: True,
        )
        self.assertEqual(found, r"C:\Apps\Chrome\chrome.exe")

    def test_program_files_then_x86_then_local_appdata_then_path(self):
        environ = {
            "ProgramFiles": r"C:\Program Files",
            "ProgramFiles(x86)": r"C:\Program Files (x86)",
            "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
        }
        expected = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Users\me\AppData\Local\Google\Chrome\Application\chrome.exe",
        ]
        for index, path in enumerate(expected):
            with self.subTest(path=path):
                existing = set(expected[index:])
                found = browser.find_chrome(
                    app_paths=lambda name: [], environ=environ, path_lookup=lambda name: "",
                    isfile=lambda candidate: candidate in existing,
                )
                self.assertEqual(found, path)
        on_path = r"D:\Portable\chrome.exe"
        found = browser.find_chrome(
            app_paths=lambda name: [], environ=environ, path_lookup=lambda name: on_path,
            isfile=lambda candidate: candidate == on_path,
        )
        self.assertEqual(found, on_path)

    def test_relative_or_wrong_registry_values_are_ignored(self):
        found = browser.find_chrome(
            app_paths=lambda name: ["chrome.exe", r"C:\Tools\notepad.exe"],
            environ={}, path_lookup=lambda name: "", isfile=lambda path: True,
        )
        self.assertEqual(found, "")

    def test_a_custom_browser_must_be_an_existing_absolute_exe(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            exe = Path(temporary_dir) / "brave.exe"
            exe.write_bytes(b"MZ")
            text = Path(temporary_dir) / "brave.txt"
            text.write_bytes(b"MZ")
            self.assertEqual(browser.validate_custom_executable(str(exe)), str(exe))
            for value in ("brave.exe", str(text), str(Path(temporary_dir) / "missing.exe"), "", None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    browser.validate_custom_executable(value)


class LaunchCommandTests(unittest.TestCase):
    def test_the_browser_always_gets_its_own_profile_and_debug_flags(self):
        profile = os.path.join(app.CONFIG_DIR, "chrome_debug_profile")
        command = browser.launch_command(CHROME, profile, port=9222, background=True)
        self.assertEqual(command[0], CHROME)
        for flag in (
            "--remote-debugging-port=9222",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--start-minimized",
        ):
            self.assertIn(flag, command)
        # One blank start tab: callers open their own pages, so a launch never adds a second tab.
        self.assertEqual(command[-1], "about:blank")
        self.assertNotIn("--start-minimized", browser.launch_command(CHROME, profile, background=False))

    def test_each_browser_kind_has_its_own_profile_folder_in_the_config_dir(self):
        self.assertEqual(app.browser_profile_dir("chrome"), os.path.join(app.CONFIG_DIR, "chrome_debug_profile"))
        self.assertEqual(app.browser_profile_dir("edge"), os.path.join(app.CONFIG_DIR, "edge_debug_profile"))
        self.assertNotEqual(app.browser_profile_dir("chrome"), app.USER_DATA_DIR_DEBUG)
        self.assertTrue(app.path_is_within(app.browser_profile_dir("chrome"), _isolation.SANDBOX_DIR))


REAL_SLEEP = __import__("time").sleep


class FakeDebugPort:
    """A browser that answers on the debug port a few polls after it is launched."""

    def __init__(self, polls_until_ready=2, launch_delay=0.0):
        self.polls_until_ready = polls_until_ready
        self.launch_delay = launch_delay
        self.commands = []
        self.polls = 0

    def popen(self, command, **_kwargs):
        self.commands.append(list(command))
        return Mock()

    def payload(self):
        if not self.commands:
            return None
        self.polls += 1
        if self.launch_delay:
            REAL_SLEEP(self.launch_delay)
        return dict(CHROME_PAYLOAD) if self.polls > self.polls_until_ready else None

    def ours(self, fresh=False):
        return bool(self.commands) and self.polls > self.polls_until_ready

    def instance(self, config=None):
        if not self.ours():
            return None
        command = self.commands[-1]
        return {"pid": 42, "profile": app.browser_profile_dir("chrome"), "executable": CHROME,
                "session": CHROME_PAYLOAD["webSocketDebuggerUrl"],
                "headless": any(argument.startswith("--headless") for argument in command)}


class EnsureBrowserTests(unittest.TestCase):
    def setUp(self):
        app.browser_lifecycle.forget()

    def tearDown(self):
        app.browser_lifecycle.forget()

    def patches(self, port, sleep=None):
        return (
            patch.object(app, "resolve_browser_executable", return_value=CHROME),
            patch.object(app, "read_debug_payload", side_effect=port.payload),
            patch.object(app, "check_browser_status", side_effect=port.ours),
            patch.object(app, "owned_browser_instance", side_effect=port.instance),
            patch.object(app, "browser_profile_in_use", return_value=False),
            patch.object(app, "minimize_browser_window"),
            patch.object(app.subprocess, "Popen", side_effect=port.popen),
            patch.object(app.time, "sleep", sleep or Mock()),
        )

    def run_with(self, port, sleep=None, **kwargs):
        patchers = self.patches(port, sleep)
        for patcher in patchers:
            patcher.start()
        try:
            return app.ensure_browser_running(**kwargs)
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

    def test_chrome_is_started_with_its_own_profile_and_polled_every_300_ms(self):
        port = FakeDebugPort(polls_until_ready=3)
        sleep = Mock()
        self.assertTrue(self.run_with(port, sleep, background=False))
        self.assertEqual(len(port.commands), 1)
        command = port.commands[0]
        self.assertEqual(command[0], CHROME)
        self.assertIn(f"--user-data-dir={app.browser_profile_dir('chrome')}", command)
        self.assertIn("--remote-debugging-port=9222", command)
        self.assertTrue(sleep.call_args_list)
        self.assertTrue(all(call.args == (0.3,) for call in sleep.call_args_list))

    def test_two_simultaneous_requests_launch_the_browser_once(self):
        port = FakeDebugPort(polls_until_ready=3, launch_delay=0.05)
        patchers = self.patches(port)
        for patcher in patchers:
            patcher.start()
        results = []
        try:
            threads = [
                threading.Thread(target=lambda: results.append(app.ensure_browser_running(background=False)))
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        finally:
            for patcher in reversed(patchers):
                patcher.stop()
        self.assertEqual(results, [True, True])
        self.assertEqual(len(port.commands), 1)

    def test_a_cold_start_waits_at_most_15_seconds(self):
        port = FakeDebugPort(polls_until_ready=10**6)
        sleep = Mock()
        with self.assertRaises(app.BrowserStartError) as raised:
            self.run_with(port, sleep, background=False)
        message = str(raised.exception)
        self.assertIn("9222", message)
        self.assertIn("15 giây", message)
        self.assertAlmostEqual(sleep.call_count * 0.3, 15, delta=0.5)

    def test_slow_answers_never_stretch_the_wait_past_its_limit(self):
        clock = {"now": 0.0}

        def slow_payload():
            clock["now"] += 1.5  # A refused connection to 127.0.0.1 takes this long on Windows.
            return None

        with (
            patch.object(app, "read_debug_payload", side_effect=slow_payload),
            patch.object(app.time, "monotonic", side_effect=lambda: clock["now"]),
            patch.object(app.time, "sleep", side_effect=lambda seconds: clock.update(now=clock["now"] + seconds)),
        ):
            self.assertFalse(app.wait_for_debug_port(lambda payload: payload is not None, 50))
        self.assertLessEqual(clock["now"], browser.COLD_START_SECONDS + 1.5)

    def test_nothing_listening_on_the_port_is_answered_without_a_request(self):
        with (
            patch.object(app, "debug_port_listening", return_value=False),
            patch.object(app.LOCAL_URL_OPENER, "open", side_effect=AssertionError("no request")),
        ):
            self.assertIsNone(app.read_debug_payload())
            self.assertFalse(app.check_browser_status())

    @unittest.skipUnless(os.name == "nt", "reads the Windows TCP table")
    def test_the_tcp_table_sees_a_local_listener(self):
        import socket

        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertEqual(app.native_listening_pid(port), os.getpid())
        self.assertIsNone(app.native_listening_pid(port))

    def test_a_running_browser_with_our_profile_is_reused(self):
        port = FakeDebugPort(polls_until_ready=-1)
        port.commands.append(["already running"])
        self.assertTrue(self.run_with(port, background=False))
        self.assertEqual(port.commands, [["already running"]])

    def test_a_foreign_program_on_the_port_is_reported_and_never_replaced(self):
        owner = {"pid": 42, "name": "python.exe", "path": r"C:\Python\python.exe",
                 "command_line": "python -m http.server 9222"}
        with (
            patch.object(app, "check_browser_status", return_value=False),
            patch.object(app, "read_debug_payload", return_value=dict(CHROME_PAYLOAD)),
            patch.object(app, "get_debug_port_owner", return_value=owner),
            patch.object(app, "resolve_browser_executable", return_value=CHROME),
            patch.object(app.subprocess, "Popen") as popen,
        ):
            with self.assertRaises(app.BrowserStartError) as raised:
                app.ensure_browser_running(background=False)
        self.assertIn("9222", str(raised.exception))
        self.assertIn("python.exe", str(raised.exception))
        popen.assert_not_called()

    def test_an_old_debug_browser_of_this_app_is_closed_before_chrome_starts(self):
        old = {"pid": 7, "name": "msedge.exe", "path": r"C:\Edge\msedge.exe",
               "command_line": f'"msedge.exe" --remote-debugging-port=9222 --user-data-dir="{app.USER_DATA_DIR_DEBUG}"'}
        port = FakeDebugPort(polls_until_ready=1)
        state = {"closed": False}

        def payload():
            if not state["closed"]:
                return {"Browser": "Edg/142.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/old"}
            return port.payload()

        with (
            patch.object(app, "resolve_browser_executable", return_value=CHROME),
            patch.object(app, "read_debug_payload", side_effect=payload),
            patch.object(app, "check_browser_status", side_effect=port.ours),
            patch.object(app, "owned_browser_instance", side_effect=port.instance),
            patch.object(app, "get_debug_port_owner", return_value=old),
            patch.object(app, "close_debug_browser", side_effect=lambda owner: state.update(closed=True)) as close,
            patch.object(app, "browser_profile_in_use", return_value=False),
            patch.object(app.subprocess, "Popen", side_effect=port.popen),
            patch.object(app.time, "sleep"),
        ):
            self.assertTrue(app.ensure_browser_running(background=False))
        close.assert_called_once_with(old)
        self.assertEqual(len(port.commands), 1)

    def test_a_profile_that_is_open_without_the_debug_port_is_not_launched_again(self):
        with (
            patch.object(app, "check_browser_status", return_value=False),
            patch.object(app, "read_debug_payload", return_value=None),
            patch.object(app, "resolve_browser_executable", return_value=CHROME),
            patch.object(app, "browser_profile_in_use", return_value=True),
            patch.object(app.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(app.BrowserStartError, "đóng"):
                app.ensure_browser_running(background=False)
        popen.assert_not_called()


class OwnerCheckTests(unittest.TestCase):
    def test_the_port_owner_must_be_the_selected_exe_with_its_own_profile(self):
        profile = app.browser_profile_dir("chrome")
        good = {"pid": 9, "name": "chrome.exe", "path": CHROME,
                "command_line": f'"{CHROME}" --user-data-dir="{profile}" --remote-debugging-port=9222'}
        self.assertTrue(app.browser_owner_matches(good, CHROME, profile))
        quoted = {**good, "command_line": f'"{CHROME}" "--user-data-dir={profile}"'}
        self.assertTrue(app.browser_owner_matches(quoted, CHROME, profile))
        rejected = {
            "other profile": {**good, "command_line": f'"{CHROME}" --user-data-dir="{profile} Backup"'},
            "personal profile": {**good, "command_line": f'"{CHROME}" --remote-debugging-port=9222'},
            "two profiles": {**good, "command_line": good["command_line"] + f' "--user-data-dir={profile}"'},
            "planted exe": {**good, "path": r"C:\Temp\chrome.exe"},
            "other program": {**good, "name": "python.exe"},
        }
        for label, owner in rejected.items():
            with self.subTest(label):
                self.assertFalse(app.browser_owner_matches(owner, CHROME, profile))
        self.assertFalse(app.browser_owner_matches(None, CHROME, profile))
        self.assertFalse(app.browser_owner_matches(good, "", profile))

    def test_the_owner_lookup_falls_back_to_powershell(self):
        metadata = {"ProcessId": 42, "Name": "chrome.exe", "ExecutablePath": CHROME, "CommandLine": "chrome.exe x"}
        with (
            patch.object(app, "native_debug_port_owner", side_effect=OSError("blocked")),
            patch.object(app.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(metadata))) as run,
        ):
            owner = app.get_debug_port_owner()
        self.assertEqual(owner, {"pid": 42, "name": "chrome.exe", "path": CHROME, "command_line": "chrome.exe x"})
        self.assertEqual(run.call_args.args[0][0], app.POWERSHELL_EXE)
        self.assertTrue(os.path.isabs(app.POWERSHELL_EXE))

    def test_the_debug_payload_must_come_from_the_selected_browser(self):
        edge = {**CHROME_PAYLOAD, "Browser": "Edg/142.0.0.0"}
        self.assertTrue(browser.is_valid_debug_payload(CHROME_PAYLOAD, "chrome"))
        self.assertFalse(browser.is_valid_debug_payload(edge, "chrome"))
        self.assertTrue(browser.is_valid_debug_payload(edge, "edge"))
        self.assertFalse(browser.is_valid_debug_payload(CHROME_PAYLOAD, "edge"))
        self.assertTrue(browser.is_valid_debug_payload(CHROME_PAYLOAD, "custom"))
        for url in (
            "ws://localhost:9222/devtools/browser/x", "ws://127.0.0.1:9333/devtools/browser/x",
            "wss://127.0.0.1:9222/devtools/browser/x", "ws://127.0.0.1:9222/devtools/page/x",
        ):
            with self.subTest(url=url):
                self.assertFalse(browser.is_valid_debug_payload({**CHROME_PAYLOAD, "webSocketDebuggerUrl": url}, "chrome"))


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        self.config_patch = patch.object(app, "CONFIG_PATH", str(self.root / "app_config.json"))
        self.config_patch.start()
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config["TESTING"] = False
        self.config_patch.stop()
        self.temporary_dir.cleanup()


class LazyStartTests(ApiTestCase):
    def test_opening_the_app_with_an_api_provider_never_starts_a_browser(self):
        self.client.post("/api/settings", json={
            "translation_provider": "deepseek",
            "translation_api_base_url": "https://api.deepseek.com",
            "translation_api_model": "deepseek-v4-pro",
        })
        with (
            patch.object(app, "ensure_browser_running", side_effect=AssertionError("browser must not start")),
            patch.object(app.subprocess, "Popen", side_effect=AssertionError("browser must not start")),
            patch.object(app, "read_debug_payload", return_value=None),
        ):
            for path in ("/api/settings", "/api/health", "/api/sources/sessions", "/api/check-edge",
                         "/api/queue", "/api/progress", "/api/history"):
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
            sessions = self.client.get("/api/sources/sessions").get_json()
        self.assertFalse(sessions["browser_connected"])
        self.assertEqual(sessions["browser"], "Chrome")


class BrowserSettingsTests(ApiTestCase):
    def test_chrome_is_the_default_and_edge_or_a_custom_exe_can_be_chosen(self):
        data = self.client.get("/api/settings").get_json()
        self.assertEqual((data["browser_kind"], data["browser_label"], data["browser_short_label"]),
                         ("chrome", "Google Chrome", "Chrome"))
        self.assertEqual(data["browser_profile_dir"], app.browser_profile_dir("chrome"))
        self.assertEqual(self.client.post("/api/settings", json={"browser_kind": "edge"}).status_code, 200)
        data = self.client.get("/api/settings").get_json()
        self.assertEqual((data["browser_kind"], data["browser_short_label"]), ("edge", "Edge"))

        exe = self.root / "chromium.exe"
        exe.write_bytes(b"MZ")
        response = self.client.post("/api/settings", json={"browser_kind": "custom", "browser_path": str(exe)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app.resolve_browser_executable(), str(exe))
        for invalid in (
            {"browser_kind": "firefox"},
            {"browser_kind": "custom", "browser_path": "chromium.exe"},
            {"browser_kind": "custom", "browser_path": str(self.root / "missing.exe")},
            {"browser_path": 5},
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/api/settings", json=invalid).status_code, 400)


if __name__ == "__main__":
    unittest.main()
