"""Phase 1 manifest + handoff regression: isolated files only, no AI/browser imports."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from . import _isolation  # noqa: F401
except ImportError:
    import _isolation  # noqa: F401

import app
from vietsub import manifest

CROP = {"crop_x": .04, "crop_y": .64, "crop_width": .92, "crop_height": .30}
RAW = "1\n00:00:00,000 --> 00:00:01,000\n你好世界\n"
VI = "1\n00:00:00,000 --> 00:00:01,000\nXin chào thế giới\n"


class ManifestFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vietsub-manifest-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = {"project_name": "Việt 字", "project_dir": str(self.root),
                        "video": str(self.root / "Việt 字.mp4"), "source_video": str(self.root / "Việt 字.mp4"),
                        "raw_srt": str(self.root / "Việt 字.raw.srt"),
                        "translated_srt": str(self.root / "Việt 字.vi.srt"),
                        "crop_coords": CROP, "video_resolution": {"width": 1920, "height": 1080}}
        self.target = Path(manifest.path_for(self.project))


class ManifestTests(ManifestFixture):
    def test_snapshot_is_pure_and_roundtrip_is_unicode_and_atomic(self):
        snapshot = manifest.snapshot(self.project)
        self.assertEqual(list(self.root.iterdir()), [])
        target, saved = manifest.save_project(self.project, video_id="123", source_url="https://example.test/video")
        self.assertEqual(target, str(self.target))
        self.assertEqual(saved["crop_box"], {"x": .04, "y": .64, "width": .92, "height": .30})
        self.assertEqual(saved["source_resolution"], {"width": 1920, "height": 1080})
        self.assertEqual(manifest.snapshot(self.project), saved)
        self.assertEqual(snapshot["project_id"], saved["project_id"])
        self.assertIn("Việt 字", self.target.read_text(encoding="utf-8"))
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_video_destination_not_cache_defines_stable_sidecar_name(self):
        self.project.update(video="C:/cache/remote.mp4", video_destination=str(self.root / "final name.mp4"))
        self.assertEqual(Path(manifest.path_for(self.project)).name, "final name.project.json")

    def test_legacy_manifest_and_publish_pack_migrate_without_rewrite(self):
        pack = self.root / "clip.publish.json"
        pack.write_text(json.dumps({"title": "Tiêu đề", "description": "Mô tả", "hashtags": ["#Việt"]}), encoding="utf-8")
        legacy = self.root / "project.json"
        legacy.write_text(json.dumps({**self.project, "publish_metadata": str(pack)}), encoding="utf-8")
        before = legacy.read_bytes()
        value = manifest.snapshot({"project_dir": str(self.root), "video": self.project["video"]})
        self.assertEqual(value["metadata"]["hashtags"], ["#Việt"])
        self.assertEqual(value["crop_box"]["y"], .64)
        self.assertEqual(legacy.read_bytes(), before)
        self.assertFalse(self.target.exists())

    def test_normalized_values_keep_other_workspace_assets_metadata_and_unknown_fields(self):
        _, value = manifest.save_project(self.project)
        value["metadata"]["title"] = "Manual title"
        value["assets"]["edited_video"] = "edited.mp4"
        value["assets"]["tts_audio"] = "voice.wav"
        value["stages"]["editor"] = {"status": "success", "render_id": "r1"}
        value["future_extension"] = {"retain": True}
        manifest.write_atomic(str(self.target), value)
        _, updated = manifest.save_project({**self.project, "edited_video": "", "tts_audio": ""}, status="processing")
        self.assertEqual(updated["metadata"]["title"], "Manual title")
        self.assertEqual(updated["assets"]["edited_video"], str(self.root / "edited.mp4"))
        self.assertEqual(updated["stages"]["editor"]["render_id"], "r1")
        self.assertTrue(updated["future_extension"]["retain"])

    def test_explicit_metadata_update_does_not_require_heavy_runtime(self):
        metadata = {"title": "New title", "description": "", "hashtags": ["#New"]}
        _, saved = manifest.save_project(self.project, metadata=metadata)
        self.assertEqual(saved["metadata"], metadata)

    def test_corrupt_or_future_version_never_overwritten(self):
        for raw in ('not JSON', '[]', '{"schema_version":99}', '{"schema_version":true}',
                    '{"nest":' * 1100 + 'null' + '}' * 1100):
            with self.subTest(raw=raw):
                self.target.write_text(raw, encoding="utf-8")
                with self.assertRaises(manifest.ManifestError):
                    manifest.save_project(self.project)
                self.assertEqual(self.target.read_text(encoding="utf-8"), raw)

    def test_failed_replace_keeps_old_manifest_and_removes_temp(self):
        manifest.save_project(self.project)
        before = self.target.read_bytes()
        with patch.object(manifest.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                manifest.save_project(self.project, status="processing")
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_invalid_crop_boolean_nonfinite_outside_bounds_and_resolution_rejected(self):
        for bad in (True, {}, {**CROP, "crop_x": True}, {**CROP, "crop_y": math.nan},
                    {**CROP, "crop_width": math.inf}, {**CROP, "crop_x": .9}, {**CROP, "crop_x": 10**400}):
            with self.subTest(bad=bad), self.assertRaises(manifest.ManifestError):
                manifest.snapshot({**self.project, "crop_coords": bad})
        for bad in (True, [], {"width": True, "height": 1080}, {"width": 0, "height": 1}):
            with self.subTest(bad=bad), self.assertRaises(manifest.ManifestError):
                manifest.snapshot({**self.project, "video_resolution": bad})
        self.assertFalse(self.target.exists())

    def test_oversized_manifest_and_path_traversal_name_rejected(self):
        self.target.write_bytes(b" " * (manifest.MAX_MANIFEST_BYTES + 1))
        with self.assertRaises(manifest.ManifestError):
            manifest.snapshot(self.project)
        with self.assertRaises(manifest.ManifestError):
            manifest.path_for({"project_dir": str(self.root), "project_name": "../outside"})

    def test_legacy_export_still_written_and_workbench_sidecar_added(self):
        app.write_project_manifest(self.project, video_id="123", source_url="local://123", status="success")
        legacy = json.loads((self.root / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(legacy["video"], self.project["video"])
        self.assertEqual(legacy["crop_coords"], CROP)
        self.assertTrue(self.target.exists())
        self.assertEqual(self.project["project_manifest"], str(self.target))

    def test_corrupt_workbench_manifest_does_not_break_legacy_vietsub_export(self):
        self.target.write_text("broken", encoding="utf-8")
        with patch.object(app, "log_to_app") as log:
            app.write_project_manifest(self.project, video_id="123", source_url="local://123", status="success")
        self.assertTrue((self.root / "project.json").exists())
        self.assertEqual(self.target.read_text(), "broken")
        log.assert_called_once()

    def test_actual_ocr_geometry_is_captured_before_settings_can_change(self):
        from threading import local
        executable = self.root / "videocr.exe"
        executable.write_bytes(b"fixture")
        with (patch.object(app, "workflow_context", local()),
              patch.object(app, "VIDEOCR_CLI", str(executable)),
              patch.object(app, "get_saved_crop_coords", return_value=CROP),
              patch.object(app, "get_video_resolution", return_value=(1920, 1080)),
              patch.object(app, "get_video_duration_seconds", return_value=None),
              patch.object(app, "execute_videocr_command") as execute,
              patch.object(app, "read_videocr_gui_settings", return_value={}),
              patch.object(app, "load_app_config", return_value={}),
              patch.object(app, "check_cancel_requested"),
              patch.object(app, "log_to_app")):
            app.run_videocr(self.project["video"], output_srt=self.project["raw_srt"])
            self.assertEqual(app.workflow_context.ocr_geometry,
                             {"crop_coords": CROP, "video_resolution": {"width": 1920, "height": 1080}})
            with patch.object(app, "get_video_resolution", return_value=None):
                app.run_videocr(self.project["video"], output_srt=self.project["raw_srt"])
            self.assertEqual(app.workflow_context.ocr_geometry,
                             {"crop_coords": None, "video_resolution": None})
            self.assertNotIn("--crop_x", execute.call_args.args[0])

    def test_workflow_persists_the_crop_used_by_ocr_not_a_later_setting(self):
        from threading import local
        Path(self.project["video"]).write_bytes(b"video")
        Path(self.project["raw_srt"]).write_text(RAW, encoding="utf-8")
        Path(self.project["translated_srt"]).write_text(VI, encoding="utf-8")
        project = {key: value for key, value in self.project.items() if key not in {"crop_coords", "video_resolution"}}
        actual = {"crop_x": .1, "crop_y": .7, "crop_width": .8, "crop_height": .2}
        def ocr(*args, **kwargs):
            app.workflow_context.ocr_geometry = {"crop_coords": actual, "video_resolution": {"width": 1280, "height": 720}}
            return self.project["raw_srt"]
        with (patch.object(app, "workflow_context", local()),
              patch.object(app, "current_job_id", ""),
              patch.object(app, "load_app_config", return_value={}),
              patch.object(app, "get_translation_runtime_config", return_value={"provider": "custom"}),
              patch.object(app, "run_videocr", side_effect=ocr),
              patch.object(app, "translate_srt_via_api", return_value=self.project["translated_srt"]),
              patch.object(app, "get_saved_crop_coords", return_value=CROP),
              patch.object(app, "check_cancel_requested"),
              patch.object(app, "update_progress"), patch.object(app, "log_to_app")):
            app.run_workflow_thread("local://old", "ch", resume_project=project, resume_step="ocr")
        value = manifest.snapshot(project)
        self.assertEqual(value["crop_box"], {"x": .1, "y": .7, "width": .8, "height": .2})
        self.assertEqual(value["source_resolution"], {"width": 1280, "height": 720})

    def test_deep_or_huge_corruption_stays_nonfatal_to_legacy_workflow(self):
        Path(self.project["video"]).write_bytes(b"video")
        Path(self.project["raw_srt"]).write_text(RAW, encoding="utf-8")
        Path(self.project["translated_srt"]).write_text(VI, encoding="utf-8")
        _, valid = manifest.save_project(self.project)
        huge = copy.deepcopy(valid)
        huge["crop_box"]["x"] = 10**400
        for raw in ('{"nest":' * 1100 + 'null' + '}' * 1100, json.dumps(huge)):
            with self.subTest(kind=raw[:20]):
                self.target.write_text(raw, encoding="utf-8")
                with (patch.object(app, "current_job_id", ""),
                      patch.object(app, "load_app_config", return_value={}),
                      patch.object(app, "get_translation_runtime_config", return_value={"provider": "custom"}),
                      patch.object(app, "translate_srt_via_api", return_value=self.project["translated_srt"]) as translate,
                      patch.object(app, "check_cancel_requested"),
                      patch.object(app, "update_progress") as progress,
                      patch.object(app, "log_to_app")):
                    app.run_workflow_thread("local://old", "ch", resume_project=self.project, resume_step="translate")
                translate.assert_called_once()
                self.assertTrue(any(call.kwargs.get("status") == "success" for call in progress.call_args_list))
                self.assertEqual(self.target.read_text(encoding="utf-8"), raw)

    def test_symlink_is_rejected_without_needing_os_link_privileges(self):
        with patch.object(manifest.os.path, "islink", side_effect=lambda path: path == str(self.root)):
            with self.assertRaises(manifest.ManifestError):
                manifest.save_project(self.project)
        self.assertFalse(self.target.exists())


class ProjectHandoffTests(ManifestFixture):
    def setUp(self):
        super().setUp()
        Path(self.project["video"]).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 30)
        Path(self.project["raw_srt"]).write_text(RAW, encoding="utf-8")
        Path(self.project["translated_srt"]).write_text(VI, encoding="utf-8")
        self.job = {"id": "job-123", "status": "success", "result_ready": True,
                    "source_type": "local", "source_video": self.project["video"],
                    "result": copy.deepcopy(self.project), "crop_coords": CROP,
                    "video_resolution": {"width": 1920, "height": 1080}}
        self.enterContext(patch.dict(app.app.config, TESTING=True))
        self.enterContext(patch.object(app, "workflow_jobs", [self.job]))
        self.enterContext(patch.object(app, "current_job_id", ""))
        self.enterContext(patch.object(app, "prefetch_job_id", ""))
        self.enterContext(patch.object(app, "log_to_app"))
        self.client = app.app.test_client()

    def test_get_is_pure_then_handoff_creates_only_sidecar_and_media_is_scoped(self):
        response = self.client.get("/api/projects/job-123")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.target.exists())
        response = self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"})
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["workspace"], "editor")
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["manifest"]["crop_box"]["x"], .04)
        self.assertTrue(self.target.exists())
        with self.client.get(payload["video_url"], headers={"Range": "bytes=0-11"}) as media:
            self.assertEqual(media.status_code, 206)
            self.assertEqual(media.get_data(), b"\x00\x00\x00\x18ftypmp42")
        self.assertEqual(self.client.get("/api/projects/job-123/video").status_code, 403)
        self.assertEqual(self.client.get(payload["video_url"].replace("job-123", "other")).status_code, 404)

    def test_post_rejects_arbitrary_path_wrong_workspace_and_malformed_payload(self):
        for body in (None, [], {}, {"workspace": "edit"}, {"workspace": []}, {"workspace": {}},
                     {"workspace": "editor", "path": "C:/secret"}):
            with self.subTest(body=body):
                response = self.client.post("/api/projects/job-123/handoff", json=body)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(self.target.exists())

    def test_only_successful_ready_jobs_can_handoff(self):
        for status, ready in (("queued", False), ("running", True), ("error", True), ("success", False)):
            self.job.update(status=status, result_ready=ready)
            self.assertEqual(self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"}).status_code, 409)
        self.assertFalse(self.target.exists())

    def test_deleted_or_moved_video_reports_readiness_without_destroying_srt(self):
        Path(self.project["video"]).unlink()
        self.job["result"]["source_video_deleted"] = True
        self.assertFalse(self.client.get("/api/projects/job-123").get_json()["ready"])
        for workspace in ("editor", "uploader"):
            self.assertEqual(self.client.post("/api/projects/job-123/handoff", json={"workspace": workspace}).status_code, 409)
        self.assertTrue(Path(self.project["translated_srt"]).exists())
        self.assertFalse(self.target.exists())

    def test_invalid_subtitle_cannot_handoff(self):
        Path(self.project["translated_srt"]).write_text("broken", encoding="utf-8")
        self.assertEqual(self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"}).status_code, 409)
        self.assertFalse(self.target.exists())

    def test_job_geometry_is_used_for_old_result(self):
        self.job["result"].pop("crop_coords")
        self.job["result"].pop("video_resolution")
        data = self.client.get("/api/projects/job-123").get_json()
        self.assertEqual(data["manifest"]["crop_box"]["y"], .64)
        self.assertEqual(data["manifest"]["source_resolution"]["width"], 1920)

    def test_saved_geometry_wins_over_legacy_queue_requested_defaults(self):
        actual = {"crop_x": .1, "crop_y": .7, "crop_width": .8, "crop_height": .2}
        manifest.save_project({**self.project, "crop_coords": actual})
        self.job["result"].pop("crop_coords")
        self.job["result"].pop("video_resolution")
        self.job["crop_coords"] = CROP
        self.job["video_resolution"] = {"width": 1280, "height": 720}
        for use_saved in (False, True):
            self.job["use_saved_crop"] = use_saved
            snapshot = self.client.get("/api/projects/job-123").get_json()["manifest"]
            self.assertEqual(snapshot["crop_box"]["y"], .7)
            self.assertEqual(snapshot["source_resolution"]["width"], 1920)
            response = self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"})
            self.assertEqual(response.status_code, 200)
            saved = json.loads(self.target.read_text(encoding="utf-8"))
            self.assertEqual(saved["crop_box"]["y"], .7)
            self.assertEqual(saved["source_resolution"]["width"], 1920)

    def test_legacy_saved_default_without_geometry_is_unknown_not_guessed(self):
        self.job["result"].pop("crop_coords")
        self.job["result"].pop("video_resolution")
        self.job["use_saved_crop"] = True
        value = self.client.get("/api/projects/job-123").get_json()["manifest"]
        self.assertIsNone(value["crop_box"])
        self.assertIsNone(value["source_resolution"])

    def test_deep_corrupt_project_endpoints_return_controlled_conflict(self):
        self.target.write_text('{"nest":' * 1100 + 'null' + '}' * 1100, encoding="utf-8")
        before = self.target.read_bytes()
        self.assertEqual(self.client.get("/api/projects/job-123").status_code, 409)
        self.assertEqual(self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"}).status_code, 409)
        self.assertEqual(self.target.read_bytes(), before)

    def test_cleanup_preserves_new_sidecar_and_old_legacy_manifest(self):
        app.write_project_manifest(self.job["result"], video_id="123", source_url="", status="success")
        response = self.client.post("/api/queue/bulk-delete", json={"ids": ["job-123"], "delete_files": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.target.exists())
        self.assertTrue((self.root / "project.json").exists())
        self.assertTrue(Path(self.project["translated_srt"]).exists())

    def test_workbench_routes_require_old_token_origin_and_media_capability(self):
        with patch.dict(app.app.config, TESTING=False):
            self.assertEqual(self.client.get("/api/projects/job-123").status_code, 403)
            self.assertEqual(self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"}).status_code, 403)
            headers = {app.API_TOKEN_HEADER: app.API_TOKEN}
            response = self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"},
                            headers={**headers, "Origin": "https://evil.test"}).status_code, 403)
            with self.client.get(response.get_json()["video_url"]) as media:
                self.assertEqual(media.status_code, 200)
            self.assertEqual(self.client.get("/api/projects/job-123/video").status_code, 403)

    def test_handoff_refuses_active_success_transition(self):
        for field in ("current_job_id", "prefetch_job_id"):
            with patch.object(app, field, "job-123"):
                response = self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"})
                self.assertEqual(response.status_code, 409)
                self.assertFalse(self.client.get("/api/projects/job-123").get_json()["ready"])
        self.assertFalse(self.target.exists())

    def test_snapshot_and_recheck_select_edited_media_only_for_uploader(self):
        edited = self.root / "edited.mp4"
        edited.write_bytes(b"edited-video")
        _, value = manifest.save_project(self.job["result"])
        value["assets"]["edited_video"] = str(edited)
        manifest.write_atomic(str(self.target), value)
        editor = self.client.get("/api/projects/job-123?workspace=editor").get_json()
        uploader = self.client.get("/api/projects/job-123?workspace=uploader").get_json()
        self.assertIn("asset=source", editor["video_url"])
        self.assertIn("asset=edited", uploader["video_url"])
        with self.client.get(uploader["video_url"]) as media:
            self.assertEqual(media.get_data(), b"edited-video")
        self.assertEqual(self.client.get("/api/projects/job-123?workspace=invalid").status_code, 409)

    def test_handoff_write_failure_keeps_existing_sidecar_and_queue(self):
        manifest.save_project(self.job["result"])
        before = self.target.read_bytes()
        with patch.object(manifest.os, "replace", side_effect=PermissionError("locked")):
            response = self.client.post("/api/projects/job-123/handoff", json={"workspace": "editor"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(len(app.workflow_jobs), 1)


if __name__ == "__main__":
    unittest.main()
