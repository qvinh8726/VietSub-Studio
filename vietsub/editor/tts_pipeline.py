"""Bounded concurrent cue fetching and batched FFmpeg timeline assembly.

The coordinator alone publishes progress and the final WAV. Worker services own
provider state; raw cache locks are shared. Legacy custom adapters remain serial
unless they explicitly advertise concurrent_safe; unsafe fallbacks are serialized.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import copy
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import tempfile
import threading
import time
import wave

from .alignment import atempo_chain, cue_audio_filter, cue_windows, fit_cue, parse_srt
from .capcut_tts import TTSCancelled, TTSError, TTSTimeout, check_cancel


class CombinedCancel:
    def __init__(self, parent, stop):
        self.parent, self.stop = parent, stop

    def is_set(self):
        if self.stop.is_set():
            return True
        return bool(self.parent and (self.parent.is_set() if hasattr(self.parent, "is_set") else self.parent()))

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while not self.is_set():
            left = end - time.monotonic()
            if left <= 0:
                break
            self.stop.wait(min(.05, left))
        return self.is_set()


class AdaptiveThrottle:
    """Reduce only new admissions on 429; never cancel valid in-flight cues."""
    def __init__(self, maximum, *, clock=time.monotonic, cooldown=.4):
        self.maximum = maximum
        self.limit = maximum
        self.clock, self.cooldown = clock, cooldown
        self.until = 0.0
        self.successes = 0

    def rate_limited(self):
        now = self.clock()
        if now < self.until:
            return False  # The HTTP failure and its retry log are one incident.
        self.limit = max(1, self.limit // 2)
        self.until = now + self.cooldown
        self.successes = 0
        return True

    def success(self):
        self.successes += 1
        if self.clock() >= self.until and self.successes >= 3 and self.limit < self.maximum:
            self.limit += 1
            self.successes = 0

    def ready(self):
        return self.clock() >= self.until


def safe_workers(service, provider):
    maximum = 6 if provider == "capcut_internal" else 8
    value = service.workers.get(provider, 1)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"TTS workers for {provider} must be an integer from 1 to {maximum}")
    return value


def fetch_cues(service, cues, voice, provider, speed, pitch, allow_fallback):
    """Yield results by source index while reporting actual completions, not order."""
    count = len(cues)
    maximum = safe_workers(service, provider)
    throttle = AdaptiveThrottle(maximum)
    stop, events, local = threading.Event(), Queue(), threading.local()
    cancel = CombinedCancel(service.cancel, stop)
    executor = ThreadPoolExecutor(max_workers=maximum, thread_name_prefix="tts-cue")
    completed, next_index, fallback = 0, 0, None
    pending, results = {}, {}

    def fetch(cue, effective_provider, effective_voice):
        check_cancel(cancel)
        if not hasattr(local, "service"):
            child = copy.copy(service)
            child.cancel, child.progress = cancel, events.put
            child._cue_progress, child._raw_only, child._track_active = {}, True, False
            child._track_fallback = None
            child.providers = {name: adapter.fork(cancel=cancel, progress=child._provider_progress)
                               if getattr(adapter, "concurrent_safe", False) and callable(getattr(adapter, "fork", None)) else adapter
                               for name, adapter in service.providers.items()}
            local.service = child
        return local.service._speech(cue["text"], effective_voice, effective_provider, speed, pitch, allow_fallback)

    def drain():
        while True:
            try:
                event = events.get_nowait()
            except Empty:
                return
            if event.get("http_status") == 429 and throttle.rate_limited():
                service._emit(stage="tts_throttle", provider=provider, workers=throttle.limit,
                              message=f"CapCut HTTP 429: tạm giảm còn {throttle.limit} luồng; giãn nhịp {throttle.cooldown:g}s.")
            service._emit(**event)

    try:
        service._cue_update(current=1, completed=0, total=count)
        service._emit(stage="tts_cue", provider=provider)
        while pending or next_index < count:
            check_cancel(cancel)
            if service._deadline is not None and time.monotonic() >= service._deadline:
                raise TTSTimeout("SRT speech track exceeded its bounded deadline")
            drain()
            while next_index < count and len(pending) < throttle.limit and throttle.ready():
                effective_provider, effective_voice = ("edge_tts", fallback) if fallback else (provider, voice)
                future = executor.submit(fetch, cues[next_index], effective_provider, effective_voice)
                pending[future] = next_index
                next_index += 1
            if not pending:
                cancel.wait(.05)
                continue
            done, _ = wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
            drain()
            for future in sorted(done, key=lambda item: pending[item]):
                index = pending.pop(future)
                result = future.result()
                results[index] = result
                if provider == "capcut_internal" and result["provider"] == "edge_tts" and allow_fallback and not fallback:
                    fallback = result["voice"]
                    service._emit(stage="tts_fallback", provider="edge_tts",
                                  message="Đã chuyển sang Edge-TTS cho các câu chưa gửi; giữ kết quả các câu đang chạy.")
                completed += 1
                throttle.success()
                service._cue_update(current=completed, completed=completed, total=count)
                service._emit(stage="tts_cue_done", provider=result["provider"])
        drain()
        return [results[index] for index in range(count)]
    finally:
        stop.set()
        for future in pending:
            future.cancel()
        # Do not let executor.__exit__ block cancellation on an external adapter.
        # Built-in HTTP/process workers observe CombinedCancel within 50-100ms.
        executor.shutdown(wait=False, cancel_futures=True)


def recover_batch(service, items, directory, speed, pitch, allow_fallback, recovered):
    """Keep the fast batch path; bisect only failed batches to isolate bad audio."""
    items = [(cue, recovered.get(raw["path"], raw)) for cue, raw in items]
    try:
        return canonical_batch(service, items, directory, speed, pitch)
    except (TTSCancelled, TTSTimeout):
        raise
    except TTSError as error:
        if len(items) > 1:
            middle = len(items) // 2
            return (recover_batch(service, items[:middle], directory, speed, pitch, allow_fallback, recovered)
                    + recover_batch(service, items[middle:], directory, speed, pitch, allow_fallback, recovered))
        cue, raw = items[0]
        service._invalidate_raw(raw)
        if not allow_fallback or raw["provider"] != "capcut_internal":
            raise
        fallback_voice = service._fallback_voice(raw.get("cache_voice", raw["voice"]))
        service._emit(stage="tts_fallback", provider="edge_tts", level="warning",
                      message=f"Audio CapCut không giải mã được ({error}); chuyển câu {cue['index']} sang Edge-TTS.")
        replacement = service._raw_cache(cue["text"], fallback_voice, "edge_tts")
        replacement["warnings"] = [*replacement["warnings"],
            f"CapCut audio could not be decoded; used optional Edge voice {fallback_voice}. Voice/timbre may differ."]
        recovered[raw["path"]] = replacement
        return recover_batch(service, [(cue, replacement)], directory, speed, pitch, False, recovered)


def canonical_batch(service, items, directory, speed, pitch):
    """One decode/effects process for <=25 cues; no per-cue ffprobe spawn."""
    from .tts import SAMPLE_RATE, _atomic_json, _wav_info, audio_demuxer, cache_key
    from vietsub.manifest import _safe_path
    inputs, outputs, filters, pending, results = [], [], [], [], []
    factor = 2 ** (pitch / 12)
    effects = f"aresample={SAMPLE_RATE},aformat=sample_fmts=s16:channel_layouts=mono"
    if pitch:
        effects += f",asetrate={SAMPLE_RATE}*{factor:.12g},aresample={SAMPLE_RATE}"
    effects += "," + atempo_chain(speed / factor)
    for position, (cue, raw) in enumerate(items):
        check_cancel(service.cancel)
        key = cache_key(cue["text"], raw["provider"], raw.get("cache_voice", raw["voice"]), speed, pitch)
        cached, metadata = service.cache_dir / (key + ".wav"), service.cache_dir / (key + ".json")
        _safe_path(str(cached)); _safe_path(str(metadata))
        info = None
        if cached.is_file() and metadata.is_file():
            try:
                if metadata.stat().st_size > 16384:
                    raise ValueError("Metadata limit")
                data = json.loads(metadata.read_text(encoding="utf-8"))
                if data.get("sha256") != service._digest(cached):
                    raise ValueError("Cache mismatch")
                info = _wav_info(cached)
                os.utime(cached, None)
            except TTSCancelled:
                raise
            except (OSError, ValueError, TTSError, AttributeError):
                pass
        results.append({**raw, "path": str(cached), **(info or {})})
        if info is not None:
            continue
        index = len(pending)
        target = directory / f"normalized-{position}.wav"
        inputs += ["-protocol_whitelist", "file,pipe", "-f", audio_demuxer(raw["path"]), "-i", raw["path"]]
        filters.append(f"[{index}:a:0]{effects}[cue{index}]")
        outputs += ["-map", f"[cue{index}]", "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", "-t", "600", "-f", "wav", str(target)]
        pending.append((position, target, cached, metadata))
    if pending:
        script = directory / "normalize.filters"
        script.write_text(";\n".join(filters), encoding="utf-8")
        service._ffmpeg(["-filter_complex_threads", "1", *inputs, "-filter_complex_script", str(script), *outputs])
        for position, target, cached, metadata in pending:
            check_cancel(service.cancel)
            info = _wav_info(target)
            if info["duration_ms"] >= 600000:
                raise TTSError("Cue audio exceeds the 10-minute processed duration limit")
            result = results[position]
            data = {key: result[key] for key in ("provider", "voice", "warnings")}
            data["sha256"] = service._digest(target)
            os.replace(target, cached)
            _atomic_json(metadata, data)
            result.update(info)
    return results


def align_batch(service, items, canonical, directory):
    from .tts import SAMPLE_RATE
    inputs, filters, fits = [], [], []
    start = items[0][0]["start_ms"]
    end = max(cue["start_ms"] + cue["window_ms"] for cue, _ in items)
    for index, ((cue, _), result) in enumerate(zip(items, canonical)):
        fit = fit_cue(result["duration_ms"], cue["window_ms"], max_speed=service.max_fit_speed)
        fits.append(fit)
        inputs += ["-protocol_whitelist", "file,pipe", "-f", "wav", "-i", result["path"]]
        filters.append(f"[{index}:a:0]{cue_audio_filter(cue['window_ms'], speed=fit['speed'])},"
                       f"adelay={cue['start_ms'] - start}:all=1[cue{index}]")
    labels = "".join(f"[cue{index}]" for index in range(len(items)))
    filters.append(f"{labels}amix=inputs={len(items)}:duration=longest:normalize=0,"
                   f"apad,atrim=end_sample={(end - start) * SAMPLE_RATE // 1000}[batch]")
    script, target = directory / "align.filters", directory / "aligned.wav"
    script.write_text(";\n".join(filters), encoding="utf-8")
    service._ffmpeg(["-filter_complex_threads", "1", *inputs, "-filter_complex_script", str(script), "-map", "[batch]",
                     "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", str(target)])
    return target, fits, start, end


def synthesize_srt(service, srt_path, output_path, *, voice, provider, speed, pitch, allow_fallback):
    from .tts import SAMPLE_RATE, _number, speech_text
    from .alignment import AlignmentError
    from vietsub.manifest import _safe_path
    source, output = Path(srt_path), Path(output_path)
    if output.suffix.lower() != ".wav":
        raise ValueError("Aligned SRT output must use .wav")
    if source.stat().st_size > 4 * 1024 * 1024:
        raise AlignmentError("SRT exceeds the 4 MiB input limit")
    cues = cue_windows(parse_srt(source.read_text(encoding="utf-8-sig"), allow_empty=True))
    for cue in cues:
        cue["text"] = speech_text(cue["text"])
    speakable = [cue for cue in cues if cue["text"] and cue["window_ms"]]
    if not speakable:
        raise AlignmentError("Phụ đề không có câu chứa chữ hoặc số để tạo giọng.")
    speed, pitch = _number(speed, "speed", .5, 2), _number(pitch, "pitch", -12, 12)
    safe_workers(service, provider)
    if type(service.batch_size) is not int or not 1 <= service.batch_size <= 30:
        raise ValueError("TTS batch_size must be from 1 to 30")
    _safe_path(str(service.cache_dir))
    service.cache_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    previous_deadline, previous_progress = service._deadline, service._cue_progress
    deadline = time.monotonic() + service.track_timeout
    service._deadline = min(previous_deadline, deadline) if previous_deadline is not None else deadline
    temporary = None
    published = False
    warnings, records, providers = [], {}, set()

    def checkpoint():
        check_cancel(service.cancel)
        if time.monotonic() >= service._deadline:
            raise TTSTimeout("SRT speech track exceeded its bounded deadline")

    def silence(track, frames):
        while frames > 0:
            checkpoint()
            count = min(frames, SAMPLE_RATE)
            track.writeframesraw(b"\x00" * (count * 2))
            frames -= count

    try:
        checkpoint()
        raw = fetch_cues(service, speakable, voice, provider, speed, pitch, allow_fallback)
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".tts-track-", suffix=".wav", delete=False) as stream:
            temporary = Path(stream.name)
        with tempfile.TemporaryDirectory(prefix=".tts-batch-", dir=service.cache_dir) as temp, wave.open(str(temporary), "wb") as track:
            directory = Path(temp)
            track.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
            written = 0
            recovered = {}
            for offset in range(0, len(speakable), service.batch_size):
                checkpoint()
                items = list(zip(speakable[offset:offset + service.batch_size], raw[offset:offset + service.batch_size]))
                service._emit(stage="tts_align", provider=provider,
                              message=f"Đang căn giọng theo lô: {offset + 1}–{offset + len(items)}/{len(speakable)} câu.")
                normalized = recover_batch(service, items, directory, speed, pitch, allow_fallback, recovered)
                aligned, fits, start, end = align_batch(service, items, normalized, directory)
                start_frame, end_frame = start * SAMPLE_RATE // 1000, end * SAMPLE_RATE // 1000
                silence(track, max(0, start_frame - written))
                copied = 0
                with wave.open(str(aligned), "rb") as segment:
                    while copied < end_frame - start_frame:
                        checkpoint()
                        data = segment.readframes(min(SAMPLE_RATE, end_frame - start_frame - copied))
                        if not data:
                            break
                        track.writeframesraw(data)
                        copied += len(data) // 2
                silence(track, end_frame - start_frame - copied)
                written = end_frame
                for (cue, _), result, fit in zip(items, normalized, fits):
                    providers.add(result["provider"])
                    cue_warnings = [*result["warnings"], *fit["warnings"]]
                    warnings.extend(f"Cue {cue['index']}: {warning}" for warning in cue_warnings)
                    records[cue["index"]] = {key: cue[key] for key in ("index", "start_ms", "end_ms", "window_ms")}
                    records[cue["index"]].update(provider=result["provider"], voice=result["voice"], fit_speed=fit["speed"],
                                                trimmed=fit["trimmed"], skipped=False, duration_ms=fit["duration_ms"], warnings=cue_warnings)
            silence(track, max(0, max(cue["end_ms"] for cue in cues) * SAMPLE_RATE // 1000 - written))
        for cue in cues:
            if cue["index"] not in records:
                warning = f"Câu SRT {cue['index']}: bỏ qua nội dung rỗng/ký tự đặc biệt hoặc không còn cửa sổ đọc."
                warnings.append(warning)
                records[cue["index"]] = {key: cue[key] for key in ("index", "start_ms", "end_ms", "window_ms")}
                records[cue["index"]].update(provider=None, voice=None, skipped=True, warnings=[warning])
                service._emit(stage="tts_skip", provider=provider, message=warning)
        checkpoint()
        os.replace(temporary, output)
        published = True
        return {"path": str(output), "provider": next(iter(providers)) if len(providers) == 1 else "mixed",
                "warnings": list(dict.fromkeys(warnings)), "cues": [records[cue["index"]] for cue in cues]}
    finally:
        service._deadline, service._cue_progress = previous_deadline, previous_progress
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        # Pruning here happens after readers finish; on cancellation workers might
        # still be unwinding, so leave entries for the next successful run.
        if published:
            try:
                service.prune_cache()
            except (OSError, ValueError):
                pass  # A locked cache entry must not invalidate published audio.
