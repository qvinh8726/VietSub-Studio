"""Phase 1 acceptance: three workspaces, real manifest APIs and isolated media.

Run: python tools/phase1_qa.py --output <directory>
No external accounts, OCR, AI calls or uploads. Only diagnostics/update checks are
browser fixtures; queue, signed project video, handoff, settings and cleanup hit Flask.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from workbench_qa import make_video, OVERFLOW, crop_check

RAW = "1\n00:00:00,000 --> 00:00:01,000\n你好世界\n"
VI = "1\n00:00:00,000 --> 00:00:01,000\nXin chào thế giới\n"
CROP = {"crop_x": .04, "crop_y": .64, "crop_width": .92, "crop_height": .30}

WORKSPACE_LAYOUT = """() => {
    const issues = [];
    const rail = document.querySelector('.activity-rail').getBoundingClientRect();
    if (rail.width !== 48) issues.push('rail width differs from 48px');
    const names = ['workspace-vietsub-tab','workspace-editor-tab','workspace-uploader-tab',
        'workspace-editor','workspace-uploader','editor-back-source','uploader-back-source'];
    for (const id of names) {
        const el = document.getElementById(id);
        if (!el.getClientRects().length) continue;
        const r = el.getBoundingClientRect();
        if (r.left < 0 || r.top < 0 || r.right > innerWidth+1 || r.bottom > innerHeight+1)
            issues.push(id + ' outside viewport');
        if (el.tagName === 'BUTTON') {
            const hit = document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
            if (hit && !el.contains(hit)) issues.push(id + ' covered');
        }
    }
    return issues;
}"""


def run(output):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    output.mkdir(parents=True, exist_ok=True)
    records, screenshots, errors = [], [], []
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg required for deterministic video fixture")
    with tempfile.TemporaryDirectory(prefix="vietsub-phase1-qa-") as temp:
        root = Path(temp)
        if os.name == "nt" and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
        os.environ["VIETSUB_STUDIO_CONFIG_DIR"] = str(root / "config")
        os.environ["VIETSUB_STUDIO_DOWNLOAD_DIR"] = str(root / "downloads")
        os.environ["APPDATA"] = str(root / "appdata")
        os.environ["LOCALAPPDATA"] = str(root / "localappdata")
        import app
        from vietsub import manifest
        source = root / "sample.mp4"
        make_video(source, "1280x720", ffmpeg)
        server = make_server("127.0.0.1", 0, app.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for width, height in ((1280, 720), (1440, 900), (1920, 1080)):
                    label = f"{width}x{height}"
                    directory = root / label
                    directory.mkdir()
                    video = directory / "Bàn giao Việt 字.mp4"
                    shutil.copyfile(source, video)
                    raw, vi = directory / "clip.raw.srt", directory / "clip.vi.srt"
                    raw.write_text(RAW, encoding="utf-8"); vi.write_text(VI, encoding="utf-8")
                    project = {"project_dir": str(directory), "project_name": "Bàn giao Việt 字",
                               "video": str(video), "source_video": str(video), "video_destination": str(video),
                               "raw_srt": str(raw), "translated_srt": str(vi), "crop_coords": CROP,
                               "video_resolution": {"width": 1280, "height": 720}}
                    app.write_project_manifest(project, video_id="123", source_url="local://phase1", status="success")
                    manifest.save_project(project, metadata={"title": "Tiêu đề thật từ manifest", "description": "Mô tả kiểm thử", "hashtags": ["#VietSub", "#Anime"]})
                    job = {"id": "phase1-ready", "status": "success", "step": "done", "result_ready": True,
                           "source_type": "local", "source_video": str(video), "video_id": "123", "source_url": "local://phase1",
                           "ocr_lang": "ch", "created_at": 1, "finished_at": 2, "project_name": project["project_name"],
                           "result": project, "crop_coords": CROP, "video_resolution": project["video_resolution"]}
                    with app.queue_state_lock:
                        app.workflow_jobs[:] = [job]; app.queue_revision += 1
                    with app.status_lock:
                        app.progress_status.update(status="success", step="done", job_id=job["id"], logs=[],
                                                   log_base_index=0, error="", result_ready=True, result=dict(project))
                    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
                    page = context.new_page()
                    page.add_init_script("window.qaCsp = []; document.addEventListener('securitypolicyviolation', e => window.qaCsp.push(e.violatedDirective));")
                    local_errors, failures, navigations = [], [], []
                    page.on("pageerror", lambda error: local_errors.append(str(error)))
                    page.on("console", lambda message: local_errors.append(message.text) if message.type == "error" else None)
                    page.on("response", lambda response: failures.append(f"{response.status} {response.url}") if response.status >= 400 else None)
                    page.on("framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
                    def fixtures(route):
                        path = route.request.url.split(url, 1)[-1].split("?", 1)[0]
                        payload = None
                        if path == "/api/health":
                            payload = {"ready_local": True, "ready_douyin": True, "checks": {}}
                        elif path == "/api/update": payload = {"current_version": app.APP_VERSION, "update_available": False}
                        elif path == "/api/history": payload = []
                        elif path == "/api/check-edge": payload = {"connected": False, "browser": "Chrome"}
                        elif path == "/api/sources/sessions": payload = {"browser_connected": False, "sites": {}}
                        if payload is None: route.continue_()
                        else: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
                    page.route("**/api/**", fixtures)
                    try:
                        page.goto(url, wait_until="networkidle")
                        page.evaluate("document.fonts.ready")
                        page.wait_for_function("() => document.querySelector('#result-editor-btn') && !document.querySelector('#result-editor-btn').disabled")
                        page.locator("#sidebar-resizer").dblclick(); page.locator("#console-resizer").dblclick()
                        assert page.locator(".activity-rail").bounding_box()["width"] == 48
                        assert not page.evaluate(OVERFLOW)
                        assert not page.evaluate(WORKSPACE_LAYOUT)
                        # Real local preview/crop and state must survive workspace navigation.
                        page.locator("#source-local-tab").click()
                        page.locator("#local-video-input").set_input_files(str(source))
                        page.wait_for_function("() => currentPreview && !document.querySelector('#add-queue-btn').disabled")
                        page.locator("#lower-third-btn").click()
                        page.locator("#preview-player").evaluate("el => { el.pause(); el.currentTime = 1; window.qaPlayer = el; }")
                        before = page.evaluate("({preview:currentPreview.id, crop:{...crop},src:qaPlayer.src,time:qaPlayer.currentTime,name:document.querySelector('#project-name').value})")
                        count = len(navigations)
                        for workspace in ("editor", "uploader", "vietsub", "editor", "uploader", "vietsub"):
                            page.locator(f"#workspace-{workspace}-tab").click()
                            assert page.locator(f"#workspace-{workspace}-tab").get_attribute("aria-selected") == "true"
                            assert not page.evaluate(OVERFLOW)
                            assert not page.evaluate(WORKSPACE_LAYOUT)
                            # Exercise limited pane space, not just the default console height.
                            page.locator('#console-resizer').focus(); page.keyboard.press('End')
                            page.evaluate("document.querySelector('#update-banner').hidden = false")
                            assert not page.evaluate(OVERFLOW)
                            assert not page.evaluate(WORKSPACE_LAYOUT)
                            page.evaluate("document.querySelector('#update-banner').hidden = true")
                            page.locator('#console-resizer').dblclick()
                            if workspace != "vietsub":
                                assert page.locator("#workbench").evaluate("el => el.hidden && el.inert")
                                assert not page.locator("#workspace-" + workspace).evaluate("el => el.hidden || el.inert")
                        after = page.evaluate("({preview:currentPreview.id,crop:{...crop},src:qaPlayer.src,time:qaPlayer.currentTime,name:document.querySelector('#project-name').value})")
                        assert before == after and len(navigations) == count
                        assert page.locator("#preview-player").evaluate("el => el === window.qaPlayer")
                        crop_check(page, label + " crop after workspace roundtrip", records)
                        image = output / f"{label}-vietsub.png"; page.screenshot(path=str(image)); screenshots.append(str(image))
                        # Keyboard rail is vertical; background progress never switches workspace.
                        page.locator("#workspace-vietsub-tab").focus(); page.keyboard.press("ArrowDown")
                        assert page.locator("#workspace-editor-tab").get_attribute("aria-selected") == "true"
                        page.evaluate("pollProgress()")
                        assert page.locator("#workspace-editor-tab").get_attribute("aria-selected") == "true"
                        page.keyboard.press("End")
                        assert page.locator("#workspace-uploader-tab").get_attribute("aria-selected") == "true"
                        # Drawer inert includes rail, with focus restored from every workspace.
                        page.locator("#settings-open-btn").click()
                        assert page.locator("#workbench-shell").evaluate("el => el.inert")
                        page.keyboard.press("Escape")
                        assert page.locator("#settings-open-btn").evaluate("el => el === document.activeElement")
                        page.locator("#workspace-vietsub-tab").click()
                        page.locator("#console-result-tab").click()
                        page.locator("#result-editor-btn").click()
                        page.wait_for_function("() => !document.querySelector('#editor-project').hidden")
                        assert page.locator("#workspace-editor-tab").get_attribute("aria-selected") == "true"
                        assert page.locator("#editor-project-name").inner_text() == project["project_name"]
                        assert page.locator("#editor-title").inner_text() == "Tiêu đề thật từ manifest"
                        assert "0.6400" in page.locator("#editor-crop").inner_text()
                        page.wait_for_function("() => document.querySelector('#editor-video').readyState >= 1")
                        assert not page.evaluate(OVERFLOW)
                        image = output / f"{label}-editor.png"; page.screenshot(path=str(image)); screenshots.append(str(image))
                        page.locator("#workspace-vietsub-tab").click()
                        page.locator('[data-job-id="phase1-ready"]').get_by_role("button", name="Đẩy sang Đăng đa nền tảng →", exact=True).click()
                        page.wait_for_function("() => !document.querySelector('#uploader-project').hidden")
                        assert page.locator("#uploader-hashtags").inner_text() == "#VietSub #Anime"
                        page.wait_for_function("() => document.querySelector('#uploader-video').readyState >= 1")
                        image = output / f"{label}-uploader.png"; page.screenshot(path=str(image)); screenshots.append(str(image))
                        page.locator("#workspace-editor-tab").click()
                        page.locator("#editor-refresh-project").click()
                        page.wait_for_function("() => document.querySelector('#editor-project-status').dataset.state !== 'loading'")
                        assert page.locator("#workspace-editor-tab").get_attribute("aria-selected") == "true"
                        page.locator("#workspace-vietsub-tab").click()
                        # Real dry run + destructive cleanup on throwaway media only.
                        page.locator("#workspace-vietsub-tab").click()
                        page.locator("#queue-select-finished").click()
                        page.locator("#bulk-delete-btn").click()
                        page.locator("#bulk-delete-files").check()
                        page.wait_for_function("() => !document.querySelector('#bulk-delete-confirm').disabled")
                        assert page.locator("#bulk-keep-srt").is_checked()
                        page.locator("#bulk-delete-confirm").click()
                        page.wait_for_function("() => !bulkDeleteBusy && queueJobs.length === 0")
                        assert not video.exists() and raw.exists() and vi.exists()
                        assert Path(manifest.path_for(project)).exists()
                        assert len(navigations) == count
                        assert not local_errors, local_errors
                        assert not failures, failures
                        csp_errors = page.evaluate("window.qaCsp")
                        assert not csp_errors, csp_errors
                        records.append({"viewport": label, "rail_css_px": 48, "workspace_switch_no_reload": True,
                                        "state_preserved": True, "real_handoff": True, "keep_srt": True,
                                        "console_errors": len(local_errors), "csp_errors": len(csp_errors),
                                        "http_errors": len(failures)})
                    except Exception:
                        page.screenshot(path=str(output / f"{label}-failure.png"))
                        errors.append({"viewport": label, "console": local_errors, "http": failures})
                        raise
                    finally:
                        context.close()
                browser.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(5)
            (output / "report.json").write_text(json.dumps({"checks": records, "screenshots": screenshots, "issues": errors},
                                                           ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": len(records), "screenshots": len(screenshots), "issues": errors, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
