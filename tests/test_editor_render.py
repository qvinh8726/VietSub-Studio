"""Real FFmpeg render/extraction integration for Auto Edit, no online providers."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from vietsub.editor import renderer
from vietsub.editor.options import normalize
from vietsub.editor.jobs import EditorJobs

FFMPEG = shutil.which('ffmpeg')
FFPROBE = shutil.which('ffprobe')
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(FFMPEG and FFPROBE, 'FFmpeg/FFprobe not installed')
class RenderIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vietsub-render-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "nguồn Việt.mp4"
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=s=320x180:r=25:d=1.2', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.2',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(self.source)],
                       check=True, timeout=30, capture_output=True)
        self.srt = self.root / 'clip.vi.srt'
        self.srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nXin chào Việt Nam\n', encoding='utf-8')
        self.project = {'project_dir': str(self.root), 'crop_box': {'x': .04, 'y': .65, 'width': .92, 'height': .3},
                        'assets': {'video': str(self.source), 'translated_srt': str(self.srt)}}
        self.before = self.source.read_bytes()

    def execute(self, action, options, identifier='a'*32):
        progress = []
        result = renderer.execute(identifier, self.project, normalize(options, inherited_crop=self.project['crop_box']), action,
                                  ffmpeg=FFMPEG, ffprobe=FFPROBE, fonts_dir=str(ROOT / 'static/fonts'),
                                  cancel=threading.Event(), update=lambda **value: progress.append(value))
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertFalse((self.root / '.vietsub-editor' / identifier).exists())
        return result, progress

    def test_render_source_ass_audio_and_auto_encoder(self):
        result, progress = self.execute('render', {'font_size': 20, 'encoder': 'auto'})
        self.assertEqual(result['output_kind'], 'video')
        self.assertTrue(Path(result['output_path']).exists())
        metadata = renderer.probe_media(result['output_path'], FFPROBE)
        self.assertEqual((metadata['width'], metadata['height']), (320, 180))
        self.assertTrue(metadata['has_audio'])
        self.assertTrue(any(value.get('percent', 0) > 0 for value in progress))

    def test_vertical_solid_bar_banner_and_mute(self):
        result, _ = self.execute('render', {'mask_mode': 'solid_bar', 'reframe': 'vertical', 'output_height': 720,
                                           'banner_text': 'Tiêu đề cố định {literal}', 'font_size': 20,
                                           'audio_mode': 'mute', 'encoder': 'libx264'})
        metadata = renderer.probe_media(result['output_path'], FFPROBE)
        self.assertEqual(metadata['width'] * 16, metadata['height'] * 9)
        self.assertFalse(metadata['has_audio'])

    def test_extract_both_formats_and_no_optional_ai_import(self):
        for index, extension in enumerate(('mp3', 'wav')):
            result, _ = self.execute('extract_audio', {'audio_format': extension}, identifier=str(index)*32)
            self.assertTrue(result['output_path'].endswith('.'+extension))
            self.assertTrue(renderer.probe_media(result['output_path'], FFPROBE)['has_audio'])
        import sys
        self.assertNotIn('audio_separator.separator', sys.modules)

    def test_nvenc_runtime_failure_falls_back_not_lists_only(self):
        from vietsub.editor import process
        capture = process.capture
        def fake(command, **kwargs):
            if 'h264_nvenc' in command:
                raise RuntimeError('No capable devices')
            return capture(command, **kwargs)
        with patch.object(process, 'capture', side_effect=fake):
            result = renderer.detect_encoders(FFMPEG, refresh=True)
        self.assertFalse(result['h264_nvenc'])
        self.assertTrue(result['libx264'])

    def test_render_with_aligned_tts_and_ducking_keeps_bgm_tail(self):
        import wave
        from vietsub.editor import providers
        class Service:
            def synthesize_srt(self, source, output, **kwargs):
                with wave.open(output, 'wb') as audio:
                    audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
                    audio.writeframes(b'\x10\x00' * 12000)
                return {'path': output, 'provider': 'synthetic', 'warnings': ['synthetic only']}
        with patch.object(providers, 'service', return_value=Service()):
            result, _ = self.execute('render', {'tts_enabled': True, 'tts_voice': 'qa-tone', 'font_size': 20})
        metadata = renderer.probe_media(result['output_path'], FFPROBE)
        self.assertGreater(metadata['duration'], 1.1)
        self.assertTrue(metadata['has_audio'])
        self.assertTrue(Path(result['assets']['tts_audio']).exists())

    def test_rotated_source_has_display_dimensions_exactly_once(self):
        rotated = self.root / 'rotated.mp4'
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-display_rotation:v:0', '90', '-i', str(self.source),
                        '-c', 'copy', str(rotated)], check=True, timeout=20, capture_output=True)
        self.project['assets']['video'] = str(rotated)
        result, _ = self.execute('render', {'font_size': 20, 'encoder': 'libx264'})
        metadata = renderer.probe_media(result['output_path'], FFPROBE)
        self.assertEqual((metadata['width'], metadata['height']), (180, 320))
        self.assertEqual(metadata['rotation'], 0)

    def test_reflected_display_matrix_matches_ffmpeg_displayed_source(self):
        reflected = self.root / 'reflected.mp4'
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-display_hflip:v:0', '-i', str(self.source),
                        '-c', 'copy', str(reflected)], check=True, timeout=20, capture_output=True)
        self.project['assets']['video'] = str(reflected)
        result, _ = self.execute('render', {'mask_mode':'none','subtitle_enabled':False,'audio_mode':'mute','encoder':'libx264'})
        def frame(path):
            return subprocess.check_output([FFMPEG,'-v','error','-i',str(path),'-frames:v','1','-vf','scale=64:36',
                                            '-f','rawvideo','-pix_fmt','rgb24','-'],timeout=20)
        expected, actual = frame(reflected), frame(result['output_path'])
        self.assertLess(sum(abs(a-b) for a,b in zip(expected,actual))/len(expected), 8)

    def test_instrumental_preserves_delayed_audio_start(self):
        import wave
        delayed = self.root / 'delayed.mp4'
        subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','color=s=320x180:r=25:d=3',
                        '-itsoffset','1','-f','lavfi','-i','sine=frequency=440:duration=2',
                        '-c:v','libx264','-c:a','aac','-t','3',str(delayed)],check=True,timeout=20,capture_output=True)
        self.project['assets']['video'] = str(delayed)
        from vietsub.editor import separation
        # Inference only is mocked; extraction, timing and mux are actual FFmpeg.
        with patch.object(separation,'separate',side_effect=lambda source,*args,**kwargs: source):
            result,_ = self.execute('render', {'mask_mode':'none','subtitle_enabled':False,'audio_mode':'instrumental','encoder':'libx264'})
        wav = self.root / 'timing.wav'
        subprocess.run([FFMPEG,'-v','error','-y','-i',result['output_path'],'-vn','-ar','8000','-ac','1','-c:a','pcm_s16le',str(wav)],
                       check=True,timeout=20,capture_output=True)
        import struct
        with wave.open(str(wav),'rb') as audio:
            samples=struct.unpack('<'+'h'*audio.getnframes(),audio.readframes(audio.getnframes()))
        first_loud=next(index for index,value in enumerate(samples) if abs(value)>200)
        self.assertGreater(first_loud/8000,.85)
        self.assertLess(first_loud/8000,1.15)

    def test_cancelled_render_preserves_preexisting_output_and_source(self):
        cancelled = threading.Event(); cancelled.set()
        previous = self.root / 'previous.mp4'; previous.write_bytes(b'previous')
        with self.assertRaises(RuntimeError):
            renderer.execute('c'*32, self.project, normalize({}, inherited_crop=self.project['crop_box']), 'render',
                             ffmpeg=FFMPEG, ffprobe=FFPROBE, fonts_dir=str(ROOT/'static/fonts'), cancel=cancelled,
                             update=lambda **values: None)
        self.assertEqual(previous.read_bytes(), b'previous')
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertFalse((self.root / '.vietsub-editor' / ('c'*32)).exists())


if __name__ == '__main__':
    unittest.main()
