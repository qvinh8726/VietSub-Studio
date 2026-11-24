"""Phase 3.1 tests: Synchronized Glossaries, Inline SRT Editor, Dynamic ASS Subtitles & Security."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import app as vietsub_app
from vietsub import capcut_draft, glossary, manifest, srt_utils, storage
from vietsub.editor import filters as editor_filters
from vietsub.editor import options as editor_options
from vietsub.editor import subtitles as editor_subtitles


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


def _create_tiny_mp4(ffmpeg: str, target: Path, *, width: int = 320, height: int = 180, duration: float = 3.0) -> None:
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"color=c=#101820:s={width}x{height}:d={duration}:r=24",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest",
        str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg sample generation failed: {proc.stderr[-300:]}")


def _extract_rgb_frame(ffmpeg: str, video_path: Path, timestamp_sec: float, width: int, height: int) -> bytes:
    cmd = [
        ffmpeg, "-ss", f"{timestamp_sec:.3f}", "-i", str(video_path),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0 or len(proc.stdout) != width * height * 3:
        raise RuntimeError(f"Frame extraction failed ({len(proc.stdout)} bytes): {proc.stderr.decode('utf-8', 'replace')[-200:]}")
    return proc.stdout


def _count_matching_pixels(rgb: bytes, predicate) -> int:
    count = 0
    for i in range(0, len(rgb), 3):
        if predicate(rgb[i], rgb[i + 1], rgb[i + 2]):
            count += 1
    return count


class GlossaryUnitAndIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.orig_config_dir = vietsub_app.CONFIG_DIR
        self.orig_testing = vietsub_app.app.config.get("TESTING")
        vietsub_app.CONFIG_DIR = str(self.root / "config")
        os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
        vietsub_app.app.config["TESTING"] = True
        self.client = vietsub_app.app.test_client()

    def tearDown(self):
        vietsub_app.CONFIG_DIR = self.orig_config_dir
        vietsub_app.app.config["TESTING"] = self.orig_testing
        self.temp_dir.cleanup()

    def test_get_does_not_autocreate_file_and_crud_roundtrip(self):
        store_path = glossary.glossary_path(vietsub_app.CONFIG_DIR)
        self.assertFalse(store_path.exists())
        resp = self.client.get("/api/glossaries")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["active_id"], "default")
        self.assertEqual(len(data["profiles"]), 1)
        self.assertFalse(store_path.exists(), "GET /api/glossaries must never auto-create glossaries.json")

        # Create + activate
        create = self.client.post("/api/glossaries", json={
            "name": "Tiên Hiệp",
            "style_note": "Xưng hô ta–ngươi, giữ Hán-Việt cho tên chiêu thức.",
            "entries": [
                {"source": "青云宗", "target": "Thanh Vân Tông", "note": "Môn phái"},
                {"source": "九天剑诀", "target": "Cửu Thiên Kiếm Quyết", "note": "Chiêu thức"},
            ],
            "activate": True,
        })
        self.assertEqual(create.status_code, 201)
        created = create.get_json()
        profile_id = created["profile"]["id"]
        self.assertEqual(created["active_id"], profile_id)
        self.assertTrue(store_path.is_file())

        # Update
        upd = self.client.put(f"/api/glossaries/{profile_id}", json={
            "name": "Tiên Hiệp VIP",
            "style_note": "Xưng hô tại hạ – các hạ.",
            "entries": [
                {"source": "青云宗", "target": "Thanh Vân Tông", "note": "Đại tông môn"},
            ],
        })
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.get_json()["profile"]["name"], "Tiên Hiệp VIP")

        # Duplicate source rejected
        dup = self.client.put(f"/api/glossaries/{profile_id}", json={
            "entries": [
                {"source": "Hero", "target": "Anh hùng", "note": ""},
                {"source": "hero", "target": "Hào kiệt", "note": ""},
            ],
        })
        self.assertEqual(dup.status_code, 400)

        # Cannot delete default profile
        del_default = self.client.delete("/api/glossaries/default")
        self.assertEqual(del_default.status_code, 400)

        # Delete custom profile falls back active_id to default
        deleted = self.client.delete(f"/api/glossaries/{profile_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["active_id"], "default")

        # Missing ID returns 404
        self.assertEqual(self.client.delete("/api/glossaries/nonexistent123").status_code, 404)
        self.assertEqual(self.client.post("/api/glossaries/active", json={"active_id": "nonexistent123"}).status_code, 404)

    def test_symlink_or_junction_rejected_by_glossary_store(self):
        outside = self.root / "outside_dir"
        outside.mkdir()
        (outside / "glossaries.json").write_text('{"schema_version": 1, "active_id": "default", "profiles": []}', encoding="utf-8")
        link_dir = self.root / "linked_config"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link_dir), str(outside)], check=True, capture_output=True)
        else:
            os.symlink(outside, link_dir)
        try:
            with self.assertRaises(ValueError):
                glossary.load_store(link_dir, strict=True)
            with self.assertRaises(ValueError):
                glossary.save_store(link_dir, glossary.default_store())
        finally:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "rmdir", str(link_dir)], capture_output=True)
            else:
                link_dir.unlink(missing_ok=True)

    def test_prompt_budget_prioritization_cache_hash_and_injection(self):
        # Empty glossary preserves byte-identical prompt & cache fingerprint
        base_prompt = vietsub_app.build_translation_prompt(SAMPLE_RAW_SRT, "Dịch chuẩn.", source_lang="ch")
        empty_profile = glossary.default_store()["profiles"][0]
        self.assertEqual(
            vietsub_app.build_translation_prompt(SAMPLE_RAW_SRT, "Dịch chuẩn.", source_lang="ch", glossary=empty_profile),
            base_prompt,
        )
        base_runtime = {
            "provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat", "translation_instructions": "Dịch chuẩn.", "source_lang": "ch",
        }
        base_hash = vietsub_app.translation_cache_fingerprint(base_runtime)
        self.assertEqual(
            vietsub_app.translation_cache_fingerprint({**base_runtime, "glossary": empty_profile, "glossary_hash": ""}),
            base_hash,
        )

        # Build a large glossary exceeding 4,000 chars with a target term at the very end
        entries = [
            {"source": f"无关术语{i:03d}", "target": f"Thuật ngữ phụ dài số {i:03d} để vượt giới hạn ký tự", "note": "Ghi chú thêm cho đủ độ dài"}
            for i in range(120)
        ]
        entries.append({"source": "青云宗", "target": "Thanh Vân Tông", "note": "Môn phái chính"})
        big_profile = {"id": "big", "name": "Tiên Hiệp", "style_note": "Xưng hô ta–ngươi", "entries": entries}

        block = glossary.format_prompt_block(big_profile, source_text=SAMPLE_RAW_SRT, max_chars=4000)
        self.assertLessEqual(len(block), 4000)
        self.assertIn("青云宗 => Thanh Vân Tông", block, "Matched entry from current chunk must be prioritized")

        # Cache fingerprint changes when active glossary changes, even for tail entries
        hash_with_big = vietsub_app.translation_cache_fingerprint({**base_runtime, "glossary": big_profile})
        self.assertNotEqual(hash_with_big, base_hash)
        mutated = copy.deepcopy(big_profile)
        mutated["entries"][100]["target"] = "Đổi nghĩa mục nằm ngoài 4000 ký tự"
        self.assertNotEqual(vietsub_app.translation_cache_fingerprint({**base_runtime, "glossary": mutated}), hash_with_big)

        # OpenAI-compatible API chunk injection
        captured_messages = []
        def fake_completion(rc, messages, *, stream):
            captured_messages.append(messages)
            return f"```srt\n{SAMPLE_VI_SRT}```"

        with mock.patch.object(vietsub_app, "request_translation_completion", side_effect=fake_completion):
            vietsub_app.translate_api_chunk(
                {**base_runtime, "label": "DeepSeek", "glossary": big_profile},
                SAMPLE_RAW_SRT, 1, 1,
            )
        self.assertIn("青云宗 => Thanh Vân Tông", captured_messages[0][1]["content"])
        self.assertIn("Xưng hô ta–ngươi", captured_messages[0][1]["content"])

        # Gemini Notebook chunk injection
        filled_prompts = []
        class FakeInput:
            def focus(self): pass
            def fill(self, text, timeout=None): filled_prompts.append(text)
        class FakeResp:
            def inner_text(self): return f"```srt\n{SAMPLE_VI_SRT}```"
        class FakeSubmit:
            def click(self): pass

        responses = [None, FakeResp(), FakeResp(), FakeResp(), FakeResp()]
        with mock.patch.object(vietsub_app, "find_last_response", side_effect=lambda *a, **kw: responses.pop(0) if len(responses) > 1 else responses[0]), \
             mock.patch.object(vietsub_app, "first_visible_locator", return_value=FakeSubmit()), \
             mock.patch.object(vietsub_app, "is_gemini_generating", return_value=False), \
             mock.patch.object(vietsub_app, "extract_gemini_translated_srt", return_value=SAMPLE_VI_SRT), \
             mock.patch("time.sleep", return_value=None):
            vietsub_app.translate_gemini_chunk(
                object(), FakeInput(), [], [], SAMPLE_RAW_SRT, 1, 1,
                translation_instructions="Dịch chuẩn.", source_lang="ch", glossary=big_profile,
            )
        self.assertEqual(len(filled_prompts), 1)
        self.assertIn("青云宗 => Thanh Vân Tông", filled_prompts[0])

    def test_apply_glossary_single_pass_no_chaining_preserves_timestamps(self):
        profile = {
            "id": "p1", "name": "Test", "style_note": "",
            "entries": [
                {"source": "Kiếm Thánh", "target": "Thánh Kiếm Sư", "note": ""},
                {"source": "Thánh Kiếm Sư", "target": "BỊ CHUYỂN CHUỖI", "note": ""},
                {"source": "Kiếm", "target": "Đao", "note": ""},
                {"source": "ai", "target": "AI", "note": ""},
            ],
        }
        cues = [
            {"index": 1, "start_ms": 100, "end_ms": 1500, "text": "Kiếm Thánh cầm Kiếm nói với mọi người rằng ai cũng biết."},
            {"index": 2, "start_ms": 1600, "end_ms": 3000, "text": "Cái bàn và mái nhà không bị đổi chữ bên trong."},
        ]
        updated, stats = glossary.apply_glossary_to_cues(cues, profile)
        self.assertEqual(updated[0]["start_ms"], 100)
        self.assertEqual(updated[0]["end_ms"], 1500)
        self.assertEqual(updated[0]["index"], 1)
        self.assertIn("Thánh Kiếm Sư", updated[0]["text"], "Longest compound term wins")
        self.assertNotIn("BỊ CHUYỂN CHUỖI", updated[0]["text"], "Single pass must not chain A->B->C")
        self.assertIn("cầm Đao", updated[0]["text"])
        self.assertIn("rằng AI cũng", updated[0]["text"])
        self.assertEqual(updated[1]["text"], cues[1]["text"], "Word boundary must protect 'Cái'/'mái' from 'ai'")
        self.assertEqual(stats["changed_cues"], 1)
        self.assertEqual(stats["replacements"], 3)


class InlineSrtEditorAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.orig_config_dir = vietsub_app.CONFIG_DIR
        self.orig_testing = vietsub_app.app.config.get("TESTING")
        self.orig_jobs = copy.deepcopy(vietsub_app.workflow_jobs)
        self.orig_current_job = vietsub_app.current_job_id
        self.orig_prefetch_job = vietsub_app.prefetch_job_id

        vietsub_app.CONFIG_DIR = str(self.root / "config")
        os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
        vietsub_app.app.config["TESTING"] = True

        self.project_dir = self.root / "project_demo"
        self.project_dir.mkdir()
        self.video_path = self.project_dir / "demo.mp4"
        self.video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        self.raw_srt = self.project_dir / "demo.raw.srt"
        self.raw_srt.write_text(SAMPLE_RAW_SRT, encoding="utf-8")
        self.vi_srt = self.project_dir / "demo.vi.srt"
        self.vi_srt.write_text(SAMPLE_VI_SRT, encoding="utf-8")

        self.job_id = "job-srt-test-1"
        self.result = {
            "project_name": "demo",
            "project_dir": str(self.project_dir),
            "video": str(self.video_path),
            "raw_srt": str(self.raw_srt),
            "translated_srt": str(self.vi_srt),
        }
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.append({
            "id": self.job_id,
            "status": "success",
            "result_ready": True,
            "video_id": "v123",
            "source_url": "",
            "project_name": "demo",
            "result": copy.deepcopy(self.result),
        })
        vietsub_app.current_job_id = None
        vietsub_app.prefetch_job_id = None
        self.client = vietsub_app.app.test_client()

    def tearDown(self):
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.extend(self.orig_jobs)
        vietsub_app.current_job_id = self.orig_current_job
        vietsub_app.prefetch_job_id = self.orig_prefetch_job
        vietsub_app.CONFIG_DIR = self.orig_config_dir
        vietsub_app.app.config["TESTING"] = self.orig_testing
        self.temp_dir.cleanup()

    def test_srt_utils_validation_and_raw_alignment(self):
        cues = srt_utils.parse_srt_text(SAMPLE_VI_SRT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(srt_utils.parse_srt_text(srt_utils.serialize_srt(cues)), cues)

        for bad in [
            "",
            "1\n00:00:05,000 --> 00:00:02,000\nReversed",
            "1\n00:00:02,000 --> 00:00:04,000\nFirst\n\n2\n00:00:01,000 --> 00:00:03,000\nOut of order",
            "1\n00:00:01,000 --> 00:00:02,000\nLine 1\n00:00:02,000 --> 00:00:03,000",
        ]:
            with self.assertRaises(ValueError):
                srt_utils.parse_srt_text(bad)

        # Time-overlap matching even when user splits cue 1 into two cues
        split_vi = [
            {"index": 1, "start_ms": 200, "end_ms": 1100, "text": "Chưởng môn"},
            {"index": 2, "start_ms": 1100, "end_ms": 2000, "text": "Thanh Vân Tông đã đến."},
            {"index": 3, "start_ms": 2200, "end_ms": 4500, "text": "Cửu Thiên Kiếm Quyết uy lực vô song!"},
        ]
        raw_cues = srt_utils.parse_srt_text(SAMPLE_RAW_SRT)
        aligned = srt_utils.match_raw_cues(split_vi, raw_cues)
        self.assertEqual(aligned[0], "青云宗掌门来了")
        self.assertEqual(aligned[1], "青云宗掌门来了")
        self.assertEqual(aligned[2], "九天剑诀威力无穷")

    def test_srt_api_crud_backup_restore_glossary_and_manual_validation(self):
        # GET
        resp = self.client.get(f"/api/editor/projects/{self.job_id}/srt")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(len(body["cues"]), 2)
        self.assertTrue(body["has_raw"])
        self.assertEqual(body["cues"][0]["raw_text"], "青云宗掌门来了")
        self.assertFalse(body["has_backup"])

        # Restore before backup exists -> 404
        no_bak = self.client.post(f"/api/editor/projects/{self.job_id}/srt/restore-backup", json={})
        self.assertEqual(no_bak.status_code, 404)

        # PUT with an added 3rd cue -> creates .bak, sets srt_manually_edited=True
        edited_cues = [
            {"index": 1, "start_ms": 200, "end_ms": 2000, "text": "Chưởng môn đã xuất hiện!"},
            {"index": 2, "start_ms": 2200, "end_ms": 4500, "text": "Cửu Thiên Kiếm Quyết thật kinh khủng!"},
            {"index": 3, "start_ms": 4600, "end_ms": 6000, "text": "Thêm câu mới thủ công."},
        ]
        put = self.client.put(f"/api/editor/projects/{self.job_id}/srt", json={"cues": edited_cues})
        self.assertEqual(put.status_code, 200)
        put_data = put.get_json()
        self.assertTrue(put_data["has_backup"])
        self.assertTrue(put_data["srt_manually_edited"])
        self.assertEqual(len(put_data["cues"]), 3)
        self.assertTrue(Path(srt_utils.backup_path_for(self.vi_srt)).is_file())

        # validate_project_result accepts manually edited SRT (3 cues vs 2 raw cues),
        # while allow_manual_srt=False still enforces strict auto-translation parity!
        self.assertTrue(vietsub_app.validate_project_result(self.result))
        with self.assertRaises(ValueError):
            vietsub_app.validate_project_result(self.result, allow_manual_srt=False)

        # Storage cleanup candidates include .bak when keep_srt=False, keep it when keep_srt=True
        job_record = vietsub_app.workflow_jobs[0]
        delete_all = storage.candidates(job_record, keep_srt=False)
        keep_subs = storage.candidates(job_record, keep_srt=True)
        bak_canonical = storage.canonical(srt_utils.backup_path_for(self.vi_srt))
        self.assertIn(bak_canonical, delete_all)
        self.assertNotIn(bak_canonical, keep_subs)

        # Apply glossary (dry-run save=False)
        prof = glossary.create_glossary(
            vietsub_app.CONFIG_DIR,
            name="Kiếm Hiệp",
            entries=[{"source": "kinh khủng", "target": "vô song", "note": ""}],
        )
        applied = self.client.post(
            f"/api/editor/projects/{self.job_id}/srt/apply-glossary",
            json={"glossary_id": prof["id"], "cues": edited_cues, "save": False},
        )
        self.assertEqual(applied.status_code, 200)
        ap_data = applied.get_json()
        self.assertEqual(ap_data["changed_cues"], 1)
        self.assertEqual(ap_data["replacements"], 1)
        self.assertIn("vô song", ap_data["cues"][1]["text"])
        self.assertFalse(ap_data["saved"])

        # Restore .bak brings back original 2 cues
        restored = self.client.post(f"/api/editor/projects/{self.job_id}/srt/restore-backup", json={})
        self.assertEqual(restored.status_code, 200)
        res_data = restored.get_json()
        self.assertEqual(len(res_data["cues"]), 2)
        self.assertEqual(res_data["cues"][0]["text"], "Chưởng môn Thanh Vân Tông đã đến.")

    def test_srt_put_returns_409_when_editor_vietsub_or_uploader_active(self):
        cues = srt_utils.parse_srt_text(SAMPLE_VI_SRT)
        # 1. Active EditorJobs
        with mock.patch.object(vietsub_app.editor_api.jobs, "active", return_value=True):
            r1 = self.client.put(f"/api/editor/projects/{self.job_id}/srt", json={"cues": cues})
            self.assertEqual(r1.status_code, 409)

        # 2. Active VietSub current_job_id
        vietsub_app.current_job_id = self.job_id
        try:
            r2 = self.client.put(f"/api/editor/projects/{self.job_id}/srt", json={"cues": cues})
            self.assertEqual(r2.status_code, 409)
        finally:
            vietsub_app.current_job_id = None

        # 3. Protected by Uploader metadata generation
        with mock.patch.object(vietsub_app.uploader_api, "protected_paths", return_value={storage.canonical(str(self.vi_srt))}):
            r3 = self.client.put(f"/api/editor/projects/{self.job_id}/srt", json={"cues": cues})
            self.assertEqual(r3.status_code, 409)

    def test_tts_cache_reuses_unchanged_cue_after_inline_srt_edit(self):
        from vietsub.editor import tts as editor_tts

        ffmpeg = vietsub_app.resolve_ffmpeg_executable()
        ffprobe = vietsub_app.resolve_ffprobe_executable()
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/FFprobe required for TTS pipeline cache test")

        synth_calls = []
        def fake_synth(text, voice, output_path, **_kw):
            synth_calls.append(text)
            # Generate 0.6s silent wav as fake TTS output
            subprocess.run([
                ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=0.6",
                "-c:a", "pcm_s16le", "-f", "wav", str(output_path),
            ], check=True, capture_output=True)
            return {"provider": "fake", "voice": voice, "warnings": []}

        cache_dir = self.root / "tts_cache"
        service = editor_tts.TTSService(ffmpeg, ffprobe, str(cache_dir))
        fake_provider = mock.Mock()
        fake_provider.provider = "fake"
        fake_provider.concurrent_safe = False
        fake_provider.synthesize.side_effect = fake_synth
        service.register("fake", fake_provider)

        out1 = self.root / "run1.wav"
        service.synthesize_srt(str(self.vi_srt), str(out1), voice="voice-1", provider="fake", allow_fallback=False)
        self.assertEqual(len(synth_calls), 2)

        # Edit ONLY cue 2 via PUT /srt
        cues = srt_utils.parse_srt_text(SAMPLE_VI_SRT)
        cues[1]["text"] = "Câu hai đã được biên tập lại!"
        put = self.client.put(f"/api/editor/projects/{self.job_id}/srt", json={"cues": cues})
        self.assertEqual(put.status_code, 200)

        out2 = self.root / "run2.wav"
        res2 = service.synthesize_srt(str(self.vi_srt), str(out2), voice="voice-1", provider="fake", allow_fallback=False)
        self.assertEqual(len(synth_calls), 3, "Unchanged cue #1 must hit raw-v1 TTS cache; only edited cue #2 re-synthesizes")
        self.assertEqual(synth_calls[-1], "Câu hai đã được biên tập lại!")
        self.assertEqual(len(res2["cues"]), 2)


class DynamicSubtitleAssAndFfmpegTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.srt_path = self.root / "sub.vi.srt"
        self.srt_path.write_text(
            "1\n00:00:00,100 --> 00:00:02,600\nXin chào mọi người hôm nay trời rất đẹp!\n\n"
            "2\n00:00:02,650 --> 00:00:02,670\nRất ngắn\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_options_and_filters_validation_for_dynamic_subtitles(self):
        norm = editor_options.normalize({
            "subtitle_animation": "word_pop",
            "subtitle_highlight_color": "#FFD54A",
            "subtitle_max_words_per_chunk": 5,
        })
        self.assertEqual(norm["subtitle_animation"], "word_pop")
        self.assertEqual(norm["subtitle_highlight_color"].lower(), "#ffd54a")
        self.assertEqual(norm["subtitle_max_words_per_chunk"], 5)

        for bad in [
            {"subtitle_animation": "bounce"},
            {"subtitle_highlight_color": "yellow"},
            {"subtitle_max_words_per_chunk": 1},
            {"subtitle_max_words_per_chunk": 9},
            {"subtitle_max_words_per_chunk": 3.5},
            {"subtitle_max_words_per_chunk": True},
        ]:
            with self.assertRaises(ValueError, msg=str(bad)):
                editor_options.normalize(bad)
            with self.assertRaises(ValueError, msg=str(bad)):
                editor_filters._validated_options(bad)

    def test_ass_generation_static_karaoke_word_pop_and_inline_color_format(self):
        # 1. Static legacy compatibility
        static_ass = self.root / "static.ass"
        editor_subtitles.write_ass(
            self.srt_path, static_ass, 1280, 720,
            {"subtitle_animation": "static", "text_color": "#ffffff", "subtitle_highlight_color": "#ff0000"},
        )
        static_text = static_ass.read_text(encoding="utf-8")
        self.assertIn("&H00FFFFFF,&H00FFFFFF,", static_text)
        self.assertNotIn(r"\kf", static_text)
        self.assertNotIn(r"\fscx", static_text)

        # 2. Karaoke highlight: PrimaryColour = highlight (#ff2010 -> &H001020FF), Secondary = text (#ffffff -> &H00FFFFFF)
        karaoke_ass = self.root / "karaoke.ass"
        editor_subtitles.write_ass(
            self.srt_path, karaoke_ass, 1280, 720,
            {"subtitle_animation": "karaoke_highlight", "text_color": "#ffffff", "subtitle_highlight_color": "#ff2010"},
        )
        k_text = karaoke_ass.read_text(encoding="utf-8")
        self.assertIn("&H001020FF,&H00FFFFFF,", k_text)
        self.assertIn(r"{\kf", k_text)
        # Verify cue 1 kf sum == 250 centiseconds (2.600 - 0.100 = 2.50s)
        dialogue_lines = [line for line in k_text.splitlines() if line.startswith("Dialogue:")]
        import re
        kf_vals = [int(v) for v in re.findall(r"\{\\kf(\d+)\}", dialogue_lines[0])]
        self.assertEqual(sum(kf_vals), 250)

        # 3. Word Pop: inline \1c MUST be &HBBGGRR& (6 hex digits, NO alpha!), balanced chunks (8 words / max 4 -> 2 chunks of 4; 5 words / max 4 -> 3+2)
        pop_ass = self.root / "pop.ass"
        editor_subtitles.write_ass(
            self.srt_path, pop_ass, 1080, 1920,
            {"subtitle_animation": "word_pop", "text_color": "#ffffff", "subtitle_highlight_color": "#ff2010", "subtitle_max_words_per_chunk": 4},
            cue_timings={1: 2200},
        )
        p_text = pop_ass.read_text(encoding="utf-8")
        self.assertIn(r"\1c&H1020FF&", p_text, "Inline \\1c must use 6-digit BGR &HBBGGRR& without alpha")
        self.assertNotIn(r"\1c&H001020FF&", p_text, "Inline \\1c must NOT include alpha channel")
        self.assertIn(r"\1c&HFFFFFF&", p_text)
        self.assertIn(r"\fscx114\fscy114", p_text)
        # Orphan balancing check: 5 words with max 4 -> [3, 2]
        chunks = editor_subtitles._chunk_tokens(editor_subtitles._tokenize_cue("Một hai ba bốn năm"), 4)
        self.assertEqual(
            [[tok["text"] for tok in ch] for ch in chunks],
            [["Một", "hai", "ba"], ["bốn", "năm"]],
        )

        # 4. ASS override injection escaping works in dynamic modes too
        inj_srt = self.root / "inject.srt"
        inj_srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello {\\pos(0,0)} world\n", encoding="utf-8")
        inj_ass = self.root / "inject.ass"
        editor_subtitles.write_ass(inj_srt, inj_ass, 1280, 720, {"subtitle_animation": "word_pop"})
        inj_text = inj_ass.read_text(encoding="utf-8")
        self.assertNotIn(r"{\pos(0,0)}", inj_text)
        self.assertIn("\\{\\\u2060pos(0,0)\\}", inj_text)

    def test_real_ffmpeg_libass_burn_in_16x9_and_9x16_renders_highlight_color(self):
        ffmpeg = vietsub_app.resolve_ffmpeg_executable()
        ffprobe = vietsub_app.resolve_ffprobe_executable()
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/FFprobe required for libass burn-in verification")

        fonts_dir = str(Path(vietsub_app.RESOURCE_DIR) / "static" / "fonts")
        src_video = self.root / "src.mp4"
        _create_tiny_mp4(ffmpeg, src_video, width=320, height=180, duration=3.0)

        cue_srt = self.root / "burn.srt"
        cue_srt.write_text("1\n00:00:00,200 --> 00:00:02,600\nXin chào thế giới tuyệt vời\n", encoding="utf-8")

        for mode, reframe, expected_w, expected_h in [
            ("word_pop", "original", 320, 180),
            ("karaoke_highlight", "vertical", 720, 1280),
        ]:
            ass_path = self.root / f"{mode}_{reframe}.ass"
            out_video = self.root / f"{mode}_{reframe}.mp4"
            opts = editor_options.normalize({
                "mask_mode": "none",
                "subtitle_enabled": True,
                "subtitle_animation": mode,
                "subtitle_position": "bottom",
                "font_size": 72,
                "text_color": "#ffffff",
                "subtitle_highlight_color": "#ff1818",
                "subtitle_max_words_per_chunk": 3,
                "reframe": reframe,
                "output_height": 1280,
            })
            editor_subtitles.write_ass(cue_srt, ass_path, 320, 180, opts)
            graph = editor_filters.build_video_graph(
                320, 180, opts, ass_path=str(ass_path), fonts_dir=fonts_dir,
            )
            filter_expr = graph["filter_complex"]
            cmd = [
                ffmpeg, "-y", "-i", str(src_video),
                "-filter_complex", filter_expr,
                "-map", graph["output_label"], "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
                str(out_video),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, f"FFmpeg failed for {mode}/{reframe}: {proc.stderr[-400:]}")
            self.assertTrue(out_video.is_file() and out_video.stat().st_size > 0)

            # Extract mid-cue frame and verify libass genuinely painted red highlight pixels!
            rgb = _extract_rgb_frame(ffmpeg, out_video, 1.2, graph["width"], graph["height"])
            red_pixels = _count_matching_pixels(rgb, lambda r, g, b: r > 170 and g < 85 and b < 85)
            self.assertGreater(
                red_pixels, 20,
                f"Expected red highlight pixels from libass for {mode}/{reframe}, found {red_pixels}",
            )

    def test_capcut_draft_export_compatible_with_word_pop(self):
        ffmpeg = vietsub_app.resolve_ffmpeg_executable()
        ffprobe = vietsub_app.resolve_ffprobe_executable()
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/FFprobe required for CapCut Draft export test")

        proj_dir = self.root / "draft_proj"
        proj_dir.mkdir()
        vid = proj_dir / "clip.mp4"
        _create_tiny_mp4(ffmpeg, vid, width=320, height=180, duration=2.0)
        srt = proj_dir / "clip.vi.srt"
        srt.write_text(SAMPLE_VI_SRT, encoding="utf-8")
        project = {
            "project_name": "draft_demo",
            "project_dir": str(proj_dir),
            "assets": {"video": str(vid), "translated_srt": str(srt)},
        }
        opts = editor_options.normalize({
            "mask_mode": "none",
            "subtitle_animation": "word_pop",
            "subtitle_highlight_color": "#ffd54a",
            "subtitle_max_words_per_chunk": 4,
            "tts_enabled": False,
        })
        result = capcut_draft.execute(
            "taskdraft01", project, opts,
            ffmpeg=ffmpeg, ffprobe=ffprobe,
            fonts_dir=str(Path(vietsub_app.RESOURCE_DIR) / "static" / "fonts"),
            cancel=threading.Event(), update=lambda **_kw: None,
        )
        self.assertEqual(result["output_kind"], "archive")
        self.assertTrue(Path(result["output_path"]).is_file())


class Phase31SecurityControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.orig_config_dir = vietsub_app.CONFIG_DIR
        self.orig_testing = vietsub_app.app.config.get("TESTING")
        self.orig_jobs = copy.deepcopy(vietsub_app.workflow_jobs)

        vietsub_app.CONFIG_DIR = str(self.root / "config")
        os.makedirs(vietsub_app.CONFIG_DIR, exist_ok=True)
        proj = self.root / "sec_proj"
        proj.mkdir()
        (proj / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
        (proj / "clip.raw.srt").write_text(SAMPLE_RAW_SRT, encoding="utf-8")
        (proj / "clip.vi.srt").write_text(SAMPLE_VI_SRT, encoding="utf-8")
        self.job_id = "job-sec-p31"
        vietsub_app.workflow_jobs.clear()
        vietsub_app.workflow_jobs.append({
            "id": self.job_id,
            "status": "success",
            "result_ready": True,
            "project_name": "clip",
            "result": {
                "project_name": "clip",
                "project_dir": str(proj),
                "video": str(proj / "clip.mp4"),
                "raw_srt": str(proj / "clip.raw.srt"),
                "translated_srt": str(proj / "clip.vi.srt"),
            },
        })
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

    def test_glossary_and_srt_routes_enforce_token_origin_host_and_json(self):
        endpoints = [
            ("GET", "/api/glossaries", None),
            ("POST", "/api/glossaries", {"name": "SecTest"}),
            ("POST", "/api/glossaries/active", {"active_id": "default"}),
            ("PUT", "/api/glossaries/default", {"name": "Mặc định"}),
            ("GET", f"/api/editor/projects/{self.job_id}/srt", None),
            ("PUT", f"/api/editor/projects/{self.job_id}/srt", {"cues": srt_utils.parse_srt_text(SAMPLE_VI_SRT)}),
            ("POST", f"/api/editor/projects/{self.job_id}/srt/apply-glossary", {"glossary_id": "default"}),
            ("POST", f"/api/editor/projects/{self.job_id}/srt/restore-backup", {}),
        ]
        for method, path, payload in endpoints:
            # Missing token -> 403
            r_no_tok = self.client.open(path, method=method, headers=self._headers(token=None), data=json.dumps(payload) if payload is not None else None)
            self.assertEqual(r_no_tok.status_code, 403, f"Missing token failed on {method} {path}")

            # Foreign Origin -> 403
            r_origin = self.client.open(path, method=method, headers=self._headers(token=self.token, origin="https://evil.example"), data=json.dumps(payload) if payload is not None else None)
            self.assertEqual(r_origin.status_code, 403, f"Foreign origin failed on {method} {path}")

            # Foreign Host -> 403
            r_host = self.client.open(path, method=method, headers=self._headers(token=self.token, host="evil.example:8123"), data=json.dumps(payload) if payload is not None else None)
            self.assertEqual(r_host.status_code, 403, f"Foreign host failed on {method} {path}")

            # Non-JSON Content-Type on mutating methods -> 415
            if method in {"POST", "PUT", "DELETE"}:
                r_ct = self.client.open(path, method=method, headers=self._headers(token=self.token, content_type="text/plain"), data="{}")
                self.assertEqual(r_ct.status_code, 415, f"Non-JSON 415 failed on {method} {path}")

        # Authorized roundtrip works under TESTING=False
        ok_get = self.client.get("/api/glossaries", headers=self._headers(token=self.token, content_type=None))
        self.assertEqual(ok_get.status_code, 200)
        ok_srt = self.client.get(f"/api/editor/projects/{self.job_id}/srt", headers=self._headers(token=self.token, content_type=None))
        self.assertEqual(ok_srt.status_code, 200)


if __name__ == "__main__":
    unittest.main()
