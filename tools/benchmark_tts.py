"""Offline, reproducible TTS benchmark: simulated provider latency + real FFmpeg.

Run: python tools/benchmark_tts.py --cues 30 --latency .15 --output artifacts/tts-benchmark.json
No service credentials, video downloads, or network calls are used. Baseline is
legacy serial synthesis/probe/canonical/fit; optimized uses concurrent raw cache
and batched filters. Timings are machine-specific, not a live CapCut promise.
"""
import argparse
from array import array
import copy
import io
import json
import math
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vietsub.editor import tts
from vietsub.editor.capcut_tts import check_cancel


def audio():
    buffer = io.BytesIO()
    samples = array("h", [round(6000 * math.sin(2 * math.pi * 440 * i / tts.SAMPLE_RATE)) for i in range(4800)])
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(buffer, "wb") as stream:
        stream.setparams((1, 2, tts.SAMPLE_RATE, 0, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())
    return buffer.getvalue()


class Provider:
    def __init__(self, name, latency, parallel):
        self.name, self.latency, self.concurrent_safe = name, latency, parallel
        self.cancel = None
        self.data = audio()
        self.shared = {"calls": 0, "active": 0, "peak": 0, "lock": threading.Lock()}

    def voices(self, language="vi"):
        return []

    def fork(self, *, cancel=None, progress=None):
        value = copy.copy(self)
        value.cancel = cancel
        return value

    def synthesize(self, text, voice, output_path):
        shared = self.shared
        with shared["lock"]:
            shared["calls"] += 1
            shared["active"] += 1
            shared["peak"] = max(shared["peak"], shared["active"])
        try:
            end = time.monotonic() + self.latency
            while time.monotonic() < end:
                check_cancel(self.cancel)
                time.sleep(min(.01, max(0, end - time.monotonic())))
            Path(output_path).write_bytes(self.data)
            return {"provider": self.name, "voice": voice, "warnings": []}
        finally:
            with shared["lock"]:
                shared["active"] -= 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cues", type=int, default=30)
    parser.add_argument("--latency", type=float, default=.15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.cues <= 1000 or not 0 <= args.latency <= 5:
        parser.error("cues: 1–1000; latency: 0–5 seconds")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        parser.error("FFmpeg and FFprobe are required")
    report = {"python": platform.python_version(), "platform": platform.platform(), "cues": args.cues,
              "simulated_network_seconds_per_cue": args.latency, "network": "none", "runs": []}
    with tempfile.TemporaryDirectory(prefix="tts-benchmark-") as temp:
        root = Path(temp)
        source = root / "input.srt"
        def stamp(ms):
            sec, ms = divmod(ms, 1000)
            minute, sec = divmod(sec, 60)
            return f"00:{minute:02}:{sec:02},{ms:03}"
        source.write_text("\n\n".join(f"{i + 1}\n{stamp(i * 2800)} --> {stamp((i + 1) * 2800)}\nCâu {i}"
                                      for i in range(args.cues)), encoding="utf-8")
        for name, provider_name, parallel in (("serial_baseline", "capcut_internal", False),
                                               ("capcut_3_workers", "capcut_internal", True),
                                               ("edge_5_workers", "edge_tts", True)):
            fixture = Provider(provider_name, args.latency, parallel)
            service = tts.TTSService(ffmpeg, ffprobe, root / name)
            service.register(provider_name, fixture)
            for cached in ([False, True] if parallel else [False]):
                processes, ffmpeg_processes = [], []
                original_process = tts._process
                def record(argv, **kwargs):
                    processes.append(argv[0])
                    if argv[0] == ffmpeg:
                        ffmpeg_processes.append(argv)
                    return original_process(argv, **kwargs)
                calls_before = fixture.shared["calls"]
                started = time.perf_counter()
                tts._process = record
                try:
                    service.synthesize_srt(source, root / "output.wav", voice="vi-VN-HoaiMyNeural",
                                           provider=provider_name, pitch=2 if cached else 0)
                finally:
                    tts._process = original_process
                elapsed = time.perf_counter() - started
                report["runs"].append({"mode": name + ("_pitch_change_cache_hit" if cached else ""),
                                       "seconds": round(elapsed, 4), "provider_calls": fixture.shared["calls"] - calls_before,
                                       "max_concurrent": fixture.shared["peak"], "audio_processes": len(processes),
                                       "ffmpeg_processes": len(ffmpeg_processes)})
    baseline = report["runs"][0]["seconds"]
    for run in report["runs"]:
        run["speedup_vs_serial"] = round(baseline / run["seconds"], 2)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
