"""Opt-in live CapCut + SRT alignment + render smoke with a fixed public test phrase.

No user files/account/cookies required. Run only explicitly:
python tools/editor_live_smoke.py --live --output artifacts/phase2-live
Internal service availability can change; this is not part of deterministic CI.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(output):
    from vietsub.editor.renderer import execute, probe_media
    from vietsub.editor.options import normalize
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise RuntimeError('FFmpeg and FFprobe required')
    output.mkdir(parents=True, exist_ok=True)
    source = output/'qa-source.mp4'
    if source.exists():
        raise RuntimeError('Choose an empty output directory to avoid overwriting a previous smoke')
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=0x10251e:s=640x360:r=25:d=5',
                    '-f','lavfi','-i','sine=frequency=220:duration=5','-c:v','libx264','-pix_fmt','yuv420p',
                    '-c:a','aac','-shortest',str(source)],check=True,timeout=30)
    srt = output/'qa.vi.srt'
    srt.write_text('1\n00:00:00,500 --> 00:00:03,500\nXin chào.\n',encoding='utf-8')
    project = {'project_dir':str(output),'crop_box':{'x':.1,'y':.7,'width':.8,'height':.2},
               'assets':{'video':str(source),'translated_srt':str(srt)}}
    options = normalize({'tts_enabled':True,'tts_voice':'BV421_vivn_streaming','tts_fallback':False,
                         'banner_text':'QA CAPCUT LIVE SMOKE','font_size':28,'encoder':'libx264'},inherited_crop=project['crop_box'])
    progress=[]
    import uuid
    result = execute(uuid.uuid4().hex,project,options,'render',ffmpeg=ffmpeg,ffprobe=ffprobe,
                     fonts_dir=str(ROOT/'static/fonts'),cancel=threading.Event(),update=lambda **values:progress.append(values),
                     capcut_config={'editor_capcut_compat':True})
    metadata=probe_media(result['output_path'],ffprobe)
    voice=probe_media(result['assets']['tts_audio'],ffprobe)
    report={'provider':'capcut_internal','phrase':'Xin chào.','voice':'BV421_vivn_streaming',
            'result':result,'video_probe':metadata,'voice_probe':voice,'progress':progress,
            'live_service_scope':'Anonymous fixed public phrase only; no account credentials; not a guarantee of future availability.'}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'live_capcut_render':'success','output':result['output_path'],'duration':metadata['duration'],
                      'audio_duration':voice['duration']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Explicitly send the fixed public phrase to CapCut')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.live:parser.error('--live is required; no request sent')
    run(args.output.resolve())
