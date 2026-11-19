"""Desktop Draft timing, prepared audio semantics and metadata cache regressions."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from vietsub import capcut_draft
from vietsub.editor.options import normalize
from vietsub.media_cache import cached_media


class DraftTests(unittest.TestCase):
    def test_native_speed_and_tone_flags_roundtrip_all_presets(self):
        metadata = {'duration': 12, 'width': 1920, 'height': 1080, 'has_audio': True}
        for speed in (.5, 1, 1.5, 2):
            for tone in (False, True):
                for pitch in (-2, 0, 2, 4):
                    options = normalize({'speed': speed, 'change_audio_pitch': tone, 'pitch_semitones': pitch})
                    draft = capcut_draft.document('Sample', 'prepared.mp4', metadata, options)
                    segment = draft['tracks'][0]['segments'][0]
                    self.assertEqual(draft['duration'], round(12_000_000/speed))
                    self.assertEqual(segment['source_timerange']['duration'], 12_000_000)
                    self.assertEqual(segment['target_timerange']['duration'], draft['duration'])
                    self.assertIs(segment['is_tone_modify'], tone)
                    self.assertEqual(segment['speed'], speed)
                    self.assertEqual(draft['materials']['speeds'][0]['speed'], speed)
                    self.assertEqual(draft['vietsub']['pitch_semitones_baked'], pitch)

    def test_export_bakes_pitch_once_then_preserves_native_speed_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.mp4'; source.write_bytes(b'original')
            project = {'project_dir': temp, 'project_name': 'Tiếng Việt', 'project_id': 'test',
                       'assets': {'video': str(source)}, 'crop_box': None}
            options = normalize({'speed': 2, 'change_audio_pitch': True, 'pitch_semitones': 4})
            seen = []
            def render(identifier, prepared, values, action, **kwargs):
                seen.append(values)
                target = Path(prepared['project_dir'])/'prepared.mp4'; target.write_bytes(b'render')
                return {'output_path': str(target), 'warnings': []}
            metadata = {'duration': 6, 'width': 640, 'height': 360, 'has_audio': True}
            with patch.object(capcut_draft.renderer, 'execute', side_effect=render), patch.object(capcut_draft.renderer, 'probe_media', return_value=metadata):
                result = capcut_draft.execute('abc', project, options, ffmpeg='ffmpeg', ffprobe='ffprobe',
                    fonts_dir='fonts', cancel=threading.Event(), update=lambda **kw: None)
            self.assertEqual(seen[0]['speed'], 1)
            self.assertIs(seen[0]['change_audio_pitch'], False)
            self.assertEqual(seen[0]['pitch_semitones'], 4)
            with zipfile.ZipFile(result['output_path']) as bundle:
                draft = json.loads(bundle.read('CapCut-abc/draft_content.json'))
                saved = json.loads(bundle.read('CapCut-abc/project.json'))
                self.assertEqual(saved['editor_options'], options)
                self.assertEqual(draft['duration'], 3_000_000)
            self.assertEqual(source.read_bytes(), b'original')
            self.assertEqual(result['output_kind'], 'archive')

    def test_cancel_removes_only_private_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source.mp4'; source.write_bytes(b'original')
            cancel = threading.Event(); cancel.set()
            with self.assertRaises(capcut_draft.process.EditorCancelled):
                capcut_draft.execute('cancelled', {'project_dir': temp}, normalize({}),
                    ffmpeg='ffmpeg', ffprobe='ffprobe', fonts_dir='fonts', cancel=cancel, update=lambda **kw: None)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['source.mp4'])


class MediaCacheTests(unittest.TestCase):
    def test_same_path_hits_and_replacement_invalidates_and_results_are_copies(self):
        calls = []
        @cached_media(maxsize=2)
        def probe(path):
            calls.append(path)
            return {'duration': Path(path).stat().st_size}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'video'; path.write_bytes(b'1')
            probe(path)['duration'] = 99
            self.assertEqual(probe(path), {'duration': 1}); self.assertEqual(len(calls), 1)
            path.write_bytes(b'123')
            self.assertEqual(probe(path), {'duration': 3}); self.assertEqual(len(calls), 2)
            probe.cache_clear(); probe(path); self.assertEqual(len(calls), 3)

    def test_failure_is_not_cached(self):
        calls = []
        @cached_media()
        def probe(path):
            calls.append(path)
            return None
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'video'; path.touch()
            probe(path); probe(path)
            self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
