"""Phase2 TTS: synthetic WAV + fake HTTP only; never sends speech to a service."""
from __future__ import annotations

from array import array
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
import wave

from vietsub.editor import alignment, capcut_tts, separation, tts

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
VOICE = "BV421_vivn_streaming"
SRT = "1\n00:00:00,100 --> 00:00:00,500\nXin chào\n\n2\n00:00:00,700 --> 00:00:00,950\nThế giới\n"


def wav_bytes(duration=.2, frequency=440, amplitude=8000):
    stream = io.BytesIO()
    samples = array("h", [round(amplitude * math.sin(2 * math.pi * frequency * index / tts.SAMPLE_RATE))
                          for index in range(round(duration * tts.SAMPLE_RATE))])
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(tts.SAMPLE_RATE)
        output.writeframes(samples.tobytes())
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, data, status=200):
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode("utf-8")
        super().__init__(data)
        self.status = status


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("Unexpected HTTP request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def created():
    return {"ret": 0, "data": {"tasks": [{"id": "task1", "token": "private-task-token", "bind_id": "binding"}]}}


def completed(*, url="https://audio.example.org/speech.mp3", payload=None, status="success"):
    if payload is None:
        payload = {"audio_subtitles": [{"speech_url": url}]}
    return {"ret": 0, "data": {"tasks": [{"status": status, "payload": json.dumps(payload)}]}}


class SyntheticProvider:
    def __init__(self, duration=.2, provider="synthetic"):
        self.duration, self.provider, self.calls = duration, provider, []

    def voices(self, language="vi"):
        return [{"id": "synthetic", "name": "Synthetic test", "language": "vi-VN", "provider": self.provider}]

    def synthesize(self, text, voice, output_path):
        self.calls.append((text, voice, str(output_path)))
        Path(output_path).write_bytes(wav_bytes(self.duration))
        return {"path": str(output_path), "voice": voice, "provider": self.provider, "warnings": []}


class AlignmentTests(unittest.TestCase):
    def test_parse_unicode_multiline_bom_crlf_exact_milliseconds(self):
        value = "\ufeff1\r\n00:00:01,234 --> 00:00:02.567\r\nXin chào\r\nViệt Nam\r\n"
        cue = alignment.parse_srt(value)[0]
        self.assertEqual(cue, {"index": 1, "start_ms": 1234, "end_ms": 2567, "text": "Xin chào\nViệt Nam"})

    def test_rejects_invalid_cues_instead_of_silent_skip(self):
        for raw in ("", "garbage", "1\n00:00:00,000 --> 00:00:00,000\nHi", "1\n00:99:00,000 --> 00:00:01,000\nHi",
                    "0\n00:00:00,000 --> 00:00:01,000\nHi", SRT + "\n" + SRT,
                    "1\n00:00:01,000 --> 00:00:02,000\nHi\n\n2\n00:00:00,000 --> 00:00:01,000\nHo",
                    "1\n00:00:00,000 --> 00:00:01,000\n\x00bad", "1\n00:00:00,000 --> 00:00:01,000 align:start\nHi"):
            with self.subTest(raw=raw), self.assertRaises(alignment.AlignmentError):
                alignment.parse_srt(raw)

    def test_size_count_text_and_duration_bounds(self):
        for options in ({"max_text": 1}, {"max_total_text": 3}, {"max_cues": 1}, {"max_duration_ms": 300}):
            with self.subTest(options=options), self.assertRaises(alignment.AlignmentError):
                alignment.parse_srt(SRT, **options)

    def test_atempo_factors_remain_compatible_and_multiply_correctly(self):
        for speed in (.03125, .1, .5, 1, 2, 3, 64):
            chain = alignment.atempo_chain(speed)
            factors = [float(piece.split("=")[1]) for piece in chain.split(",")]
            self.assertTrue(all(.5 <= factor <= 2 for factor in factors))
            self.assertAlmostEqual(math.prod(factors), speed)
        for value in (0, -1, 65, float("nan"), float("inf"), True):
            with self.assertRaises(alignment.AlignmentError):
                alignment.atempo_chain(value)

    def test_overlap_and_equal_start_never_overlap_speech(self):
        cues = alignment.parse_srt("1\n00:00:00,000 --> 00:00:03,000\nA\n\n2\n00:00:01,000 --> 00:00:04,000\nB\n\n3\n00:00:01,000 --> 00:00:02,000\nC")
        windows = alignment.cue_windows(cues)
        self.assertEqual([cue["window_ms"] for cue in windows], [1000, 0, 1000])
        self.assertTrue(alignment.fit_cue(1000, 0)["skip"])

    def test_fit_modest_speed_and_trim_warning(self):
        fit = alignment.fit_cue(3000, 1000)
        self.assertEqual(fit["speed"], 1.35)
        self.assertTrue(fit["trimmed"])
        self.assertTrue(fit["warnings"])
        self.assertFalse(alignment.fit_cue(500, 1000)["trimmed"])
        self.assertAlmostEqual(alignment.fit_cue(1100, 1000)["speed"], 1.1)

    def test_alignment_graph_uses_adelay_padding_non_normalized_mix(self):
        graph = alignment.build_alignment_filter(alignment.parse_srt(SRT))
        self.assertIn("adelay=100:all=1", graph)
        self.assertIn("adelay=700:all=1", graph)
        self.assertIn("normalize=0", graph)
        self.assertIn("atrim=duration=0.950000[tts]", graph)
        self.assertNotIn("Xin chào", graph)

    def test_ducking_splits_voice_and_validates_labels(self):
        graph = alignment.ducking_filter()
        self.assertIn("asplit=2", graph)
        self.assertIn("sidechaincompress", graph)
        self.assertIn("normalize=0", graph)
        for kwargs in ({"background": "evil;filter"}, {"output_label": "x];movie=/secret"}, {"threshold": 0}, {"ratio": 50}):
            with self.assertRaises(alignment.AlignmentError):
                alignment.ducking_filter(**kwargs)


class CapCutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vietsub-capcut-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def client(self, responses, **kwargs):
        transport = Transport(responses)
        return capcut_tts.CapCutClient(transport=transport, payload_signer=lambda *args: "mock-authorized-sign",
                                      poll_interval=.001, **kwargs), transport

    def test_default_is_honest_offline_and_does_not_guess_signature(self):
        transport = Transport([])
        client = capcut_tts.CapCutClient(transport=transport)
        self.assertFalse(client.availability()["available"])
        self.assertFalse(client.availability()["service_verified"])
        with self.assertRaises(capcut_tts.ProviderUnavailable):
            client.synthesize("Synthetic test", VOICE, self.root / "out.mp3")
        self.assertEqual(transport.requests, [])

    def test_catalog_vietnamese_multilingual_offline_not_entitlement(self):
        client = capcut_tts.CapCutClient(transport=Transport([]))
        self.assertGreaterEqual(len(client.voices("vi")), 20)
        self.assertEqual(client.voices("vi"), client.voices("vi-VN"))
        self.assertTrue(all(voice["catalog_only"] and not voice["service_verified"] for voice in client.voices("vi")))
        self.assertGreaterEqual(len({item["language"] for item in client.voices(None)}), 10)
        self.assertEqual(client.voices("xx"), [])

    def test_exact_observed_request_paths_schema_header_hash_and_xml_escaping(self):
        client, _ = self.client([])
        with patch.object(capcut_tts.time, "time", return_value=123456):
            request = client.build_tts_request('Synthetic < & " >', VOICE)
        self.assertEqual(urlsplit(request.full_url).path, "/lv/v1/common_task/new")
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["region"], ["VN"])
        self.assertEqual(query["aid"], ["359289"])
        body = json.loads(request.data)
        payload = json.loads(body["tasks"][0]["payload"])
        self.assertEqual(body["tasks"][0]["req_key"], "sami_text_to_speech")
        self.assertEqual(body["tasks"][0]["task_version"], "v3")
        self.assertIn("Synthetic &lt; &amp; &quot; &gt;", payload["ssml"])
        self.assertEqual(payload["sign"], "mock-authorized-sign")
        self.assertEqual(payload["audio_format"], "mp3")
        self.assertEqual(request.get_header("X-ss-stub"), hashlib.md5(request.data).hexdigest())
        material = f"9e2c|ask/new|3|8.7.0|123456|{client.device['tdid']}|11ac"
        self.assertEqual(request.get_header("Sign"), hashlib.md5(material.encode()).hexdigest())
        query_request = client.build_query_request("id", "token", "bind")
        self.assertEqual(urlsplit(query_request.full_url).path, "/lv/v1/common_task/query")
        self.assertNotIn("region", parse_qs(urlsplit(query_request.full_url).query))

    def test_mock_success_poll_then_artifact_download_never_leaks_auth(self):
        data = wav_bytes()
        client, transport = self.client([Response(created()), Response(completed(status="queueing")),
                                         Response(completed()), Response(data)], headers={"Cookie": "secret", "Authorization": "Bearer secret"})
        output = self.root / "speech.mp3"
        result = client.synthesize("Synthetic fixture", VOICE, output)
        self.assertEqual(output.read_bytes(), data)
        self.assertEqual(result["provider"], "capcut_internal")
        self.assertEqual(len(transport.requests), 4)
        download = transport.requests[-1][0]
        self.assertIsNone(download.get_header("Cookie"))
        self.assertIsNone(download.get_header("Authorization"))
        self.assertTrue(all(0 < timeout <= client.timeout for _, timeout in transport.requests))

    def test_nonzero_ret_and_invalid_schema_never_fake_success(self):
        for data in ({"ret": 1014, "errmsg": "secret user text"}, [], {"ret": 0, "data": {"tasks": []}},
                     {"ret": 0, "data": {"tasks": [{"id": "id"}]}}):
            client, _ = self.client([Response(data)])
            with self.subTest(data=data), self.assertRaises(capcut_tts.TTSError) as context:
                client.synthesize("Synthetic fixture", VOICE, self.root / "speech.mp3")
            self.assertNotIn("secret user text", str(context.exception))
            self.assertFalse((self.root / "speech.mp3").exists())

    def test_login_challenge_not_retried_or_bypassed(self):
        failure = HTTPError("https://example.org", 403, "secret challenge", {}, None)
        client, transport = self.client([failure])
        with self.assertRaises(capcut_tts.VerificationRequired):
            client.synthesize("Synthetic fixture", VOICE, self.root / "out.mp3")
        self.assertEqual(len(transport.requests), 1)

    def test_no_retry_ambiguous_task_creation(self):
        client, transport = self.client([URLError("failed"), Response(created())])
        with self.assertRaises(capcut_tts.ProviderUnavailable):
            client.synthesize("Synthetic fixture", VOICE, self.root / "out.mp3")
        self.assertEqual(len(transport.requests), 1)

    def test_poll_retry_is_bounded(self):
        client, transport = self.client([Response(created()), URLError("transient"), Response(completed()), Response(wav_bytes())])
        with patch.object(capcut_tts, "bounded_wait"):
            client.synthesize("Synthetic fixture", VOICE, self.root / "out.mp3")
        self.assertEqual(len(transport.requests), 4)

    def test_max_polls_stops_queued_forever(self):
        client, transport = self.client([Response(created()), Response(completed(status="running")),
                                         Response(completed(status="running"))], max_polls=2)
        with self.assertRaises(capcut_tts.TTSTimeout):
            client.synthesize("Synthetic fixture", VOICE, self.root / "out.mp3")
        self.assertEqual(len(transport.requests), 3)

    def test_vid_only_bad_payload_private_url_and_html_download_fail_closed(self):
        responses = [completed(payload={"audio_subtitles": [{"vid": "vid-only"}]}),
                     completed(payload={}), completed(url="http://audio.example.org/audio"),
                     completed(url="https://127.0.0.1/audio"), completed(status="unknown")]
        for response in responses:
            client, transport = self.client([Response(created()), Response(response)])
            with self.subTest(response=response), self.assertRaises(capcut_tts.TTSError):
                client.synthesize("Synthetic fixture", VOICE, self.root / "out.mp3")
            self.assertEqual(len(transport.requests), 2)
        client, _ = self.client([Response(created()), Response(completed()), Response(b"<html>login</html>")])
        with self.assertRaises(capcut_tts.CapCutProtocolError):
            client.synthesize("Synthetic fixture", VOICE, self.root / "out.mp3")

    def test_response_size_limit_and_pre_cancel(self):
        client, _ = self.client([Response(created()), Response(completed()), Response(wav_bytes())], max_audio_bytes=20)
        with self.assertRaises(capcut_tts.CapCutProtocolError):
            client.synthesize("Synthetic fixture", VOICE, self.root / "out.mp3")
        cancel = threading.Event()
        cancel.set()
        client, transport = self.client([], cancel=cancel)
        with self.assertRaises(capcut_tts.TTSCancelled):
            client.synthesize("Synthetic fixture", VOICE, self.root / "out.mp3")
        self.assertFalse(transport.requests)

    def test_unknown_voice_and_bad_limits_rejected_before_network(self):
        client, transport = self.client([])
        with self.assertRaises(ValueError):
            client.synthesize("Synthetic fixture", "unlisted", self.root / "out.mp3")
        self.assertFalse(transport.requests)
        for kwargs in ({"retries": 999}, {"timeout": 0}, {"task_timeout": 1000000}, {"max_polls": 0}):
            with self.assertRaises(ValueError):
                capcut_tts.CapCutClient(**kwargs)

    def test_original_output_survives_atomic_failure(self):
        output = self.root / "out.mp3"
        output.write_bytes(b"original")
        client, _ = self.client([Response(created()), Response(completed()), Response(wav_bytes())])
        with patch("os.replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            client.synthesize("Synthetic fixture", VOICE, output)
        self.assertEqual(output.read_bytes(), b"original")
        self.assertEqual(list(self.root.iterdir()), [output])


class LazyRuntimeTests(unittest.TestCase):
    def test_optional_availability_only_uses_find_spec_external_worker(self):
        with patch.object(tts, "_process", return_value=b'{"installed":false}') as run:
            result = tts.EdgeProvider().availability()
            self.assertFalse(result["available"])
            self.assertIn("find_spec", run.call_args.args[0][2])
            self.assertNotIn("import edge_tts", run.call_args.args[0][2])
        with patch.object(separation, "_process", return_value=b'{"installed":false}') as run:
            self.assertFalse(separation.availability()["available"])
            self.assertIn("find_spec", run.call_args.args[0][2])
            self.assertNotIn("from audio_separator", run.call_args.args[0][2])

    def test_frozen_default_requires_external_python(self):
        with patch.object(sys, "frozen", True, create=True):
            with self.assertRaises(capcut_tts.ProviderUnavailable):
                tts._runtime()
            self.assertEqual(tts._runtime("external-python.exe"), "external-python.exe")

    def test_real_worker_timeout_and_cancel_are_bounded(self):
        start = time.monotonic()
        with self.assertRaises(capcut_tts.TTSTimeout):
            tts._process([sys.executable, "-c", "import time; time.sleep(10)"], timeout=.15)
        self.assertLess(time.monotonic() - start, 4)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(capcut_tts.TTSCancelled):
            tts._process([sys.executable, "-c", "raise Exception('must not start')"], cancel=cancel)

    def test_lazy_imports_in_fresh_python_process(self):
        code = ("import sys; from vietsub.editor import tts,separation; "
                "assert not any(name in sys.modules for name in ('edge_tts','audio_separator','torch','onnxruntime','aiohttp')); "
                "print('lazy imports verified')")
        output = tts._process([sys.executable, "-c", code], timeout=10)
        self.assertIn(b"lazy imports verified", output)

    def test_separation_missing_model_never_returns_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            source.write_bytes(wav_bytes())
            with self.assertRaises(capcut_tts.ProviderUnavailable):
                separation.separate(source, root / "out", model_dir=root)
            self.assertFalse((root / "out" / "instrumental.wav").exists())

    def test_separation_mock_external_result_atomic_publish_and_no_download_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            source.write_bytes(wav_bytes())
            model = root / "model.onnx"
            model.write_bytes(b"model")

            def run(argv, **kwargs):
                config = json.loads(Path(argv[-2]).read_text(encoding="utf-8"))
                self.assertFalse(config["allow_download"])
                instrumental = Path(config["output_dir"]) / "instrumental.wav"
                instrumental.write_bytes(wav_bytes())
                Path(argv[-1]).write_text(json.dumps({"paths": [str(instrumental)]}), encoding="utf-8")
                return b""

            with patch.object(separation, "availability", return_value={"available": True}), patch.object(separation, "_process", side_effect=run):
                result = separation.separate(source, root / "out", model_dir=root, model_filename="model.onnx")
            self.assertEqual(Path(result).name, "instrumental.wav")
            self.assertTrue(Path(result).is_file())
            self.assertEqual(source.read_bytes(), wav_bytes())

    def test_separation_rejects_worker_artifact_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            source.write_bytes(wav_bytes())
            (root / "model.onnx").write_bytes(b"model")

            def run(argv, **kwargs):
                Path(argv[-1]).write_text(json.dumps({"paths": [str(source)]}), encoding="utf-8")
                return b""

            with patch.object(separation, "availability", return_value={"available": True}), patch.object(separation, "_process", side_effect=run):
                with self.assertRaises(capcut_tts.ProviderUnavailable):
                    separation.separate(source, root / "out", model_dir=root, model_filename="model.onnx")
            self.assertTrue(source.exists())


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/ffprobe required for synthetic audio integration")
class TTSIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vietsub-tts-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = tts.TTSService(FFMPEG, FFPROBE, self.root / "cache")
        self.synthetic = SyntheticProvider()
        self.service.register("synthetic", self.synthetic)

    def synthesize(self, output="out.wav", **kwargs):
        return self.service.synthesize("Synthetic fixture", "synthetic", self.root / output,
                                       provider="synthetic", **kwargs)

    def test_constructor_and_catalog_do_not_check_edge_or_network(self):
        with patch.object(tts, "_process", side_effect=AssertionError("no worker")):
            service = tts.TTSService(FFMPEG, FFPROBE, self.root / "unused")
            self.assertGreater(len(service.voices()), 1)
            self.assertFalse(service.availability()["available"])
        self.assertFalse((self.root / "unused").exists())

    def test_synthesis_real_wav_cache_hit_and_no_plain_text_metadata(self):
        result = self.synthesize()
        self.assertEqual(result["provider"], "synthetic")
        self.assertEqual(tts._wav_info(result["path"])["duration_ms"], 200)
        self.synthesize("second.wav")
        self.assertEqual(len(self.synthetic.calls), 1)
        for metadata in (self.root / "cache").glob("*.json"):
            self.assertNotIn("Synthetic fixture", metadata.read_text(encoding="utf-8"))
        self.assertEqual((self.root / "out.wav").read_bytes(), (self.root / "second.wav").read_bytes())

    def test_cache_key_changes_all_semantic_controls(self):
        base = tts.cache_key("fixture", "synthetic", "voice", 1, 0)
        self.assertEqual(base, tts.cache_key("fixture", "synthetic", "voice", 1.0, 0.0))
        values = [tts.cache_key("other", "synthetic", "voice", 1, 0),
                  tts.cache_key("fixture", "edge_tts", "voice", 1, 0),
                  tts.cache_key("fixture", "synthetic", "other", 1, 0),
                  tts.cache_key("fixture", "synthetic", "voice", 1.2, 0),
                  tts.cache_key("fixture", "synthetic", "voice", 1, 2)]
        self.assertNotIn(base, values)
        self.assertEqual(len(set(values)), len(values))

    def test_local_speed_pitch_controls_and_encoded_output(self):
        slow = self.synthesize("slow.wav", speed=.5, pitch=3)
        fast = self.synthesize("fast.wav", speed=2, pitch=-3)
        self.assertGreater(tts._wav_info(slow["path"])["duration_ms"], tts._wav_info(fast["path"])["duration_ms"] * 2)
        encoded = self.synthesize("encoded.mp3")
        self.assertGreater(Path(encoded["path"]).stat().st_size, 0)

    def test_default_capcut_fallback_warns_actual_provider_and_preserves_cache_identity(self):
        edge = SyntheticProvider(provider="edge_tts")
        self.service.register("edge_tts", edge)
        result = self.service.synthesize("Synthetic fixture", VOICE, self.root / "fallback.wav")
        self.assertEqual(result["provider"], "edge_tts")
        self.assertEqual(result["voice"], "vi-VN-HoaiMyNeural")
        self.assertTrue(any("used optional Edge" in warning for warning in result["warnings"]))
        self.assertFalse((self.root / "cache" / (tts.cache_key("Synthetic fixture", "capcut_internal", VOICE) + ".wav")).exists())
        with self.assertRaises(capcut_tts.ProviderUnavailable):
            self.service.synthesize("Synthetic fixture", VOICE, self.root / "strict.wav", allow_fallback=False)
        self.assertFalse((self.root / "strict.wav").exists())

    def test_cancel_never_falls_back_or_replaces_output(self):
        cancel = threading.Event()
        cancel.set()
        self.service.cancel = cancel
        output = self.root / "out.wav"
        output.write_bytes(b"original")
        with self.assertRaises(capcut_tts.TTSCancelled):
            self.synthesize()
        self.assertFalse(self.synthetic.calls)
        self.assertEqual(output.read_bytes(), b"original")

    def test_bad_audio_cannot_masquerade_as_ffmpeg_playlist(self):
        class Bad(SyntheticProvider):
            def synthesize(self, text, voice, output_path):
                Path(output_path).write_bytes(b"#EXTM3U\nhttps://private.example.org/secret\n")
                return {"voice": "synthetic", "warnings": []}
        self.service.register("bad", Bad())
        with patch.object(self.service, "_ffmpeg", side_effect=AssertionError("must not parse playlist")):
            with self.assertRaises(capcut_tts.TTSError):
                self.service.synthesize("Synthetic fixture", "synthetic", self.root / "out.wav", provider="bad")

    def test_srt_full_track_silence_exact_duration_and_trimming_warning(self):
        self.synthetic.duration = 1
        srt = self.root / "input.srt"
        srt.write_text(SRT, encoding="utf-8")
        result = self.service.synthesize_srt(srt, self.root / "track.wav", voice="synthetic", provider="synthetic")
        self.assertEqual(result["provider"], "synthetic")
        self.assertEqual(len(result["cues"]), 2)
        self.assertTrue(all(cue["trimmed"] for cue in result["cues"]))
        self.assertTrue(any("trimmed" in warning for warning in result["warnings"]))
        with wave.open(result["path"], "rb") as stream:
            self.assertEqual(stream.getnframes(), round(.95 * tts.SAMPLE_RATE))
            samples = array("h", stream.readframes(stream.getnframes()))
        self.assertFalse(any(samples[:round(.1 * tts.SAMPLE_RATE)]))
        self.assertFalse(any(samples[round(.5 * tts.SAMPLE_RATE):round(.7 * tts.SAMPLE_RATE)]))
        self.assertTrue(any(samples[round(.12 * tts.SAMPLE_RATE):round(.4 * tts.SAMPLE_RATE)]))

    def test_srt_overlap_and_zero_window_skip(self):
        srt = self.root / "input.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nA\n\n2\n00:00:00,100 --> 00:00:01,000\nB\n\n3\n00:00:00,100 --> 00:00:00,400\nC", encoding="utf-8")
        result = self.service.synthesize_srt(srt, self.root / "track.wav", voice="synthetic", provider="synthetic")
        self.assertEqual(result["cues"][0]["window_ms"], 100)
        self.assertTrue(result["cues"][1]["skipped"])
        self.assertEqual(len(self.synthetic.calls), 2)
        self.assertEqual(tts._wav_info(result["path"])["duration_ms"], 2000)

    def test_srt_worker_error_preserves_original_and_cleans_temp(self):
        output = self.root / "track.wav"
        output.write_bytes(b"original")
        srt = self.root / "input.srt"
        srt.write_text(SRT, encoding="utf-8")
        with patch.object(self.service, "_canonical", side_effect=capcut_tts.TTSError("mock failure")):
            with self.assertRaises(capcut_tts.TTSError):
                self.service.synthesize_srt(srt, output, voice="synthetic", provider="synthetic")
        self.assertEqual(output.read_bytes(), b"original")
        self.assertFalse(list(self.root.glob(".tts-track-*")))
        self.assertFalse(list((self.root / "cache").glob(".tts-*")))

    def test_preview_and_controls_bound_input_without_network(self):
        with self.assertRaises(ValueError):
            self.service.preview("x" * 301, VOICE, self.root / "preview.wav")
        for kwargs in ({"speed": 0}, {"speed": 20}, {"pitch": float("nan")}, {"pitch": 13}):
            with self.assertRaises(ValueError):
                self.synthesize(**kwargs)
        self.assertFalse(self.synthetic.calls)

    def test_valid_but_tampered_cache_audio_regenerates(self):
        self.synthesize()
        cached = next((self.root / "cache").glob("*.wav"))
        cached.write_bytes(wav_bytes(frequency=880))
        self.synthesize("second.wav")
        self.assertEqual(len(self.synthetic.calls), 2)

    def test_pitch_changes_frequency_without_halving_duration(self):
        self.synthetic.duration = 1
        original = self.synthesize("original.wav")
        pitched = self.synthesize("pitched.wav", pitch=12)

        def crossings(path):
            with wave.open(str(path), "rb") as stream:
                samples = array("h", stream.readframes(stream.getnframes()))
            return sum(left <= 0 < right for left, right in zip(samples, samples[1:])) / (len(samples) / tts.SAMPLE_RATE)

        self.assertAlmostEqual(crossings(pitched["path"]) / crossings(original["path"]), 2, delta=.15)
        self.assertAlmostEqual(tts._wav_info(pitched["path"])["duration_ms"], 1000, delta=100)

    def test_ducking_graph_executes_with_synthetic_audio(self):
        bg, speech, output = self.root / "bg.wav", self.root / "voice.wav", self.root / "ducked.wav"
        bg.write_bytes(wav_bytes(1, frequency=220))
        speech.write_bytes(wav_bytes(1))
        self.service._ffmpeg(["-i", str(bg), "-i", str(speech), "-filter_complex", alignment.ducking_filter(),
                              "-map", "[mixed]", "-ar", str(tts.SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(output)])
        self.assertAlmostEqual(tts._wav_info(output)["duration_ms"], 1000, delta=10)

    def test_alignment_adelay_graph_executes_with_synthetic_audio(self):
        first, second, output = self.root / "a.wav", self.root / "b.wav", self.root / "aligned.wav"
        first.write_bytes(wav_bytes())
        second.write_bytes(wav_bytes())
        graph = alignment.build_alignment_filter(alignment.parse_srt(SRT))
        self.service._ffmpeg(["-i", str(first), "-i", str(second), "-filter_complex", graph,
                              "-map", "[tts]", "-ar", str(tts.SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(output)])
        self.assertEqual(tts._wav_info(output)["duration_ms"], 950)

    def test_srt_deadline_failure_is_atomic(self):
        output = self.root / "track.wav"
        output.write_bytes(b"original")
        srt = self.root / "input.srt"
        srt.write_text(SRT, encoding="utf-8")
        self.service.track_timeout = 0
        with self.assertRaises(capcut_tts.TTSTimeout):
            self.service.synthesize_srt(srt, output, voice="synthetic", provider="synthetic")
        self.assertEqual(output.read_bytes(), b"original")
        self.assertFalse(self.synthetic.calls)
        self.assertIsNone(self.service._deadline)

    def test_ffmpeg_absent_returns_unavailable_not_fake_output(self):
        self.service.ffmpeg = str(self.root / "missing-ffmpeg.exe")
        with self.assertRaises(capcut_tts.ProviderUnavailable):
            self.synthesize()
        self.assertFalse((self.root / "out.wav").exists())

    def test_corrupt_cache_regenerated_not_used(self):
        self.synthesize()
        cached = next((self.root / "cache").glob("*.wav"))
        cached.write_bytes(b"broken")
        self.synthesize("second.wav")
        self.assertEqual(len(self.synthetic.calls), 2)
        self.assertEqual(tts._wav_info(cached)["duration_ms"], 200)


if __name__ == "__main__":
    unittest.main()
