"""TTS-only pitch contract, with legacy payloads kept unchanged.

Real FFmpeg fixtures use a 220 Hz background and a 660 Hz synthetic voice;
no network, provider account or separation model is needed.
"""
import json
import math
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import zipfile

from tests import test_audio_speed as fixtures
from vietsub import capcut_draft
from vietsub.editor import providers, renderer, separation
from vietsub.editor.audio import audio_effects, track_audio_effects
from vietsub.editor.options import normalize


class PitchOptionsTests(unittest.TestCase):
    def test_fresh_options_opt_in_and_legacy_options_roundtrip_without_migration(self):
        fresh = normalize()
        self.assertIs(fresh['tts_change_pitch'], False)
        self.assertEqual(normalize(fresh), fresh)
        self.assertEqual(normalize({}, base=fresh), fresh)
        old = normalize({'speed': 1.5, 'change_audio_pitch': True, 'pitch_semitones': 4})
        self.assertNotIn('tts_change_pitch', old)
        self.assertEqual(normalize(old), old)
        self.assertEqual(normalize({}, base=old), old)
        migrated = normalize({'tts_change_pitch': True}, base=old)
        self.assertIs(migrated['change_audio_pitch'], True)
        self.assertEqual(migrated['pitch_semitones'], 4)
        self.assertEqual(normalize({}, base=migrated), migrated)

    def test_either_alias_can_update_scoped_saved_options(self):
        options = normalize({'tts_change_pitch': True, 'pitch_semitones': -2})
        for update in ({'tts_change_pitch': False}, {'change_audio_pitch': False}):
            result = normalize(update, base=options)
            self.assertIs(result['tts_change_pitch'], False)
            self.assertIs(result['change_audio_pitch'], False)
            self.assertEqual(result['pitch_semitones'], -2)

    def test_alias_rejects_non_booleans_and_conflicting_values(self):
        for invalid in (None, 0, 1, 'false', [], {}):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                normalize({'tts_change_pitch': invalid})
        for value in (True, False):
            with self.assertRaisesRegex(ValueError, 'khớp'):
                normalize({'tts_change_pitch': value, 'change_audio_pitch': not value})
        with self.assertRaises(ValueError):
            normalize({}, base={'tts_change_pitch': 'true'})

    def test_background_uses_tempo_only_and_public_helper_stays_legacy(self):
        options = normalize({'speed': 2, 'tts_change_pitch': True, 'pitch_semitones': 12})
        background = track_audio_effects(options, track='background')
        self.assertIn('atempo=2', background)
        self.assertNotIn('asetrate', background)
        self.assertIn('asetrate=48000*4', track_audio_effects(options, track='tts'))
        legacy = {key: value for key, value in options.items() if key != 'tts_change_pitch'}
        self.assertEqual(track_audio_effects(legacy, track='background'), audio_effects(legacy))
        self.assertEqual(track_audio_effects(legacy, track='tts'), audio_effects(legacy))
        with self.assertRaises(ValueError):
            track_audio_effects(options, track='video')

    def test_voice_pitch_graph_precedes_mix_and_background_is_not_shifted(self):
        for ducking in (True, False):
            options = normalize({'speed': 2, 'tts_change_pitch': True, 'pitch_semitones': 12,
                                 'ducking_enabled': ducking})
            filters, label = renderer._audio_graph(True, None, 1, options, 4)
            self.assertEqual(label, '[aout]')
            self.assertIn('[1:a:0]', filters[0])
            self.assertIn('asetrate=48000*4', filters[0])
            self.assertNotIn('asetrate', ';'.join(filters[1:]))
            self.assertIn('[source_mix]', filters[-1])
            self.assertIn('atempo=2', filters[-1])

    def test_draft_native_speed_preserves_background_tone(self):
        metadata = {'duration': 4, 'width': 160, 'height': 90, 'has_audio': True}
        for speed in (.5, 1, 2):
            for linked in (False, True):
                options = normalize({'speed': speed, 'tts_change_pitch': linked, 'pitch_semitones': 12})
                draft = capcut_draft.document('Fixture', 'prepared.mp4', metadata, options)
                segment = draft['tracks'][0]['segments'][0]
                self.assertIs(segment['is_tone_modify'], False)
                self.assertEqual(segment['speed'], speed)
                self.assertEqual(draft['duration'], round(4_000_000 / speed))
                self.assertEqual(draft['vietsub']['pitch_scope'], 'tts')
                self.assertEqual(draft['vietsub']['tts_speed_pitch_baked'], speed if linked else 1)
                self.assertEqual(draft['vietsub']['editor_options'], options)


class PitchAPITests(unittest.TestCase):
    def setUp(self):
        from tests import test_editor as api_fixtures
        api_fixtures.EditorAPITests.setUp(self)

    def test_existing_project_route_saves_and_reloads_alias_without_changing_shape(self):
        route = '/api/editor/projects/one'
        initial = self.client.get(route).get_json()
        self.assertIs(initial['options']['tts_change_pitch'], False)
        legacy = self.client.post(route, json={'options': {
            'change_audio_pitch': True, 'pitch_semitones': 4, 'speed': 1.5}})
        self.assertEqual(legacy.status_code, 200)
        self.assertNotIn('tts_change_pitch', legacy.get_json()['options'])
        migrated = self.client.post(route, json={'options': {'tts_change_pitch': True}})
        self.assertEqual(migrated.status_code, 200, migrated.get_json())
        saved = migrated.get_json()
        self.assertEqual(set(saved), set(initial))
        self.assertIs(saved['options']['tts_change_pitch'], True)
        self.assertIs(saved['options']['change_audio_pitch'], True)
        self.assertEqual(saved['options']['pitch_semitones'], 4)
        self.assertEqual(self.client.get(route).get_json()['options'], saved['options'])
        changed = self.client.post(route, json={'options': {'change_audio_pitch': False}})
        self.assertIs(changed.get_json()['options']['tts_change_pitch'], False)

    def test_invalid_or_conflicting_alias_returns_400_without_mutating_saved_options(self):
        route = '/api/editor/projects/one'
        before = self.client.get(route).get_json()['options']
        for values in ({'tts_change_pitch': 'true'}, {'tts_change_pitch': 1},
                       {'tts_change_pitch': True, 'change_audio_pitch': False}):
            response = self.client.post(route, json={'options': values})
            self.assertEqual(response.status_code, 400, response.get_json())
            self.assertEqual(self.client.get(route).get_json()['options'], before)


@unittest.skipUnless(fixtures.FFMPEG and fixtures.FFPROBE, 'Real FFmpeg and FFprobe required')
class PitchScopeIntegrationTests(unittest.TestCase):
    # Reuse the existing deterministic fixture without inheriting/rerunning its tests.
    setUp = fixtures.AudioSpeedIntegrationTests.setUp
    run_ffmpeg = fixtures.AudioSpeedIntegrationTests.run_ffmpeg
    execute = fixtures.AudioSpeedIntegrationTests.execute

    def voice_options(self, **changes):
        return {'tts_change_pitch': False, 'speed': 2, 'pitch_semitones': 12,
                'tts_enabled': True, 'tts_voice': 'fixture', 'subtitle_enabled': False,
                'encoder': 'libx264', **changes}

    def assert_spectrum(self, path, expected, absent, start=.6, end=.9):
        output = self.root / 'analysis.wav'
        self.run_ffmpeg('-i', str(path), '-vn', '-ar', '12000', '-ac', '1', '-c:a', 'pcm_s16le', str(output))
        values, rate = fixtures.samples(output)
        values = values[round(start * rate):round(end * rate)]
        def strength(hz):
            real = sum(value * math.cos(2 * math.pi * hz * i / rate) for i, value in enumerate(values))
            imag = sum(value * math.sin(2 * math.pi * hz * i / rate) for i, value in enumerate(values))
            return math.hypot(real, imag)
        for present, missing in zip(expected, absent):
            self.assertGreater(strength(present), strength(missing) * 8, f'{present} Hz vs {missing} Hz')

    def test_render_keeps_original_and_instrumental_tone_with_voice_only_pitch(self):
        for mode, ducking in (('original', True), ('original', False), ('instrumental', True), ('instrumental', False)):
            with self.subTest(mode=mode, ducking=ducking), \
                    patch.object(providers, 'service', return_value=self.service), \
                    patch.object(separation, 'separate', return_value={'instrumental': str(self.audio)}):
                result = self.execute('render', self.voice_options(audio_mode=mode, ducking_enabled=ducking))
                # Measure the bed before speech: ducking intentionally suppresses
                # it under the voice and AAC leakage can dominate a tiny bed.
                self.assert_spectrum(result['output_path'], (220,), (440,), start=.1, end=.4)
                self.assert_spectrum(result['output_path'], (1320,), (660,))
                if not ducking:
                    self.assert_spectrum(result['output_path'], (220, 1320), (440, 660))
                self.assertAlmostEqual(renderer.probe_media(result['output_path'], fixtures.FFPROBE)['duration'], 2, delta=.08)
                self.assertAlmostEqual(fixtures.frequency(result['assets']['tts_audio'], .6, .9), 1320, delta=15)

    def test_disabled_tts_and_extraction_ignore_voice_pitch(self):
        for action in ('render', 'extract_audio'):
            options = self.voice_options(tts_enabled=False, tts_change_pitch=True)
            with self.subTest(action=action), patch.object(providers, 'service') as create:
                result = self.execute(action, options)
                create.assert_not_called()
            output = self.root / 'background.wav'
            self.run_ffmpeg('-i', result['output_path'], '-vn', '-c:a', 'pcm_s16le', str(output))
            self.assertAlmostEqual(fixtures.frequency(output), 220, delta=5)
            self.assertAlmostEqual(renderer.probe_media(result['output_path'], fixtures.FFPROBE)['duration'], 2, delta=.08)

    def test_original_preview_preserves_pitch_and_pads_to_three_seconds(self):
        for speed in (.5, 2):
            with self.subTest(speed=speed):
                result = self.execute('audio_preview', self.voice_options(tts_enabled=False, speed=speed, tts_change_pitch=True))
                self.assertEqual(result['preview_source'], 'original')
                values, rate = fixtures.samples(result['output_path'])
                self.assertEqual(len(values), 3 * rate)
                self.assertAlmostEqual(fixtures.frequency(result['output_path']), 220, delta=5)
                self.assertEqual(result['assets'], {})
                if speed == 2:
                    self.assertFalse(any(values[round(2.1 * rate):]))

    def test_tts_preview_uses_one_bounded_utterance_and_voice_pitch(self):
        self.srt.write_text('1\n00:01:00,000 --> 00:01:05,000\n' + 'Xin chào ' * 100 +
                            '\n\n2\n00:02:00,000 --> 00:02:01,000\nDo not synthesize\n', encoding='utf-8')
        source_before, srt_before = self.source.read_bytes(), self.srt.read_bytes()
        with patch.object(providers, 'service', return_value=self.service), \
                patch.object(self.service, 'preview', wraps=self.service.preview) as preview, \
                patch.object(self.service, 'synthesize_srt', side_effect=AssertionError('preview must not synthesize the SRT')):
            result = self.execute('audio_preview', self.voice_options(speed=.5, tts_change_pitch=True))
        self.assertEqual(result['preview_source'], 'tts')
        preview.assert_called_once()
        self.assertEqual(len(self.speech.calls), 1)
        self.assertLessEqual(len(self.speech.calls[0]), 250)
        self.assertNotIn('Do not synthesize', self.speech.calls[0])
        values, rate = fixtures.samples(result['output_path'])
        self.assertEqual(len(values), 3 * rate)
        self.assertAlmostEqual(fixtures.frequency(result['output_path']), 660, delta=12)
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertEqual(self.srt.read_bytes(), srt_before)

    def test_tts_export_and_render_share_exact_pitch_and_alignment(self):
        for linked, expected in ((False, 330), (True, 660)):
            options = self.voice_options(tts_change_pitch=linked, pitch_semitones=-12, audio_mode='mute')
            with patch.object(providers, 'service', return_value=self.service):
                for action in ('tts', 'render'):
                    with self.subTest(linked=linked, action=action):
                        result = self.execute(action, options)
                        voice = result['assets']['tts_audio']
                        values, rate = fixtures.samples(voice)
                        first = next(i for i, value in enumerate(values) if abs(value) > 300)
                        self.assertAlmostEqual(first / rate, .5, delta=.07)
                        self.assertAlmostEqual(len(values) / rate, 2, delta=.02)
                        self.assertAlmostEqual(fixtures.frequency(voice, .6, .9), expected, delta=10)
                        if action == 'render':
                            self.assert_spectrum(result['output_path'], (expected,), (expected * 2,))

    def test_project_cache_survives_preview_markup_and_new_draft_directories(self):
        self.srt.write_text('1\n00:00:00,000 --> 00:00:00,500\n...\n\n'
                            '2\n00:00:01,000 --> 00:00:03,000\n<i>Xin chào</i>\n', encoding='utf-8')
        cache_dirs = []
        def create(ffmpeg, ffprobe, cache_dir, **kwargs):
            cache_dirs.append(Path(cache_dir).resolve())
            service = fixtures.tts.TTSService(ffmpeg, ffprobe, cache_dir, cancel=kwargs.get('cancel'))
            service.register('capcut_internal', self.speech)
            return service
        with patch.object(providers, 'service', side_effect=create):
            self.execute('tts', self.voice_options())
            self.execute('audio_preview', self.voice_options(pitch_semitones=-2, tts_pitch=2))
            capcut_draft.execute('cache-draft', self.project, normalize(self.voice_options(pitch_semitones=4)),
                ffmpeg=fixtures.FFMPEG, ffprobe=fixtures.FFPROBE, fonts_dir=None,
                cancel=threading.Event(), update=lambda **kw: None)
        self.assertEqual(self.speech.calls, ['Xin chào'])
        self.assertEqual(cache_dirs, [(self.root / '.vietsub-tts-cache').resolve()] * 3)
        self.assertFalse((self.root / 'CapCut-cache-draft' / '.vietsub-tts-cache').exists())

    def test_draft_bakes_voice_only_at_source_duration_before_native_speed(self):
        # The combined shift is +24 st, beyond either individual option's limit;
        # no public option is widened or clamped to prepare this valid draft.
        options = normalize(self.voice_options(tts_change_pitch=True))
        with patch.object(providers, 'service', return_value=self.service):
            result = capcut_draft.execute('pitch-draft', self.project, options, ffmpeg=fixtures.FFMPEG,
                ffprobe=fixtures.FFPROBE, fonts_dir=None, cancel=threading.Event(), update=lambda **kw: None)
        with zipfile.ZipFile(result['output_path']) as bundle:
            draft = json.loads(bundle.read('CapCut-pitch-draft/draft_content.json'))
            saved = json.loads(bundle.read('CapCut-pitch-draft/project.json'))
            guide = bundle.read('CapCut-pitch-draft/HUONG-DAN.txt').decode('utf-8')
        media = draft['materials']['videos'][0]['path']
        self.assertEqual(saved['editor_options'], options)
        self.assertEqual(saved['pitch_scope'], 'tts')
        self.assertEqual(draft['duration'], 2_000_000)
        self.assertIs(draft['tracks'][0]['segments'][0]['is_tone_modify'], False)
        self.assertAlmostEqual(renderer.probe_media(media, fixtures.FFPROBE)['duration'], 4, delta=.08)
        self.assert_spectrum(media, (220, 2640), (440, 1320), start=1.2, end=1.8)
        playback = self.root / 'capcut-native-speed.wav'
        self.run_ffmpeg('-i', media, '-vn', '-af', 'atempo=2', '-c:a', 'pcm_s16le', str(playback))
        self.assert_spectrum(playback, (220, 2640), (440, 1320))
        self.assertIn('cao độ TTS đã bake', guide)
        self.assertTrue(self.source.is_file())


if __name__ == '__main__':
    unittest.main()
