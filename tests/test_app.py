import io
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import ANY, Mock, patch

# The sandbox must be in place before app.py computes its module-level paths.
try:
    from . import _isolation  # python -m unittest tests.test_app
except ImportError:
    import _isolation  # python -m unittest discover -s tests

import app
import automate

REPO_ROOT = Path(__file__).resolve().parent.parent


SOURCE_SRT = """1
00:00:01,000 --> 00:00:02,000
Hello

2
00:00:03,000 --> 00:00:04,000
World
"""

TRANSLATED_SRT = """1
00:00:01,000 --> 00:00:02,000
Xin chao

2
00:00:03,000 --> 00:00:04,000
The gioi
"""


class ValidationTests(unittest.TestCase):
    class FakeElement:
        def __init__(self, *, visible=True, enabled=True):
            self.visible = visible
            self.enabled = enabled

        def is_visible(self):
            return self.visible

        def is_enabled(self):
            return self.enabled

    class FakeLocator:
        def __init__(self, elements=None):
            self.elements = list(elements or [])

        def count(self):
            return len(self.elements)

        def nth(self, index):
            return self.elements[index]

    class FakePage:
        def __init__(self, url, selectors=None):
            self.url = url
            self.selectors = selectors or {}

        def locator(self, selector):
            return ValidationTests.FakeLocator(self.selectors.get(selector))

    def test_project_names_are_safe_and_keep_a_shared_base(self):
        self.assertEqual(app.sanitize_project_name("  CON.  "), "Video CON")
        self.assertEqual(app.sanitize_project_name("CON.txt"), "Video CON.txt")
        self.assertEqual(app.sanitize_project_name('Demo: 01 / ban?'), "Demo 01 ban")
        self.assertEqual(app.sanitize_project_name("视频 标题"), "视频 标题")
        self.assertEqual(app.sanitize_project_name("", fallback="Tên Douyin"), "Tên Douyin")
        with self.assertRaises(ValueError):
            app.validate_output_dir("relative-folder")

    def test_default_output_directory_is_independent_from_douzy(self):
        with patch.object(app, "get_documents_directory", return_value=r"C:\Users\Test\Documents"):
            self.assertEqual(
                app.get_default_output_directory(),
                os.path.abspath(r"C:\Users\Test\Documents\VietSub Studio"),
            )

    def test_native_window_watchdog_marks_failure_and_destroys_window(self):
        window = Mock()
        ready = app.Event()
        failed = app.Event()
        app.native_window_watchdog(window, ready, failed, timeout=0)
        self.assertTrue(failed.is_set())
        window.destroy.assert_called_once_with()

    def test_desktop_folder_picker_returns_the_selected_directory(self):
        class FakeWebview:
            class FileDialog:
                FOLDER = 20

        with tempfile.TemporaryDirectory() as temporary_dir:
            api = app.DesktopApi(FakeWebview)
            api._window = Mock()
            api._window.create_file_dialog.return_value = (temporary_dir,)

            selected = api.select_output_directory(temporary_dir)

            self.assertEqual(selected, os.path.abspath(temporary_dir))
            api._window.create_file_dialog.assert_called_once_with(
                20,
                directory=os.path.abspath(temporary_dir),
                allow_multiple=False,
            )
            self.assertFalse(hasattr(api, "webview"))
            self.assertFalse(hasattr(api, "window"))

    def test_browser_debug_port_is_stable_across_app_sessions(self):
        self.assertEqual(app.get_browser_debug_port(), 9222)
        self.assertEqual(app.browser_debug_url("/json/version"), "http://127.0.0.1:9222/json/version")

    def test_browser_status_requires_a_real_local_cdp_payload(self):
        invalid_payload = {"Browser": "plain-http-server"}
        valid_payload = {
            "Browser": "Edg/128.0.0.0",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/session",
        }
        invalid_cdp_payloads = [
            {**valid_payload, "Browser": "Chrome/128.0.0.0"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/session"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://127.0.0.1:not-a-port/devtools/browser/session"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://example.test:9222/devtools/browser/session"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/session"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://[::1]:9222/devtools/browser/session"},
            {**valid_payload, "webSocketDebuggerUrl": "wss://127.0.0.1:9222/devtools/browser/session"},
            {**valid_payload, "webSocketDebuggerUrl": "http://127.0.0.1:9222/devtools/browser/session"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/session/extra"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/session?token=x"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://user@127.0.0.1:9222/devtools/browser/session"},
            {**valid_payload, "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/session"},
        ]
        for payload in invalid_cdp_payloads:
            with self.subTest(payload=payload):
                self.assertFalse(app.is_valid_browser_debug_payload(payload, "edge"))
        self.assertTrue(app.is_valid_browser_debug_payload(valid_payload, "edge"))
        # Chrome is the default automation browser, so its payload is the one accepted by default.
        chrome_payload = {**valid_payload, "Browser": "Chrome/128.0.0.0"}
        # The debug port is read through LOCAL_URL_OPENER, which never uses a system or VPN proxy.
        with (
            patch.object(app, "debug_port_listening", return_value=True),
            patch.object(app, "browser_debug_owner_uses_profile", return_value=True),
            patch.object(
                app.LOCAL_URL_OPENER,
                "open",
                return_value=io.BytesIO(json.dumps(invalid_payload).encode()),
            ),
        ):
            self.assertFalse(app.check_browser_status())
        with (
            patch.object(app, "debug_port_listening", return_value=True),
            patch.object(app, "browser_debug_owner_uses_profile", return_value=True),
            patch.object(
                app.LOCAL_URL_OPENER,
                "open",
                return_value=io.BytesIO(json.dumps(chrome_payload).encode()),
            ) as opener,
        ):
            self.assertTrue(app.check_browser_status())
        self.assertEqual(opener.call_args.args[0], "http://127.0.0.1:9222/json/version")
        # build_opener(ProxyHandler({})) leaves out the default ProxyHandler, so neither the
        # proxy environment variables nor the Windows proxy settings apply to this opener.
        self.assertFalse(any(
            isinstance(handler, app.urllib.request.ProxyHandler)
            for handler in app.LOCAL_URL_OPENER.handlers
        ))

    def test_legacy_owner_command_line_parsing_still_requires_one_exact_profile(self):
        profile = app.USER_DATA_DIR_DEBUG
        accepted = (
            f'"msedge.exe" --user-data-dir="{profile}" --remote-debugging-port=9222',
            f'"msedge.exe" "--user-data-dir={profile}"',
        )
        rejected = (
            f'"msedge.exe" --user-data-dir="{profile} Backup"',
            f'"msedge.exe" "--ignored=--user-data-dir={profile}" "--user-data-dir={profile} Backup"',
            f'"msedge.exe" --user-data-dir="{profile}" "--user-data-dir={profile}"',
            '"msedge.exe" --remote-debugging-port=9222',
        )
        for command_line in accepted:
            with self.subTest(command_line=command_line):
                self.assertTrue(app.browser_command_line_uses_profile(command_line, profile))
        for command_line in rejected:
            with self.subTest(command_line=command_line):
                self.assertFalse(app.browser_command_line_uses_profile(command_line, profile))

    def test_edge_resolver_does_not_trust_path_fallback(self):
        with (
            patch.object(app.os.path, "isfile", return_value=False),
            patch.object(app.shutil, "which", return_value=r"C:\\Temp\\msedge.exe") as which,
        ):
            self.assertEqual(app.resolve_edge_executable(), "")
        which.assert_not_called()

    def test_edge_resolver_accepts_the_known_per_user_installation(self):
        per_user_edge = os.path.join(
            app.LOCALAPPDATA,
            "Microsoft",
            "Edge",
            "Application",
            "msedge.exe",
        )
        with patch.object(
            app.os.path,
            "isfile",
            side_effect=lambda path: os.path.normcase(path) == os.path.normcase(per_user_edge),
        ):
            self.assertEqual(app.resolve_edge_executable(), os.path.abspath(per_user_edge))

    def test_edge_start_reuses_an_existing_debug_endpoint(self):
        with (
            patch.object(app, "check_browser_status", return_value=True),
            patch.object(app.subprocess, "Popen") as popen,
        ):
            self.assertTrue(
                app.ensure_browser_running(
                    background=False,
                )
            )
        popen.assert_not_called()

    def test_browser_status_polling_reuses_the_verified_debug_session(self):
        payload = {
            "Browser": "Chrome/128.0.0.0",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/session-1",
        }

        def fake_urlopen(*_args, **_kwargs):
            return io.BytesIO(json.dumps(payload).encode())

        with (
            patch.object(app, "browser_status_cache", {}, create=True),
            patch.object(app, "debug_port_listening", return_value=True),
            patch.object(app.LOCAL_URL_OPENER, "open", side_effect=fake_urlopen),
            patch.object(app, "browser_debug_owner_uses_profile", return_value=True) as owner_check,
        ):
            for _ in range(3):
                self.assertTrue(app.check_browser_status())
            self.assertEqual(owner_check.call_count, 1)

            payload["webSocketDebuggerUrl"] = "ws://127.0.0.1:9222/devtools/browser/session-2"
            self.assertTrue(app.check_browser_status())
            self.assertEqual(owner_check.call_count, 2)

            self.assertTrue(app.check_browser_status(fresh=True))
            self.assertEqual(owner_check.call_count, 3)

    def test_automation_always_reverifies_the_edge_owner(self):
        with (
            patch.object(app, "check_browser_status", return_value=True) as status,
            patch.object(app.subprocess, "Popen") as popen,
        ):
            self.assertTrue(app.ensure_browser_running())
        status.assert_called_once_with(fresh=True)
        popen.assert_not_called()

    def test_ffmpeg_tools_are_never_taken_from_the_current_directory(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            planted = Path(temporary_dir) / "Downloads"
            trusted = Path(temporary_dir) / "ffmpeg" / "bin"
            planted.mkdir()
            trusted.mkdir(parents=True)
            for tool in ("ffprobe.exe", "ffmpeg.exe"):
                (planted / tool).write_bytes(b"MZplanted")
            (trusted / "ffprobe.exe").write_bytes(b"MZtrusted")
            original_cwd = os.getcwd()
            os.chdir(planted)
            try:
                with patch.dict(os.environ, {"PATH": str(trusted)}):
                    self.assertEqual(
                        os.path.normcase(app.resolve_ffprobe_executable()),
                        os.path.normcase(str(trusted / "ffprobe.exe")),
                    )
                    self.assertEqual(app.resolve_ffmpeg_executable(), "")
                with patch.dict(os.environ, {"PATH": os.pathsep.join([".", "Downloads", ""])}):
                    self.assertEqual(app.resolve_ffprobe_executable(), "")
                    self.assertEqual(app.resolve_ffmpeg_executable(), "")
            finally:
                os.chdir(original_cwd)

    def test_ffprobe_runs_without_a_console_window(self):
        with (
            patch.object(app, "resolve_ffprobe_executable", return_value=r"C:\Tools\ffprobe.exe"),
            patch.object(app.subprocess, "check_output", side_effect=["1920x1080\n", "12.5\n"]) as check_output,
        ):
            self.assertEqual(app.get_video_resolution("video.mp4"), (1920, 1080))
            self.assertEqual(app.get_video_duration_seconds("video.mp4"), 12.5)

        self.assertEqual(check_output.call_count, 2)
        for call in check_output.call_args_list:
            self.assertIs(call.kwargs.get("stdin"), app.subprocess.DEVNULL)
            self.assertEqual(call.kwargs.get("creationflags"), getattr(app.subprocess, "CREATE_NO_WINDOW", 0))

    def test_browser_fallback_closes_an_unstarted_server(self):
        server = Mock(server_port=4321)
        with (
            patch.object(app, "initialize_queue_state"),
            patch.object(app, "make_server", return_value=server),
            patch.object(app.webbrowser, "open", return_value=False),
            patch.object(app, "request_app_shutdown"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Không thể mở trình duyệt"):
                app.run_browser_app()

        server.serve_forever.assert_not_called()
        server.shutdown.assert_not_called()
        server.server_close.assert_called_once_with()

    def test_browser_fallback_closes_after_the_serve_loop_exits(self):
        server = Mock(server_port=4321)
        server.serve_forever.side_effect = KeyboardInterrupt
        with (
            patch.object(app, "initialize_queue_state"),
            patch.object(app, "make_server", return_value=server),
            patch.object(app.webbrowser, "open", return_value=True),
            patch.object(app, "request_app_shutdown"),
        ):
            app.run_browser_app()

        server.serve_forever.assert_called_once_with()
        server.shutdown.assert_not_called()
        server.server_close.assert_called_once_with()

    def test_main_falls_back_for_pywebview_startup_exceptions(self):
        from webview.errors import WebViewException

        with (
            patch.object(sys, "argv", ["app.py"]),
            patch.object(app, "acquire_instance_lock", return_value=True),
            patch.object(
                app,
                "run_desktop_app",
                side_effect=WebViewException("Main window failed to load"),
            ),
            patch.object(app, "run_browser_app") as browser_fallback,
            patch.object(app, "release_instance_lock") as release_lock,
            patch.object(app, "log_to_app"),
        ):
            exit_code = app.main()

        self.assertEqual(exit_code, 0)
        browser_fallback.assert_called_once_with()
        release_lock.assert_called_once_with()

    def test_only_fresh_douzy_downloads_are_moved(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            download_root = Path(temporary_dir) / "douzy"
            source_folder = download_root / "video-id"
            source_folder.mkdir(parents=True)
            source = source_folder / "video-id.mp4"
            source.write_bytes(b"video")

            with patch.object(app, "get_douzy_download_dir", return_value=str(download_root)):
                self.assertTrue(app.should_move_douzy_source(str(source)))
                (source_folder / "project.json").write_text("{}", encoding="utf-8")
                self.assertFalse(app.should_move_douzy_source(str(source)))

    def test_sidecar_startup_cleans_process_when_cancelled(self):
        process = Mock()
        process.poll.return_value = None
        process.stdout = Mock()
        process.stdout.readline.return_value = ""
        with tempfile.TemporaryDirectory() as temporary_dir:
            sidecar = Path(temporary_dir) / "sidecar.exe"
            config = Path(temporary_dir) / "config.yml"
            sidecar.write_bytes(b"exe")
            config.write_text("path: test", encoding="utf-8")
            with (
                patch.object(app, "SIDECAR_BIN", str(sidecar)),
                patch.object(app, "DOUZY_CONFIG", str(config)),
                patch.object(app.subprocess, "Popen", return_value=process),
                patch.object(
                    app,
                    "check_cancel_requested",
                    side_effect=[None, app.WorkflowCancelled("cancelled")],
                ),
                patch.object(app, "stop_process") as stop_process,
            ):
                with self.assertRaises(app.WorkflowCancelled):
                    app.start_douzy_sidecar()

        stop_process.assert_called_once_with(process, "sidecar Douzy")
        with app.active_process_lock:
            self.assertNotIn("sidecar Douzy", app.active_processes)

    def test_douzy_asset_matching_does_not_move_prefix_siblings(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            source_folder = directory / "douzy"
            source_folder.mkdir()
            source = source_folder / "123.mp4"
            unrelated = source_folder / "1234.mp4"
            source.write_bytes(b"video-data")
            unrelated.write_bytes(b"unrelated")
            project = app.prepare_project_files(
                str(source), "Video", "123", output_dir=str(directory / "exports"), defer_video_move=True
            )
            with (
                patch.object(app, "DOUZY_DB", str(directory / "missing.db")),
                patch.object(app, "replace_source_video_references"),
            ):
                app.finalize_project_assets(project, "123")

            self.assertTrue(unrelated.is_file())
            self.assertEqual(Path(project["video"]).read_bytes(), b"video-data")

    def test_srt_structure_rejects_invalid_order_and_duration(self):
        invalid_order = SOURCE_SRT.replace("\n2\n00:00:03", "\n1\n00:00:03")
        invalid_duration = SOURCE_SRT.replace(
            "00:00:01,000 --> 00:00:02,000",
            "00:00:02,000 --> 00:00:01,000",
        )
        with self.assertRaisesRegex(ValueError, "thứ tự"):
            app.validate_srt_structure(invalid_order, "SRT test")
        with self.assertRaisesRegex(ValueError, "kết thúc"):
            app.validate_srt_structure(invalid_duration, "SRT test")

    def test_project_bundle_uses_synchronized_unique_names(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            source = directory / "cached.mp4"
            source.write_bytes(b"video-data")
            output = directory / "exports"

            first = app.prepare_project_files(
                str(source), "Video: demo", "123", output_dir=str(output)
            )
            second = app.prepare_project_files(
                str(source), "Video: demo", "123", output_dir=str(output)
            )

            self.assertEqual(Path(first["video"]).name, "Video demo.mp4")
            self.assertEqual(Path(first["raw_srt"]).name, "Video demo.raw.srt")
            self.assertEqual(Path(first["translated_srt"]).name, "Video demo.vi.srt")
            self.assertEqual(Path(first["video"]).read_bytes(), b"video-data")
            self.assertEqual(second["project_name"], "Video demo (2)")

    def test_douyin_project_moves_all_downloaded_assets_after_translation(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            source_folder = directory / "cached"
            source_folder.mkdir()
            source = source_folder / "cached.mp4"
            thumbnail = source_folder / "cached_cover.jpg"
            metadata = source_folder / "cached_data.json"
            music = source_folder / "cached_music.mp3"
            output = directory / "exports"
            source.write_bytes(b"video-data")
            thumbnail.write_bytes(b"\xff\xd8\xffthumbnail")
            metadata.write_text('{"id":"123"}', encoding="utf-8")
            music.write_bytes(b"music-data")

            project = app.prepare_project_files(
                str(source),
                "Video demo",
                "123",
                output_dir=str(output),
                defer_video_move=True,
                include_thumbnail=True,
            )

            self.assertTrue(os.path.samefile(project["video"], source))
            self.assertFalse((Path(project["project_dir"]) / "Video demo.mp4").exists())
            self.assertEqual(Path(project["thumbnail"]).name, "Video demo.thumbnail.jpg")
            self.assertEqual(Path(project["thumbnail"]).read_bytes(), thumbnail.read_bytes())

            with (
                patch.object(app, "DOUZY_DB", str(directory / "missing.db")),
                patch.object(app, "replace_source_video_references"),
            ):
                app.finalize_project_assets(project, "123")

            self.assertEqual(Path(project["video"]).name, "Video demo.mp4")
            self.assertEqual(Path(project["video"]).read_bytes(), b"video-data")
            self.assertEqual(Path(project["thumbnail"]).name, "Video demo.thumbnail.jpg")
            self.assertEqual((Path(project["project_dir"]) / "Video demo.data.json").read_text(encoding="utf-8"), '{"id":"123"}')
            self.assertEqual((Path(project["project_dir"]) / "Video demo.music.mp3").read_bytes(), b"music-data")
            self.assertFalse(source_folder.exists())

    def test_thumbnail_download_validates_host_size_and_image_bytes(self):
        class FakeResponse:
            headers = {"Content-Length": "12", "Content-Type": "image/jpeg"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://p3.douyinpic.com/cover.jpeg"

            def read(self, _limit):
                return b"\xff\xd8\xffthumbnail"

        with tempfile.TemporaryDirectory() as temporary_dir:
            destination = str(Path(temporary_dir) / "video.thumbnail")
            with patch.object(app.urllib.request, "urlopen", return_value=FakeResponse()):
                downloaded = app.download_thumbnail(
                    "https://p3.douyinpic.com/cover.jpeg", destination
                )

            self.assertEqual(downloaded, destination + ".jpg")
            self.assertTrue(Path(downloaded).is_file())
            with self.assertRaises(ValueError):
                app.download_thumbnail("https://example.com/cover.jpg", destination)

    def test_logging_survives_a_legacy_console_encoding(self):
        class Cp1252Console:
            encoding = "cp1252"

            def write(self, value):
                value.encode(self.encoding)

            def flush(self):
                pass

        original_stdout = sys.stdout
        app.reset_progress()
        try:
            sys.stdout = Cp1252Console()
            app.log_to_app("Đang xử lý", "system")
        finally:
            sys.stdout = original_stdout

        self.assertIn("Đang xử lý", app.progress_snapshot()["logs"][-1])

    def test_progress_returns_incremental_logs_and_caps_memory(self):
        app.reset_progress()
        with patch.object(app, "MAX_LOG_LINES", 2):
            app.log_to_app("one")
            app.log_to_app("two")
            app.log_to_app("three")

        snapshot = app.progress_snapshot()
        self.assertEqual(snapshot["logs"], ["[*] two", "[*] three"])
        self.assertEqual(snapshot["log_base_index"], 1)
        self.assertEqual(snapshot["next_log_index"], 3)
        self.assertEqual(app.progress_snapshot(2)["logs"], ["[*] three"])

    def test_accepts_douyin_subdomains_only(self):
        self.assertEqual(
            app.validate_douyin_url("https://www.douyin.com/video/123"),
            "https://www.douyin.com/video/123",
        )
        self.assertEqual(
            app.validate_douyin_url("https://v.douyin.com/abc/"),
            "https://v.douyin.com/abc/",
        )
        self.assertEqual(
            app.validate_douyin_url(
                "3.21 Copy this text https://v.douyin.com/AbCdEfG/ 09/12"
            ),
            "https://v.douyin.com/AbCdEfG/",
        )
        with self.assertRaises(ValueError):
            app.validate_douyin_url("https://douyin.com.attacker.test/video/123")

    def test_rejects_invalid_notebook_urls_and_languages(self):
        with self.assertRaises(ValueError):
            app.validate_notebook_url("https://example.test/notebook/123")
        with self.assertRaises(ValueError):
            app.validate_notebook_url("")
        self.assertEqual(app.validate_notebook_url("", allow_empty=True), "")
        with self.assertRaises(ValueError):
            app.validate_ocr_lang("shell")

    def test_release_versions_compare_semantically(self):
        self.assertEqual(app.parse_release_version("v1.2.3"), (1, 2, 3))
        self.assertTrue(app.is_newer_release("1.2.0", "1.1.9"))
        self.assertFalse(app.is_newer_release("1.1.0", "1.1.0"))
        with self.assertRaises(ValueError):
            app.parse_release_version("latest")

    def test_update_download_urls_are_restricted_to_the_official_release(self):
        valid = (
            "https://github.com/qvinh8726/VietSub-Studio/releases/download/"
            "v1.3.0/VietSub-Studio-Portable-v1.3.0.zip"
        )
        self.assertEqual(app.validate_release_download_url(valid, "1.3.0"), valid)
        with self.assertRaises(ValueError):
            app.validate_release_download_url(
                "https://example.test/VietSub-Studio-Portable-v1.3.0.zip",
                "1.3.0",
            )

    def test_translation_must_preserve_all_timestamps(self):
        self.assertEqual(app.validate_translated_srt(SOURCE_SRT, TRANSLATED_SRT), TRANSLATED_SRT)
        invalid = TRANSLATED_SRT.replace("00:00:04,000", "00:00:05,000")
        with self.assertRaises(ValueError):
            app.validate_translated_srt(SOURCE_SRT, invalid)

    def test_translation_rejects_chinese_echo_and_bilingual_output(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n\u4f60\u597d\u4e16\u754c\uff0c\u8fd9\u662f\u6d4b\u8bd5\u5b57\u5e55\u3002\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n\u6211\u4eec\u73b0\u5728\u51fa\u53d1\u3002\n"
        )
        bilingual = (
            "1\n00:00:01,000 --> 00:00:02,000\n\u4f60\u597d\u4e16\u754c。 Xin chao the gioi.\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n\u6211\u4eec\u73b0\u5728\u51fa\u53d1\u3002 Chung ta di thoi.\n"
        )
        for invalid in (source, bilingual):
            with self.assertRaisesRegex(ValueError, "tiếng Trung"):
                app.validate_translated_srt(source, invalid)

    def test_translation_cache_fingerprint_changes_with_model_and_instructions(self):
        runtime = {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        }
        first = app.translation_cache_fingerprint(runtime)
        runtime["model"] = "deepseek-reasoner"
        self.assertNotEqual(first, app.translation_cache_fingerprint(runtime))
        runtime["model"] = "deepseek-chat"
        runtime["translation_instructions"] = "Dịch ngắn gọn, dứt khoát."
        self.assertNotEqual(first, app.translation_cache_fingerprint(runtime))

    def test_default_prompt_is_neutral_and_the_game_glossary_is_a_preset(self):
        prompt = app.build_translation_prompt(SOURCE_SRT)
        self.assertNotIn("Saeed", prompt)
        self.assertNotIn("game", prompt.lower())
        self.assertNotIn("liệt kê", prompt.lower())
        self.assertIn("```srt", prompt)
        self.assertIn(SOURCE_SRT, prompt)

        preset = app.TRANSLATION_INSTRUCTION_PRESETS["delta_force"]["instructions"]
        preset_prompt = app.build_translation_prompt(SOURCE_SRT, preset)
        self.assertIn("Saeed (赛伊德)", preset_prompt)
        self.assertIn("HA AVK CITY", preset_prompt)
        self.assertIn(SOURCE_SRT, preset_prompt)

        custom_prompt = app.build_translation_prompt(
            SOURCE_SRT,
            "Dịch theo giọng hành động gọn, mạnh và tự nhiên.",
        )
        self.assertIn("Dịch theo giọng hành động gọn, mạnh và tự nhiên.", custom_prompt)
        self.assertIn("Giữ nguyên số thứ tự và timestamp", custom_prompt)
        self.assertIn("Đặt toàn bộ SRT đã dịch trong đúng một block code srt", custom_prompt)
        self.assertIn(SOURCE_SRT, custom_prompt)

    def test_translation_restores_source_timestamps_when_blocks_match(self):
        changed_timecodes = TRANSLATED_SRT.replace("00:00:04,000", "00:00:05,000")
        normalized = app.normalize_translated_srt(SOURCE_SRT, changed_timecodes)
        self.assertEqual(
            app.SRT_TIMECODE_PATTERN.findall(normalized),
            app.SRT_TIMECODE_PATTERN.findall(SOURCE_SRT),
        )

    def test_translation_chunks_only_after_45_minutes_and_preserves_blocks(self):
        short_srt = "1\n00:44:59,000 --> 00:45:00,000\nShort\n"
        chunks, is_long_video = app.split_srt_for_translation(short_srt)
        self.assertFalse(is_long_video)
        self.assertEqual(chunks, [short_srt])

        long_srt = (
            "1\n00:01:00,000 --> 00:01:01,000\nOne\n\n"
            "2\n00:31:00,000 --> 00:31:01,000\nTwo\n\n"
            "3\n01:01:00,000 --> 01:01:01,000\nThree\n"
        )
        chunks, is_long_video = app.split_srt_for_translation(long_srt)
        self.assertTrue(is_long_video)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(
            [block for chunk in chunks for block in app.parse_srt_blocks(chunk)],
            app.parse_srt_blocks(long_srt),
        )

        sparse_long_srt = (
            "1\n00:01:00,000 --> 00:01:01,000\nOne\n\n"
            "2\n00:31:00,000 --> 00:31:01,000\nTwo\n"
        )
        chunks, is_long_video = app.split_srt_for_translation(
            sparse_long_srt,
            video_duration_seconds=46 * 60,
        )
        self.assertTrue(is_long_video)
        self.assertEqual(len(chunks), 2)

    def test_translation_api_configuration_validates_providers_and_urls(self):
        self.assertEqual(app.validate_translation_provider(None), "notebook")
        self.assertEqual(
            app.validate_translation_api_base_url("", "deepseek"),
            "https://api.deepseek.com",
        )
        self.assertEqual(
            app.validate_translation_api_base_url("http://127.0.0.1:11434/v1", "custom"),
            "http://127.0.0.1:11434/v1",
        )
        self.assertEqual(
            app.validate_translation_api_model("", "openrouter"),
            "deepseek/deepseek-chat",
        )
        with self.assertRaises(ValueError):
            app.validate_translation_provider("unknown")
        with self.assertRaises(ValueError):
            app.validate_translation_api_base_url("http://example.test/v1", "custom")
        with self.assertRaises(ValueError):
            app.validate_translation_api_model([], "custom")

    def test_openai_compatible_api_sends_auth_model_and_messages(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps({
                    "choices": [{
                        "message": {"content": "OK"},
                        "finish_reason": "stop",
                    }]
                }).encode("utf-8")

        def fake_urlopen(request_object, timeout):
            captured["url"] = request_object.full_url
            captured["headers"] = dict(request_object.header_items())
            captured["body"] = json.loads(request_object.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        runtime_config = {
            "provider": "openrouter",
            "label": "OpenRouter",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "deepseek/deepseek-chat",
            "api_key": "sk-test",
        }
        with patch.object(app, "open_translation_api_request", side_effect=fake_urlopen):
            result = app.call_openai_compatible_api(
                runtime_config,
                [{"role": "user", "content": "Ping"}],
                max_tokens=16,
            )

        self.assertEqual(result, "OK")
        self.assertEqual(captured["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "deepseek/deepseek-chat")
        self.assertEqual(captured["body"]["max_tokens"], 16)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(captured["headers"]["X-title"], "VietSub Studio")

    def test_deepseek_requests_disable_thinking_for_srt_output(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps({
                    "choices": [{
                        "message": {"content": "OK"},
                        "finish_reason": "stop",
                    }]
                }).encode("utf-8")

        def fake_urlopen(request_object, timeout):
            captured["body"] = json.loads(request_object.data.decode("utf-8"))
            return FakeResponse()

        runtime_config = {
            "provider": "deepseek",
            "label": "DeepSeek API",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "api_key": "sk-test",
        }
        with patch.object(app, "open_translation_api_request", side_effect=fake_urlopen):
            app.call_openai_compatible_api(
                runtime_config,
                [{"role": "user", "content": "Ping"}],
                max_tokens=32_768,
            )

        self.assertEqual(captured["body"]["thinking"], {"type": "disabled"})
        self.assertEqual(captured["body"]["response_format"], {"type": "text"})
        self.assertEqual(captured["body"]["max_tokens"], 32_768)

    def test_openai_compatible_api_reads_sse_streams(self):
        captured = {}

        class StreamingResponse:
            headers = {"Content-Type": "text/event-stream"}

            def __init__(self):
                self.lines = iter([
                    b'data: {"choices":[{"delta":{"content":"Xin "}}]}\n',
                    b'data: {"choices":[{"delta":{"content":"chao"}}]}\n',
                    b"data: [DONE]\n",
                    b"",
                ])

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def readline(self):
                return next(self.lines)

        def fake_urlopen(request_object, timeout):
            captured["body"] = json.loads(request_object.data.decode("utf-8"))
            return StreamingResponse()

        runtime_config = {
            "provider": "custom",
            "label": "Custom API",
            "base_url": "http://127.0.0.1:20128/v1",
            "model": "test-model",
            "api_key": "",
        }
        with patch.object(app, "open_translation_api_request", side_effect=fake_urlopen):
            result = app.call_openai_compatible_api(
                runtime_config,
                [{"role": "user", "content": "Ping"}],
                max_tokens=16,
                stream=True,
            )

        self.assertEqual(result, "Xin chao")
        self.assertTrue(captured["body"]["stream"])

    def test_api_translation_resumes_from_completed_chunks(self):
        long_srt = (
            "1\n00:01:00,000 --> 00:01:01,000\nOne\n\n"
            "2\n00:31:00,000 --> 00:31:01,000\nTwo\n\n"
            "3\n01:01:00,000 --> 01:01:01,000\nThree\n"
        )

        def translated_chunk(source_chunk):
            return app.format_srt_blocks([
                (index, timecode, f"VI {text}")
                for index, timecode, text in app.parse_srt_blocks(source_chunk)
            ])

        runtime_config = {
            "provider": "deepseek",
            "label": "DeepSeek API",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "sk-test",
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            raw_srt = directory / "video.raw.srt"
            output_srt = directory / "video.vi.srt"
            raw_srt.write_text(long_srt, encoding="utf-8")
            first_run_indexes = []

            def fail_second(_runtime, source_chunk, chunk_index, _chunk_count):
                first_run_indexes.append(chunk_index)
                if chunk_index == 2:
                    raise RuntimeError("temporary failure")
                return translated_chunk(source_chunk)

            with patch.object(app, "translate_api_chunk", side_effect=fail_second):
                with self.assertRaises(RuntimeError):
                    app.translate_srt_via_api(
                        str(raw_srt),
                        runtime_config,
                        output_srt=str(output_srt),
                    )

            self.assertEqual(first_run_indexes, [1, 2])
            self.assertTrue(Path(str(output_srt) + ".parts").is_dir())
            resumed_indexes = []

            def finish_remaining(_runtime, source_chunk, chunk_index, _chunk_count):
                resumed_indexes.append(chunk_index)
                return translated_chunk(source_chunk)

            with patch.object(app, "translate_api_chunk", side_effect=finish_remaining):
                result = app.translate_srt_via_api(
                    str(raw_srt),
                    runtime_config,
                    output_srt=str(output_srt),
                )

            self.assertEqual(result, str(output_srt))
            self.assertEqual(resumed_indexes, [2, 3])
            self.assertFalse(Path(str(output_srt) + ".parts").exists())
            app.validate_translated_srt(
                long_srt,
                output_srt.read_text(encoding="utf-8"),
            )

    def test_edge_login_detection_ignores_hidden_or_generic_google_links(self):
        hidden_sign_in = self.FakeElement(visible=False)
        generic_account_link = self.FakeElement(visible=True)
        page = self.FakePage(
            "https://gemini.google.com/notebook/test-id",
            {
                "button:has-text('Sign in')": [hidden_sign_in],
                "a[href*='accounts.google.com']": [generic_account_link],
            },
        )

        self.assertFalse(app.edge_page_needs_login(page))

    def test_edge_login_detection_requires_a_visible_control_or_login_url(self):
        visible_sign_in = self.FakeElement(visible=True)
        page = self.FakePage(
            "https://gemini.google.com/notebook/test-id",
            {"button:has-text('Sign in')": [visible_sign_in]},
        )

        self.assertTrue(app.edge_page_needs_login(page))
        self.assertTrue(
            app.edge_page_needs_login(self.FakePage("https://accounts.google.com/signin"))
        )

    def test_result_is_ready_only_when_every_output_file_is_valid(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            translated_srt = directory / "project.vi.srt"
            video.write_bytes(b"video")
            raw_srt.write_text(SOURCE_SRT, encoding="utf-8")
            translated_srt.write_text(TRANSLATED_SRT, encoding="utf-8")
            result = {
                "project_name": "Project",
                "project_dir": str(directory),
                "video": str(video),
                "raw_srt": str(raw_srt),
                "translated_srt": str(translated_srt),
            }

            self.assertTrue(app.project_result_ready(result))
            translated_srt.unlink()
            self.assertFalse(app.project_result_ready(result))

    def test_error_job_never_exposes_a_ready_result(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            translated_srt = directory / "project.vi.srt"
            video.write_bytes(b"video")
            raw_srt.write_text(SOURCE_SRT, encoding="utf-8")
            translated_srt.write_text(TRANSLATED_SRT, encoding="utf-8")
            job = {
                "id": "job-error",
                "status": "error",
                "step": "translate",
                "result": {
                    "project_dir": str(directory),
                    "video": str(video),
                    "raw_srt": str(raw_srt),
                    "translated_srt": str(translated_srt),
                },
            }

            self.assertFalse(app.serialize_job(job)["result_ready"])

    def test_ocr_speed_settings_are_validated(self):
        self.assertEqual(app.validate_ocr_model_mode(None), "auto")
        self.assertEqual(app.validate_ocr_model_mode(""), "auto")
        self.assertEqual(app.validate_ocr_model_mode("fast"), "fast")
        self.assertEqual(app.validate_ocr_model_mode("accurate"), "accurate")
        with self.assertRaises(ValueError):
            app.validate_ocr_model_mode("turbo")
        with self.assertRaises(ValueError):
            app.validate_ocr_model_mode([])

        self.assertIsNone(app.validate_ocr_frames_to_skip(None))
        self.assertIsNone(app.validate_ocr_frames_to_skip(""))
        self.assertEqual(app.validate_ocr_frames_to_skip(3), 3)
        self.assertEqual(app.validate_ocr_frames_to_skip("2"), 2)
        with self.assertRaises(ValueError):
            app.validate_ocr_frames_to_skip(-1)
        with self.assertRaises(ValueError):
            app.validate_ocr_frames_to_skip(99)
        with self.assertRaises(ValueError):
            app.validate_ocr_frames_to_skip("nhanh")
        with self.assertRaises(ValueError):
            app.validate_ocr_frames_to_skip(True)

    def test_run_videocr_applies_speed_settings_from_app_config(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            cli = directory / "videocr-cli.exe"
            cli.write_bytes(b"exe")
            video = directory / "video.mp4"
            video.write_bytes(b"video")
            output_srt = directory / "video.raw.srt"
            output_srt.write_text("stub", encoding="utf-8")

            captured = {}

            def fake_popen(cmd, **_kwargs):
                captured["cmd"] = cmd
                output_srt.write_text("fresh", encoding="utf-8")
                process = Mock()
                process.poll.return_value = 0
                process.returncode = 0
                process.stdout = io.StringIO("")
                return process

            with (
                patch.object(app, "VIDEOCR_CLI", str(cli)),
                patch.object(app, "VIDEOCR_CONFIG", str(directory / "missing.ini")),
                patch.object(app, "load_app_config", return_value={
                    "ocr_model_mode": "fast",
                    "ocr_frames_to_skip": 2,
                }),
                patch.object(app, "get_saved_crop_coords", return_value=None),
                patch.object(app, "get_video_resolution", return_value=None),
                patch.object(app, "get_video_duration_seconds", return_value=44 * 60),
                patch.object(app.subprocess, "Popen", side_effect=fake_popen),
            ):
                result = app.run_videocr(str(video), ocr_lang="ch", output_srt=str(output_srt))

            self.assertEqual(result, str(output_srt))
            cmd = captured["cmd"]
            self.assertEqual(cmd[cmd.index("--use_server_model") + 1], "false")
            self.assertEqual(cmd[cmd.index("--frames_to_skip") + 1], "2")

    def test_run_videocr_chunks_long_video_and_repairs_a_crashing_segment(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            cli = directory / "videocr-cli.exe"
            cli.write_bytes(b"exe")
            video = directory / "long.mp4"
            video.write_bytes(b"video")
            output_srt = directory / "long.raw.srt"
            executed_commands = []

            def fake_execute(cmd, target, *, allow_empty=False, **_kwargs):
                executed_commands.append(list(cmd))
                target_path = Path(target)
                if target_path.name.endswith(".normalized.srt"):
                    is_last_chunk = "chunk-003" in target_path.name
                    timecode = (
                        "00:00:00,000 --> 00:00:00,500"
                        if is_last_chunk
                        else "00:00:02,000 --> 00:00:03,000"
                    )
                    target_path.write_text(
                        f"1\n{timecode}\n{'C' if is_last_chunk else 'B'}\n",
                        encoding="utf-8",
                    )
                    return str(target_path)
                start = cmd[cmd.index("--time_start") + 1]
                if start == "00:10:00":
                    raise app.VideoOCRProcessError(3221225477)
                timestamp = "00:00:01" if start == "00:00:00" else "00:20:00"
                target_path.write_text(
                    f"1\n{timestamp},000 --> {timestamp},500\n"
                    f"{'A' if start == '00:00:00' else 'C'}\n",
                    encoding="utf-8",
                )
                return str(target_path)

            def fake_transcode(_video, target, *_args, **_kwargs):
                Path(target).write_bytes(b"normalized")
                return target

            with (
                patch.object(app, "VIDEOCR_CLI", str(cli)),
                patch.object(app, "VIDEOCR_CONFIG", str(directory / "missing.ini")),
                patch.object(app, "load_app_config", return_value={}),
                patch.object(app, "VIDEOCR_CHUNK_THRESHOLD_SECONDS", 900),
                patch.object(app, "get_video_duration_seconds", return_value=1201),
                patch.object(app, "execute_videocr_command", side_effect=fake_execute),
                patch.object(app, "transcode_videocr_chunk", side_effect=fake_transcode) as transcode,
            ):
                result = app.run_videocr(
                    str(video),
                    ocr_lang="ch",
                    output_srt=str(output_srt),
                    crop_coords={
                        "crop_x": 0.1,
                        "crop_y": 0.8,
                        "crop_width": 0.8,
                        "crop_height": 0.1,
                    },
                    use_saved_crop=False,
                    video_resolution={"width": 1280, "height": 720},
                )

            self.assertEqual(result, str(output_srt))
            self.assertEqual(transcode.call_count, 2)
            blocks = app.parse_srt_blocks(output_srt.read_text(encoding="utf-8"))
            self.assertEqual([block[2] for block in blocks], ["A", "B", "C"])
            self.assertEqual(blocks[1][1], "00:10:02,000 --> 00:10:03,000")
            self.assertEqual(
                [
                    cmd[cmd.index("--time_start") + 1]
                    for cmd in executed_commands
                    if "--time_start" in cmd
                ],
                ["00:00:00", "00:10:00"],
            )

    ANSI_VIDEOCR_INI = (
        "[Settings]\n"
        "--output_dir = C:\\Users\\José\\Documents\\VideOCR\n"
        "--use_gpu = false\n"
        "--use_server_model = true\n"
        "--normalize_to_simplified_chinese = false\n"
        "--saved_crop_boxes = [{'coords': {'crop_x': 0.1, 'crop_y': 0.8, "
        "'crop_width': 0.8, 'crop_height': 0.15}}]\n"
        "--frames_to_skip\n"
    )

    def write_ansi_videocr_ini(self, directory):
        ini_path = Path(directory) / "videocr_gui_config.ini"
        ini_path.write_bytes(self.ANSI_VIDEOCR_INI.encode("cp1252"))
        return ini_path

    def test_videocr_ini_written_in_an_ansi_code_page_is_readable(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            ini_path = self.write_ansi_videocr_ini(temporary_dir)
            with (
                patch.object(app, "VIDEOCR_CONFIG", str(ini_path)),
                patch.object(app, "load_app_config", return_value={}),
            ):
                settings = app.read_videocr_gui_settings()
                crop = app.get_saved_crop_coords()

        self.assertEqual(settings["--use_gpu"], "false")
        self.assertIn("José", settings["--output_dir"])
        self.assertNotIn("--frames_to_skip", settings)
        self.assertEqual(crop["crop_y"], 0.8)

    def test_run_videocr_uses_flags_from_an_ansi_videocr_ini(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            ini_path = self.write_ansi_videocr_ini(directory)
            cli = directory / "videocr-cli.exe"
            cli.write_bytes(b"exe")
            video = directory / "video.mp4"
            video.write_bytes(b"video")
            output_srt = directory / "video.raw.srt"
            captured = {}

            def fake_execute(cmd, target, **kwargs):
                captured["cmd"] = list(cmd)
                captured["kwargs"] = kwargs
                Path(target).write_text(SOURCE_SRT, encoding="utf-8")
                return target

            with (
                patch.object(app, "VIDEOCR_CLI", str(cli)),
                patch.object(app, "VIDEOCR_CONFIG", str(ini_path)),
                patch.object(app, "load_app_config", return_value={}),
                patch.object(app, "get_video_resolution", return_value=(1280, 720)),
                patch.object(app, "get_video_duration_seconds", return_value=44 * 60),
                patch.object(app, "execute_videocr_command", side_effect=fake_execute),
            ):
                app.run_videocr(str(video), ocr_lang="ch", output_srt=str(output_srt))

        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("--use_gpu") + 1], "false")
        self.assertEqual(cmd[cmd.index("--normalize_to_simplified_chinese") + 1], "false")
        self.assertIn("--crop_y", cmd)
        # A 44-minute single run needs far more than the old fixed 30-minute limit.
        self.assertGreaterEqual(captured["kwargs"]["timeout_seconds"], 2 * 60 * 60)

    def test_videocr_timeout_scales_with_the_scanned_span(self):
        self.assertEqual(app.videocr_timeout_seconds(10 * 60), 40 * 60)
        self.assertEqual(app.videocr_timeout_seconds(44 * 60), 44 * 60 * 3 + 600)
        self.assertEqual(app.videocr_timeout_seconds(30), app.VIDEOCR_PROCESS_TIMEOUT_SECONDS)
        self.assertEqual(app.videocr_timeout_seconds(None), app.VIDEOCR_UNKNOWN_SPAN_TIMEOUT_SECONDS)

    def test_settings_page_loads_with_an_ansi_videocr_ini(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            ini_path = self.write_ansi_videocr_ini(temporary_dir)
            with (
                patch.object(app, "VIDEOCR_CONFIG", str(ini_path)),
                patch.object(app, "CONFIG_PATH", str(Path(temporary_dir) / "app_config.json")),
                patch.dict(app.app.config, {"TESTING": True}),
            ):
                response = app.app.test_client().get("/api/settings")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["crop_coords"]["crop_x"], 0.1)

    def make_videocr_process(self, output_srt, returncode, lines):
        process = Mock()
        process.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
        process.poll.side_effect = [None] * len(lines) + [returncode] * 20
        process.returncode = returncode
        Path(output_srt).write_text(SOURCE_SRT, encoding="utf-8")
        return process

    def test_videocr_output_goes_to_the_console_and_only_failures_reach_the_log_file(self):
        lines = [f"OCR frame line {index:02d}" for index in range(30)]
        with tempfile.TemporaryDirectory() as temporary_dir:
            log_path = Path(temporary_dir) / "vietsub-studio.log"
            output_srt = Path(temporary_dir) / "out.srt"
            with patch.object(app, "LOG_PATH", str(log_path)):
                app.reset_progress()
                with patch.object(
                    app.subprocess,
                    "Popen",
                    side_effect=lambda *_a, **_k: self.make_videocr_process(output_srt, 0, lines),
                ):
                    app.execute_videocr_command(["videocr"], str(output_srt))
                success_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                console = app.progress_snapshot()["logs"]

                with (
                    patch.object(
                        app.subprocess,
                        "Popen",
                        side_effect=lambda *_a, **_k: self.make_videocr_process(output_srt, 3, lines),
                    ),
                    self.assertRaises(app.VideoOCRProcessError),
                ):
                    app.execute_videocr_command(["videocr"], str(output_srt))
                failure_log = log_path.read_text(encoding="utf-8")

        self.assertIn("[VideOCR] OCR frame line 00", console)
        self.assertNotIn("OCR frame line", success_log)
        self.assertIn("OCR frame line 29", failure_log)
        self.assertNotIn("OCR frame line 00", failure_log)

    def test_videocr_normalization_filter_tracks_resolution_changes(self):
        video_filter = app.build_videocr_normalization_filter(
            {
                "crop_x": 0.093458,
                "crop_y": 0.807271,
                "crop_width": 0.810946,
                "crop_height": 0.128928,
            },
            (119, 581, 1038, 92),
            (1280, 720),
        )

        self.assertIn("iw*0.810946000", video_filter)
        self.assertIn("ih*0.128928000", video_filter)
        self.assertIn("scale=1038:92", video_filter)

    def test_videocr_access_violation_has_a_readable_error(self):
        error = app.VideoOCRProcessError(3221225477)
        self.assertIn("0xC0000005", str(error))
        self.assertIn("access violation", str(error))

    def test_merged_videocr_srt_deduplicates_chunk_boundary_text(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_srt = Path(temporary_dir) / "merged.srt"
            app.write_merged_videocr_srt(
                [
                    {"start": 599_000, "end": 600_050, "text": "Same"},
                    {"start": 600_000, "end": 601_000, "text": "Same"},
                    {"start": 602_000, "end": 603_000, "text": "Next"},
                ],
                str(output_srt),
            )

            blocks = app.parse_srt_blocks(output_srt.read_text(encoding="utf-8"))
            self.assertEqual(len(blocks), 2)
            self.assertEqual(blocks[0][1], "00:09:59,000 --> 00:10:01,000")
            self.assertEqual(blocks[0][2], "Same")

    def test_managed_local_videos_are_hardlinked_into_the_project(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            local_dir = directory / "local_videos"
            local_dir.mkdir()
            source = local_dir / "abc.mp4"
            source.write_bytes(b"video-bytes")

            with patch.object(app, "LOCAL_VIDEO_DIR", str(local_dir)):
                project = app.prepare_project_files(
                    str(source),
                    "Video local",
                    "local-abc",
                    output_dir=str(directory / "out"),
                    defer_video_move=False,
                    include_thumbnail=False,
                )

            video_path = Path(project["video"])
            self.assertTrue(video_path.is_file())
            self.assertEqual(video_path.read_bytes(), b"video-bytes")
            self.assertEqual(os.stat(video_path).st_nlink, 2)

    def test_hardlink_failure_falls_back_to_a_full_copy(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            local_dir = directory / "local_videos"
            local_dir.mkdir()
            source = local_dir / "abc.mp4"
            source.write_bytes(b"video-bytes")

            with (
                patch.object(app, "LOCAL_VIDEO_DIR", str(local_dir)),
                patch.object(app.os, "link", side_effect=OSError("no links")),
            ):
                project = app.prepare_project_files(
                    str(source),
                    "Video local",
                    "local-abc",
                    output_dir=str(directory / "out"),
                    defer_video_move=False,
                    include_thumbnail=False,
                )

            video_path = Path(project["video"])
            self.assertTrue(video_path.is_file())
            self.assertEqual(video_path.read_bytes(), b"video-bytes")
            self.assertEqual(os.stat(video_path).st_nlink, 1)

    def test_cloud_synced_output_paths_are_detected(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            onedrive_root = os.path.join(temporary_dir, "OneDrive")
            inside = os.path.join(onedrive_root, "Documents", "VietSub Studio")
            outside = os.path.join(temporary_dir, "Videos")

            with patch.dict(os.environ, {"OneDrive": onedrive_root}):
                self.assertTrue(app.is_cloud_synced_path(inside))

            cleaned = {
                key: value for key, value in os.environ.items()
                if not key.lower().startswith("onedrive")
            }
            with patch.dict(os.environ, cleaned, clear=True):
                self.assertTrue(app.is_cloud_synced_path(inside))  # tên đường dẫn vẫn chứa OneDrive
                self.assertFalse(app.is_cloud_synced_path(outside))

    def test_success_job_trusts_the_cached_flag_but_rechecks_file_presence(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            translated_srt = directory / "project.vi.srt"
            video.write_bytes(b"video")
            raw_srt.write_text(SOURCE_SRT, encoding="utf-8")
            translated_srt.write_text(TRANSLATED_SRT, encoding="utf-8")
            job = {
                "id": "job-success",
                "status": "success",
                "step": "done",
                "result_ready": True,
                "result": {
                    "project_dir": str(directory),
                    "video": str(video),
                    "raw_srt": str(raw_srt),
                    "translated_srt": str(translated_srt),
                },
            }

            with patch.object(app, "project_result_ready") as validate:
                self.assertTrue(app.serialize_job(job)["result_ready"])
                validate.assert_not_called()

            job["result_ready"] = False
            self.assertFalse(app.serialize_job(job)["result_ready"])

            job["result_ready"] = True
            translated_srt.unlink()
            self.assertFalse(app.serialize_job(job)["result_ready"])

    def test_legacy_persisted_success_jobs_validate_once_at_load(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            translated_srt = directory / "project.vi.srt"
            video.write_bytes(b"video")
            raw_srt.write_text(SOURCE_SRT, encoding="utf-8")
            translated_srt.write_text(TRANSLATED_SRT, encoding="utf-8")
            legacy_value = {
                "id": "legacy-success",
                "status": "success",
                "step": "done",
                "result": {
                    "project_dir": str(directory),
                    "video": str(video),
                    "raw_srt": str(raw_srt),
                    "translated_srt": str(translated_srt),
                },
            }

            job = app.normalize_persisted_job(legacy_value)
            self.assertTrue(job["result_ready"])

            job_with_flag = app.normalize_persisted_job({**legacy_value, "result_ready": False})
            self.assertFalse(job_with_flag["result_ready"])

    def test_persisted_false_positive_success_becomes_translation_retry(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            translated_srt = directory / "project.vi.srt"
            video.write_bytes(b"video")
            chinese = (
                "1\n00:00:01,000 --> 00:00:02,000\n\u4f60\u597d\u4e16\u754c\uff0c\u8fd9\u662f\u6d4b\u8bd5\u5b57\u5e55\u3002\n"
            )
            raw_srt.write_text(chinese, encoding="utf-8")
            translated_srt.write_text(chinese, encoding="utf-8")
            job = app.normalize_persisted_job({
                "id": "false-success",
                "status": "success",
                "step": "done",
                "result_ready": True,
                "result": {
                    "project_dir": str(directory),
                    "video": str(video),
                    "raw_srt": str(raw_srt),
                    "translated_srt": str(translated_srt),
                },
            })

        self.assertEqual(job["status"], "error")
        self.assertFalse(job["result_ready"])
        self.assertEqual(job["resume_step"], "translate")


class DownloadLookupTests(unittest.TestCase):
    def test_updates_douzy_history_after_moving_the_video(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            database = directory / "douzy.db"
            destination = directory / "project.mp4"
            destination.write_bytes(b"video")

            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE aweme (id INTEGER PRIMARY KEY, aweme_id TEXT, download_time INTEGER, file_path TEXT)"
                )
                connection.execute(
                    "INSERT INTO aweme (aweme_id, download_time, file_path) VALUES (?, ?, ?)",
                    ("target", 101, "old-folder"),
                )
                connection.commit()
            finally:
                connection.close()

            with patch.object(app, "DOUZY_DB", str(database)):
                app.update_douzy_video_path("target", str(destination))

            connection = sqlite3.connect(database)
            try:
                saved_path = connection.execute(
                    "SELECT file_path FROM aweme WHERE aweme_id = ?", ("target",)
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(saved_path, str(destination))

    def test_looks_up_the_requested_video_id_only(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            database = directory / "douzy.db"
            video = directory / "target.mp4"
            video.write_bytes(b"video")

            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE aweme (id INTEGER PRIMARY KEY, aweme_id TEXT, download_time INTEGER, file_path TEXT)"
                )
                connection.execute(
                    "INSERT INTO aweme (aweme_id, download_time, file_path) VALUES (?, ?, ?)",
                    ("target", 101, str(video)),
                )
                connection.execute(
                    "INSERT INTO aweme (aweme_id, download_time, file_path) VALUES (?, ?, ?)",
                    ("other", 102, str(video)),
                )
                connection.commit()
            finally:
                connection.close()

            with patch.object(app, "DOUZY_DB", str(database)):
                self.assertEqual(app.get_downloaded_video_file(100, "target"), str(video))
                self.assertEqual(
                    app.get_downloaded_video_file(200, "target", allow_existing=True), str(video)
                )
                with self.assertRaises(FileNotFoundError):
                    app.get_downloaded_video_file(100, "missing")

    def test_retries_when_douzy_database_is_locked(self):
        with (
            patch.object(
                app,
                "get_downloaded_video_file",
                side_effect=[sqlite3.OperationalError("database is locked"), "video.mp4"],
            ),
            patch.object(app.time, "sleep"),
        ):
            self.assertEqual(app.wait_for_downloaded_video_file(100, "target"), "video.mp4")

    def test_resolves_an_mp4_inside_a_douzy_folder(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "custom-name.mp4"
            video.write_bytes(b"video")
            self.assertEqual(app.resolve_video_file_path(str(directory)), str(video))


class QueuePersistenceTests(unittest.TestCase):
    def make_job(self, **overrides):
        job = {
            "id": "job-1",
            "source_url": "https://www.douyin.com/video/123",
            "video_id": "123",
            "video_title": "Demo",
            "source_video": "",
            "project_name": "Demo",
            "ocr_lang": "ch",
            "crop_coords": None,
            "video_resolution": None,
            "preview_id": "",
            "use_saved_crop": True,
            "source_type": "douyin",
            "managed_source": False,
            "status": "queued",
            "step": "resolve",
            "error": "",
            "result": {},
            "needs_edge_login": False,
            "resume_step": "download",
            "created_at": 1,
            "started_at": None,
            "finished_at": None,
        }
        job.update(overrides)
        return job

    def test_queue_jobs_round_trip_through_the_persistence_file(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            queue_path = str(Path(temporary_dir) / "workflow_queue.json")
            app.write_queue_file(queue_path, [self.make_job()])

            restored = app.load_queue_file(queue_path)

        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["id"], "job-1")
        self.assertEqual(restored[0]["status"], "queued")

    def test_running_job_becomes_retryable_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            queue_path = str(Path(temporary_dir) / "workflow_queue.json")
            app.write_queue_file(
                queue_path,
                [self.make_job(status="running", step="ocr", started_at=10)],
            )

            restored = app.load_queue_file(queue_path)

        self.assertEqual(restored[0]["status"], "error")
        self.assertEqual(restored[0]["step"], "ocr")
        self.assertIn("App đã đóng", restored[0]["error"])

    def test_queue_mutation_rolls_back_when_persistence_fails(self):
        with app.queue_state_lock:
            original_jobs = app.copy.deepcopy(app.workflow_jobs)
            app.workflow_jobs.clear()
        try:
            with patch.object(app, "persist_queue_state_locked", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    app.enqueue_workflow("https://www.douyin.com/video/123", "ch")
            with app.queue_state_lock:
                self.assertEqual(app.workflow_jobs, [])
        finally:
            with app.queue_state_lock:
                app.workflow_jobs[:] = original_jobs

    def test_orphaned_local_video_cache_is_pruned_by_age(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            stale = Path(temporary_dir) / "stale.mp4"
            referenced = Path(temporary_dir) / "referenced.mp4"
            stale.write_bytes(b"stale")
            referenced.write_bytes(b"keep")
            old_time = app.time.time() - app.PREVIEW_TTL_SECONDS - 10
            os.utime(stale, (old_time, old_time))
            os.utime(referenced, (old_time, old_time))
            with patch.object(app, "LOCAL_VIDEO_DIR", temporary_dir):
                removed = app.prune_orphaned_local_videos([str(referenced)])

            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(referenced.exists())

    def test_corrupt_queue_is_quarantined(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            queue_path = Path(temporary_dir) / "workflow_queue.json"
            queue_path.write_text("{broken", encoding="utf-8")
            restored = app.load_queue_file(str(queue_path))
            quarantined = list(Path(temporary_dir).glob("workflow_queue.json.corrupt-*"))

        self.assertEqual(restored, [])
        self.assertEqual(len(quarantined), 1)

    def test_cli_process_never_rewrites_the_desktop_queue_file(self):
        # The CLI never loads the desktop queue, so moving a Douzy video from a CLI
        # run must not replace the saved queue with its empty in-memory list.
        with tempfile.TemporaryDirectory() as temporary_dir:
            queue_path = Path(temporary_dir) / "workflow_queue.json"
            app.write_queue_file(
                str(queue_path),
                [self.make_job(), self.make_job(id="job-2", status="error")],
            )
            original_bytes = queue_path.read_bytes()
            with (
                patch.object(app, "QUEUE_PATH", str(queue_path)),
                patch.object(app, "queue_persistence_enabled", False, create=True),
                patch.object(app, "workflow_jobs", []),
            ):
                app.replace_source_video_references(
                    str(Path(temporary_dir) / "douzy" / "video.mp4"),
                    str(Path(temporary_dir) / "project" / "video.mp4"),
                )

            self.assertEqual(queue_path.read_bytes(), original_bytes)

    def test_desktop_queue_keeps_persisting_after_it_was_loaded(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            queue_path = Path(temporary_dir) / "workflow_queue.json"
            app.write_queue_file(str(queue_path), [self.make_job()])
            with (
                patch.object(app, "QUEUE_PATH", str(queue_path)),
                patch.object(app, "LOCAL_VIDEO_DIR", str(Path(temporary_dir) / "local_videos")),
                patch.object(app, "queue_state_initialized", False),
                patch.object(app, "queue_persistence_enabled", False, create=True),
                patch.object(app, "workflow_jobs", []),
            ):
                app.initialize_queue_state()
                app.enqueue_workflow("https://www.douyin.com/video/456", "ch")
                restored = app.load_queue_file(str(queue_path))

        self.assertEqual([job["id"] for job in restored][0], "job-1")
        self.assertEqual(len(restored), 2)

    def test_unreadable_queue_file_is_kept_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            queue_path = Path(temporary_dir) / "workflow_queue.json"
            app.write_queue_file(str(queue_path), [self.make_job()])
            original_bytes = queue_path.read_bytes()
            locked = PermissionError(13, "The process cannot access the file")
            with (
                patch.object(app, "QUEUE_PATH", str(queue_path)),
                patch.object(app, "LOCAL_VIDEO_DIR", str(Path(temporary_dir) / "local_videos")),
                patch.object(app, "queue_state_initialized", False),
                patch.object(app, "queue_persistence_enabled", False),
                patch.object(app, "workflow_jobs", []),
                patch.object(app, "read_text_file", side_effect=locked, create=True),
                patch.object(app.time, "sleep"),
            ):
                with self.assertRaises(OSError):
                    app.load_queue_file(str(queue_path))
                app.initialize_queue_state()
                app.enqueue_workflow("https://www.douyin.com/video/789", "ch")

            self.assertEqual(queue_path.read_bytes(), original_bytes)
            self.assertEqual(list(Path(temporary_dir).glob("*.corrupt-*")), [])

    def test_quarantined_files_never_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            queue_path = Path(temporary_dir) / "workflow_queue.json"
            with patch.object(app.time, "time", return_value=1_700_000_000):
                for content in ("{broken-1", "{broken-2"):
                    queue_path.write_text(content, encoding="utf-8")
                    self.assertEqual(app.load_queue_file(str(queue_path)), [])
            quarantined = sorted(
                path.read_text(encoding="utf-8")
                for path in Path(temporary_dir).glob("workflow_queue.json.corrupt-*")
            )

        self.assertEqual(quarantined, ["{broken-1", "{broken-2"])

    def test_moved_project_reports_missing_files_instead_of_a_translation_hint(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            queue_path = Path(temporary_dir) / "workflow_queue.json"
            moved = Path(temporary_dir) / "moved-away"
            result = {
                "project_dir": str(moved),
                "video": str(moved / "Video.mp4"),
                "raw_srt": str(moved / "Video.raw.srt"),
                "translated_srt": str(moved / "Video.vi.srt"),
            }
            app.write_queue_file(
                str(queue_path),
                [self.make_job(status="success", step="done", result=result, result_ready=True)],
            )
            restored = app.load_queue_file(str(queue_path))[0]

        self.assertEqual(restored["status"], "error")
        self.assertEqual(restored["error_code"], "project_missing")
        self.assertIn("di chuyển", restored["error"])
        self.assertNotIn("deepseek", restored["error"].lower())
        self.assertNotIn("dịch lại", restored["error"].lower())
        self.assertEqual(app.serialize_job(restored)["error_code"], "project_missing")

    def test_invalid_translation_after_restart_resumes_from_translation(self):
        chinese_srt = (
            "1\n00:00:01,000 --> 00:00:02,000\n你好世界，这是测试字幕。\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n我们现在出发。\n"
        )
        with tempfile.TemporaryDirectory() as temporary_dir:
            project_dir = Path(temporary_dir) / "project"
            project_dir.mkdir()
            (project_dir / "Video.mp4").write_bytes(b"video")
            (project_dir / "Video.raw.srt").write_text(chinese_srt, encoding="utf-8")
            (project_dir / "Video.vi.srt").write_text(chinese_srt, encoding="utf-8")
            result = {
                "project_dir": str(project_dir),
                "video": str(project_dir / "Video.mp4"),
                "raw_srt": str(project_dir / "Video.raw.srt"),
                "translated_srt": str(project_dir / "Video.vi.srt"),
            }
            queue_path = Path(temporary_dir) / "workflow_queue.json"
            app.write_queue_file(
                str(queue_path),
                [self.make_job(status="success", step="done", result=result, result_ready=True)],
            )
            restored = app.load_queue_file(str(queue_path))[0]
            retry_step = app.get_job_retry_step(restored)

        self.assertEqual(restored["status"], "error")
        self.assertEqual(restored["error_code"], "translation_invalid")
        self.assertEqual(restored["resume_step"], "translate")
        self.assertEqual(retry_step, "translate")
        self.assertNotIn("deepseek", restored["error"].lower())


class ConfigFileSafetyTests(unittest.TestCase):
    NOTEBOOK_URL = "https://gemini.google.com/notebook/real-notebook"

    def test_config_saved_with_a_utf8_bom_is_read_normally(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            config_path.write_text(json.dumps({"notebook_url": self.NOTEBOOK_URL}), encoding="utf-8-sig")
            with patch.object(app, "CONFIG_PATH", str(config_path)):
                config = app.load_app_config()

            self.assertEqual(config["notebook_url"], self.NOTEBOOK_URL)
            self.assertEqual(list(Path(temporary_dir).glob("*.corrupt-*")), [])

    def test_config_read_retries_a_transient_lock(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            config_path.write_text("{}", encoding="utf-8")
            reads = [
                PermissionError(13, "The process cannot access the file"),
                json.dumps({"notebook_url": self.NOTEBOOK_URL}),
            ]
            with (
                patch.object(app, "CONFIG_PATH", str(config_path)),
                patch.object(app, "read_text_file", side_effect=reads, create=True),
                patch.object(app.time, "sleep"),
            ):
                config = app.load_app_config()

        self.assertEqual(config["notebook_url"], self.NOTEBOOK_URL)

    def test_locked_config_is_never_quarantined_or_overwritten_with_defaults(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            config_path.write_text(json.dumps({"notebook_url": self.NOTEBOOK_URL}), encoding="utf-8")
            original_bytes = config_path.read_bytes()
            locked = PermissionError(13, "The process cannot access the file")
            with (
                patch.object(app, "CONFIG_PATH", str(config_path)),
                patch.object(app, "read_text_file", side_effect=locked, create=True),
                patch.object(app.time, "sleep"),
                patch.dict(app.app.config, {"TESTING": True}),
            ):
                self.assertEqual(app.load_app_config()["notebook_url"], "")
                with self.assertRaises(OSError):
                    app.load_app_config(strict=True)
                response = app.app.test_client().post("/api/settings", json={"ocr_lang": "en"})

            self.assertEqual(response.status_code, 503)
            self.assertIn("error", response.get_json())
            self.assertEqual(config_path.read_bytes(), original_bytes)
            self.assertEqual(list(Path(temporary_dir).glob("*.corrupt-*")), [])


class SecurityBoundaryTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = False
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config["TESTING"] = False

    def api_headers(self, **overrides):
        headers = {app.API_TOKEN_HEADER: app.API_TOKEN}
        headers.update(overrides)
        return headers

    def test_api_requires_per_launch_token_and_local_host(self):
        self.assertEqual(self.client.get("/api/progress").status_code, 403)
        self.assertEqual(
            self.client.get(
                "/api/progress",
                headers=self.api_headers(Host="attacker.example"),
            ).status_code,
            403,
        )
        response = self.client.get("/api/progress", headers=self.api_headers())
        self.assertEqual(response.status_code, 200)

    def test_api_rejects_cross_origin_and_simple_mutation(self):
        cross_origin = self.client.get(
            "/api/progress",
            headers=self.api_headers(Origin="https://attacker.example"),
        )
        self.assertEqual(cross_origin.status_code, 403)
        simple_post = self.client.post("/api/cancel", headers=self.api_headers())
        self.assertEqual(simple_post.status_code, 415)

    def test_a_non_ascii_token_is_rejected_without_a_server_error(self):
        response = self.client.get(
            "/api/progress",
            headers=self.api_headers(**{app.API_TOKEN_HEADER: "tökén-đặc-biệt".encode("utf-8").decode("latin-1")}),
        )
        self.assertEqual(response.status_code, 403)

    def test_durable_log_keeps_one_entry_per_line(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            log_path = Path(temporary_dir) / "vietsub-studio.log"
            with patch.object(app, "LOG_PATH", str(log_path)):
                app.log_to_app("API trả lỗi\r\n2026-01-01 00:00:00 [SYSTEM] dòng giả mạo")
            lines = log_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 1)
        self.assertIn("\\n", lines[0])

    def test_unexpected_api_errors_are_returned_as_json(self):
        with patch.object(app, "progress_snapshot", side_effect=RuntimeError("boom")):
            response = self.client.get("/api/progress", headers=self.api_headers())
        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.is_json)
        self.assertIn("error", response.get_json())

    def test_oversized_upload_is_rejected_with_json(self):
        with patch.dict(app.app.config, {"MAX_CONTENT_LENGTH": 16}):
            response = self.client.post(
                "/api/previews/local",
                headers=self.api_headers(),
                data={"video": (io.BytesIO(b"x" * 64), "video.mp4")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 413)
        self.assertTrue(response.is_json)
        self.assertIn("8 GiB", response.get_json()["error"])

    def test_security_headers_are_present(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertNotIn("script-src 'self' 'unsafe-inline'", response.headers["Content-Security-Policy"])
        with self.client.get("/static/app.js") as script_response:
            self.assertEqual(script_response.status_code, 200)
        with self.client.get("/static/style.css") as style_response:
            self.assertEqual(style_response.status_code, 200)


class CliTests(unittest.TestCase):
    def test_cli_runs_directly_without_desktop_queue(self):
        result = {
            "status": "success",
            "error": "",
            "result": {
                "video": "video.mp4",
                "raw_srt": "video.raw.srt",
                "translated_srt": "video.vi.srt",
            },
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            lock_path = str(Path(temporary_dir) / "instance.lock")
            with (
                patch.object(app, "INSTANCE_LOCK_PATH", lock_path),
                patch.object(sys, "argv", ["automate.py", "--url", "https://www.douyin.com/video/1"]),
                patch.object(automate, "run_single_workflow", return_value=result) as direct_run,
                patch("builtins.print"),
            ):
                exit_code = automate.main()

            self.assertTrue(Path(lock_path).is_file())
        self.assertIsNone(app.instance_lock_handle)
        self.assertEqual(exit_code, 0)
        direct_run.assert_called_once_with("https://www.douyin.com/video/1", "ch", "")

    def run_cli_with_legacy_code_page(self, result):
        stdout_bytes = io.BytesIO()
        stderr_bytes = io.BytesIO()
        # A redirected stdout on Windows uses the ANSI code page with strict errors.
        legacy_stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252", errors="strict")
        legacy_stderr = io.TextIOWrapper(stderr_bytes, encoding="cp1252", errors="backslashreplace")
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(app, "INSTANCE_LOCK_PATH", str(Path(temporary_dir) / "instance.lock")),
                patch.object(sys, "argv", ["automate.py", "--url", "https://www.douyin.com/video/1"]),
                patch.object(sys, "stdout", legacy_stdout),
                patch.object(sys, "stderr", legacy_stderr),
                patch.object(automate, "run_single_workflow", return_value=result),
            ):
                exit_code = automate.main()
            legacy_stdout.flush()
            legacy_stderr.flush()
        return exit_code, stdout_bytes.getvalue().decode("utf-8"), stderr_bytes.getvalue().decode("utf-8")

    def test_cli_prints_vietnamese_results_through_a_legacy_code_page_pipe(self):
        exit_code, stdout, _stderr = self.run_cli_with_legacy_code_page({
            "status": "success",
            "error": "",
            "result": {
                "video": r"C:\Dự án\Video.mp4",
                "raw_srt": r"C:\Dự án\Video.raw.srt",
                "translated_srt": r"C:\Dự án\Video.vi.srt",
            },
        })

        self.assertEqual(exit_code, 0)
        self.assertIn(r"Video gốc: C:\Dự án\Video.mp4", stdout)
        self.assertIn(r"Phụ đề dịch: C:\Dự án\Video.vi.srt", stdout)

    def test_cli_errors_stay_readable_through_a_legacy_code_page_pipe(self):
        exit_code, _stdout, stderr = self.run_cli_with_legacy_code_page({
            "status": "error",
            "error": "Không tìm thấy video",
            "result": {},
        })

        self.assertEqual(exit_code, 1)
        self.assertIn("Lỗi: Không tìm thấy video", stderr)


class DesktopShortcutTests(unittest.TestCase):
    def test_packaged_app_creates_a_desktop_shortcut_to_the_current_exe(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            desktop = Path(temporary_dir) / "Desktop"
            desktop.mkdir()
            executable = Path(temporary_dir) / "VietSub Studio.exe"
            executable.write_bytes(b"exe")

            def create_fake_shortcut(_command, **kwargs):
                shortcut_path = Path(kwargs["env"]["VIETSUB_SHORTCUT_PATH"])
                shortcut_path.write_bytes(b"shortcut")
                return Mock(returncode=0, stdout="", stderr="")

            with (
                patch.object(app, "is_packaged_app", return_value=True),
                patch.object(app, "get_desktop_directory", return_value=str(desktop)),
                patch.object(app.sys, "executable", str(executable)),
                patch.object(app.subprocess, "run", side_effect=create_fake_shortcut) as run,
            ):
                shortcut_path = app.create_desktop_shortcut()

        self.assertEqual(shortcut_path, str(desktop / "VietSub Studio.lnk"))
        shortcut_env = run.call_args.kwargs["env"]
        self.assertEqual(shortcut_env["VIETSUB_SHORTCUT_TARGET"], str(executable))
        self.assertEqual(shortcut_env["VIETSUB_SHORTCUT_WORKDIR"], str(executable.parent))

    def test_initial_shortcut_is_created_only_once(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = str(Path(temporary_dir) / "app_config.json")
            with (
                patch.object(app, "CONFIG_PATH", config_path),
                patch.object(app, "is_packaged_app", return_value=True),
                patch.object(
                    app,
                    "create_desktop_shortcut",
                    return_value=str(Path(temporary_dir) / "VietSub Studio.lnk"),
                ) as create_shortcut,
            ):
                app.ensure_initial_desktop_shortcut()
                app.ensure_initial_desktop_shortcut()

                config = app.load_app_config()

        create_shortcut.assert_called_once_with()
        self.assertTrue(config["desktop_shortcut_initialized"])


class AutomaticUpdateTests(unittest.TestCase):
    def test_onedir_build_does_not_offer_in_place_update(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable = Path(temporary_dir) / "VietSub Studio.exe"
            executable.write_bytes(b"MZ")
            (Path(temporary_dir) / "_internal").mkdir()
            with (
                patch.object(app, "is_packaged_app", return_value=True),
                patch.object(app.sys, "executable", str(executable)),
            ):
                self.assertFalse(app.supports_in_place_update())

    def test_release_zip_is_verified_and_staged_next_to_the_current_exe(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            current_exe = directory / "VietSub Studio.exe"
            current_exe.write_bytes(b"MZold-version")
            release_zip = directory / "release.zip"
            with zipfile.ZipFile(release_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("VietSub Studio/VietSub Studio.exe", b"MZnew-version")
            zip_bytes = release_zip.read_bytes()
            zip_hash = hashlib.sha256(zip_bytes).hexdigest()
            zip_name, checksum_name = app.release_asset_names("9.9.9")
            checksum_bytes = f"{zip_hash.upper()}  {zip_name}\n".encode("ascii")
            base_url = "https://github.com/qvinh8726/VietSub-Studio/releases/download/v9.9.9"
            payload = {
                "tag_name": "v9.9.9",
                "assets": [
                    {
                        "name": zip_name,
                        "browser_download_url": f"{base_url}/{zip_name}",
                        "digest": f"sha256:{zip_hash}",
                        "size": len(zip_bytes),
                    },
                    {
                        "name": checksum_name,
                        "browser_download_url": f"{base_url}/{checksum_name}",
                        "size": len(checksum_bytes),
                    },
                ],
            }

            def fake_download(url, destination, _max_bytes):
                content = checksum_bytes if url.endswith(checksum_name) else zip_bytes
                Path(destination).write_bytes(content)
                return hashlib.sha256(content).hexdigest(), len(content)

            update_package = None
            try:
                with (
                    patch.object(app, "is_packaged_app", return_value=True),
                    patch.object(app.sys, "executable", str(current_exe)),
                    patch.object(app, "download_release_asset", side_effect=fake_download),
                ):
                    update_package = app.prepare_update_executable(payload)

                self.assertEqual(Path(update_package["staged_path"]).read_bytes(), b"MZnew-version")
                self.assertEqual(update_package["target_path"], str(current_exe))
                self.assertEqual(update_package["version"], "9.9.9")
            finally:
                if update_package:
                    Path(update_package["staged_path"]).unlink(missing_ok=True)
                    app.shutil.rmtree(update_package["update_dir"], ignore_errors=True)


class ApiTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        with app.queue_state_lock:
            app.workflow_jobs.clear()
            app.current_job_id = ""
            app.queue_worker_thread = None
        with app.preview_registry_lock:
            app.preview_registry.clear()
        app.cancel_event.clear()
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config["TESTING"] = False
        with app.queue_state_lock:
            app.workflow_jobs.clear()
            app.current_job_id = ""
            app.queue_worker_thread = None
        with app.preview_registry_lock:
            app.preview_registry.clear()
        app.cancel_event.clear()

    def test_rejects_invalid_process_requests(self):
        response = self.client.post("/api/process", json={"url": "https://example.test/video/1"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post("/api/process", json={"url": "https://www.douyin.com/video/1", "lang": "bad"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post("/api/settings", json={"output_dir": "relative-folder"})
        self.assertEqual(response.status_code, 400)

    def test_settings_allow_an_empty_notebook_during_first_run(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = str(Path(temporary_dir) / "app_config.json")
            with patch.object(app, "CONFIG_PATH", config_path):
                response = self.client.post("/api/settings", json={"notebook_url": ""})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(app.load_app_config()["notebook_url"], "")

    def test_settings_persist_and_validate_ocr_speed_options(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = str(Path(temporary_dir) / "app_config.json")
            with patch.object(app, "CONFIG_PATH", config_path):
                response = self.client.post(
                    "/api/settings",
                    json={"ocr_model_mode": "fast", "ocr_frames_to_skip": 2},
                )
                self.assertEqual(response.status_code, 200)
                config = app.load_app_config()
                self.assertEqual(config["ocr_model_mode"], "fast")
                self.assertEqual(config["ocr_frames_to_skip"], 2)

                settings = self.client.get("/api/settings").get_json()
                self.assertEqual(settings["ocr_model_mode"], "fast")
                self.assertEqual(settings["ocr_frames_to_skip"], 2)

                self.assertEqual(
                    self.client.post("/api/settings", json={"ocr_model_mode": "turbo"}).status_code,
                    400,
                )
                self.assertEqual(
                    self.client.post("/api/settings", json={"ocr_frames_to_skip": 99}).status_code,
                    400,
                )
                self.assertEqual(
                    self.client.post("/api/settings", json={"ocr_model_mode": []}).status_code,
                    400,
                )

    def test_settings_store_api_key_encrypted_and_never_return_it(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            with patch.object(app, "CONFIG_PATH", str(config_path)):
                response = self.client.post(
                    "/api/settings",
                    json={
                        "translation_provider": "deepseek",
                        "translation_api_base_url": "",
                        "translation_api_model": "",
                        "translation_api_key": "sk-secret-value",
                    },
                )
                settings = self.client.get("/api/settings").get_json()
                saved_text = config_path.read_text(encoding="utf-8")

                self.assertEqual(response.status_code, 200)
                self.assertNotIn("sk-secret-value", saved_text)
                self.assertEqual(settings["translation_provider"], "deepseek")
                self.assertEqual(settings["translation_api_base_url"], "https://api.deepseek.com")
                self.assertEqual(settings["translation_api_model"], "deepseek-v4-pro")
                self.assertTrue(settings["translation_api_key_set"])
                self.assertNotIn("translation_api_key", settings)

                cleared = self.client.post(
                    "/api/settings",
                    json={"translation_api_key": None},
                )
                self.assertEqual(cleared.status_code, 200)
                self.assertFalse(
                    self.client.get("/api/settings").get_json()["translation_api_key_set"]
                )

    def test_api_key_does_not_follow_a_provider_or_base_url_change(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            with patch.object(app, "CONFIG_PATH", str(config_path)):
                saved = self.client.post(
                    "/api/settings",
                    json={"translation_provider": "deepseek", "translation_api_key": "sk-deepseek"},
                )
                self.assertTrue(saved.get_json()["translation_api_key_set"])
                self.assertEqual(app.get_translation_runtime_config()["api_key"], "sk-deepseek")

                switched = self.client.post(
                    "/api/settings",
                    json={
                        "translation_provider": "custom",
                        "translation_api_base_url": "https://gateway.example.test/v1",
                        "translation_api_model": "any-model",
                    },
                )
                self.assertFalse(switched.get_json()["translation_api_key_set"])
                self.assertEqual(app.get_translation_runtime_config()["api_key"], "")
                self.assertFalse(self.client.get("/api/settings").get_json()["translation_api_key_set"])

                self.client.post(
                    "/api/settings",
                    json={"translation_provider": "deepseek", "translation_api_base_url": ""},
                )
                self.assertEqual(app.get_translation_runtime_config()["api_key"], "sk-deepseek")

    def test_a_key_saved_before_v154_stays_with_its_original_provider(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            config_path.write_text(json.dumps({
                "translation_provider": "deepseek",
                "translation_api_key_protected": app.protect_config_secret("sk-legacy"),
            }), encoding="utf-8")
            with patch.object(app, "CONFIG_PATH", str(config_path)):
                self.assertEqual(app.get_translation_runtime_config()["api_key"], "sk-legacy")
                self.client.post(
                    "/api/settings",
                    json={
                        "translation_provider": "custom",
                        "translation_api_base_url": "https://gateway.example.test/v1",
                        "translation_api_model": "any-model",
                    },
                )
                self.assertEqual(app.get_translation_runtime_config()["api_key"], "")
                self.client.post(
                    "/api/settings",
                    json={"translation_provider": "deepseek", "translation_api_base_url": ""},
                )
                self.assertEqual(app.get_translation_runtime_config()["api_key"], "sk-legacy")

    def test_open_folder_rejects_bodies_that_are_not_objects(self):
        for body in (["C:\\Videos"], {"path": ["C:\\Videos"]}, {"path": {"x": 1}}):
            with self.subTest(body=body):
                response = self.client.post("/api/open-folder", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.is_json)

    def test_saved_glossary_instructions_are_kept_after_the_default_changes(self):
        preset = app.TRANSLATION_INSTRUCTION_PRESETS["delta_force"]["instructions"]
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            config_path.write_text(
                json.dumps({"translation_instructions": preset}, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(app, "CONFIG_PATH", str(config_path)):
                settings = self.client.get("/api/settings").get_json()
                runtime = app.get_translation_runtime_config()

        self.assertEqual(settings["translation_instructions"], preset)
        self.assertEqual(runtime["translation_instructions"], preset)

    def test_settings_expose_default_and_store_translation_instructions(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            with patch.object(app, "CONFIG_PATH", str(config_path)):
                defaults = self.client.get("/api/settings").get_json()
                self.assertEqual(
                    defaults["translation_instructions"],
                    app.TRANSLATION_DEFAULT_INSTRUCTIONS,
                )
                self.assertEqual(
                    defaults["translation_default_instructions"],
                    app.TRANSLATION_DEFAULT_INSTRUCTIONS,
                )
                presets = defaults["translation_instruction_presets"]
                self.assertEqual(
                    presets["default"]["instructions"], app.TRANSLATION_DEFAULT_INSTRUCTIONS
                )
                self.assertIn("Saeed (赛伊德)", presets["delta_force"]["instructions"])
                self.assertTrue(presets["delta_force"]["label"])

                custom = "Dịch tự nhiên, câu ngắn, ưu tiên đúng sắc thái nhân vật."
                response = self.client.post(
                    "/api/settings",
                    json={"translation_instructions": custom},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    self.client.get("/api/settings").get_json()["translation_instructions"],
                    custom,
                )
                self.assertEqual(
                    app.get_translation_runtime_config()["translation_instructions"],
                    custom,
                )

                self.assertEqual(
                    self.client.post(
                        "/api/settings",
                        json={"translation_instructions": []},
                    ).status_code,
                    400,
                )

    def test_translation_api_test_endpoint_uses_saved_provider(self):
        runtime_config = {
            "provider": "openrouter",
            "label": "OpenRouter",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "deepseek/deepseek-chat",
            "api_key": "sk-test",
        }
        with (
            patch.object(app, "get_translation_runtime_config", return_value=runtime_config),
            patch.object(app, "call_openai_compatible_api", return_value="OK") as call_api,
        ):
            response = self.client.post("/api/translation/test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["provider"], "OpenRouter")
        self.assertEqual(call_api.call_args.kwargs["max_tokens"], 16)

    def test_settings_fall_back_when_saved_ocr_options_are_invalid(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "app_config.json"
            config_path.write_text(
                json.dumps({"ocr_model_mode": "turbo", "ocr_frames_to_skip": True}),
                encoding="utf-8",
            )
            with patch.object(app, "CONFIG_PATH", str(config_path)):
                response = self.client.get("/api/settings")

            self.assertEqual(response.status_code, 200)
            settings = response.get_json()
            self.assertEqual(settings["ocr_model_mode"], "auto")
            self.assertIsNone(settings["ocr_frames_to_skip"])

    def test_prefetch_context_isolates_cancel_logging_and_processes(self):
        app.workflow_context.cancel_event = app.Event()
        app.workflow_context.quiet = True
        app.workflow_context.detached = True
        try:
            app.cancel_event.set()
            app.check_cancel_requested()  # cancelling the visible job must not stop the prefetch
            app.workflow_context.cancel_event.set()
            with self.assertRaises(app.WorkflowCancelled):
                app.check_cancel_requested()

            with app.status_lock:
                logs_before = len(app.progress_status["logs"])
            app.log_to_app("prefetch chạy ngầm")
            with app.status_lock:
                self.assertEqual(len(app.progress_status["logs"]), logs_before)

            app.register_active_process("prefetch process", Mock())
            with app.active_process_lock:
                self.assertNotIn("prefetch process", app.active_processes)
        finally:
            app.workflow_context.cancel_event = None
            app.workflow_context.quiet = False
            app.workflow_context.detached = False
            app.cancel_event.clear()

    def test_prefetch_downloads_the_next_queued_video_during_ocr(self):
        job = app.enqueue_workflow("https://www.douyin.com/video/9", "ch")
        app.reset_progress("running-job")
        app.update_progress(status="running", step="ocr")
        try:
            with (
                patch.object(app, "load_app_config", return_value={
                    "notebook_url": "https://gemini.google.com/notebook/test-id",
                }),
                patch.object(app, "download_video_source", return_value={
                    "source_url": "https://www.douyin.com/video/9",
                    "video_id": "9",
                    "source_video": "cached.mp4",
                    "video_title": "Video 9",
                }) as download,
            ):
                app.run_prefetch_worker()

            download.assert_called_once()
            with app.queue_state_lock:
                stored = next(item for item in app.workflow_jobs if item["id"] == job["id"])
                self.assertEqual(stored["source_video"], "cached.mp4")
                self.assertEqual(stored["video_id"], "9")
                self.assertEqual(stored["status"], "queued")
        finally:
            app.update_progress(status="idle")
            app.prefetch_cancel_event.clear()

    def test_prefetch_ignores_prepared_and_local_queue_entries(self):
        preview_id = "prefetch-preview"
        with app.preview_registry_lock:
            app.preview_registry[preview_id] = {
                "id": preview_id,
                "created_at": app.time.time(),
                "source_url": "https://www.douyin.com/video/1",
                "video_id": "1",
                "video_title": "Demo",
                "source_video": "video.mp4",
                "crop_coords": None,
            }
        app.enqueue_workflow("https://www.douyin.com/video/1", "ch", preview_id=preview_id)
        app.reset_progress("running-job")
        app.update_progress(status="running", step="translate")
        try:
            with patch.object(app, "download_video_source") as download:
                app.run_prefetch_worker()
            download.assert_not_called()
        finally:
            app.update_progress(status="idle")
            app.prefetch_cancel_event.clear()

    def test_queue_worker_coordinates_and_restarts_the_prefetch(self):
        job = app.enqueue_workflow("https://www.douyin.com/video/5", "ch")

        def finish(video_url, *_args, **_kwargs):
            app.update_progress(status="success", step="done")
            app.workflow_lock.release()

        with (
            patch.object(app, "coordinate_prefetch_before_job") as coordinate,
            patch.object(app, "start_prefetch_thread") as start_prefetch,
            patch.object(app, "run_workflow_thread", side_effect=finish),
        ):
            app.run_queue_worker()

        coordinate.assert_called_once()
        self.assertEqual(coordinate.call_args.args[0]["id"], job["id"])
        start_prefetch.assert_called_once()

    def test_queue_waits_until_prefetch_stops_before_starting_another_download(self):
        thread = Mock()
        thread.is_alive.return_value = True
        with (
            patch.object(app, "prefetch_thread", thread),
            patch.object(app, "prefetch_job_id", "different-job"),
        ):
            app.coordinate_prefetch_before_job({"id": "next-job", "source_video": ""})

        self.assertTrue(app.prefetch_cancel_event.is_set())
        thread.join.assert_called_once_with()
        app.prefetch_cancel_event.clear()

    def test_multiple_workflows_can_be_queued_in_order(self):
        first = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        second = app.enqueue_workflow("https://www.douyin.com/video/2", "en")

        self.assertEqual([job["id"] for job in app.queue_snapshot()], [first["id"], second["id"]])
        self.assertTrue(all(job["status"] == "queued" for job in app.queue_snapshot()))

    def test_queue_revision_skips_unchanged_payloads(self):
        app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        first = self.client.get("/api/queue").get_json()
        unchanged = self.client.get(f"/api/queue?revision={first['revision']}").get_json()
        self.assertIsNone(unchanged["jobs"])

        app.enqueue_workflow("https://www.douyin.com/video/2", "ch")
        changed = self.client.get(f"/api/queue?revision={first['revision']}").get_json()
        self.assertEqual(len(changed["jobs"]), 2)
        self.assertGreater(changed["revision"], first["revision"])

    def test_preview_admission_blocks_queue_start(self):
        app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        self.assertTrue(app.preview_lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(RuntimeError, "preview"):
                app.start_workflow_queue()
        finally:
            app.preview_lock.release()

    def test_preview_crop_is_attached_to_a_queued_job(self):
        preview_id = "preview-test"
        with app.preview_registry_lock:
            app.preview_registry[preview_id] = {
                "id": preview_id,
                "created_at": app.time.time(),
                "source_url": "https://www.douyin.com/video/1",
                "video_id": "1",
                "video_title": "Demo",
                "source_video": "video.mp4",
                "crop_coords": None,
            }
        crop = {"crop_x": 0.1, "crop_y": 0.7, "crop_width": 0.8, "crop_height": 0.2}
        job = app.enqueue_workflow(
            "https://www.douyin.com/video/1",
            "ch",
            preview_id=preview_id,
            crop_coords=crop,
            use_saved_crop=False,
        )
        self.assertEqual(job["crop_coords"], crop)
        self.assertEqual(job["preview_id"], preview_id)

    def test_local_mp4_can_be_previewed_and_queued(self):
        mp4_bytes = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(app, "LOCAL_VIDEO_DIR", temporary_dir),
                patch.object(app, "get_saved_crop_coords", return_value=None),
            ):
                response = self.client.post(
                    "/api/previews/local",
                    data={"video": (io.BytesIO(mp4_bytes), "sample.mp4")},
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 201)
                preview = response.get_json()
                job_response = self.client.post(
                    "/api/queue",
                    json={
                        "url": preview["source_url"],
                        "preview_id": preview["id"],
                        "lang": "ch",
                    },
                )

                self.assertEqual(job_response.status_code, 201)
                job = job_response.get_json()
                self.assertEqual(job["source_type"], "local")
                with app.preview_registry_lock:
                    source_video = app.preview_registry[preview["id"]]["source_video"]
                self.assertEqual(Path(source_video).read_bytes(), mp4_bytes)

    def test_local_preview_rejects_non_mp4_files(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            with patch.object(app, "LOCAL_VIDEO_DIR", temporary_dir):
                response = self.client.post(
                    "/api/previews/local",
                    data={"video": (io.BytesIO(b"video"), "sample.mov")},
                    content_type="multipart/form-data",
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("MP4", response.get_json()["error"])

    def test_local_preview_rejects_invalid_mp4_content(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            with patch.object(app, "LOCAL_VIDEO_DIR", temporary_dir):
                response = self.client.post(
                    "/api/previews/local",
                    data={"video": (io.BytesIO(b"not-an-mp4"), "sample.mp4")},
                    content_type="multipart/form-data",
                )
        self.assertEqual(response.status_code, 400)
        self.assertIn("cấu trúc MP4", response.get_json()["error"])

    def test_retry_resumes_from_ocr_or_translation_when_files_exist(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            video.write_bytes(b"video")
            raw_srt.write_text(SOURCE_SRT, encoding="utf-8")

            first = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
            second = app.enqueue_workflow("https://www.douyin.com/video/2", "ch")
            with app.queue_state_lock:
                first_job = next(job for job in app.workflow_jobs if job["id"] == first["id"])
                first_job.update({
                    "status": "error",
                    "step": "ocr",
                    "result": {"video": str(video), "raw_srt": str(directory / "missing.srt")},
                })
                second_job = next(job for job in app.workflow_jobs if job["id"] == second["id"])
                second_job.update({
                    "status": "error",
                    "step": "translate",
                    "result": {"video": str(video), "raw_srt": str(raw_srt)},
                })

            retried_ocr = app.retry_queue_job(first["id"])
            retried_translation = app.retry_queue_job(second["id"])

        self.assertEqual(retried_ocr["resume_step"], "ocr")
        self.assertEqual(retried_translation["resume_step"], "translate")

    def test_retry_redownloads_douyin_but_requires_local_source(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            missing_source = str(Path(temporary_dir) / "missing-preview.mp4")
            douyin = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
            local = app.enqueue_workflow("https://www.douyin.com/video/2", "ch")
            with app.queue_state_lock:
                douyin_job = next(job for job in app.workflow_jobs if job["id"] == douyin["id"])
                douyin_job.update({"status": "error", "source_video": missing_source})
                local_job = next(job for job in app.workflow_jobs if job["id"] == local["id"])
                local_job.update({
                    "status": "error",
                    "source_type": "local",
                    "source_video": missing_source,
                })

            retried = app.retry_queue_job(douyin["id"])
            self.assertEqual(retried["resume_step"], "download")
            with app.queue_state_lock:
                persisted_douyin = next(job for job in app.workflow_jobs if job["id"] == douyin["id"])
                self.assertEqual(persisted_douyin["source_video"], "")
            with self.assertRaises(FileNotFoundError):
                app.retry_queue_job(local["id"])

    def test_edge_background_setting_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = str(Path(temporary_dir) / "app_config.json")
            with patch.object(app, "CONFIG_PATH", config_path):
                response = self.client.post(
                    "/api/settings",
                    json={"edge_background": False},
                )
                settings = self.client.get("/api/settings")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(settings.get_json()["edge_background"])

    def test_shortcut_endpoint_creates_and_records_the_desktop_link(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = str(Path(temporary_dir) / "app_config.json")
            shortcut_path = str(Path(temporary_dir) / "Desktop" / "VietSub Studio.lnk")
            with (
                patch.object(app, "CONFIG_PATH", config_path),
                patch.object(app, "create_desktop_shortcut", return_value=shortcut_path),
            ):
                response = self.client.post("/api/shortcut")
                config = app.load_app_config()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["path"], shortcut_path)
        self.assertTrue(config["desktop_shortcut_initialized"])

    def test_edge_login_failure_marks_the_current_queue_job(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            translated_srt = directory / "project.vi.srt"
            video.write_bytes(b"video")
            raw_srt.write_text(SOURCE_SRT, encoding="utf-8")
            project = {
                "project_name": "Project",
                "project_dir": str(directory),
                "video": str(video),
                "source_video": str(video),
                "raw_srt": str(raw_srt),
                "translated_srt": str(translated_srt),
            }
            queued = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
            with app.queue_state_lock:
                job = next(item for item in app.workflow_jobs if item["id"] == queued["id"])
                job.update({"status": "running", "step": "translate", "result": dict(project)})
                app.current_job_id = job["id"]

            self.assertTrue(app.workflow_lock.acquire(blocking=False))
            app.reset_progress(job["id"])
            try:
                with (
                    patch.object(app, "load_app_config", return_value={
                        "notebook_url": "https://gemini.google.com/notebook/test-id",
                        "output_dir": str(directory),
                    }),
                    patch.object(app, "write_project_manifest"),
                    patch.object(
                        app,
                        "translate_srt_via_gemini_edge",
                        side_effect=app.EdgeLoginRequired("Cần đăng nhập Edge."),
                    ),
                ):
                    app.run_workflow_thread(
                        "https://www.douyin.com/video/1",
                        "ch",
                        prepared_video={
                            "source_url": "https://www.douyin.com/video/1",
                            "video_id": "1",
                            "source_video": str(video),
                        },
                        resume_project=project,
                        resume_step="translate",
                    )
            finally:
                if app.workflow_lock.locked():
                    app.workflow_lock.release()

            with app.queue_state_lock:
                job = next(item for item in app.workflow_jobs if item["id"] == queued["id"])
                retry_step = app.serialize_job(job)["retry_step"]

        self.assertEqual(app.progress_snapshot()["status"], "error")
        self.assertTrue(job["needs_edge_login"])
        self.assertEqual(retry_step, "translate")

    def test_missing_translated_file_cannot_mark_the_workflow_successful(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            translated_srt = directory / "project.vi.srt"
            video.write_bytes(b"video")
            raw_srt.write_text(SOURCE_SRT, encoding="utf-8")
            project = {
                "project_name": "Project",
                "project_dir": str(directory),
                "video": str(video),
                "source_video": str(video),
                "raw_srt": str(raw_srt),
                "translated_srt": str(translated_srt),
            }

            self.assertTrue(app.workflow_lock.acquire(blocking=False))
            app.reset_progress("missing-translation")
            with (
                patch.object(app, "load_app_config", return_value={
                    "notebook_url": "https://gemini.google.com/notebook/test-id",
                    "output_dir": str(directory),
                }),
                patch.object(app, "write_project_manifest"),
                patch.object(
                    app,
                    "translate_srt_via_gemini_edge",
                    return_value=str(translated_srt),
                ),
            ):
                app.run_workflow_thread(
                    "https://www.douyin.com/video/1",
                    "ch",
                    prepared_video={
                        "source_url": "https://www.douyin.com/video/1",
                        "video_id": "1",
                        "source_video": str(video),
                    },
                    resume_project=project,
                    resume_step="translate",
                )

            snapshot = app.progress_snapshot()

        self.assertEqual(snapshot["status"], "error")
        self.assertFalse(snapshot["result_ready"])
        self.assertIn("sub Việt", snapshot["error"])

    def test_workflow_uses_api_translation_provider_without_notebook(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            video = directory / "project.mp4"
            raw_srt = directory / "project.raw.srt"
            translated_srt = directory / "project.vi.srt"
            video.write_bytes(b"video")
            raw_srt.write_text(SOURCE_SRT, encoding="utf-8")
            project = {
                "project_name": "Project",
                "project_dir": str(directory),
                "video": str(video),
                "source_video": str(video),
                "raw_srt": str(raw_srt),
                "translated_srt": str(translated_srt),
            }
            runtime_config = {
                "provider": "deepseek",
                "label": "DeepSeek API",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "api_key": "sk-test",
            }

            self.assertTrue(app.workflow_lock.acquire(blocking=False))
            app.reset_progress("api-translation")
            with (
                patch.object(app, "load_app_config", return_value={"output_dir": ""}),
                patch.object(app, "get_translation_runtime_config", return_value=runtime_config),
                patch.object(app, "write_project_manifest"),
                patch.object(app, "validate_project_result", return_value=True),
                patch.object(app, "finalize_project_assets", return_value=project),
                patch.object(
                    app,
                    "translate_srt_via_api",
                    return_value=str(translated_srt),
                ) as translate_api,
                patch.object(app, "translate_srt_via_gemini_edge") as translate_notebook,
            ):
                app.run_workflow_thread(
                    "https://www.douyin.com/video/1",
                    "ch",
                    prepared_video={
                        "source_url": "https://www.douyin.com/video/1",
                        "video_id": "1",
                        "source_video": str(video),
                    },
                    resume_project=project,
                    resume_step="translate",
                )

            translate_api.assert_called_once_with(
                str(raw_srt),
                runtime_config,
                output_srt=str(translated_srt),
                source_lang="ch",
            )
            translate_notebook.assert_not_called()
            self.assertEqual(app.progress_snapshot()["status"], "success")

    def test_queue_worker_runs_jobs_one_after_another(self):
        app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        app.enqueue_workflow("https://www.douyin.com/video/2", "en")
        call_order = []

        def finish_immediately(video_url, *_args, **_kwargs):
            self.assertTrue(app.workflow_lock.locked())
            call_order.append(video_url)
            app.update_progress(status="success", step="done")
            app.workflow_lock.release()

        with patch.object(app, "run_workflow_thread", side_effect=finish_immediately):
            app.run_queue_worker()

        self.assertEqual(
            call_order,
            ["https://www.douyin.com/video/1", "https://www.douyin.com/video/2"],
        )
        self.assertTrue(all(job["status"] == "success" for job in app.queue_snapshot()))

    def test_run_workflow_propagates_queue_persistence_errors(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(app, "load_app_config", return_value={
                    "notebook_url": "https://gemini.google.com/notebook/test-id",
                    "output_dir": temporary_dir,
                }),
                patch.object(
                    app,
                    "update_progress",
                    side_effect=app.QueuePersistenceError("disk full"),
                ),
            ):
                with self.assertRaisesRegex(app.QueuePersistenceError, "disk full"):
                    app.run_workflow_thread(
                        "https://www.douyin.com/video/1",
                        "ch",
                    )

    def assert_queue_worker_recovers_from_persistence_error(self, persistence_error):
        first = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        second = app.enqueue_workflow("https://www.douyin.com/video/2", "en")
        persist_calls = 0

        def fail_after_start(_jobs=None):
            nonlocal persist_calls
            persist_calls += 1
            if persist_calls == 2:
                raise persistence_error

        def fail_during_progress(*_args, **_kwargs):
            app.update_progress(status="success", step="done")

        with (
            patch.object(app, "persist_queue_state_locked", side_effect=fail_after_start),
            patch.object(app, "start_prefetch_thread"),
            patch.object(
                app,
                "run_workflow_thread",
                side_effect=fail_during_progress,
            ) as run_workflow,
        ):
            app.run_queue_worker()

        run_workflow.assert_called_once()
        jobs = {job["id"]: job for job in app.queue_snapshot()}
        self.assertEqual(jobs[first["id"]]["status"], "error")
        self.assertFalse(jobs[first["id"]]["result_ready"])
        self.assertIn("Không thể lưu trạng thái hàng đợi", jobs[first["id"]]["error"])
        self.assertEqual(jobs[second["id"]]["status"], "queued")
        self.assertEqual(app.current_job_id, "")
        self.assertIsNone(app.queue_worker_thread)
        self.assertFalse(app.workflow_lock.locked())

    def test_queue_worker_recovers_when_progress_persistence_fails(self):
        self.assert_queue_worker_recovers_from_persistence_error(OSError("disk full"))

    def test_queue_worker_recovers_when_queue_encoding_fails(self):
        self.assert_queue_worker_recovers_from_persistence_error(
            UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogate not allowed")
        )

    def test_result_folder_opens_with_the_windows_explorer_binary(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            with app.queue_state_lock:
                app.workflow_jobs.append({"id": "done", "result": {"project_dir": temporary_dir}})
            with patch.object(app.subprocess, "Popen") as popen:
                response = self.client.post("/api/open-folder", json={"path": temporary_dir})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            os.path.normcase(popen.call_args.args[0][0]),
            os.path.normcase(os.path.join(app.WINDOWS_DIRECTORY, "explorer.exe")),
        )

    def test_queue_persistence_failures_return_json_and_keep_memory_state(self):
        queued = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        finished = app.enqueue_workflow("https://www.douyin.com/video/2", "ch")
        with app.queue_state_lock:
            next(job for job in app.workflow_jobs if job["id"] == finished["id"])["status"] = "error"
        before = app.queue_snapshot()
        requests = (
            ("post", "/api/queue", {"url": "https://www.douyin.com/video/3", "lang": "ch"}),
            ("delete", f"/api/queue/{queued['id']}", None),
            ("post", f"/api/queue/{finished['id']}/retry", {}),
            ("delete", "/api/queue/finished", None),
        )
        with patch.object(app, "persist_queue_state_locked", side_effect=OSError(28, "No space left on device")):
            for method, url, body in requests:
                with self.subTest(url=url):
                    response = getattr(self.client, method)(url, json=body)
                    self.assertEqual(response.status_code, 503)
                    self.assertTrue(response.is_json)
                    self.assertIn("Không thể lưu trạng thái hàng đợi", response.get_json()["error"])
        self.assertEqual(app.queue_snapshot(), before)

    def test_process_passes_the_requested_project_name(self):
        with patch.object(app, "queue_workflow") as queue:
            response = self.client.post(
                "/api/process",
                json={
                    "url": "https://www.douyin.com/video/1",
                    "lang": "ch",
                    "name": "Video demo",
                },
            )
        self.assertEqual(response.status_code, 202)
        queue.assert_called_once_with(
            "https://www.douyin.com/video/1", "ch", "Video demo"
        )

    def test_health_reports_required_dependencies(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("ready", data)
        self.assertIn("ready_local", data)
        self.assertIn("ready_douyin", data)
        self.assertTrue(data["checks"]["videocr"]["required"])
        self.assertTrue(data["checks"]["browser"]["required"])
        self.assertFalse(data["checks"]["ffmpeg"]["required"])

    def test_api_translation_health_does_not_require_notebook_or_edge(self):
        config = {
            "translation_provider": "openrouter",
            "translation_api_base_url": "https://openrouter.ai/api/v1",
            "translation_api_model": "deepseek/deepseek-chat",
            "translation_api_key": "sk-test",
            "notebook_url": "",
        }
        with patch.object(app, "load_app_config", return_value=config):
            data = self.client.get("/api/health").get_json()

        self.assertTrue(data["checks"]["translation"]["ok"])
        self.assertFalse(data["checks"]["notebook"]["required"])
        self.assertFalse(data["checks"]["browser"]["required"])

    def test_update_endpoint_reports_a_new_release(self):
        update = {
            "current_version": "1.1.0",
            "latest_version": "1.2.0",
            "update_available": True,
            "release_name": "VietSub Studio v1.2.0",
            "release_url": "https://github.com/qvinh8726/VietSub-Studio/releases/tag/v1.2.0",
        }
        with patch.object(app, "get_update_status", return_value=update):
            response = self.client.get("/api/update")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["update_available"])

    def test_update_button_opens_the_official_release(self):
        update = {
            "release_url": "https://github.com/qvinh8726/VietSub-Studio/releases/tag/v1.2.0"
        }
        with (
            patch.object(app, "get_update_status", return_value=update),
            patch.object(app.webbrowser, "open", return_value=True) as open_browser,
        ):
            response = self.client.post("/api/update/open")
        self.assertEqual(response.status_code, 200)
        open_browser.assert_called_once_with(update["release_url"], new=2)

    def test_automatic_update_endpoint_prepares_restart_and_returns_version(self):
        update_package = {
            "version": "1.3.1",
            "target_path": r"C:\Apps\VietSub Studio.exe",
            "staged_path": r"C:\Apps\.VietSub Studio.update.exe",
            "update_dir": r"C:\Temp\VietSub-Studio-update-test",
        }
        with (
            patch.object(app, "fetch_latest_release_payload", return_value={"tag_name": "v1.3.1"}),
            patch.object(app, "prepare_update_executable", return_value=update_package),
            patch.object(app, "launch_update_helper") as launch_helper,
            patch.object(app, "schedule_app_exit") as schedule_exit,
        ):
            response = self.client.post("/api/update/install")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["version"], "1.3.1")
        launch_helper.assert_called_once_with(update_package)
        schedule_exit.assert_called_once_with()

    def test_automatic_update_waits_for_the_active_job(self):
        self.assertTrue(app.workflow_lock.acquire(blocking=False))
        try:
            response = self.client.post("/api/update/install")
        finally:
            app.workflow_lock.release()

        self.assertEqual(response.status_code, 409)
        self.assertIn("job", response.get_json()["error"])

    def test_update_reservation_blocks_reentry_and_new_queue_start(self):
        app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        self.assertTrue(app.update_install_lock.acquire(blocking=False))
        try:
            response = self.client.post("/api/update/install")
            self.assertEqual(response.status_code, 409)
            with self.assertRaisesRegex(RuntimeError, "cập nhật"):
                app.start_workflow_queue()
        finally:
            app.update_install_lock.release()

    def test_cancel_endpoint_reports_the_request(self):
        with patch.object(app, "request_workflow_cancel", return_value=True):
            response = self.client.post("/api/cancel")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["status"], "cancelling")

    def test_cancel_stops_registered_processes(self):
        process = Mock(pid=2468)
        process.poll.return_value = None
        self.assertTrue(app.workflow_lock.acquire(blocking=False))
        app.register_active_process("test process", process)
        try:
            with patch.object(app.subprocess, "run") as run:
                self.assertTrue(app.request_workflow_cancel())
            self.assertTrue(app.cancel_event.is_set())
            if os.name == "nt":
                self.assertEqual(run.call_args.args[0][1:3], ["/PID", "2468"])
            else:
                process.terminate.assert_called_once()
            process.wait.assert_called_with(timeout=10)
        finally:
            app.unregister_active_process("test process", process)
            app.cancel_event.clear()
            app.workflow_lock.release()

    def test_cancelled_workflow_has_a_distinct_status(self):
        self.assertTrue(app.workflow_lock.acquire(blocking=False))
        app.reset_progress()
        app.cancel_event.set()
        app.run_workflow_thread("https://www.douyin.com/video/1", "ch")
        snapshot = app.progress_snapshot()
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertIn("Đã huỷ", snapshot["error"])
        self.assertFalse(app.workflow_lock.locked())

    def test_short_share_link_is_resolved_and_downloaded_without_douzy(self):
        # Since v1.6.0 the share link is resolved over HTTP and the video is captured in
        # the automation browser's own profile; the Douzy sidecar is never started.
        short_url = "https://v.douyin.com/AbCdEfG/"
        downloads = {}

        def fake_edge_download(aweme_id, target_path, notebook_url, *, quiet):
            downloads.update({"aweme_id": aweme_id, "target": target_path, "quiet": quiet})
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_bytes(b"\x00\x00\x00\x18ftypmp42video")
            return {
                "source_url": "https://www.douyin.com/video/123",
                "video_id": aweme_id,
                "source_video": target_path,
                "video_title": "Test video",
                "source_type": "douyin",
            }

        self.assertTrue(app.workflow_lock.acquire(blocking=False))
        app.reset_progress()
        try:
            with (
                tempfile.TemporaryDirectory() as cache_dir,
                patch.object(app, "DOWNLOAD_DIR_OVERRIDE", cache_dir),
                patch.object(app, "load_app_config", return_value={
                    "notebook_url": "https://gemini.google.com/notebook/test-id"
                }),
                patch.object(app, "start_douzy_sidecar", side_effect=AssertionError("Douzy must not run")),
                patch.object(app.douyin_source, "resolve_short_link", return_value="123") as resolve,
                patch.object(app, "resolve_url_via_browser") as edge_resolve,
                patch.object(app, "download_douyin_via_share_page",
                             side_effect=app.VideoDownloadError("Trang chia sẻ không có dữ liệu video")),
                patch.object(app, "download_douyin_via_browser", side_effect=fake_edge_download),
                patch.object(app, "prepare_project_files", return_value={
                    "project_name": "Test video",
                    "project_dir": "project",
                    "video": "project/Test video.mp4",
                    "source_video": "video.mp4",
                    "raw_srt": "project/Test video.raw.srt",
                    "translated_srt": "project/Test video.vi.srt",
                }) as prepare_project,
                patch.object(app, "write_project_manifest"),
                patch.object(app, "validate_project_result", return_value=True),
                patch.object(app, "run_videocr", return_value="raw.srt") as run_ocr,
                patch.object(app, "translate_srt_via_gemini_edge", return_value="vi.srt") as translate,
            ):
                app.run_workflow_thread(short_url, "ch")
                expected_target = os.path.join(cache_dir, "douyin-123", "douyin-123.mp4")

            resolve.assert_called_once_with(short_url)
            edge_resolve.assert_not_called()
            self.assertEqual(downloads, {"aweme_id": "123", "target": expected_target, "quiet": False})
            # The fresh download waits in the download folder and moves into the project at the end.
            prepare_project.assert_called_once_with(
                expected_target,
                None,
                "123",
                video_title="Test video",
                output_dir="",
                defer_video_move=True,
                include_thumbnail=True,
            )
            run_ocr.assert_called_once_with(
                "project/Test video.mp4",
                ocr_lang="ch",
                output_srt="project/Test video.raw.srt",
                crop_coords=None,
                use_saved_crop=True,
                video_resolution=None,
            )
            translate.assert_called_once_with(
                "raw.srt",
                "https://gemini.google.com/notebook/test-id",
                output_srt="project/Test video.vi.srt",
                translation_instructions=app.TRANSLATION_DEFAULT_INSTRUCTIONS,
                source_lang="ch",
            )
            self.assertEqual(app.progress_snapshot()["status"], "success")
        finally:
            if app.workflow_lock.locked():
                app.workflow_lock.release()


TRANSLATED_REPLY = "```srt\n" + TRANSLATED_SRT + "```\n"


class FakeApiResponse:
    """Minimal stand-in for an HTTP response returned by the translation API opener."""

    def __init__(self, *, stream_lines=None, json_body=None, fail_on_read=None):
        content_type = "text/event-stream" if stream_lines is not None else "application/json"
        self.headers = {"Content-Type": content_type}
        self.lines = iter(stream_lines or [])
        self.json_body = json_body
        self.fail_on_read = fail_on_read
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self):
        self.closed = True

    def readline(self, *_args):
        if self.fail_on_read:
            raise self.fail_on_read
        return next(self.lines, b"")

    def read(self, *_args):
        return json.dumps(self.json_body).encode("utf-8")


def sse_line(payload):
    return f"data: {json.dumps(payload)}\n".encode("utf-8")


def complete_stream():
    return FakeApiResponse(stream_lines=[
        sse_line({"choices": [{"delta": {"content": TRANSLATED_REPLY}}]}),
        sse_line({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n",
    ])


def http_error(code, body, headers=None):
    return app.urllib.error.HTTPError(
        "https://api.example.test/v1/chat/completions",
        code,
        "error",
        headers or {},
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


class TranslationApiReliabilityTests(unittest.TestCase):
    RUNTIME = {
        "provider": "custom",
        "label": "Custom API",
        "base_url": "https://api.example.test/v1",
        "model": "test-model",
        "api_key": "sk-test",
    }

    def setUp(self):
        app.cancel_event.clear()
        self.sent_bodies = []
        self.sleeps = []

    def tearDown(self):
        app.cancel_event.clear()

    def run_chunk(self, responses, runtime=None):
        responses = iter(responses)

        def fake_open(request_object, timeout):
            self.sent_bodies.append(json.loads(request_object.data.decode("utf-8")))
            response = next(responses)
            if isinstance(response, BaseException):
                raise response
            return response

        with (
            patch.object(app.urllib.request, "urlopen", side_effect=AssertionError("network")),
            patch.object(app, "open_translation_api_request", side_effect=fake_open),
            patch.object(app.time, "sleep", side_effect=self.sleeps.append),
            patch.object(app, "log_to_app") as log,
        ):
            try:
                return app.translate_api_chunk(dict(runtime or self.RUNTIME), SOURCE_SRT, 1, 1)
            finally:
                self.logged = " ".join(str(call.args[0]) for call in log.call_args_list)

    def test_a_stream_cut_without_done_is_retried(self):
        truncated = FakeApiResponse(stream_lines=[
            sse_line({"choices": [{"delta": {"content": TRANSLATED_REPLY[:40]}}]}),
        ])
        result = self.run_chunk([truncated, complete_stream()])

        self.assertEqual(len(self.sent_bodies), 2)
        self.assertIn("Xin chao", result)

    def test_an_incomplete_read_is_retried(self):
        broken = FakeApiResponse(
            stream_lines=[],
            fail_on_read=app.http.client.IncompleteRead(b"partial"),
        )
        result = self.run_chunk([broken, complete_stream()])

        self.assertEqual(len(self.sent_bodies), 2)
        self.assertIn("The gioi", result)

    def test_a_bad_model_400_is_reported_after_one_request(self):
        error = http_error(400, {"error": {"message": "Model Not Exist"}})
        with self.assertRaisesRegex(RuntimeError, "Model Not Exist"):
            self.run_chunk([error])

        self.assertEqual(len(self.sent_bodies), 1)
        self.assertNotIn("không hỗ trợ streaming", self.logged)

    def test_a_gateway_that_rejects_streaming_falls_back_to_json_once(self):
        runtime = dict(self.RUNTIME)
        rejection = http_error(400, {"error": {"message": "stream mode is not supported"}})
        json_reply = FakeApiResponse(json_body={
            "choices": [{"message": {"content": TRANSLATED_REPLY}, "finish_reason": "stop"}],
        })
        responses = iter([rejection, json_reply, FakeApiResponse(json_body=json_reply.json_body)])

        def fake_open(request_object, timeout):
            self.sent_bodies.append(json.loads(request_object.data.decode("utf-8")))
            response = next(responses)
            if isinstance(response, BaseException):
                raise response
            return response

        with (
            patch.object(app.urllib.request, "urlopen", side_effect=AssertionError("network")),
            patch.object(app, "open_translation_api_request", side_effect=fake_open),
            patch.object(app.time, "sleep"),
            patch.object(app, "log_to_app"),
        ):
            app.translate_api_chunk(runtime, SOURCE_SRT, 1, 2)
            app.translate_api_chunk(runtime, SOURCE_SRT, 2, 2)

        self.assertEqual([body["stream"] for body in self.sent_bodies], [True, False, False])

    def test_rate_limits_wait_for_retry_after(self):
        limited = http_error(429, {"error": {"message": "rate limited"}}, {"Retry-After": "7"})
        self.run_chunk([limited, complete_stream()])

        self.assertEqual(len(self.sent_bodies), 2)
        self.assertEqual(sum(self.sleeps), 7)

    def test_unauthorized_requests_are_not_retried(self):
        with self.assertRaisesRegex(RuntimeError, "401"):
            self.run_chunk([http_error(401, {"error": {"message": "bad key"}})])
        self.assertEqual(len(self.sent_bodies), 1)

    def test_cancel_interrupts_a_slow_api_request(self):
        release = threading.Event()

        def slow_open(_request_object, timeout):
            release.wait(10)
            raise OSError("connection closed")

        threading.Timer(0.3, app.cancel_event.set).start()
        started = app.time.monotonic()
        try:
            with (
                patch.object(app, "open_translation_api_request", side_effect=slow_open),
                patch.object(app, "log_to_app"),
            ):
                with self.assertRaises(app.WorkflowCancelled):
                    app.translate_api_chunk(dict(self.RUNTIME), SOURCE_SRT, 1, 1)
        finally:
            release.set()
        self.assertLess(app.time.monotonic() - started, 3)

    def test_cancel_does_not_wait_for_a_response_whose_close_blocks(self):
        # A real HTTPResponse.close() blocks while another thread is inside read(),
        # so cancelling a stalled stream must not close it on the waiting thread.
        release = threading.Event()

        class StalledResponse:
            def close(self):
                release.wait(10)

        def stalled_stream(remember_response):
            remember_response(StalledResponse())
            release.wait(10)
            raise OSError("connection closed")

        threading.Timer(0.3, app.cancel_event.set).start()
        started = app.time.monotonic()
        try:
            with self.assertRaises(app.WorkflowCancelled):
                app.run_cancellable(stalled_stream, deadline_seconds=60, timeout_message="hết hạn")
        finally:
            release.set()
            app.cancel_event.clear()
        self.assertLess(app.time.monotonic() - started, 3)

    def test_each_api_request_has_a_deadline(self):
        release = threading.Event()

        def slow_open(_request_object, timeout):
            release.wait(10)
            raise OSError("connection closed")

        try:
            with (
                patch.object(app, "TRANSLATION_API_REQUEST_DEADLINE_SECONDS", 0.2, create=True),
                patch.object(app, "open_translation_api_request", side_effect=slow_open),
                patch.object(app.time, "sleep"),
                patch.object(app, "log_to_app"),
            ):
                with self.assertRaisesRegex(RuntimeError, "hạn chót"):
                    app.translate_api_chunk(dict(self.RUNTIME), SOURCE_SRT, 1, 1)
        finally:
            release.set()

    def test_redirects_are_refused_so_the_key_stays_with_the_configured_host(self):
        request_object = app.urllib.request.Request(
            "https://api.example.test/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
        )
        handler = app.RefuseRedirectHandler()
        with self.assertRaisesRegex(RuntimeError, "chuyển hướng"):
            handler.redirect_request(
                request_object, io.BytesIO(b""), 302, "Found", {}, "http://evil.example/collect"
            )


def numbered_srt(entries):
    return "\n\n".join(f"{index}\n{timecode}\n{text}" for index, timecode, text in entries) + "\n"


class TranslationIntegrityTests(unittest.TestCase):
    SOURCE_CHUNK = numbered_srt([
        (401, "00:50:01,000 --> 00:50:02,000", "Hello"),
        (402, "00:50:03,000 --> 00:50:04,000", "World"),
    ])

    def test_a_renumbered_chunk_gets_the_source_indexes_back(self):
        renumbered = numbered_srt([
            (1, "00:50:01,000 --> 00:50:02,000", "Xin chao"),
            (2, "00:50:03,000 --> 00:50:04,000", "The gioi"),
        ])
        normalized = app.normalize_translated_srt(self.SOURCE_CHUNK, renumbered)
        self.assertEqual([block[0] for block in app.parse_srt_blocks(normalized)], ["401", "402"])

    def test_glued_blocks_are_rejected_before_they_reach_the_cache(self):
        glued = (
            "401\n00:50:01,000 --> 00:50:02,000\nXin chao\n"
            "402\n00:50:03,000 --> 00:50:04,000\nThe gioi\n"
        )
        with self.assertRaisesRegex(ValueError, "dính"):
            app.normalize_translated_srt(self.SOURCE_CHUNK, glued)

    def test_a_missing_block_is_rejected(self):
        missing = numbered_srt([(401, "00:50:01,000 --> 00:50:02,000", "Xin chao")])
        with self.assertRaisesRegex(ValueError, "1/2"):
            app.normalize_translated_srt(self.SOURCE_CHUNK, missing)

    def test_gemini_reply_ignores_code_blocks_without_subtitles(self):
        class Locator:
            def __init__(self, texts):
                self.texts = texts

            def count(self):
                return len(self.texts)

            def all_inner_texts(self):
                return list(self.texts)

        class Reply:
            def locator(self, selector):
                return Locator([
                    "1\n00:00:01,000 --> 00:00:02,000\nXin chao",
                    "Tên riêng: Saeed",
                    "2\n00:00:03,000 --> 00:00:04,000\nThe gioi",
                ] if selector == "pre" else [])

            def inner_text(self):
                return ""

        translated = app.extract_gemini_translated_srt(Reply(), SOURCE_SRT)
        self.assertEqual([block[2] for block in app.parse_srt_blocks(translated)], ["Xin chao", "The gioi"])

    def test_a_dense_short_video_is_split_by_size(self):
        entries = []
        for index in range(1, 1321):
            start = index * 2000
            entries.append((
                index,
                f"{app.format_srt_timestamp(start)} --> {app.format_srt_timestamp(start + 1500)}",
                f"Câu thoại số {index:04d} khá dài để mô phỏng phụ đề dày đặc.",
            ))
        dense_srt = numbered_srt(entries)
        chunks, chunked = app.split_srt_for_translation(dense_srt, video_duration_seconds=44 * 60)

        self.assertTrue(chunked)
        self.assertGreaterEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertLessEqual(len(app.parse_srt_blocks(chunk)), app.TRANSLATION_CHUNK_MAX_BLOCKS)
            self.assertLessEqual(len(chunk), app.TRANSLATION_CHUNK_MAX_CHARS)
        self.assertEqual(
            [block for chunk in chunks for block in app.parse_srt_blocks(chunk)],
            app.parse_srt_blocks(dense_srt),
        )

    def test_a_small_srt_is_still_sent_in_one_request(self):
        chunks, chunked = app.split_srt_for_translation(SOURCE_SRT, video_duration_seconds=20 * 60)
        self.assertFalse(chunked)
        self.assertEqual(chunks, [SOURCE_SRT])

    def test_prompts_name_the_real_source_language(self):
        english = app.build_translation_prompt(SOURCE_SRT, "Dịch tự nhiên.", source_lang="en")
        self.assertNotIn("TIẾNG TRUNG", english)
        self.assertIn("TIẾNG ANH", english)
        chinese = app.build_translation_prompt(SOURCE_SRT, "Dịch tự nhiên.", source_lang="ch")
        self.assertIn("ĐẦU VÀO LÀ TIẾNG TRUNG", chinese)
        vietnamese = app.build_translation_prompt(SOURCE_SRT, "Dịch tự nhiên.", source_lang="vi")
        self.assertNotIn("TIẾNG TRUNG", vietnamese)
        self.assertIn("sửa lỗi", vietnamese)
        for lang in ("en", "ja", "ko", "vi"):
            with self.subTest(lang=lang):
                self.assertNotIn("Trung", app.build_translation_system_prompt(lang))
        self.assertIn("Trung sang Việt", app.build_translation_system_prompt("ch"))

    def test_api_requests_carry_the_job_source_language(self):
        captured = {}

        def fake_request(runtime_config, messages, *, stream):
            captured["messages"] = messages
            return "```srt\n" + TRANSLATED_SRT + "```"

        runtime = {"provider": "custom", "label": "Custom API", "source_lang": "en"}
        with (
            patch.object(app, "request_translation_completion", side_effect=fake_request),
            patch.object(app, "log_to_app"),
        ):
            app.translate_api_chunk(runtime, SOURCE_SRT, 1, 1)

        system_prompt, user_prompt = (message["content"] for message in captured["messages"])
        self.assertIn("tiếng Anh", system_prompt)
        self.assertNotIn("TIẾNG TRUNG", user_prompt)

    def test_cache_fingerprint_changes_with_the_source_language(self):
        runtime = {"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "m"}
        chinese = app.translation_cache_fingerprint({**runtime, "source_lang": "ch"})
        english = app.translation_cache_fingerprint({**runtime, "source_lang": "en"})
        self.assertNotEqual(chinese, english)


class ProcessLifecycleTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        with app.queue_state_lock:
            app.workflow_jobs.clear()
            app.current_job_id = ""
            app.queue_worker_thread = None
        app.cancel_event.clear()
        app.shutdown_event.clear()

    def tearDown(self):
        app.app.config["TESTING"] = False
        with app.queue_state_lock:
            app.workflow_jobs.clear()
            app.current_job_id = ""
            app.queue_worker_thread = None
        app.cancel_event.clear()
        app.shutdown_event.clear()
        app.prefetch_cancel_event.clear()
        if app.workflow_lock.locked():
            app.workflow_lock.release()

    @unittest.skipUnless(os.name == "nt", "taskkill is Windows-only")
    def test_stopping_a_process_kills_its_whole_tree(self):
        process = Mock(pid=4321)
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch.object(app.subprocess, "run") as run:
            app.stop_process(process, "VideOCR")

        command = run.call_args.args[0]
        self.assertEqual(os.path.normcase(command[0]), os.path.normcase(app.TASKKILL_EXE))
        self.assertEqual(command[1:], ["/PID", "4321", "/T", "/F"])

    def test_a_process_that_ignores_the_stop_does_not_hide_the_original_error(self):
        process = Mock(pid=4321)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("videocr", 10)
        with patch.object(app.subprocess, "run"):
            app.stop_process(process, "VideOCR")

    def test_keyboard_interrupt_still_stops_videocr(self):
        process = Mock(pid=4321)
        process.poll.return_value = None
        process.stdout = io.StringIO("")
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(app.subprocess, "Popen", return_value=process),
                patch.object(app, "check_cancel_requested", side_effect=KeyboardInterrupt),
                patch.object(app, "stop_process") as stop_process,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    app.execute_videocr_command(["videocr"], str(Path(temporary_dir) / "out.srt"))

        stop_process.assert_called_with(process, "VideOCR")

    def test_closing_the_app_stops_the_worker_before_the_next_job(self):
        first = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        second = app.enqueue_workflow("https://www.douyin.com/video/2", "ch")

        def close_app_during_first_job(*_args, **_kwargs):
            app.request_app_shutdown()
            app.update_progress(status="cancelled", error="App đang đóng.")

        with (
            patch.object(app, "start_prefetch_thread"),
            patch.object(app, "run_workflow_thread", side_effect=close_app_during_first_job) as run,
        ):
            app.run_queue_worker()

        run.assert_called_once()
        jobs = {job["id"]: job for job in app.queue_snapshot()}
        self.assertEqual(jobs[first["id"]]["status"], "cancelled")
        self.assertEqual(jobs[second["id"]]["status"], "queued")
        self.assertIsNone(app.queue_worker_thread)

    def test_workflow_steps_refuse_to_start_while_the_app_is_closing(self):
        app.shutdown_event.set()
        with self.assertRaises(app.WorkflowCancelled):
            app.check_cancel_requested()

    def test_previews_wait_for_the_queue_and_prefetch_between_jobs(self):
        alive = Mock()
        alive.is_alive.return_value = True
        with patch.object(app, "queue_worker_thread", alive):
            with self.assertRaisesRegex(RuntimeError, "hàng đợi"):
                app.create_video_preview("https://www.douyin.com/video/1")
        with patch.object(app, "prefetch_thread", alive):
            with self.assertRaisesRegex(RuntimeError, "hàng đợi"):
                app.create_video_preview("https://www.douyin.com/video/1")
        app.shutdown_event.set()
        with self.assertRaisesRegex(RuntimeError, "đang đóng"):
            app.create_video_preview("https://www.douyin.com/video/1")
        self.assertFalse(app.preview_lock.locked())

    def test_a_cancel_sent_while_the_next_job_starts_is_not_erased(self):
        job = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        seen = {}

        def cancel_while_claiming(_jobs=None):
            if app.current_job_id == job["id"]:
                seen["accepted"] = app.request_workflow_cancel()

        def run_job(*_args, **_kwargs):
            seen["cancel_set"] = app.cancel_event.is_set()
            app.update_progress(status="cancelled", error="Đã huỷ")

        app.queue_worker_thread = threading.current_thread()
        with (
            patch.object(app, "persist_queue_state_locked", side_effect=cancel_while_claiming),
            patch.object(app, "start_prefetch_thread"),
            patch.object(app, "run_workflow_thread", side_effect=run_job),
        ):
            app.run_queue_worker()

        self.assertTrue(seen.get("accepted"))
        self.assertTrue(seen.get("cancel_set"))


class DouzyPollingTests(unittest.TestCase):
    def poll_with_fake_clock(self, payloads, seconds_per_poll=60):
        clock = {"now": 1_000.0}

        def fake_sleep(seconds):
            clock["now"] += seconds

        def fake_urlopen(_request, timeout):
            clock["now"] += seconds_per_poll
            return io.BytesIO(json.dumps(next(payloads)).encode("utf-8"))

        with (
            patch.object(app.time, "monotonic", side_effect=lambda: clock["now"]),
            patch.object(app.time, "sleep", side_effect=fake_sleep),
            patch.object(app.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            return app.poll_download_job(1234, "token", "job-1")

    def test_a_long_download_that_keeps_progressing_is_not_cancelled(self):
        payloads = [
            {"status": "downloading", "progress": index, "updated_at": 5_000 + index}
            for index in range(12)
        ] + [{"status": "success", "progress": 100}]
        # Twelve one-minute polls: the old fixed limit stopped at five minutes.
        job = self.poll_with_fake_clock(iter(payloads))
        self.assertEqual(job["status"], "success")

    def test_a_stalled_download_times_out_with_a_specific_reason(self):
        payloads = (
            {
                "status": "downloading",
                "progress": 40,
                "updated_at": 5_000 + index,
                "updatedAt": f"2026-11-17T10:{index:02d}:00",
                "elapsedSeconds": index * 60,
            }
            for index in range(100)
        )
        with self.assertRaisesRegex(TimeoutError, "5 phút"):
            self.poll_with_fake_clock(payloads)

    def test_downloads_have_an_absolute_six_hour_limit(self):
        payloads = ({"status": "downloading", "progress": index} for index in range(10_000))
        with self.assertRaisesRegex(TimeoutError, "6 giờ"):
            self.poll_with_fake_clock(payloads)

    def test_sidecar_startup_never_waits_with_a_negative_timeout(self):
        process = Mock()
        process.poll.return_value = None
        process.stdout = io.StringIO("")
        readings = iter([100.0, 100.0, 200.0])

        def fake_monotonic():
            return next(readings, 200.0)

        with tempfile.TemporaryDirectory() as temporary_dir:
            sidecar = Path(temporary_dir) / "sidecar.exe"
            config = Path(temporary_dir) / "config.yml"
            sidecar.write_bytes(b"exe")
            config.write_text("path: test", encoding="utf-8")
            with (
                patch.object(app, "SIDECAR_BIN", str(sidecar)),
                patch.object(app, "DOUZY_CONFIG", str(config)),
                patch.object(app.subprocess, "Popen", return_value=process),
                patch.object(app.time, "monotonic", side_effect=fake_monotonic),
                patch.object(app, "stop_process"),
            ):
                with self.assertRaises(TimeoutError):
                    app.start_douzy_sidecar()


class TestSandboxTests(unittest.TestCase):
    def test_machine_specific_paths_point_into_the_test_sandbox(self):
        for name in (
            "CONFIG_DIR",
            "CONFIG_PATH",
            "QUEUE_PATH",
            "LOCAL_VIDEO_DIR",
            "INSTANCE_LOCK_PATH",
            "LOG_PATH",
            "VIDEOCR_CONFIG",
            "DOUZY_CONFIG",
            "DOUZY_DB",
            "SIDECAR_BIN",
            "USER_DATA_DIR_DEBUG",
        ):
            with self.subTest(name=name):
                value = getattr(app, name)
                self.assertTrue(app.path_is_within(value, _isolation.SANDBOX_DIR), f"{name} = {value}")

    def test_durable_log_is_written_inside_the_sandbox(self):
        app.log_to_app("sandbox log probe")
        self.assertTrue(app.path_is_within(app.LOG_PATH, _isolation.SANDBOX_DIR))
        with open(app.LOG_PATH, "r", encoding="utf-8") as log_file:
            self.assertIn("sandbox log probe", log_file.read())

    def test_app_imports_without_windows_profile_variables(self):
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in {"APPDATA", "LOCALAPPDATA"}
        }
        env["VIETSUB_STUDIO_CONFIG_DIR"] = os.path.join(_isolation.SANDBOX_DIR, "no-profile")
        completed = subprocess.run(
            [sys.executable, "-c", "import app; print(app.APPDATA); print(app.LOCALAPPDATA)"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        appdata, localappdata = completed.stdout.strip().splitlines()[-2:]
        home = os.path.expanduser("~")
        self.assertTrue(app.path_is_within(appdata, home), appdata)
        self.assertTrue(app.path_is_within(localappdata, home), localappdata)


if __name__ == "__main__":
    unittest.main()
