"""Optional audio-separator external-worker adapter; no heavy main-process imports.

Explicit setup (NEVER run automatically)::

    <runtime_python> -m pip install "audio-separator[cpu]"

Users must separately obtain a compatible, appropriately licensed model and its
metadata/configuration in ``model_dir``. Default execution is OFFLINE: missing
model/configuration downloads are refused. ``allow_download=True`` is an explicit
opt-in to upstream model/metadata downloads, not to installing Python packages.

``availability`` checks package presence in an external interpreter, not model,
GPU/dependency usability or separation quality. All actual heavy imports happen
in the bounded worker started by ``separate``. Instrumental/vocal separation may
remove dialogue AND some desired BGM/SFX or leave vocal artifacts; this is NOT a
guarantee that sound effects are preserved or that speech is perfectly removed.

Adapter API verified against:
https://github.com/nomadkaraoke/python-audio-separator/blob/main/audio_separator/separator/separator.py
(Separator(output_dir,model_file_dir,output_format,output_single_stem),
 load_model(model_filename), separate(audio_path) -> output filename list.)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .capcut_tts import ProviderUnavailable, TTSError, check_cancel
from .tts import _process, _runtime

GUIDANCE = ('Install explicitly: <runtime_python> -m pip install "audio-separator[cpu]"; '
            "configure external runtime_python and pre-download a compatible model/config into model_dir. "
            "No Python packages or models are bundled/installed automatically.")
_CHECK = "import importlib.util,json; print(json.dumps({'installed':importlib.util.find_spec('audio_separator') is not None}))"
_WORKER = r'''
import json,os,socket,sys
from pathlib import Path
config=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not config["allow_download"]:
    def offline(*args,**kwargs):
        raise RuntimeError("Offline separation: missing files/network access prohibited")
    socket.socket.connect=offline
    socket.create_connection=offline
from audio_separator.separator import Separator
separator=Separator(output_dir=config["output_dir"],model_file_dir=config["model_dir"],
                    output_format="WAV",output_single_stem="Instrumental")
separator.load_model(model_filename=config["model_filename"])
result=separator.separate(config["audio_path"])
if not isinstance(result,list) or len(result)!=1:
    raise RuntimeError("Expected exactly one instrumental output")
paths=[]
root=Path(config["output_dir"]).resolve()
for item in result:
    path=Path(item)
    if not path.is_absolute():
        path=root/path
    path=path.resolve()
    path.relative_to(root)
    if not path.is_file() or path.stat().st_size==0:
        raise RuntimeError("Missing separated instrumental")
    paths.append(str(path))
Path(sys.argv[2]).write_text(json.dumps({"paths":paths}),encoding="utf-8")
'''


def availability(runtime_python=None) -> dict:
    """No network/heavy imports: report package presence, not model readiness."""
    try:
        raw = _process([_runtime(runtime_python), "-c", _CHECK], timeout=10)
        installed = json.loads(raw.decode("utf-8")).get("installed") is True
        return {"provider": "audio_separator", "available": installed, "installed": installed,
                "runtime_verified": False, "models_checked": False,
                "reason": "Optional package present; dependencies/model must be verified by worker." if installed else "Optional audio-separator is not installed.",
                "guidance": GUIDANCE,
                "warnings": ["Instrumental separation can damage BGM/SFX and may retain vocal artifacts."]}
    except (TTSError, ValueError, AttributeError, UnicodeError):
        return {"provider": "audio_separator", "available": False, "installed": False,
                "runtime_verified": False, "models_checked": False,
                "reason": "Optional Python runtime/audio-separator could not be checked.", "guidance": GUIDANCE}


def separate(audio_path, output_dir, *, model_dir=None, model_filename="UVR-MDX-NET-Inst_HQ_3.onnx",
             runtime_python=None, cancel=None, progress=None, timeout=900.0,
             allow_download=False) -> str:
    """Return an atomically published instrumental WAV path or raise explicitly.

    ``cancel`` Event/callback terminates only our worker; progress receives dicts.
    Missing runtime/model/dependencies never falls back to the unmodified input.
    The worker has a 15-minute default wall-clock bound, configurable up to 1h.
    """
    check_cancel(cancel)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise ValueError("Separation timeout must be positive and at most one hour")
    source = Path(audio_path).resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError("Separation input must be an existing non-empty audio file")
    if not isinstance(model_filename, str) or not model_filename or Path(model_filename).name != model_filename or any(char in model_filename for char in "/\\:"):
        raise ValueError("model_filename must be a plain model filename")
    if model_dir is None:
        raise ProviderUnavailable("A separately installed model_dir is required. " + GUIDANCE)
    models = Path(model_dir).resolve()
    if not models.is_dir() or (not allow_download and not (models / model_filename).is_file()):
        raise ProviderUnavailable("Selected separation model is missing; downloads are disabled. " + GUIDANCE)
    ready = availability(runtime_python)
    check_cancel(cancel)
    if not ready["available"]:
        raise ProviderUnavailable(ready["reason"] + " " + GUIDANCE)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / "instrumental.wav"
    if source == target.resolve():
        raise ValueError("Separation output must not overwrite its input")
    if progress:
        progress({"stage": "separation", "status": "running", "provider": "audio_separator",
                  "warning": "BGM/SFX preservation is model-dependent, not guaranteed."})
    with tempfile.TemporaryDirectory(dir=output, prefix=".separation-") as directory:
        root = Path(directory)
        work = root / "audio"
        work.mkdir()
        request, response = root / "request.json", root / "result.json"
        request.write_text(json.dumps({"audio_path": str(source), "output_dir": str(work), "model_dir": str(models),
                                       "model_filename": model_filename, "allow_download": bool(allow_download)}), encoding="utf-8")
        _process([_runtime(runtime_python), "-c", _WORKER, request, response], cancel=cancel, timeout=timeout)
        check_cancel(cancel)
        if not response.is_file() or response.stat().st_size > 16384:
            raise ProviderUnavailable("Separation worker returned no bounded result metadata")
        try:
            result = json.loads(response.read_text(encoding="utf-8"))
            paths = result.get("paths")
            if not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str):
                raise ValueError("Invalid separation result")
            instrumental = Path(paths[0]).resolve()
            instrumental.relative_to(work.resolve())
        except (ValueError, AttributeError, UnicodeError):
            raise ProviderUnavailable("Separation worker returned invalid/out-of-scope artifact") from None
        if not instrumental.is_file() or instrumental.stat().st_size < 44:
            raise ProviderUnavailable("Separation worker produced no usable instrumental WAV")
        with instrumental.open("rb") as stream:
            header = stream.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ProviderUnavailable("Separation worker did not return a WAV artifact")
        check_cancel(cancel)
        os.replace(instrumental, target)
    if progress:
        progress({"stage": "separation", "status": "complete", "provider": "audio_separator"})
    return str(target)
