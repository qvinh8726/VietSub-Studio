"""Real Playwright browser DOM verification for v2.0.3 M-09 & L-02 frontend fixes."""

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


class RealDomPlaywrightTests(unittest.TestCase):
    def test_m09_and_l02_dom_fixes_in_real_chromium(self):
        from playwright.sync_api import sync_playwright

        server = make_server("127.0.0.1", 0, app.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with (
                patch.dict(app.app.config, TESTING=False),
                sync_playwright() as pw,
            ):
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 720}, reduced_motion="reduce")
                page = context.new_page()
                page_errors = []
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                dom_snapshot = {
                    "job_id": "dom-test", "workspace": "editor", "ready": True,
                    "availability": {"video": True, "translated_srt": True},
                    "video_url": "/api/projects/dom-test/video?signature=x&asset=source",
                    "manifest": {
                        "schema_version": 1, "project_id": "dom-test", "project_name": "DOM",
                        "crop_box": {"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.2},
                        "source_resolution": {"width": 1280, "height": 720},
                        "assets": {}, "metadata": {}, "stages": {},
                    },
                }

                def route_api(route):
                    path = route.request.url.split(url, 1)[-1].split("?", 1)[0]
                    fixtures = {
                        "/api/health": {"ready_local": True, "ready_douyin": True, "checks": {}},
                        "/api/update": {"current_version": app.APP_VERSION, "update_available": False},
                        "/api/check-edge": {"connected": False, "browser": "Chrome"},
                        "/api/history": [],
                        "/api/sources/sessions": {"browser_connected": False, "sites": {}},
                        "/api/queue": {"jobs": [], "revision": 1},
                        "/api/progress": {"status": "idle", "step": "resolve", "logs": [], "next_log_index": 0, "download": {"active": False}},
                        "/api/editor/capabilities": {"ffmpeg": True, "ffprobe": True, "encoders": {"libx264": True}, "optional": {}, "presets": ["standard", "anime", "social"], "fonts": ["Be Vietnam Pro"]},
                        "/api/editor/projects/dom-test": {
                            "snapshot": dom_snapshot,
                            "options": {
                                "mask_mode": "boxblur",
                                "crop_box": {"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.2},
                                "tts_change_pitch": False,
                                "change_audio_pitch": False,
                            },
                            "active_task": None,
                            "last_render": None,
                        },
                    }
                    if path == "/api/queue" and route.request.method == "POST":
                        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
                    elif path in fixtures:
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(fixtures[path]))
                    else:
                        route.continue_()

                page.route("**/api/**", route_api)
                page.goto(url, wait_until="networkidle")
                page.wait_for_function("() => typeof VietSubWorkbench !== 'undefined' && typeof VietSubEditor !== 'undefined'")

                # 1. [L-02] #local-video-input runtime accept supports .mkv without mutating static HTML contract
                accept_attr = page.locator("#local-video-input").get_attribute("accept")
                self.assertIn(".mkv", accept_attr)
                self.assertIn("video/x-matroska", accept_attr)

                # 2. [M-09] #queue-list empty state preserves SVG icon
                self.assertGreater(page.locator("#queue-list .queue-empty-icon svg").count(), 0)

                # 3. [M-09] #play-pause-btn keeps <svg class="icon"> and fits without overflow
                self.assertGreater(page.locator("#play-pause-btn svg.icon").count(), 0)
                page.evaluate("""() => {
                    const p = document.getElementById('preview-player');
                    p.dataset.previewId = 'p1';
                    Object.defineProperty(p, 'readyState', {configurable: true, get: () => 2});
                    Object.defineProperty(p, 'duration', {configurable: true, get: () => 10});
                    Object.defineProperty(p, 'paused', {configurable: true, get: () => false});
                    VietSubWorkbench.previewChanged({id: 'p1', video_resolution: {width: 1280, height: 720}, video_metadata: {frame_rate: 25, duration: 10}});
                }""")
                self.assertGreater(page.locator("#play-pause-btn svg.icon").count(), 0)
                self.assertIn("Tạm dừng", page.locator("#play-pause-btn").inner_text())
                overflow = page.evaluate("() => { const b = document.getElementById('play-pause-btn'); return b.scrollWidth > b.clientWidth + 1; }")
                self.assertFalse(overflow)

                # 4. [M-09] invalidatePreview() clears inline width/height & importShareLinks() invalidates stale preview
                page.evaluate("""() => {
                    const s = document.getElementById('video-stage');
                    s.style.width = '400px';
                    s.style.height = '225px';
                }""")
                page.locator("#url-input").fill("https://youtu.be/dQw4w9WgXcQ")
                page.locator("#batch-queue-btn").click()
                page.wait_for_function("() => document.getElementById('url-input').value === ''")
                dims = page.evaluate("() => ({w: document.getElementById('video-stage').style.width, h: document.getElementById('video-stage').style.height})")
                self.assertEqual(dims, {"w": "", "h": ""})

                # 5. [M-09] editor.js: clean <select> label in error text (no <option> text leaked) & no false legacy warning
                page.locator("#workspace-editor-tab").click()
                pitch_help = page.locator("#editor-pitch-help").inner_text()
                self.assertNotIn("Cấu hình cũ", pitch_help)

                page.evaluate("(snap) => VietSubEditor.projectReceived(snap)", dom_snapshot)
                page.wait_for_function("() => !VietSubEditor.state().loading && !document.getElementById('editor-options-fieldset').disabled")
                label_text = page.evaluate("""() => {
                    const sel = document.getElementById('editor-mask-mode');
                    const opt = document.createElement('option');
                    opt.value = 'invalid_option';
                    opt.textContent = 'Invalid';
                    sel.appendChild(opt);
                    sel.value = 'invalid_option';
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    return document.getElementById('editor-form-error').textContent;
                }""")
                self.assertIn("Cách che", label_text)
                self.assertNotIn("Box blur", label_text)
                self.assertNotIn("Thanh màu", label_text)

                # 6. [L-02] hideResult resets #res-publish & #res-publish-row when transitioning back to running
                page.locator("#workspace-vietsub-tab").click()
                page.evaluate("""() => {
                    const row = document.getElementById('res-publish-row');
                    const text = document.getElementById('res-publish');
                    row.hidden = false;
                    text.textContent = 'stale-pack.json';
                    document.getElementById('result-box').hidden = false;
                }""")
                page.unroute("**/api/**")
                page.route("**/api/**", lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"status": "running", "step": "ocr", "logs": [], "next_log_index": 0, "download": {"active": False}}),
                ) if "/api/progress" in route.request.url else route.continue_())
                page.evaluate("pollProgress()")
                page.wait_for_function("() => document.getElementById('res-publish-row').hidden === true && document.getElementById('res-publish').textContent === ''")

                self.assertEqual(page_errors, [])
                context.close()
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == "__main__":
    unittest.main()
