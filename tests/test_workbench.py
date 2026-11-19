"""Regression coverage for the desktop workbench's additive backend contracts.

All state lives in temporary directories. The only live network fixture is the
existing localhost media server; FFprobe is mocked at its process boundary.
"""

import io
import json
import math
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    from . import _isolation  # noqa: F401  import before app
    from . import test_downloads as download_fixtures
except ImportError:
    import _isolation  # noqa: F401  unittest discover -s tests
    import test_downloads as download_fixtures

import app
from vietsub.sources import http_download, local


LAYOUT = {
    "version": 1,
    "sidebar_ratio": 350 / 1440,
    "console_ratio": 0.24,
    "console_collapsed": False,
}
METADATA = {"duration": 12.5, "frame_rate": 30000 / 1001, "bit_rate": 2400000}
CROP = {"crop_x": 0.04, "crop_y": 0.64, "crop_width": 0.92, "crop_height": 0.30}


class WorkbenchSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vietsub-workbench-settings-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config_path = self.root / "app_config.json"
        self.enterContext(patch.object(app, "CONFIG_PATH", str(self.config_path)))
        self.enterContext(patch.dict(app.app.config, {"TESTING": True}))
        self.client = app.app.test_client()

    def write_config(self, config):
        self.config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.config_path.read_bytes()

    def get_settings(self):
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_first_run_get_returns_defaults_without_creating_a_config_file(self):
        settings = self.get_settings()
        self.assertIs(settings["sakuga_vfx_enabled"], True)
        self.assertIsNone(settings["workbench_layout"])
        self.assertFalse(self.config_path.exists())

    def test_legacy_config_get_adds_defaults_without_rewriting_user_data(self):
        before = self.write_config({"notebook_url": "https://gemini.google.com/notebook/keep", "ocr_lang": "en"})
        settings = self.get_settings()
        self.assertIs(settings["sakuga_vfx_enabled"], True)
        self.assertIsNone(settings["workbench_layout"])
        self.assertEqual(settings["ocr_lang"], "en")
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_preferences_round_trip_through_post_disk_fresh_load_and_get(self):
        for enabled, collapsed in ((False, True), (True, False)):
            with self.subTest(enabled=enabled):
                layout = {**LAYOUT, "console_collapsed": collapsed}
                response = self.client.post("/api/settings", json={
                    "sakuga_vfx_enabled": enabled, "workbench_layout": layout,
                })
                self.assertEqual(response.status_code, 200)
                self.assertIs(response.get_json()["sakuga_vfx_enabled"], enabled)
                self.assertEqual(response.get_json()["workbench_layout"], layout)
                on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
                fresh = app.load_app_config(strict=True)
                settings = self.get_settings()
                for value in (on_disk, fresh, settings):
                    self.assertIs(value["sakuga_vfx_enabled"], enabled)
                    self.assertEqual(value["workbench_layout"], layout)

    def test_old_client_save_preserves_preferences_other_fields_and_secret_maps(self):
        preserved = {
            "sakuga_vfx_enabled": False,
            "workbench_layout": {**LAYOUT, "console_collapsed": True},
            "notebook_url": "https://gemini.google.com/notebook/keep",
            "crop_coords": CROP,
            "translation_api_keys_protected": {"unused-provider-slot": "opaque-api-ciphertext"},
            "source_cookies_protected": {"douyin": "opaque-cookie-ciphertext"},
            "future_client_setting": {"retained": True},
        }
        self.write_config(preserved)
        with patch.object(app, "ensure_browser_running") as launch:
            response = self.client.post("/api/settings", json={"ocr_lang": "en"})
        self.assertEqual(response.status_code, 200)
        launch.assert_not_called()
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        for field, value in preserved.items():
            self.assertEqual(saved[field], value, field)
        self.assertEqual(saved["ocr_lang"], "en")
        public = json.dumps(self.get_settings()) + response.get_data(as_text=True)
        self.assertNotIn("opaque-api-ciphertext", public)
        self.assertNotIn("opaque-cookie-ciphertext", public)
        self.assertNotIn("translation_api_keys_protected", public)

    def test_non_boolean_motion_values_reject_the_entire_update_without_disk_changes(self):
        before = self.write_config({"sakuga_vfx_enabled": False, "workbench_layout": LAYOUT, "ocr_lang": "ch"})
        for value in (None, 0, 1, 0.0, 1.0, "false", "true", [], {}):
            with self.subTest(value=value):
                response = self.client.post("/api/settings", json={"ocr_lang": "en", "sakuga_vfx_enabled": value})
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
                self.assertEqual(self.config_path.read_bytes(), before)

    def test_invalid_layout_rejects_all_fields_in_the_request_without_disk_changes(self):
        before = self.write_config({"sakuga_vfx_enabled": False, "workbench_layout": LAYOUT, "ocr_lang": "ch"})
        invalid = [False, [], {}, "layout", {**LAYOUT, "extra": 1},
                   {key: value for key, value in LAYOUT.items() if key != "console_collapsed"}]
        invalid.extend({**LAYOUT, "version": value} for value in (True, 1.0, "1", 0, 2, None))
        invalid.extend({**LAYOUT, "console_collapsed": value} for value in (0, 1, None, "false"))
        for field in ("sidebar_ratio", "console_ratio"):
            invalid.extend({**LAYOUT, field: value} for value in
                           (True, False, "0.25", None, [], {}, 0, 1, -0.1, 1.1, math.nan, math.inf, -math.inf))
        for value in invalid:
            with self.subTest(value=value):
                response = self.client.post("/api/settings", json={
                    "ocr_lang": "en", "sakuga_vfx_enabled": True, "workbench_layout": value,
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
                self.assertEqual(self.config_path.read_bytes(), before)

    def test_invalid_saved_preferences_use_safe_defaults_without_rewriting_config(self):
        for enabled, layout in (("false", []), (0, {**LAYOUT, "version": True}),
                                (None, {**LAYOUT, "sidebar_ratio": math.nan}),
                                ({}, {**LAYOUT, "console_ratio": math.inf})):
            with self.subTest(enabled=enabled, layout=layout):
                before = self.write_config({"sakuga_vfx_enabled": enabled, "workbench_layout": layout})
                settings = self.get_settings()
                self.assertIs(settings["sakuga_vfx_enabled"], True)
                self.assertIsNone(settings["workbench_layout"])
                self.assertEqual(self.config_path.read_bytes(), before)

    def test_null_layout_resets_only_layout_and_retains_motion_choice(self):
        self.write_config({"sakuga_vfx_enabled": False, "workbench_layout": LAYOUT})
        response = self.client.post("/api/settings", json={"workbench_layout": None})
        self.assertEqual(response.status_code, 200)
        settings = self.get_settings()
        self.assertIsNone(settings["workbench_layout"])
        self.assertIs(settings["sakuga_vfx_enabled"], False)
        self.assertIsNone(app.load_app_config(strict=True)["workbench_layout"])

    def test_locked_config_returns_503_instead_of_overwriting_saved_preferences(self):
        before = self.write_config({"sakuga_vfx_enabled": False, "workbench_layout": LAYOUT})
        with patch.object(app, "read_text_file_with_retry", side_effect=PermissionError("sharing violation")):
            response = self.client.post("/api/settings", json={"sakuga_vfx_enabled": True})
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.get_json())
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.config_path])

    def test_failed_atomic_replace_keeps_old_config_and_removes_the_temporary_file(self):
        before = self.write_config({"sakuga_vfx_enabled": False, "workbench_layout": LAYOUT})
        with patch.object(app.os, "replace", side_effect=PermissionError("destination locked")):
            response = self.client.post("/api/settings", json={"sakuga_vfx_enabled": True})
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.get_json())
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.config_path])

    def test_new_preferences_still_require_the_existing_api_token(self):
        before = self.write_config({"sakuga_vfx_enabled": False, "workbench_layout": LAYOUT})
        with patch.dict(app.app.config, {"TESTING": False}):
            rejected = self.client.post("/api/settings", json={"sakuga_vfx_enabled": True})
            self.assertEqual(rejected.status_code, 403)
            self.assertEqual(self.config_path.read_bytes(), before)
            accepted = self.client.post("/api/settings", json={"sakuga_vfx_enabled": True},
                                        headers={app.API_TOKEN_HEADER: app.API_TOKEN})
        self.assertEqual(accepted.status_code, 200)
        self.assertIs(app.load_app_config(strict=True)["sakuga_vfx_enabled"], True)


class VideoMetadataTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(app, "resolve_ffprobe_executable", return_value="C:/tools/ffprobe.exe"))

    def probe(self, output):
        with patch.object(app.subprocess, "check_output", return_value=json.dumps(output)):
            return app.get_video_metadata("C:/clips/视频 with spaces.mp4")

    def test_stream_values_and_fractional_fps_take_priority_over_container_values(self):
        output = {"streams": [{"duration": "12.5", "avg_frame_rate": "30000/1001",
                                "r_frame_rate": "30/1", "bit_rate": "2400000"}],
                  "format": {"duration": "20", "bit_rate": "9000000"}}
        self.assertEqual(self.probe(output), METADATA)

    def test_unknown_stream_values_fall_back_to_container_and_nominal_fps(self):
        output = {"streams": [{"duration": "N/A", "avg_frame_rate": "0/0",
                                "r_frame_rate": "24000/1001", "bit_rate": "N/A"}],
                  "format": {"duration": "9.25", "bit_rate": "1500000"}}
        self.assertEqual(self.probe(output), {"duration": 9.25, "frame_rate": 24000 / 1001, "bit_rate": 1500000})

    def test_invalid_scalars_and_fps_do_not_leak_nonfinite_or_boolean_values(self):
        invalid = (None, True, False, [], {}, -1, 0, "N/A", "NaN", "Infinity", "1e999")
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(self.probe({"streams": [{
                    "duration": value, "avg_frame_rate": value, "bit_rate": value,
                }]}))
        for value in ("24/0", "0/24", "-24/1", "24/-1", "24/", "/1", "24/1/1", "1e308/1e-308"):
            with self.subTest(fps=value):
                metadata = self.probe({"streams": [{"duration": "1", "avg_frame_rate": value}]})
                self.assertEqual(metadata, {"duration": 1.0, "frame_rate": None, "bit_rate": None})

    def test_malformed_probe_output_is_optional_metadata_not_a_preview_error(self):
        for raw in ("", "not JSON", "null", "[]", "true", "42", '{"streams": null}',
                    '{"streams": [false], "format": []}', '{"streams": {"bit_rate": 5}}'):
            with self.subTest(raw=raw), patch.object(app.subprocess, "check_output", return_value=raw):
                self.assertIsNone(app.get_video_metadata("video.mp4"))
        # Valid fields remain useful even when the other FFprobe sections are malformed.
        self.assertEqual(self.probe({"streams": "bad", "format": {"duration": "3"}}),
                         {"duration": 3.0, "frame_rate": None, "bit_rate": None})

    def test_missing_ffprobe_does_not_spawn_a_process(self):
        with (patch.object(app, "resolve_ffprobe_executable", return_value=None),
              patch.object(app.subprocess, "check_output") as process):
            self.assertIsNone(app.get_video_metadata("video.mp4"))
        process.assert_not_called()

    def test_probe_errors_are_contained_and_process_has_a_finite_timeout(self):
        for error in (FileNotFoundError("gone"), subprocess.CalledProcessError(1, "ffprobe"),
                      subprocess.TimeoutExpired("ffprobe", 15), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")):
            with self.subTest(error=type(error).__name__), patch.object(app.subprocess, "check_output", side_effect=error) as process:
                self.assertIsNone(app.get_video_metadata("C:/clips/name with spaces.mp4"))
                args, kwargs = process.call_args
                self.assertIsInstance(args[0], list)
                self.assertEqual(args[0][-1], "C:/clips/name with spaces.mp4")
                self.assertFalse(kwargs.get("shell", False))
                self.assertGreater(kwargs["timeout"], 0)
                self.assertLessEqual(kwargs["timeout"], 15)


class PreviewMetadataCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vietsub-workbench-preview-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.video = self.root / "视频 source.mp4"
        self.video.write_bytes(download_fixtures.MP4_BYTES)
        config_path = self.root / "app_config.json"
        config_path.write_text(json.dumps({"crop_coords": CROP}), encoding="utf-8")
        self.enterContext(patch.object(app, "CONFIG_PATH", str(config_path)))
        self.enterContext(patch.object(app, "LOCAL_VIDEO_DIR", str(self.root / "uploads")))
        self.enterContext(patch.object(app, "preview_registry", {}))
        self.enterContext(patch.object(app, "preview_lock", threading.Lock()))
        self.enterContext(patch.object(app, "workflow_lock", threading.Lock()))
        self.enterContext(patch.object(app, "update_install_lock", threading.Lock()))
        self.enterContext(patch.object(app, "shutdown_event", threading.Event()))
        self.enterContext(patch.object(app, "cancel_event", threading.Event()))
        self.enterContext(patch.object(app, "queue_worker_thread", None))
        self.enterContext(patch.object(app, "prefetch_thread", None))
        self.enterContext(patch.object(app, "get_video_resolution", return_value=(1920, 1080)))
        self.enterContext(patch.object(app, "workflow_context", threading.local()))
        self.enterContext(patch.object(app, "download_progress", app.idle_download_progress()))
        self.enterContext(patch.dict(app.app.config, {"TESTING": True}))
        self.client = app.app.test_client()

    def create_preview(self, source):
        if source == "native":
            return app.create_local_path_preview(str(self.video))
        if source == "upload":
            response = self.client.post("/api/previews/local", data={
                "video": (io.BytesIO(download_fixtures.MP4_BYTES), "视频 source.mp4"),
            }, content_type="multipart/form-data")
        else:
            url = "https://www.douyin.com/video/7312345678901234567"
            downloaded = {"source_url": url, "video_id": "7312345678901234567",
                          "video_title": "视频 source", "source_video": str(self.video), "source_type": "douyin"}
            with patch.object(app, "download_video_source", return_value=downloaded):
                response = self.client.post("/api/previews", json={"url": url})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def assert_cached_preview(self, source):
        with patch.object(app, "get_video_metadata", return_value=METADATA) as probe:
            payload = self.create_preview(source)
            self.assertEqual(payload["video_metadata"], METADATA)
            self.assertEqual(payload["crop_coords"], CROP)
            self.assertEqual(payload["source_type"], "douyin" if source == "remote" else "local")
            for _ in range(3):
                cached = app.get_video_preview(payload["id"])
                self.assertEqual(app.preview_payload(cached)["video_metadata"], METADATA)
                with self.client.get(payload["video_url"], headers={"Range": "bytes=0-11"}) as response:
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(response.get_data(), download_fixtures.MP4_BYTES[:12])
            probe.assert_called_once_with(cached["source_video"])
        self.assertEqual(Path(cached["source_video"]).read_bytes(), download_fixtures.MP4_BYTES)
        self.assertEqual(self.video.read_bytes(), download_fixtures.MP4_BYTES)
        self.assertEqual(cached["managed_source"], source == "upload")
        self.assertNotIn("source_video", payload)
        self.assertFalse(app.preview_lock.locked())
        if source != "upload":
            self.assertEqual(payload["video_resolution"], {"width": 1920, "height": 1080})

    def test_remote_preview_probes_once_then_serves_cached_metadata(self):
        self.assert_cached_preview("remote")

    def test_native_preview_probes_once_without_copying_the_users_video(self):
        self.assert_cached_preview("native")
        self.assertFalse((self.root / "uploads").exists())

    def test_uploaded_preview_probes_once_after_the_managed_file_is_saved(self):
        self.assert_cached_preview("upload")

    def test_probe_timeout_does_not_break_any_preview_entry_point_or_reprobe_on_read(self):
        for source in ("remote", "native", "upload"):
            with (self.subTest(source=source),
                  patch.object(app, "resolve_ffprobe_executable", return_value="ffprobe"),
                  patch.object(app.subprocess, "check_output", side_effect=subprocess.TimeoutExpired("ffprobe", 15)) as process):
                payload = self.create_preview(source)
                self.assertIsNone(payload["video_metadata"])
                for _ in range(2):
                    self.assertIsNone(app.preview_payload(app.get_video_preview(payload["id"]))["video_metadata"])
                self.assertEqual(process.call_count, 1)
                self.assertFalse(app.preview_lock.locked())
                if source != "upload":
                    self.assertEqual(payload["video_resolution"], {"width": 1920, "height": 1080})


class DownloadConnectionTests(download_fixtures.MediaServerTestCase):
    def test_parallel_transfer_reports_six_connections_and_preserves_byte_progress(self):
        data = download_fixtures.BIG_MP4_BYTES
        download_fixtures.MediaHandler.routes["/video"] = lambda handler: download_fixtures.serve_bytes(handler, data)
        counts, progress = [], []
        result = http_download.stream_download(
            [f"{self.base}/video"], self.destination, connections=6,
            on_connections=counts.append, on_progress=lambda done, total, speed: progress.append((done, total, speed)),
            progress_interval=0, validate=local.require_mp4_header,
        )
        self.assertEqual(counts, [6])
        self.assertEqual(result["connections"], 6)
        self.assertEqual(self.destination.read_bytes(), data)
        self.assertEqual(progress[-1][:2], (len(data), len(data)))
        self.assertEqual([item[0] for item in progress], sorted(item[0] for item in progress))

    def test_failed_range_transfer_reports_six_then_one_and_saves_the_complete_file(self):
        data = download_fixtures.BIG_MP4_BYTES

        def probe_only(handler):
            download_fixtures.serve_bytes(handler, data, honor_range=handler.headers.get("Range") == http_download.PROBE_RANGE)

        download_fixtures.MediaHandler.routes["/video"] = probe_only
        counts = []
        result = http_download.stream_download(
            [f"{self.base}/video"], self.destination, connections=6, on_connections=counts.append,
            retry_delays=(0, 0), validate=local.require_mp4_header,
        )
        self.assertEqual(counts, [6, 1])
        self.assertEqual(result["connections"], 1)
        self.assertEqual(self.destination.read_bytes(), data)
        self.assertEqual(list(Path(self.temporary_dir.name).iterdir()), [self.destination])

    def test_no_range_support_reports_only_one_connection(self):
        data = download_fixtures.BIG_MP4_BYTES
        download_fixtures.MediaHandler.routes["/video"] = lambda handler: download_fixtures.serve_bytes(handler, data, honor_range=False)
        counts = []
        result = http_download.stream_download(
            [f"{self.base}/video"], self.destination, on_connections=counts.append, validate=local.require_mp4_header,
        )
        self.assertEqual(counts, [1])
        self.assertEqual(result["connections"], 1)
        self.assertEqual(self.destination.read_bytes(), data)

    def test_app_download_wires_telemetry_to_real_range_workers_and_fallback(self):
        data = download_fixtures.BIG_MP4_BYTES
        observed = []

        def probe_only(handler):
            observed.append(app.download_progress_snapshot()["connections"])
            download_fixtures.serve_bytes(handler, data, honor_range=handler.headers.get("Range") == http_download.PROBE_RANGE)

        download_fixtures.MediaHandler.routes["/video"] = probe_only
        video = SimpleNamespace(play_urls=[f"{self.base}/video"])
        with (patch.object(app, "workflow_context", threading.local()),
              patch.object(app, "download_progress", app.idle_download_progress()),
              patch.object(app, "cancel_event", threading.Event()),
              patch.object(app, "shutdown_event", threading.Event()),
              patch.object(app, "log_to_app")):
            app.reset_download_progress("douyin")
            app.stream_douyin_video(video, self.destination, {}, None)
            snapshot = app.download_progress_snapshot()
        self.assertEqual(snapshot["connections"], 1)
        self.assertEqual((snapshot["downloaded"], snapshot["total"]), (len(data), len(data)))
        self.assertIn(6, observed)
        self.assertIn(1, observed)
        self.assertLess(observed.index(6), observed.index(1))
        self.assertEqual(self.destination.read_bytes(), data)

    def test_a_failing_telemetry_callback_cannot_abort_the_download(self):
        data = download_fixtures.MP4_BYTES
        download_fixtures.MediaHandler.routes["/video"] = lambda handler: download_fixtures.serve_bytes(handler, data)
        calls = []

        def broken_display(count):
            calls.append(count)
            raise RuntimeError("UI consumer disconnected")

        result = http_download.stream_download(
            [f"{self.base}/video"], self.destination, on_connections=broken_display, validate=local.require_mp4_header,
        )
        self.assertEqual(calls, [1])
        self.assertEqual(result["bytes"], len(data))
        self.assertEqual(self.destination.read_bytes(), data)


class DownloadConnectionContextTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(app, "workflow_context", threading.local()))
        self.enterContext(patch.object(app, "download_progress", app.idle_download_progress()))
        self.enterContext(patch.object(app, "log_to_app"))
        self.enterContext(patch.dict(app.app.config, {"TESTING": True}))
        self.client = app.app.test_client()

    def in_worker(self, callback, *args):
        failures = []

        def run():
            try:
                callback(*args)
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "telemetry callback did not return")
        if failures:
            raise failures[0]

    def test_connection_counts_cross_worker_threads_and_are_exposed_in_progress(self):
        app.reset_download_progress("douyin")
        self.assertIsNone(self.client.get("/api/progress").get_json()["download"]["connections"])
        callback = app.bound_download_connections_callback()
        progress, _notice = app.bound_download_callbacks()
        self.in_worker(progress, 1024, 2048, 512)
        for count in (6, 1):
            self.in_worker(callback, count)
            snapshot = self.client.get("/api/progress").get_json()["download"]
            self.assertEqual(snapshot["connections"], count)
            self.assertEqual((snapshot["downloaded"], snapshot["total"], snapshot["speed"]), (1024, 2048, 512))
        app.clear_download_progress()
        snapshot = self.client.get("/api/progress").get_json()["download"]
        self.assertIsNone(snapshot["connections"])
        self.assertFalse(snapshot["active"])

    def test_background_prefetch_cannot_replace_foreground_count_even_from_another_thread(self):
        app.reset_download_progress("douyin")
        foreground = app.bound_download_connections_callback()
        self.in_worker(foreground, 6)
        app.workflow_context.quiet = True
        background = app.bound_download_connections_callback()
        app.workflow_context.quiet = False
        self.in_worker(background, 1)
        self.assertEqual(app.download_progress_snapshot()["connections"], 6)
        # The factory binds the originating context, not a later caller's thread-local value.
        app.workflow_context.quiet = True
        self.in_worker(foreground, 1)
        self.assertEqual(app.download_progress_snapshot()["connections"], 1)

    def test_switching_to_ytdlp_clears_previous_connection_count_but_prefetch_stays_silent(self):
        cleanup = Mock()
        observed = []

        def fake_download(_url, _cache_dir, **options):
            observed.append(app.download_progress_snapshot()["connections"])
            options["on_progress"](1024, 2048, 512)
            return {"path": "unused.mp4"}

        with (tempfile.TemporaryDirectory(prefix="vietsub-workbench-ytdlp-") as cache,
              patch.object(app, "prepare_ytdlp_cookie_file", return_value=(None, cleanup)),
              patch.object(app, "resolve_ffmpeg_executable", return_value=None),
              patch.object(app, "get_js_runtime", return_value={}),
              patch.object(app.ytdlp_source, "download_with_ytdlp", side_effect=fake_download)):
            app.reset_download_progress("douyin")
            app.bound_download_connections_callback()(6)
            app.run_ytdlp_download("https://www.youtube.com/watch?v=fixture", "youtube", cache)
            self.assertEqual(observed, [None])
            self.assertIsNone(app.download_progress_snapshot()["connections"])
            self.assertEqual(app.download_progress_snapshot()["downloaded"], 1024)
            app.bound_download_connections_callback()(6)
            app.workflow_context.quiet = True
            app.run_ytdlp_download("https://www.youtube.com/watch?v=prefetch", "youtube", cache)
            self.assertEqual(observed, [None, 6])
            self.assertEqual(app.download_progress_snapshot()["connections"], 6)
        self.assertEqual(cleanup.call_count, 2)

    def test_invalid_connection_counts_clear_unknown_values_instead_of_fabricating_numbers(self):
        callback = app.bound_download_connections_callback()
        for value in (True, False, 0, -1, 1.5, "6", None, math.inf):
            with self.subTest(value=value):
                callback(6)
                self.in_worker(callback, value)
                self.assertIsNone(app.download_progress_snapshot()["connections"])


class WorkbenchStaticSecurityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(app.app.config, {"TESTING": False}))
        self.client = app.app.test_client()

    def test_scripts_fonts_and_licenses_are_served_locally_with_the_existing_csp(self):
        assets = (
            "app.js", "editor.js", "editor-THIRD-PARTY.txt", "style.css", "sakuga-vfx.js", "workbench.js", "fonts.css", "sakuga-LICENSE.txt",
            "fonts/be-vietnam-pro-regular.ttf", "fonts/be-vietnam-pro-medium.ttf",
            "fonts/be-vietnam-pro-semibold.ttf", "fonts/be-vietnam-pro-bold.ttf",
            "fonts/noto-sans-sc.ttf", "fonts/jetbrains-mono.ttf",
            "fonts/be-vietnam-pro-OFL.txt", "fonts/noto-sans-sc-OFL.txt", "fonts/jetbrains-mono-OFL.txt",
        )
        for name in assets:
            with self.subTest(asset=name), self.client.get(f"/static/{name}") as response:
                self.assertEqual(response.status_code, 200)
                self.assertGreater(response.content_length, 0)
                csp = response.headers["Content-Security-Policy"]
                self.assertIn("script-src 'self';", csp)
                self.assertIn("default-src 'self';", csp)
                self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                if name.endswith(".ttf"):
                    self.assertIn(response.get_data()[:4], (b"\x00\x01\x00\x00", b"OTTO", b"ttcf"))

    def test_traversal_and_unlisted_paths_remain_unavailable(self):
        for name in ("../app.py", "%2e%2e/app.py", "%2e%2e%5capp.py", "fonts/../../app.py",
                     "fonts/..%5c..%5capp.py", "app.js/../app.py", "app_config.json",
                     "sakuga-vfx.js.bak", "fonts/noto-sans-sc.ttf.bak", "fonts/missing.ttf"):
            with self.subTest(path=name), self.client.get(f"/static/{name}") as response:
                self.assertEqual(response.status_code, 404)

    def test_allowlist_is_not_a_broad_extension_or_directory_allowance(self):
        with tempfile.TemporaryDirectory(prefix="vietsub-workbench-assets-") as root:
            static = Path(root) / "static"
            (static / "fonts").mkdir(parents=True)
            for name in ("unlisted.js", "fonts/private.ttf", "fonts/credentials.txt"):
                (static / name).write_text("must not be exposed", encoding="utf-8")
            with patch.object(app, "RESOURCE_DIR", root):
                for name in ("unlisted.js", "fonts/private.ttf", "fonts/credentials.txt"):
                    with self.subTest(asset=name), self.client.get(f"/static/{name}") as response:
                        self.assertEqual(response.status_code, 404)
                        self.assertNotIn(b"must not be exposed", response.get_data())


if __name__ == "__main__":
    unittest.main()
