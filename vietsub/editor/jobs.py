"""Serial editor task queue, independent from the legacy VietSub workflow queue."""
import copy
import json
import os
from collections import OrderedDict
from queue import Queue
from threading import Event, RLock, Thread
import time
import uuid

from .process import EditorCancelled, check_cancel

TERMINAL = {"success", "error", "cancelled"}


def _discard_preview(task):
    """Remove only a failed task's own private preview, never an input/export."""
    if task.get("action") not in {"audio_preview", "tts_preview"}:
        return
    from vietsub import storage
    path = task.get("output_path") or ""
    identifier = task.get("id") or ""
    if (len(identifier) != 32 or any(char not in "0123456789abcdef" for char in identifier)
            or os.path.basename(path) != identifier + ".wav"
            or os.path.basename(os.path.dirname(path)) not in {".vietsub-editor-previews", "editor-previews"}):
        return
    # inventory/delete rechecks regular-file identity immediately before unlink.
    storage.delete_inventory(storage.inventory([path]))


class EditorJobs:
    def __init__(self):
        self.lock = RLock()
        self.tasks = OrderedDict()
        self.events = {}
        self._callbacks = {}
        self._cancelled_queued = set()
        self.work = Queue()
        self.worker = None
        self.closing = False

    def _evict_terminal_locked(self):
        while len(self.tasks) > 100:
            oldest = next((key for key, value in self.tasks.items() if value["status"] in TERMINAL), None)
            if oldest is None:
                break
            self.tasks.pop(oldest)
            self.events.pop(oldest, None)
            self._callbacks.pop(oldest, None)
            self._cancelled_queued.discard(oldest)

    def submit(self, job_id, action, execute, *, finish=None, after_terminal=None, protected_paths=(), identifier=None, prepare=None):
        with self.lock:
            if self.closing:
                raise RuntimeError("Auto Edit đang đóng; không nhận tác vụ mới.")
            if job_id and self.active(job_id):
                raise RuntimeError("Dự án đã có tác vụ Auto Edit đang chờ/chạy.")
            if sum(task["status"] not in TERMINAL for task in self.tasks.values()) >= 20:
                raise RuntimeError("Hàng đợi Auto Edit đã đủ 20 tác vụ.")
            identifier = identifier or uuid.uuid4().hex
            if identifier in self.tasks:
                raise RuntimeError("ID tác vụ Auto Edit đã tồn tại.")
            task = {"id": identifier, "job_id": job_id, "action": action, "status": "queued",
                    "phase": "queued", "percent": 0, "fps": 0, "error": "", "warnings": [],
                    "output_path": "", "output_kind": "", "created_at": time.time(),
                    "_protected_paths": sorted({os.path.normcase(os.path.abspath(path)) for path in protected_paths if path})}
            # Admission checks happen before the durable reservation. A rejected
            # task can never replace the last-good stage then need a second write.
            if prepare:
                prepare(copy.deepcopy(task))
            self.tasks[identifier] = task
            self.events[identifier] = Event()
            self._callbacks[identifier] = (finish, after_terminal)
            self.work.put((identifier, execute, finish, after_terminal))
            if self.worker is None or not self.worker.is_alive():
                self.worker = Thread(target=self._run, name="vietsub-editor", daemon=True)
                self.worker.start()
            self._evict_terminal_locked()
            return copy.deepcopy(task)

    def active(self, job_id):
        with self.lock:
            return any(task["job_id"] == job_id and task["status"] not in TERMINAL for task in self.tasks.values())

    def any_active(self):
        with self.lock:
            return any(task["status"] not in TERMINAL for task in self.tasks.values())

    def protected_paths(self):
        with self.lock:
            return set().union(*(set(task.get("_protected_paths", ())) for task in self.tasks.values()
                                 if task["status"] not in TERMINAL))

    def restore(self, task):
        """Restore a bounded terminal record from a project manifest, never resume work."""
        with self.lock:
            if task.get("status") not in TERMINAL or not isinstance(task.get("id"), str):
                raise ValueError("Không khôi phục tác vụ đang chạy từ manifest.")
            if task["id"] not in self.tasks:
                self.tasks[task["id"]] = copy.deepcopy(task)
                self.events[task["id"]] = Event()
                self._evict_terminal_locked()

    def snapshot(self, identifier):
        with self.lock:
            if identifier not in self.tasks:
                raise KeyError("Không tìm thấy tác vụ Auto Edit.")
            return copy.deepcopy(self.tasks[identifier])

    def for_project(self, job_id):
        with self.lock:
            return next((copy.deepcopy(task) for task in reversed(list(self.tasks.values()))
                         if task["job_id"] == job_id and not task.get('_historical')), None)

    def cancel(self, identifier):
        finish_cb = None
        after_cb = None
        task_snap = None
        with self.lock:
            task = self.snapshot(identifier)
            if task["status"] not in TERMINAL:
                self.events[identifier].set()
                if task["status"] == "queued":
                    self.tasks[identifier].update(
                        status="cancelled",
                        phase="cancelled",
                        error="Đã huỷ tác vụ Auto Edit.",
                        finished_at=time.time(),
                    )
                    self._cancelled_queued.add(identifier)
                    cb = self._callbacks.pop(identifier, None)
                    if cb:
                        finish_cb, after_cb = cb
                    task_snap = copy.deepcopy(self.tasks[identifier])
                    task = task_snap
        if finish_cb and task_snap is not None:
            try:
                finish_cb(task_snap, "cancelled")
            except Exception:
                pass
        if after_cb and task_snap is not None:
            try:
                after_cb(task_snap, "cancelled")
            except Exception:
                pass
        return task

    def _update(self, identifier, **values):
        with self.lock:
            self.tasks[identifier].update(values)

    def _run(self):
        while True:
            identifier, execute, finish, after_terminal = self.work.get()
            with self.lock:
                self._callbacks.pop(identifier, None)
                if identifier in self._cancelled_queued:
                    self._cancelled_queued.discard(identifier)
                    self.work.task_done()
                    if self.work.empty():
                        self.worker = None
                        return
                    continue
                cancel = self.events[identifier]
                if not cancel.is_set():
                    self.tasks[identifier].update(status="running", phase="prepare", started_at=time.time())
            result = {}
            terminal_state = "error"
            try:
                check_cancel(cancel)
                result = execute(identifier, cancel, lambda **values: self._update(identifier, **values)) or {}
                check_cancel(cancel)
                self._update(identifier, **result)
                # Commit manifest before reporting a successful result to the UI.
                if finish:
                    finish(self.snapshot(identifier), "success")
                self._update(identifier, status="success", phase="done", percent=100, finished_at=time.time())
                terminal_state = "success"
            except Exception as error:
                state = "cancelled" if isinstance(error, EditorCancelled) or cancel.is_set() else "error"
                terminal_state = state
                task = self.snapshot(identifier)
                if task["action"] in {"audio_preview", "tts_preview"}:
                    try:
                        _discard_preview({**task, **result})
                    except OSError:
                        pass  # Best-effort cleanup must not hide the actual failure.
                    self._update(identifier, output_path="", output_kind="", assets={})
                # Keep inputs/project reserved until even error-state persistence
                # has finished; cleanup cannot unlink assets during that window.
                from vietsub import redaction
                safe_error = redaction.redact_error(error)[:6000]
                self._update(identifier, phase=state, error=safe_error)
                if finish:
                    try:
                        finish(self.snapshot(identifier), state)
                    except Exception as persist_error:
                        safe_persist = redaction.redact_error(persist_error)
                        self._update(identifier, error=f"{safe_error}; không lưu được trạng thái manifest: {safe_persist}"[:6000])
                self._update(identifier, status=state, finished_at=time.time())
            finally:
                self.work.task_done()
            if after_terminal:
                try:
                    after_terminal(self.snapshot(identifier), terminal_state)
                except Exception:
                    pass
            with self.lock:
                if self.work.empty():
                    self.worker = None
                    return

    def shutdown(self):
        with self.lock:
            self.closing = True
            for identifier, task in self.tasks.items():
                if task["status"] not in TERMINAL:
                    self.events[identifier].set()
            worker = self.worker
        if worker:
            worker.join(timeout=3)
