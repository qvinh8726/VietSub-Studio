"""Application wiring of optional CapCut compatibility and explicit credentials.

Credential values stay in memory. Nothing imports optional providers until called.
"""
import os


def service(ffmpeg, ffprobe, cache_dir, *, runtime_python=None, cancel=None, progress=None, config=None):
    from .tts import TTSService
    from .capcut_tts import CapCutClient
    value = TTSService(ffmpeg or "", ffprobe or "", cache_dir, runtime_python=runtime_python or None, cancel=cancel, progress=progress)
    config = config or {}
    # Deployment-only tuning; never accept arbitrary worker counts from a cue.
    for provider, key, default, maximum in (("capcut_internal", "editor_tts_capcut_workers", 3, 6),
                                           ("edge_tts", "editor_tts_edge_workers", 5, 8)):
        workers = config.get(key, default)
        if type(workers) is not int or not 1 <= workers <= maximum:
            raise ValueError(f"{key} phải là số nguyên từ 1 đến {maximum}.")
        value.workers[provider] = workers
    batch = config.get("editor_tts_batch_size", 25)
    if type(batch) is not int or not 1 <= batch <= 30:
        raise ValueError("editor_tts_batch_size phải là số nguyên từ 1 đến 30.")
    value.batch_size = batch
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
