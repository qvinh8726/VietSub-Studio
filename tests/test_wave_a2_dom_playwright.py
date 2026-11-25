"""Real Playwright Chromium DOM regression checks for Audit Round 2 Wave A2 (H-13, H-14, H-15, M-15, L-01)."""

import json
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.serving import make_server

_REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
if os.name == "nt" and _REAL_LOCALAPPDATA and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(_REAL_LOCALAPPDATA) / "ms-playwright")

try:
    from . import _isolation  # noqa: F401
except ImportError:
    import _isolation  # noqa: F401

import app


class WaveA2RealDomPlaywrightTests(unittest.TestCase):
    def test_wave_a2_real_dom_and_async_race_fixes_in_chromium(self):
        from playwright.sync_api import Error as PlaywrightError, sync_playwright

        server = make_server("127.0.0.1", 0, app.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with (
                patch.dict(app.app.config, TESTING=False),
                sync_playwright() as pw,
            ):
                try:
                    browser = pw.chromium.launch(headless=True)
                except PlaywrightError as exc:
                    if "Executable doesn't exist" in str(exc) and os.environ.get("VIETSUB_REQUIRE_BROWSER_TESTS") != "1":
                        self.skipTest("Playwright Chromium browser binary is not installed in this environment.")
                    raise

                context = browser.new_context(viewport={"width": 1280, "height": 720}, reduced_motion="reduce")
                page = context.new_page()
                page_errors = []
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                yt_id = "11111111-1111-4111-8111-111111111111"
                tt_id = "22222222-2222-4222-8222-222222222222"

                def make_snapshot(job_id):
                    return {
                        "job_id": job_id,
                        "workspace": "editor",
                        "ready": True,
                        "availability": {"video": True, "translated_srt": True},
                        "video_url": f"/api/projects/{job_id}/video?signature=sig&asset=source",
                        "manifest": {
                            "schema_version": 1,
                            "project_id": f"proj-{job_id}",
                            "project_name": f"Project {job_id}",
                            "crop_box": {"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.2},
                            "source_resolution": {"width": 1920, "height": 1080},
                            "assets": {},
                            "metadata": {},
                            "stages": {},
                        },
                    }

                cues_a = [
                    {
                        "index": i + 1,
                        "start_ms": i * 2000,
                        "end_ms": i * 2000 + 1800,
                        "text": f"Dòng phụ đề số {i + 1} trên trình duyệt thật",
                        "raw_text": f"台词 {i + 1}",
                    }
                    for i in range(20)
                ]
                saved_srt_payloads = []
                uploader_queue_polls = {"count": 0}

                presets_store = {
                    "active_id": "p1",
                    "presets": [
                        {
                            "id": "p1",
                            "name": "Kênh Tiên Hiệp 9:16",
                            "description": "",
                            "glossary_id": "g-tien-hiep",
                            "editor_options": {
                                "reframe": "vertical",
                                "mask_mode": "boxblur",
                                "crop_box": None,
                                "subtitle_animation": "word_pop",
                                "subtitle_highlight_color": "#ffd54a",
                                "subtitle_max_words_per_chunk": 4,
                                "watermark_enabled": False,
                                "watermark_path": "",
                            },
                            "publish_defaults": {
                                "profile_ids": [yt_id],
                                "upload_mode": "assisted",
                                "visibility": "private",
                                "confirm_public_auto": False,
                            },
                        }
                    ],
                    "resolved_warnings": {},
                }
                glossary_store = {
                    "active_id": "default",
                    "profiles": [
                        {"id": "default", "name": "Mặc định", "style_note": "", "entries": []},
                        {
                            "id": "g-tien-hiep",
                            "name": "Tiên Hiệp",
                            "style_note": "ta-ngươi",
                            "entries": [{"source": "剑", "target": "Kiếm", "note": ""}],
                        },
                    ],
                }
                uploader_caps = {
                    "browser_available": True,
                    "timezone": "Asia/Saigon",
                    "scheduling": "local",
                    "default_upload_mode": "assisted",
                    "youtube_api_supported": True,
                    "platforms": [
                        {"key": "tiktok", "label": "TikTok", "modes": ["public", "draft"]},
                        {"key": "youtube", "label": "YouTube", "modes": ["public", "private", "draft"]},
                        {"key": "facebook", "label": "Facebook", "modes": ["public", "private", "draft"]},
                        {"key": "douyin", "label": "Douyin", "modes": ["public", "private", "draft"]},
                        {"key": "bilibili", "label": "Bilibili", "modes": ["public", "draft"]},
                        {"key": "xiaohongshu", "label": "Xiaohongshu", "modes": ["public", "private", "draft"]},
                    ],
                }
                uploader_profiles = [
                    {
                        "id": yt_id,
                        "platform": "youtube",
                        "name": "Kênh YouTube API",
                        "upload_method": "youtube_api",
                        "session_status": "ready",
                        "detail": "",
                        "youtube_api": {"configured": True, "connected": False, "channel_title": ""},
                    },
                    {
                        "id": tt_id,
                        "platform": "tiktok",
                        "name": "Kênh TikTok Phụ",
                        "upload_method": "browser",
                        "session_status": "ready",
                        "detail": "",
                    },
                ]

                def route_api(route):
                    path = route.request.url.split(url, 1)[-1].split("?", 1)[0]
                    method = route.request.method

                    if path == "/api/uploader/capabilities":
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(uploader_caps))
                        return
                    if path == "/api/uploader/profiles":
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({"profiles": uploader_profiles}),
                        )
                        return
                    if path == "/api/uploader/queue" and method == "GET":
                        uploader_queue_polls["count"] += 1
                        cnt = uploader_queue_polls["count"]
                        # Keep TikTok job running for first 3 reads, then succeed on 4th+ read to trigger card #2 update.
                        status = "running" if cnt < 4 else "success"
                        percent = min(100, cnt * 25)
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "jobs": [
                                        {
                                            "id": "up-poll-1",
                                            "profile_id": tt_id,
                                            "platform": "tiktok",
                                            "status": status,
                                            "phase": "upload",
                                            "step": "upload_file",
                                            "upload_mode": "assisted",
                                            "percent": percent,
                                            "error": "",
                                            "screenshot_url": "",
                                            "result_url": "",
                                            "visibility": "draft",
                                            "scheduled_at": None,
                                        }
                                    ]
                                }
                            ),
                        )
                        return
                    if path == "/api/glossaries" and method == "GET":
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(glossary_store))
                        return
                    if path == "/api/channel-presets" and method == "GET":
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(presets_store))
                        return
                    if path == "/api/channel-presets/p1/apply" and method == "POST":
                        body = json.loads(route.request.post_data or "{}")
                        req_job = body.get("job_id") or ""
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "preset_id": "p1",
                                    "preset_name": "Kênh Tiên Hiệp 9:16",
                                    "glossary_id": "g-tien-hiep",
                                    "editor_options": {
                                        "reframe": "vertical",
                                        "mask_mode": "boxblur",
                                        "crop_box": {"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.2},
                                        "subtitle_animation": "word_pop",
                                        "subtitle_highlight_color": "#ffd54a",
                                        "subtitle_max_words_per_chunk": 4,
                                        "watermark_enabled": bool(req_job),
                                        "watermark_path": (
                                            f"C:/projects/{req_job}/.vietsub-assets/watermark-preset-abc.png"
                                            if req_job
                                            else ""
                                        ),
                                    },
                                    "publish_defaults": presets_store["presets"][0]["publish_defaults"],
                                    "glossary_store": glossary_store,
                                    "presets_store": presets_store,
                                    "warnings": [],
                                }
                            ),
                        )
                        return
                    if path in ("/api/editor/projects/job-a", "/api/editor/projects/job-b") and method == "GET":
                        jid = path.rsplit("/", 1)[-1]
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "snapshot": make_snapshot(jid),
                                    "options": {
                                        "mask_mode": "boxblur",
                                        "crop_box": {"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.2},
                                        "subtitle_position": "crop",
                                        "tts_change_pitch": False,
                                        "change_audio_pitch": False,
                                    },
                                    "active_task": None,
                                    "last_render": None,
                                }
                            ),
                        )
                        return
                    if path == "/api/editor/projects/job-a/srt" and method == "GET":
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "job_id": "job-a",
                                    "cues": cues_a,
                                    "has_raw": True,
                                    "has_backup": False,
                                    "locked": False,
                                }
                            ),
                        )
                        return
                    if path == "/api/editor/projects/job-b/srt" and method == "GET":
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "job_id": "job-b",
                                    "cues": [
                                        {"index": 1, "start_ms": 0, "end_ms": 1500, "text": "Phụ đề B", "raw_text": ""}
                                    ],
                                    "has_raw": False,
                                    "has_backup": False,
                                    "locked": False,
                                }
                            ),
                        )
                        return
                    if path == "/api/editor/projects/job-a/srt" and method == "PUT":
                        payload = json.loads(route.request.post_data or "{}")
                        saved_srt_payloads.append(payload)
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "job_id": "job-a",
                                    "cues": payload.get("cues", []),
                                    "has_raw": True,
                                    "has_backup": True,
                                    "locked": False,
                                }
                            ),
                        )
                        return

                    fixtures = {
                        "/api/health": {"ready_local": True, "ready_douyin": True, "checks": {}},
                        "/api/update": {"current_version": app.APP_VERSION, "update_available": False},
                        "/api/check-edge": {"connected": False, "browser": "Chrome"},
                        "/api/history": [],
                        "/api/sources/sessions": {"browser_connected": False, "sites": {}},
                        "/api/queue": {"jobs": [], "revision": 1},
                        "/api/progress": {
                            "status": "idle",
                            "step": "resolve",
                            "logs": [],
                            "next_log_index": 0,
                            "download": {"active": False},
                        },
                        "/api/editor/capabilities": {
                            "ffmpeg": True,
                            "ffprobe": True,
                            "encoders": {"libx264": True},
                            "optional": {},
                            "presets": ["standard", "anime", "social"],
                            "fonts": ["Be Vietnam Pro"],
                        },
                    }
                    if path in fixtures:
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(fixtures[path]))
                    else:
                        route.continue_()

                page.add_init_script(
                    """(() => {
                        const origFetch = window.fetch.bind(window);
                        window.__holds = {};
                        window.fetch = async function(input, init) {
                            const reqUrl = typeof input === 'string' ? input : (input?.url || '');
                            const reqMethod = (init?.method || 'GET').toUpperCase();
                            const res = await origFetch(input, init);
                            if (window.__holds.glossaries && reqUrl.includes('/api/glossaries') && reqMethod === 'GET') {
                                await window.__holds.glossaries.promise;
                            }
                            if (window.__holds.apply && reqUrl.includes('/apply') && reqMethod === 'POST') {
                                await window.__holds.apply.promise;
                            }
                            return res;
                        };
                    })();"""
                )
                page.route("**/api/**", route_api)
                page.goto(url, wait_until="networkidle")
                page.wait_for_function(
                    "() => typeof VietSubWorkbench !== 'undefined' && typeof VietSubEditor !== 'undefined' && typeof VietSubUploader !== 'undefined'"
                )

                # =========================================================================
                # 1. [H-15 & L-01] Publish Hub paintProfiles(): keep focus, typed text in
                #    <textarea class="uploader-yt-json-input">, and user-collapsed <details>
                #    across multiple live poll cycles (including when another profile updates).
                # =========================================================================
                page.locator("#workspace-uploader-tab").click()
                page.wait_for_selector(".uploader-yt-json-input")
                yt_details = page.locator("details.uploader-yt-api-panel").first
                yt_textarea = page.locator("textarea.uploader-yt-json-input").first
                self.assertEqual(yt_details.evaluate("el => el.open"), True)

                # Verify L-01 CSS fix: textarea background is not transparent.
                ta_bg = yt_textarea.evaluate("el => getComputedStyle(el).backgroundColor")
                self.assertNotEqual(ta_bg, "rgba(0, 0, 0, 0)")

                # Tag the exact DOM node, focus it, and type real text via keyboard.
                yt_textarea.evaluate("el => { el.__waveA2Tag = 'preserved-textarea'; }")
                yt_textarea.click()
                typed_json = '{"installed":{"client_id":"demo.apps.googleusercontent.com","client_secret":"sec123456"}}'
                page.keyboard.type(typed_json)

                polls_before = uploader_queue_polls["count"]
                # Wait for at least 2 real poll ticks (each 1800ms) while focused in textarea.
                deadline_polls = polls_before + 2
                page.wait_for_timeout(4100)
                self.assertGreaterEqual(uploader_queue_polls["count"], deadline_polls)

                focus_check = page.evaluate(
                    """() => {
                        const ta = document.querySelector('textarea.uploader-yt-json-input');
                        return {
                            sameNode: ta?.__waveA2Tag === 'preserved-textarea',
                            isFocused: document.activeElement === ta,
                            value: ta?.value || '',
                        };
                    }"""
                )
                self.assertTrue(focus_check["sameNode"], "Textarea DOM node was destroyed during polling!")
                self.assertTrue(focus_check["isFocused"], "Focus on YouTube OAuth textarea was lost during polling!")
                self.assertEqual(focus_check["value"], typed_json)

                # Now explicitly collapse <details>, let another poll update TikTok job -> status='success',
                # and verify <details> stays collapsed and YouTube card node is NOT detached!
                yt_details.locator("summary").click()
                self.assertEqual(yt_details.evaluate("el => el.open"), False)
                page.wait_for_timeout(2300)
                post_collapse = page.evaluate(
                    """() => {
                        const details = document.querySelector('details.uploader-yt-api-panel');
                        const ta = document.querySelector('textarea.uploader-yt-json-input');
                        return {
                            open: details?.open,
                            sameNode: ta?.__waveA2Tag === 'preserved-textarea',
                            value: ta?.value || '',
                        };
                    }"""
                )
                self.assertFalse(post_collapse["open"], "User-collapsed YouTube OAuth <details> was forced open again!")
                self.assertTrue(post_collapse["sameNode"], "YouTube card was rebuilt when only TikTok card changed!")
                self.assertEqual(post_collapse["value"], typed_json)

                # =========================================================================
                # 2. [H-15 & L-01] #editor-live-subtitle paddingBottom in px proportional
                #    to content.height (NOT %), and CSS --body / non-transparent row bg.
                # =========================================================================
                page.locator("#workspace-editor-tab").click()
                page.evaluate("(snap) => VietSubWorkbench.projectReceived('editor', snap)", make_snapshot("job-a"))
                page.wait_for_function("() => !VietSubEditor.state().loading && VietSubEditor.state().srtCount === 20")

                # Trigger live subtitle at t = 0.5s (cue #1).
                live_metrics = page.evaluate(
                    """() => {
                        const video = document.getElementById('editor-video');
                        video.hidden = false;
                        Object.defineProperty(video, 'currentTime', {configurable: true, writable: true, value: 0.5});
                        video.dispatchEvent(new Event('timeupdate'));
                        const vr = video.getBoundingClientRect();
                        const scale = Math.min(vr.width / 1920, vr.height / 1080);
                        const contentH = 1080 * scale;
                        const overlay = document.getElementById('editor-live-subtitle');
                        const row = document.querySelector('.editor-srt-row');
                        const srtTa = document.querySelector('.editor-srt-text');
                        return {
                            hidden: overlay.hidden,
                            paddingBottom: overlay.style.paddingBottom,
                            contentH,
                            expectedCropPx: Math.round(contentH * 0.10),
                            rowBg: getComputedStyle(row).backgroundColor,
                            srtFont: getComputedStyle(srtTa).fontFamily,
                        };
                    }"""
                )
                self.assertFalse(live_metrics["hidden"])
                self.assertTrue(live_metrics["paddingBottom"].endswith("px"), live_metrics)
                self.assertLessEqual(
                    abs(float(live_metrics["paddingBottom"][:-2]) - live_metrics["expectedCropPx"]),
                    1.0,
                    live_metrics,
                )
                self.assertNotEqual(live_metrics["rowBg"], "rgba(0, 0, 0, 0)")
                self.assertIn("Be Vietnam Pro", live_metrics["srtFont"])

                # Switch subtitle position to 'bottom' -> 4% of content.height in px.
                page.locator("#editor-inspector-subtitle-tab").click()
                page.locator("#editor-subtitle-position").select_option("bottom")
                bottom_pad = page.evaluate("() => document.getElementById('editor-live-subtitle').style.paddingBottom")
                self.assertTrue(bottom_pad.endswith("px"), bottom_pad)
                self.assertLessEqual(
                    abs(float(bottom_pad[:-2]) - round(live_metrics["contentH"] * 0.04)),
                    1.0,
                    (bottom_pad, live_metrics["contentH"]),
                )

                # =========================================================================
                # 3. [H-15] Active cue change in #editor-srt-list uses list.scrollTop and
                #    NEVER scrolls <video id="editor-video"> out of the viewport.
                # =========================================================================
                scroll_check = page.evaluate(
                    """() => {
                        const col = document.querySelector('.editor-preview-column');
                        const list = document.getElementById('editor-srt-list');
                        const video = document.getElementById('editor-video');
                        col.scrollTop = 0;
                        list.scrollTop = 0;
                        const videoTopBefore = video.getBoundingClientRect().top;
                        Object.defineProperty(video, 'currentTime', {configurable: true, writable: true, value: 35.0});
                        video.dispatchEvent(new Event('timeupdate'));
                        return {
                            listScrollHeight: list.scrollHeight,
                            listClientHeight: list.clientHeight,
                            listScrollTopAfter: list.scrollTop,
                            colScrollTopAfter: col.scrollTop,
                            videoTopBefore,
                            videoTopAfter: video.getBoundingClientRect().top,
                            activeIdx: VietSubEditor.state().activeCueIndex,
                        };
                    }"""
                )
                self.assertEqual(scroll_check["activeIdx"], 17)
                self.assertGreater(scroll_check["listScrollHeight"], scroll_check["listClientHeight"])
                self.assertGreater(scroll_check["listScrollTopAfter"], 0, "SRT list did not scroll to active cue!")
                self.assertEqual(scroll_check["colScrollTopAfter"], 0, "Outer preview column scrolled unexpectedly!")
                self.assertLessEqual(abs(scroll_check["videoTopAfter"] - scroll_check["videoTopBefore"]), 1.0)
                self.assertGreaterEqual(scroll_check["videoTopAfter"], 0)

                # =========================================================================
                # 4. [H-13, H-14, M-15, L-01] SRT Add/Edit/Save (no stuck srtInvalidCount,
                #    clears search filter, focuses real DOM textarea via querySelector),
                #    Settings glossary race + display:grid, and Channel Preset apply with
                #    no cross-project watermark contamination & preserved dragged crop.
                # =========================================================================
                # Trigger invalid timestamp on cue #1 -> srtInvalidCount === 1, Save disabled.
                first_start = page.locator('#editor-srt-list input[data-srt-field="start"]').first
                first_start.fill("bad-timestamp")
                first_start.dispatch_event("change")
                self.assertEqual(page.evaluate("() => VietSubEditor.state().srtInvalidCount"), 1)
                self.assertTrue(page.locator("#editor-srt-save-btn").is_disabled())

                # Apply search filter, then click "+ Thêm dòng" -> resets srtInvalidCount, clears filter, focuses new textarea!
                page.locator("#editor-srt-search").fill("không có kết quả nào")
                page.locator("#editor-srt-add-btn").click()
                after_add = page.evaluate(
                    """() => ({
                        search: document.getElementById('editor-srt-search').value,
                        invalidCount: VietSubEditor.state().srtInvalidCount,
                        srtCount: VietSubEditor.state().srtCount,
                        srtDirty: VietSubEditor.state().srtDirty,
                        saveDisabled: document.getElementById('editor-srt-save-btn').disabled,
                        activeTag: document.activeElement?.tagName,
                        activeField: document.activeElement?.dataset?.srtField || '',
                    })"""
                )
                self.assertEqual(after_add["search"], "")
                self.assertEqual(after_add["invalidCount"], 0)
                self.assertEqual(after_add["srtCount"], 21)
                self.assertTrue(after_add["srtDirty"])
                self.assertFalse(after_add["saveDisabled"])
                self.assertEqual(after_add["activeTag"], "TEXTAREA")
                self.assertEqual(after_add["activeField"], "text")

                # Edit the newly focused textarea and click Save SRT -> PUT succeeds cleanly!
                page.keyboard.type("Đã sửa trên Chromium thật")
                page.locator("#editor-srt-save-btn").click()
                page.wait_for_function("() => !VietSubEditor.state().srtDirty")
                self.assertEqual(len(saved_srt_payloads), 1)
                self.assertEqual(len(saved_srt_payloads[0]["cues"]), 21)

                # Drag/set custom unsaved crop on job-a before opening Settings & applying preset.
                page.locator("#editor-inspector-frame-tab").click()
                page.locator("#editor-crop-x").fill("20")
                page.locator("#editor-crop-x").dispatch_event("change")
                page.locator("#editor-crop-y").fill("50")
                page.locator("#editor-crop-y").dispatch_event("change")
                page.locator("#editor-crop-width").fill("60")
                page.locator("#editor-crop-width").dispatch_event("change")
                page.locator("#editor-crop-height").fill("25")
                page.locator("#editor-crop-height").dispatch_event("change")

                # Open Settings with delayed /api/glossaries so channel-presets resolves first (H-14 race!).
                page.evaluate("() => { let r; window.__holds.glossaries = {promise: new Promise(x => { r = x; }), release: r}; }")
                page.locator("#settings-open-btn").click()
                page.wait_for_function("() => document.getElementById('setting-preset-select').value === 'p1'")
                # Release /api/glossaries AFTER presets already rendered -> must restore 'g-tien-hiep', not 'default'!
                page.evaluate("() => { window.__holds.glossaries.release(); window.__holds.glossaries = null; }")
                page.wait_for_function("() => document.getElementById('setting-preset-glossary').value === 'g-tien-hiep'")

                # Verify L-01 display: grid on .glossary-settings and .channel-presets-settings,
                # and Phase 4 tabbed Settings Drawer navigation + console toggle SVG preservation.
                page.locator("#settings-translation-tab").click()
                self.assertEqual(page.locator("#settings-translation-tab").get_attribute("aria-selected"), "true")
                self.assertFalse(page.locator("#settings-translation-panel").is_hidden())
                page.locator("#settings-presets-tab").click()
                self.assertEqual(page.locator("#settings-presets-tab").get_attribute("aria-selected"), "true")
                self.assertFalse(page.locator("#settings-presets-panel").is_hidden())
                grid_displays = page.evaluate(
                    """() => ({
                        glossary: getComputedStyle(document.querySelector('.glossary-settings')).display,
                        presets: getComputedStyle(document.querySelector('.channel-presets-settings')).display,
                        consoleHasSvg: Boolean(document.querySelector('#console-toggle-btn svg.icon')),
                        zCrop: Number(getComputedStyle(document.getElementById('editor-crop-layer')).zIndex),
                        zBanner: Number(getComputedStyle(document.getElementById('editor-live-banner')).zIndex),
                        zSub: Number(getComputedStyle(document.getElementById('editor-live-subtitle')).zIndex),
                    })"""
                )
                self.assertEqual(grid_displays["glossary"], "grid")
                self.assertEqual(grid_displays["presets"], "grid")
                self.assertTrue(grid_displays["consoleHasSvg"])
                self.assertGreater(grid_displays["zCrop"], grid_displays["zBanner"])
                self.assertGreater(grid_displays["zBanner"], grid_displays["zSub"])

                # Apply preset on job-a -> reframe becomes 'vertical' AND user's dragged crop (0.2, 0.5) is PRESERVED!
                page.locator("#setting-preset-apply-btn").click()
                page.wait_for_function("() => VietSubEditor.state().options.reframe === 'vertical'")
                crop_after_apply = page.evaluate("() => VietSubEditor.state().options.crop_box")
                self.assertAlmostEqual(crop_after_apply["x"], 0.2, places=3)
                self.assertAlmostEqual(crop_after_apply["y"], 0.5, places=3)

                # Trigger 2nd apply (for job-a, held in browser fetch), switch Editor to job-b BEFORE it returns,
                # then release the response -> job-b must NOT receive job-a's watermark_path!
                page.evaluate("() => { let r; window.__holds.apply = {promise: new Promise(x => { r = x; }), release: r}; }")
                page.locator("#setting-preset-apply-btn").click()
                page.wait_for_timeout(120)
                page.locator("#settings-close-btn").click()
                page.evaluate("(snap) => VietSubWorkbench.projectReceived('editor', snap)", make_snapshot("job-b"))
                page.wait_for_function("() => VietSubEditor.state().job_id === 'job-b' && !VietSubEditor.state().loading")
                page.evaluate("() => { window.__holds.apply.release(); window.__holds.apply = null; }")
                page.wait_for_timeout(250)
                job_b_opts = page.evaluate("() => VietSubEditor.state().options")
                self.assertEqual(job_b_opts["watermark_path"], "")
                self.assertFalse(job_b_opts["watermark_enabled"])

                self.assertEqual(page_errors, [], f"Uncaught page errors: {page_errors}")
                context.close()
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == "__main__":
    unittest.main()
