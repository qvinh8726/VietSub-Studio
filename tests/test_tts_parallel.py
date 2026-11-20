"""Parallel SRT tests use synthetic WAV/real FFmpeg, never online providers."""
from array import array
import copy
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from vietsub.editor import capcut_tts, providers, tts, tts_pipeline
try:
    from .test_editor_tts import wav_bytes, VOICE
except ImportError:
    from test_editor_tts import wav_bytes, VOICE

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")


class ConcurrentProvider:
    concurrent_safe = True

    def __init__(self, provider="capcut_internal", delay=.05, *, throttle=False, fail=False):
        self.provider, self.delay, self.throttle, self.fail = provider, delay, throttle, fail
        self.cancel = None
        self.progress = None
        self.shared = {"active": 0, "peak": 0, "calls": [], "lock": threading.Lock(), "throttled": False}

    def voices(self, language="vi"):
        return []

    def fork(self, *, cancel=None, progress=None):
        clone = copy.copy(self)
        clone.cancel, clone.progress = cancel, progress
        return clone

    def synthesize(self, text, voice, output):
        shared = self.shared
        with shared["lock"]:
            shared["active"] += 1
            shared["peak"] = max(shared["peak"], shared["active"])
            shared["calls"].append((text, time.monotonic()))
            rate_limit = self.throttle and not shared["throttled"]
            if rate_limit:
                shared["throttled"] = True
        try:
            if rate_limit and self.progress:
                self.progress({"stage": "tts_http", "http_status": 429, "provider": self.provider,
                               "message": "CapCut HTTP 429; code=rate_limit"})
            end = time.monotonic() + self.delay
            while time.monotonic() < end:
                capcut_tts.check_cancel(self.cancel)
                if self.cancel:
                    self.cancel.wait(.01)
                else:
                    time.sleep(.01)
            if self.fail:
                raise capcut_tts.TTSTimeout("poll budget reached")
            index = int(text.split()[-1]) if text.split()[-1].isdigit() else 0
            frequency = (int(voice["resource_id"]) * 4 if isinstance(voice, dict) else 400 + index * 100)
            Path(output).write_bytes(b'ID3' + b'\x00' * 25 if getattr(self, 'bad_audio', False) else wav_bytes(.2, frequency))
            return {"path": str(output), "voice": voice["id"] if isinstance(voice, dict) else voice,
                    "provider": self.provider, "warnings": []}
        finally:
            with shared["lock"]:
                shared["active"] -= 1


def srt_text(count, step=400, offset=0):
    def stamp(value):
        seconds, ms = divmod(value, 1000)
        minutes, seconds = divmod(seconds, 60)
        return f"00:{minutes:02}:{seconds:02},{ms:03}"
    return "\n\n".join(f"{i+1}\n{stamp(offset+i*step)} --> {stamp(offset+(i+1)*step)}\nCâu số {i}"
                         for i in range(count))


class ThrottleTests(unittest.TestCase):
    def test_rate_limit_halves_admission_and_recovers_gradually(self):
        now = [10.]
        throttle = tts_pipeline.AdaptiveThrottle(5, clock=lambda: now[0])
        self.assertTrue(throttle.rate_limited())
        self.assertEqual(throttle.limit, 2)
        self.assertFalse(throttle.ready())
        self.assertFalse(throttle.rate_limited())
        now[0] += .5
        self.assertTrue(throttle.ready())
        for _ in range(3):
            throttle.success()
        self.assertEqual(throttle.limit, 3)
        self.assertTrue(throttle.rate_limited())
        self.assertEqual(throttle.limit, 1)

    def test_edge_cue_budget_is_capped_before_external_process(self):
        edge = tts.EdgeProvider(timeout=120)
        edge._available = True
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'voice.wav'
            def process(argv, **kwargs):
                self.assertGreater(kwargs['timeout'], 19)
                self.assertLessEqual(kwargs['timeout'], 20)
                output.write_bytes(b'fixture')
            with patch.object(tts, '_runtime', return_value='fixture-python'), patch.object(tts, '_process', side_effect=process):
                edge.synthesize('Xin chào', 'vi-VN-HoaiMyNeural', output)

    def test_configuration_limits_and_defaults(self):
        service = providers.service("ffmpeg", "ffprobe", "unused")
        self.assertEqual(service.workers, {"capcut_internal": 3, "edge_tts": 5})
        self.assertEqual(service.batch_size, 25)
        for key, value in (("editor_tts_capcut_workers", 0), ("editor_tts_capcut_workers", True),
                           ("editor_tts_edge_workers", 99), ("editor_tts_batch_size", 31)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                providers.service("ffmpeg", "ffprobe", "unused", config={key: value})


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/ffprobe required")
class ParallelTTSTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="parallel-tts-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.events, self.cancel = [], threading.Event()
        self.service = tts.TTSService(FFMPEG, FFPROBE, self.root / "cache", cancel=self.cancel, progress=self.events.append)
        self.provider = ConcurrentProvider()
        self.service.register("capcut_internal", self.provider)
        self.source, self.output = self.root / "input.srt", self.root / "output.wav"
        self.source.write_text(srt_text(7), encoding="utf-8")

    def run_track(self, **kwargs):
        return self.service.synthesize_srt(self.source, self.output, voice=VOICE, **kwargs)

    def test_parallel_completion_order_correct_timeline_and_filter_scripts(self):
        self.service.batch_size = 3
        commands = []
        original = self.service._ffmpeg
        def record(argv):
            commands.append(argv)
            self.assertIn("-filter_complex_script", argv)
            script = Path(argv[argv.index("-filter_complex_script") + 1])
            self.assertTrue(script.is_file())
            return original(argv)
        with patch.object(self.service, "_ffmpeg", side_effect=record):
            result = self.run_track()
        self.assertEqual(self.provider.shared["peak"], 3)
        self.assertEqual(len(self.provider.shared["calls"]), 7)
        self.assertEqual(len(commands), 6, "two FFmpeg processes per batch, never per cue")
        self.assertEqual([cue["index"] for cue in result["cues"]], list(range(1, 8)))
        self.assertEqual(tts._wav_info(self.output)["duration_ms"], 2800)
        done = [e for e in self.events if e["stage"] == "tts_cue_done"]
        self.assertEqual([e["completed"] for e in done], list(range(1, 8)))
        self.assertEqual([e["percent"] for e in self.events], sorted(e["percent"] for e in self.events))
        with wave.open(str(self.output), "rb") as stream:
            for index in range(7):
                stream.setpos(round((index * .4 + .02) * tts.SAMPLE_RATE))
                samples = array("h", stream.readframes(round(.15 * tts.SAMPLE_RATE)))
                frequency = sum(a <= 0 < b for a, b in zip(samples, samples[1:])) / .15
                self.assertAlmostEqual(frequency, 400 + index * 100, delta=20)

    def test_pitch_timeline_and_text_whitespace_reuse_raw_cache_without_network(self):
        self.run_track()
        self.source.write_text(srt_text(7, offset=200).replace("Câu số", "Câu   số"), encoding="utf-8")
        self.run_track(pitch=2, speed=1.2)
        self.assertEqual(len(self.provider.shared["calls"]), 7)
        self.assertEqual(tts._wav_info(self.output)["duration_ms"], 3000)
        self.service.preview("Câu số 0", VOICE, self.root / "preview.wav", pitch=-2)
        self.assertEqual(len(self.provider.shared["calls"]), 7, "preview must reuse the track's raw cue")
        for path in (self.root / "cache").rglob("*.json"):
            self.assertNotIn("Câu", path.read_text(encoding="utf-8"))

    def test_throttle_429_changes_admission_and_completes_without_reset(self):
        self.provider.throttle = True
        self.run_track()
        throttle = [e for e in self.events if e["stage"] == "tts_throttle"]
        self.assertEqual(len(throttle), 1)
        self.assertEqual(throttle[0]["workers"], 1)
        starts = [stamp for _, stamp in self.provider.shared["calls"]]
        self.assertGreaterEqual(starts[3] - starts[0], .35)
        self.assertEqual(self.events[-1]["percent"], 100)

    def test_cancel_preserves_output_and_completed_raw_cues_for_resume(self):
        self.output.write_bytes(b"last-good")
        def progress(event):
            self.events.append(event)
            if event["stage"] == "tts_cue_done" and event["completed"] == 2:
                self.cancel.set()
        self.service.progress = progress
        started = time.monotonic()
        with self.assertRaises(capcut_tts.TTSCancelled):
            self.run_track()
        self.assertLess(time.monotonic() - started, .8)
        self.assertEqual(self.output.read_bytes(), b"last-good")
        self.assertGreaterEqual(len(list((self.root / "cache" / "raw-v1").glob("*.audio"))), 2)
        # Let the cooperative providers unwind before creating a new queue job.
        end = time.monotonic() + 1
        while self.provider.shared["active"] and time.monotonic() < end:
            time.sleep(.01)
        before = len(self.provider.shared["calls"])
        cached = len(list((self.root / "cache" / "raw-v1").glob("*.audio")))
        self.service.cancel = threading.Event()
        self.service.progress = self.events.append
        self.run_track()
        self.assertLessEqual(len(self.provider.shared["calls"]) - before, 7 - cached)
        self.assertEqual(len(list((self.root / "cache" / "raw-v1").glob("*.audio"))), 7)

    def test_parallel_fallback_does_not_retry_capcut_on_remaining_cues(self):
        self.provider.fail = True
        edge = ConcurrentProvider("edge_tts")
        self.service.register("edge_tts", edge)
        result = self.run_track()
        self.assertEqual(result["provider"], "edge_tts")
        self.assertLessEqual(len(self.provider.shared["calls"]), 3)
        self.assertEqual(len(edge.shared["calls"]), 7)
        self.assertTrue(any(e["stage"] == "tts_fallback" for e in self.events))

    def test_edge_uses_five_workers_and_repeated_text_coalesces_network(self):
        edge = ConcurrentProvider("edge_tts", delay=.1)
        self.service.register("edge_tts", edge)
        self.run_track(provider="edge_tts")
        self.assertEqual(edge.shared["peak"], 5)
        self.assertEqual(len(edge.shared["calls"]), 7)
        # A different voice has a distinct cache key, but repeated cues with that
        # voice share one in-flight raw request, even across five worker threads.
        self.source.write_text(srt_text(7).replace("Câu số 1", "Câu số 0").replace("Câu số 2", "Câu số 0"), encoding="utf-8")
        self.service.synthesize_srt(self.source, self.output, voice="vi-VN-NamMinhNeural", provider="edge_tts")
        self.assertEqual(len(edge.shared["calls"]), 12)

    def test_track_deadline_stops_parallel_workers_and_preserves_good_output(self):
        self.output.write_bytes(b"last-good")
        self.provider.delay = 1
        self.service.track_timeout = .06
        started = time.monotonic()
        with self.assertRaises(capcut_tts.TTSTimeout):
            self.run_track()
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(self.output.read_bytes(), b"last-good")
        end = time.monotonic() + 1
        while self.provider.shared["active"] and time.monotonic() < end:
            time.sleep(.01)
        self.assertEqual(self.provider.shared["active"], 0)
        self.assertFalse(list((self.root / "cache" / "raw-v1").glob("*.audio")))

    def test_undecodable_raw_is_evicted_then_corrected_response_can_resume(self):
        self.source.write_text(srt_text(1), encoding='utf-8')
        self.provider.bad_audio = True
        with self.assertRaises(capcut_tts.TTSError):
            self.run_track(allow_fallback=False)
        self.assertFalse(list((self.root / 'cache' / 'raw-v1').glob('*.audio')))
        self.provider.bad_audio = False
        self.run_track(allow_fallback=False)
        self.assertEqual(len(self.provider.shared['calls']), 2)

    def test_batch_decode_failure_falls_back_and_reuses_duplicate_replacement(self):
        self.source.write_text(srt_text(3).replace('Câu số 1', 'Câu số 0').replace('Câu số 2', 'Câu số 0'), encoding='utf-8')
        self.provider.bad_audio = True
        edge = ConcurrentProvider('edge_tts')
        self.service.register('edge_tts', edge)
        result = self.run_track()
        self.assertEqual(result['provider'], 'edge_tts')
        self.assertEqual(len(self.provider.shared['calls']), 1)
        self.assertEqual(len(edge.shared['calls']), 1)
        self.assertTrue(any('decoded' in warning for warning in result['warnings']))

    def test_non_threadsafe_legacy_fallback_is_serialized(self):
        self.provider.fail = True
        edge = ConcurrentProvider('edge_tts')
        class Legacy:
            def voices(self, language='vi'):
                return []

            def synthesize(self, *args):
                return edge.synthesize(*args)
        self.service.register('edge_tts', Legacy())
        self.run_track()
        self.assertEqual(edge.shared['peak'], 1)
        self.assertEqual(len(edge.shared['calls']), 7)

    def test_explicit_voice_resources_have_distinct_raw_and_canonical_cache(self):
        self.source.write_text(srt_text(1), encoding='utf-8')
        for resource in ('100', '200'):
            self.service.synthesize_srt(self.source, self.output,
                voice={'id': 'custom', 'resource_id': resource}, provider='capcut_internal', allow_fallback=False)
            with wave.open(str(self.output), 'rb') as stream:
                samples = array('h', stream.readframes(round(.15 * tts.SAMPLE_RATE)))
                frequency = sum(a <= 0 < b for a, b in zip(samples, samples[1:])) / .15
                self.assertAlmostEqual(frequency, int(resource) * 4, delta=20)
        self.assertEqual(len(self.provider.shared['calls']), 2)
        self.assertEqual(len(list((self.root / 'cache' / 'raw-v1').glob('*.audio'))), 2)

    def test_cache_cleanup_owns_only_hash_named_entries(self):
        self.run_track()
        scratch = self.root / "cache" / "personal.wav"
        scratch.write_bytes(b"keep me")
        self.service.cache_max_entries = 2
        self.service.prune_cache()
        entries = list((self.root / "cache").glob("*.wav")) + list((self.root / "cache" / "raw-v1").glob("*.audio"))
        self.assertEqual(len(entries), 3)
        self.assertEqual(scratch.read_bytes(), b"keep me")


if __name__ == "__main__":
    unittest.main()
