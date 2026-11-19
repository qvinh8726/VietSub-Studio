"""Queue-worker TTS service; stdlib at import/startup, optional workers on demand.

Run synthesis on the editor/render queue, NOT inside an async Flask view. HTTP,
FFmpeg, and optional Edge work are synchronous here but bounded/cancellable.

Optional installation is explicit, never performed by this module::

    <runtime_python> -m pip install edge-tts

For portable/frozen applications pass a real external Python interpreter as
``runtime_python`` (the application EXE is not a Python worker). In an ordinary
Python installation it defaults to sys.executable. Edge is imported only inside
the separate worker process; no aiohttp/ONNX/torch imports reach app startup.
Edge itself is an unofficial online provider and may become unavailable.

Speed is a tempo multiplier (0.5–2.0, 2.0 means twice as fast); pitch is
semitones (-12–12). Both are
applied locally by FFmpeg to all providers, avoiding invented CapCut parameters.
Pitch uses asetrate/resample plus compensating atempo; it is not formant-aware.
Cached audio is canonical 24 kHz mono PCM WAV, keyed by provider, voice, text,
speed, pitch and cache-format version; text never appears in cache metadata.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

from .alignment import AlignmentError, atempo_chain, cue_audio_filter, cue_windows, fit_cue, parse_srt
from .capcut_tts import (CapCutClient, ProviderUnavailable, TTSCancelled, TTSError,
                         TTSTimeout, catalog_voices, check_cancel, validate_text)

SAMPLE_RATE = 24000
_EDGE_GUIDANCE = "Install optional edge-tts with: <runtime_python> -m pip install edge-tts; configure a real external runtime_python for portable builds."
_CACHE_VERSION = 1


def _runtime(runtime_python=None) -> str:
    if runtime_python not in (None, ""):
        value = os.fspath(runtime_python)
        if not value or "\x00" in value:
            raise ValueError("runtime_python must be an interpreter path")
        return value
    if getattr(sys, "frozen", False):
        raise ProviderUnavailable("A real external runtime_python is required for portable optional workers")
    return sys.executable


def _process(argv, *, cancel=None, timeout=120.0, cwd=None, max_stdout=2 * 1024 * 1024) -> bytes:
    """Run argv without shell/pipes; drain to temporary files with bounded waits.

    This prevents stdout/stderr pipe deadlocks and lets the queue terminate a
    cancelled worker. No arbitrary upstream stderr is exposed (it may echo text
    or credentials). Background jobs/process trees are never killed by name.
    """
    if isinstance(timeout, bool) or not math.isfinite(float(timeout)) or timeout <= 0:
        raise ValueError("Process timeout must be positive")
    check_cancel(cancel)
    process = None
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            try:
                process = subprocess.Popen([os.fspath(arg) for arg in argv], stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, shell=False, cwd=cwd,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                raise ProviderUnavailable("Required audio executable/runtime could not be started") from None
            deadline = time.monotonic() + float(timeout)
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() >= deadline:
                    raise TTSTimeout("Audio process exceeded its deadline")
                # Wait on this specific process, not a busy-poll/sleep loop.
                try:
                    process.wait(timeout=min(.1, max(.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
                if stdout.tell() > max_stdout or stderr.tell() > 8 * 1024 * 1024:
                    raise TTSError("Audio worker produced excessive diagnostic output")
            check_cancel(cancel)
            if process.returncode:
                raise ProviderUnavailable(f"Audio worker failed (exit {process.returncode}); check optional runtime, network, model, or media configuration")
            if stdout.tell() > max_stdout or stderr.tell() > 8 * 1024 * 1024:
                raise TTSError("Audio worker returned excessive output")
            stdout.seek(0)
            return stdout.read(max_stdout + 1)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


_EDGE_CHECK = "import importlib.util,json; print(json.dumps({'installed':importlib.util.find_spec('edge_tts') is not None}))"
_EDGE_WORKER = r'''
import asyncio,json,sys
from pathlib import Path
config=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
async def main():
    import edge_tts
    if config["action"] == "voices":
        result=await edge_tts.list_voices()
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
    else:
        communicate=edge_tts.Communicate(config["text"],config["voice"],connect_timeout=8,receive_timeout=30)
        await communicate.save(sys.argv[2])
asyncio.run(main())
'''


class EdgeProvider:
    """Lazy external-runtime adapter; even ordinary installs isolate async imports."""
    provider = "edge_tts"

    def __init__(self, *, runtime_python=None, cancel=None, timeout=120.0):
        self.runtime_python, self.cancel, self.timeout = runtime_python, cancel, timeout

    def availability(self) -> dict:
        try:
            python = _runtime(self.runtime_python)
            raw = _process([python, "-c", _EDGE_CHECK], cancel=self.cancel, timeout=10)
            installed = json.loads(raw.decode("utf-8")).get("installed") is True
            return {"provider": self.provider, "available": installed, "installed": installed,
                    "service_verified": False, "network_checked": False,
                    "reason": "Optional edge-tts found; online availability is unverified." if installed else "Optional edge-tts is not installed.",
                    "guidance": _EDGE_GUIDANCE}
        except TTSCancelled:
            raise
        except (TTSError, ValueError, UnicodeError, AttributeError):
            return {"provider": self.provider, "available": False, "installed": False,
                    "service_verified": False, "network_checked": False,
                    "reason": "Optional Python runtime/edge-tts could not be checked.", "guidance": _EDGE_GUIDANCE}

    def _worker(self, config: dict, output_path):
        deadline = time.monotonic() + self.timeout
        if not self.availability()["available"]:
            raise ProviderUnavailable("Optional Edge provider unavailable. " + _EDGE_GUIDANCE)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TTSTimeout("Edge worker deadline exceeded during runtime check")
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Text is passed via a private temporary UTF-8 file, not process argv/logs.
        with tempfile.TemporaryDirectory(prefix="vietsub-edge-") as directory:
            request = Path(directory) / "request.json"
            request.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            _process([_runtime(self.runtime_python), "-c", _EDGE_WORKER, request, output],
                     cancel=self.cancel, timeout=remaining)
        if not output.is_file() or output.stat().st_size == 0:
            raise ProviderUnavailable("Edge returned no output")

    def voices(self, language="vi") -> list[dict]:
        with tempfile.TemporaryDirectory(prefix="vietsub-edge-voices-") as directory:
            output = Path(directory) / "voices.json"
            self._worker({"action": "voices"}, output)
            if output.stat().st_size > 2 * 1024 * 1024:
                raise TTSError("Edge voice response exceeds size limit")
            try:
                data = json.loads(output.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                raise ProviderUnavailable("Edge returned malformed voice data") from None
        if not isinstance(data, list) or len(data) > 5000:
            raise ProviderUnavailable("Edge returned invalid voice list")
        language = str(language or "").lower().replace("_", "-")
        result = []
        for voice in data:
            if not isinstance(voice, dict):
                raise ProviderUnavailable("Edge returned invalid voice data")
            identifier, locale = voice.get("ShortName"), voice.get("Locale")
            if not isinstance(identifier, str) or not isinstance(locale, str):
                raise ProviderUnavailable("Edge voice lacks identifier/language")
            if (not language or language == "*" or locale.lower() == language
                    or ("-" not in language and locale.lower().split("-")[0] == language)):
                result.append({"id": identifier, "name": voice.get("FriendlyName") or voice.get("LocalName") or identifier,
                               "language": locale, "provider": self.provider})
        return result

    def synthesize(self, text, voice, output_path) -> dict:
        text = validate_text(text)
        if not isinstance(voice, str) or not voice or len(voice) > 200 or not voice.endswith("Neural"):
            raise ValueError("Edge voice must be a listed Neural voice identifier")
        self._worker({"action": "synthesize", "text": text, "voice": voice}, output_path)
        return {"path": str(output_path), "provider": self.provider, "voice": voice,
                "warnings": ["Edge TTS is an optional unofficial online provider; service availability is not guaranteed."]}


def _number(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number between {low:g} and {high:g}")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{name} must be a finite number between {low:g} and {high:g}") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} must be a finite number between {low:g} and {high:g}")
    return 0.0 if number == 0 else number


def cache_key(text: str, provider: str, voice, speed=1.0, pitch=0.0) -> str:
    """Stable identity; provider-specific fallback never poisons the requested key."""
    payload = {"version": _CACHE_VERSION, "text": validate_text(text), "provider": provider,
               "voice": voice, "speed": _number(speed, "speed", .5, 2), "pitch": _number(pitch, "pitch", -12, 12)}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _wav_info(path, *, max_seconds=600) -> dict:
    try:
        with wave.open(str(path), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                raise TTSError("Audio is not canonical 24 kHz mono PCM WAV")
            frames = source.getnframes()
            if frames <= 0 or (max_seconds is not None and frames > SAMPLE_RATE * max_seconds):
                raise TTSError("Cue audio duration is empty or exceeds its limit")
            if Path(path).stat().st_size < frames * 2 + 44:
                raise TTSError("Cue WAV is incomplete")
            return {"frames": frames, "duration_ms": frames * 1000 / SAMPLE_RATE}
    except (OSError, EOFError, wave.Error):
        raise TTSError("Invalid or unreadable cue WAV") from None


def _atomic_json(path: Path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tts-meta-", suffix=".tmp", mode="w",
                                         encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class TTSService:
    """Synchronous service for editor workers, with replaceable provider adapters.

    ``progress`` receives one dict, e.g. ``{stage, provider, current, total}``.
    ``cancel`` is a threading.Event or zero-argument bool callback. ``register``
    accepts adapters exposing ``voices(language)``, ``synthesize(text,voice,path)``
    and optionally ``availability()``. Providers must honor their cancel/deadline.
    """
    def __init__(self, ffmpeg, ffprobe, cache_dir, runtime_python=None, cancel=None, progress=None):
        self.ffmpeg, self.ffprobe = os.fspath(ffmpeg), os.fspath(ffprobe)
        self.cache_dir = Path(cache_dir)
        from vietsub.manifest import _safe_path
        _safe_path(str(self.cache_dir))
        self.runtime_python, self.cancel, self.progress = runtime_python, cancel, progress
        self.process_timeout = 120.0
        self.track_timeout = 1800.0
        self.max_fit_speed = 1.35
        self._deadline = None
        self.providers = {"capcut_internal": CapCutClient(cancel=cancel, progress=progress),
                          "edge_tts": EdgeProvider(runtime_python=runtime_python, cancel=cancel)}

    def register(self, provider: str, adapter):
        if not isinstance(provider, str) or not provider or len(provider) > 80:
            raise ValueError("Invalid provider name")
        if not callable(getattr(adapter, "voices", None)) or not callable(getattr(adapter, "synthesize", None)):
            raise TypeError("Provider must implement voices and synthesize")
        self.providers[provider] = adapter

    def _provider(self, provider):
        if provider not in self.providers:
            raise ValueError("Unknown speech provider")
        return self.providers[provider]

    def availability(self, provider="capcut_internal") -> dict:
        adapter = self._provider(provider)
        if callable(getattr(adapter, "availability", None)):
            return adapter.availability()
        return {"provider": provider, "available": True, "service_verified": False, "network_checked": False,
                "reason": "Custom provider registered; live availability unverified."}

    def voices(self, provider="capcut_internal", language="vi") -> list[dict]:
        check_cancel(self.cancel)
        return self._provider(provider).voices(language)

    def _emit(self, **value):
        check_cancel(self.cancel)
        if self.progress:
            self.progress(value)

    def _ffmpeg(self, arguments):
        timeout = self.process_timeout
        if self._deadline is not None:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise TTSTimeout("SRT speech track exceeded its bounded deadline")
            timeout = min(timeout, remaining)
        return _process([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *arguments],
                        cancel=self.cancel, timeout=timeout)

    def _canonical(self, source, target, *, speed=1.0, pitch=0.0, fit_filter=None):
        # Force an audio demuxer rather than letting an untrusted response masquerade
        # as a playlist that instructs FFmpeg to open more URLs/local files.
        with open(source, "rb") as stream:
            header = stream.read(12)
        if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            demuxer = "wav"
        elif header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0):
            demuxer = "mp3"
        elif header.startswith(b"fLaC"):
            demuxer = "flac"
        else:
            raise TTSError("Provider response is not a supported WAV/MP3/FLAC audio artifact")
        if fit_filter is None:
            probe_timeout = min(self.process_timeout, 15)
            if self._deadline is not None:
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise TTSTimeout("SRT speech track exceeded its bounded deadline")
                probe_timeout = min(probe_timeout, remaining)
            duration_data = _process([self.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-f", demuxer,
                                      "-i", str(source), "-show_entries", "format=duration", "-of", "json"],
                                     cancel=self.cancel, timeout=probe_timeout)
            try:
                duration = float(json.loads(duration_data.decode("utf-8"))["format"]["duration"])
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise TTSError("Could not determine provider audio duration") from None
            if not math.isfinite(duration) or duration <= 0 or duration / speed > 600:
                raise TTSError("Cue audio is empty or exceeds the 10-minute processed duration limit")
        factor = 2 ** (pitch / 12)
        effects = f"aresample={SAMPLE_RATE},aformat=sample_fmts=s16:channel_layouts=mono"
        if pitch:
            effects += f",asetrate={SAMPLE_RATE}*{factor:.12g},aresample={SAMPLE_RATE}"
        effects += "," + atempo_chain(speed / factor)
        if fit_filter:
            effects += "," + fit_filter
        arguments = ["-protocol_whitelist", "file,pipe", "-f", demuxer, "-i", str(source), "-vn", "-af", effects,
                     "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le"]
        if fit_filter is None:
            arguments.extend(["-t", "600"])
        arguments.extend(["-f", "wav", str(target)])
        self._ffmpeg(arguments)
        return _wav_info(target, max_seconds=None if fit_filter else 600)

    def _digest(self, path):
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while True:
                check_cancel(self.cancel)
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _adapter_synthesize(self, adapter, text, voice, output):
        # One service instance belongs to one queue job. Propagate the caller's
        # current cancellation and the full-track remaining budget to built-ins.
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise TTSTimeout("SRT speech track exceeded its bounded deadline")
        if not isinstance(adapter, (CapCutClient, EdgeProvider)):
            return adapter.synthesize(text, voice, output)
        adapter.cancel = self.cancel
        if isinstance(adapter, CapCutClient):
            adapter.progress = self.progress
        attribute = "task_timeout" if isinstance(adapter, CapCutClient) else "timeout"
        original = getattr(adapter, attribute)
        try:
            if self._deadline is not None:
                setattr(adapter, attribute, min(original, max(.001, self._deadline - time.monotonic())))
            return adapter.synthesize(text, voice, output)
        finally:
            setattr(adapter, attribute, original)

    def _cache(self, text, voice, provider, speed, pitch) -> dict:
        from vietsub.manifest import _safe_path
        _safe_path(str(self.cache_dir))
        adapter = self._provider(provider)
        key = cache_key(text, provider, voice, speed, pitch)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached = self.cache_dir / (key + ".wav")
        metadata = self.cache_dir / (key + ".json")
        _safe_path(str(cached)); _safe_path(str(metadata))
        check_cancel(self.cancel)
        if cached.is_file() and metadata.is_file():
            try:
                info = _wav_info(cached)
                if metadata.stat().st_size > 16384:
                    raise ValueError("Excessive cache metadata")
                data = json.loads(metadata.read_text(encoding="utf-8"))
                if (not isinstance(data, dict) or data.get("provider") != provider
                        or not isinstance(data.get("voice"), str) or not isinstance(data.get("warnings"), list)
                        or any(not isinstance(item, str) for item in data["warnings"])
                        or data.get("sha256") != self._digest(cached)):
                    raise ValueError("Invalid or mismatched cache metadata")
                return {**data, **info, "path": str(cached), "cached": True}
            except TTSCancelled:
                raise
            except (TTSError, OSError, ValueError, UnicodeError):
                # Atomic regeneration replaces corrupt entries; no fake cache success.
                pass
        self._emit(stage="tts_synthesize", provider=provider)
        with tempfile.TemporaryDirectory(prefix=".tts-cue-", dir=self.cache_dir) as directory:
            raw, normalized = Path(directory) / "source.audio", Path(directory) / "normalized.wav"
            response = self._adapter_synthesize(adapter, text, voice, raw)
            if (not isinstance(response, dict) or not raw.is_file() or raw.stat().st_size == 0
                    or raw.stat().st_size > 128 * 1024 * 1024):
                raise TTSError("Provider returned no valid bounded audio file")
            actual_voice = response.get("voice")
            warnings = response.get("warnings", [])
            if response.get("provider", provider) != provider:
                raise TTSError("Provider returned a mismatched provider identity")
            if (not isinstance(actual_voice, str) or not actual_voice or not isinstance(warnings, list)
                    or any(not isinstance(item, str) or len(item) > 2000 for item in warnings) or len(warnings) > 20):
                raise TTSError("Provider returned invalid voice/warning metadata")
            info = self._canonical(raw, normalized, speed=speed, pitch=pitch)
            data = {"provider": provider, "voice": actual_voice, "warnings": warnings,
                    "sha256": self._digest(normalized)}
            _atomic_json(metadata, data)
            check_cancel(self.cancel)
            os.replace(normalized, cached)
        return {**data, **info, "path": str(cached), "cached": False}

    def _fallback_voice(self, voice) -> str:
        identifier = voice.get("id") if isinstance(voice, dict) else voice
        if isinstance(identifier, str) and identifier.endswith("Neural"):
            return identifier
        record = next((item for item in catalog_voices(None) if item["id"] == identifier), None)
        language = (voice.get("language") if isinstance(voice, dict) else None) or (record or {}).get("language")
        # A mapping is explicit, not a silent claim that CapCut voices are identical.
        if language and language.lower().startswith("vi"):
            if identifier in {"BV075_streaming", "multi_male_felipe_uranus_bigtts"}:
                return "vi-VN-NamMinhNeural"
            return "vi-VN-HoaiMyNeural"
        raise ProviderUnavailable("No safe Edge voice mapping for this CapCut voice; choose an explicit Edge voice")

    def _speech(self, text, voice, provider, speed, pitch, allow_fallback):
        try:
            return self._cache(text, voice, provider, speed, pitch)
        except TTSCancelled:
            raise
        except TTSError as original:
            check_cancel(self.cancel)
            if not allow_fallback or provider != "capcut_internal":
                raise
            fallback_voice = self._fallback_voice(voice)
            try:
                result = self._cache(text, fallback_voice, "edge_tts", speed, pitch)
            except TTSCancelled:
                raise
            except TTSError:
                raise ProviderUnavailable("CapCut unavailable and optional Edge fallback failed/unavailable. " + _EDGE_GUIDANCE) from None
            warning = f"CapCut unavailable ({type(original).__name__}); used optional Edge voice {fallback_voice} instead. Voice/timbre may differ."
            return {**result, "warnings": [warning, *result["warnings"]]}

    def _publish(self, source, output_path):
        output = Path(output_path)
        extension = output.suffix.lower()
        formats = {".wav": ("wav", "pcm_s16le"), ".mp3": ("mp3", "libmp3lame"),
                   ".flac": ("flac", "flac"), ".m4a": ("ipod", "aac"), ".ogg": ("ogg", "libvorbis")}
        if extension not in formats:
            raise ValueError("Audio output extension must be .wav, .mp3, .flac, .m4a, or .ogg")
        if Path(source).resolve() == output.resolve():
            return str(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".tts-out-", suffix=extension, delete=False) as stream:
                temporary = Path(stream.name)
            if extension == ".wav":
                with open(source, "rb") as src, open(temporary, "wb") as dst:
                    while True:
                        check_cancel(self.cancel)
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
            else:
                form, codec = formats[extension]
                self._ffmpeg(["-i", str(source), "-vn", "-c:a", codec, "-f", form, str(temporary)])
            if not temporary.stat().st_size:
                raise TTSError("Audio output is empty")
            check_cancel(self.cancel)
            os.replace(temporary, output)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return str(output)

    def synthesize(self, text, voice, output_path, *, provider="capcut_internal", speed=1.0,
                   pitch=0.0, allow_fallback=True) -> dict:
        text = validate_text(text)
        speed, pitch = _number(speed, "speed", .5, 2), _number(pitch, "pitch", -12, 12)
        self._provider(provider)
        result = self._speech(text, voice, provider, speed, pitch, bool(allow_fallback))
        path = self._publish(result["path"], output_path)
        return {"path": path, "provider": result["provider"], "voice": result["voice"], "warnings": list(result["warnings"])}

    def preview(self, text, voice, output_path, **kwargs) -> dict:
        """Normal bounded synthesis, capped to 300 preview characters; never automatic."""
        validate_text(text, max_text=300)
        return self.synthesize(text, voice, output_path, **kwargs)

    def synthesize_srt(self, srt_path, output_path, *, voice, provider="capcut_internal", speed=1.0,
                       pitch=0.0, allow_fallback=True) -> dict:
        """Build a full-track PCM WAV; streaming assembly keeps memory/argv bounded.

        At most 1000 cues/2h/200k text characters. Overlap is clipped at next cue,
        short cues padded with silence, long cues accelerated <=1.35x then trimmed
        with a warning. Empty overlap windows are omitted explicitly. A failed or
        cancelled run never replaces the previous output.
        """
        path, output = Path(srt_path), Path(output_path)
        if output.suffix.lower() != ".wav":
            raise ValueError("Aligned SRT output must use .wav")
        if path.stat().st_size > 4 * 1024 * 1024:
            raise AlignmentError("SRT exceeds the 4 MiB input limit")
        with path.open("r", encoding="utf-8-sig") as stream:
            contents = stream.read(4 * 1024 * 1024 + 1)
        cues = cue_windows(parse_srt(contents))
        speed, pitch = _number(speed, "speed", .5, 2), _number(pitch, "pitch", -12, 12)
        self._provider(provider)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.track_timeout
        warnings, records, providers = [], [], set()
        temporary = None
        silence = b"\x00" * (SAMPLE_RATE * 2)

        def checkpoint():
            check_cancel(self.cancel)
            if time.monotonic() > deadline:
                raise TTSTimeout("SRT speech track exceeded its bounded deadline")

        def write_silence(destination, frames):
            while frames > 0:
                checkpoint()
                count = min(SAMPLE_RATE, frames)
                destination.writeframesraw(silence[:count * 2])
                frames -= count

        previous_deadline = self._deadline
        self._deadline = deadline
        try:
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".tts-track-", suffix=".wav", delete=False) as stream:
                temporary = Path(stream.name)
            with wave.open(str(temporary), "wb") as track, tempfile.TemporaryDirectory(prefix=".tts-fit-", dir=self.cache_dir) as directory:
                track.setnchannels(1)
                track.setsampwidth(2)
                track.setframerate(SAMPLE_RATE)
                written = 0
                for position, cue in enumerate(cues):
                    checkpoint()
                    self._emit(stage="tts_cue", provider=provider, current=position + 1, total=len(cues))
                    start_frame = cue["start_ms"] * SAMPLE_RATE // 1000
                    if start_frame > written:
                        write_silence(track, start_frame - written)
                        written = start_frame
                    base = {"index": cue["index"], "start_ms": cue["start_ms"], "end_ms": cue["end_ms"],
                            "window_ms": cue["window_ms"]}
                    if not cue["window_ms"]:
                        warning = f"Cue {cue['index']}: no window before next cue; omitted."
                        warnings.append(warning)
                        records.append({**base, "provider": None, "voice": None, "skipped": True, "warnings": [warning]})
                        continue
                    result = self._speech(cue["text"], voice, provider, speed, pitch, bool(allow_fallback))
                    checkpoint()
                    providers.add(result["provider"])
                    fit = fit_cue(result["duration_ms"], cue["window_ms"], max_speed=self.max_fit_speed)
                    fitted = Path(directory) / "fitted.wav"
                    self._canonical(result["path"], fitted, fit_filter=cue_audio_filter(cue["window_ms"], speed=fit["speed"]))
                    window_frames = cue["window_ms"] * SAMPLE_RATE // 1000
                    copied = 0
                    with wave.open(str(fitted), "rb") as segment:
                        while copied < window_frames:
                            checkpoint()
                            data = segment.readframes(min(SAMPLE_RATE, window_frames - copied))
                            if not data:
                                break
                            track.writeframesraw(data)
                            copied += len(data) // 2
                    write_silence(track, window_frames - copied)
                    written += window_frames
                    cue_warnings = [*result["warnings"], *fit["warnings"]]
                    warnings.extend(f"Cue {cue['index']}: {warning}" for warning in cue_warnings)
                    records.append({**base, "provider": result["provider"], "voice": result["voice"],
                                    "fit_speed": fit["speed"], "trimmed": fit["trimmed"], "skipped": False,
                                    "duration_ms": fit["duration_ms"], "warnings": cue_warnings})
                duration_frames = max(cue["end_ms"] for cue in cues) * SAMPLE_RATE // 1000
                write_silence(track, max(0, duration_frames - written))
            checkpoint()
            os.replace(temporary, output)
            return {"path": str(output), "provider": next(iter(providers)) if len(providers) == 1 else "mixed",
                    "warnings": list(dict.fromkeys(warnings)), "cues": records}
        finally:
            self._deadline = previous_deadline
            if temporary is not None and temporary.exists():
                temporary.unlink()
