"""v1.8 destructive operations use isolated files, never a user's media."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from . import _isolation  # noqa: F401
except ImportError:
    import _isolation  # noqa: F401
import app
from vietsub import storage, publish

RAW = "1\n00:00:00,000 --> 00:00:01,000\n你好世界\n"
VI = "1\n00:00:00,000 --> 00:00:01,000\nXin chào thế giới\n"
PACK = {"title": "Xin chào thế giới", "description": "Một lời chào thân thiện.",
        "hashtags": ["#XinChào", "#CuộcSống", "#TiếngViệt"]}


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vietsub-storage-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.dict(app.app.config, TESTING=True))
        self.enterContext(patch.object(app, "workflow_jobs", []))
        self.enterContext(patch.object(app, "preview_registry", {}))
        self.enterContext(patch.object(app, "current_job_id", ""))
        self.enterContext(patch.object(app, "prefetch_job_id", ""))
        self.enterContext(patch.object(app, "CONFIG_PATH", str(self.root / "config.json")))
        self.client = app.app.test_client()
        self.job = self.make_job("one")

    def make_job(self, job_id):
        directory = self.root / job_id
        directory.mkdir()
        video = directory / "clip.mp4"
        raw, vi = directory / "clip.raw.srt", directory / "clip.vi.srt"
        video.write_bytes(b"v" * 100)
        raw.write_text(RAW, encoding="utf-8")
        vi.write_text(VI, encoding="utf-8")
        project = {"project_name": "clip", "project_dir": str(directory), "video": str(video),
                   "source_video": str(video), "video_destination": str(video),
                   "raw_srt": str(raw), "translated_srt": str(vi)}
        job = {"id": job_id, "status": "success", "source_video": str(video), "result": project,
               "result_ready": True, "source_type": "local", "created_at": 1, "finished_at": 2}
        app.workflow_jobs.append(job)
        return job

    def post(self, **kwargs):
        response = self.client.post("/api/queue/bulk-delete", json={"ids": [self.job["id"]], **kwargs})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_record_only_default_keeps_managed_video_and_subtitles(self):
        with patch.object(app, "LOCAL_VIDEO_DIR", str(self.root)):
            result = self.post()
        self.assertEqual(result["deleted_ids"], ["one"])
        self.assertEqual(result["freed_bytes"], 0)
        self.assertTrue(Path(self.job["source_video"]).exists())
        self.assertTrue(Path(self.job["result"]["translated_srt"]).exists())
        self.assertEqual(app.workflow_jobs, [])

    def test_record_only_managed_upload_survives_expiry_and_restart_pruning(self):
        video = Path(self.job["source_video"])
        os.utime(video, (1, 1))
        with patch.object(app, "LOCAL_VIDEO_DIR", str(video.parent)):
            self.post()
            app.remove_managed_source_if_unused(str(video))
            self.assertEqual(app.prune_orphaned_local_videos(), 0)
        self.assertTrue(video.exists())
        self.assertTrue(Path(str(video) + ".vietsub-keep").exists())

    def test_preview_bytes_deduplicate_paths_and_do_not_mutate(self):
        result = self.post(delete_files=True, dry_run=True, ids=["one", "one"])
        self.assertEqual(result["reclaimable_bytes"], 100)
        self.assertEqual(result["selected_count"], 1)
        self.assertTrue(Path(self.job["source_video"]).exists())
        self.assertEqual(len(app.workflow_jobs), 1)
        self.assertEqual(app.serialize_job(self.job)["disk_bytes"], 100)

    def test_default_preserves_srt_and_publish_but_deletes_video_and_temp(self):
        project = self.job["result"]
        temporary = Path(project["video"] + ".part")
        temporary.write_bytes(b"tmp")
        pack = Path(project["project_dir"]) / "clip.publish.json"
        pack.write_text("{}")
        unrelated = Path(project["project_dir"]) / "other.mp4"
        unrelated.write_bytes(b"keep")
        parts = Path(project["translated_srt"] + ".parts")
        parts.mkdir()
        chunk = parts / "part-0001-hash.srt"
        chunk.write_bytes(b"cache")
        result = self.post(delete_files=True)
        self.assertEqual(result["freed_bytes"], 108)
        for path in (project["video"], temporary, chunk):
            self.assertFalse(Path(path).exists())
        for path in (project["raw_srt"], project["translated_srt"], pack, unrelated):
            self.assertTrue(Path(path).exists())

    def test_keep_srt_false_removes_both_tracked_subtitles(self):
        expected = app.job_disk_bytes(self.job, keep_srt=False)
        result = self.post(delete_files=True, keep_srt=False)
        self.assertEqual(result["freed_bytes"], expected)
        self.assertFalse(Path(self.job["result"]["translated_srt"]).exists())
        self.assertFalse(Path(self.job["result"]["raw_srt"]).exists())

    def test_shared_job_protects_files_and_reports_zero_reclaim(self):
        other = copy.deepcopy(self.job)
        other["id"] = "two"
        other["status"] = "queued"
        app.workflow_jobs.append(other)
        self.assertEqual(self.post(delete_files=True, dry_run=True)["reclaimable_bytes"], 0)
        result = self.post(delete_files=True)
        self.assertEqual(result["freed_bytes"], 0)
        self.assertTrue(Path(other["source_video"]).exists())
        self.assertEqual([job["id"] for job in app.workflow_jobs], ["two"])

    def test_shared_selected_paths_count_once(self):
        other = copy.deepcopy(self.job)
        other["id"] = "two"
        app.workflow_jobs.append(other)
        self.assertEqual(self.post(ids=["one", "two"], delete_files=True, dry_run=True)["reclaimable_bytes"], 100)
        result = self.post(ids=["one", "two"], delete_files=True)
        self.assertEqual(result["freed_bytes"], 100)
        self.assertEqual(result["deleted_ids"], ["one", "two"])

    def test_hardlink_estimate_counts_only_last_link(self):
        video = Path(self.job["source_video"])
        outside = self.root / "external.mp4"
        try:
            os.link(video, outside)
        except OSError as error:
            self.skipTest(f"Filesystem lacks hard links: {error}")
        self.assertEqual(self.post(delete_files=True, dry_run=True)["reclaimable_bytes"], 0)
        self.job["result"]["original_source_video"] = str(outside)
        self.assertEqual(self.post(delete_files=True, dry_run=True)["reclaimable_bytes"], 100)
        self.assertEqual(self.post(delete_files=True)["freed_bytes"], 100)

    def test_running_prefetch_and_missing_ids_report_failures(self):
        for field in ("current_job_id", "prefetch_job_id"):
            with patch.object(app, field, "one"):
                result = self.post(delete_files=True)
                self.assertEqual(result["deleted_ids"], [])
                self.assertEqual(len(result["failures"]), 1)
        self.job["status"] = "running"
        result = self.post(ids=["one", "missing"], delete_files=True)
        self.assertEqual(len(result["failures"]), 2)
        self.assertTrue(Path(self.job["source_video"]).exists())

    def test_dry_run_counts_only_eligible_jobs_not_running_or_missing_ids(self):
        other = self.make_job("running")
        other["status"] = "running"
        result = self.post(ids=["one", "running", "missing"], delete_files=True, dry_run=True)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(len(result["failures"]), 2)
        self.assertTrue(Path(self.job["source_video"]).exists())

    def test_one_inaccessible_inventory_does_not_block_other_jobs(self):
        other = self.make_job("two")
        inventory = storage.inventory
        bad_path = storage.canonical(self.job["source_video"])
        def checked(paths):
            if bad_path in paths:
                raise PermissionError("inaccessible one")
            return inventory(paths)
        with patch.object(storage, "inventory", side_effect=checked):
            result = self.post(ids=["one", "two"], delete_files=True)
        self.assertEqual(result["deleted_ids"], ["two"])
        self.assertEqual(result["failures"][0]["id"], "one")
        self.assertTrue(Path(self.job["source_video"]).exists())
        self.assertFalse(Path(other["source_video"]).exists())

    def test_failed_inventory_protects_its_shared_subtitles(self):
        other = self.make_job("two")
        other["result"]["translated_srt"] = self.job["result"]["translated_srt"]
        original = storage.inventory
        bad_path = storage.canonical(self.job["source_video"])
        def checked(paths):
            if bad_path in paths:
                raise PermissionError("locked one")
            return original(paths)
        with patch.object(storage, "inventory", side_effect=checked):
            result = self.post(ids=["one", "two"], delete_files=True, keep_srt=False)
        self.assertEqual(result["deleted_ids"], ["two"])
        self.assertTrue(Path(self.job["result"]["translated_srt"]).exists())

    def test_unlink_failure_retains_record_for_retry(self):
        with patch.object(storage.os, "unlink", side_effect=PermissionError("locked video")):
            result = self.post(delete_files=True)
        self.assertEqual(result["deleted_ids"], [])
        self.assertIn("locked video", result["failures"][0]["error"])
        self.assertEqual(len(app.workflow_jobs), 1)

    def test_missing_file_is_idempotent(self):
        Path(self.job["source_video"]).unlink()
        self.assertEqual(self.post(delete_files=True)["freed_bytes"], 0)
        self.assertEqual(app.workflow_jobs, [])

    def test_persistence_failure_prevents_unlink(self):
        with patch.object(app, "persist_queue_state_locked", side_effect=OSError("disk full")):
            response = self.client.post("/api/queue/bulk-delete", json={"ids": ["one"], "delete_files": True})
        self.assertEqual(response.status_code, 503)
        self.assertTrue(Path(self.job["source_video"]).exists())
        self.assertEqual(len(app.workflow_jobs), 1)

    def test_rejects_malformed_ids_and_boolean_options(self):
        for data in ({}, {"ids": "one"}, {"ids": [1]}, {"ids": []},
                     {"ids": ["one"], "keep_srt": "false"}, {"ids": ["one"], "delete_files": 1},
                     {"ids": ["one"], "dry_run": None}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post("/api/queue/bulk-delete", json=data).status_code, 400)

    def test_symlink_never_removes_target(self):
        link = self.root / "link.mp4"
        try:
            link.symlink_to(self.job["source_video"])
        except OSError as error:
            self.skipTest(f"Symlink privileges unavailable: {error}")
        self.assertEqual(storage.inventory({str(link)}), {})
        self.assertTrue(Path(self.job["source_video"]).exists())

    def test_defaults_and_strict_setting_roundtrip(self):
        settings = self.client.get("/api/settings").get_json()
        for key in ("auto_delete_source_video", "generate_publish_metadata"):
            self.assertIs(settings[key], False)
            response = self.client.post("/api/settings", json={key: True})
            self.assertEqual(response.status_code, 200)
            self.assertIs(response.get_json()[key], True)
            self.assertIs(app.load_app_config()[key], True)
            for bad in (1, "true", None, []):
                self.assertEqual(self.client.post("/api/settings", json={key: bad}).status_code, 400)

    def test_cleanup_disabled_and_invalid_subtitles_never_delete(self):
        project = self.job["result"]
        self.assertEqual(app.auto_cleanup_source_video(project, {}), 0)
        Path(project["translated_srt"]).write_text("broken")
        with self.assertRaises(ValueError):
            app.auto_cleanup_source_video(project, {"auto_delete_source_video": True})
        self.assertTrue(Path(project["video"]).exists())

    def test_cleanup_preserves_readiness_after_reload(self):
        project = self.job["result"]
        with patch.object(app, "current_job_id", "one"):
            self.assertEqual(app.auto_cleanup_source_video(project, {"auto_delete_source_video": True}), 100)
        self.assertIs(project["source_video_deleted"], True)
        self.assertTrue(app.project_result_ready(project))
        self.assertTrue(app.serialize_job(self.job)["result_ready"])
        restored = app.normalize_persisted_job(self.job)
        self.assertEqual(restored["status"], "success")
        self.assertTrue(restored["result_ready"])
        app.write_project_manifest(project, video_id="id", source_url="", status="success")
        manifest = json.loads((Path(project["project_dir"]) / "project.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["source_video_deleted"])

    def test_cleanup_protects_other_queued_job(self):
        other = copy.deepcopy(self.job)
        other.update(id="two", status="queued")
        app.workflow_jobs.append(other)
        with patch.object(app, "current_job_id", "one"):
            self.assertEqual(app.auto_cleanup_source_video(self.job["result"], {"auto_delete_source_video": True}), 0)
        self.assertTrue(Path(other["source_video"]).exists())

    def test_publish_api_writes_valid_pack_atomically_next_to_video(self):
        with patch.object(app, "request_translation_completion", return_value=json.dumps(PACK)) as complete:
            destination = app.generate_publish_pack(self.job["result"], {"provider": "openai"})
        self.assertEqual(Path(destination).name, "clip.publish.json")
        payload = json.loads(Path(destination).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["title"], PACK["title"])
        self.assertIn("Xin chào thế giới", complete.call_args.args[1][1]["content"])
        self.assertFalse(list(Path(destination).parent.glob("*.tmp")))

    def test_publish_notebook_uses_existing_provider_and_rejects_bad_json(self):
        config = {"provider": "notebook", "notebook_url": "https://gemini.google.com/notebook/test"}
        with patch.object(publish, "request_notebook", return_value="```json\n" + json.dumps(PACK) + "\n```") as request:
            destination = app.generate_publish_pack(self.job["result"], config)
        self.assertEqual(request.call_args.args[1], config)
        before = Path(destination).read_bytes()
        with patch.object(publish, "request_notebook", return_value="not JSON"):
            with self.assertRaises(ValueError):
                app.generate_publish_pack(self.job["result"], config)
        self.assertEqual(Path(destination).read_bytes(), before)

    def test_workflow_runs_pack_then_cleanup_only_after_valid_translation(self):
        project = self.job["result"]
        events = []
        original_cleanup = app.auto_cleanup_source_video
        def cleanup(result, config):
            events.append("cleanup")
            return original_cleanup(result, config)
        def pack(result, config):
            events.append("publish")
            self.assertTrue(Path(result["video"]).exists())
        app.reset_progress("one")
        with (
            patch.object(app, "current_job_id", "one"),
            patch.object(app, "load_app_config", return_value={"auto_delete_source_video": True,
                         "generate_publish_metadata": True, "output_dir": str(self.root)}),
            patch.object(app, "get_translation_runtime_config", return_value={"provider": "openai"}),
            patch.object(app, "translate_srt_via_api", return_value=project["translated_srt"]),
            patch.object(app, "generate_publish_pack", side_effect=pack),
            patch.object(app, "auto_cleanup_source_video", side_effect=cleanup),
        ):
            app.run_workflow_thread("https://www.douyin.com/video/1", "ch",
                                    resume_project=project, resume_step="translate")
        self.assertEqual(events, ["publish", "cleanup"])
        self.assertEqual(app.progress_snapshot()["status"], "success")
        self.assertTrue(app.progress_snapshot()["result_ready"])
        self.assertFalse(Path(project["video"]).exists())

    def test_workflow_translation_failure_never_cleans_source(self):
        project = self.job["result"]
        app.reset_progress("one")
        with (
            patch.object(app, "current_job_id", "one"),
            patch.object(app, "load_app_config", return_value={"auto_delete_source_video": True,
                         "generate_publish_metadata": True}),
            patch.object(app, "get_translation_runtime_config", return_value={"provider": "openai"}),
            patch.object(app, "translate_srt_via_api", side_effect=RuntimeError("API failed")),
            patch.object(app, "auto_cleanup_source_video") as cleanup,
            patch.object(app, "generate_publish_pack") as pack,
        ):
            app.run_workflow_thread("https://www.douyin.com/video/1", "ch",
                                    resume_project=project, resume_step="translate")
        cleanup.assert_not_called()
        pack.assert_not_called()
        self.assertEqual(app.progress_snapshot()["status"], "error")
        self.assertTrue(Path(project["video"]).exists())

    def test_multiple_local_uploads_include_mkv_and_enqueue_in_order(self):
        import io
        from threading import Lock
        with (
            patch.object(app, "workflow_lock", Lock()),
            patch.object(app, "queue_worker_active", return_value=False),
            patch.object(app, "prefetch_active", return_value=False),
            patch.object(app, "LOCAL_VIDEO_DIR", str(self.root / "uploads")),
            patch.object(app, "get_video_metadata", return_value=None),
            patch.object(app, "get_video_resolution", return_value=(1280, 720)),
            patch.object(app, "get_saved_crop_coords", return_value=None),
        ):
            ids = []
            for filename, data in (("first.mp4", b"\x00\x00\x00\x18ftypmp42" + b"x" * 20),
                                   ("second.mkv", b"\x1a\x45\xdf\xa3" + b"x" * 20)):
                response = self.client.post('/api/previews/local', data={"video": (io.BytesIO(data), filename)})
                self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
                preview = response.get_json()
                stored = app.preview_registry[preview["id"]]["source_video"]
                self.assertTrue(stored.endswith(Path(filename).suffix))
                self.assertEqual(Path(stored).read_bytes(), data)
                queued = self.client.post('/api/queue', json={"url": preview["source_url"], "preview_id": preview["id"]})
                self.assertEqual(queued.status_code, 201)
                ids.append(queued.get_json()["id"])
            self.assertEqual([job["id"] for job in app.workflow_jobs[-2:]], ids)
            invalid = self.client.post('/api/previews/local', data={"video": (io.BytesIO(b"bad"), "bad.mkv")})
            self.assertEqual(invalid.status_code, 400)

    def test_publish_prompt_bound_and_payload_validation(self):
        self.assertLess(len(publish.build_prompt("a" * 100000)), 26000)
        for invalid in ([], {}, {**PACK, "title": ""}, {**PACK, "hashtags": "#bad"},
                        {**PACK, "hashtags": ["#has space", "#a", "#b"]}, {**PACK, "title": "你好"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                publish.parse_response(json.dumps(invalid))


if __name__ == "__main__":
    unittest.main()
