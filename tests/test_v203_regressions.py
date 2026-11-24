"""Regression and TESTING=False security-boundary suite for VietSub Studio v2.0.3."""

import io
import json
import os
import tempfile
import threading
import time
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

try:
    from . import _isolation  # noqa: F401
except ImportError:
    import _isolation  # noqa: F401

import app
from vietsub import manifest
from vietsub.editor.tts import TTSService
from vietsub.sources import web, ytdlp_source
from vietsub.uploader import adapters, browser as uploader_browser
from vietsub.uploader.queue import UploadQueue


def numbered_srt(entries):
    return "\n\n".join(f"{idx}\n{tc}\n{text}" for idx, tc, text in entries) + "\n"


class _FakeNode:
    def __init__(self, text="", attrs=None, children=None, visible=True):
        self._text = text
        self._attrs = attrs or {}
        self._children = children or {}
        self._visible = visible

    def count(self):
        return 1

    def nth(self, index):
        return self

    def is_visible(self):
        return self._visible

    def inner_text(self, **_kwargs):
        return self._text

    def get_attribute(self, name, **_kwargs):
        return self._attrs.get(name)

    def locator(self, selector):
        items = self._children.get(selector, [])
        return _FakeGroup(items)


class _FakeGroup:
    def __init__(self, items):
        self._items = items

    def count(self):
        return len(self._items)

    def nth(self, index):
        return self._items[index]


class _FakeRun:
    def __init__(self, url, root):
        self.page = type("FakePage", (), {"url": url, "locator": lambda _self, _s: _FakeGroup([])})()
        self.root = root


class H01PersistencePlatformTests(unittest.TestCase):
    def test_normalize_persisted_job_preserves_all_download_platforms(self):
        for source_type in ("local", "douyin", "tiktok", "youtube", "bilibili", "xiaohongshu", "kuaishou"):
            with self.subTest(source_type=source_type):
                normalized = app.normalize_persisted_job({
                    "id": "job-" + source_type,
                    "source_type": source_type,
                    "source_url": "https://example.test/video/1",
                    "status": "queued",
                    "ocr_lang": "ch",
                    "crop_coords": {"crop_x": 0.0, "crop_y": 0.7, "crop_width": 1.0, "crop_height": 0.3},
                    "result": {"source_video_deleted": True},
                })
                self.assertIsNotNone(normalized)
                self.assertEqual(normalized["source_type"], source_type)

        fallback = app.normalize_persisted_job({
            "id": "job-unknown",
            "source_type": "invalid_site",
            "status": "queued",
            "ocr_lang": "ch",
            "crop_coords": {"crop_x": 0.0, "crop_y": 0.7, "crop_width": 1.0, "crop_height": 0.3},
        })
        self.assertEqual(fallback["source_type"], "douyin")


class H02UpdateInstallGuardTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(app.app.config, TESTING=True))
        self.client = app.app.test_client()

    def test_update_install_returns_409_when_editor_or_uploader_active(self):
        with patch.object(app.editor_api.jobs, "any_active", return_value=True):
            res = self.client.post("/api/update/install", json={})
            self.assertEqual(res.status_code, 409)
            self.assertIn("Auto Edit", res.get_json()["error"])

        with (
            patch.object(app.editor_api.jobs, "any_active", return_value=False),
            patch.object(app.uploader_api, "any_active", return_value=True),
        ):
            res = self.client.post("/api/update/install", json={})
            self.assertEqual(res.status_code, 409)
            self.assertIn("Publish Hub", res.get_json()["error"])

    def test_schedule_app_exit_runs_bounded_request_app_shutdown_before_exit(self):
        events = []

        def fake_shutdown():
            events.append("shutdown")

        def fake_exit(code):
            events.append(("exit", code))

        done = threading.Event()

        class SyncThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

            def join(self, timeout=None):
                events.append(("join", timeout))

        def capture_exit(code):
            fake_exit(code)
            done.set()

        with (
            patch.object(app, "request_app_shutdown", side_effect=fake_shutdown),
            patch.object(app.os, "_exit", side_effect=capture_exit),
            patch.object(app, "Thread", side_effect=lambda target=None, name=None, daemon=None: threading.Thread(target=target, name=name, daemon=daemon)),
        ):
            app.schedule_app_exit(delay_seconds=0.01, shutdown_timeout=1.5)
            self.assertTrue(done.wait(3))
        self.assertEqual(events, ["shutdown", ("exit", 0)])


class H03AndM03AdapterTests(unittest.TestCase):
    def test_youtube_shorts_and_vietnamese_studio_confirmations(self):
        adapter = adapters.adapter_for("youtube")
        for host in ("www.youtube.com", "youtube.com"):
            for text in (
                "Video đã được xuất bản",
                "  Đã   xuất bản video ",
                "Video đã tải lên",
                "Đã tải video lên",
                "Video đã được lên lịch",
            ):
                with self.subTest(host=host, text=text):
                    link = _FakeNode(attrs={"href": f"https://{host}/shorts/aBcDeFgHiJk?feature=share"})
                    dialog = _FakeNode(text=text, children={"a[href]": [link]})
                    root = _FakeNode(children={"ytcp-video-share-dialog": [dialog]})
                    run = _FakeRun("https://studio.youtube.com/", root)
                    confirmed = adapter._confirmation(run, "public")
                    self.assertIsNotNone(confirmed)
                    result_url = adapter._result_url(confirmed)
                    self.assertEqual(result_url, f"https://{host}/shorts/aBcDeFgHiJk")

        self.assertEqual(
            uploader_browser.BrowserRuntime._result(adapter, {
                "status": "success",
                "url": "https://youtube.com/shorts/aBcDeFgHiJk?si=token",
            })["url"],
            "https://youtube.com/shorts/aBcDeFgHiJk",
        )

    def test_bilibili_confirmation_substring_and_facebook_watch_query_preserved(self):
        bili = adapters.adapter_for("bilibili")
        bili_node = _FakeNode(
            text="  投稿成功 · 查看稿件 ",
            children={"a[href]": [_FakeNode(attrs={"href": "https://www.bilibili.com/video/BV1xx411c7mD/?spm=1"})]},
        )
        bili_root = _FakeNode(children={".upload-success": [bili_node]})
        confirmed = bili._confirmation(_FakeRun("https://member.bilibili.com/platform/upload/success", bili_root), "public")
        self.assertIsNotNone(confirmed)
        self.assertEqual(bili._result_url(confirmed), "https://www.bilibili.com/video/BV1xx411c7mD/")

        fb = adapters.adapter_for("facebook")
        fb_node = _FakeNode(
            children={"a[href]": [_FakeNode(attrs={"href": "https://www.facebook.com/watch/?v=1234567890&secret=drop"})]}
        )
        self.assertEqual(fb._result_url(fb_node), "https://www.facebook.com/watch/?v=1234567890")
        self.assertEqual(
            uploader_browser.BrowserRuntime._result(fb, {
                "status": "success",
                "url": "https://www.facebook.com/watch/?v=1234567890&token=secret",
            })["url"],
            "https://www.facebook.com/watch/?v=1234567890",
        )

    def test_browser_runtime_preserves_safe_business_upload_error(self):
        msg = uploader_browser.BrowserRuntime._safe_business_error(
            adapters.UploadError("Bilibili yêu cầu ít nhất 1 hashtag khi đăng công khai."),
            adapters.UploadError,
        )
        self.assertEqual(msg, "Bilibili yêu cầu ít nhất 1 hashtag khi đăng công khai.")
        redacted = uploader_browser.BrowserRuntime._safe_business_error(
            adapters.UploadError("Cookie: secret=1 https://evil.test/?token=abc"),
            adapters.UploadError,
        )
        self.assertIn("private local diagnostic screenshot", redacted)


class M01BilibiliPartCacheTests(unittest.TestCase):
    def test_bilibili_part_distinguished_in_cache_id_and_ytdlp_options(self):
        req1 = web.parse_web_url("https://www.bilibili.com/video/BV1xx411c7mD/?p=1")
        req2 = web.parse_web_url("https://www.bilibili.com/video/BV1xx411c7mD/?p=2")
        key1 = web.cache_video_id(req1.site, req1.video_id, req1.url)
        key2 = web.cache_video_id(req2.site, req2.video_id, req2.url)
        self.assertEqual(key1, "BV1xx411c7mD-p1")
        self.assertEqual(key2, "BV1xx411c7mD-p2")
        self.assertNotEqual(key1, key2)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            p1_dir = tmp_path / "bilibili-BV1xx411c7mD-p1"
            p1_dir.mkdir(parents=True)
            (p1_dir / "bilibili-BV1xx411c7mD-p1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"1" * 20)
            self.assertIsNotNone(app.find_cached_download("bilibili", key1, str(tmp_path)))
            self.assertIsNone(app.find_cached_download("bilibili", key2, str(tmp_path)))

            opts = ytdlp_source.build_ytdlp_options(tmp_path, site="bilibili", cache_id=key2)
            self.assertIn("bilibili-BV1xx411c7mD-p2", opts["outtmpl"]["default"])


class M02ParallelAndSerialTtsPruneTests(unittest.TestCase):
    def _make_service(self, root: Path):
        svc = TTSService("ffmpeg", "ffprobe", root / "cache")
        svc.cache_max_entries = 1
        svc.batch_size = 1

        class DummyAdapter:
            concurrent_safe = True

            def fork(self, cancel=None, progress=None):
                return self

            def voices(self, language="vi"):
                return [{"id": "v1", "name": "V1", "language": "vi-VN", "provider": "custom_p"}]

            def synthesize(self, text, voice, output_path):
                with wave.open(str(output_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(b"\x00\x00" * 2400)
                return {"path": str(output_path), "provider": "custom_p", "voice": voice, "warnings": []}

        def fake_canonical_batch(service, items, directory, speed, pitch):
            out = []
            for idx, (cue, raw) in enumerate(items):
                p = Path(directory) / f"norm-{cue['index']}.wav"
                with wave.open(str(p), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(b"\x00\x00" * 2400)
                out.append({**raw, "path": str(p), "frames": 2400, "duration_ms": 100.0})
            return out

        def fake_align_batch(service, items, canonical, directory):
            p = Path(directory) / "aligned.wav"
            start = items[0][0]["start_ms"]
            end = max(cue["start_ms"] + cue["window_ms"] for cue, _ in items)
            frames = max(1, (end - start) * 24000 // 1000)
            with wave.open(str(p), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(b"\x00\x00" * frames)
            fits = [{"speed": 1.0, "trimmed": False, "duration_ms": 100.0, "warnings": []} for _ in items]
            return p, fits, start, end

        svc.register("custom_p", DummyAdapter())
        return svc, fake_canonical_batch, fake_align_batch

    def test_parallel_synthesize_srt_prunes_cache_without_evicting_remaining_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "in.srt"
            srt.write_text(
                numbered_srt([
                    (1, "00:00:00,000 --> 00:00:00,200", "Câu một"),
                    (2, "00:00:00,300 --> 00:00:00,500", "Câu hai"),
                ]),
                encoding="utf-8",
            )
            svc, fake_canon, fake_align = self._make_service(root)
            calls = []
            real_prune = svc.prune_cache

            def spy_prune(*, protected=None):
                calls.append(set(protected or ()))
                return real_prune(protected=protected)

            svc.prune_cache = spy_prune
            with (
                patch("vietsub.editor.tts_pipeline.canonical_batch", side_effect=fake_canon),
                patch("vietsub.editor.tts_pipeline.align_batch", side_effect=fake_align),
            ):
                result = svc.synthesize_srt(srt, root / "out.wav", voice="v1", provider="custom_p")
            self.assertTrue(Path(result["path"]).is_file())
            self.assertGreaterEqual(len(calls), 2)
            # First batch prune protected cue 2's raw audio; final prune ran after publish.
            self.assertTrue(calls[0])
            self.assertEqual(calls[-1], set())


class M05QueueStartupCleanupAndRetryTests(unittest.TestCase):
    def test_load_persists_interrupted_and_sweeps_orphan_snapshots_and_retry_clears_stale_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"mp4")
            snapshots = root / "input-snapshots"
            active_id = "a" * 32
            orphan_id = "b" * 32
            (snapshots / f"{active_id}-keep").mkdir(parents=True)
            (snapshots / f"{orphan_id}-stale").mkdir(parents=True)
            (snapshots / f"{orphan_id}-stale" / "video.mp4").write_bytes(b"old")
            qpath = root / "queue.json"
            payload = {
                "version": 1,
                "jobs": [{
                    "id": active_id,
                    "profile_id": "p1",
                    "platform": "youtube",
                    "video": str(video),
                    "video_size": video.stat().st_size,
                    "video_mtime_ns": video.stat().st_mtime_ns,
                    "metadata": {"title": "Test", "description": "", "hashtags": ["#v"]},
                    "visibility": "private",
                    "scheduled_at": None,
                    "status": "running",
                    "commit_attempted": False,
                    "screenshot_path": "/tmp/old.png",
                    "result_url": "https://youtube.com/watch?v=old",
                    "message": "Old msg",
                    "started_at": 100.0,
                    "finished_at": 101.0,
                }],
            }
            qpath.write_text(json.dumps(payload), encoding="utf-8")
            queue = UploadQueue(qpath, lambda *args: {"status": "draft"})
            try:
                # Interrupted running job persisted to disk and orphan snapshot directory deleted.
                persisted = json.loads(qpath.read_text(encoding="utf-8"))
                self.assertEqual(persisted["jobs"][0]["status"], "error")
                self.assertFalse((snapshots / f"{orphan_id}-stale").exists())
                # Also sweep active_id dir since status is no longer ACTIVE after interrupt.
                self.assertFalse((snapshots / f"{active_id}-keep").exists())

                with patch.object(queue, "start"):
                    retried = queue.retry(active_id)
                for field, expected in (
                    ("screenshot_path", ""),
                    ("result_url", ""),
                    ("message", ""),
                    ("started_at", None),
                    ("finished_at", None),
                ):
                    self.assertEqual(retried[field], expected)
            finally:
                queue.shutdown()


class M04AndM06AndL02UploaderApiTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        temp = tempfile.TemporaryDirectory(prefix="vietsub-v203-api-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.client = app.app.test_client()
        self.enterContext(patch.dict(app.app.config, TESTING=True))
        self.enterContext(patch.object(app, "CONFIG_DIR", str(self.root)))
        self.enterContext(patch.object(app, "CONFIG_PATH", str(self.root / "config.json")))
        self.enterContext(patch.object(app, "log_to_app"))
        self.enterContext(patch.object(app.uploader_api, "instances", {}))
        self.enterContext(patch.object(app.uploader_api, "sessions", {}))
        self.enterContext(patch.object(app.uploader_api, "meta_tasks", {}))
        self.enterContext(patch.object(app.uploader_api, "media", {}))
        self.addCleanup(app.uploader_api.shutdown)

    def test_submit_validates_platform_metadata_early(self):
        profile = self.client.post("/api/uploader/profiles", json={"platform": "bilibili", "name": "Bili"}).get_json()
        app.uploader_api.services()[0].update_session(profile["id"], "alive", "session_alive")
        mp4 = b"\x00\x00\x00\x18ftypmp42" + b"x" * 30
        media = self.client.post("/api/uploader/media", data={"video": (io.BytesIO(mp4), "clip.mp4")}).get_json()

        # Public Bilibili without hashtags rejected at submit() with 400
        res = self.client.post("/api/uploader/queue", json={
            "media_id": media["id"],
            "profile_ids": [profile["id"]],
            "metadata": {"title": "Valid", "description": "", "hashtags": []},
            "visibility": "public",
            "confirm_public": True,
            "request_id": "bili-no-tags-01",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("hashtag", res.get_json()["error"].lower())

        # Bilibili title > 80 chars rejected at submit() with 400
        res = self.client.post("/api/uploader/queue", json={
            "media_id": media["id"],
            "profile_ids": [profile["id"]],
            "metadata": {"title": "a" * 81, "description": "", "hashtags": ["#ok"]},
            "visibility": "public",
            "confirm_public": True,
            "request_id": "bili-long-title-01",
        })
        self.assertEqual(res.status_code, 400)

    def test_session_screenshot_path_is_redacted_and_signed_url_works_and_delete_purges_artifacts(self):
        profile = self.client.post("/api/uploader/profiles", json={"platform": "youtube", "name": "YT"}).get_json()
        store, browser, _ = app.uploader_api.services()
        shot = Path(store.artifact_dir(profile["id"])) / "error-1.png"
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"png-body"
        shot.write_bytes(png_bytes)

        with patch.object(browser, "probe", return_value={"session_status": "error", "screenshot_path": str(shot)}):
            started = self.client.post(f"/api/uploader/profiles/{profile['id']}/check", json={})
            self.assertEqual(started.status_code, 202)
            sid = started.get_json()["id"]
            deadline = time.monotonic() + 3
            status_payload = {}
            while time.monotonic() < deadline:
                status_payload = self.client.get(f"/api/uploader/sessions/{sid}").get_json()
                if status_payload["status"] == "success":
                    break
                time.sleep(0.02)
        self.assertEqual(status_payload["status"], "success")
        self.assertNotIn("screenshot_path", json.dumps(status_payload))
        screenshot_url = status_payload["result"]["screenshot_url"]
        self.assertTrue(screenshot_url.startswith(f"/api/uploader/sessions/{sid}/screenshot?signature="))
        self.assertEqual(self.client.get(screenshot_url).get_data(), png_bytes)
        self.assertEqual(self.client.get(f"/api/uploader/sessions/{sid}/screenshot?signature=bad").status_code, 403)

        # Delete profile -> artifacts purged, browser dir retained.
        self.assertEqual(self.client.delete(f"/api/uploader/profiles/{profile['id']}").status_code, 200)
        self.assertFalse(shot.exists())
        self.assertTrue(Path(self.root / "uploader" / "profiles" / profile["id"] / "youtube" / "browser").is_dir())

    def test_upload_media_failure_removes_empty_directory(self):
        before = set(Path(self.root / "uploader" / "media").glob("*")) if (self.root / "uploader" / "media").exists() else set()
        res = self.client.post("/api/uploader/media", data={"video": (io.BytesIO(b"not-mp4"), "bad.mp4")})
        self.assertEqual(res.status_code, 400)
        after = set(Path(self.root / "uploader" / "media").glob("*")) if (self.root / "uploader" / "media").exists() else set()
        self.assertEqual(before, after)

    def test_delete_profile_is_serialized_with_queue_lease_and_caches_are_bounded(self):
        profile = self.client.post("/api/uploader/profiles", json={"platform": "youtube", "name": "Lease"}).get_json()
        store, _, _ = app.uploader_api.services()
        store.update_session(profile["id"], "alive", "session_alive")
        with store.lease(profile["id"]):
            res = self.client.delete(f"/api/uploader/profiles/{profile['id']}")
            self.assertEqual(res.status_code, 409)

        dummy = {f"k{i}": {"id": f"k{i}", "status": "success"} for i in range(260)}
        app.uploader_api._trim_cache(dummy, terminal_only=True)
        self.assertEqual(len(dummy), 256)

    def test_ai_metadata_uses_manifest_assets_translated_srt(self):
        raw = self.root / "raw.srt"
        vi_result = self.root / "result.vi.srt"
        vi_asset = self.root / "manifest_asset.vi.srt"
        clip = self.root / "clip.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 30)
        raw.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
        vi_result.write_text("1\n00:00:00,000 --> 00:00:01,000\nBản cũ\n", encoding="utf-8")
        vi_asset.write_text("1\n00:00:00,000 --> 00:00:01,000\nBản chuẩn trong manifest\n", encoding="utf-8")
        result = {
            "project_dir": str(self.root),
            "project_name": "proj",
            "video": str(clip),
            "source_video": str(clip),
            "raw_srt": str(raw),
            "translated_srt": str(vi_result),
        }
        _, value = manifest.save_project(result)
        value["assets"]["translated_srt"] = str(vi_asset)
        manifest.write_atomic(manifest.path_for(result), value)
        result.pop("translated_srt", None)
        captured_prompts = []

        def fake_completion(_cfg, messages, *, stream):
            captured_prompts.append(messages[-1]["content"])
            return json.dumps({"title": "Tiêu đề AI", "description": "Mô tả", "hashtags": ["#v1", "#v2", "#v3"]})

        with (
            patch.object(app, "workflow_jobs", [{
                "id": "job-asset-srt", "status": "success", "result_ready": True,
                "result": result, "source_video": str(clip), "source_type": "local",
            }]),
            patch.object(app, "get_translation_runtime_config", return_value={"provider": "deepseek", "label": "DeepSeek"}),
            patch.object(app, "request_translation_completion", side_effect=fake_completion),
        ):
            res = self.client.post("/api/uploader/projects/job-asset-srt/metadata", json={})
            self.assertEqual(res.status_code, 202)
            tid = res.get_json()["id"]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = self.client.get(f"/api/uploader/metadata/{tid}").get_json()
                if state["status"] in ("success", "error"):
                    break
                time.sleep(0.02)
        self.assertEqual(state["status"], "success", state)
        self.assertTrue(captured_prompts and "Bản chuẩn trong manifest" in captured_prompts[0])


class M07AndM08TranslationAndConfigTests(unittest.TestCase):
    def test_validate_translation_language_catches_ja_ko_en_and_allows_vi(self):
        ja = numbered_srt([(1, "00:00:01,000 --> 00:00:02,000", "これはテストの字幕です。"), (2, "00:00:03,000 --> 00:00:04,000", "もうすぐ出発します。")])
        ko = numbered_srt([(1, "00:00:01,000 --> 00:00:02,000", "안녕하세요 반갑습니다"), (2, "00:00:03,000 --> 00:00:04,000", "지금 바로 출발합시다")])
        en_long = numbered_srt([
            (i, f"00:00:{i:02d},000 --> 00:00:{i:02d},500", f"This is an English subtitle line number {i} for verification.")
            for i in range(1, 7)
        ])
        with self.assertRaisesRegex(ValueError, "tiếng Nhật"):
            app.validate_translation_language(ja, ja)
        with self.assertRaisesRegex(ValueError, "tiếng Hàn"):
            app.validate_translation_language(ko, ko)
        with self.assertRaisesRegex(ValueError, "tiếng Anh"):
            app.validate_translation_language(en_long, en_long)
        # Bilingual Japanese + Vietnamese rejected
        ja_bi = numbered_srt([(1, "00:00:01,000 --> 00:00:02,000", "これはテスト Đây là thử nghiệm"), (2, "00:00:03,000 --> 00:00:04,000", "もうすぐ出発 Sắp xuất phát")])
        with self.assertRaisesRegex(ValueError, "tiếng Nhật"):
            app.validate_translation_language(ja, ja_bi)
        # Vietnamese proofreading allowed even if identical
        vi = numbered_srt([(1, "00:00:01,000 --> 00:00:02,000", "Xin chào thế giới.")])
        self.assertEqual(app.validate_translation_language(vi, vi, source_lang="vi"), vi)

    def test_load_app_config_keeps_saved_deepseek_chat_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "app_config.json"
            cfg.write_text(json.dumps({
                "translation_provider": "deepseek",
                "translation_api_model": "deepseek-chat",
            }), encoding="utf-8")
            with patch.object(app, "CONFIG_PATH", str(cfg)):
                loaded = app.load_app_config()
                self.assertEqual(loaded["translation_api_model"], "deepseek-chat")


class L02AndSecurityBoundaryTests(unittest.TestCase):
    def test_release_version_prerelease_precedence(self):
        self.assertTrue(app.parse_release_version("2.0.3-alpha") < app.parse_release_version("2.0.3-beta"))
        self.assertTrue(app.parse_release_version("2.0.3-beta.2") < app.parse_release_version("2.0.3-beta.10"))
        self.assertTrue(app.parse_release_version("2.0.3-beta") < app.parse_release_version("2.0.3"))
        self.assertTrue(app.is_newer_release("2.0.3", "2.0.3-beta"))
        self.assertFalse(app.is_newer_release("2.0.3-beta", "2.0.3"))

    def test_make_release_verify_detects_tampered_inner_sha256(self):
        import importlib.util
        import tempfile
        spec = importlib.util.spec_from_file_location("make_release_check", Path(app.RESOURCE_DIR) / "tools" / "make_release.py")
        make_release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(make_release)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            exe = tmp_path / "VietSub Studio.exe"
            exe.write_bytes(b"MZ" + b"x" * 1024)
            zpath, cpath = make_release.build_release(exe, tmp_path / "out", version="1.2.3")
            # Re-pack ZIP with mismatched SHA256.txt
            bad_zip = tmp_path / "bad.zip"
            with zipfile.ZipFile(zpath) as src, zipfile.ZipFile(bad_zip, "w") as dst:
                for info in src.infolist():
                    if info.filename == "VietSub Studio/SHA256.txt":
                        dst.writestr(info.filename, ("0" * 64) + "  VietSub Studio.exe\n")
                    else:
                        dst.writestr(info.filename, src.read(info.filename))
            bad_chk = tmp_path / "bad.zip.sha256.txt"
            bad_chk.write_bytes(f"{make_release.sha256_file(bad_zip)}  bad.zip\n".encode("ascii"))
            with self.assertRaisesRegex(RuntimeError, "SHA256.txt"):
                make_release.verify_release(bad_zip, bad_chk, make_release.sha256_file(exe))

    def test_stream_preview_safe_regular_open_folder_editor_outputs_and_smoke_assets(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edited = root / "out.edited.mp4"
            tts = root / "out.tts.wav"
            extracted = root / "out.extracted.wav"
            for f in (edited, tts, extracted):
                f.write_bytes(b"ok")
            with (
                patch.dict(app.app.config, TESTING=True),
                patch.object(app, "preview_registry", {"p1": {"id": "p1", "source_video": str(edited), "created_at": time.time()}}),
                patch.object(app.storage, "safe_regular", return_value=False),
            ):
                client = app.app.test_client()
                self.assertEqual(client.get("/api/previews/p1/video").status_code, 404)

            with (
                patch.dict(app.app.config, TESTING=True),
                patch.object(app, "workflow_jobs", [{"id": "j1", "result": {"edited_video": str(edited), "tts_audio": str(tts), "extracted_audio": str(extracted)}}]),
                patch.object(app.subprocess, "Popen") as popen,
            ):
                client = app.app.test_client()
                for asset in (edited, tts, extracted):
                    self.assertEqual(client.post("/api/open-folder", json={"path": str(asset)}).status_code, 200)
                self.assertEqual(popen.call_count, 3)

        smoke_src = (Path(app.RESOURCE_DIR) / "tools" / "editor_portable_smoke.py").read_text(encoding="utf-8")
        for expected in ("static/app.js", "static/motion.css", "static/sakuga-vfx.js", "static/fonts/jetbrains-mono-OFL.txt"):
            self.assertIn(expected, smoke_src)

    def test_security_boundaries_with_testing_false_for_editor_uploader_and_multipart(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_headers = {app.API_TOKEN_HEADER: app.API_TOKEN}
            with (
                patch.dict(app.app.config, TESTING=False),
                patch.object(app, "CONFIG_DIR", str(root)),
                patch.object(app, "CONFIG_PATH", str(root / "cfg.json")),
                patch.object(app, "workflow_jobs", []),
            ):
                client = app.app.test_client()
                # Host check
                self.assertEqual(client.get("/api/editor/capabilities", headers={**valid_headers, "Host": "evil.test:5000"}).status_code, 403)
                # Missing token
                self.assertEqual(client.get("/api/editor/capabilities").status_code, 403)
                self.assertEqual(client.get("/api/uploader/profiles").status_code, 403)
                # Cross-origin / bad Sec-Fetch-Site
                self.assertEqual(client.post("/api/editor/runtime", json={"capcut_compat": True}, headers={**valid_headers, "Origin": "https://evil.test"}).status_code, 403)
                self.assertEqual(client.post("/api/uploader/profiles", json={"platform": "youtube", "name": "x"}, headers={**valid_headers, "Sec-Fetch-Site": "cross-site"}).status_code, 403)
                # Non-JSON state-changing API rejected with 415
                self.assertEqual(client.post("/api/editor/runtime", data="x=1", content_type="text/plain", headers=valid_headers).status_code, 415)
                # Multipart routes require X-VietSub-Token & same-origin when TESTING=False (do NOT reject with 415!)
                mp4 = io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"x" * 20)
                self.assertEqual(client.post("/api/previews/local", data={"video": (io.BytesIO(mp4.getvalue()), "a.mp4")}).status_code, 403)
                self.assertEqual(client.post("/api/uploader/media", data={"video": (io.BytesIO(mp4.getvalue()), "a.mp4")}).status_code, 403)
                self.assertEqual(client.post("/api/uploader/media", data={"video": (io.BytesIO(mp4.getvalue()), "a.mp4")}, headers={**valid_headers, "Origin": "https://evil.test"}).status_code, 403)
                ok_media = client.post("/api/uploader/media", data={"video": (io.BytesIO(mp4.getvalue()), "a.mp4")}, headers=valid_headers)
                self.assertEqual(ok_media.status_code, 201)
                # Editor watermark multipart CSRF
                self.assertEqual(client.post("/api/editor/projects/missing/watermark", data={"file": (io.BytesIO(b"x"), "w.png")}).status_code, 403)
                wm = client.post("/api/editor/projects/missing/watermark", data={"file": (io.BytesIO(b"x"), "w.png")}, headers=valid_headers)
                self.assertNotEqual(wm.status_code, 415)


if __name__ == "__main__":
    unittest.main()
