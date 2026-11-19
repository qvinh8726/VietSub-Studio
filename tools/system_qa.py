"""Three-workspace desktop QA: isolated Flask + real FFmpeg, no platform traffic."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/'tools'))
from workbench_qa import make_video, OVERFLOW


def run(output):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    output.mkdir(parents=True, exist_ok=True)
    checks, screenshots, errors = [], [], []
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise RuntimeError('FFmpeg + FFprobe required')
    with tempfile.TemporaryDirectory(prefix='vietsub-system-qa-') as temp:
        root = Path(temp)
        if os.name == 'nt' and 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(os.environ['LOCALAPPDATA'])/'ms-playwright')
        os.environ.update(VIETSUB_STUDIO_CONFIG_DIR=str(root/'config'), VIETSUB_STUDIO_DOWNLOAD_DIR=str(root/'downloads'),
                          APPDATA=str(root/'appdata'), LOCALAPPDATA=str(root/'localappdata'))
        import app
        from vietsub import manifest
        from vietsub.editor import renderer
        silent = root/'silent.mp4'; make_video(silent, '960x540', ffmpeg)
        source = root/'source.mp4'
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(silent), '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=48000:duration=6', '-c:v', 'copy', '-c:a', 'aac', '-shortest', str(source)], check=True)
        raw, vi = root/'source.raw.srt', root/'source.vi.srt'
        raw.write_text('1\n00:00:00,200 --> 00:00:05,500\n测试字幕\n', encoding='utf-8')
        vi.write_text('1\n00:00:00,200 --> 00:00:05,500\nVietSub Studio · Mẫu kiểm thử\n', encoding='utf-8')
        result = {'project_name': 'Phim ngắn · QA Studio', 'project_dir': str(root), 'video': str(source), 'source_video': str(source),
                  'raw_srt': str(raw), 'translated_srt': str(vi), 'crop_coords': {'crop_x': .1, 'crop_y': .7, 'crop_width': .8, 'crop_height': .2},
                  'video_resolution': {'width': 960, 'height': 540}}
        app.write_project_manifest(result, video_id='qa', source_url='local://qa', status='success')
        with app.queue_state_lock:
            app.workflow_jobs[:] = [{'id': 'qa', 'status': 'success', 'step': 'done', 'result_ready': True, 'result': result,
                'project_name': result['project_name'], 'source_type': 'local', 'source_video': str(source), 'ocr_lang': 'ch', 'created_at': 1, 'finished_at': 2}]
            app.queue_revision += 1
        app.reset_progress()
        store, _, _ = app.uploader_api.services()
        for platform, name in [('youtube', 'Kênh sáng tạo'), ('tiktok', 'Video ngắn'), ('facebook', 'Studio Việt')]:
            profile = store.create(platform, name)
            store.update_session(profile['id'], 'alive', 'session_alive')
        server = make_server('127.0.0.1', 0, app.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for width, height in ((1280,720), (1440,900), (1920,1080)):
                    label = f'{width}x{height}'
                    context = browser.new_context(viewport={'width':width,'height':height}, reduced_motion='no-preference')
                    page = context.new_page(); js_errors, http_errors = [], []
                    page.on('pageerror', lambda error: js_errors.append(str(error)))
                    page.on('console', lambda msg: js_errors.append(msg.text) if msg.type == 'error' else None)
                    page.on('response', lambda res: http_errors.append(f'{res.status} {res.url}') if res.status >= 400 else None)
                    page.add_init_script("""window.qaCsp=[];window.qaFrames=0;
                        const raf=window.requestAnimationFrame.bind(window);window.requestAnimationFrame=fn=>raf(t=>{qaFrames++;fn(t)});
                        document.addEventListener('securitypolicyviolation',e=>qaCsp.push(e.violatedDirective));""")
                    def fixture(route):
                        path = route.request.url.split(url,1)[-1].split('?',1)[0]
                        data = {'/api/update': {'current_version':app.APP_VERSION, 'update_available':False},
                                '/api/check-edge': {'connected':False,'browser':'Chrome'}, '/api/health':{'checks':{},'ready_local':True,'ready_douyin':True},
                                '/api/history': [], '/api/sources/sessions':{'sites':{},'browser_connected':False}}
                        if path in data:
                            route.fulfill(status=200, content_type='application/json', body=json.dumps(data[path]))
                        else:
                            route.continue_()
                    page.route('**/api/**', fixture)
                    try:
                        page.goto(url, wait_until='networkidle'); page.evaluate('document.fonts.ready')
                        page.locator('#source-local-tab').click()
                        page.locator('#local-video-input').set_input_files(str(source))
                        page.wait_for_function("() => currentPreview && document.querySelector('#preview-player').readyState >= 2", timeout=30000)
                        page.evaluate("document.querySelector('#preview-player').pause()")
                        ids = json.loads((ROOT/'tests/frontend/dom-contract.json').read_text(encoding='utf-8'))['ids']
                        missing = page.evaluate('(ids) => ids.filter(id => !document.getElementById(id))', list(ids))
                        assert not missing, missing
                        def capture(workspace):
                            page.wait_for_function('() => !VietSubVFX.pending', timeout=10000)
                            failures = page.evaluate(OVERFLOW)
                            assert not failures, failures
                            image = output/f'{label}-{workspace}.png'; page.screenshot(path=str(image)); screenshots.append(str(image))
                            checks.append({'viewport':label,'workspace':workspace,'overflow':failures,'dom_ids_preserved':len(ids)})
                        capture('vietsub')
                        page.locator('[data-job-id="qa"]').get_by_role('button', name='Chuyển sang Auto Edit →', exact=True).click()
                        page.wait_for_function("() => !document.querySelector('#editor-render-btn').disabled && document.querySelector('#editor-video').readyState >= 1", timeout=30000)
                        # All inspector tabs: keyboard, selection, exclusive visible panels.
                        tabs = page.locator('#editor-inspector-tabs [role=tab]')
                        for index in range(4):
                            tabs.nth(index).click()
                            assert tabs.nth(index).get_attribute('aria-selected') == 'true'
                            assert page.locator('#editor-options-fieldset > [role=tabpanel]:not([hidden])').count() == 1
                            assert not page.evaluate(OVERFLOW)
                        page.locator('#editor-inspector-frame-tab').focus(); page.keyboard.press('ArrowRight')
                        assert page.locator('#editor-inspector-audio-tab').get_attribute('aria-selected') == 'true'
                        page.locator('#editor-speed').fill('1.5'); page.locator('#editor-speed').dispatch_event('change')
                        page.locator('#editor-change-audio-pitch').check()
                        page.locator('[data-pitch-preset="2"]').click()
                        assert page.locator('#editor-pitch-semitones').input_value() == '2'
                        assert page.locator('#editor-speed-range').input_value() == '1.5'
                        assert '04.00' in page.locator('#editor-speed-duration').inner_text()
                        page.locator('#editor-save-btn').click()
                        page.wait_for_function("() => !VietSubEditor.state().dirty")
                        saved = manifest.snapshot(result)['editor_options']
                        assert saved['speed'] == 1.5 and saved['change_audio_pitch'] is True and saved['pitch_semitones'] == 2
                        page.locator('#editor-audio-preview').click()
                        page.wait_for_function("() => VietSubEditor.state().task?.action === 'audio_preview' && ['success','error'].includes(VietSubEditor.state().task?.status)", timeout=30000)
                        state = page.evaluate('VietSubEditor.state()'); assert state['task']['status'] == 'success', state['task']
                        duration = renderer.probe_media(state['task']['output_path'], ffprobe)['duration']
                        assert abs(duration-3) < .08, duration
                        assert 'CapCut uses an unofficial' not in page.locator('#editor-task-warnings').inner_text()
                        page.locator('#editor-inspector-audio-panel').evaluate('(el) => el.scrollTop = 0')
                        capture('editor')
                        if width == 1280:
                            page.locator('#editor-render-btn').click()
                            page.wait_for_function("() => VietSubEditor.state().task?.action === 'render' && ['success','error'].includes(VietSubEditor.state().task?.status)", timeout=90000)
                            state = page.evaluate('VietSubEditor.state()'); assert state['task']['status'] == 'success', state['task']
                            duration = renderer.probe_media(state['task']['output_path'], ffprobe)['duration']
                            assert abs(duration-4) < .15, duration
                            checks.append({'check':'real_ffmpeg_speed_pitch_render','duration':duration})
                            page.locator('#editor-inspector-export-tab').click(); page.locator('#editor-draft-btn').click()
                            page.wait_for_function("() => VietSubEditor.state().task?.action === 'capcut_draft' && ['success','error'].includes(VietSubEditor.state().task?.status)", timeout=90000)
                            state = page.evaluate('VietSubEditor.state()'); assert state['task']['status'] == 'success', state['task']
                            with zipfile.ZipFile(state['task']['output_path']) as bundle:
                                name = next(name for name in bundle.namelist() if name.endswith('/draft_content.json'))
                                draft = json.loads(bundle.read(name)); segment = draft['tracks'][0]['segments'][0]
                                assert segment['speed'] == 1.5 and segment['is_tone_modify'] is True
                                assert draft['vietsub']['pitch_semitones_baked'] == 2
                            with page.expect_download() as download:
                                page.locator('#editor-output-link').click()
                            assert download.value.failure() is None
                            checks.append({'check':'capcut_draft_archive','native_speed':1.5,'tone_modify':True,'semitones_baked':2})
                        # Handoff with confirmed rendered video, no posting.
                        page.locator('#editor-send-publish-btn').click()
                        page.wait_for_function("() => document.querySelector('#workspace-uploader-tab').getAttribute('aria-selected')==='true' && !document.querySelector('#uploader-content-fields').disabled")
                        page.wait_for_function("() => document.querySelector('#uploader-video').readyState >= 2", timeout=15000)
                        page.locator('#uploader-video').evaluate('(video) => {video.pause();video.currentTime=.8}')
                        page.wait_for_function("() => !document.querySelector('#uploader-video').seeking")
                        capture('uploader')
                        page.locator('#uploader-inspector-publish-tab').click()
                        assert not page.evaluate(OVERFLOW)
                        page.locator('#uploader-inspector-publish-tab').focus(); page.keyboard.press('Home')
                        assert page.locator('#uploader-inspector-accounts-tab').get_attribute('aria-selected') == 'true'
                        # Check active preferences and no idle callbacks after all effects settle.
                        page.wait_for_function('() => !VietSubVFX.pending', timeout=10000)
                        page.wait_for_function('() => document.getAnimations().filter(a=>a.playState==="running").length === 0', timeout=5000)
                        frames = page.evaluate('qaFrames'); page.wait_for_timeout(350)
                        assert page.evaluate('qaFrames') == frames, 'Idle requestAnimationFrame callbacks detected'
                        page.emulate_media(reduced_motion='reduce')
                        page.locator('#workspace-editor-tab').click()
                        assert page.evaluate('VietSubVFX.active') is False
                        assert page.evaluate('VietSubVFX.pending') == 0
                        assert not js_errors, js_errors
                        assert not http_errors, http_errors
                        assert not page.evaluate('qaCsp')
                        checks.append({'viewport':label,'inspector_keyboard':True,'real_audio_preview_seconds':3,'saved_speed_pitch':True,
                            'idle_raf_callbacks':0,'reduced_motion_stops_vfx':True,'console_errors':0,'http_errors':0,'csp_errors':0})
                    except Exception:
                        print('QA animations:', page.evaluate("document.getAnimations().map(a=>({name:a.animationName, state:a.playState, target:a.effect?.target?.id, css:a.effect?.target?.className, timing:a.effect?.getTiming()}))"), flush=True)
                        page.screenshot(path=str(output/f'{label}-failure.png'))
                        errors.append({'viewport':label,'console':js_errors,'http':http_errors})
                        raise
                    finally:
                        context.close()
                browser.close()
        finally:
            app.editor_api.jobs.shutdown(); app.uploader_api.shutdown()
            server.shutdown(); server.server_close(); thread.join(5)
            (output/'browser-report.json').write_text(json.dumps({'checks':checks,'screenshots':screenshots,'issues':errors,
                'limitations':['Synthetic local media; no external TTS calls, account login or platform publishing.',
                               'CapCut JSON/archive verified; CapCut desktop import not exercised.']}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'checks':len(checks),'screenshots':len(screenshots),'issues':errors}, ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,default=ROOT/'tools/qa-system')
    run(parser.parse_args().output.resolve())
