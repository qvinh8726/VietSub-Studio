"""Isolated smoke of an already-built portable EXE, no replacement GUI/server.

Uses its standard --server on port5000 only after checking the port is free.
Temporary config/download directories and this exact executable process tree only.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler


def run(exe, report_path):
    exe=exe.resolve()
    if not exe.is_file():raise RuntimeError('Build executable does not exist')
    with socket.socket() as test:
        test.bind(('127.0.0.1',5000))
    with tempfile.TemporaryDirectory(prefix='vietsub-portable-phase2-', ignore_cleanup_errors=True) as temp:
        env=os.environ.copy();env['VIETSUB_STUDIO_CONFIG_DIR']=temp;env['VIETSUB_STUDIO_DOWNLOAD_DIR']=str(Path(temp)/'downloads')
        directory=Path(temp)/'project';directory.mkdir()
        video=directory/'portable-source.mp4';raw=directory/'portable.raw.srt';vi=directory/'portable.vi.srt'
        import shutil
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:raise RuntimeError('FFmpeg required for portable render smoke')
        subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=s=320x180:r=25:d=2','-c:v','libx264',
                        '-pix_fmt','yuv420p',str(video)],check=True,timeout=30)
        raw.write_text('1\n00:00:00,000 --> 00:00:01,500\n你好世界\n',encoding='utf-8')
        vi.write_text('1\n00:00:00,000 --> 00:00:01,500\nXin chào thế giới\n',encoding='utf-8')
        result={'project_name':'portable-source','project_dir':str(directory),'video':str(video),'source_video':str(video),
                'raw_srt':str(raw),'translated_srt':str(vi),'crop_coords':{'crop_x':.1,'crop_y':.7,'crop_width':.8,'crop_height':.2}}
        queue={'version':2,'jobs':[{'id':'portable-qa','status':'success','step':'done','result_ready':True,'source_type':'local',
                                   'ocr_lang':'ch','source_video':str(video),'result':result,'created_at':1,'finished_at':2}]}
        (Path(temp)/'workflow_queue.json').write_text(json.dumps(queue,ensure_ascii=False),encoding='utf-8')
        process=subprocess.Popen([str(exe),'--server'],env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        opener=build_opener(ProxyHandler({}));deadline=time.monotonic()+75;html=''
        try:
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Portable EXE exited before server readiness')
                try:
                    with opener.open('http://127.0.0.1:5000/',timeout=2) as response:html=response.read().decode('utf-8')
                    if 'workspace-editor' in html:break
                except OSError:time.sleep(.2)
            else:raise RuntimeError('Portable app did not become ready')
            token=re.search(r'name="api-token" content="([^"]+)"',html).group(1)
            checks=[]
            for asset in ('static/editor.js','static/uploader.js','static/workbench.js','static/style.css','static/fonts.css','static/fonts/be-vietnam-pro-regular.ttf'):
                with opener.open('http://127.0.0.1:5000/'+asset,timeout=5) as response:
                    assert response.status==200 and response.read(1)
                checks.append(asset)
            request=Request('http://127.0.0.1:5000/api/editor/capabilities',headers={'X-VietSub-Token':token})
            with opener.open(request,timeout=35) as response:capabilities=json.load(response)
            assert capabilities['ffmpeg'] and capabilities['ffprobe']
            request=Request('http://127.0.0.1:5000/api/editor/voices?provider=capcut_internal&language=vi',headers={'X-VietSub-Token':token})
            with opener.open(request,timeout=10) as response:voices=json.load(response)
            assert len(voices['voices'])>=2
            body=json.dumps({'job_id':'portable-qa','action':'render','options':{'font_size':24,'encoder':'libx264'}}).encode('utf-8')
            request=Request('http://127.0.0.1:5000/api/editor/jobs',data=body,method='POST',
                            headers={'X-VietSub-Token':token,'Content-Type':'application/json'})
            with opener.open(request,timeout=15) as response:task=json.load(response)
            limit=time.monotonic()+60
            while task['status'] in ('queued','running') and time.monotonic()<limit:
                time.sleep(.2)
                request=Request('http://127.0.0.1:5000/api/editor/jobs/'+task['id'],headers={'X-VietSub-Token':token})
                with opener.open(request,timeout=5) as response:task=json.load(response)
            assert task['status']=='success', task
            assert Path(task['output_path']).is_file() and video.is_file() and vi.is_file()
            with opener.open('http://127.0.0.1:5000'+task['output_url'],timeout=10) as response:
                assert response.status==200 and response.read(12)[4:8]==b'ftyp'
            request=Request('http://127.0.0.1:5000/api/uploader/capabilities',headers={'X-VietSub-Token':token})
            with opener.open(request,timeout=10) as response:uploader=json.load(response)
            assert len(uploader['platforms'])==6 and uploader['timezone']=='Asia/Saigon'
            request=Request('http://127.0.0.1:5000/api/uploader/profiles',headers={'X-VietSub-Token':token})
            with opener.open(request,timeout=10) as response:accounts=json.load(response)
            assert accounts['profiles']==[]
            report={'exe':str(exe),'checks':checks,'capabilities':capabilities,'uploader':uploader,'vietnamese_catalog_count':len(voices['voices']),
                    'portable_render':{'status':task['status'],'percent':task['percent'],'source_preserved':video.is_file()},
                    'scope':'startup/local assets/editor API/offline catalog/real FFmpeg render; no live TTS/model/network upload'}
            report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'portable_smoke':'success','catalog_count':len(voices['voices']),'report':str(report_path)}))
        finally:
            # Resolve/print exact intended executable before stopping its own PID tree.
            print('Stopping isolated portable process:',process.pid,str(exe))
            if process.poll() is None:
                taskkill=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/taskkill.exe'
                subprocess.run([str(taskkill),'/PID',str(process.pid),'/T','/F'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                with contextlib.suppress(subprocess.TimeoutExpired):process.wait(timeout=10)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exe',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();run(args.exe,args.report.resolve())
