"""Flask adapter for Auto Edit. The legacy app remains the authority for projects."""
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import struct
import sys
import tempfile
from threading import Event, Thread
import time
import uuid
import zlib

from flask import Blueprint, jsonify, request, send_file

from vietsub import glossary as translation_glossary
from vietsub import manifest, publish, redaction, srt_utils, storage
from .jobs import EditorJobs, TERMINAL
from .options import DEFAULTS, FONTS, PRESETS, SUBTITLE_ANIMATIONS, normalize
from . import renderer, runtime


def _png(data):
    if len(data) < 45 or len(data) > 8 * 1024 * 1024 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Watermark phải là PNG hợp lệ, tối đa 8 MiB.")
    offset = 8; header = False; pixels = False; ended = False
    while offset + 12 <= len(data):
        size = struct.unpack(">I", data[offset:offset+4])[0]
        kind = data[offset+4:offset+8]
        body = data[offset+8:offset+8+size]
        if size > 8 * 1024 * 1024 or offset+size+12 > len(data):
            raise ValueError("PNG bị cắt hoặc chunk quá lớn.")
        crc = struct.unpack(">I", data[offset+8+size:offset+12+size])[0]
        if zlib.crc32(kind + body) & 0xffffffff != crc:
            raise ValueError("PNG có CRC không hợp lệ.")
        if not header:
            if kind != b"IHDR" or size != 13:
                raise ValueError("PNG thiếu IHDR.")
            width, height = struct.unpack(">II", body[:8])
            if not 1 <= width <= 4096 or not 1 <= height <= 4096:
                raise ValueError("Ảnh PNG chỉ hỗ trợ tối đa 4096 × 4096.")
            header = True
        if kind == b"IDAT":
            pixels = pixels or size > 0
        if kind == b"IEND":
            ended = size == 0 and offset + size + 12 == len(data)
            break
        offset += size+12
    if not ended or not pixels:
        raise ValueError("PNG chưa đầy đủ dữ liệu ảnh.")


class EditorAPI:
    def __init__(self, app_runtime):
        self.app = app_runtime
        self.jobs = EditorJobs()
        self.blueprint = Blueprint("editor", __name__)
        self._routes()

    def config(self):
        return self.app.load_app_config()

    def _project(self, job_id, *, require_ready=False):
        job, result = self.app.project_for_job(job_id)
        if require_ready:
            if (job.get("status") != "success" or not job.get("result_ready")
                    or job_id in {self.app.current_job_id, self.app.prefetch_job_id}):
                raise RuntimeError("Chỉ Auto Edit dự án VietSub đã hoàn tất và không còn xử lý.")
            self.app.validate_project_result(result)
        value = manifest.snapshot(result, video_id=job.get("video_id") or "", source_url=job.get("source_url") or "",
                                  status=job.get("status") or "error", geometry_fallback=self.app.project_geometry_fallback(job))
        if require_ready:
            for key in ("video", "translated_srt"):
                if not value["assets"][key] or not storage.safe_regular(value["assets"][key]):
                    raise RuntimeError("Video hoặc SRT không còn khả dụng; không thể Auto Edit.")
        return job, result, value

    def _options(self, value, submitted=None):
        options = normalize(submitted, base=value.get("editor_options"), inherited_crop=value.get("crop_box"))
        # HTTP paths are never trusted: only a PNG registered via this project's
        # upload endpoint may be used by FFmpeg, not a random local/network file.
        if options["watermark_path"]:
            known = value.get("editor_options", {}).get("watermark_path", "")
            if options["watermark_path"] != known or (options["watermark_enabled"] and not storage.safe_regular(known)):
                raise ValueError("Watermark chưa được đăng ký cho dự án; hãy tải ảnh PNG lại.")
        return options

    def _signed(self, task):
        task = copy.deepcopy(task)
        task.pop("assets", None)
        task.pop("_protected_paths", None)
        path = task.get("output_path") or ""
        if task.get("status") == "success" and path and storage.safe_regular(path):
            text = f"editor|{task['id']}|{path}".encode("utf-8")
            signature = hmac.new(self.app.API_TOKEN.encode("ascii"), text, hashlib.sha256).hexdigest()
            task["output_url"] = f"/api/editor/jobs/{task['id']}/output?signature={signature}"
        else:
            task["output_url"] = ""
        return task

    def _progress_update(self, identifier, update):
        """Bridge worker diagnostics to the same live log used by the Nhật ký tab."""
        last_message = None
        def report(**values):
            nonlocal last_message
            update(**values)
            message = values.get("message")
            if message and message != last_message:
                level = "error" if values.get("level") in {"error", "warning"} else "system"
                self.app.log_to_app(f"[Auto Edit {identifier[:8]}] {message}", level)
                last_message = message
        return report

    def _finish(self, result, manifest_path, task, state):
        with manifest.LOCK:
            value = manifest.snapshot(result)
            stage = {"status": state, "task_id": task["id"], "action": task["action"],
                     "updated_at": int(time.time()), "error": task.get("error", ""),
                     "warnings": task.get("warnings", []), "percent": task.get("percent", 0),
                     "output_path": task.get("output_path", ""), "output_kind": task.get("output_kind", "")}
            for key in ("duration", "preview_source"):
                if key in task:
                    stage[key] = task[key]
            value["stages"]["editor"] = stage
            if state == "success":
                value["assets"].update(task.get("assets", {}))
                stale = dict(value.get("srt_stale_assets") or {})
                if task.get("action") == "render":
                    value["last_editor_render"] = copy.deepcopy(stage)
                    stale["edited_video"] = False
                    if (task.get("assets") or {}).get("tts_audio"):
                        stale["tts_audio"] = False
                elif task.get("action") == "tts":
                    stale["tts_audio"] = False
                if stale:
                    value["srt_stale_assets"] = stale
            value["updated_at"] = int(time.time())
            manifest.write_atomic(manifest_path, value)
        if state == "success" and task["action"] == "audio_preview":
            # Preview WAVs are a bounded cache, not durable rendered assets.
            # Retain the newest eight (including the just-published result).
            directory = Path(result["project_dir"]) / ".vietsub-editor-previews"
            try:
                manifest._safe_path(str(directory))
                paths = [str(path) for path in directory.glob("*.wav")
                         if len(path.stem) == 32 and all(c in "0123456789abcdef" for c in path.stem)
                         and str(path) != task.get("output_path")]
                inventory = storage.inventory(paths)
                stale = sorted(inventory, key=lambda path: inventory[path].st_mtime_ns, reverse=True)[7:]
                storage.delete_inventory({path: inventory[path] for path in stale})
            except (OSError, ValueError):
                pass  # A cache cleanup failure must not invalidate a committed result.
        self.app.log_to_app(f"[Auto Edit] {task['action']}: {state}" + (f" · {task.get('error')}" if state == "error" else ""),
                            "error" if state == "error" else "system")

    def _restored_task(self, job_id, result, value):
        current = self.jobs.for_project(job_id)
        if current:
            return current
        stage = value.get("stages", {}).get("editor", {})
        identifier = stage.get("task_id", "")
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            return None
        status = stage.get("status")
        interrupted = status in {"queued", "running"}
        if status not in TERMINAL and not interrupted:
            return None
        output = stage.get("output_path", "")
        if output:
            try:
                Path(output).resolve().relative_to(Path(result["project_dir"]).resolve())
            except (ValueError, TypeError):
                output = ""
        task = {"id": identifier, "job_id": job_id, "action": stage.get("action", "render"),
                "status": "error" if interrupted else status, "phase": "interrupted" if interrupted else status,
                "percent": 100 if status == "success" else 0, "fps": 0,
                "error": "App đã đóng khi Auto Edit đang chạy. Output cũ được giữ; hãy chạy lại." if interrupted else stage.get("error", ""),
                "warnings": stage.get("warnings", []), "output_path": output,
                "output_kind": stage.get("output_kind", ""), "restored": True}
        for key in ("duration", "preview_source"):
            if key in stage:
                task[key] = stage[key]
        self.jobs.restore(task)
        return task

    def _last_render(self, job_id, result, value):
        stage = value.get('last_editor_render') or {}
        if not isinstance(stage, dict):
            raise ValueError("Thông tin kết quả editor đã lưu không hợp lệ; giữ nguyên manifest để khôi phục.")
        path = value.get('assets', {}).get('edited_video') or ''
        if not path or not storage.safe_regular(path):
            return None
        try:
            Path(path).resolve().relative_to(Path(result['project_dir']).resolve())
        except ValueError:
            return None
        identifier = stage.get('task_id') or hashlib.sha256((job_id+'|'+path).encode('utf-8')).hexdigest()[:32]
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            return None
        task = {'id': identifier, 'job_id': job_id, 'action': 'render', 'status': 'success', 'phase': 'done',
                'percent': 100, 'fps': 0, 'error': '', 'warnings': stage.get('warnings', []),
                'output_path': path, 'output_kind': 'video', 'restored': True, '_historical': True}
        self.jobs.restore(task)
        return self._signed(task)

    def _body(self, fields, required=()):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - set(fields) or any(key not in data for key in required):
            raise ValueError("JSON Auto Edit không hợp lệ hoặc có trường không hỗ trợ.")
        return data

    def _srt_paths(self, result, value):
        project_dir = Path(result["project_dir"]).resolve()
        translated_srt = value["assets"].get("translated_srt") or ""
        if not translated_srt or not storage.safe_regular(translated_srt):
            raise RuntimeError("File phụ đề Việt không còn khả dụng.")
        try:
            Path(translated_srt).resolve().relative_to(project_dir)
        except ValueError as error:
            raise RuntimeError("File phụ đề Việt nằm ngoài thư mục dự án.") from error
        raw_srt = value["assets"].get("raw_srt") or ""
        if raw_srt:
            try:
                if not storage.safe_regular(raw_srt):
                    raw_srt = ""
                else:
                    Path(raw_srt).resolve().relative_to(project_dir)
            except ValueError:
                raw_srt = ""
        return translated_srt, raw_srt

    def _srt_lock_reason(self, job_id, translated_srt):
        if self.jobs.active(job_id):
            return "Không thể sửa phụ đề trong khi Auto Edit đang chạy tác vụ cho dự án này."
        if job_id in {self.app.current_job_id, self.app.prefetch_job_id}:
            return "Không thể sửa phụ đề trong khi VietSub đang xử lý dự án này."
        uploader = getattr(self.app, "uploader_api", None)
        if uploader is not None:
            protected = {storage.canonical(path) for path in uploader.protected_paths() if path}
            if storage.canonical(translated_srt) in protected:
                return "Không thể sửa phụ đề trong khi Publish Hub đang sử dụng file SRT này."
        return ""

    def _attach_raw(self, cues, raw_srt):
        raw_cues = []
        if raw_srt:
            try:
                raw_cues = srt_utils.read_srt_file(raw_srt, allow_empty=True)
            except (OSError, ValueError):
                raw_cues = []
        matched = srt_utils.match_raw_cues(cues, raw_cues)
        enriched = [{**cue, "raw_text": raw_text} for cue, raw_text in zip(cues, matched)]
        return enriched, bool(raw_cues)

    def _check_admission_open(self):
        if self.app.shutdown_event.is_set():
            raise RuntimeError("App đang đóng.")
        update_lock = getattr(self.app, "update_install_lock", None)
        if update_lock is not None and update_lock.locked():
            raise RuntimeError("App đang chuẩn bị cài đặt bản cập nhật; hãy thử lại sau.")

    def _write_srt_and_manifest(self, result, value, translated_srt, write_fn):
        """Write SRT + manifest consistently; roll back SRT bytes if manifest commit fails."""
        manifest_path = manifest.path_for(result)
        manifest._safe_path(manifest_path)
        prior_bytes = None
        if storage.safe_regular(translated_srt):
            try:
                with open(translated_srt, "rb") as handle:
                    prior_bytes = handle.read(srt_utils.MAX_SRT_BYTES + 1)
            except OSError:
                prior_bytes = None
        saved_cues = write_fn()
        now = int(time.time())
        value["srt_manually_edited"] = True
        value["srt_updated_at"] = now
        value["updated_at"] = now
        assets = value.get("assets") if isinstance(value.get("assets"), dict) else {}
        value["srt_stale_assets"] = {
            "edited_video": bool(assets.get("edited_video")),
            "tts_audio": bool(assets.get("tts_audio")),
        }
        try:
            manifest.write_atomic(manifest_path, value)
        except Exception:
            if prior_bytes is not None:
                try:
                    tmp = None
                    with tempfile.NamedTemporaryFile(
                        mode="wb", delete=False, prefix=".srt-rb-", suffix=".tmp",
                        dir=os.path.dirname(os.path.abspath(translated_srt)),
                    ) as handle:
                        tmp = handle.name
                        handle.write(prior_bytes)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, translated_srt)
                except OSError:
                    pass
            raise
        return saved_cues

    def _prune_global_previews(self, keep_output_path=""):
        directory = Path(self.app.CONFIG_DIR) / "editor-previews"
        try:
            manifest._safe_path(str(directory))
            if not directory.is_dir():
                return
            protected = self.jobs.protected_paths()
            cutoff = time.time() - 86400
            paths = [
                str(path) for path in directory.glob("*.wav")
                if len(path.stem) == 32 and all(c in "0123456789abcdef" for c in path.stem)
                and str(path) != keep_output_path
                and os.path.normcase(os.path.abspath(str(path))) not in protected
            ]
            inventory = storage.inventory(paths)
            ordered = sorted(inventory, key=lambda p: inventory[p].st_mtime_ns, reverse=True)
            stale = [p for idx, p in enumerate(ordered) if idx >= 15 or inventory[p].st_mtime < cutoff]
            if stale:
                storage.delete_inventory({p: inventory[p] for p in stale})
        except (OSError, ValueError):
            pass

    def enqueue_project_task(
        self,
        job_id,
        action,
        submitted_options=None,
        *,
        allow_preset_watermark=False,
        pipeline_meta=None,
        after_terminal=None,
    ):
        """Admit, reserve in manifest, and enqueue an Auto Edit task (shared by HTTP and Auto Pipeline)."""
        if (not isinstance(job_id, str) or not isinstance(action, str)
                or action not in ("render", "tts", "extract_audio", "audio_preview")):
            raise ValueError("Job ID/action Auto Edit không hợp lệ.")
        checked_runtime_python = None
        for _attempt in range(2):
            runtime_probe_python = None
            with self.app.operation_admission_lock, self.app.queue_state_lock, manifest.LOCK:
                self._check_admission_open()
                _, result, value = self._project(job_id, require_ready=True)
                if allow_preset_watermark and isinstance(submitted_options, dict):
                    candidate_wm = submitted_options.get("watermark_path") or ""
                    if candidate_wm and storage.safe_regular(candidate_wm):
                        assets_root = (Path(result["project_dir"]) / ".vietsub-assets").resolve()
                        try:
                            Path(candidate_wm).resolve().relative_to(assets_root)
                            if not isinstance(value.get("editor_options"), dict):
                                value["editor_options"] = {}
                            value["editor_options"]["watermark_path"] = candidate_wm
                        except ValueError:
                            pass
                options = self._options(value, submitted_options if submitted_options is not None else {})
                if action == "tts" and not options["tts_voice"].strip():
                    raise ValueError("Hãy chọn giọng TTS trước khi tạo audio.")
                ffmpeg, ffprobe = self.app.resolve_ffmpeg_executable(), self.app.resolve_ffprobe_executable()
                if not ffmpeg or not ffprobe:
                    raise RuntimeError("Auto Edit cần FFmpeg và FFprobe trong PATH.")
                config = self.config()
                if action == "render" and options["audio_mode"] == "instrumental":
                    model_dir = config.get("editor_separation_model_dir") or ""
                    filename = config.get("editor_separation_model") or "UVR-MDX-NET-Inst_HQ_3.onnx"
                    if not model_dir or not os.path.isfile(os.path.join(model_dir, filename)):
                        raise RuntimeError("Hãy cài model tách giọng và chọn thư mục model trong Runtime; app không tự tải model.")
                    rt_py = config.get("editor_runtime_python") or ""
                    if checked_runtime_python != rt_py:
                        runtime_probe_python = rt_py
                if runtime_probe_python is None:
                    directory = os.path.abspath(result["project_dir"])
                    manifest._safe_path(directory)
                    task_project = copy.deepcopy(value)
                    task_project["project_dir"] = directory

                    def execute(identifier, cancel, update):
                        return renderer.execute(
                            identifier, task_project, options, action, ffmpeg=ffmpeg, ffprobe=ffprobe,
                            fonts_dir=os.path.join(self.app.RESOURCE_DIR, "static", "fonts"),
                            cancel=cancel, update=self._progress_update(identifier, update),
                            runtime_python=config.get("editor_runtime_python"),
                            capcut_config={
                                **config,
                                "separation_model_dir": config.get("editor_separation_model_dir"),
                                "separation_model": config.get("editor_separation_model") or "UVR-MDX-NET-Inst_HQ_3.onnx",
                            },
                        )

                    target = manifest.path_for(result)
                    value["editor_options"] = options
                    identifier = uuid.uuid4().hex
                    now = int(time.time())
                    value["stages"]["editor"] = {"status": "queued", "action": action, "task_id": identifier, "updated_at": now}
                    if isinstance(pipeline_meta, dict):
                        stage_pipeline = copy.deepcopy(pipeline_meta)
                        stage_pipeline.update(editor_task_id=identifier, auto_edit="queued", updated_at=now)
                        value["stages"]["pipeline"] = stage_pipeline
                    if self.jobs.active(job_id):
                        raise RuntimeError("Dự án đã có tác vụ Auto Edit.")
                    return self.jobs.submit(
                        job_id, action, execute, identifier=identifier,
                        prepare=lambda task: manifest.write_atomic(target, value),
                        finish=lambda task, state: self._finish(result, target, task, state),
                        after_terminal=after_terminal,
                        protected_paths=[*task_project["assets"].values(), options.get("watermark_path")],
                    )
            # Run external subprocess probe outside all global admission/queue/manifest locks.
            if not runtime.availability(runtime_probe_python or None)["audio_separator"]["available"]:
                raise RuntimeError("Chưa có audio-separator trong runtime đã chọn; FFmpeg thuần vẫn khả dụng.")
            checked_runtime_python = runtime_probe_python
        raise RuntimeError("Cấu hình runtime thay đổi liên tục; hãy thử lại.")

    def _run_hook_api_call(self, runtime_config, messages, *, deadline_seconds=60.0):
        """Bounded synchronous OpenAI-compatible API call isolated from workflow_lock with light retry."""
        import inspect

        deadline = time.monotonic() + float(deadline_seconds)
        api_fn = self.app.call_openai_compatible_api
        try:
            sig = inspect.signature(api_fn)
            accepts_timeout = "timeout" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_timeout = False

        max_attempts = 2
        for attempt in range(max_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Gợi ý Hook AI quá thời gian chờ (60 giây).")
            state = {"response": None, "aborted": False}
            outcome = {}
            done = Event()

            def remember_response(resp):
                state["response"] = resp
                if state["aborted"]:
                    self.app.close_quietly(resp)

            call_kwargs = {"max_tokens": 512, "stream": False, "response_hook": remember_response}
            if accepts_timeout:
                call_kwargs["timeout"] = max(5.0, min(60.0, remaining))

            def worker():
                try:
                    outcome["value"] = api_fn(runtime_config, messages, **call_kwargs)
                except BaseException as error:
                    outcome["error"] = error
                finally:
                    done.set()

            Thread(target=worker, name="editor-suggest-hooks", daemon=True).start()
            while not done.wait(0.25):
                if self.app.shutdown_event.is_set() or time.monotonic() >= deadline:
                    state["aborted"] = True
                    self.app.close_in_background(state["response"])
                    if self.app.shutdown_event.is_set():
                        raise RuntimeError("Ứng dụng đang đóng; đã huỷ gợi ý Hook AI.")
                    raise TimeoutError("Gợi ý Hook AI quá thời gian chờ (60 giây).")
            if "error" not in outcome:
                return outcome.get("value")
            err = outcome["error"]
            retryable = isinstance(err, self.app.TranslationApiError) and (
                getattr(err, "retryable", False)
                or getattr(err, "status_code", None) in {429, 500, 502, 503, 504}
                or any(code in str(err) for code in ("429", "500", "502", "503", "504", "timed out", "timeout"))
            )
            if attempt + 1 < max_attempts and retryable and (deadline - time.monotonic()) > 6.0:
                backoff_end = time.monotonic() + 1.2
                while time.monotonic() < backoff_end:
                    if self.app.shutdown_event.is_set():
                        raise RuntimeError("Ứng dụng đang đóng; đã huỷ gợi ý Hook AI.")
                    time.sleep(0.1)
                continue
            raise err
        raise TimeoutError("Gợi ý Hook AI quá thời gian chờ (60 giây).")

    def _routes(self):
        bp = self.blueprint

        @bp.errorhandler(ValueError)
        def invalid(error):
            return jsonify({"error": redaction.redact_error(error)}), 400

        @bp.errorhandler(KeyError)
        def missing(error):
            return jsonify({"error": redaction.redact_error(error.args[0] if error.args else error)}), 404

        @bp.errorhandler(RuntimeError)
        @bp.errorhandler(OSError)
        def conflict(error):
            return jsonify({"error": redaction.redact_error(error)}), 409

        @bp.get("/api/editor/capabilities")
        def capabilities():
            config = self.config()
            ffmpeg, ffprobe = self.app.resolve_ffmpeg_executable(), self.app.resolve_ffprobe_executable()
            return jsonify({"ffmpeg": bool(ffmpeg), "ffprobe": bool(ffprobe),
                            "encoders": renderer.detect_encoders(ffmpeg),
                            "optional": runtime.availability(config.get("editor_runtime_python")),
                            "runtime_python": config.get("editor_runtime_python") or "",
                            "separation_model_dir": config.get("editor_separation_model_dir") or "",
                            "separation_model": config.get("editor_separation_model") or "UVR-MDX-NET-Inst_HQ_3.onnx",
                            "capcut_compat": config.get("editor_capcut_compat", True) is True,
                            "capcut": {"compatibility_enabled": config.get("editor_capcut_compat", True) is True,
                                       "available": config.get("editor_capcut_compat", True) is True, "service_verified": False,
                                       "detail": "CapCut common-task tương thích nội bộ; đã smoke-test câu mẫu 2026-11-19, không bảo đảm mọi giọng/tài khoản/region. Chỉ gửi text khi bạn yêu cầu; Edge fallback là tùy chọn."},
                            "presets": list(PRESETS), "fonts": list(FONTS),
                            "subtitle_animations": list(SUBTITLE_ANIMATIONS)})

        @bp.post("/api/editor/runtime")
        def configure_runtime():
            data = self._body(("python_path", "model_dir", "model_filename", "capcut_compat"))
            if not data:
                raise ValueError("Chưa có thiết lập runtime để lưu.")
            with self.app.config_write_lock:
                config = self.app.load_app_config(strict=True)
                if "python_path" in data:
                    if not isinstance(data["python_path"], str):
                        raise ValueError("Runtime path phải là chuỗi.")
                    config["editor_runtime_python"] = runtime.interpreter(data["python_path"]) if data["python_path"] else ""
                if "model_dir" in data:
                    directory = data["model_dir"]
                    if not isinstance(directory, str) or (directory and (not os.path.isabs(directory) or not os.path.isdir(directory))):
                        raise ValueError("Thư mục model phải là đường dẫn tuyệt đối đã tồn tại.")
                    config["editor_separation_model_dir"] = directory
                if "model_filename" in data:
                    filename = data["model_filename"]
                    if (not isinstance(filename, str) or not filename or len(filename) > 200
                            or Path(filename).name != filename or any(char in filename for char in "/\\:\x00")):
                        raise ValueError("Tên model không hợp lệ.")
                    config["editor_separation_model"] = filename
                if "capcut_compat" in data:
                    if type(data["capcut_compat"]) is not bool:
                        raise ValueError("Tuỳ chọn CapCut phải là boolean.")
                    config["editor_capcut_compat"] = data["capcut_compat"]
                self.app.save_app_config(config)
            return jsonify({"optional": runtime.availability(config.get("editor_runtime_python")),
                            "runtime_python": config.get("editor_runtime_python") or "",
                            "capcut_compat": config.get("editor_capcut_compat") is True})

        @bp.route("/api/editor/projects/<job_id>", methods=["GET", "POST"])
        def project_options(job_id):
            with self.app.operation_admission_lock, self.app.queue_state_lock, manifest.LOCK:
                job, result, value = self._project(job_id)
                if request.method == "POST":
                    if self.jobs.active(job_id):
                        raise RuntimeError("Không đổi cấu hình dự án trong khi Auto Edit đang chạy.")
                    data = self._body(("options",), ("options",))
                    value["editor_options"] = self._options(value, data["options"])
                    manifest.write_atomic(manifest.path_for(result), value)
                task = self._restored_task(job_id, result, value)
                return jsonify({"snapshot": self.app.project_handoff_payload(job, result, value, "editor"),
                                "options": self._options(value),
                                "last_render": self._last_render(job_id, result, value),
                                "active_task": self._signed(task) if task else None})

        @bp.route("/api/editor/projects/<job_id>/srt", methods=["GET", "PUT"])
        def project_srt(job_id):
            if request.method == "PUT":
                data = self._body(("cues",), ("cues",))
            with self.app.operation_admission_lock, self.app.queue_state_lock, manifest.LOCK:
                _, result, value = self._project(job_id)
                translated_srt, raw_srt = self._srt_paths(result, value)
                lock_reason = self._srt_lock_reason(job_id, translated_srt)
                if request.method == "GET":
                    cues = srt_utils.read_srt_file(translated_srt)
                    enriched, has_raw = self._attach_raw(cues, raw_srt)
                    has_backup = storage.safe_regular(srt_utils.backup_path_for(translated_srt))
                    return jsonify({
                        "job_id": job_id,
                        "cues": enriched,
                        "has_raw": has_raw,
                        "has_backup": has_backup,
                        "locked": bool(lock_reason),
                        "lock_reason": lock_reason,
                        "srt_manually_edited": value.get("srt_manually_edited") is True,
                    })
                if lock_reason:
                    raise RuntimeError(lock_reason)
                saved = self._write_srt_and_manifest(
                    result, value, translated_srt,
                    lambda: srt_utils.write_srt_atomic(translated_srt, data["cues"], backup=True),
                )
                enriched, has_raw = self._attach_raw(saved, raw_srt)
                has_backup = storage.safe_regular(srt_utils.backup_path_for(translated_srt))
                self.app.log_to_app(f"[Auto Edit] Đã lưu phụ đề SRT thủ công ({len(saved)} câu) cho {value['project_name']}.")
                return jsonify({
                    "job_id": job_id,
                    "cues": enriched,
                    "has_raw": has_raw,
                    "has_backup": has_backup,
                    "locked": False,
                    "lock_reason": "",
                    "srt_manually_edited": True,
                    "srt_stale_assets": value.get("srt_stale_assets", {}),
                })

        @bp.post("/api/editor/projects/<job_id>/srt/apply-glossary")
        def apply_glossary_srt(job_id):
            data = self._body(("glossary_id", "cues", "save"))
            save = data.get("save", False)
            if type(save) is not bool:
                raise ValueError("Tham số save phải là boolean.")
            glossary_id = data.get("glossary_id")
            if glossary_id:
                profile = translation_glossary.get_glossary(self.app.CONFIG_DIR, glossary_id)
            else:
                profile = translation_glossary.get_active_glossary(self.app.CONFIG_DIR)
            with self.app.operation_admission_lock, self.app.queue_state_lock, manifest.LOCK:
                _, result, value = self._project(job_id)
                translated_srt, raw_srt = self._srt_paths(result, value)
                if "cues" in data and data["cues"] is not None:
                    base_cues = srt_utils.validate_cues(data["cues"])
                else:
                    base_cues = srt_utils.read_srt_file(translated_srt)
                updated_cues, stats = translation_glossary.apply_glossary_to_cues(base_cues, profile)
                if save:
                    lock_reason = self._srt_lock_reason(job_id, translated_srt)
                    if lock_reason:
                        raise RuntimeError(lock_reason)
                    updated_cues = self._write_srt_and_manifest(
                        result, value, translated_srt,
                        lambda: srt_utils.write_srt_atomic(translated_srt, updated_cues, backup=True),
                    )
                enriched, has_raw = self._attach_raw(updated_cues, raw_srt)
                has_backup = storage.safe_regular(srt_utils.backup_path_for(translated_srt))
                return jsonify({
                    "job_id": job_id,
                    "cues": enriched,
                    "has_raw": has_raw,
                    "has_backup": has_backup,
                    "saved": save,
                    "srt_stale_assets": value.get("srt_stale_assets", {}),
                    **stats,
                })

        @bp.post("/api/editor/projects/<job_id>/srt/restore-backup")
        def restore_srt_backup_route(job_id):
            self._body(())
            with self.app.operation_admission_lock, self.app.queue_state_lock, manifest.LOCK:
                _, result, value = self._project(job_id)
                translated_srt, raw_srt = self._srt_paths(result, value)
                lock_reason = self._srt_lock_reason(job_id, translated_srt)
                if lock_reason:
                    raise RuntimeError(lock_reason)
                try:
                    restored = self._write_srt_and_manifest(
                        result, value, translated_srt,
                        lambda: srt_utils.restore_srt_backup(translated_srt),
                    )
                except FileNotFoundError as error:
                    raise KeyError(str(error)) from error
                enriched, has_raw = self._attach_raw(restored, raw_srt)
                has_backup = storage.safe_regular(srt_utils.backup_path_for(translated_srt))
                self.app.log_to_app(f"[Auto Edit] Đã hoàn tác phụ đề SRT từ bản sao lưu .bak cho {value['project_name']}.")
                return jsonify({
                    "job_id": job_id,
                    "cues": enriched,
                    "has_raw": has_raw,
                    "has_backup": has_backup,
                    "locked": False,
                    "lock_reason": "",
                    "srt_manually_edited": True,
                    "srt_stale_assets": value.get("srt_stale_assets", {}),
                })

        @bp.post("/api/editor/projects/<job_id>/watermark")
        def upload_watermark(job_id):
            image = request.files.get("image")
            if image is None:
                raise ValueError("Hãy chọn ảnh PNG.")
            data = image.stream.read(8 * 1024 * 1024 + 1)
            _png(data)
            with self.app.operation_admission_lock, self.app.queue_state_lock, manifest.LOCK:
                _, result, value = self._project(job_id, require_ready=True)
                if self.jobs.active(job_id):
                    raise RuntimeError("Hãy chờ Auto Edit xong trước khi thay watermark.")
                directory = Path(result["project_dir"]) / ".vietsub-assets"
                manifest._safe_path(str(directory))
                directory.mkdir(exist_ok=True)
                target = directory / f"watermark-{uuid.uuid4().hex}.png"
                target.write_bytes(data)
                options = normalize({}, base=value.get("editor_options"), inherited_crop=value.get("crop_box"))
                options.update(watermark_path=str(target), watermark_enabled=True)
                value["editor_options"] = options
                manifest.write_atomic(manifest.path_for(result), value)
                return jsonify({"watermark_path": str(target), "options": options})

        @bp.post("/api/editor/projects/<job_id>/suggest-hooks")
        def suggest_hooks(job_id):
            data = self._body(("max_chars",))
            max_chars = data.get("max_chars", publish.DEFAULT_HOOK_MAX_CHARS)
            if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 20 <= max_chars <= 100:
                raise ValueError("max_chars phải là số nguyên từ 20 đến 100.")
            with self.app.operation_admission_lock, self.app.queue_state_lock, manifest.LOCK:
                self._check_admission_open()
                job, result, value = self._project(job_id)
                translated_srt, _ = self._srt_paths(result, value)
                cues = srt_utils.read_srt_file(translated_srt)
                project_name = value.get("project_name") or result.get("project_name") or ""
                pinned_preset_id = job.get("channel_preset_id") if isinstance(job.get("channel_preset_id"), str) else ""
            srt_text = srt_utils.serialize_srt(cues)
            override_glossary_id = None
            if pinned_preset_id:
                try:
                    from vietsub import channel_presets as preset_store
                    preset = preset_store.get_active_preset(self.app.CONFIG_DIR, override_id=pinned_preset_id)
                    if preset is not None:
                        override_glossary_id = preset_store.resolve_preset(self.app.CONFIG_DIR, preset)["glossary_id"]
                except Exception:
                    override_glossary_id = None
            try:
                runtime_config = (
                    self.app.get_translation_runtime_config(require_ready=True, glossary_id=override_glossary_id)
                    if override_glossary_id else self.app.get_translation_runtime_config(require_ready=True)
                )
            except TypeError:
                runtime_config = self.app.get_translation_runtime_config(require_ready=True)
            if runtime_config.get("provider") == "notebook":
                raise ValueError(
                    "Gợi ý Hook AI nhanh hiện chỉ hỗ trợ bộ dịch API (DeepSeek / OpenRouter / OpenAI-compatible). "
                    "Hãy chọn API dịch trong Thiết lập → Dịch thuật để sử dụng tính năng này."
                )
            active_glossary = runtime_config.get("glossary") if isinstance(runtime_config.get("glossary"), dict) else {}
            prompt = publish.build_hooks_prompt(
                srt_text,
                project_name,
                style_note=active_glossary.get("style_note", ""),
                max_chars=max_chars,
            )
            messages = [
                {"role": "system", "content": "Bạn là biên tập viên video ngắn tiếng Việt chuyên viết câu Hook Top Banner. Chỉ trả về JSON hợp lệ."},
                {"role": "user", "content": prompt},
            ]
            try:
                raw_response = self._run_hook_api_call(runtime_config, messages, deadline_seconds=60.0)
            except TimeoutError as error:
                return jsonify({"error": redaction.redact_error(error)}), 504
            except self.app.TranslationApiError as error:
                return jsonify({"error": redaction.redact_error(error)}), 502
            try:
                hooks = publish.parse_hooks_response(raw_response, max_chars=max_chars)
            except ValueError as error:
                return jsonify({"error": redaction.redact_error(error)}), 502
            return jsonify({
                "job_id": job_id,
                "hooks": hooks,
                "provider": runtime_config.get("label", ""),
                "model": runtime_config.get("model", ""),
            })

        @bp.post("/api/editor/jobs")
        @bp.post("/api/editor/projects/<job_id>/audio-preview")
        def start_task(job_id=None):
            if job_id is not None:
                data = self._body(("options",))
                action = "audio_preview"
            else:
                data = self._body(("job_id", "action", "options"), ("job_id", "action"))
                job_id, action = data["job_id"], data["action"]
            task = self.enqueue_project_task(job_id, action, data.get("options", {}))
            return jsonify(self._signed(task)), 202

        @bp.get("/api/editor/jobs/<identifier>")
        def task_status(identifier):
            return jsonify(self._signed(self.jobs.snapshot(identifier)))

        @bp.post("/api/editor/jobs/<identifier>/cancel")
        def cancel_task(identifier):
            self._body(())
            self.jobs.cancel(identifier)
            return jsonify(self._signed(self.jobs.snapshot(identifier)))

        @bp.get("/api/editor/jobs/<identifier>/output")
        def stream_output(identifier):
            task = self.jobs.snapshot(identifier)
            expected = self._signed(task).get("output_url", "").partition("signature=")[2]
            supplied = request.args.get("signature", "")
            if not expected or not secrets.compare_digest(expected.encode("ascii"), supplied.encode("utf-8")):
                return jsonify({"error": "Liên kết output không hợp lệ hoặc file chưa sẵn sàng."}), 403
            path = task["output_path"]
            if task['output_kind'] == 'archive':
                return send_file(path, conditional=True, mimetype='application/zip', as_attachment=True,
                                 download_name=Path(path).name)
            mimetype = "video/mp4" if task["output_kind"] == "video" else "audio/mpeg" if path.endswith(".mp3") else "audio/wav"
            return send_file(path, conditional=True, mimetype=mimetype)

        @bp.get("/api/editor/voices")
        def voices():
            from .providers import service as create_tts_service
            provider = request.args.get("provider", "capcut_internal")
            if provider not in ("capcut_internal", "edge_tts"):
                raise ValueError("TTS provider không hợp lệ.")
            language = request.args.get("language", "vi")
            if provider == "capcut_internal":
                from .capcut_tts import catalog_voices
                return jsonify({"voices": catalog_voices(language), "warnings": [
                    "Danh sách giọng CapCut là catalog offline; không xác nhận quyền dùng giọng hoặc dịch vụ live."]})
            config = self.config()
            service = create_tts_service(self.app.resolve_ffmpeg_executable() or "", self.app.resolve_ffprobe_executable() or "",
                                         os.path.join(self.app.CONFIG_DIR, "editor-tts-cache"),
                                         runtime_python=config.get("editor_runtime_python"), config=config)
            return jsonify({"voices": service.voices(provider=provider, language=language), "warnings": []})

        @bp.post("/api/editor/tts/preview")
        def preview_tts():
            data = self._body(("text", "voice", "provider", "speed", "pitch", "allow_fallback"), ("text", "voice"))
            text = data["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 250:
                raise ValueError("Nghe thử cần một câu từ 1 đến 250 ký tự.")
            options = normalize({"tts_voice": data["voice"], "tts_provider": data.get("provider", "capcut_internal"),
                                 "tts_speed": data.get("speed", 1), "tts_pitch": data.get("pitch", 0),
                                 "tts_fallback": data.get("allow_fallback", True)})
            config = self.config()
            ffmpeg, ffprobe = self.app.resolve_ffmpeg_executable(), self.app.resolve_ffprobe_executable()
            if not ffmpeg or not ffprobe:
                raise RuntimeError("Nghe thử cần FFmpeg/FFprobe để xử lý tốc độ và cao độ.")
            def execute(identifier, cancel, update):
                from .providers import service as create_tts_service
                output = Path(self.app.CONFIG_DIR) / "editor-previews" / f"{identifier}.wav"
                output.parent.mkdir(parents=True, exist_ok=True)
                service = create_tts_service(ffmpeg, ffprobe, os.path.join(self.app.CONFIG_DIR, "editor-tts-cache"),
                                             runtime_python=config.get("editor_runtime_python"), cancel=cancel, config=config,
                                             progress=renderer.tts_progress(self._progress_update(identifier, update)))
                result = service.preview(text, options["tts_voice"], str(output), provider=options["tts_provider"],
                                             speed=options["tts_speed"], pitch=options["tts_pitch"], allow_fallback=options["tts_fallback"])
                return {"output_path": str(output), "output_kind": "audio", "warnings": result.get("warnings", [])}
            with self.app.operation_admission_lock:
                self._check_admission_open()
                task = self.jobs.submit(
                    "", "tts_preview", execute,
                    finish=lambda t, st: self._prune_global_previews(t.get("output_path", "")) if st == "success" else None,
                )
            return jsonify(self._signed(task)), 202


def register(app_runtime):
    adapter = EditorAPI(app_runtime)
    app_runtime.app.register_blueprint(adapter.blueprint)
    return adapter
