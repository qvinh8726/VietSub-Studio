"""Application wiring of optional CapCut compatibility and explicit credentials.

Credential values stay in memory. Nothing imports optional providers until called.
"""
import os


def service(ffmpeg, ffprobe, cache_dir, *, runtime_python=None, cancel=None, progress=None, config=None):
    from .tts import TTSService
    from .capcut_tts import CapCutClient
    value = TTSService(ffmpeg or "", ffprobe or "", cache_dir, runtime_python=runtime_python or None, cancel=cancel, progress=progress)
    config = config or {}
    headers = {}
    # Read only explicit operator credentials, never browser/CapCut databases.
    cookie = os.environ.get("VIETSUB_CAPCUT_COOKIE", "")
    bearer = os.environ.get("VIETSUB_CAPCUT_BEARER", "")
    if cookie:
        headers["Cookie"] = cookie
    if bearer:
        headers["Authorization"] = bearer if bearer.lower().startswith("bearer ") else "Bearer " + bearer
    signer = None
    if config.get("editor_capcut_compat", True) is True:
        from .compat_signer import experimental_payload_signer
        signer = experimental_payload_signer
    value.register("capcut_internal", CapCutClient(payload_signer=signer, headers=headers, cancel=cancel, progress=progress))
    return value
