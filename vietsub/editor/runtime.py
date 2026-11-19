"""Optional external Python runtimes; inspection never imports AI packages."""
import importlib.util
import json
import os
import sys

from .process import capture

MODULES = {"edge_tts": "edge_tts", "audio_separator": "audio_separator"}
COMMANDS = {"edge_tts": 'python -m pip install edge-tts',
            "audio_separator": 'python -m pip install "audio-separator[cpu]"'}


def interpreter(value=None):
    if value:
        path = os.path.abspath(value)
        if not os.path.isabs(value) or not os.path.isfile(path) or not os.path.basename(path).lower().startswith("python"):
            raise ValueError("Runtime cần đường dẫn tuyệt đối tới python.exe của môi trường bổ sung.")
        return path
    return None if getattr(sys, "frozen", False) else sys.executable


def availability(runtime_python=None):
    try:
        python = interpreter(runtime_python)
    except (ValueError, OSError):
        return {name: {"available": False, "detail": "Python runtime tùy chọn không còn tồn tại; FFmpeg vẫn dùng bình thường.",
                       "install_command": COMMANDS[name]} for name in MODULES}
    present = {key: False for key in MODULES}
    detail = "Portable cần Python runtime ngoài; xem lệnh cài bổ sung và chọn python.exe."
    if python:
        script = "import importlib.util,json; print(json.dumps({n:importlib.util.find_spec(n) is not None for n in ['edge_tts','audio_separator']}))"
        try:
            present.update(json.loads(capture([python, "-I", "-c", script], timeout=10)))
            detail = "Runtime đã kiểm tra qua tiến trình độc lập; chưa import model hoặc tải model."
        except Exception:
            detail = "Không kiểm tra được Python runtime. Hãy cài package đúng môi trường."
    return {name: {"available": present[name] is True, "detail": detail,
                   "install_command": COMMANDS[name]} for name in MODULES}
