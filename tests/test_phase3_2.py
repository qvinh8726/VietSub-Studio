"""Phase 3.2 tests: Channel Presets, End-to-End Auto Pipeline, AI Hook Suggestions & Security."""
import copy
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

import app as vietsub_app
from vietsub import channel_presets, glossary, manifest, publish, storage
from vietsub.editor import renderer as editor_renderer
from vietsub.editor.jobs import EditorJobs


SAMPLE_RAW_SRT = (
    "1\n"
    "00:00:00,200 --> 00:00:02,000\n"
    "青云宗掌门来了\n\n"
    "2\n"
    "00:00:02,200 --> 00:00:04,500\n"
    "九天剑诀威力无穷\n"
)

SAMPLE_VI_SRT = (
    "1\n"
    "00:00:00,200 --> 00:00:02,000\n"
    "Chưởng môn Thanh Vân Tông đã đến.\n\n"
    "2\n"
    "00:00:02,200 --> 00:00:04,500\n"
    "Cửu Thiên Kiếm Quyết uy lực vô song!\n"
)


def _valid_png_bytes(width=4, height=4) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _wait_until(predicate, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


class ChannelPresetsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.orig_config_dir = vietsub_app.CONFIG_DIR
        self.orig_testing = vietsub_app.app.config.get("TESTING")
        self.orig_jobs = copy.deepcopy(vietsub_app.workflow_jobs)
        vietsub_app.CONFIG_DIR = str(self.root / "config")
        os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
        vietsub_app.app.config["TESTING"] = True
        self.client = vietsub_app.app.test_client()

    def tearDown(self):
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.extend(self.orig_jobs)
        vietsub_app.CONFIG_DIR = self.orig_config_dir
        vietsub_app.app.config["TESTING"] = self.orig_testing
        self.temp_dir.cleanup()

    def test_get_does_not_autocreate_and_crud_watermark_orphans_roundtrip(self):
        store_path = channel_presets.presets_path(vietsub_app.CONFIG_DIR)
        self.assertFalse(store_path.exists())
        r_get = self.client.get("/api/channel-presets")
        self.assertEqual(r_get.status_code, 200)
        self.assertEqual(r_get.get_json()["presets"], [])
        self.assertEqual(r_get.get_json()["active_id"], "")
        self.assertFalse(store_path.exists(), "GET /api/channel-presets must never auto-create file")

        # Create glossary & uploader profile to link
        gl = glossary.create_glossary(vietsub_app.CONFIG_DIR, name="Tiên Hiệp", style_note="ta–ngươi", entries=[])
        store, _, _ = vietsub_app.uploader_api.services()
        prof = store.create("youtube", "Kênh Shorts Chính")
        missing_uuid = "11111111-1111-4111-8111-111111111111"

        # Reject public+auto without confirm_public_auto
        r_bad_pub = self.client.post("/api/channel-presets", json={
            "name": "Unsafe Public",
            "publish_defaults": {"profile_ids": [prof["id"]], "upload_mode": "auto", "visibility": "public", "confirm_public_auto": False},
        })
        self.assertEqual(r_bad_pub.status_code, 400)

        # Reject arbitrary watermark_path in JSON
        r_bad_wm = self.client.post("/api/channel-presets", json={
            "name": "Bad WM",
            "editor_options": {"watermark_enabled": True, "watermark_path": "C:\\Windows\\system.ini"},
        })
        self.assertEqual(r_bad_wm.status_code, 400)

        # Create valid preset with crop_box=None + mask_mode=boxblur + word_pop
        r_create = self.client.post("/api/channel-presets", json={
            "name": "Kênh Review 9:16",
            "description": "Word Pop vàng, bán tự động",
            "glossary_id": gl["id"],
            "editor_options": {
                "reframe": "vertical",
                "mask_mode": "boxblur",
                "crop_box": None,
                "subtitle_animation": "word_pop",
                "subtitle_highlight_color": "#ffd54a",
                "subtitle_max_words_per_chunk": 4,
                "banner_text": "Cái kết gây sốc!",
            },
            "publish_defaults": {
                "profile_ids": [prof["id"], missing_uuid],
                "upload_mode": "assisted",
                "visibility": "private",
            },
            "activate": True,
        })
        self.assertEqual(r_create.status_code, 201)
        body = r_create.get_json()
        pid = body["preset"]["id"]
        self.assertEqual(body["active_id"], pid)
        self.assertIsNone(body["preset"]["editor_options"]["crop_box"])
        self.assertEqual(body["preset"]["editor_options"]["mask_mode"], "boxblur")
        self.assertTrue(store_path.is_file())

        # Upload valid PNG watermark to preset
        import io
        r_wm_bad = self.client.post(
            f"/api/channel-presets/{pid}/watermark",
            data={"image": (io.BytesIO(b"not-a-png"), "fake.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(r_wm_bad.status_code, 400)

        r_wm_ok = self.client.post(
            f"/api/channel-presets/{pid}/watermark",
            data={"image": (io.BytesIO(_valid_png_bytes()), "logo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(r_wm_ok.status_code, 200)
        wm_path = r_wm_ok.get_json()["watermark_path"]
        self.assertTrue(Path(wm_path).is_file())

        # Apply to a real project -> copies watermark into project .vietsub-assets, filters orphan profile, activates glossary
        proj_dir = self.root / "proj1"
        proj_dir.mkdir()
        vid = proj_dir / "clip.mp4"
        vid.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
        (proj_dir / "clip.raw.srt").write_text(SAMPLE_RAW_SRT, encoding="utf-8")
        (proj_dir / "clip.vi.srt").write_text(SAMPLE_VI_SRT, encoding="utf-8")
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.append({
            "id": "job-apply-1", "status": "success", "result_ready": True, "project_name": "clip",
            "crop_coords": {"crop_x": 0.05, "crop_y": 0.65, "crop_width": 0.9, "crop_height": 0.28},
            "result": {
                "project_name": "clip", "project_dir": str(proj_dir), "video": str(vid),
                "raw_srt": str(proj_dir / "clip.raw.srt"), "translated_srt": str(proj_dir / "clip.vi.srt"),
            },
        })
        r_apply = self.client.post(f"/api/channel-presets/{pid}/apply", json={"job_id": "job-apply-1", "activate": True})
        self.assertEqual(r_apply.status_code, 200)
        applied = r_apply.get_json()
        self.assertEqual(applied["glossary_id"], gl["id"])
        self.assertEqual(applied["publish_defaults"]["profile_ids"], [prof["id"]])
        self.assertEqual(applied["publish_defaults"]["missing_profile_ids"], [missing_uuid])
        self.assertTrue(any("tài khoản Publish Hub" in w for w in applied["warnings"]))
        copied_wm = Path(applied["editor_options"]["watermark_path"])
        self.assertTrue(copied_wm.is_file())
        self.assertEqual(copied_wm.parent.name, ".vietsub-assets")

        # Now delete the glossary AND the preset watermark PNG externally -> orphan fallbacks kick in gracefully!
        glossary.delete_glossary(vietsub_app.CONFIG_DIR, gl["id"])
        Path(wm_path).unlink()
        resolved_orphan = channel_presets.resolve_preset(
            vietsub_app.CONFIG_DIR,
            channel_presets.get_preset(vietsub_app.CONFIG_DIR, pid),
            uploader_profiles=[],
            project_dir=str(proj_dir),
            inherited_crop=None,
        )
        self.assertEqual(resolved_orphan["glossary_id"], "default")
        self.assertEqual(resolved_orphan["publish_defaults"]["profile_ids"], [])
        self.assertFalse(resolved_orphan["editor_options"]["watermark_enabled"])
        self.assertEqual(resolved_orphan["editor_options"]["watermark_path"], "")
        self.assertEqual(resolved_orphan["editor_options"]["mask_mode"], "none")
        self.assertGreaterEqual(len(resolved_orphan["warnings"]), 4)

        # Delete active preset resets active_id to ""
        r_del = self.client.delete(f"/api/channel-presets/{pid}")
        self.assertEqual(r_del.status_code, 200)
        self.assertEqual(r_del.get_json()["active_id"], "")

    def test_symlink_or_junction_rejected_by_channel_presets_store(self):
        outside = self.root / "outside_presets"
        outside.mkdir()
        (outside / "channel_presets.json").write_text('{"schema_version": 1, "active_id": "", "presets": []}', encoding="utf-8")
        link_dir = self.root / "linked_presets_config"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link_dir), str(outside)], check=True, capture_output=True)
        else:
            os.symlink(outside, link_dir)
        try:
            with self.assertRaises(ValueError):
                channel_presets.load_store(link_dir, strict=True)
            with self.assertRaises(ValueError):
                channel_presets.save_store(link_dir, channel_presets.default_store())
        finally:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "rmdir", str(link_dir)], capture_output=True)
            else:
                link_dir.unlink(missing_ok=True)


class AutoPipelineEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
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
        # Stop any uploader queue bound to temp config dir cleanly
        with vietsub_app.uploader_api.lock:
            root_key = os.path.abspath(os.path.join(vietsub_app.CONFIG_DIR, "uploader"))
            inst = vietsub_app.uploader_api.instances.pop(root_key, None)
        if inst:
            inst[2].shutdown()
        vietsub_app.editor_api.jobs.shutdown()
        vietsub_app.editor_api.jobs = self.orig_editor_jobs
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
        self.temp_dir.cleanup()

    def _make_project(self, name="demo_clip", title="Tiêu đề video siêu dài vượt quá giới hạn hai mươi ký tự của Xiaohongshu và Douyin"):
        proj_dir = self.root / name
        proj_dir.mkdir(exist_ok=True)
        vid = proj_dir / f"{name}.mp4"
        vid.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        raw_srt = proj_dir / f"{name}.raw.srt"
        raw_srt.write_text(SAMPLE_RAW_SRT, encoding="utf-8")
        vi_srt = proj_dir / f"{name}.vi.srt"
        vi_srt.write_text(SAMPLE_VI_SRT, encoding="utf-8")
        result = {
            "project_name": name,
            "project_dir": str(proj_dir),
            "video": str(vid),
            "raw_srt": str(raw_srt),
            "translated_srt": str(vi_srt),
            "crop_coords": {"crop_x": 0.04, "crop_y": 0.64, "crop_width": 0.92, "crop_height": 0.30},
            "video_resolution": {"width": 1080, "height": 1920},
        }
        snap = manifest.snapshot(result, status="success")
        snap["metadata"] = {"title": title, "description": "Mô tả chi tiết", "hashtags": []}
        manifest.write_atomic(manifest.path_for(result), snap)
        return proj_dir, result

    def test_defaults_off_preserve_legacy_behavior_and_per_job_preset_pinning(self):
        # Settings default both toggles to False
        s_get = self.client.get("/api/settings").get_json()
        self.assertFalse(s_get["auto_edit_after_translate"])
        self.assertFalse(s_get["auto_queue_publish"])

        # Setting auto_queue_publish=True while auto_edit_after_translate=False stays coerced to False
        s_post = self.client.post("/api/settings", json={"auto_edit_after_translate": False, "auto_queue_publish": True}).get_json()
        self.assertFalse(s_post["auto_edit_after_translate"])
        self.assertFalse(s_post["auto_queue_publish"])

        # Per-job channel_preset_id pinning survives mid-batch preset switch
        p_a, _ = channel_presets.create_preset(vietsub_app.CONFIG_DIR, name="Preset A", activate=True)
        p_b, _ = channel_presets.create_preset(vietsub_app.CONFIG_DIR, name="Preset B", activate=False)

        with mock.patch.object(vietsub_app, "detect_video_source", return_value=mock.Mock(url="https://www.douyin.com/video/111", site="douyin")):
            j1 = self.client.post("/api/queue", json={"url": "https://www.douyin.com/video/111", "lang": "ch"}).get_json()
        self.assertEqual(j1["channel_preset_id"], p_a["id"])

        channel_presets.set_active_preset(vietsub_app.CONFIG_DIR, p_b["id"])
        with mock.patch.object(vietsub_app, "detect_video_source", return_value=mock.Mock(url="https://www.douyin.com/video/222", site="douyin")):
            j2 = self.client.post("/api/queue", json={"url": "https://www.douyin.com/video/222", "lang": "ch"}).get_json()
        self.assertEqual(j2["channel_preset_id"], p_b["id"])
        # Job 1 stays pinned to Preset A!
        q = self.client.get("/api/queue").get_json()["jobs"]
        by_id = {item["id"]: item for item in q}
        self.assertEqual(by_id[j1["id"]]["channel_preset_id"], p_a["id"])
        self.assertEqual(by_id[j2["id"]]["channel_preset_id"], p_b["id"])

        # Legacy persisted job without channel_preset_id normalizes cleanly to ""
        legacy = vietsub_app.normalize_persisted_job({
            "id": "legacy-1", "source_url": "https://www.douyin.com/video/333",
            "ocr_lang": "ch", "status": "queued", "source_type": "douyin",
        })
        self.assertEqual(legacy["channel_preset_id"], "")

        # When disabled, maybe_trigger_auto_pipeline returns None and touches nothing
        _, res = self._make_project("idle_clip")
        vietsub_app.workflow_jobs.append({
            "id": "job-off", "status": "success", "result_ready": True, "project_name": "idle_clip", "result": res,
        })
        self.assertIsNone(vietsub_app.maybe_trigger_auto_pipeline("job-off"))
        self.assertFalse(vietsub_app.editor_api.jobs.active("job-off"))

    def test_end_to_end_auto_edit_to_publish_platform_metadata_idempotency_and_bilibili_fail_closed(self):
        # Create 3 ready uploader profiles: YouTube, Xiaohongshu (20-char limit), Bilibili (public requires >=1 tag)
        store, _, uqueue = vietsub_app.uploader_api.services()
        yt = store.create("youtube", "Kênh YT")
        xhs = store.create("xiaohongshu", "Kênh XHS")
        bili = store.create("bilibili", "Kênh Bili")
        for p in (yt, xhs, bili):
            store.update_session(p["id"], "alive", "logged_in")

        preset, _ = channel_presets.create_preset(
            vietsub_app.CONFIG_DIR,
            name="Kênh Đa Nền Tảng",
            editor_options={"reframe": "vertical", "mask_mode": "none", "subtitle_animation": "word_pop"},
            publish_defaults={
                "profile_ids": [yt["id"], xhs["id"], bili["id"]],
                "upload_mode": "assisted",
                "visibility": "public",
                "confirm_public_auto": False,
            },
            activate=True,
        )
        self.client.post("/api/settings", json={"auto_edit_after_translate": True, "auto_queue_publish": True})

        proj_dir, res = self._make_project("e2e_clip")
        job_id = "job-e2e-1"
        vietsub_app.workflow_jobs.append({
            "id": job_id, "status": "success", "result_ready": True, "project_name": "e2e_clip",
            "channel_preset_id": preset["id"], "result": res,
        })

        edited_mp4 = proj_dir / "e2e_clip.edited.mp4"

        def fake_render(identifier, project, options, action, **_kw):
            edited_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128)
            return {
                "output_path": str(edited_mp4),
                "output_kind": "video",
                "assets": {"edited_video": str(edited_mp4)},
                "warnings": [],
            }

        # Freeze upload worker execution so jobs stay inspectable in queue without browser launch
        with mock.patch.object(editor_renderer, "execute", side_effect=fake_render), \
             mock.patch.object(vietsub_app, "resolve_ffmpeg_executable", return_value="ffmpeg"), \
             mock.patch.object(vietsub_app, "resolve_ffprobe_executable", return_value="ffprobe"), \
             mock.patch.object(uqueue, "start", return_value=None):
            task = vietsub_app.maybe_trigger_auto_pipeline(job_id)
            self.assertIsNotNone(task)

            ok = _wait_until(lambda: (
                not vietsub_app.editor_api.jobs.active(job_id)
                and manifest.snapshot(res).get("stages", {}).get("pipeline", {}).get("auto_publish") == "queued"
            ), timeout=10.0)
            self.assertTrue(ok, f"Pipeline did not reach queued publish: {manifest.snapshot(res).get('stages', {}).get('pipeline')}")

            pipe = manifest.snapshot(res)["stages"]["pipeline"]
            self.assertEqual(pipe["auto_edit"], "success")
            self.assertEqual(pipe["auto_publish"], "queued")
            # YouTube + Xiaohongshu queued; Bilibili public with 0 hashtags failed closed with warning!
            jobs_in_uqueue = uqueue.snapshot()
            self.assertEqual(len(jobs_in_uqueue), 2)
            by_plat = {j["platform"]: j for j in jobs_in_uqueue}
            self.assertIn("youtube", by_plat)
            self.assertIn("xiaohongshu", by_plat)
            self.assertNotIn("bilibili", by_plat, "Bilibili public without hashtags must fail closed")
            self.assertLessEqual(len(by_plat["xiaohongshu"]["metadata"]["title"]), 20)
            self.assertGreater(len(by_plat["youtube"]["metadata"]["title"]), 20)
            self.assertEqual(by_plat["youtube"]["upload_mode"], "assisted")
            self.assertTrue(by_plat["youtube"]["request_id"].startswith("pipe_"))
            self.assertTrue(any("Bilibili" in w for w in pipe.get("warnings", [])))

            # Idempotency: re-running enqueue_pipeline_upload does NOT duplicate jobs
            second = vietsub_app.uploader_api.enqueue_pipeline_upload(
                job_id,
                preset["publish_defaults"],
                preset_id=preset["id"],
            )
            self.assertEqual(second["status"], "queued")
            self.assertEqual(len(uqueue.snapshot()), 2, "Deterministic request_id must prevent duplicate uploads")

            # Safety gate: public + auto without confirm_public_auto is blocked!
            blocked = vietsub_app.uploader_api.enqueue_pipeline_upload(
                job_id,
                {"profile_ids": [yt["id"]], "upload_mode": "auto", "visibility": "public", "confirm_public_auto": False},
                preset_id=preset["id"],
            )
            self.assertEqual(blocked["status"], "skipped")
            self.assertIn("Công khai", blocked["error"])

    def test_failed_render_skips_publish_and_failed_vietsub_skips_editor(self):
        store, _, uqueue = vietsub_app.uploader_api.services()
        yt = store.create("youtube", "YT Fail Test")
        store.update_session(yt["id"], "alive", "logged_in")
        preset, _ = channel_presets.create_preset(
            vietsub_app.CONFIG_DIR,
            name="Fail Guard",
            editor_options={"mask_mode": "none"},
            publish_defaults={"profile_ids": [yt["id"]], "upload_mode": "assisted", "visibility": "private"},
            activate=True,
        )
        self.client.post("/api/settings", json={"auto_edit_after_translate": True, "auto_queue_publish": True})

        # 1. Failed VietSub job -> skipped completely
        _, res_fail = self._make_project("vs_fail")
        vietsub_app.workflow_jobs.append({
            "id": "job-vs-fail", "status": "error", "result_ready": False, "result": res_fail,
        })
        self.assertIsNone(vietsub_app.maybe_trigger_auto_pipeline("job-vs-fail"))

        # 2. Render fails -> after_terminal sets auto_publish='skipped' and never touches UploadQueue
        _, res_render_fail = self._make_project("render_fail")
        vietsub_app.workflow_jobs.append({
            "id": "job-rf", "status": "success", "result_ready": True,
            "channel_preset_id": preset["id"], "result": res_render_fail,
        })
        with mock.patch.object(editor_renderer, "execute", side_effect=RuntimeError("FFmpeg test crash")), \
             mock.patch.object(vietsub_app, "resolve_ffmpeg_executable", return_value="ffmpeg"), \
             mock.patch.object(vietsub_app, "resolve_ffprobe_executable", return_value="ffprobe"), \
             mock.patch.object(uqueue, "start", return_value=None):
            task = vietsub_app.maybe_trigger_auto_pipeline("job-rf")
            self.assertIsNotNone(task)
            ok = _wait_until(lambda: (
                not vietsub_app.editor_api.jobs.active("job-rf")
                and manifest.snapshot(res_render_fail).get("stages", {}).get("pipeline", {}).get("auto_edit") == "error"
            ), timeout=10.0)
            self.assertTrue(ok)
            pipe = manifest.snapshot(res_render_fail)["stages"]["pipeline"]
            self.assertEqual(pipe["auto_publish"], "skipped")
            self.assertEqual(len(uqueue.snapshot()), 0)


class AiHookSuggestionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.orig_config_dir = vietsub_app.CONFIG_DIR
        self.orig_config_path = vietsub_app.CONFIG_PATH
        self.orig_testing = vietsub_app.app.config.get("TESTING")
        self.orig_jobs = copy.deepcopy(vietsub_app.workflow_jobs)
        vietsub_app.CONFIG_DIR = str(self.root / "config")
        vietsub_app.CONFIG_PATH = str(self.root / "config" / "config.json")
        os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
        vietsub_app.app.config["TESTING"] = True

        self.proj_dir = self.root / "hook_proj"
        self.proj_dir.mkdir()
        (self.proj_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
        (self.proj_dir / "clip.raw.srt").write_text(SAMPLE_RAW_SRT, encoding="utf-8")
        (self.proj_dir / "clip.vi.srt").write_text(SAMPLE_VI_SRT, encoding="utf-8")
        self.job_id = "job-hooks-1"
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.append({
            "id": self.job_id, "status": "success", "result_ready": True, "project_name": "clip",
            "result": {
                "project_name": "clip", "project_dir": str(self.proj_dir),
                "video": str(self.proj_dir / "clip.mp4"),
                "raw_srt": str(self.proj_dir / "clip.raw.srt"),
                "translated_srt": str(self.proj_dir / "clip.vi.srt"),
            },
        })
        self.client = vietsub_app.app.test_client()

    def tearDown(self):
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.extend(self.orig_jobs)
        vietsub_app.CONFIG_DIR = self.orig_config_dir
        vietsub_app.CONFIG_PATH = self.orig_config_path
        vietsub_app.app.config["TESTING"] = self.orig_testing
        self.temp_dir.cleanup()

    def test_prompt_bounds_and_parser_validation(self):
        long_srt = "\n\n".join(f"{i}\n00:00:{i%60:02d},000 --> 00:00:{i%60:02d},500\nCâu phụ đề dài số {i} rất kịch tính." for i in range(1, 800))
        prompt = publish.build_hooks_prompt(long_srt, "Phim dài", style_note="Hài hước")
        self.assertLess(len(prompt), 18500)
        self.assertIn("Hài hước", prompt)

        # Parses fenced JSON object, strips numbering/hashtags, rejects CJK
        parsed = publish.parse_hooks_response(
            '```json\n{"hooks": ["1. Cú twist cuối khiến ai cũng ngỡ ngàng!", "- Chưởng môn ra tay một chiêu định càn khôn #vietsub", "3. Đừng bỏ lỡ giây thứ 10!", "青云宗 invalid"]}\n```'
        )
        self.assertEqual(len(parsed), 3)
        self.assertTrue(all(len(h) <= 60 and "#" not in h for h in parsed))

        # Rejects fewer than 3 valid hooks
        with self.assertRaises(ValueError):
            publish.parse_hooks_response('{"hooks": ["Chỉ một câu", "Hai câu"]}')

    def test_suggest_hooks_notebook_400_api_200_and_redaction(self):
        # 1. Notebook provider -> HTTP 400 with clear Vietnamese guidance
        self.client.post("/api/settings", json={"translation_provider": "notebook", "notebook_url": "https://gemini.google.com/notebook/abc"})
        r_nb = self.client.post(f"/api/editor/projects/{self.job_id}/suggest-hooks", json={})
        self.assertEqual(r_nb.status_code, 400)
        self.assertIn("API", r_nb.get_json()["error"])

        # 2. OpenAI-compatible API -> HTTP 200 with 3-5 hooks
        fake_runtime = {
            "provider": "deepseek", "label": "DeepSeek API", "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat", "api_key": "sk-secret-hook-test-99999", "requires_key": True,
            "translation_instructions": "Dịch chuẩn.", "glossary": {"style_note": "Kịch tính"},
        }
        fake_json = json.dumps({
            "hooks": [
                "Chưởng môn xuất hiện, cả tông môn chấn động!",
                "Một chiêu Cửu Thiên Kiếm Quyết quét sạch đối thủ",
                "Xem tới cuối mới thấy sức mạnh thật sự!",
                "Cao thủ ẩn danh chính thức lộ diện!",
            ]
        }, ensure_ascii=False)
        with mock.patch.object(vietsub_app, "get_translation_runtime_config", return_value=fake_runtime), \
             mock.patch.object(vietsub_app, "call_openai_compatible_api", return_value=fake_json):
            r_ok = self.client.post(f"/api/editor/projects/{self.job_id}/suggest-hooks", json={})
        self.assertEqual(r_ok.status_code, 200)
        body = r_ok.get_json()
        self.assertEqual(len(body["hooks"]), 4)
        self.assertEqual(body["provider"], "DeepSeek API")

        # 3. Upstream API error redacts secret API key
        with mock.patch.object(vietsub_app, "get_translation_runtime_config", return_value=fake_runtime), \
             mock.patch.object(vietsub_app, "call_openai_compatible_api", side_effect=vietsub_app.TranslationApiError("Upstream failed with sk-secret-hook-test-99999")):
            r_err = self.client.post(f"/api/editor/projects/{self.job_id}/suggest-hooks", json={})
        self.assertEqual(r_err.status_code, 502)
        self.assertNotIn("sk-secret-hook-test-99999", r_err.get_json()["error"])


class Phase32SecurityControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.orig_config_dir = vietsub_app.CONFIG_DIR
        self.orig_testing = vietsub_app.app.config.get("TESTING")
        self.orig_jobs = copy.deepcopy(vietsub_app.workflow_jobs)
        vietsub_app.CONFIG_DIR = str(self.root / "config")
        os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
        proj = self.root / "sec_p32"
        proj.mkdir()
        (proj / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
        (proj / "clip.raw.srt").write_text(SAMPLE_RAW_SRT, encoding="utf-8")
        (proj / "clip.vi.srt").write_text(SAMPLE_VI_SRT, encoding="utf-8")
        self.job_id = "job-sec-p32"
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.append({
            "id": self.job_id, "status": "success", "result_ready": True, "project_name": "clip",
            "result": {
                "project_name": "clip", "project_dir": str(proj),
                "video": str(proj / "clip.mp4"),
                "raw_srt": str(proj / "clip.raw.srt"),
                "translated_srt": str(proj / "clip.vi.srt"),
            },
        })
        self.preset, _ = channel_presets.create_preset(vietsub_app.CONFIG_DIR, name="Sec Preset")
        vietsub_app.app.config["TESTING"] = False
        self.client = vietsub_app.app.test_client()
        self.token = vietsub_app.API_TOKEN

    def tearDown(self):
        vietsub_app.app.config["TESTING"] = self.orig_testing
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.extend(self.orig_jobs)
        vietsub_app.CONFIG_DIR = self.orig_config_dir
        self.temp_dir.cleanup()

    def _headers(self, *, token=None, origin="http://127.0.0.1:8123", host="127.0.0.1:8123", content_type="application/json"):
        headers = {"Host": host}
        if token is not None:
            headers[vietsub_app.API_TOKEN_HEADER] = token
        if origin is not None:
            headers["Origin"] = origin
        if content_type is not None:
            headers["Content-Type"] = content_type
        return headers

    def test_channel_presets_and_suggest_hooks_enforce_token_origin_host_and_json(self):
        pid = self.preset["id"]
        endpoints = [
            ("GET", "/api/channel-presets", None),
            ("POST", "/api/channel-presets", {"name": "New"}),
            ("POST", "/api/channel-presets/active", {"active_id": pid}),
            ("PUT", f"/api/channel-presets/{pid}", {"name": "Renamed"}),
            ("POST", f"/api/channel-presets/{pid}/apply", {"activate": True}),
            ("POST", f"/api/editor/projects/{self.job_id}/suggest-hooks", {}),
        ]
        for method, path, payload in endpoints:
            r_no_tok = self.client.open(path, method=method, headers=self._headers(token=None), data=json.dumps(payload) if payload is not None else None)
            self.assertEqual(r_no_tok.status_code, 403, f"Missing token failed on {method} {path}")

            r_origin = self.client.open(path, method=method, headers=self._headers(token=self.token, origin="https://evil.example"), data=json.dumps(payload) if payload is not None else None)
            self.assertEqual(r_origin.status_code, 403, f"Foreign origin failed on {method} {path}")

            r_host = self.client.open(path, method=method, headers=self._headers(token=self.token, host="evil.example:8123"), data=json.dumps(payload) if payload is not None else None)
            self.assertEqual(r_host.status_code, 403, f"Foreign host failed on {method} {path}")

            if method in {"POST", "PUT", "DELETE"}:
                r_ct = self.client.open(path, method=method, headers=self._headers(token=self.token, content_type="text/plain"), data="{}")
                self.assertEqual(r_ct.status_code, 415, f"Non-JSON 415 failed on {method} {path}")

        # Multipart watermark route rejects missing token with 403, accepts valid token + multipart PNG with 200
        import io
        r_wm_no_tok = self.client.post(
            f"/api/channel-presets/{pid}/watermark",
            headers={"Host": "127.0.0.1:8123", "Origin": "http://127.0.0.1:8123"},
            data={"image": (io.BytesIO(_valid_png_bytes()), "logo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(r_wm_no_tok.status_code, 403)

        r_wm_ok = self.client.post(
            f"/api/channel-presets/{pid}/watermark",
            headers={"Host": "127.0.0.1:8123", "Origin": "http://127.0.0.1:8123", vietsub_app.API_TOKEN_HEADER: self.token},
            data={"image": (io.BytesIO(_valid_png_bytes()), "logo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(r_wm_ok.status_code, 200)

    def test_m02_set_active_preset_syncs_glossary_and_empty_preserves_user_glossary(self):
        from vietsub import channel_presets, glossary as translation_glossary

        g_custom = translation_glossary.create_glossary(
            vietsub_app.CONFIG_DIR,
            name="Tiên Hiệp M02",
            style_note="ta-ngươi",
            entries=[{"source": "剑", "target": "Kiếm"}],
        )
        # Keep active glossary as default initially
        translation_glossary.set_active_glossary(vietsub_app.CONFIG_DIR, "default")
        preset, _ = channel_presets.create_preset(
            vietsub_app.CONFIG_DIR,
            name="Kênh Đồng Bộ Từ Điển",
            glossary_id=g_custom["id"],
        )
        r_active = self.client.post(
            "/api/channel-presets/active",
            headers=self._headers(token=self.token),
            data=json.dumps({"active_id": preset["id"]}),
        )
        self.assertEqual(r_active.status_code, 200)
        body = r_active.get_json()
        self.assertEqual(body["active_id"], preset["id"])
        self.assertIn("glossary_store", body)
        self.assertEqual(body["glossary_store"]["active_id"], g_custom["id"])
        self.assertEqual(translation_glossary.list_glossaries(vietsub_app.CONFIG_DIR)["active_id"], g_custom["id"])

        # Clearing active preset ("") keeps user's currently active glossary untouched
        r_clear = self.client.post(
            "/api/channel-presets/active",
            headers=self._headers(token=self.token),
            data=json.dumps({"active_id": ""}),
        )
        self.assertEqual(r_clear.status_code, 200)
        self.assertEqual(r_clear.get_json()["active_id"], "")
        self.assertNotIn("glossary_store", r_clear.get_json())
        self.assertEqual(translation_glossary.list_glossaries(vietsub_app.CONFIG_DIR)["active_id"], g_custom["id"])


if __name__ == "__main__":
    unittest.main()
