"""Deterministic desktop UI acceptance check with real Flask assets/settings/media.

Run: python tools/workbench_qa.py --output <artifact-directory>
Uses installed Python Playwright/Chromium and FFmpeg. No external accounts or OCR
jobs run: queue/progress and machine diagnostics are controlled browser fixtures.
All configuration, uploaded media and logs live in an isolated temporary directory.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def make_video(target: Path, size: str, ffmpeg: str):
    font = str(ROOT / "static/fonts/noto-sans-sc.ttf").replace("\\", "/").replace(":", "\\:")
    width, height = (int(n) for n in size.split("x"))
    # A deliberately labelled diagnostic clip, not a misleading production screenshot.
    filters = (
        f"drawgrid=width=iw/8:height=ih/8:thickness=1:color=0x6aa08a@0.2,"
        f"drawbox=x=iw*.1:y=ih*.15:w=iw*.8:h=ih*.5:color=0x284d40:t=fill,"
        f"drawbox=x=iw*.1:y=ih*.15:w=iw*.8:h=ih*.5:color=0x7ee0b8:t=2,"
        f"drawtext=fontfile='{font}':text='VietSub Studio':fontcolor=0xc6ddd3:fontsize={width//20}:x=(w-tw)/2:y=h*.34,"
        f"drawtext=fontfile='{font}':text='视频预览与字幕区域':fontcolor=white:fontsize={width//30}:x=(w-tw)/2:y=h*.78,"
        f"drawtext=fontfile='{font}':text='QA SAMPLE  {size}  25 FPS':fontcolor=0x7ee0b8:fontsize={max(14,width//58)}:x=w*.04:y=h*.05"
    )
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=0x10251e:s={size}:r=25:d=6", "-vf", filters,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)],
                   check=True, timeout=90)


OVERFLOW = """() => {
    const root = document.documentElement;
    const failures = [];
    if (root.scrollWidth > innerWidth + 1 || root.scrollHeight > innerHeight + 1)
        failures.push(`document ${root.scrollWidth}×${root.scrollHeight}, viewport ${innerWidth}×${innerHeight}`);
    const ids = ['settings-open-btn','source-link-tab','source-local-tab','add-queue-btn',
        'start-queue-btn','clear-finished-btn','console-toggle-btn','frame-back-btn','frame-forward-btn',
        'play-pause-btn','video-seek','full-frame-btn','lower-third-btn','save-crop-btn'];
    for (const id of ids) {
        const el = document.getElementById(id);
        if (!el.getClientRects().length) continue;
        const r = el.getBoundingClientRect();
        if (r.left < -1 || r.top < -1 || r.right > innerWidth+1 || r.bottom > innerHeight+1)
            failures.push(`${id} outside viewport ${JSON.stringify(r.toJSON())}`);
        if (el.tagName === 'BUTTON' && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1))
            failures.push(`${id} text overflows`);
        const hit = document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
        if (hit && !el.contains(hit)) failures.push(`${id} covered by ${hit.id || hit.className}`);
    }
    return failures;
}"""
CROP = """() => {
    const stage = document.getElementById('video-stage').getBoundingClientRect();
    const player = document.getElementById('preview-player');
    const box = document.getElementById('crop-box').getBoundingClientRect();
    const expected = {left:stage.left+stage.width*crop.crop_x,top:stage.top+stage.height*crop.crop_y,
        width:stage.width*crop.crop_width,height:stage.height*crop.crop_height};
    return {stage:stage.toJSON(), box:box.toJSON(), crop:{...crop}, resolution:currentPreview?.video_resolution,
        ratioError:Math.abs(stage.width/stage.height-player.videoWidth/player.videoHeight),
        error:Math.max(...Object.keys(expected).map(key=>Math.abs(box[key]-expected[key])))};
}"""


def check_layout(page, label, records):
    failures = page.evaluate(OVERFLOW)
    records.append({"check": label, "failures": failures})
    assert not failures, f"{label}: {failures}"


def crop_check(page, label, records):
    data = page.evaluate(CROP)
    assert data["error"] <= 1, f"{label}: crop overlay differs {data}"
    assert data["ratioError"] <= .001, f"{label}: video/stage aspect ratio differs {data}"
    records.append({"check": label, "crop_error_css_px": data["error"], "ratio_error": data["ratioError"]})
    return data


def drag(page, selector, dx, dy):
    r = page.locator(selector).bounding_box()
    assert r, selector
    x, y = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=10)
    page.mouse.up()
    page.wait_for_timeout(100)


def run(output: Path, quick: bool):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    output.mkdir(parents=True, exist_ok=True)
    records, screenshots, issues = [], [], []
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to create deterministic media fixtures")
    with tempfile.TemporaryDirectory(prefix="vietsub-workbench-qa-") as temp:
        temporary = Path(temp)
        # Keep Playwright's installed browser cache while isolating application data.
        if os.name == 'nt' and 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(os.environ['LOCALAPPDATA']) / 'ms-playwright')
        os.environ["VIETSUB_STUDIO_CONFIG_DIR"] = str(temporary / "config")
        os.environ["VIETSUB_STUDIO_DOWNLOAD_DIR"] = str(temporary / "downloads")
        os.environ["APPDATA"] = str(temporary / "appdata")
        os.environ["LOCALAPPDATA"] = str(temporary / "localappdata")
        import app
        # app import is isolated above; do not launch the desktop/browser or queue workers.
        videos = {}
        for size in (["1280x720"] if quick else ["1280x720", "720x1280", "720x720", "960x720"]):
            target = temporary / f"sample-{size}.mp4"
            make_video(target, size, ffmpeg)
            videos[size] = target
        server = make_server("127.0.0.1", 0, app.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for width, height, dpr in ([(1280, 720, 1)] if quick else [(1280, 720, 1), (1440, 900, 1), (1920, 1080, 1), (1280, 720, 1.25), (1920, 1080, 1.5)]):
                    name = f"{width}x{height}" + (f"-dpr{dpr}" if dpr != 1 else "")
                    context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=dpr, reduced_motion="no-preference")
                    page = context.new_page()
                    errors, failed_responses = [], []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                    page.on("response", lambda res: failed_responses.append(f"{res.status} {res.url}") if res.status >= 400 else None)
                    state = {"jobs": [], "revision": 1, "progress": {"status": "idle", "step": "resolve", "logs": [], "next_log_index": 0, "job_id": "", "download": {"active": False}}}
                    def route_api(route):
                        path = route.request.url.split(url, 1)[-1].split("?", 1)[0]
                        data = None
                        if path == "/api/health":
                            data = {"ready_local": True, "ready_douyin": True, "ready_sources": {s: True for s in app.source_registry.DOWNLOADABLE_SOURCE_TYPES}, "checks": {"videocr": {"ok": True, "required": True, "detail": "QA fixture — no OCR process started"}}}
                        elif path == "/api/check-edge": data = {"connected": False, "browser": "Chrome"}
                        elif path == "/api/update": data = {"current_version": app.APP_VERSION, "update_available": False}
                        elif path == "/api/history": data = []
                        elif path == "/api/sources/sessions": data = {"browser_connected": False, "sites": {}}
                        elif path == "/api/progress":
                            data = dict(state["progress"])
                            after = int(parse_qs(urlsplit(route.request.url).query).get('after', ['0'])[0])
                            data['logs'] = data.get('logs', [])[after:]
                        elif path == "/api/queue":
                            if route.request.method == "POST":
                                body = route.request.post_data_json
                                state["queued_payload"] = body
                                state["jobs"].append({"id": f"qa-{len(state['jobs'])}", "status": "queued", "step": "resolve", "source_type": "local", "project_name": body.get("name") or "Video kiểm thử", "ocr_lang": body["lang"]})
                                state["revision"] += 1
                                data = {"ok": True}
                            else: data = {"jobs": state["jobs"], "revision": state["revision"]}
                        elif path == "/api/queue/bulk-delete":
                            body = route.request.post_data_json
                            selected = [job for job in state["jobs"] if job["id"] in body["ids"] and job["status"] != "running"]
                            byte_count = sum(job.get("disk_bytes", 0) for job in selected) if body.get("delete_files") else 0
                            if body.get("dry_run"):
                                data = {"selected_count": len(selected), "reclaimable_bytes": byte_count, "failures": []}
                            else:
                                state["bulk_payload"] = body
                                deleted = [job["id"] for job in selected]
                                state["jobs"] = [job for job in state["jobs"] if job["id"] not in deleted]
                                state["revision"] += 1
                                data = {"deleted_ids": deleted, "freed_bytes": byte_count, "failures": [], "jobs": state["jobs"]}
                        elif path == "/api/queue/start": data = {"ok": True}
                        elif path == "/api/cancel": data = {"ok": True}
                        elif path.startswith("/api/queue/") and route.request.method == "DELETE":
                            state["jobs"] = [j for j in state["jobs"] if j["id"] != path.rsplit("/", 1)[-1]]
                            state["revision"] += 1; data = {"ok": True}
                        if data is None: route.continue_()
                        else: route.fulfill(status=200, content_type="application/json", body=json.dumps(data, ensure_ascii=False))
                    page.route("**/api/**", route_api)
                    try:
                        page.goto(url, wait_until="networkidle")
                        page.evaluate("document.fonts.ready")
                        page.wait_for_function("() => typeof VietSubWorkbench !== 'undefined' && document.querySelector('#source-link-tab').getAttribute('aria-selected') === 'true'")
                        # Reset any layout preference written by the preceding viewport.
                        page.locator("#sidebar-resizer").dblclick()
                        page.locator("#console-resizer").dblclick()
                        page.wait_for_timeout(200)
                        check_layout(page, name + " idle", records)
                        if dpr == 1:
                            image = output / f"{name}-idle.png"; page.screenshot(path=str(image)); screenshots.append(str(image))
                        page.locator("#source-local-tab").click()
                        page.locator("#local-video-input").set_input_files(str(videos["1280x720"]))
                        page.wait_for_function("() => currentPreview && currentPreview.video_resolution && !document.querySelector('#add-queue-btn').disabled", timeout=30000)
                        page.wait_for_function("() => !document.querySelector('#frame-forward-btn').disabled")
                        page.locator("#lower-third-btn").click()
                        page.wait_for_timeout(150)
                        crop_check(page, name + " landscape", records)
                        check_layout(page, name + " ready", records)
                        # Actual frame seek and a pointer crop move, not just setting styles.
                        page.locator("#frame-forward-btn").click()
                        page.wait_for_function("() => Math.abs(document.querySelector('#preview-player').currentTime - .04) < .002")
                        before = page.evaluate("({...crop})")
                        box = page.locator("#crop-box").bounding_box()
                        stage = page.locator("#video-stage").bounding_box()
                        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        page.mouse.down(); page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2 - 12, steps=8); page.mouse.up()
                        after = crop_check(page, name + " moved crop", records)["crop"]
                        assert abs(after["crop_y"] - (before["crop_y"] - 12 / stage["height"])) < .001
                        for corner, dx, dy in [("nw", 6, 5), ("ne", -6, 5), ("sw", 6, -5), ("se", -6, -5)]:
                            previous_crop = page.evaluate("({...crop})")
                            drag(page, f'[data-handle="{corner}"]', dx, dy)
                            assert page.evaluate("({...crop})") != previous_crop, f"{corner} handle must change crop"
                            crop_check(page, name + " corner " + corner, records)
                        drag(page, "#sidebar-resizer", 25, 0)
                        drag(page, "#console-resizer", 0, -45)
                        crop_check(page, name + " resized panes", records)
                        check_layout(page, name + " resized panes", records)
                        # Restore a useful console and capture a representative active workbench.
                        page.locator("#sidebar-resizer").dblclick(); page.locator("#console-resizer").dblclick()
                        page.locator("#lower-third-btn").click()
                        state["jobs"] = [
                            {"id": "running-1", "status": "running", "step": "ocr", "source_type": "douyin", "project_name": "Một ngày ở Vân Nam · 雲南旅行記", "ocr_lang": "ch"},
                            {"id": "waiting-2", "status": "queued", "step": "resolve", "source_type": "local", "project_name": "Phỏng vấn — tập 02", "ocr_lang": "ch"},
                            {"id": "waiting-3", "status": "queued", "step": "resolve", "source_type": "bilibili", "project_name": "Tên video rất dài để kiểm chứng không tràn khung " * 3, "ocr_lang": "ch"},
                        ]
                        state["revision"] += 1
                        state["progress"] = {"status": "running", "step": "ocr", "job_id": "running-1", "logs": ["[SYSTEM] Đã chuẩn bị video 1280 × 720.", "[VideOCR] Đang quét vùng phụ đề đã chọn…", "[SYSTEM] Video tiếp theo được tải trước khi OCR."], "next_log_index": 3, "download": {"active": True, "phase": "download", "source": "douyin", "percent": 62, "downloaded": 65*1048576, "total": 105*1048576, "speed": 8.4*1048576, "connections": 6}}
                        page.evaluate("loadQueue()")
                        page.wait_for_function("() => document.querySelector('#metric-connections').textContent === '6'")
                        page.wait_for_timeout(250)
                        check_layout(page, name + " running", records)
                        if dpr == 1:
                            page.locator("#console-resizer").evaluate("el => el.blur()")
                            page.wait_for_timeout(3500)
                            image = output / f"{name}-workbench.png"; page.screenshot(path=str(image)); screenshots.append(str(image))
                        page.locator("#console-toggle-btn").click()
                        check_layout(page, name + " console collapsed", records)
                        assert page.locator("#metric-connections").is_visible()
                        page.locator("#console-toggle-btn").click()
                        # Config round trip goes to real Flask settings endpoint.
                        page.locator("#settings-open-btn").click()
                        assert page.locator("#settings-close-btn").evaluate("el => el === document.activeElement")
                        page.locator("#setting-sakuga-vfx").uncheck()
                        page.wait_for_function("() => !document.querySelector('#setting-sakuga-vfx').disabled && document.documentElement.dataset.motion === 'off'")
                        page.locator("#setting-sakuga-vfx").check()
                        page.wait_for_function("() => !document.querySelector('#setting-sakuga-vfx').disabled && document.documentElement.dataset.motion === 'on'")
                        if dpr == 1:
                            page.locator(".control-card").evaluate("el => el.scrollTop = 0")
                            page.wait_for_timeout(3500)
                            image = output / f"{name}-settings.png"; page.screenshot(path=str(image)); screenshots.append(str(image))
                        page.keyboard.press("Escape")
                        page.wait_for_function("() => document.querySelector('#settings-drawer').hidden")
                        assert page.locator("#settings-open-btn").evaluate("el => el === document.activeElement")
                        page.emulate_media(reduced_motion="reduce")
                        page.wait_for_function("() => document.documentElement.dataset.motion === 'off'")
                        assert page.evaluate("VietSubVFX.pending") == 0
                        page.emulate_media(reduced_motion="no-preference")
                        page.wait_for_function("() => document.documentElement.dataset.motion === 'on'")
                        # Completion with very long result paths stays inside the dock.
                        result = {"project_name": "Một ngày ở Vân Nam", "project_dir": "C:\\Videos\\Vân Nam", "video": "C:\\Videos\\" + "video dài "*30 + ".mp4", "raw_srt": "C:\\Videos\\vannam.raw.srt", "translated_srt": "C:\\Videos\\vannam.vi.srt"}
                        state["progress"] = {"status": "success", "step": "done", "job_id": "running-1", "logs": [], "next_log_index": 3, "download": {"active": False}, "result_ready": True, "result": result}
                        page.evaluate("pollProgress()")
                        page.wait_for_timeout(750)
                        assert page.locator("#result-box").is_visible()
                        check_layout(page, name + " result", records)
                        # Ready queue captures crop in original normalized source coordinates.
                        state["jobs"] = []; state["revision"] += 1
                        state["progress"] = {"status": "idle", "step": "resolve", "job_id": "", "logs": [], "next_log_index": 0, "download": {"active": False}}
                        expected = page.evaluate("({...crop})")
                        page.locator("#add-queue-btn").click()
                        page.wait_for_function("() => currentPreview === null")
                        assert state["queued_payload"]["crop_coords"] == expected
                        assert state["queued_payload"]["video_resolution"] == {"width": 1280, "height": 720}
                        if dpr == 1 and not quick:
                            for size in ["720x1280", "720x720", "960x720"]:
                                page.locator("#local-video-input").set_input_files(str(videos[size]))
                                page.wait_for_function("() => currentPreview && !document.querySelector('#add-queue-btn').disabled", timeout=30000)
                                page.locator("#lower-third-btn").click(); page.wait_for_timeout(100)
                                crop_check(page, name + " " + size, records)
                                check_layout(page, name + " " + size, records)
                            # Exercise update-banner row while a video is loaded.
                            page.evaluate("document.querySelector('#update-banner').hidden = false")
                            page.wait_for_timeout(100)
                            check_layout(page, name + " update banner", records)
                            page.evaluate("document.querySelector('#update-banner').hidden = true")
                        if width == 1280 and dpr == 1:
                            # Native dialog bridge preserves the existing desktop event handler.
                            payload = page.evaluate("({...currentPreview})") if not quick else None
                            if payload:
                                page.evaluate("data => { window.pywebview = {api:{choose_local_video:async()=>data}}; }", payload)
                                page.locator('#local-video-btn').click()
                                page.wait_for_function("() => !localPreviewBusy && currentPreview && !document.querySelector('#add-queue-btn').disabled")
                                page.wait_for_function("() => document.querySelector('#preview-player').readyState >= 1")
                                page.evaluate("delete window.pywebview")
                                # Metadata is optional: FPS controls disable without blocking crop/queue.
                                page.evaluate("currentPreview.video_metadata = null; VietSubWorkbench.previewChanged(currentPreview)")
                                assert page.locator('#frame-forward-btn').is_disabled()
                                assert page.locator('#add-queue-btn').is_enabled()
                                # A codec error still allows OCR when source dimensions are known.
                                page.locator('#preview-player').dispatch_event('error')
                                assert page.locator('#play-pause-btn').is_disabled()
                                assert page.locator('#add-queue-btn').is_enabled()
                                assert 'vẫn chỉnh crop' in page.locator('#preview-status').input_value()
                                records.append({'check':name+' native bridge / unknown FPS / codec fallback', 'passed':True})
                            for status in ['error', 'cancelled']:
                                state['progress'] = {'status':status,'step':'ocr','job_id':'failure-case','logs':['[!] Kiểm thử trạng thái lỗi'],'next_log_index':1,'download':{'active':False}}
                                page.evaluate('pollProgress()')
                                assert 'error' in page.locator('#step-ocr').get_attribute('class')
                                assert not page.locator('#result-box').is_visible()
                                check_layout(page, name+' '+status, records)
                        # v1.8: actual multiline controls, selection, authoritative
                        # byte preview and dialog keyboard behavior at every viewport.
                        state['progress'] = {'status':'idle','step':'resolve','job_id':'','logs':[],
                                             'next_log_index':0,'download':{'active':False}}
                        page.locator('#source-link-tab').click()
                        page.locator('#url-input').fill('Chia sẻ https://v.douyin.com/FirstQA/\nhttps://youtu.be/SecondQA')
                        page.locator('#batch-queue-btn').click()
                        page.wait_for_function("() => !batchImportBusy && document.querySelector('#batch-import-status').textContent.includes('2')")
                        assert state['queued_payload']['url'] == 'https://youtu.be/SecondQA'
                        state['jobs'] = [
                            {'id':'keep-running','status':'running','step':'ocr','project_name':'Đang xử lý','source_type':'local'},
                            {'id':'old-done','status':'success','step':'done','project_name':'Video đã xong','source_type':'local','finished_at':1,'disk_bytes':2*1048576},
                            {'id':'old-error','status':'error','step':'translate','project_name':'Video cần dọn','source_type':'local','finished_at':1,'disk_bytes':3*1048576},
                        ]
                        state['revision'] += 1
                        page.evaluate('loadQueue()')
                        page.wait_for_function("() => queueJobs.some(job => job.id === 'old-done')")
                        page.locator('#queue-select-finished').click()
                        assert page.locator('[data-select-job="keep-running"]').is_disabled()
                        page.locator('#bulk-delete-btn').click()
                        page.wait_for_function("() => !document.querySelector('#bulk-delete-confirm').disabled")
                        page.locator('#bulk-delete-files').check()
                        page.wait_for_function("() => !document.querySelector('#bulk-delete-confirm').disabled && document.querySelector('#bulk-delete-estimate').textContent.includes('5')")
                        assert page.locator('#bulk-keep-srt').is_checked()
                        for selector in ['#bulk-delete-dialog', '#bulk-delete-confirm', '#bulk-delete-cancel']:
                            rectangle = page.locator(selector).bounding_box()
                            assert rectangle and rectangle['y'] >= 0 and rectangle['y']+rectangle['height'] <= height+1
                        if dpr == 1:
                            image = output / f'{name}-bulk-delete.png'
                            page.screenshot(path=str(image)); screenshots.append(str(image))
                        page.keyboard.press('Escape')
                        assert not page.locator('#bulk-delete-dialog').is_visible()
                        assert page.locator('#bulk-delete-btn').evaluate('el => el === document.activeElement')
                        page.locator('#bulk-delete-btn').click()
                        assert page.locator('#bulk-delete-records').is_checked()
                        page.locator('#bulk-delete-files').check()
                        page.wait_for_function("() => !document.querySelector('#bulk-delete-confirm').disabled")
                        page.locator('#bulk-delete-confirm').click()
                        page.wait_for_function("() => !bulkDeleteBusy && queueJobs.length === 1")
                        assert state['bulk_payload']['keep_srt'] is True
                        assert state['bulk_payload']['delete_files'] is True
                        records.append({'check':name+' batch import / bulk delete / focus return', 'passed':True})
                        # Saved layout and preference survive a page reload.
                        page.locator("#sidebar-resizer").focus(); page.keyboard.press("End")
                        saved = page.evaluate("JSON.parse(localStorage.getItem('vietsub.studio.workbench.v1'))")
                        page.wait_for_function("""async expected => {
                            const data = await fetchJson('/api/settings');
                            return data.workbench_layout && Object.entries(expected).every(
                                ([key, value]) => data.workbench_layout[key] === value);
                        }""", arg=saved, polling=100, timeout=15000)
                        page.reload(wait_until="networkidle")
                        restored = page.evaluate("JSON.parse(localStorage.getItem('vietsub.studio.workbench.v1'))")
                        assert restored == saved
                        check_layout(page, name + " reload", records)
                        assert not errors, f"{name} browser errors: {errors}"
                        assert not failed_responses, f"{name} HTTP failures: {failed_responses}"
                        records.append({"check": name + " console/network", "console_errors": 0, "http_errors": 0})
                    except Exception:
                        page.screenshot(path=str(output / (name + '-failure.png')))
                        (output / (name + '-errors.json')).write_text(json.dumps({'errors': errors, 'responses': failed_responses}), encoding='utf-8')
                        raise
                    context.close()
                browser.close()
        except Exception as error:
            issues.append(traceback.format_exc())
            if 'page' in locals() and not page.is_closed():
                with contextlib.suppress(Exception):
                    page.screenshot(path=str(output / "failure.png"))
            raise
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            (output / "report.json").write_text(json.dumps({"checks": records, "screenshots": screenshots, "issues": issues}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": len(records), "screenshots": len(screenshots), "issues": issues, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="One viewport/clip for fast layout diagnosis")
    args = parser.parse_args()
    run(args.output.resolve(), args.quick)
