import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

import app


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
    def test_project_names_are_safe_and_keep_a_shared_base(self):
        self.assertEqual(app.sanitize_project_name("  CON.  "), "Video CON")
        self.assertEqual(app.sanitize_project_name("CON.txt"), "Video CON.txt")
        self.assertEqual(app.sanitize_project_name('Demo: 01 / ban?'), "Demo 01 ban")
        self.assertEqual(app.sanitize_project_name("视频 标题"), "视频 标题")
        self.assertEqual(app.sanitize_project_name("", fallback="Tên Douyin"), "Tên Douyin")
        with self.assertRaises(ValueError):
            app.validate_output_dir("relative-folder")

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

    def test_translation_must_preserve_all_timestamps(self):
        self.assertEqual(app.validate_translated_srt(SOURCE_SRT, TRANSLATED_SRT), TRANSLATED_SRT)
        invalid = TRANSLATED_SRT.replace("00:00:04,000", "00:00:05,000")
        with self.assertRaises(ValueError):
            app.validate_translated_srt(SOURCE_SRT, invalid)

    def test_translation_restores_source_timestamps_when_blocks_match(self):
        changed_timecodes = TRANSLATED_SRT.replace("00:00:04,000", "00:00:05,000")
        normalized = app.normalize_translated_srt(SOURCE_SRT, changed_timecodes)
        self.assertEqual(
            app.SRT_TIMECODE_PATTERN.findall(normalized),
            app.SRT_TIMECODE_PATTERN.findall(SOURCE_SRT),
        )


class DownloadLookupTests(unittest.TestCase):
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

    def test_multiple_workflows_can_be_queued_in_order(self):
        first = app.enqueue_workflow("https://www.douyin.com/video/1", "ch")
        second = app.enqueue_workflow("https://www.douyin.com/video/2", "en")

        self.assertEqual([job["id"] for job in app.queue_snapshot()], [first["id"], second["id"]])
        self.assertTrue(all(job["status"] == "queued" for job in app.queue_snapshot()))

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
        self.assertTrue(data["checks"]["videocr"]["required"])
        self.assertFalse(data["checks"]["edge"]["required"])

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

    def test_cancel_endpoint_reports_the_request(self):
        with patch.object(app, "request_workflow_cancel", return_value=True):
            response = self.client.post("/api/cancel")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["status"], "cancelling")

    def test_cancel_stops_registered_processes(self):
        process = Mock()
        process.poll.return_value = None
        self.assertTrue(app.workflow_lock.acquire(blocking=False))
        app.register_active_process("test process", process)
        try:
            self.assertTrue(app.request_workflow_cancel())
            self.assertTrue(app.cancel_event.is_set())
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=5)
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

    def test_short_share_link_is_sent_directly_to_douzy(self):
        short_url = "https://v.douyin.com/AbCdEfG/"
        sidecar = Mock()
        self.assertTrue(app.workflow_lock.acquire(blocking=False))
        app.reset_progress()
        try:
            with (
                patch.object(app, "load_app_config", return_value={
                    "notebook_url": "https://gemini.google.com/notebook/test-id"
                }),
                patch.object(app, "get_douzy_download_dir", return_value=""),
                patch.object(app, "start_douzy_sidecar", return_value=(sidecar, 1234, "token")),
                patch.object(app, "trigger_download", return_value="job-id") as trigger,
                patch.object(app, "poll_download_job", return_value={"skipped": 1}),
                patch.object(app, "wait_for_downloaded_video_file", return_value="video.mp4") as wait_for_video,
                patch.object(app, "get_video_title", return_value="Test video"),
                patch.object(app, "prepare_project_files", return_value={
                    "project_name": "Test video",
                    "project_dir": "project",
                    "video": "project/Test video.mp4",
                    "source_video": "video.mp4",
                    "raw_srt": "project/Test video.raw.srt",
                    "translated_srt": "project/Test video.vi.srt",
                }) as prepare_project,
                patch.object(app, "write_project_manifest"),
                patch.object(app, "run_videocr", return_value="raw.srt") as run_ocr,
                patch.object(app, "translate_srt_via_gemini_edge", return_value="vi.srt") as translate,
                patch.object(
                    app,
                    "resolve_url_via_edge",
                    return_value="https://www.douyin.com/video/123",
                ) as resolve,
            ):
                app.run_workflow_thread(short_url, "ch")

            trigger.assert_called_once_with(1234, "token", short_url, scope="single")
            resolve.assert_called_once()
            wait_for_video.assert_called_once_with(ANY, "123", allow_existing=True)
            prepare_project.assert_called_once_with(
                "video.mp4", None, "123", video_title="Test video", output_dir=""
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
            )
            self.assertEqual(app.progress_snapshot()["status"], "success")
        finally:
            if app.workflow_lock.locked():
                app.workflow_lock.release()


if __name__ == "__main__":
    unittest.main()
