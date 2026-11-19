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


class EditorJobs:
    def __init__(self):
        self.lock = RLock()
        self.tasks = OrderedDict()
        self.events = {}
        self.work = Queue()
        self.worker = None
        self.closing = False

    def submit(self, job_id, action, execute, *, finish=None, protected_paths=(), identifier=None, prepare=None):
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
            self.work.put((identifier, execute, finish))
            if self.worker is None or not self.worker.is_alive():
                self.worker = Thread(target=self._run, name="vietsub-editor", daemon=True)
                self.worker.start()
            while len(self.tasks) > 100:
                oldest = next((key for key, value in self.tasks.items() if value["status"] in TERMINAL), None)
                if oldest is None:
                    break
                self.tasks.pop(oldest)
                self.events.pop(oldest, None)
            return copy.deepcopy(task)

    def active(self, job_id):
        with self.lock:
            return any(task["job_id"] == job_id and task["status"] not in TERMINAL for task in self.tasks.values())

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
        with self.lock:
            task = self.snapshot(identifier)
            if task["status"] not in TERMINAL:
                self.events[identifier].set()
            return task

    def _update(self, identifier, **values):
        with self.lock:
            self.tasks[identifier].update(values)

    def _run(self):
        while True:
            identifier, execute, finish = self.work.get()
            cancel = self.events[identifier]
            try:
                check_cancel(cancel)
                self._update(identifier, status="running", phase="prepare", started_at=time.time())
                result = execute(identifier, cancel, lambda **values: self._update(identifier, **values)) or {}
                check_cancel(cancel)
                self._update(identifier, **result)
                # Commit manifest before reporting a successful result to the UI.
                if finish:
                    finish(self.snapshot(identifier), "success")
                self._update(identifier, status="success", phase="done", percent=100, finished_at=time.time())
            except Exception as error:
                state = "cancelled" if isinstance(error, EditorCancelled) or cancel.is_set() else "error"
                # Keep inputs/project reserved until even error-state persistence
                # has finished; cleanup cannot unlink assets during that window.
                self._update(identifier, phase=state, error=str(error)[:6000])
                if finish:
                    try:
                        finish(self.snapshot(identifier), state)
                    except Exception as persist_error:
                        self._update(identifier, error=f"{error}; không lưu được trạng thái manifest: {persist_error}"[:6000])
                self._update(identifier, status=state, finished_at=time.time())
            finally:
                self.work.task_done()
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
