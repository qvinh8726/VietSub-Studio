"""FFmpeg-only render pipeline. Optional TTS/separation imports happen on demand."""
from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading

from . import process
from .options import normalize
from .audio import track_audio_effects

_ENCODERS = {}
_ENCODER_LOCK = threading.Lock()
_PROBES = OrderedDict()
_PROBE_LOCK = threading.Lock()


def probe_media(path, ffprobe):
    """Cache verified metadata by file identity; never cache failures or missing files."""
    resolved = Path(path).resolve(strict=True)
    stat = resolved.stat()
    binary = str(ffprobe)
    binary_mtime = os.stat(ffprobe).st_mtime_ns if ffprobe and os.path.isfile(ffprobe) else 0
    key = (os.path.normcase(str(resolved)), stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, binary, binary_mtime)
    with _PROBE_LOCK:
        if key in _PROBES:
            _PROBES.move_to_end(key)
            return dict(_PROBES[key])
        result = _probe_media(resolved, ffprobe)
        _PROBES[key] = result
        while len(_PROBES) > 128:
            _PROBES.popitem(last=False)
        return dict(result)


def _probe_media(path, ffprobe):
    if not ffprobe:
        raise RuntimeError("Auto Edit cần FFprobe để xác minh độ phân giải và thời lượng.")
    raw = process.capture([ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
                           "-format_whitelist", "mov,matroska,webm,wav,mp3,flac,aac", "-show_streams", "-show_format", "-of", "json", str(path)])
    payload = json.loads(raw)
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = any(stream.get("codec_type") == "audio" for stream in streams)
    duration = video.get("duration") or payload.get("format", {}).get("duration")
    try:
        duration = float(duration)
    except (ValueError, TypeError):
        duration = 0
    if not math.isfinite(duration) or duration <= 0 or duration > 12 * 3600:
        raise ValueError("Media không có thời lượng hợp lệ (tối đa 12 giờ).")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    sar_text = video.get("sample_aspect_ratio") or "1:1"
    try:
        numerator, denominator = (int(number) for number in sar_text.split(":"))
        sar = numerator / denominator
        if not math.isfinite(sar) or not .1 <= sar <= 10:
            sar = 1
    except (ValueError, ZeroDivisionError):
        sar = 1
    square_width = max(2, round(width * sar)) if width else 0
    rotation = next((side.get("rotation") for side in video.get("side_data_list", [])
                     if isinstance(side, dict) and "rotation" in side), video.get("tags", {}).get("rotate", 0))
    try:
        rotation = round(float(rotation)) % 360
    except (ValueError, TypeError):
        rotation = 0
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Góc xoay video chưa được hỗ trợ; hãy chuẩn hoá nguồn trước.")
    display_width, display_height = (height, square_width) if rotation in (90, 270) else (square_width, height)
    audio_stream = next((stream for stream in streams if stream.get('codec_type') == 'audio'), {})
    try:
        audio_offset = float(audio_stream.get('start_time') or 0) - float(video.get('start_time') or 0)
        if not math.isfinite(audio_offset):
            audio_offset = 0
    except (TypeError, ValueError):
        audio_offset = 0
    return {"width": display_width, "height": display_height, "stored_width": width, "stored_height": height,
            "square_width": square_width, "rotation": rotation, "duration": duration, "has_audio": audio,
            "audio_offset": audio_offset}


def detect_encoders(ffmpeg, *, refresh=False):
    """A compiled-in encoder is not enough: verify actual hardware initialization."""
    if not ffmpeg:
        return {"h264_nvenc": False, "libx264": False}
    key = (str(ffmpeg), os.stat(ffmpeg).st_mtime_ns if os.path.isfile(ffmpeg) else 0)
    with _ENCODER_LOCK:
        cached = _ENCODERS.get(key)
        if cached is not None and not refresh:
            return dict(cached)
        found = {}
        for encoder in ("libx264", "h264_nvenc"):
            try:
                process.capture([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                 "color=c=black:s=128x128:r=1:d=1", "-frames:v", "1", "-an", "-c:v", encoder,
                                 "-pix_fmt", "yuv420p", "-f", "null", "-"], timeout=12)
                found[encoder] = True
            except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
                found[encoder] = False
        _ENCODERS[key] = found
        return dict(found)


def progress_reader(duration, update, *, phase="render", offset=0, scale=100):
    state = {"fps": 0.0, "percent": offset}
    def read(line):
        key, _, raw = line.partition("=")
        try:
            if key == "fps":
                value = float(raw)
                if math.isfinite(value):
                    state["fps"] = max(0, value)
            if key in {"out_time_us", "out_time_ms"}:
                # FFmpeg's historical out_time_ms is also microseconds.
                elapsed = max(0, float(raw) / 1_000_000)
                state["percent"] = max(state["percent"], min(offset + scale * .999, offset + elapsed / duration * scale))
            if key in {"progress", "out_time_us"}:
                update(phase=phase, percent=round(state["percent"], 1), fps=round(state["fps"], 1))
        except (ValueError, OverflowError):
            pass
    return read


def tts_progress(update):
    """Forward cue counters and diagnostics without resetting progress on a poll."""
    def emit(values):
        fields = {key: values[key] for key in ("current", "completed", "total", "percent", "provider", "message", "level")
                  if key in values}
        fields.update(phase=values.get("stage", "tts"), fps=0)
        update(**fields)
    return emit


def _encoder_args(encoder):
    return ["-c:v", encoder, "-preset", "p4", "-cq", "23"] if encoder == "h264_nvenc" else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _source_audio_timeline(metadata):
    offset = metadata.get('audio_offset', 0)
    chain = ['asetpts=PTS-STARTPTS']
    if offset > .0001:
        chain.append(f"adelay={round(offset * 1000)}:all=1")
    elif offset < -.0001:
        chain += [f"atrim=start={-offset}", 'asetpts=PTS-STARTPTS']
    chain += ['apad', f"atrim=duration={metadata['duration']}"]
    return ','.join(chain)


def _audio_graph(has_audio, bg_index, tts_index, options, duration, source_timeline=None, *, tts_pitch_speed=None):
    filters = []
    background = f"{bg_index}:a:0" if bg_index is not None else "0:a:0" if has_audio else None
    voice = f"{tts_index}:a:0" if tts_index is not None else None
    if background == '0:a:0' and source_timeline and options['audio_mode'] != 'mute':
        filters.append(f'[0:a:0]{source_timeline}[sourceaudio]')
        background = 'sourceaudio'
    if options["audio_mode"] == "mute":
        background = None
    if voice and "tts_change_pitch" in options:
        reference = options.get("speed", 1) if tts_pitch_speed is None else tts_pitch_speed
        effects = track_audio_effects({**options, "speed": 1}, track="tts", pitch_speed=reference)
        filters.append(f"[{voice}]{effects}[pitched_voice]")
        voice = "pitched_voice"
    if voice and background and not options.get("ducking_enabled", True):
        filters += [f"[{background}]aresample=48000,volume={options['bgm_volume']}[bgm]",
                    f"[{voice}]aresample=48000,apad,atrim=duration={duration}[voice]",
                    f"[bgm][voice]amix=inputs=2:duration=longest:normalize=0,apad,atrim=duration={duration}[aout]"]
    elif voice and background:
        filters += [f"[{background}]aresample=48000,volume={options['bgm_volume']}[bgm]",
                    f"[{voice}]aresample=48000,apad,atrim=duration={duration},asplit=2[side][voice]",
                    "[bgm][side]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=250[ducked]",
                    f"[ducked][voice]amix=inputs=2:duration=longest:normalize=0,apad,atrim=duration={duration}[aout]"]
    elif voice:
        filters += [f"[{voice}]aresample=48000,apad,atrim=duration={duration}[aout]"]
    elif background:
        volume = options["bgm_volume"] if options["audio_mode"] == "instrumental" else 1
        filters += [f"[{background}]aresample=48000,volume={volume},apad,atrim=duration={duration}[aout]"]
    if filters:
        # Speech pitch is already applied. Change timeline speed once after the
        # mix, preserving original/BGM pitch, silence and delayed-start timing.
        # Payloads without the alias retain the old global transform here.
        filters[-1] = filters[-1].replace("[aout]", "[source_mix]")
        output_duration = duration / options.get("speed", 1)
        effects = track_audio_effects(options, track="background")
        filters.append(f"[source_mix]{effects},apad,atrim=duration={output_duration}[aout]")
    return filters, "[aout]" if filters else None


def _audio_file(source, target, options, source_duration, output_duration, *, ffmpeg, cancel,
                update, timeout, phase, timeline=None, bounded=False, track="background", tts_pitch_speed=None):
    """Export one track with the same effects and timebase as the render mix."""
    chain = timeline or f"asetpts=PTS-STARTPTS,apad,atrim=duration={source_duration}"
    effects = track_audio_effects(options, track=track, pitch_speed=tts_pitch_speed)
    chain += f",{effects},apad,atrim=duration={output_duration}"
    input_args = ["-t", str(source_duration)] if bounded else []
    process.run([ffmpeg, "-hide_banner", "-y", *input_args, "-i", str(source), "-vn", "-af", chain,
                 "-ar", "48000", "-c:a", "pcm_s16le", "-t", str(output_duration),
                 "-progress", "pipe:1", "-nostats", str(target)], cancel=cancel, timeout=timeout,
                on_line=progress_reader(output_duration, update, phase=phase))


def execute(task_id, project, options, action, *, ffmpeg, ffprobe, fonts_dir, cancel, update,
            runtime_python=None, capcut_config=None, _tts_pitch_speed=None, _tts_cache_dir=None):
    if not ffmpeg:
        raise RuntimeError("Chưa tìm thấy FFmpeg trong PATH. Cài FFmpeg trước khi Auto Edit.")
    from .filters import build_video_graph
    from .subtitles import write_ass
    options = normalize(options, inherited_crop=project.get("crop_box"))
    source = project["assets"]["video"]
    srt = project["assets"]["translated_srt"]
    root = Path(project["project_dir"])
    work = root / ".vietsub-editor" / task_id
    process.check_cancel(cancel)
    metadata = probe_media(source, ffprobe)
    if not 2 <= metadata["width"] <= 16384 or not 2 <= metadata["height"] <= 16384:
        raise ValueError("Độ phân giải video không được hỗ trợ.")
    source_duration = metadata["duration"]
    duration = source_duration / options["speed"]
    timeout = min(24 * 3600, max(300, max(duration, source_duration) * 10 + 120))
    stem = Path(source).stem
    warnings = []
    tts_path = None
    tts_cue_timings = {}
    final_paths = []
    from vietsub import manifest
    manifest._safe_path(str(work))
    work.mkdir(parents=True, exist_ok=False)
    try:
        process.check_cancel(cancel)
        if action == "audio_preview":
            preview_input = source
            preview_source = "original"
            preview_seconds = 3.0
            source_seconds = min(source_duration, preview_seconds * options["speed"])
            timeline = _source_audio_timeline({**metadata, "duration": source_seconds})
            if options["tts_enabled"]:
                from .providers import service as create_tts_service
                from .alignment import cue_windows, parse_srt
                from .tts import speech_text
                # Use the same speakable cue/normalization as a full track, so
                # markup and empty leading cues do not bypass its raw cache.
                source_srt = Path(srt)
                if source_srt.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError("SRT vượt giới hạn 4 MiB.")
                cues = cue_windows(parse_srt(source_srt.read_text(encoding="utf-8-sig"), allow_empty=True))
                text = next((clean for cue in cues if cue["window_ms"]
                             if (clean := speech_text(cue["text"]))), "")[:250].strip()
                if not text:
                    raise ValueError("Phụ đề không có câu chứa chữ hoặc số để nghe thử.")
                service = create_tts_service(ffmpeg, ffprobe, str(_tts_cache_dir or root / ".vietsub-tts-cache"),
                                             runtime_python=runtime_python, config=capcut_config, cancel=cancel,
                                             progress=tts_progress(update))
                preview_input = work / "preview-voice.wav"
                update(phase="tts_preview", percent=0, fps=0)
                generated = service.preview(text, options["tts_voice"], str(preview_input),
                                            provider=options["tts_provider"], speed=options["tts_speed"],
                                            pitch=options["tts_pitch"], allow_fallback=options["tts_fallback"])
                warnings += generated.get("warnings", [])
                preview_source = "tts"
                source_seconds = preview_seconds * options["speed"]
                timeline = None
            elif not metadata["has_audio"]:
                raise ValueError("Video nguồn không có âm thanh để nghe thử; hãy bật TTS và chọn giọng đọc.")
            temporary = work / "preview.wav"
            _audio_file(preview_input, temporary, options, source_seconds, preview_seconds,
                        ffmpeg=ffmpeg, cancel=cancel, update=update, timeout=60,
                        phase="audio_preview", timeline=timeline, bounded=True,
                        track="tts" if preview_source == "tts" else "background")
            verified = probe_media(temporary, ffprobe)
            if abs(verified["duration"] - preview_seconds) > .02:
                raise RuntimeError("Audio nghe thử không đủ ba giây; hãy thử lại.")
            preview_dir = root / ".vietsub-editor-previews"
            manifest._safe_path(str(preview_dir))
            preview_dir.mkdir(exist_ok=True)
            result = preview_dir / f"{task_id}.wav"
            process.check_cancel(cancel); os.replace(temporary, result)
            final_paths.append(result)
            process.check_cancel(cancel)
            return {"output_path": str(result), "output_kind": "audio", "assets": {},
                    "duration": preview_seconds, "preview_source": preview_source, "warnings": warnings}
        if action == "extract_audio":
            if not metadata["has_audio"]:
                raise ValueError("Video nguồn không có track âm thanh để tách.")
            extension = options["audio_format"]
            temporary = work / f"extracted.{extension}"
            codec = ["-c:a", "pcm_s16le"] if extension == "wav" else ["-c:a", "libmp3lame", "-q:a", "2"]
            process.run([ffmpeg, "-hide_banner", "-y", "-i", source, "-vn", "-af", _source_audio_timeline(metadata) + "," + track_audio_effects(options, track="background") + f",apad,atrim=duration={duration}",
                         *codec, "-progress", "pipe:1", "-nostats", str(temporary)],
                        cancel=cancel, timeout=timeout, on_line=progress_reader(duration, update, phase="extract_audio"))
            result = root / f"{stem}.extracted.{task_id[:10]}.{extension}"
            probe_media(temporary, ffprobe)
            process.check_cancel(cancel); os.replace(temporary, result)
            final_paths.append(result)
            process.check_cancel(cancel)
            return {"output_path": str(result), "output_kind": "audio", "assets": {"extracted_audio": str(result)}, "warnings": warnings}
        if action == "tts" or options["tts_enabled"]:
            from .providers import service as create_tts_service
            update(phase="tts", percent=0, fps=0)
            service = create_tts_service(ffmpeg, ffprobe, str(_tts_cache_dir or root / ".vietsub-tts-cache"), runtime_python=runtime_python,
                                        config=capcut_config, cancel=cancel, progress=tts_progress(update))
            tts_path = work / "voice.wav"
            generated = service.synthesize_srt(srt, str(tts_path), voice=options["tts_voice"],
                                               provider=options["tts_provider"], speed=options["tts_speed"],
                                               pitch=options["tts_pitch"], allow_fallback=options["tts_fallback"])
            warnings += generated.get("warnings", [])
            tts_cue_timings = {
                cue["index"]: cue["duration_ms"]
                for cue in (generated.get("cues") or [])
                if isinstance(cue, dict) and not cue.get("skipped") and cue.get("duration_ms")
            }
            if action == "tts":
                processed = work / "voice-edited.wav"
                _audio_file(tts_path, processed, options, source_duration, duration, ffmpeg=ffmpeg,
                            cancel=cancel, update=update, timeout=timeout, phase="tts_audio", track="tts", tts_pitch_speed=_tts_pitch_speed)
                result = root / f"{stem}.tts.{task_id[:10]}.wav"
                probe_media(processed, ffprobe)
                process.check_cancel(cancel); os.replace(processed, result)
                final_paths.append(result)
                process.check_cancel(cancel)
                return {"output_path": str(result), "output_kind": "audio", "assets": {"tts_audio": str(result)}, "warnings": warnings}
        if action != "render":
            raise ValueError("Tác vụ Auto Edit không được hỗ trợ.")
        instrumental = None
        if options["audio_mode"] == "instrumental":
            if not metadata["has_audio"]:
                raise ValueError("Video nguồn không có âm thanh để tách vocal/BGM.")
            from . import separation
            update(phase="separation", percent=0, fps=0)
            extracted = work / "original.wav"
            process.run([ffmpeg, "-hide_banner", "-y", "-i", source, "-vn", "-af", _source_audio_timeline(metadata),
                         "-c:a", "pcm_s16le", str(extracted)],
                        cancel=cancel, timeout=timeout)
            separated = separation.separate(str(extracted), str(work / "stems"), runtime_python=runtime_python or None,
                                            model_dir=(capcut_config or {}).get("separation_model_dir"),
                                            model_filename=(capcut_config or {}).get("separation_model", "UVR-MDX-NET-Inst_HQ_3.onnx"),
                                            cancel=cancel)
            instrumental = separated.get("instrumental") if isinstance(separated, dict) else separated
            if not instrumental or not Path(instrumental).is_file():
                raise RuntimeError("Runtime không trả về instrumental hợp lệ; không trộn lại giọng gốc.")
            warnings.append("Tách stem phụ thuộc model: BGM/SFX có thể bị suy giảm; hãy nghe kiểm tra.")
        ass = None
        if options["subtitle_enabled"] or options["banner_text"]:
            # Use a local relative name in filtergraph to avoid Windows path parsing.
            ass = write_ass(srt, str(work / "subtitles.ass"), metadata["width"], metadata["height"], options,
                            cue_timings=tts_cue_timings or None)
        # FFmpeg autorotation handles the full display matrix, including selfie
        # reflections; do not reduce that matrix to a scalar angle/manual flip.
        inputs = ["-i", source]
        input_count = 1
        watermark_index = None
        if options["watermark_enabled"]:
            watermark_index = input_count; input_count += 1
            inputs += ["-i", options["watermark_path"]]
        background_index = None
        if instrumental:
            background_index = input_count; input_count += 1
            inputs += ["-i", str(instrumental)]
        tts_index = None
        if tts_path:
            tts_index = input_count; input_count += 1
            inputs += ["-i", str(tts_path)]
        graph = build_video_graph(metadata["width"], metadata["height"], options,
                                  ass_path="subtitles.ass" if ass else None,
                                  watermark_input=watermark_index, fonts_dir=fonts_dir)
        # Input frames have already been display-matrix transformed by FFmpeg.
        # Resize anamorphic frames to the same square-pixel geometry used by UI.
        transform = [f"scale={metadata['width']}:{metadata['height']}", 'setsar=1', 'setpts=PTS-STARTPTS']
        video_graph = '[0:v:0]' + ','.join(transform) + '[oriented];' + graph['filter_complex'].replace('[0:v:0]', '[oriented]', 1)
        audio_filters, audio_label = _audio_graph(metadata["has_audio"], background_index, tts_index, options, source_duration,
                                                  source_timeline=_source_audio_timeline(metadata), tts_pitch_speed=_tts_pitch_speed)
        full_graph = video_graph + (";" + ";".join(audio_filters) if audio_filters else "")
        available = detect_encoders(ffmpeg)
        requested = options["encoder"]
        encoder = "h264_nvenc" if available["h264_nvenc"] and requested != "libx264" else "libx264"
        if requested == "h264_nvenc" and not available["h264_nvenc"]:
            warnings.append("NVENC không khả dụng; chuyển sang libx264.")
        if not available.get(encoder):
            raise RuntimeError("FFmpeg không có encoder H.264 hoạt động (NVENC/libx264).")
        temporary = work / "render.mp4"
        def render_with(selected):
            command = [ffmpeg, "-hide_banner", "-y", "-filter_complex_threads", "1", *inputs,
                       "-filter_complex", full_graph, "-map", graph["output_label"]]
            command += ["-map", audio_label, "-c:a", "aac", "-b:a", "192k"] if audio_label else ["-an"]
            command += [*_encoder_args(selected), "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-metadata:s:v:0", "rotate=0", "-t", str(duration),
                        "-progress", "pipe:1", "-nostats", str(temporary)]
            update(phase="render", percent=0, fps=0, encoder=selected)
            process.run(command, cwd=str(work), cancel=cancel, timeout=timeout,
                        on_line=progress_reader(duration, update))
        try:
            render_with(encoder)
        except RuntimeError:
            if encoder != "h264_nvenc" or (cancel and cancel.is_set()):
                raise
            warnings.append("Render NVENC thất bại; đã thử lại bằng libx264.")
            encoder = "libx264"; render_with(encoder)
        rendered = probe_media(temporary, ffprobe)
        if (rendered['width'], rendered['height']) != (graph['width'], graph['height']):
            raise RuntimeError("Kích thước video render không khớp cấu hình; không công bố output.")
        if abs(rendered["duration"] - duration) > max(1, duration * .01):
            raise RuntimeError("Video render bị thiếu thời lượng; không công bố output.")
        output = root / f"{stem}.edited.{task_id[:10]}.mp4"
        assets = {"edited_video": str(output)}
        if tts_path:
            processed = work / "voice-edited.wav"
            _audio_file(tts_path, processed, options, source_duration, duration, ffmpeg=ffmpeg,
                        cancel=cancel, update=update, timeout=timeout, phase="tts_audio", track="tts", tts_pitch_speed=_tts_pitch_speed)
            voice_output = root / f"{stem}.tts.{task_id[:10]}.wav"
            process.check_cancel(cancel); os.replace(processed, voice_output)
            final_paths.append(voice_output); assets["tts_audio"] = str(voice_output)
        process.check_cancel(cancel); os.replace(temporary, output); final_paths.append(output)
        process.check_cancel(cancel)
        return {"output_path": str(output), "output_kind": "video", "assets": assets,
                "warnings": list(dict.fromkeys(warnings)), "encoder": encoder,
                "width": rendered["width"], "height": rendered["height"], "duration": duration}
    except BaseException:
        # Roll back only files published by this task; earlier good outputs and
        # inputs keep their own filenames and are never in final_paths.
        for published in final_paths:
            if published.resolve().is_relative_to(root.resolve()) and task_id[:10] in published.name:
                published.unlink(missing_ok=True)
        raise
    finally:
        # Only this verified task-owned temporary directory, never project/source.
        if work.parent == root / ".vietsub-editor" and work.name == task_id:
            shutil.rmtree(work, ignore_errors=True)
