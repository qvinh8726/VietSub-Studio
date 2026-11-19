"""Phase 2 real Flask/FFmpeg UI acceptance; no account or external TTS calls.

Synthetic TTS adapter emits a labelled tone through the real alignment/mix code,
not a claim that a live provider synthesized Vietnamese. All files are temporary.
"""
import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from workbench_qa import make_video, OVERFLOW


def run(output):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('FFmpeg required')
    output.mkdir(parents=True, exist_ok=True)
    reports, screenshots, issues = [], [], []
    with tempfile.TemporaryDirectory(prefix='vietsub-phase2-qa-') as temp:
        root = Path(temp)
        if os.name == 'nt' and 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(os.environ['LOCALAPPDATA']) / 'ms-playwright')
        os.environ.update(VIETSUB_STUDIO_CONFIG_DIR=str(root/'config'), VIETSUB_STUDIO_DOWNLOAD_DIR=str(root/'downloads'),
                          APPDATA=str(root/'appdata'), LOCALAPPDATA=str(root/'localappdata'))
        import app
        from vietsub.editor import providers
        from vietsub.editor.tts import TTSService
        from vietsub import manifest
        source = root / 'sample.mp4'
        make_video(source, '640x360', ffmpeg)
        class ToneProvider:
            def voices(self, language='vi'):
                return [{'id':'qa-tone','name':'QA tone — not a live voice','language':'vi-VN','provider':'capcut_internal'}]
            def synthesize(self, text, voice, path):
                rate = 24000
                with wave.open(str(path),'wb') as f:
                    f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
                    f.writeframes(b''.join(struct.pack('<h',round(1800*math.sin(2*math.pi*440*n/rate))) for n in range(18000)))
                return {'path':str(path),'provider':'capcut_internal','voice':'qa-tone',
                        'warnings':['QA synthetic tone — live CapCut service not tested.']}
        def local_service(ffmpeg,ffprobe,cache_dir,**kwargs):
            kwargs.pop('config',None)
            service=TTSService(ffmpeg,ffprobe,cache_dir,**kwargs)
            service.register('capcut_internal',ToneProvider())
            return service
        server = make_server('127.0.0.1',0,app.app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(providers,'service',side_effect=local_service), sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                for width,height in ((1280,720),(1440,900),(1920,1080)):
                    from vietsub.editor.jobs import EditorJobs
                    app.editor_api.jobs.shutdown()
                    app.editor_api.jobs = EditorJobs()
                    label=f'{width}x{height}'
                    project_dir=root/label; project_dir.mkdir()
                    video=project_dir/'clip.mp4'; shutil.copyfile(source,video)
                    raw=project_dir/'clip.raw.srt'; vi=project_dir/'clip.vi.srt'
                    raw.write_text('1\n00:00:00,200 --> 00:00:01,300\n你好世界\n',encoding='utf-8')
                    vi.write_text('1\n00:00:00,200 --> 00:00:01,300\nXin chào thế giới\n',encoding='utf-8')
                    crop={'crop_x':.1,'crop_y':.7,'crop_width':.8,'crop_height':.2}
                    result={'project_name':'QA Auto Edit','project_dir':str(project_dir),'video':str(video),'source_video':str(video),
                            'raw_srt':str(raw),'translated_srt':str(vi),'crop_coords':crop,'video_resolution':{'width':640,'height':360}}
                    app.write_project_manifest(result,video_id='qa',source_url='local://qa',status='success')
                    with app.queue_state_lock:
                        app.workflow_jobs[:]=[{'id':'qa','status':'success','step':'done','result_ready':True,'result':result,
                                              'project_name':result['project_name'],'source_type':'local','source_video':str(video),
                                              'ocr_lang':'ch','created_at':1,'finished_at':2}]
                        app.queue_revision+=1
                    app.reset_progress()
                    context=browser.new_context(viewport={'width':width,'height':height},reduced_motion='reduce')
                    page=context.new_page(); errors=[]; failures=[]
                    page.add_init_script("window.qaCsp=[];document.addEventListener('securitypolicyviolation',e=>qaCsp.push(e.violatedDirective))")
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
                    page.on('response',lambda response:failures.append(f'{response.status} {response.url}') if response.status>=400 else None)
                    def fixture(route):
                        path=route.request.url.split(url,1)[-1].split('?',1)[0]
                        payload={'/api/update':{'current_version':app.APP_VERSION,'update_available':False},
                                 '/api/check-edge':{'connected':False,'browser':'Chrome'},'/api/sources/sessions':{'sites':{},'browser_connected':False},
                                 '/api/history':[],'/api/health':{'checks':{},'ready_local':True,'ready_douyin':True}}
                        if path in payload:route.fulfill(status=200,content_type='application/json',body=json.dumps(payload[path]))
                        else:route.continue_()
                    page.route('**/api/**',fixture)
                    try:
                        page.goto(url,wait_until='networkidle'); page.evaluate('document.fonts.ready')
                        page.locator('[data-job-id="qa"]').get_by_role('button',name='Chuyển sang Auto Edit →',exact=True).click()
                        page.wait_for_function("() => !document.querySelector('#editor-render-btn').disabled",timeout=30000)
                        page.wait_for_function("() => document.querySelector('#editor-video').readyState >= 1")
                        assert not page.evaluate(OVERFLOW)
                        page.locator('#editor-crop-y').fill('60')
                        page.locator('#editor-crop-y').dispatch_event('change')
                        page.locator('#editor-mask-mode').select_option('solid_bar')
                        page.locator('#editor-font-size').fill('28'); page.locator('#editor-font-size').dispatch_event('change')
                        page.locator('#editor-save-btn').click()
                        page.wait_for_function("() => document.querySelector('#editor-dirty').textContent.includes('Đã')",timeout=15000)
                        saved=manifest.snapshot(result)
                        assert abs(saved['editor_options']['crop_box']['y']-.6)<.001
                        assert saved['crop_box']['y']==.7
                        image=output/f'{label}-editor.png'; page.screenshot(path=str(image));screenshots.append(str(image))
                        page.locator('#editor-render-btn').click()
                        page.wait_for_function("() => !document.querySelector('#editor-output-video').hidden && document.querySelector('#editor-task-percent').textContent.includes('100')",timeout=90000)
                        page.wait_for_function("() => document.querySelector('#editor-output-video').readyState >= 1",timeout=15000)
                        assert Path(manifest.snapshot(result)['assets']['edited_video']).is_file()
                        assert not page.evaluate(OVERFLOW)
                        page.locator('#editor-output').scroll_into_view_if_needed()
                        page.locator('#editor-output-video').evaluate("el => { el.pause(); el.currentTime = 0.7; }")
                        page.wait_for_function("() => document.querySelector('#editor-output-video').readyState >= 2 && !document.querySelector('#editor-output-video').seeking")
                        image=output/f'{label}-rendered.png';page.screenshot(path=str(image));screenshots.append(str(image))
                        # Open audio section; real queue/TTSService/FFmpeg with synthetic provider only.
                        page.locator('#editor-tts-provider').evaluate("el => el.closest('details').open = true")
                        page.locator('#editor-load-voices-btn').click()
                        page.wait_for_function("() => [...document.querySelector('#editor-tts-voice').options].some(o=>o.value==='qa-tone')")
                        page.locator('#editor-tts-voice').select_option('qa-tone')
                        page.locator('#editor-tts-preview-btn').click()
                        page.wait_for_function("() => !document.querySelector('#editor-output-audio').hidden && document.querySelector('#editor-task-percent').textContent.includes('100')",timeout=30000)
                        assert 'QA synthetic tone' in page.locator('#editor-task-warnings').inner_text()
                        page.locator('#editor-tts-enabled').check()
                        page.locator('#editor-tts-generate-btn').click()
                        page.wait_for_function("() => VietSubEditor.state().task?.action === 'tts' && VietSubEditor.state().task?.status === 'success'",timeout=30000)
                        assert Path(manifest.snapshot(result)['assets']['tts_audio']).is_file()
                        page.locator('#editor-render-btn').click()
                        page.wait_for_function("() => VietSubEditor.state().task?.action === 'render' && VietSubEditor.state().task?.status === 'success'",timeout=90000)
                        page.locator('#editor-send-publish-btn').click()
                        page.wait_for_function("() => document.querySelector('#workspace-uploader-tab').getAttribute('aria-selected') === 'true'")
                        assert '.edited.' in page.locator('#uploader-video-path').inner_text()
                        with page.expect_response(lambda response: '/api/projects/qa?workspace=uploader' in response.url):
                            page.locator('#uploader-refresh-project').click()
                        assert video.is_file() and vi.is_file()
                        assert not errors,errors
                        assert not page.evaluate('qaCsp')
                        assert not failures,failures
                        reports.append({'viewport':label,'real_render':True,'real_ffmpeg_tts_alignment':True,
                                        'tts_provider':'synthetic QA tone only','real_srt_tts_track':True,'real_tts_render_mix':True,
                                        'publish_handoff_recheck':True,'source_preserved':True,
                                        'console_errors':len(errors),'csp_errors':0,'http_errors':len(failures)})
                    except Exception:
                        page.screenshot(path=str(output/f'{label}-failure.png'))
                        issues.append({'viewport':label,'errors':errors,'http':failures})
                        raise
                    finally:context.close()
                browser.close()
        finally:
            app.editor_api.jobs.shutdown()
            server.shutdown();server.server_close();thread.join(5)
            (output/'report.json').write_text(json.dumps({'checks':reports,'screenshots':screenshots,'issues':issues},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':len(reports),'screenshots':len(screenshots),'issues':issues},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())
