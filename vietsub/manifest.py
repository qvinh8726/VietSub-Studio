"""Portable, versioned interchange between VietSub, Auto Edit and Publish Hub.

No browser/AI/media dependencies. Older ``project.json`` exports remain readable;
new sidecars are written next to the project's video as ``<stem>.project.json``.
Callers supply trusted queue results, not arbitrary paths from HTTP requests.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
import time
import uuid
from threading import RLock

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
LOCK = RLock()
ASSET_KEYS = ("video", "source_video", "raw_srt", "translated_srt", "thumbnail", "edited_video", "tts_audio", "extracted_audio")


class ManifestError(ValueError):
    """An invalid or newer sidecar must never be silently overwritten."""


def _text(value):
    return value if isinstance(value, str) else ""


def _copy(value):
    try:
        return copy.deepcopy(value)
    except (RecursionError, OverflowError) as error:
        raise ManifestError("Manifest lồng quá sâu hoặc chứa số vượt giới hạn.") from error


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def crop_box(coords):
    if coords is None:
        return None
    if not isinstance(coords, dict):
        raise ManifestError("Vùng crop phải là một object hoặc null.")
    keys = ("x", "y", "width", "height")
    legacy = ("crop_x", "crop_y", "crop_width", "crop_height")
    source_keys = keys if all(key in coords for key in keys) else legacy
    values = [coords.get(key) for key in source_keys]
    if any(not _finite_number(value) for value in values):
        raise ManifestError("Vùng crop phải chứa số hữu hạn, không phải boolean.")
    x, y, width, height = values
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1
            and x + width <= 1.000001 and y + height <= 1.000001):
        raise ManifestError("Vùng crop phải nằm trong tọa độ nguồn chuẩn hóa 0–1.")
    return dict(zip(keys, values))


def resolution(value):
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        value = dict(zip(("width", "height"), value))
    if not isinstance(value, dict) or any(type(value.get(key)) is not int or value[key] <= 0
                                           for key in ("width", "height")):
        raise ManifestError("Độ phân giải nguồn không hợp lệ.")
    return {key: value[key] for key in ("width", "height")}


def path_for(project):
    directory = _text(project.get("project_dir"))
    if not directory:
        raise ManifestError("Dự án chưa có thư mục kết quả.")
    directory = os.path.abspath(directory)
    # video_destination is stable even while the downloaded source is still in cache.
    video = _text(project.get("video_destination")) or _text(project.get("video"))
    stem = os.path.splitext(os.path.basename(video))[0] if video else _text(project.get("project_name"))
    if not stem or stem in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', stem):
        raise ManifestError("Tên sidecar dự án không hợp lệ.")
    return os.path.join(directory, stem + ".project.json")


def _safe_path(path):
    current = os.path.abspath(path)
    while True:
        if os.path.islink(current) or (hasattr(os.path, "isjunction") and os.path.isjunction(current)):
            raise ManifestError("Không đọc/ghi manifest qua symlink hoặc junction.")
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def _reject_constant(value):
    raise ManifestError(f"Hằng số JSON không hợp lệ: {value}")


def _read_json(path):
    _safe_path(path)
    with open(path, "rb") as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("Manifest vượt giới hạn 2 MiB.")
    try:
        value = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError, OverflowError) as error:
        raise ManifestError("Manifest JSON hỏng; giữ nguyên file để khôi phục.") from error
    if not isinstance(value, dict):
        raise ManifestError("Manifest phải là JSON object.")
    return value


def _asset_path(value, directory):
    if not isinstance(value, str) or "\x00" in value:
        raise ManifestError("Đường dẫn asset không hợp lệ.")
    return os.path.abspath(os.path.join(directory, value)) if value else ""


def validate(payload, directory):
    if not isinstance(payload, dict):
        raise ManifestError("Manifest phải là JSON object.")
    value = _copy(payload)
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ManifestError("Phiên bản manifest chưa được hỗ trợ; không ghi đè.")
    if not isinstance(value.get("project_id"), str) or not value["project_id"]:
        raise ManifestError("Manifest thiếu project_id.")
    if not isinstance(value.get("project_name"), str):
        raise ManifestError("Tên dự án không hợp lệ.")
    if not isinstance(value.get("assets"), dict):
        raise ManifestError("Manifest thiếu assets.")
    for key in ASSET_KEYS:
        value["assets"][key] = _asset_path(value["assets"].get(key, ""), directory)
    value["crop_box"] = crop_box(value.get("crop_box"))
    value["source_resolution"] = resolution(value.get("source_resolution"))
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or any(not isinstance(metadata.get(key), str) for key in ("title", "description")):
        raise ManifestError("Metadata tiêu đề/mô tả không hợp lệ.")
    tags = metadata.get("hashtags")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ManifestError("Hashtag phải là một mảng chuỗi.")
    if not isinstance(value.get("source"), dict) or not isinstance(value.get("stages"), dict):
        raise ManifestError("Manifest thiếu source/stages.")
    for name in ("vietsub", "editor", "uploader"):
        stage = value["stages"].get(name)
        if not isinstance(stage, dict) or not isinstance(stage.get("status"), str):
            raise ManifestError("Trạng thái phân hệ trong manifest không hợp lệ.")
    return value


def _initial(project, directory, *, video_id="", source_url="", status="success", error=""):
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": uuid.uuid5(uuid.NAMESPACE_URL, os.path.normcase(directory)).hex,
        "project_name": _text(project.get("project_name")),
        "source": {"url": source_url, "video_id": video_id,
                   "original_video": _text(project.get("original_source_video")) or _text(project.get("source_video"))},
        "assets": {key: _text(project.get(key)) for key in ASSET_KEYS},
        "crop_box": crop_box(project.get("crop_coords", project.get("crop_box"))),
        "source_resolution": resolution(project.get("video_resolution", project.get("source_resolution"))),
        "metadata": {"title": _text(project.get("project_name")), "description": "", "hashtags": []},
        "stages": {"vietsub": {"status": status, "error": error},
                   "editor": {"status": "not_started"}, "uploader": {"status": "not_started"}},
        "updated_at": int(time.time()),
    }


def _legacy_project(project):
    """Prefer queue fields over legacy export fields; no rewrite during read."""
    legacy = os.path.join(os.path.abspath(project["project_dir"]), "project.json")
    try:
        saved = _read_json(legacy)
    except FileNotFoundError:
        saved = {}
    return {**saved, **project}


def snapshot(project, *, video_id="", source_url="", status="success", error="", geometry_fallback=None):
    """Return a normalized snapshot, lazily adapting old exports without disk writes."""
    with LOCK:
        target = path_for(project)
        directory = os.path.dirname(target)
        try:
            value = validate(_read_json(target), directory)
        except FileNotFoundError:
            fallback = geometry_fallback if isinstance(geometry_fallback, dict) else {}
            # Requested queue geometry is only a last resort for exports with no
            # saved geometry. It must never replace actual OCR data in a sidecar.
            merged = {**fallback, **_legacy_project(project)}
            value = _initial(merged, directory, video_id=video_id or _text(merged.get("video_id")),
                             source_url=source_url or _text(merged.get("source_url")), status=status, error=error)
            metadata_file = _text(merged.get("publish_metadata"))
            if metadata_file:
                try:
                    metadata = _read_json(_asset_path(metadata_file, directory))
                except FileNotFoundError:
                    metadata = {}
                for key in ("title", "description", "hashtags"):
                    if key in metadata:
                        value["metadata"][key] = metadata[key]
        # Refresh only VietSub-owned fields. Other stages and their assets survive retries.
        value["project_name"] = _text(project.get("project_name")) or value["project_name"]
        for key in ASSET_KEYS:
            if key in project and (key not in {"edited_video", "tts_audio", "extracted_audio"} or project[key]):
                value["assets"][key] = project[key]
        for key, legacy_key, normalizer in (("crop_box", "crop_coords", crop_box),
                                            ("source_resolution", "video_resolution", resolution)):
            if legacy_key in project:
                value[key] = normalizer(project[legacy_key])
        if source_url:
            value["source"]["url"] = source_url
        if video_id:
            value["source"]["video_id"] = video_id
        if project.get("original_source_video"):
            value["source"]["original_video"] = _text(project["original_source_video"])
        value["stages"]["vietsub"].update(status=status, error=error)
        value["source_video_deleted"] = project.get("source_video_deleted") is True
        return validate(value, directory)


def write_atomic(path, payload):
    """Validate before writing, fsync and replace. Leave old data intact on failure."""
    with LOCK:
        path = os.path.abspath(path)
        _safe_path(path)
        value = validate(payload, os.path.dirname(path))
        data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_MANIFEST_BYTES:
            raise ManifestError("Manifest vượt giới hạn 2 MiB.")
        temporary = ""
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".vietsub-manifest-", suffix=".tmp",
                                             dir=os.path.dirname(path), delete=False) as handle:
                temporary = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _safe_path(path)
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return path


def save_project(project, *, metadata=None, **kwargs):
    with LOCK:
        value = snapshot(project, **kwargs)
        if metadata is not None:
            value["metadata"] = copy.deepcopy(metadata)
        value["updated_at"] = int(time.time())
        target = write_atomic(path_for(project), value)
        return target, value
