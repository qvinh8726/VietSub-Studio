"""Regression tests for the queued SRT path (fake HTTP, synthetic PCM, no network)."""
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from vietsub.editor import alignment, capcut_tts, renderer, tts
try:
    from .test_editor_tts import Response, Transport, SyntheticProvider, VOICE, SRT, created, completed, wav_bytes
except ImportError:
    from test_editor_tts import Response, Transport, SyntheticProvider, VOICE, SRT, created, completed, wav_bytes


class SRTProgressTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="tts-progress-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.events = []
        self.cancel = threading.Event()
        self.service = tts.TTSService("ffmpeg", "ffprobe", self.root / "cache", cancel=self.cancel,
                                      progress=self.events.append)
        # Audio effects are covered with real FFmpeg in test_editor_tts. Keep these
        # tests focused on actual HTTP -> service -> cue assembly/callback logic.
        def canonical(source, target, **kwargs):
            shutil.copyfile(source, target)
            return tts._wav_info(target)
        self.enterContext(patch.object(self.service, "_canonical", side_effect=canonical))
        self.edge = SyntheticProvider(provider="edge_tts")
        self.service.register("edge_tts", self.edge)
        self.source = self.root / "source.srt"
        self.source.write_text(SRT, encoding="utf-8")
        self.output = self.root / "out.wav"

    def client(self, responses, **kwargs):
        transport = Transport(responses)
        adapter = capcut_tts.CapCutClient(transport=transport, payload_signer=lambda *args: "test-signature",
                                          poll_interval=.001, **kwargs)
        self.service.register("capcut_internal", adapter)
        return transport

    def run_track(self, **kwargs):
        return self.service.synthesize_srt(self.source, self.output, voice=VOICE, **kwargs)

    def test_progress_survives_synthesis_and_poll_and_counts_finished_cues(self):
        transport = self.client([Response(created()), Response(completed()), Response(wav_bytes()),
                                 Response(created()), Response(completed(status="running")),
                                 Response(completed()), Response(wav_bytes())])
        self.run_track(allow_fallback=False)
        done = [event for event in self.events if event["stage"] == "tts_cue_done"]
        self.assertEqual([(e["completed"], e["total"], e["percent"]) for e in done], [(1, 2, 50), (2, 2, 100)])
        self.assertEqual([e["percent"] for e in self.events], sorted(e["percent"] for e in self.events))
        second_polls = [e for e in self.events if e["stage"] == "tts_poll" and e["current"] == 2]
        self.assertTrue(second_polls)
        self.assertTrue(all(e["completed"] == 1 and e["percent"] == 50 for e in second_polls))
        self.assertEqual(done[0]["message"], "Đang tạo giọng: câu 1/2 (50%)")
        self.assertEqual(len(transport.requests), 7)
        self.events.clear()
        self.run_track(allow_fallback=False)
        self.assertEqual(len(transport.requests), 7, "cached cues must still advance progress")
        self.assertEqual([e["percent"] for e in self.events if e["stage"] == "tts_cue_done"], [50, 100])

    def test_four_minute_track_reports_all_85_cues_without_poll_resets(self):
        def stamp(milliseconds):
            seconds, ms = divmod(milliseconds, 1000)
            minutes, sec = divmod(seconds, 60)
            return f"00:{minutes:02}:{sec:02},{ms:03}"
        self.source.write_text("\n\n".join(
            f"{index + 1}\n{stamp(index * 2800)} --> {stamp((index + 1) * 2800)}\nCâu số {index + 1}"
            for index in range(85)), encoding="utf-8")
        replies = []
        for _ in range(85):
            replies.extend([Response(created()), Response(completed()), Response(wav_bytes())])
        transport = self.client(replies)
        self.run_track(allow_fallback=False)
        done = [event for event in self.events if event["stage"] == "tts_cue_done"]
        self.assertEqual([event["completed"] for event in done], list(range(1, 86)))
        self.assertEqual(done[11]["message"], "Đang tạo giọng: câu 12/85 (14%)")
        self.assertTrue(all(event["total"] == 85 for event in self.events))
        self.assertEqual([event["percent"] for event in self.events], sorted(event["percent"] for event in self.events))
        self.assertEqual(done[-1]["percent"], 100)
        self.assertEqual(tts._wav_info(self.output)["duration_ms"], 238000)
        self.assertEqual(len(transport.requests), 255)

    def test_stalled_poll_falls_back_once_for_whole_track_with_live_reason(self):
        transport = self.client([Response(created()), Response(completed(status="running")),
                                 Response(completed(status="running"))], max_polls=2)
        result = self.run_track()
        self.assertEqual(result["provider"], "edge_tts")
        self.assertEqual(len(self.edge.calls), 2)
        self.assertEqual(len(transport.requests), 3, "remaining cues must not repeat the CapCut wait")
        fallbacks = [event for event in self.events if event["stage"] == "tts_fallback"]
        self.assertTrue(any("Edge-TTS" in event["message"] and "TTSTimeout" in event["message"] for event in fallbacks))
        self.assertEqual(self.events[-1]["percent"], 100)
        self.assertIsNone(self.service._track_fallback, "fallback is scoped to this track only")

    def test_unresponsive_poll_hits_hard_deadline_then_edge_finishes_track(self):
        release, returned = threading.Event(), threading.Event()
        class StuckQuery:
            def open(self, request, timeout):
                if '/query?' in request.full_url:
                    try:
                        release.wait(2)  # Deliberately ignores the supplied HTTP timeout.
                        return Response(completed(status="running"))
                    finally:
                        returned.set()
                return Response(created())
        adapter = capcut_tts.CapCutClient(transport=StuckQuery(), payload_signer=lambda *args: "test",
                                          task_timeout=.08)
        self.service.register("capcut_internal", adapter)
        started = time.monotonic()
        try:
            result = self.run_track()
            elapsed = time.monotonic() - started
            self.assertEqual(result["provider"], "edge_tts")
            self.assertLess(elapsed, 1, "a blocked HTTP read must not hold the queued SRT worker")
            self.assertEqual(len(self.edge.calls), 2)
            self.assertTrue(any(e["stage"] == "tts_fallback" and "TTSTimeout" in e["message"] for e in self.events))
            self.assertEqual(self.events[-1]["percent"], 100)
        finally:
            release.set()
            self.assertTrue(returned.wait(1))

    def test_unknown_state_and_rate_limit_retry_once_then_edge(self):
        for failure in (completed(status="unexpected_state"), 429, 503):
            with self.subTest(failure=failure):
                self.events.clear()
                def response():
                    return Response(failure) if isinstance(failure, dict) else Response({"ret": 1234}, status=failure)
                transport = self.client([Response(created()), response(), response()])
                result = self.run_track()
                self.assertEqual(result["provider"], "edge_tts")
                self.assertEqual(len(transport.requests), 3)
                self.assertTrue(any(e["stage"] == "tts_fallback" for e in self.events))
                self.assertEqual(self.events[-1]["percent"], 100)

    def test_no_fallback_stops_with_vietnamese_error_and_preserves_output(self):
        self.output.write_bytes(b"previous-good-output")
        self.client([Response(created()), Response(completed(status="running"))], max_polls=1)
        with self.assertRaisesRegex(capcut_tts.TTSError, "Chuyển sang Edge-TTS đang tắt"):
            self.run_track(allow_fallback=False)
        self.assertEqual(self.edge.calls, [])
        self.assertEqual(self.output.read_bytes(), b"previous-good-output")
        self.assertEqual(self.events[-1]["stage"], "tts_error")

    def test_cancel_in_poll_never_falls_back_or_publishes(self):
        transport = self.client([Response(created()), Response(completed(status="running"))])
        def progress(event):
            self.events.append(event)
            if event["stage"] == "tts_poll":
                self.cancel.set()
        self.service.progress = progress
        with self.assertRaises(capcut_tts.TTSCancelled):
            self.run_track()
        self.assertLessEqual(len(transport.requests), 2)
        self.assertFalse(self.edge.calls)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".tts-track-*")))

    def test_cancel_after_one_cue_does_not_start_next_cue(self):
        transport = self.client([Response(created()), Response(completed()), Response(wav_bytes())])
        def progress(event):
            self.events.append(event)
            if event["stage"] == "tts_cue_done":
                self.cancel.set()
        self.service.progress = progress
        with self.assertRaises(capcut_tts.TTSCancelled):
            self.run_track()
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(self.events[-1]["completed"], 1)
        self.assertFalse(self.edge.calls)
        self.assertFalse(self.output.exists())

    def test_empty_markup_and_symbols_are_not_sent_but_timeline_is_retained(self):
        source = ("1\n00:00:00,000 --> 00:00:00,100\n\n"
                  "2\n00:00:00,100 --> 00:00:00,300\n<i>♪ ... !!!</i>\n\n"
                  "3\n00:00:00,300 --> 00:00:00,600\n<b>Xin chào Việt Nam 123</b>\n\n"
                  "4\n00:00:00,600 --> 00:00:01,000\n&nbsp;\u200b\n")
        self.source.write_text(source, encoding="utf-8")
        self.run_track(provider="edge_tts")
        self.assertEqual([call[0] for call in self.edge.calls], ["Xin chào Việt Nam 123"])
        self.assertEqual(self.source.read_text(encoding="utf-8"), source)
        self.assertEqual(tts._wav_info(self.output)["duration_ms"], 1000)
        self.assertEqual(self.events[-1]["total"], 1)
        self.assertEqual(self.events[-1]["percent"], 100)
        self.assertEqual(sum(e["stage"] == "tts_skip" for e in self.events), 3)

    def test_all_unspeakable_cues_fail_clearly_without_network(self):
        self.source.write_text("1\n00:00:00,000 --> 00:00:01,000\n♪ ... !!!", encoding="utf-8")
        with self.assertRaisesRegex(alignment.AlignmentError, "không có câu"):
            self.run_track()
        self.assertFalse(self.edge.calls)
        self.assertFalse(self.output.exists())
        with self.assertRaises(alignment.AlignmentError):
            alignment.parse_srt("1\nbroken timestamp\n", allow_empty=True)
        with self.assertRaises(alignment.AlignmentError):
            alignment.parse_srt("1\n00:00:00,000 --> 00:00:01,000\n")

    def test_renderer_diagnostic_without_counters_preserves_last_percent(self):
        state = {"percent": 0}
        emit = renderer.tts_progress(lambda **value: state.update(value))
        emit({"stage": "tts_cue_done", "completed": 12, "total": 85, "percent": 14})
        emit({"stage": "tts_poll", "message": "HTTP 200; status=running"})
        self.assertEqual((state["percent"], state["completed"], state["total"]), (14, 12, 85))
        self.assertEqual(state["phase"], "tts_poll")


if __name__ == "__main__":
    unittest.main()
