import io
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
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

    def test_desktop_folder_picker_returns_the_selected_directory(self):
        class FakeWebview:
            class FileDialog:
                FOLDER = 20

        with tempfile.TemporaryDirectory() as temporary_dir:
            api = app.DesktopApi(FakeWebview)
            api.window = Mock()
            api.window.create_file_dialog.return_value = (temporary_dir,)

            selected = api.select_output_directory(temporary_dir)

            self.assertEqual(selected, os.path.abspath(temporary_dir))
            api.window.create_file_dialog.assert_called_once_with(
                20,
                directory=os.path.abspath(temporary_dir),
                allow_multiple=False,
            )

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

    def test_translation_restores_source_timestamps_when_blocks_match(self):
        changed_timecodes = TRANSLATED_SRT.replace("00:00:04,000", "00:00:05,000")
        normalized = app.normalize_translated_srt(SOURCE_SRT, changed_timecodes)
        self.assertEqual(
            app.SRT_TIMECODE_PATTERN.findall(normalized),
            app.SRT_TIMECODE_PATTERN.findall(SOURCE_SRT),
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

    def test_local_mp4_can_be_previewed_and_queued(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(app, "LOCAL_VIDEO_DIR", temporary_dir),
                patch.object(app, "get_saved_crop_coords", return_value=None),
            ):
                response = self.client.post(
                    "/api/previews/local",
                    data={"video": (io.BytesIO(b"local-video"), "sample.mp4")},
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
                self.assertEqual(Path(source_video).read_bytes(), b"local-video")

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
                self.assertEqual(douyin_job["source_video"], "")
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
                patch.object(app, "validate_project_result", return_value=True),
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
                "video.mp4",
                None,
                "123",
                video_title="Test video",
                output_dir="",
                defer_video_move=False,
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
            )
            self.assertEqual(app.progress_snapshot()["status"], "success")
        finally:
            if app.workflow_lock.locked():
                app.workflow_lock.release()


if __name__ == "__main__":
    unittest.main()
