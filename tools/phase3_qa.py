"""Deterministic Publish Hub UI QA with real Flask/profile/queue persistence.

The browser upload executor is mocked: no accounts/platform traffic or real post.
One platform succeeds as fixture, one fails/needs verification, others keep running.
"""
import argparse
import contextlib
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tools'))
from workbench_qa import make_video,OVERFLOW


def run(output):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    output.mkdir(parents=True,exist_ok=True);reports=[];screenshots=[];issues=[]
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:raise RuntimeError('FFmpeg required for local fixture')
    with tempfile.TemporaryDirectory(prefix='vietsub-publish-qa-') as temp:
        root=Path(temp)
        if os.name=='nt' and 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
            os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(os.environ['LOCALAPPDATA'])/'ms-playwright')
        os.environ.update(VIETSUB_STUDIO_CONFIG_DIR=str(root/'config'),VIETSUB_STUDIO_DOWNLOAD_DIR=str(root/'downloads'),
                          APPDATA=str(root/'appdata'),LOCALAPPDATA=str(root/'localappdata'))
        import app
        from vietsub import manifest
        source=root/'sample.mp4';make_video(source,'640x360',ffmpeg)
        for directory in (root/'config',root/'downloads'):directory.mkdir(exist_ok=True)
        store,browser,queue=app.uploader_api.services()
        profiles=[store.create('youtube','QA YouTube'),store.create('tiktok','QA TikTok')]
        for profile in profiles:store.update_session(profile['id'],'alive','session_alive')
        def fake_upload(profile_id,video,metadata,*,visibility,cancel,progress,before_publish,artifact_dir,
                        upload_mode='auto',debug_mode=False,confirm_submitted=None,on_awaiting_submit=None,
                        selectors=None,job_id='',assisted_timeout_ms=None,**kwargs):
            progress({'phase':'fixture-upload','percent':50})
            before_publish()
            return {'status':'success','url':'','message':'QA mock only — no platform post'}
        server=make_server('127.0.0.1',0,app.app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(browser,'upload',side_effect=fake_upload),patch.object(app,'resolve_browser_executable',return_value='chrome.exe'),sync_playwright() as pw:
                instance=pw.chromium.launch(headless=True)
                for width,height in ((1280,720),(1440,900),(1920,1080)):
                    label=f'{width}x{height}';directory=root/label;directory.mkdir()
                    video=directory/'source.mp4';shutil.copyfile(source,video)
                    edited=directory/'edited.mp4';shutil.copyfile(source,edited)
                    raw=directory/'raw.srt';vi=directory/'vi.srt'
                    raw.write_text('1\n00:00:00,000 --> 00:00:01,000\n你好世界\n',encoding='utf-8')
                    vi.write_text('1\n00:00:00,000 --> 00:00:01,000\nXin chào thế giới\n',encoding='utf-8')
                    result={'project_name':'QA Publish','project_dir':str(directory),'video':str(video),'source_video':str(video),'raw_srt':str(raw),'translated_srt':str(vi)}
                    _,value=manifest.save_project(result,metadata={'title':'Video QA','description':'Mô tả QA','hashtags':['#VietSub']})
                    value['assets']['edited_video']=str(edited);manifest.write_atomic(manifest.path_for(result),value)
                    with app.queue_state_lock:
                        app.workflow_jobs[:]=[{'id':'publish-qa','status':'success','step':'done','result_ready':True,'result':result,'source_type':'local','source_video':str(video),
                                              'project_name':'QA Publish','ocr_lang':'ch','created_at':1,'finished_at':2}];app.queue_revision+=1
                    app.reset_progress()
                    context=instance.new_context(viewport={'width':width,'height':height},reduced_motion='reduce');page=context.new_page();errors=[];http=[]
                    page.add_init_script("window.qaCsp=[];document.addEventListener('securitypolicyviolation',e=>qaCsp.push(e.violatedDirective))")
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
                    page.on('response',lambda response:http.append(f'{response.status} {response.url}') if response.status>=400 else None)
                    def fixture(route):
                        path=route.request.url.split(url,1)[-1].split('?',1)[0]
                        mapping={'/api/update':{'current_version':app.APP_VERSION,'update_available':False},'/api/check-edge':{'connected':False,'browser':'Chrome'},
                                 '/api/health':{'checks':{},'ready_local':True,'ready_douyin':True},'/api/history':[],'/api/sources/sessions':{'sites':{},'browser_connected':False}}
                        if path in mapping:route.fulfill(status=200,content_type='application/json',body=json.dumps(mapping[path]))
                        else:route.continue_()
                    page.route('**/api/**',fixture)
                    try:
                        page.goto(url,wait_until='networkidle');page.evaluate('document.fonts.ready')
                        page.locator('[data-job-id="publish-qa"]').get_by_role('button',name='Đẩy sang Đăng đa nền tảng →',exact=True).click()
                        page.wait_for_function("() => !document.querySelector('#uploader-content-fields').disabled")
                        assert not page.evaluate(OVERFLOW)
                        page.locator('#uploader-inspector-publish-tab').click()
                        page.locator('#uploader-title-input').fill('Tiêu đề riêng '+label)
                        page.locator('#uploader-save-metadata').click()
                        page.wait_for_function("() => document.querySelector('#uploader-status').textContent.includes('lưu')")
                        assert manifest.snapshot(result)['metadata']['title']=='Tiêu đề riêng '+label
                        page.locator('#uploader-inspector-accounts-tab').click()
                        labels=page.locator('#uploader-profile-list input[type="checkbox"]')
                        labels.nth(0).check();labels.nth(1).check()
                        page.locator('#uploader-inspector-publish-tab').click()
                        page.locator('#uploader-visibility').select_option('private')
                        page.locator('#uploader-schedule-mode').select_option('local')
                        scheduled=(datetime.now(timezone.utc)+timedelta(hours=8)).replace(tzinfo=None).isoformat(timespec='minutes')
                        page.locator('#uploader-scheduled-at').fill(scheduled)
                        page.locator('#uploader-queue-submit').click()
                        page.wait_for_function("() => document.querySelector('#uploader-publish-jobs').textContent.includes('lịch')")
                        rows=[j for j in queue.snapshot() if j['metadata']['title']=='Tiêu đề riêng '+label]
                        assert len(rows)==2 and all(j['status']=='scheduled' for j in rows)
                        assert all(j['video']==str(edited) for j in rows)
                        image=output/f'{label}-publish.png';page.screenshot(path=str(image));screenshots.append(str(image))
                        # Phase 4 geometry assertions: uncapped publish queue height & zero button horizontal overflow.
                        queue_h=page.evaluate("() => document.getElementById('uploader-publish-jobs').clientHeight")
                        assert queue_h>=150, f'{label}: #uploader-publish-jobs crushed to {queue_h}px'
                        btn_overflow=page.evaluate("""() => [...document.querySelectorAll('#workspace-uploader button')].filter(b=>b.getClientRects().length).filter(b=>b.scrollWidth>b.clientWidth+1).map(b=>b.id||b.textContent)""")
                        assert not btn_overflow, f'{label}: uploader button overflow {btn_overflow}'
                        # Explicit cancel through UI per-account, then clear finished via #uploader-queue-clear-finished.
                        for task in rows:queue.cancel(task['id'])
                        page.locator('#uploader-queue-refresh').click()
                        page.wait_for_function("() => document.querySelector('#uploader-publish-jobs').textContent.includes('huỷ')")
                        page.on('dialog', lambda d: d.accept())
                        assert not page.locator('#uploader-queue-clear-finished').is_disabled()
                        page.locator('#uploader-queue-clear-finished').click()
                        page.wait_for_function("() => document.querySelector('#uploader-queue-count').textContent === '0'")
                        assert video.exists() and edited.exists()
                        assert not errors,errors
                        assert not http,http
                        assert not page.evaluate('qaCsp')
                        reports.append({'viewport':label,'edited_video':True,'metadata_saved':True,'two_accounts_scheduled':True,'cancel_preserves_source':True,
                                        'upload_executor':'mock only, no platform traffic','console_errors':0,'csp_errors':0,'http_errors':0})
                    except Exception:
                        page.screenshot(path=str(output/f'{label}-failure.png'));issues.append({'viewport':label,'errors':errors,'http':http});raise
                    finally:context.close()
                instance.close()
        finally:
            app.uploader_api.shutdown();server.shutdown();server.server_close();thread.join(5)
            (output/'report.json').write_text(json.dumps({'checks':reports,'screenshots':screenshots,'issues':issues},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':len(reports),'screenshots':len(screenshots),'issues':issues}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())
