"""Reusable Channel Presets bridging VietSub (Workspace 1), Auto Edit (Workspace 2), and Publish Hub (Workspace 3).

Stores profiles atomically at ``CONFIG_DIR/channel_presets.json`` with strict schema
validation and symlink/junction checks via ``vietsub.storage.safe_regular``.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
import threading
import uuid
import zlib

from vietsub import glossary, manifest, storage
from vietsub.editor import options as editor_options

__all__ = [
    "PRESET_LOCK",
    "SCHEMA_VERSION",
    "MAX_PRESETS",
    "MAX_FILE_BYTES",
    "UPLOAD_MODES",
    "VISIBILITIES",
    "presets_path",
    "assets_dir",
    "validate_png_bytes",
    "default_store",
    "load_store",
    "save_store",
    "list_presets",
    "get_preset",
    "get_active_preset_id",
    "get_active_preset",
    "create_preset",
    "update_preset",
    "delete_preset",
    "set_active_preset",
    "save_preset_watermark",
    "resolve_preset",
]

PRESET_LOCK = threading.RLock()
SCHEMA_VERSION = 1
MAX_PRESETS = 50
MAX_FILE_BYTES = 1024 * 1024
MAX_WATERMARK_BYTES = 8 * 1024 * 1024
UPLOAD_MODES = ("assisted", "auto")
VISIBILITIES = ("private", "public", "draft")
_SENTINEL_CROP = {"x": 0.04, "y": 0.64, "width": 0.92, "height": 0.30}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Khoá trùng lặp trong channel_presets.json: {key}")
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError("Giá trị JSON không hợp lệ trong channel_presets.json.")


def _check_text(value, label: str, *, min_len: int = 0, max_len: int = 120, allow_newline: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} phải là chuỗi văn bản.")
    cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(cleaned) < min_len or len(cleaned) > max_len:
        if min_len > 0:
            raise ValueError(f"{label} cần từ {min_len} đến {max_len} ký tự.")
        raise ValueError(f"{label} tối đa {max_len} ký tự.")
    allowed_ctrl = "\n\t" if allow_newline else ""
    if any((ord(ch) < 32 and ch not in allowed_ctrl) or ord(ch) == 127 or 0xD800 <= ord(ch) <= 0xDFFF for ch in cleaned):
        raise ValueError(f"{label} chứa ký tự điều khiển không hợp lệ.")
    return cleaned


def validate_png_bytes(data: bytes) -> None:
    """Validate PNG structure, CRC, and dimensions (max 8 MiB, max 4096x4096)."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 45 or len(data) > 8 * 1024 * 1024 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Watermark phải là PNG hợp lệ, tối đa 8 MiB.")
    offset = 8
    header = False
    pixels = False
    ended = False
    while offset + 12 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + size]
        if size > 8 * 1024 * 1024 or offset + size + 12 > len(data):
            raise ValueError("PNG bị cắt hoặc chunk quá lớn.")
        crc = struct.unpack(">I", data[offset + 8 + size:offset + 12 + size])[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
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
        offset += size + 12
    if not ended or not pixels:
        raise ValueError("PNG chưa đầy đủ dữ liệu ảnh.")


def presets_path(config_dir) -> Path:
    return Path(os.path.abspath(os.fspath(config_dir))) / "channel_presets.json"


def assets_dir(config_dir, preset_id: str | None = None) -> Path:
    base = Path(os.path.abspath(os.fspath(config_dir))) / "channel-presets-assets"
    if preset_id is None:
        return base
    if not isinstance(preset_id, str) or not _ID_RE.fullmatch(preset_id):
        raise ValueError("ID hồ sơ kênh không hợp lệ.")
    return base / preset_id


def _expected_watermark_path(config_dir, preset_id: str) -> str:
    return str(assets_dir(config_dir, preset_id) / "watermark.png")


def _validate_editor_options(raw_options, *, config_dir=None, preset_id: str = "", allow_stored_watermark: bool = False) -> dict:
    if raw_options is None:
        raw_options = {}
    if not isinstance(raw_options, dict):
        raise ValueError("editor_options trong hồ sơ kênh phải là JSON object.")
    submitted = copy.deepcopy(raw_options)
    keep_crop_null = submitted.get("crop_box") is None
    watermark_path = submitted.get("watermark_path", "")
    if watermark_path:
        expected = _expected_watermark_path(config_dir, preset_id) if (config_dir and preset_id) else ""
        if not allow_stored_watermark or not expected or os.path.normcase(os.path.abspath(str(watermark_path))) != os.path.normcase(os.path.abspath(expected)):
            raise ValueError("Không chấp nhận đường dẫn watermark tùy ý trong hồ sơ kênh; hãy tải logo PNG riêng cho hồ sơ.")
        submitted["watermark_path"] = expected
    else:
        submitted["watermark_path"] = ""
    if submitted.get("watermark_enabled") is True and not submitted["watermark_path"]:
        raise ValueError("Hãy tải ảnh logo PNG cho hồ sơ kênh trước khi bật logo.")

    if keep_crop_null:
        submitted["crop_box"] = dict(_SENTINEL_CROP)
    normalized = editor_options.normalize(submitted)
    if keep_crop_null:
        normalized["crop_box"] = None
    return normalized


def _validate_publish_defaults(raw_defaults) -> dict:
    if raw_defaults is None:
        raw_defaults = {}
    if not isinstance(raw_defaults, dict):
        raise ValueError("publish_defaults phải là JSON object.")
    allowed = {"profile_ids", "upload_mode", "visibility", "confirm_public_auto"}
    if set(raw_defaults) - allowed:
        raise ValueError("publish_defaults chứa trường không hỗ trợ.")
    raw_ids = raw_defaults.get("profile_ids", [])
    if not isinstance(raw_ids, list) or len(raw_ids) > 30:
        raise ValueError("publish_defaults.profile_ids phải là mảng tối đa 30 tài khoản.")
    profile_ids: list[str] = []
    seen: set[str] = set()
    for pid in raw_ids:
        if not isinstance(pid, str) or not _UUID_RE.fullmatch(pid):
            raise ValueError("ID tài khoản Publish Hub trong hồ sơ kênh không hợp lệ.")
        key = pid.lower()
        if key not in seen:
            seen.add(key)
            profile_ids.append(pid)
    upload_mode = raw_defaults.get("upload_mode", "assisted")
    if not isinstance(upload_mode, str) or upload_mode not in UPLOAD_MODES:
        raise ValueError("publish_defaults.upload_mode chỉ hỗ trợ assisted hoặc auto.")
    visibility = raw_defaults.get("visibility", "private")
    if not isinstance(visibility, str) or visibility not in VISIBILITIES:
        raise ValueError("publish_defaults.visibility chỉ hỗ trợ private, public hoặc draft.")
    confirm_public_auto = raw_defaults.get("confirm_public_auto", False)
    if type(confirm_public_auto) is not bool:
        raise ValueError("publish_defaults.confirm_public_auto phải là boolean.")
    if visibility == "public" and upload_mode == "auto" and not confirm_public_auto:
        raise ValueError("Cần xác nhận rõ (confirm_public_auto=true) để lưu hồ sơ đăng Công khai ở chế độ Tự động hoàn toàn.")
    return {
        "profile_ids": profile_ids,
        "upload_mode": upload_mode,
        "visibility": visibility,
        "confirm_public_auto": confirm_public_auto if (visibility == "public" and upload_mode == "auto") else False,
    }


def _validate_preset(record, *, config_dir=None, require_id: bool = True, allow_stored_watermark: bool = False) -> dict:
    if not isinstance(record, dict):
        raise ValueError("Hồ sơ kênh phải là object JSON.")
    allowed = {"id", "name", "description", "glossary_id", "editor_options", "publish_defaults", "updated_at"}
    if set(record) - allowed:
        raise ValueError("Hồ sơ kênh chứa trường không hỗ trợ.")
    identifier = record.get("id", "")
    if require_id:
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            raise ValueError("ID hồ sơ kênh không hợp lệ.")
    name = _check_text(record.get("name", ""), "Tên hồ sơ kênh", min_len=1, max_len=80)
    description = _check_text(record.get("description", ""), "Mô tả hồ sơ kênh", min_len=0, max_len=300, allow_newline=True)
    glossary_id = record.get("glossary_id", glossary.DEFAULT_PROFILE_ID)
    if not isinstance(glossary_id, str) or not _ID_RE.fullmatch(glossary_id):
        raise ValueError("glossary_id trong hồ sơ kênh không hợp lệ.")
    opts = _validate_editor_options(
        record.get("editor_options"),
        config_dir=config_dir,
        preset_id=identifier,
        allow_stored_watermark=allow_stored_watermark,
    )
    pub = _validate_publish_defaults(record.get("publish_defaults"))
    updated_at = record.get("updated_at", "")
    if not isinstance(updated_at, str) or len(updated_at) > 64:
        updated_at = ""
    return {
        "id": identifier,
        "name": name,
        "description": description,
        "glossary_id": glossary_id,
        "editor_options": opts,
        "publish_defaults": pub,
        "updated_at": updated_at,
    }


def default_store() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "active_id": "",
        "presets": [],
    }


def load_store(config_dir, *, strict: bool = False) -> dict:
    """Read ``CONFIG_DIR/channel_presets.json`` safely; never auto-creates on GET."""
    path = presets_path(config_dir)
    with PRESET_LOCK:
        if not path.exists() and not path.is_symlink():
            return default_store()
        if not storage.safe_regular(str(path)):
            if strict:
                raise ValueError("channel_presets.json là liên kết không an toàn hoặc không phải tệp thường.")
            return default_store()
        try:
            with open(path, "rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("channel_presets.json vượt quá giới hạn 1 MiB.")
            payload = json.loads(
                raw.decode("utf-8-sig"),
                object_pairs_hook=_unique_keys,
                parse_constant=_invalid_json_constant,
            )
            if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
                raise ValueError("channel_presets.json thiếu schema_version hợp lệ.")
            if payload["schema_version"] != SCHEMA_VERSION:
                raise ValueError("Phiên bản channel_presets.json không được hỗ trợ.")
            raw_presets = payload.get("presets")
            if not isinstance(raw_presets, list) or len(raw_presets) > MAX_PRESETS:
                raise ValueError("Danh sách hồ sơ kênh không hợp lệ.")
            presets = []
            seen_ids: set[str] = set()
            for item in raw_presets:
                validated = _validate_preset(item, config_dir=config_dir, require_id=True, allow_stored_watermark=True)
                if validated["id"] in seen_ids:
                    raise ValueError(f"ID hồ sơ kênh trùng lặp: {validated['id']}")
                seen_ids.add(validated["id"])
                presets.append(validated)
            active_id = payload.get("active_id", "")
            if not isinstance(active_id, str) or (active_id and active_id not in seen_ids):
                active_id = ""
            return {
                "schema_version": SCHEMA_VERSION,
                "active_id": active_id,
                "presets": presets,
            }
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            if strict:
                raise
            return default_store()


def save_store(config_dir, store: dict) -> dict:
    """Validate and atomically write ``CONFIG_DIR/channel_presets.json``."""
    path = presets_path(config_dir)
    if not isinstance(store, dict) or store.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Cấu trúc channel_presets.json không hợp lệ.")
    raw_presets = store.get("presets")
    if not isinstance(raw_presets, list) or len(raw_presets) > MAX_PRESETS:
        raise ValueError(f"Tối đa {MAX_PRESETS} hồ sơ kênh.")
    presets = []
    seen_ids: set[str] = set()
    for item in raw_presets:
        validated = _validate_preset(item, config_dir=config_dir, require_id=True, allow_stored_watermark=True)
        if validated["id"] in seen_ids:
            raise ValueError(f"ID hồ sơ kênh trùng lặp: {validated['id']}")
        seen_ids.add(validated["id"])
        presets.append(validated)
    active_id = store.get("active_id", "")
    if not isinstance(active_id, str) or (active_id and active_id not in seen_ids):
        active_id = ""
    clean = {
        "schema_version": SCHEMA_VERSION,
        "active_id": active_id,
        "presets": presets,
    }
    encoded = json.dumps(clean, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError("channel_presets.json vượt quá giới hạn 1 MiB.")

    with PRESET_LOCK:
        if (path.exists() or path.is_symlink()) and not storage.safe_regular(str(path)):
            raise ValueError("Đường dẫn channel_presets.json không an toàn (symlink/junction); không ghi đè.")
        manifest._safe_path(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                prefix=".channel_presets.",
                suffix=".tmp",
                dir=str(path.parent),
            ) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            manifest._safe_path(str(path))
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return copy.deepcopy(clean)


def list_presets(config_dir) -> dict:
    return copy.deepcopy(load_store(config_dir))


def get_preset(config_dir, preset_id: str) -> dict:
    if not isinstance(preset_id, str) or not _ID_RE.fullmatch(preset_id):
        raise ValueError("ID hồ sơ kênh không hợp lệ.")
    store = load_store(config_dir)
    for preset in store["presets"]:
        if preset["id"] == preset_id:
            return copy.deepcopy(preset)
    raise KeyError("Không tìm thấy hồ sơ kênh.")


def get_active_preset_id(config_dir) -> str:
    return load_store(config_dir).get("active_id", "")


def get_active_preset(config_dir, *, override_id: str | None = None) -> dict | None:
    store = load_store(config_dir)
    target_id = override_id if isinstance(override_id, str) and override_id else store["active_id"]
    if not target_id:
        return None
    for preset in store["presets"]:
        if preset["id"] == target_id:
            return copy.deepcopy(preset)
    return None


def create_preset(
    config_dir,
    *,
    name: str,
    description: str = "",
    glossary_id: str = glossary.DEFAULT_PROFILE_ID,
    editor_options: dict | None = None,
    publish_defaults: dict | None = None,
    activate: bool = False,
) -> tuple[dict, dict]:
    if type(activate) is not bool:
        raise ValueError("Trường activate phải là boolean.")
    with PRESET_LOCK:
        store = load_store(config_dir, strict=True)
        if len(store["presets"]) >= MAX_PRESETS:
            raise ValueError(f"Đã đạt tối đa {MAX_PRESETS} hồ sơ kênh.")
        identifier = uuid.uuid4().hex[:16]
        preset = _validate_preset(
            {
                "id": identifier,
                "name": name,
                "description": description,
                "glossary_id": glossary_id,
                "editor_options": editor_options if editor_options is not None else {},
                "publish_defaults": publish_defaults if publish_defaults is not None else {},
                "updated_at": _now_iso(),
            },
            config_dir=config_dir,
            require_id=True,
            allow_stored_watermark=False,
        )
        store["presets"].append(preset)
        if activate:
            store["active_id"] = identifier
        saved = save_store(config_dir, store)
        return copy.deepcopy(preset), saved


def update_preset(
    config_dir,
    preset_id: str,
    *,
    name=None,
    description=None,
    glossary_id=None,
    editor_options=None,
    publish_defaults=None,
) -> tuple[dict, dict]:
    if not isinstance(preset_id, str) or not _ID_RE.fullmatch(preset_id):
        raise ValueError("ID hồ sơ kênh không hợp lệ.")
    with PRESET_LOCK:
        store = load_store(config_dir, strict=True)
        for index, current in enumerate(store["presets"]):
            if current["id"] == preset_id:
                merged_editor = current["editor_options"] if editor_options is None else copy.deepcopy(editor_options)
                # Preserve existing registered preset watermark if caller omits watermark_path or passes the registered path.
                if isinstance(merged_editor, dict):
                    existing_wm = current["editor_options"].get("watermark_path", "")
                    if "watermark_path" not in merged_editor and existing_wm:
                        merged_editor["watermark_path"] = existing_wm
                updated = _validate_preset(
                    {
                        "id": preset_id,
                        "name": current["name"] if name is None else name,
                        "description": current["description"] if description is None else description,
                        "glossary_id": current["glossary_id"] if glossary_id is None else glossary_id,
                        "editor_options": merged_editor,
                        "publish_defaults": current["publish_defaults"] if publish_defaults is None else publish_defaults,
                        "updated_at": _now_iso(),
                    },
                    config_dir=config_dir,
                    require_id=True,
                    allow_stored_watermark=True,
                )
                store["presets"][index] = updated
                saved = save_store(config_dir, store)
                return copy.deepcopy(updated), saved
        raise KeyError("Không tìm thấy hồ sơ kênh.")


def delete_preset(config_dir, preset_id: str) -> dict:
    if not isinstance(preset_id, str) or not _ID_RE.fullmatch(preset_id):
        raise ValueError("ID hồ sơ kênh không hợp lệ.")
    with PRESET_LOCK:
        store = load_store(config_dir, strict=True)
        before = len(store["presets"])
        store["presets"] = [item for item in store["presets"] if item["id"] != preset_id]
        if len(store["presets"]) == before:
            raise KeyError("Không tìm thấy hồ sơ kênh.")
        if store["active_id"] == preset_id:
            store["active_id"] = ""
        saved = save_store(config_dir, store)
        asset_folder = assets_dir(config_dir, preset_id)
        if asset_folder.exists() and not asset_folder.is_symlink():
            try:
                manifest._safe_path(str(asset_folder))
                shutil.rmtree(asset_folder, ignore_errors=True)
            except (OSError, ValueError):
                pass
        return saved


def set_active_preset(config_dir, preset_id: str) -> dict:
    if not isinstance(preset_id, str):
        raise ValueError("active_id phải là chuỗi.")
    if preset_id and not _ID_RE.fullmatch(preset_id):
        raise ValueError("ID hồ sơ kênh không hợp lệ.")
    with PRESET_LOCK:
        store = load_store(config_dir, strict=True)
        if preset_id and not any(item["id"] == preset_id for item in store["presets"]):
            raise KeyError("Không tìm thấy hồ sơ kênh để kích hoạt.")
        store["active_id"] = preset_id
        return save_store(config_dir, store)


def save_preset_watermark(config_dir, preset_id: str, png_bytes: bytes) -> tuple[dict, dict]:
    """Validate and store a scoped PNG watermark for a channel preset."""
    if not isinstance(preset_id, str) or not _ID_RE.fullmatch(preset_id):
        raise ValueError("ID hồ sơ kênh không hợp lệ.")
    validate_png_bytes(png_bytes)
    with PRESET_LOCK:
        store = load_store(config_dir, strict=True)
        target_index = next((idx for idx, item in enumerate(store["presets"]) if item["id"] == preset_id), None)
        if target_index is None:
            raise KeyError("Không tìm thấy hồ sơ kênh.")
        folder = assets_dir(config_dir, preset_id)
        manifest._safe_path(str(folder))
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / "watermark.png"
        if (destination.exists() or destination.is_symlink()) and not storage.safe_regular(str(destination)):
            raise ValueError("Đường dẫn watermark của hồ sơ kênh không an toàn.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", delete=False, prefix=".wm-", suffix=".tmp", dir=str(folder)) as handle:
                temporary = Path(handle.name)
                handle.write(png_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            manifest._safe_path(str(destination))
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        current = copy.deepcopy(store["presets"][target_index])
        current["editor_options"]["watermark_path"] = str(destination)
        current["editor_options"]["watermark_enabled"] = True
        current["updated_at"] = _now_iso()
        validated = _validate_preset(current, config_dir=config_dir, require_id=True, allow_stored_watermark=True)
        store["presets"][target_index] = validated
        saved = save_store(config_dir, store)
        return copy.deepcopy(validated), saved


def resolve_preset(
    config_dir,
    preset: dict,
    *,
    uploader_profiles: list[dict] | None = None,
    project_dir: str | None = None,
    inherited_crop: dict | None = None,
) -> dict:
    """Resolve a preset safely against live glossaries, uploader profiles, watermark file, and project crop."""
    warnings: list[str] = []
    # 1. Resolve glossary_id
    glossary_store = glossary.list_glossaries(config_dir)
    known_glossaries = {item["id"] for item in glossary_store.get("profiles", [])}
    resolved_glossary_id = preset.get("glossary_id", glossary.DEFAULT_PROFILE_ID)
    if resolved_glossary_id not in known_glossaries:
        warnings.append("Bộ từ điển của hồ sơ kênh không còn tồn tại; đã tự chuyển về bộ Mặc định.")
        resolved_glossary_id = glossary.DEFAULT_PROFILE_ID

    # 2. Resolve Publish Hub profile IDs
    pub = copy.deepcopy(preset.get("publish_defaults", {}))
    configured_ids = list(pub.get("profile_ids", []))
    if uploader_profiles is None:
        valid_ids = list(configured_ids)
        missing_ids: list[str] = []
    else:
        known_profiles = {item["id"] for item in uploader_profiles if isinstance(item, dict) and item.get("id")}
        valid_ids = [pid for pid in configured_ids if pid in known_profiles]
        missing_ids = [pid for pid in configured_ids if pid not in known_profiles]
        if missing_ids:
            warnings.append(f"Đã bỏ qua {len(missing_ids)} tài khoản Publish Hub không còn tồn tại.")
    pub["profile_ids"] = valid_ids
    pub["missing_profile_ids"] = missing_ids

    # 3. Resolve editor_options (watermark + crop_box inheritance)
    opts = copy.deepcopy(preset.get("editor_options", {}))
    expected_wm = _expected_watermark_path(config_dir, preset["id"])
    wm_path = opts.get("watermark_path") or ""
    if wm_path:
        same_expected = os.path.normcase(os.path.abspath(wm_path)) == os.path.normcase(os.path.abspath(expected_wm))
        raw_bytes = None
        if same_expected:
            with PRESET_LOCK:
                if storage.safe_regular(wm_path):
                    try:
                        with open(wm_path, "rb") as handle:
                            raw_bytes = handle.read(MAX_WATERMARK_BYTES + 1)
                    except OSError:
                        raw_bytes = None
        if raw_bytes is not None:
            try:
                validate_png_bytes(raw_bytes)
            except ValueError:
                raw_bytes = None
        if not same_expected or raw_bytes is None:
            if opts.get("watermark_enabled"):
                warnings.append("Logo PNG của hồ sơ kênh không còn tồn tại hoặc không hợp lệ; đã tạm tắt logo.")
            opts["watermark_enabled"] = False
            opts["watermark_path"] = ""
        elif project_dir:
            # Copy validated PNG into this project's scoped .vietsub-assets directory atomically.
            digest = hashlib.sha256(raw_bytes).hexdigest()[:12]
            assets_folder = Path(os.path.abspath(project_dir)) / ".vietsub-assets"
            manifest._safe_path(str(assets_folder))
            assets_folder.mkdir(exist_ok=True)
            scoped = assets_folder / f"watermark-preset-{digest}.png"
            manifest._safe_path(str(scoped))
            if scoped.exists() or scoped.is_symlink():
                if not storage.safe_regular(str(scoped)):
                    raise ValueError("Đường dẫn logo PNG trong dự án không an toàn.")
            else:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="wb", delete=False, prefix=".wm-", suffix=".tmp", dir=str(assets_folder)
                    ) as handle:
                        temporary = Path(handle.name)
                        handle.write(raw_bytes)
                        handle.flush()
                        os.fsync(handle.fileno())
                    manifest._safe_path(str(scoped))
                    os.replace(temporary, scoped)
                    temporary = None
                finally:
                    if temporary is not None:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
            opts["watermark_path"] = str(scoped)
    elif opts.get("watermark_enabled"):
        warnings.append("Hồ sơ kênh chưa có logo PNG hợp lệ; đã tạm tắt logo.")
        opts["watermark_enabled"] = False

    effective_crop = opts.get("crop_box") if opts.get("crop_box") is not None else inherited_crop
    if project_dir is not None and effective_crop is None and opts.get("mask_mode", "none") != "none":
        warnings.append("Dự án không có vùng crop OCR; đã tự chuyển chế độ che chữ về Không che.")
        opts["mask_mode"] = "none"
    opts["crop_box"] = effective_crop
    if project_dir is not None or effective_crop is not None:
        normalized_opts = editor_options.normalize(opts, inherited_crop=inherited_crop)
    else:
        normalized_opts = _validate_editor_options(opts, config_dir=config_dir, preset_id=preset["id"], allow_stored_watermark=True)

    return {
        "preset_id": preset["id"],
        "preset_name": preset["name"],
        "glossary_id": resolved_glossary_id,
        "editor_options": normalized_opts,
        "publish_defaults": pub,
        "warnings": warnings,
    }
