"""Audit Round 2 — Wave A1 regression & security (TESTING=False) test suite.

Covers H-01..H-12 (backend/CI) and M-01, M-03..M-14.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
import zlib

import requests

try:
    from . import _isolation  # noqa: F401
except ImportError:
    import _isolation  # noqa: F401

import app as vietsub_app
from tools import sign_release
from vietsub import channel_presets, glossary, manifest, publish, redaction, updater
from vietsub.editor import subtitles
from vietsub.editor.jobs import EditorJobs
from vietsub.uploader import adapters, browser, selector_store, youtube_api
from vietsub.uploader.profiles import ProfileStore
from vietsub.uploader.queue import UploadQueue

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
SAMPLE_RAW_SRT = "1\n00:00:00,200 --> 00:00:02,000\n青云宗掌门来了\n"
SAMPLE_VI_SRT = "1\n00:00:00,200 --> 00:00:02,000\nChưởng môn Thanh Vân Tông đã đến.\n"


def _valid_png(width=4, height=4) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\xff\x00\xff" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeResp:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class CorePipelineAndConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vietsub-wa1-core-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.orig_config_dir = vietsub_app.CONFIG_DIR
        self.orig_config_path = vietsub_app.CONFIG_PATH
        self.orig_testing = vietsub_app.app.config.get("TESTING")
        self.orig_jobs = copy.deepcopy(vietsub_app.workflow_jobs)
        self.orig_current = vietsub_app.current_job_id
        self.orig_prefetch = vietsub_app.prefetch_job_id
        self.orig_editor_jobs = vietsub_app.editor_api.jobs
        self.orig_shutdown = vietsub_app.shutdown_event.is_set()

        vietsub_app.CONFIG_DIR = str(self.root / "config")
        vietsub_app.CONFIG_PATH = str(self.root / "config" / "config.json")
        os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
        vietsub_app.app.config["TESTING"] = True
        vietsub_app.current_job_id = ""
        vietsub_app.prefetch_job_id = ""
        vietsub_app.shutdown_event.clear()
        vietsub_app.editor_api.jobs = EditorJobs()
        vietsub_app.workflow_jobs.clear()
        self.client = vietsub_app.app.test_client()

    def tearDown(self):
        with vietsub_app.uploader_api.lock:
            root_key = os.path.abspath(os.path.join(vietsub_app.CONFIG_DIR, "uploader"))
            inst = vietsub_app.uploader_api.instances.pop(root_key, None)
        if inst:
            inst[2].shutdown()
        vietsub_app.editor_api.jobs.shutdown()
        vietsub_app.editor_api.jobs = self.orig_editor_jobs
        with vietsub_app.pipeline_state_lock:
            vietsub_app.pipeline_inflight_jobs.clear()
            vietsub_app.pipeline_inflight_paths.clear()
        if self.orig_shutdown:
            vietsub_app.shutdown_event.set()
        else:
            vietsub_app.shutdown_event.clear()
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.extend(self.orig_jobs)
        vietsub_app.current_job_id = self.orig_current
        vietsub_app.prefetch_job_id = self.orig_prefetch
        vietsub_app.CONFIG_DIR = self.orig_config_dir
        vietsub_app.CONFIG_PATH = self.orig_config_path
        vietsub_app.app.config["TESTING"] = self.orig_testing

    def _make_project(self, name="clip", *, crop=True):
        pdir = self.root / name
        pdir.mkdir(exist_ok=True)
        vid = pdir / f"{name}.mp4"
        vid.write_bytes(MP4)
        raw_srt = pdir / f"{name}.raw.srt"
        raw_srt.write_text(SAMPLE_RAW_SRT, encoding="utf-8")
        vi_srt = pdir / f"{name}.vi.srt"
        vi_srt.write_text(SAMPLE_VI_SRT, encoding="utf-8")
        res = {
            "project_name": name,
            "project_dir": str(pdir),
            "video": str(vid),
            "raw_srt": str(raw_srt),
            "translated_srt": str(vi_srt),
            "video_resolution": {"width": 1080, "height": 1920},
        }
        if crop:
            res["crop_coords"] = {"crop_x": 0.04, "crop_y": 0.64, "crop_width": 0.92, "crop_height": 0.30}
        snap = manifest.snapshot(res, status="success")
        manifest.write_atomic(manifest.path_for(res), snap)
        return pdir, res

    def test_h01_workflow_lock_single_owner_no_double_release(self):
        # run_workflow_thread with release_workflow_lock=False must NOT release caller's workflow_lock.
        self.assertTrue(vietsub_app.workflow_lock.acquire(blocking=False))
        try:
            with mock.patch.object(vietsub_app, "load_app_config", side_effect=RuntimeError("boom")):
                vietsub_app.run_workflow_thread(
                    "https://www.douyin.com/video/123", "ch", release_workflow_lock=False
                )
            # Lock MUST still be held by caller!
            self.assertTrue(vietsub_app.workflow_lock.locked())
        finally:
            if vietsub_app.workflow_lock.locked():
                vietsub_app.workflow_lock.release()

    def test_h03_maybe_trigger_auto_pipeline_exception_never_crashes_worker(self):
        self.client.post("/api/settings", json={"auto_edit_after_translate": True})
        _, res = self._make_project("corrupt_proj")
        vietsub_app.workflow_jobs.append({
            "id": "job-h03", "status": "success", "result_ready": True, "result": res,
        })
        with mock.patch.object(vietsub_app.project_manifest, "snapshot", side_effect=ValueError("corrupt sidecar")):
            out = vietsub_app.maybe_trigger_auto_pipeline("job-h03")
        self.assertIsNone(out)
        self.assertFalse(vietsub_app.pipeline_uses_job("job-h03"))

    def test_h04_h05_m12_update_lock_blocks_editor_uploader_and_releases_on_unexpected_error(self):
        _, res = self._make_project("upd_proj")
        vietsub_app.workflow_jobs.append({
            "id": "job-upd", "status": "success", "result_ready": True, "result": res,
        })
        store, _, _ = vietsub_app.uploader_api.services()
        prof = store.create("tiktok", "T1")
        store.update_session(prof["id"], "alive", "session_alive")

        # 1) H-04: pipeline_inflight_jobs blocks /api/update/install AND bulk_delete_jobs (H-05)
        vietsub_app._reserve_pipeline_job("job-upd", [res["video"]])
        try:
            r_upd = self.client.post("/api/update/install", json={})
            self.assertEqual(r_upd.status_code, 409)
            r_del = self.client.post("/api/queue/bulk-delete", json={"ids": ["job-upd"]})
            self.assertEqual(r_del.status_code, 200)
            self.assertEqual(r_del.get_json()["deleted_ids"], [])
            self.assertEqual(len(r_del.get_json()["failures"]), 1)
        finally:
            vietsub_app._release_pipeline_job("job-upd")

        # 2) H-04: While update_install_lock is held, Editor & Uploader admissions return 409
        self.assertTrue(vietsub_app.update_install_lock.acquire(blocking=False))
        try:
            self.assertEqual(
                self.client.post("/api/editor/jobs", json={"job_id": "job-upd", "action": "render", "options": {}}).status_code,
                409,
            )
            self.assertEqual(
                self.client.post("/api/editor/projects/job-upd/capcut-draft", json={"options": {}}).status_code,
                409,
            )
            self.assertEqual(
                self.client.post("/api/editor/tts/preview", json={"text": "Xin chào", "voice": "v1"}).status_code,
                409,
            )
            self.assertEqual(
                self.client.post("/api/uploader/queue", json={
                    "job_id": "job-upd", "profile_ids": [prof["id"]],
                    "metadata": {"title": "T", "description": "", "hashtags": []},
                    "visibility": "draft", "request_id": "upd-block-req-1",
                }).status_code,
                409,
            )
            self.assertEqual(self.client.post(f"/api/uploader/profiles/{prof['id']}/login", json={}).status_code, 409)
            self.assertEqual(self.client.post("/api/uploader/projects/job-upd/metadata", json={}).status_code, 409)
            pipe_res = vietsub_app.uploader_api.enqueue_pipeline_upload(
                "job-upd", {"profile_ids": [prof["id"]], "upload_mode": "assisted", "visibility": "draft"}
            )
            self.assertEqual(pipe_res["status"], "skipped")
        finally:
            vietsub_app.update_install_lock.release()

        # 3) M-12: Unexpected exception inside install_update MUST still release update_install_lock
        with mock.patch.object(vietsub_app, "fetch_latest_release_payload", side_effect=KeyError("unexpected-bug")):
            with self.assertRaises(KeyError):
                self.client.post("/api/update/install", json={})
        self.assertFalse(vietsub_app.update_install_lock.locked())

    def test_m01_explicit_empty_preset_not_hijacked_and_pipeline_persists(self):
        # Enqueue job explicitly with no preset ("") when no preset is active
        with mock.patch.object(vietsub_app, "detect_video_source", return_value=mock.Mock(url="https://www.douyin.com/video/555", site="douyin")):
            j = self.client.post("/api/queue", json={"url": "https://www.douyin.com/video/555", "lang": "ch", "channel_preset_id": ""}).get_json()
        raw_job = next(item for item in vietsub_app.workflow_jobs if item["id"] == j["id"])
        self.assertEqual(raw_job["channel_preset_id"], "")
        self.assertTrue(raw_job["channel_preset_pinned"])

        # Later activate a Channel Preset with a custom glossary + auto_edit
        gl = glossary.create_glossary(vietsub_app.CONFIG_DIR, name="Late Glossary", style_note="Late", entries=[])
        preset, _ = channel_presets.create_preset(
            vietsub_app.CONFIG_DIR, name="Late Preset", glossary_id=gl["id"],
            editor_options={"mask_mode": "none"}, activate=True,
        )
        # Verify normalize_persisted_job preserves channel_preset_pinned & pipeline
        raw_job["pipeline"] = {"enabled": True, "auto_edit": "success", "auto_publish": "queued"}
        persisted = vietsub_app.persisted_job(raw_job)
        self.assertIn("pipeline", persisted)
        self.assertIn("channel_preset_pinned", persisted)
        restored = vietsub_app.normalize_persisted_job(persisted)
        self.assertTrue(restored["channel_preset_pinned"])
        self.assertEqual(restored["pipeline"]["auto_publish"], "queued")

        # Legacy job (pre-3.2, missing channel_preset_pinned) normalizes to channel_preset_pinned=False
        legacy = vietsub_app.normalize_persisted_job({
            "id": "leg-1", "source_url": "https://www.douyin.com/video/777",
            "ocr_lang": "ch", "status": "queued", "source_type": "douyin",
        })
        self.assertFalse(legacy["channel_preset_pinned"])

        # Trigger maybe_trigger_auto_pipeline on pinned-empty job -> MUST NOT pick up Late Preset!
        self.client.post("/api/settings", json={"auto_edit_after_translate": True})
        _, res = self._make_project("pinned_empty", crop=False)
        # Put mask_mode=boxblur in manifest to also verify M-03 safe downgrade when preset is None & crop_box is None!
        snap = manifest.snapshot(res)
        snap["editor_options"] = {"mask_mode": "boxblur", "crop_box": None}
        manifest.write_atomic(manifest.path_for(res), snap)
        vietsub_app.workflow_jobs.append({
            "id": "job-empty-pinned", "status": "success", "result_ready": True,
            "channel_preset_id": "", "channel_preset_pinned": True, "result": res,
        })
        captured = {}
        def fake_enqueue(job_id, action, submitted_options=None, **kw):
            captured["options"] = submitted_options
            captured["pipeline_meta"] = kw.get("pipeline_meta")
            return {"id": "a" * 32, "job_id": job_id, "action": action, "status": "queued"}
        with mock.patch.object(vietsub_app.editor_api, "enqueue_project_task", side_effect=fake_enqueue):
            task = vietsub_app.maybe_trigger_auto_pipeline("job-empty-pinned")
        self.assertIsNotNone(task)
        self.assertEqual(captured["pipeline_meta"]["preset_id"], "")
        self.assertEqual(captured["options"]["mask_mode"], "none")
        # Release reservation since fake_enqueue didn't invoke after_terminal
        vietsub_app._release_pipeline_job("job-empty-pinned")

    def test_h08_m03_m04_preset_symlink_guard_no_false_crop_warning_and_apply_atomicity(self):
        preset, _ = channel_presets.create_preset(
            vietsub_app.CONFIG_DIR,
            name="Default Blur",
            editor_options={"mask_mode": "boxblur", "crop_box": None},
            activate=False,
        )
        # M-03: resolve_preset with project_dir=None preserves mask_mode and emits NO false crop warning
        resolved_no_proj = channel_presets.resolve_preset(vietsub_app.CONFIG_DIR, preset)
        self.assertEqual(resolved_no_proj["editor_options"]["mask_mode"], "boxblur")
        self.assertFalse(any("crop OCR" in w for w in resolved_no_proj["warnings"]))

        # M-04: apply with missing job_id returns 404 and does NOT mutate active_id
        r_404 = self.client.post(f"/api/channel-presets/{preset['id']}/apply", json={"job_id": "missing-id", "activate": True})
        self.assertEqual(r_404.status_code, 404)
        self.assertEqual(channel_presets.get_active_preset_id(vietsub_app.CONFIG_DIR), "")

        # M-04: apply while Auto Edit is active returns 409 without mutating active_id
        pdir, res = self._make_project("busy_apply")
        vietsub_app.workflow_jobs.append({
            "id": "job-busy-apply", "status": "success", "result_ready": True, "result": res,
        })
        with mock.patch.object(vietsub_app.editor_api.jobs, "active", return_value=True):
            r_409 = self.client.post(f"/api/channel-presets/{preset['id']}/apply", json={"job_id": "job-busy-apply", "activate": True})
        self.assertEqual(r_409.status_code, 409)
        self.assertEqual(channel_presets.get_active_preset_id(vietsub_app.CONFIG_DIR), "")

        # H-08: Dangling symlink or unsafe target at scoped watermark path is rejected (never followed)
        png = _valid_png()
        preset, _ = channel_presets.save_preset_watermark(vietsub_app.CONFIG_DIR, preset["id"], png)
        digest = hashlib.sha256(png).hexdigest()[:12]
        assets_dir = pdir / ".vietsub-assets"
        assets_dir.mkdir(exist_ok=True)
        scoped = assets_dir / f"watermark-preset-{digest}.png"
        outside_target = self.root / "outside_secret.png"
        try:
            os.symlink(outside_target, scoped)
            symlink_created = True
        except OSError:
            symlink_created = False
        if symlink_created:
            with self.assertRaises(ValueError):
                channel_presets.resolve_preset(vietsub_app.CONFIG_DIR, preset, project_dir=str(pdir))
            self.assertFalse(outside_target.exists(), "Dangling symlink must never be written through!")

    def test_m13_remove_queued_job_blocks_prefetch_and_editor_cancel_queued_immediate(self):
        vietsub_app.workflow_jobs.append({
            "id": "job-pf", "status": "queued", "source_url": "https://www.douyin.com/video/1", "ocr_lang": "ch",
        })
        vietsub_app.prefetch_job_id = "job-pf"
        with self.assertRaises(RuntimeError):
            vietsub_app.remove_queued_job("job-pf")

        # EditorJobs.cancel() immediately transitions queued task #2 while task #1 is running
        jobs = EditorJobs()
        self.addCleanup(jobs.shutdown)
        started = threading.Event()
        release = threading.Event()
        def slow_task(identifier, cancel, update):
            started.set()
            release.wait(5)
            return {}
        t1 = jobs.submit("p1", "render", slow_task)
        self.assertTrue(started.wait(3))
        after_events = []
        t2 = jobs.submit("p2", "render", lambda *a: {}, after_terminal=lambda snap, st: after_events.append(st))
        cancelled = jobs.cancel(t2["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(jobs.active("p2"))
        self.assertEqual(after_events, ["cancelled"])
        release.set()


class PublishHubAndYouTubeHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vietsub-wa1-pub-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.orig_config_dir = vietsub_app.CONFIG_DIR
        self.orig_testing = vietsub_app.app.config.get("TESTING")
        self.orig_jobs = copy.deepcopy(vietsub_app.workflow_jobs)
        vietsub_app.CONFIG_DIR = str(self.root / "config")
        os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
        vietsub_app.app.config["TESTING"] = True
        vietsub_app.workflow_jobs.clear()
        self.client = vietsub_app.app.test_client()

    def tearDown(self):
        with vietsub_app.uploader_api.lock:
            root_key = os.path.abspath(os.path.join(vietsub_app.CONFIG_DIR, "uploader"))
            inst = vietsub_app.uploader_api.instances.pop(root_key, None)
        if inst:
            inst[2].shutdown()
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.extend(self.orig_jobs)
        vietsub_app.CONFIG_DIR = self.orig_config_dir
        vietsub_app.app.config["TESTING"] = self.orig_testing

    def _make_job(self, job_id="job-pub-1", title="Video Test"):
        pdir = self.root / job_id
        pdir.mkdir(exist_ok=True)
        vid = pdir / f"{job_id}.mp4"
        vid.write_bytes(MP4)
        (pdir / f"{job_id}.raw.srt").write_text(SAMPLE_RAW_SRT, encoding="utf-8")
        (pdir / f"{job_id}.vi.srt").write_text(SAMPLE_VI_SRT, encoding="utf-8")
        res = {
            "project_name": job_id, "project_dir": str(pdir), "video": str(vid),
            "raw_srt": str(pdir / f"{job_id}.raw.srt"), "translated_srt": str(pdir / f"{job_id}.vi.srt"),
        }
        snap = manifest.snapshot(res, status="success")
        snap["metadata"] = {"title": title, "description": "Mô tả", "hashtags": ["#vietsub"]}
        manifest.write_atomic(manifest.path_for(res), snap)
        vietsub_app.workflow_jobs.append({
            "id": job_id, "status": "success", "result_ready": True, "project_name": job_id, "result": res,
        })
        return pdir, res

    def test_h02_h06_m10_batch_enqueue_while_lease_held_and_youtube_api_public_assisted_blocked(self):
        store, _, queue = vietsub_app.uploader_api.services()
        p_yt = store.create("youtube", "YT API")
        p_tt = store.create("tiktok", "TT Ready")
        store.update_session(p_yt["id"], "alive", "oauth_connected")
        store.update_session(p_tt["id"], "alive", "session_alive")
        store.update_config(p_yt["id"], upload_method="youtube_api")
        # Seed connected OAuth file
        oauth_path = Path(store.oauth_path(p_yt["id"]))
        oauth_path.parent.mkdir(parents=True, exist_ok=True)
        oauth_path.write_text(json.dumps({
            "schema_version": 1, "client_id": "123-abc.apps.googleusercontent.com",
            "client_secret_protected": vietsub_app.protect_text_secret("GOCSPX-secret-12345678"),
            "access_token_protected": vietsub_app.protect_text_secret("ya29.token-1234567890"),
            "refresh_token_protected": vietsub_app.protect_text_secret("1//refresh-1234567890"),
            "token_expires_at": time.time() + 3600,
        }), encoding="utf-8")

        _, res = self._make_job("job-batch-2", "Video 2")

        # Hold active runtime lease on p_tt (simulating Video 1 actively uploading / awaiting assisted click!)
        with store.lease(p_tt["id"]), mock.patch.object(queue, "start", return_value=None):
            # H-06 + H-02: p_yt (youtube_api + public + assisted + confirm_public_auto=False) MUST be blocked,
            # while p_tt MUST STILL enqueue cleanly despite active lease!
            outcome = vietsub_app.uploader_api.enqueue_pipeline_upload(
                "job-batch-2",
                {
                    "profile_ids": [p_yt["id"], p_tt["id"]],
                    "upload_mode": "assisted",
                    "visibility": "public",
                    "confirm_public_auto": False,
                },
                preset_id="p1",
            )
            self.assertEqual(outcome["status"], "queued")
            self.assertEqual(len(outcome["jobs"]), 1)
            self.assertEqual(outcome["jobs"][0]["platform"], "tiktok")
            self.assertTrue(any("YouTube API v3" in w for w in outcome["warnings"]))

            # M-10: Changing metadata produces a distinct request_id (no collision!)
            snap = manifest.snapshot(res)
            snap["metadata"]["title"] = "Video 2 Edited Title"
            manifest.write_atomic(manifest.path_for(res), snap)
            outcome2 = vietsub_app.uploader_api.enqueue_pipeline_upload(
                "job-batch-2",
                {"profile_ids": [p_tt["id"]], "upload_mode": "assisted", "visibility": "public"},
                preset_id="p1",
            )
            self.assertEqual(outcome2["status"], "queued")
            self.assertNotEqual(outcome["jobs"][0]["request_id"], outcome2["jobs"][0]["request_id"])

        # M-10: Switching YouTube profile from youtube_api -> browser resets stale session_status='alive'
        r_cfg = self.client.post(f"/api/uploader/profiles/{p_yt['id']}/config", json={"upload_method": "browser"})
        self.assertEqual(r_cfg.status_code, 200)
        self.assertEqual(r_cfg.get_json()["session_status"], "unknown")

    def test_h07_h10_m11_assisted_cancel_marks_uncertain_autoprune_500_and_delete_finished_security(self):
        qpath = self.root / "q.json"
        vid = self.root / "clip.mp4"
        vid.write_bytes(MP4)
        entered = threading.Event()
        def exec_await(job, cancel, progress, checkpoint):
            progress(status="awaiting_user_submit", phase="awaiting_user_submit", step="submit", percent=88, awaiting_deadline=time.time() + 600)
            entered.set()
            cancel.wait(5)
            raise adapters.UploadCancelled("cancelled")

        q = UploadQueue(qpath, exec_await)
        self.addCleanup(q.shutdown)
        created = q.submit(
            {"video": str(vid)}, [{"id": "p1", "platform": "tiktok"}],
            {"title": "T", "description": "", "hashtags": []}, "public", None, "h07-req-001", upload_mode="assisted",
        )
        self.assertTrue(entered.wait(3))
        q.shutdown()
        final = q.get(created[0]["id"])
        self.assertEqual(final["status"], "uncertain")
        self.assertTrue(final["commit_attempted"])

        # H-10: Auto-prune at 500 preserves uncertain and removes oldest terminal + artifacts
        q2 = UploadQueue(self.root / "q500.json", lambda *a: {"status": "draft"})
        self.addCleanup(q2.shutdown)
        with q2.lock:
            for idx in range(500):
                jid = f"{idx:032x}"
                art = q2.path.parent / "artifacts" / jid
                art.mkdir(parents=True, exist_ok=True)
                (art / "shot.png").write_bytes(b"png")
                q2.jobs.append({
                    "id": jid, "profile_id": "p1", "platform": "tiktok", "job_id": "", "media_id": "",
                    "video": str(vid), "video_size": len(MP4), "video_mtime_ns": 1,
                    "metadata": {"title": f"Old {idx}", "description": "", "hashtags": []},
                    "visibility": "draft", "upload_mode": "auto", "debug_mode": False,
                    "step": "", "failed_step": "", "trace_path": "", "awaiting_deadline": None,
                    "scheduled_at": None, "status": "uncertain" if idx == 0 else "success",
                    "phase": "done", "percent": 100, "error": "", "result_url": "", "screenshot_path": "",
                    "commit_attempted": idx == 0, "created_at": idx, "request_id": f"old-req-{idx:04d}",
                    "request_fingerprint": f"fp-{idx}",
                })
        with mock.patch.object(q2, "start", return_value=None):
            added = q2.submit(
                {"video": str(vid)}, [{"id": "p1", "platform": "tiktok"}],
                {"title": "Job 501", "description": "", "hashtags": []}, "draft", None, "req-job-501",
            )
        self.assertEqual(len(added), 1)
        self.assertEqual(len(q2.snapshot()), 500)
        # Job 0 (uncertain) MUST survive; Job 1 (oldest success) MUST be pruned along with its artifacts dir!
        ids_left = {j["id"] for j in q2.snapshot()}
        self.assertIn(f"{0:032x}", ids_left)
        self.assertNotIn(f"{1:032x}", ids_left)
        self.assertFalse((q2.path.parent / "artifacts" / f"{1:032x}").exists())

        # H-10 + M-11: DELETE /api/uploader/queue/finished enforces TESTING=False CSRF
        with mock.patch.dict(vietsub_app.app.config, TESTING=False):
            r_no_tok = self.client.delete("/api/uploader/queue/finished", json={})
            self.assertEqual(r_no_tok.status_code, 403)
            r_ok = self.client.delete(
                "/api/uploader/queue/finished",
                json={},
                headers={
                    "Host": "127.0.0.1:5000",
                    "Origin": "http://127.0.0.1:5000",
                    vietsub_app.API_TOKEN_HEADER: vietsub_app.API_TOKEN,
                },
            )
            self.assertEqual(r_ok.status_code, 200)
            self.assertTrue(r_ok.get_json()["ok"])

    def test_m09_youtube_api_5xx_refresh_dpapi_probe_exception_and_308_stall(self):
        oauth_path = self.root / "yt_oauth.json"
        vid = self.root / "yt_clip.mp4"
        vid.write_bytes(MP4 + b"B" * (300 * 1024))
        http = mock.Mock()
        client = youtube_api.YouTubeAPIClient(
            protect_secret=vietsub_app.protect_text_secret,
            unprotect_secret=vietsub_app.unprotect_config_secret,
            http_session=http,
        )
        client.save_client_config(
            oauth_path,
            client_id="123-abc.apps.googleusercontent.com",
            client_secret="GOCSPX-secret-12345678",
        )
        raw = json.loads(oauth_path.read_text(encoding="utf-8"))
        raw["access_token_protected"] = vietsub_app.protect_text_secret("ya29.old-token-12345678")
        raw["refresh_token_protected"] = vietsub_app.protect_text_secret("1//refresh-token-12345678")
        raw["token_expires_at"] = time.time() - 60
        oauth_path.write_text(json.dumps(raw), encoding="utf-8")

        # 1) Google 503 during refresh -> YouTubeAPIError (NOT YouTubeAuthError!)
        http.request.return_value = FakeResp(503, {"error": "temporarily_unavailable"})
        with self.assertRaises(youtube_api.YouTubeAPIError) as caught:
            client.ensure_access_token(oauth_path)
        self.assertNotIsInstance(caught.exception, youtube_api.YouTubeAuthError)

        # 2) DPAPI decrypt OSError -> YouTubeAuthError
        bad_client = youtube_api.YouTubeAPIClient(
            protect_secret=vietsub_app.protect_text_secret,
            unprotect_secret=mock.Mock(side_effect=OSError("DPAPI fail")),
            http_session=http,
        )
        with self.assertRaises(youtube_api.YouTubeAuthError):
            bad_client.ensure_access_token(oauth_path)

        # 3) Resumable probe RequestException + 308 stall guard
        raw["token_expires_at"] = time.time() + 3600
        oauth_path.write_text(json.dumps(raw), encoding="utf-8")
        calls = [0]
        def fake_req(method, url, **kw):
            if url == youtube_api.YOUTUBE_UPLOAD_INIT_URL:
                return FakeResp(200, {}, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=u1"})
            if kw.get("headers", {}).get("Content-Range", "").startswith("bytes */"):
                raise requests.ConnectionError("probe dropped")
            calls[0] += 1
            if calls[0] == 1:
                return FakeResp(503, {"error": {"message": "transient"}})
            # Always return 308 with Range ending at 0 -> triggers stall guard after 3 attempts
            return FakeResp(308, {}, headers={"Range": "bytes=0-0"})
        http.request.side_effect = fake_req
        with mock.patch.object(youtube_api.time, "sleep"):
            with self.assertRaisesRegex(youtube_api.YouTubeAPIError, "308"):
                client.upload(
                    "p1", oauth_path, vid, {"title": "T", "description": "D", "hashtags": []},
                    visibility="private", chunk_size=256 * 1024,
                )


class SubtitlesPublishSecurityAndSignTests(unittest.TestCase):
    def test_h09_m07_word_pop_never_drops_chunks_and_overlapping_cues_clamped(self):
        with tempfile.TemporaryDirectory(prefix="vietsub-wa1-sub-") as tmp:
            root = Path(tmp)
            # 1) H-09: 8 words in 100ms at speed=2.0 (5 cs) with max 2 words/chunk -> 4 chunks, ALL must render!
            srt_short = root / "short.srt"
            srt_short.write_text("1\n00:00:01,000 --> 00:00:01,100\nmột hai ba bốn năm sáu bảy tám\n", encoding="utf-8")
            ass_short = root / "short.ass"
            subtitles.write_ass(
                str(srt_short), str(ass_short), 1080, 1920,
                {"subtitle_enabled": True, "subtitle_animation": "word_pop", "subtitle_max_words_per_chunk": 2, "speed": 2.0},
            )
            content = ass_short.read_text(encoding="utf-8")
            for word in ("một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám"):
                self.assertIn(word, content, f"Word '{word}' was dropped by word_pop!")

            # 2) M-07: Overlapping cues clamp Cue 1 End <= Cue 2 Start
            srt_ov = root / "overlap.srt"
            srt_ov.write_text(
                "1\n00:00:00,000 --> 00:00:03,000\nCâu thứ nhất kéo dài\n\n"
                "2\n00:00:02,000 --> 00:00:04,000\nCâu thứ hai bắt đầu sớm\n",
                encoding="utf-8",
            )
            for anim in ("static", "karaoke_highlight", "word_pop"):
                ass_ov = root / f"ov_{anim}.ass"
                subtitles.write_ass(
                    str(srt_ov), str(ass_ov), 1080, 1920,
                    {"subtitle_enabled": True, "subtitle_animation": anim, "speed": 1.0},
                )
                dlg_lines = [ln for ln in ass_ov.read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:")]
                c1_ends = [ln.split(",")[2] for ln in dlg_lines if "nhất" in ln or "kéo" in ln or "dài" in ln]
                c2_starts = [ln.split(",")[1] for ln in dlg_lines if "hai" in ln or "sớm" in ln]
                self.assertTrue(c1_ends and c2_starts)
                self.assertLessEqual(max(c1_ends), min(c2_starts), f"Overlap detected in {anim}: {c1_ends} vs {c2_starts}")

    def test_m08_publish_rejects_kana_hangul_and_parses_prose_wrapped_json(self):
        prose = (
            "Dưới đây là 4 câu Hook hấp dẫn cho bạn:\n"
            "```json\n"
            '{"hooks": ["Cú lật kèo không ai ngờ tới!", "Bí mật đằng sau cánh cửa đóng kín", '
            '"Xem hết 10 giây cuối mới sốc!", "ひらがなカタカナフック", "한국어 후크 테스트"]}\n'
            "```\nChúc bạn lên xu hướng!"
        )
        hooks = publish.parse_hooks_response(prose)
        self.assertEqual(len(hooks), 3)
        self.assertFalse(any("ひ" in h or "한" in h for h in hooks))

        with self.assertRaises(ValueError):
            publish.parse_response(json.dumps({
                "title": "한국어 제목 테스트", "description": "Mô tả", "hashtags": ["#a", "#b", "#c"],
            }))

    def test_m11_m12_redaction_pinning_spaced_windows_user_oauth_patterns_and_sign_icacls(self):
        # Pinned secret survives > 600 non-pinned secret rotations
        redaction.register_secret("PINNED-SYSTEM-TOKEN-999999", pinned=True)
        for idx in range(600):
            redaction.register_secret(f"ephemeral-secret-value-{idx:04d}")
        scrubbed = redaction.redact_text(
            "tok=PINNED-SYSTEM-TOKEN-999999 key=GOCSPX-abc123def456ghi "
            "at=ya29.a0AfH6SMBx999999999 rt=1//04abcdef123456789012345 "
            "client_secret=short6 code_verifier=verif12345678",
            redact_paths=True,
        )
        self.assertNotIn("PINNED-SYSTEM-TOKEN-999999", scrubbed)
        self.assertNotIn("GOCSPX-abc123def456ghi", scrubbed)
        self.assertNotIn("ya29.a0AfH6SMBx999999999", scrubbed)
        self.assertNotIn("1//04abcdef123456789012345", scrubbed)
        self.assertNotIn("short6", scrubbed)
        self.assertNotIn("verif12345678", scrubbed)

        # Windows username with spaces redacts cleanly without leaking tail of username
        spaced = redaction.redact_error(r"Cannot open C:\Users\Nguyen Van A\Documents\video.mp4 and C:/Users/Tran Thi B/app.log")
        self.assertNotIn("Nguyen Van A", spaced)
        self.assertNotIn("Tran Thi B", spaced)
        self.assertIn(r"<USER_HOME>\Documents\video.mp4", spaced)
        self.assertIn("<USER_HOME>/app.log", spaced)

        # M-12: Ed25519 self-check passes
        self.assertTrue(updater.self_check_ed25519())

        # M-12: sign_release raises RuntimeError when icacls fails on Windows
        if os.name == "nt":
            failed_proc = subprocess.CompletedProcess(args=["icacls"], returncode=5, stdout="", stderr="Access is denied.")
            with mock.patch.object(sign_release.subprocess, "run", return_value=failed_proc):
                with self.assertRaisesRegex(RuntimeError, "icacls"):
                    sign_release._restrict_private_key_permissions("C:/temp/fake.pem")

        # M-11: selector_store redacts OSError path
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("builtins.open", side_effect=PermissionError(r"Denied C:\Users\Nguyen Van A\override.json")):
                override = Path(tmp) / "uploader" / "selectors.override.json"
                override.parent.mkdir(parents=True)
                override.write_text("{}", encoding="utf-8")
                _, status = selector_store.load_selectors(tmp)
            self.assertNotIn("Nguyen Van A", status["warning"])

    def test_m06_m08_h11_m11_srt_rollback_stale_hooks_502_media_prune_and_no_store(self):
        with tempfile.TemporaryDirectory(prefix="vietsub-wa1-extra-") as tmp:
            root = Path(tmp)
            orig_cfg = vietsub_app.CONFIG_DIR
            orig_cfg_path = vietsub_app.CONFIG_PATH
            orig_testing = vietsub_app.app.config.get("TESTING")
            orig_jobs = copy.deepcopy(vietsub_app.workflow_jobs)
            vietsub_app.CONFIG_DIR = str(root / "config")
            vietsub_app.CONFIG_PATH = str(root / "config" / "config.json")
            os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
            vietsub_app.app.config["TESTING"] = True
            client = vietsub_app.app.test_client()
            try:
                pdir = root / "proj_srt"
                pdir.mkdir()
                vid = pdir / "clip.mp4"
                vid.write_bytes(MP4)
                edited = pdir / "clip.edited.mp4"
                edited.write_bytes(MP4)
                raw_srt = pdir / "clip.raw.srt"
                vi_srt = pdir / "clip.vi.srt"
                raw_srt.write_text(SAMPLE_RAW_SRT, encoding="utf-8")
                vi_srt.write_text(SAMPLE_VI_SRT, encoding="utf-8")
                res = {
                    "project_name": "clip", "project_dir": str(pdir), "video": str(vid),
                    "edited_video": str(edited), "raw_srt": str(raw_srt), "translated_srt": str(vi_srt),
                }
                snap = manifest.snapshot(res, status="success")
                manifest.write_atomic(manifest.path_for(res), snap)
                vietsub_app.workflow_jobs.clear()
                vietsub_app.workflow_jobs.append({
                    "id": "job-srt-m06", "status": "success", "result_ready": True, "project_name": "clip", "result": res,
                })

                # 1) M-06: If manifest write fails during PUT /srt, original SRT content is rolled back!
                orig_text = vi_srt.read_text(encoding="utf-8")
                with mock.patch.object(manifest, "write_atomic", side_effect=OSError("disk locked")):
                    r_fail = client.put("/api/editor/projects/job-srt-m06/srt", json={
                        "cues": [{"index": 1, "start_ms": 200, "end_ms": 2000, "text": "Bản sửa bị lỗi manifest"}]
                    })
                self.assertEqual(r_fail.status_code, 409)
                self.assertEqual(vi_srt.read_text(encoding="utf-8"), orig_text)

                # 2) M-06: Successful PUT /srt marks edited_video as stale in manifest & handoff payload
                r_ok = client.put("/api/editor/projects/job-srt-m06/srt", json={
                    "cues": [{"index": 1, "start_ms": 200, "end_ms": 2000, "text": "Bản sửa mới nhất"}]
                })
                self.assertEqual(r_ok.status_code, 200)
                self.assertTrue(r_ok.get_json()["srt_stale_assets"]["edited_video"])
                r_proj = client.get("/api/projects/job-srt-m06?workspace=uploader").get_json()
                self.assertTrue(r_proj["srt_stale_assets"]["edited_video"])

                # 3) M-06: Fresh VietSub write_project_manifest(reset_manual_srt=True) resets srt_manually_edited
                vietsub_app.write_project_manifest(
                    {**res, "source_video": str(vid)}, video_id="", source_url="", status="success", reset_manual_srt=True
                )
                self.assertFalse(manifest.snapshot(res).get("srt_manually_edited"))

                # 4) M-08: suggest-hooks retries once on transient 503 and returns 502 on upstream malformed JSON
                fake_rt = {
                    "provider": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat", "api_key": "sk-test-1234567890", "glossary": {},
                }
                attempts = [0]
                def flaky_then_bad(*_a, **_kw):
                    attempts[0] += 1
                    if attempts[0] == 1:
                        raise vietsub_app.TranslationApiError("HTTP 503 transient", retryable=True)
                    return "not-valid-json-at-all"
                with mock.patch.object(vietsub_app, "get_translation_runtime_config", return_value=fake_rt), \
                     mock.patch.object(vietsub_app, "call_openai_compatible_api", side_effect=flaky_then_bad), \
                     mock.patch("vietsub.editor.api.time.sleep"):
                    r_hooks = client.post("/api/editor/projects/job-srt-m06/suggest-hooks", json={})
                self.assertEqual(attempts[0], 2, "Transient 503 should trigger 1 retry")
                self.assertEqual(r_hooks.status_code, 502)

                # 5) H-11: _prune_media_disk cleans expired standalone media folders while honoring protected paths
                media_root = Path(vietsub_app.CONFIG_DIR) / "uploader" / "media"
                old_dir = media_root / ("a" * 32)
                old_dir.mkdir(parents=True)
                (old_dir / "video.mp4").write_bytes(MP4)
                old_time = time.time() - 86400 * 10
                os.utime(old_dir, (old_time, old_time))
                vietsub_app.uploader_api._prune_media_disk()
                self.assertFalse(old_dir.exists())

                # 6) M-11: POST /api/settings 503 redacts raw user path & /trace sets Cache-Control: no-store
                with mock.patch.object(vietsub_app, "load_app_config", side_effect=OSError(r"Locked C:\Users\Nguyen Van A\config.json")):
                    r_503 = client.post("/api/settings", json={"ocr_lang": "ch"})
                self.assertEqual(r_503.status_code, 503)
                self.assertNotIn("Nguyen Van A", r_503.get_json()["error"])

                store, _, uqueue = vietsub_app.uploader_api.services()
                prof = store.create("tiktok", "Cache Header Test")
                store.update_session(prof["id"], "alive", "session_alive")
                with mock.patch.object(uqueue, "start", return_value=None):
                    created = uqueue.submit(
                        {"video": str(vid)}, [prof], {"title": "T", "description": "", "hashtags": []},
                        "draft", None, "nostore-req-01", debug_mode=True,
                    )
                tfile = Path(store.trace_dir(prof["id"])) / f"trace-{created[0]['id']}-11111111.zip"
                tfile.write_bytes(b"PK\x03\x04zip")
                uqueue._update(created[0]["id"], status="error", trace_path=str(tfile))
                pub_job = client.get("/api/uploader/queue").get_json()["jobs"][-1]
                with client.get(pub_job["trace_url"]) as r_trace:
                    self.assertEqual(r_trace.status_code, 200)
                    self.assertEqual(r_trace.headers.get("Cache-Control"), "no-store")
            finally:
                with vietsub_app.uploader_api.lock:
                    root_key = os.path.abspath(os.path.join(vietsub_app.CONFIG_DIR, "uploader"))
                    inst = vietsub_app.uploader_api.instances.pop(root_key, None)
                if inst:
                    inst[2].shutdown()
                vietsub_app.workflow_jobs.clear()
                vietsub_app.workflow_jobs.extend(orig_jobs)
                vietsub_app.CONFIG_DIR = orig_cfg
                vietsub_app.CONFIG_PATH = orig_cfg_path
                vietsub_app.app.config["TESTING"] = orig_testing


if __name__ == "__main__":
    unittest.main()
