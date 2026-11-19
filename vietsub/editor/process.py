"""Small cancellable process boundary for editor FFmpeg/FFprobe workers."""
import os
from queue import Empty, Queue
import subprocess
from threading import Thread
import time


class EditorCancelled(RuntimeError):
    pass


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise EditorCancelled("Đã huỷ tác vụ Auto Edit.")


def run(command, *, cwd=None, cancel=None, timeout=3600, on_line=None):
    """No shell, bounded output; kill only this child when cancelled/timed out."""
    check_cancel(cancel)
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", bufsize=1,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = Queue(maxsize=2048)
    tail = []
    def reader():
        try:
            for line in process.stdout:
                # FFmpeg emits short progress lines. Never block a child on an
                # unbounded GUI consumer or keep gigabytes of codec diagnostics.
                try:
                    lines.put_nowait(line[:8192])
                except Exception:
                    pass
        finally:
            process.stdout.close()
    thread = Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None or thread.is_alive() or not lines.empty():
            check_cancel(cancel)
            if time.monotonic() > deadline:
                raise TimeoutError("FFmpeg vượt thời gian xử lý cho phép.")
            try:
                line = lines.get(timeout=.1).strip()
            except Empty:
                continue
            tail.append(line)
            tail[:] = tail[-40:]
            if on_line:
                on_line(line)
        code = process.wait(timeout=2)
        if code:
            raise RuntimeError("FFmpeg/FFprobe không hoàn tất: " + "\n".join(tail)[-6000:])
        return "\n".join(tail)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        thread.join(timeout=2)


def capture(command, *, timeout=15):
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                               timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        raise RuntimeError((completed.stderr or "Lỗi công cụ media")[-3000:])
    return completed.stdout
