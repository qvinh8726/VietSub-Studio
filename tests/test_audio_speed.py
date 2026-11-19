"""Clip speed/pitch regression: real FFmpeg, offline synthetic speech only."""
from array import array
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import wave

from vietsub import manifest
from vietsub.editor import capcut_tts, process, providers, renderer, tts
from vietsub.editor.audio import audio_effects
from vietsub.editor.options import normalize
from vietsub.editor.subtitles import write_ass

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")


def tone(path, duration=4, frequency=220, rate=24000):
    samples = array("h", (round(8000 * math.sin(2 * math.pi * frequency * i / rate))
                           for i in range(round(duration * rate))))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes(samples.tobytes())


def samples(path):
    with wave.open(str(path), "rb") as audio:
        rate, channels = audio.getframerate(), audio.getnchannels()
        values = array("h", audio.readframes(audio.getnframes()))
    if sys.byteorder != "little":
        values.byteswap()
    return values[::channels], rate


def frequency(path, start=.15, end=.85):
    values, rate = samples(path)
    values = values[round(start * rate):round(end * rate)]
    return sum(a <= 0 < b for a, b in zip(values, values[1:])) / (len(values) / rate)


class SpeedOptionsAndCacheTests(unittest.TestCase):
    def test_defaults_legacy_values_and_strict_bounds(self):
        defaults = normalize()
        self.assertEqual((defaults["speed"], defaults["change_audio_pitch"], defaults["pitch_semitones"]), (1, False, 0))
        for speed in (.5, 2):
            for pitch in (-12, 12):
                value = normalize({"speed": speed, "pitch_semitones": pitch, "change_audio_pitch": True})
                self.assertEqual(normalize({}, base=value), value)
        old = normalize({}, base={"tts_speed": 1.2, "tts_pitch": 4})
        self.assertEqual((old["speed"], old["pitch_semitones"], old["tts_speed"], old["tts_pitch"]), (1, 0, 1.2, 4))
        for key, values in (("speed", (0, .49, 2.01, "1", True, None, float("nan"), 10**400)),
                            ("pitch_semitones", (-13, 13, "0", True, float("inf"))),
                            ("change_audio_pitch", (0, 1, "false", None))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    normalize({key: value})

    def test_legacy_project_options_survive_manifest_adaptation(self):
        with tempfile.TemporaryDirectory() as directory:
            project = {"project_dir": directory, "project_name": "clip", "video": str(Path(directory) / "clip.mp4"),
                       "editor_options": {"speed": 1.5, "change_audio_pitch": True, "pitch_semitones": -3}}
            target, value = manifest.save_project(project)
            self.assertEqual(value["editor_options"], project["editor_options"])
            self.assertEqual(manifest.snapshot({key: val for key, val in project.items() if key != "editor_options"})["editor_options"], project["editor_options"])

    def test_subtitle_timestamps_scale_without_modifying_srt(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source.srt", Path(directory) / "target.ass"
            original = "1\n00:00:01,000 --> 00:00:03,000\nXin chào\n"
            source.write_text(original, encoding="utf-8")
            for speed, expected in ((2, "0:00:00.50,0:00:01.50"), (.5, "0:00:02.00,0:00:06.00")):
                write_ass(source, target, 320, 180, normalize({"speed": speed}))
                self.assertIn(expected, target.read_text(encoding="utf-8"))
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_probe_cache_uses_path_mtime_size_and_returns_copies(self):
        payload = json.dumps({"streams": [{"codec_type": "video", "width": 320, "height": 180}], "format": {"duration": "4"}})
        with tempfile.TemporaryDirectory() as directory, patch.object(process, "capture", return_value=payload) as capture:
            path = Path(directory) / "source.mp4"
            path.write_bytes(b"a")
            first = renderer.probe_media(path, "mock-ffprobe")
            first["duration"] = 999
            self.assertEqual(renderer.probe_media(path, "mock-ffprobe")["duration"], 4)
            self.assertEqual(capture.call_count, 1)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            renderer.probe_media(path, "mock-ffprobe")
            self.assertEqual(capture.call_count, 2)
            path.write_bytes(b"larger replacement")
            renderer.probe_media(path, "mock-ffprobe")
            self.assertEqual(capture.call_count, 3)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                renderer.probe_media(path, "mock-ffprobe")

    def test_failed_probe_does_not_cache_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.mp4"; path.touch()
            with patch.object(process, "capture", side_effect=RuntimeError("bad media")) as capture:
                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        renderer.probe_media(path, "mock-ffprobe")
                self.assertEqual(capture.call_count, 2)

    def test_encoder_cache_lasts_session_and_refresh_rechecks(self):
        binary = "mock-ffmpeg-" + uuid.uuid4().hex
        with patch.object(process, "capture", return_value="") as capture:
            first = renderer.detect_encoders(binary)
            first["libx264"] = False
            self.assertTrue(renderer.detect_encoders(binary)["libx264"])
            self.assertEqual(capture.call_count, 2)
            renderer.detect_encoders(binary, refresh=True)
            self.assertEqual(capture.call_count, 4)

    def test_capcut_catalog_cache_does_not_leak_mutations(self):
        capcut_tts._catalog_voices.cache_clear()
        first = capcut_tts.catalog_voices("vi_VN")
        first[0]["name"] = "tampered"
        second = capcut_tts.catalog_voices("vi-vn")
        self.assertNotEqual(second[0]["name"], "tampered")
        self.assertEqual(capcut_tts._catalog_voices.cache_info().hits, 1)


class SyntheticSpeech:
    def __init__(self):
        self.calls = []

    def voices(self, language="vi"):
        return []

    def synthesize(self, text, voice, output):
        self.calls.append(text)
        tone(output, duration=1, frequency=660)
        return {"path": str(output), "provider": "capcut_internal", "voice": voice, "warnings": []}


@unittest.skipUnless(FFMPEG and FFPROBE, "Real FFmpeg and FFprobe required")
class AudioSpeedIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vietsub-speed-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.audio = self.root / "tone.wav"
        tone(self.audio)
        self.source = self.root / "clip.mp4"
        self.run_ffmpeg("-f", "lavfi", "-i", "color=c=black:s=160x90:r=25:d=4", "-i", str(self.audio),
                        "-c:v", "libx264", "-c:a", "aac", "-t", "4", str(self.source))
        self.srt = self.root / "clip.srt"
        self.srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nXin chào\n", encoding="utf-8")
        self.project = {"project_dir": str(self.root), "assets": {"video": str(self.source), "translated_srt": str(self.srt)}}
        self.service = tts.TTSService(FFMPEG, FFPROBE, self.root / "speech-cache")
        self.speech = SyntheticSpeech()
        self.service.register("capcut_internal", self.speech)

    def run_ffmpeg(self, *args):
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True, timeout=30, capture_output=True)

    def execute(self, action, options, cancel=None):
        identifier = uuid.uuid4().hex
        result = renderer.execute(identifier, self.project, normalize(options), action, ffmpeg=FFMPEG, ffprobe=FFPROBE,
                                  fonts_dir=None, cancel=cancel or threading.Event(), update=lambda **values: None)
        self.assertFalse((self.root / ".vietsub-editor" / identifier).exists())
        return result

    def test_real_filters_speed_toggle_and_semitone_extremes(self):
        for speed in (.5, 1, 2):
            for linked in (False, True):
                for pitch in (-12, 0, 12):
                    with self.subTest(speed=speed, linked=linked, pitch=pitch):
                        output = self.root / "effect.wav"
                        opts = normalize({"speed": speed, "change_audio_pitch": linked, "pitch_semitones": pitch})
                        self.run_ffmpeg("-i", str(self.audio), "-af", audio_effects(opts), "-c:a", "pcm_s16le", str(output))
                        values, rate = samples(output)
                        self.assertAlmostEqual(len(values) / rate, 4 / speed, delta=.15)
                        expected = 220 * (speed if linked else 1) * 2 ** (pitch / 12)
                        self.assertAlmostEqual(frequency(output), expected, delta=max(4, expected * .025))

    def test_render_original_audio_video_and_subtitles_follow_speed(self):
        for speed in (.5, 2):
            with self.subTest(speed=speed):
                result = self.execute("render", {"speed": speed, "encoder": "libx264", "font_size": 12})
                metadata = renderer.probe_media(result["output_path"], FFPROBE)
                self.assertAlmostEqual(metadata["duration"], 4 / speed, delta=.08)
                output = self.root / "rendered.wav"
                self.run_ffmpeg("-i", result["output_path"], "-vn", "-c:a", "pcm_s16le", str(output))
                self.assertAlmostEqual(frequency(output), 220, delta=5)
                # Subtitle at source [1,3] must burn at output [1/speed,3/speed].
                def bright_frame(at):
                    raw = subprocess.check_output([FFMPEG, "-v", "error", "-ss", str(at), "-i", result["output_path"],
                                                   "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], timeout=15)
                    return max(raw)
                self.assertLess(bright_frame(.4 / speed), 40)
                self.assertGreater(bright_frame(2 / speed), 100)
                self.assertLess(bright_frame(3.5 / speed), 40)

    def test_extraction_applies_global_pitch_and_speed(self):
        result = self.execute("extract_audio", {"speed": 2, "change_audio_pitch": True, "pitch_semitones": -12})
        self.assertAlmostEqual(renderer.probe_media(result["output_path"], FFPROBE)["duration"], 2, delta=.02)
        self.assertAlmostEqual(frequency(result["output_path"]), 220, delta=5)

    def test_tts_export_and_render_align_and_shift_once(self):
        options = {"speed": 2, "pitch_semitones": -12, "tts_enabled": True, "tts_voice": "fixture",
                   "audio_mode": "mute", "subtitle_enabled": False, "encoder": "libx264"}
        with patch.object(providers, "service", return_value=self.service):
            for action in ("tts", "render"):
                with self.subTest(action=action):
                    result = self.execute(action, options)
                    voice = result["assets"]["tts_audio"]
                    self.assertAlmostEqual(renderer.probe_media(voice, FFPROBE)["duration"], 2, delta=.02)
                    values, rate = samples(voice)
                    first = next(i for i, value in enumerate(values) if abs(value) > 300)
                    self.assertAlmostEqual(first / rate, .5, delta=.07)
                    self.assertAlmostEqual(frequency(voice, .6, .9), 330, delta=8)
                    if action == "render":
                        mixed = self.root / "mixed.wav"
                        self.run_ffmpeg("-i", result["output_path"], "-vn", "-c:a", "pcm_s16le", str(mixed))
                        self.assertAlmostEqual(frequency(mixed, .6, .9), 330, delta=8)

    def test_render_mix_transforms_original_and_tts_together(self):
        with patch.object(providers, "service", return_value=self.service):
            result = self.execute("render", {"speed": 2, "pitch_semitones": 12, "tts_enabled": True,
                                            "tts_voice": "fixture", "subtitle_enabled": False, "encoder": "libx264"})
        output = self.root / "mixed-both.wav"
        self.run_ffmpeg("-i", result["output_path"], "-vn", "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le", str(output))
        values, rate = samples(output)
        values = values[round(.6 * rate):round(.9 * rate)]
        def strength(hz):
            real = sum(value * math.cos(2 * math.pi * hz * i / rate) for i, value in enumerate(values))
            imag = sum(value * math.sin(2 * math.pi * hz * i / rate) for i, value in enumerate(values))
            return math.hypot(real, imag)
        self.assertGreater(strength(440), strength(220) * 8)
        self.assertGreater(strength(1320), strength(660) * 8)
        self.assertAlmostEqual(renderer.probe_media(result["output_path"], FFPROBE)["duration"], 2, delta=.08)

    def test_original_delayed_audio_start_scales_with_speed(self):
        delayed = self.root / "delayed.mp4"
        self.run_ffmpeg("-i", str(self.source), "-itsoffset", "1", "-i", str(self.audio),
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-t", "4", str(delayed))
        self.project["assets"]["video"] = str(delayed)
        for speed in (.5, 2):
            result = self.execute("extract_audio", {"speed": speed})
            values, rate = samples(result["output_path"])
            first = next(i for i, value in enumerate(values) if abs(value) > 300)
            self.assertAlmostEqual(first / rate, 1 / speed, delta=.08)
            self.assertAlmostEqual(len(values) / rate, 4 / speed, delta=.02)

    def test_preview_original_is_three_seconds_even_with_short_source(self):
        result = self.execute("audio_preview", {"speed": 2, "change_audio_pitch": True, "audio_mode": "instrumental"})
        self.assertEqual((result["preview_source"], result["duration"], result["output_kind"]), ("original", 3, "audio"))
        values, rate = samples(result["output_path"])
        self.assertEqual(len(values), 3 * rate)
        self.assertAlmostEqual(frequency(result["output_path"]), 440, delta=8)
        self.assertFalse(any(values[round(2.1 * rate):]))
        self.assertEqual(result["assets"], {})

    def test_preview_tts_synthesizes_only_bounded_first_cue_and_preserves_source(self):
        self.srt.write_text("1\n00:01:00,000 --> 00:01:05,000\n" + "Xin chào " * 100 +
                            "\n\n2\n00:02:00,000 --> 00:02:01,000\nNot synthesized\n", encoding="utf-8")
        before = self.source.read_bytes()
        with patch.object(providers, "service", return_value=self.service):
            result = self.execute("audio_preview", {"speed": .5, "pitch_semitones": 12, "tts_enabled": True, "tts_voice": "fixture"})
        self.assertEqual(result["preview_source"], "tts")
        self.assertEqual(len(self.speech.calls), 1)
        self.assertLessEqual(len(self.speech.calls[0]), 250)
        self.assertNotIn("Not synthesized", self.speech.calls[0])
        values, rate = samples(result["output_path"])
        self.assertEqual(len(values), 3 * rate)
        self.assertAlmostEqual(frequency(result["output_path"]), 1320, delta=20)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertIsNone(self.service._deadline)

    def test_preview_missing_audio_fails_clearly_without_output(self):
        silent = self.root / "silent.mp4"
        self.run_ffmpeg("-i", str(self.source), "-an", "-c:v", "copy", str(silent))
        self.project["assets"]["video"] = str(silent)
        with self.assertRaisesRegex(ValueError, "không có âm thanh"):
            self.execute("audio_preview", {})
        self.assertFalse((self.root / ".vietsub-editor-previews").exists())
        self.assertEqual(list((self.root / ".vietsub-editor").iterdir()), [])

    def test_preview_cancel_during_processing_cleans_only_task_work(self):
        before = self.source.read_bytes()
        cancel = threading.Event()
        original = process.run
        def cancelled(command, **kwargs):
            cancel.set()
            return original(command, **kwargs)
        with patch.object(process, "run", side_effect=cancelled), self.assertRaises(process.EditorCancelled):
            self.execute("audio_preview", {}, cancel)
        self.assertEqual(list((self.root / ".vietsub-editor").iterdir()), [])
        self.assertFalse((self.root / ".vietsub-editor-previews").exists())
        self.assertEqual(self.source.read_bytes(), before)

    def test_old_cache_success_notices_removed_but_real_warnings_remain(self):
        self.service.synthesize("cache text", "fixture", self.root / "one.wav", allow_fallback=False)
        metadata = next((self.root / "speech-cache").glob("*.json"))
        value = json.loads(metadata.read_text(encoding="utf-8"))
        value["warnings"] = [*tts._SUCCESS_NOTICES, "Actual provider warning"]
        metadata.write_text(json.dumps(value), encoding="utf-8")
        result = self.service.synthesize("cache text", "fixture", self.root / "two.wav", allow_fallback=False)
        self.assertEqual(result["warnings"], ["Actual provider warning"])
        self.assertEqual(len(self.speech.calls), 1)


if __name__ == "__main__":
    unittest.main()
