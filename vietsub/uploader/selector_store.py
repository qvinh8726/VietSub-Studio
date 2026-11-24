"""Versioned selector loader with safe optional local override support.

Loads bundled selectors from ``vietsub/uploader/selectors.json`` and optionally
deep-merges a local ``CONFIG_DIR/uploader/selectors.override.json`` after strict
path, size and schema validation. Security-critical navigation allowlists
(``hosts``, ``login_url``, ``upload_url``, ``modes``, ``result_url``) are never
read from JSON and cannot be overridden.
"""

import copy
import json
import os
from pathlib import Path

from vietsub import storage

BUNDLED_SELECTORS_PATH = Path(__file__).resolve().parent / "selectors.json"
OVERRIDE_FILENAME = "selectors.override.json"
SUPPORTED_SCHEMA_VERSION = 1
MAX_OVERRIDE_BYTES = 256 * 1024
MAX_SELECTOR_LIST_ITEMS = 50
MAX_SELECTOR_LENGTH = 512
FORBIDDEN_OVERRIDE_KEYS = frozenset({
    "hosts",
    "login_url",
    "upload_url",
    "modes",
    "result_url",
    "allowed_hosts",
})


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Khoá trùng lặp trong tệp selector: {key}")
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError("Giá trị JSON không hợp lệ trong tệp selector.")


def _validate_selector_string(value, path_label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path_label}: selector phải là chuỗi không rỗng.")
    if len(value) > MAX_SELECTOR_LENGTH:
        raise ValueError(f"{path_label}: selector vượt quá {MAX_SELECTOR_LENGTH} ký tự.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{path_label}: selector chứa ký tự điều khiển không hợp lệ.")
    return value


def _validate_and_merge_node(base, override, path_label):
    if isinstance(base, list):
        if not isinstance(override, list) or not override or len(override) > MAX_SELECTOR_LIST_ITEMS:
            raise ValueError(f"{path_label}: phải là danh sách 1..{MAX_SELECTOR_LIST_ITEMS} selector.")
        return [_validate_selector_string(item, f"{path_label}[{idx}]") for idx, item in enumerate(override)]
    if isinstance(base, str):
        return _validate_selector_string(override, path_label)
    if isinstance(base, dict):
        if not isinstance(override, dict):
            raise ValueError(f"{path_label}: phải là đối tượng JSON.")
        merged = copy.deepcopy(base)
        for key, value in override.items():
            if key in FORBIDDEN_OVERRIDE_KEYS:
                raise ValueError(f"{path_label}.{key}: không được phép ghi đè khoá bảo mật.")
            if key not in base:
                raise ValueError(f"{path_label}.{key}: khoá selector không hợp lệ.")
            merged[key] = _validate_and_merge_node(base[key], value, f"{path_label}.{key}")
        return merged
    raise ValueError(f"{path_label}: cấu trúc schema không hỗ trợ.")


def _validate_and_merge_root(base, override):
    if not isinstance(override, dict):
        raise ValueError("selectors.override.json phải là đối tượng JSON.")
    allowed_top = {"schema_version", "updated_at", "common", "platforms"}
    for key in override:
        if key in FORBIDDEN_OVERRIDE_KEYS:
            raise ValueError(f"Không được phép ghi đè khoá bảo mật '{key}'.")
        if key not in allowed_top:
            raise ValueError(f"Khoá cấp cao không hợp lệ trong selectors.override.json: '{key}'.")
    if "schema_version" in override:
        version = override["schema_version"]
        if type(version) is not int or version != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(f"schema_version không hỗ trợ (chỉ hỗ trợ {SUPPORTED_SCHEMA_VERSION}).")
    if "updated_at" in override:
        _validate_selector_string(override["updated_at"], "updated_at")
    merged = copy.deepcopy(base)
    if "updated_at" in override:
        merged["updated_at"] = override["updated_at"]
    if "common" in override:
        merged["common"] = _validate_and_merge_node(base["common"], override["common"], "common")
    if "platforms" in override:
        merged["platforms"] = _validate_and_merge_node(base["platforms"], override["platforms"], "platforms")
    return merged


def load_bundled_selectors():
    """Load and validate the bundled selectors.json shipped with VietSub Studio."""
    raw = BUNDLED_SELECTORS_PATH.read_text(encoding="utf-8")
    payload = json.loads(raw, object_pairs_hook=_unique_keys, parse_constant=_invalid_json_constant)
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
        raise ValueError("Tệp selectors.json đi kèm ứng dụng bị hỏng.")
    if payload["schema_version"] != SUPPORTED_SCHEMA_VERSION:
        raise ValueError("Phiên bản schema của selectors.json đi kèm không được hỗ trợ.")
    if not isinstance(payload.get("common"), dict) or not isinstance(payload.get("platforms"), dict):
        raise ValueError("Tệp selectors.json đi kèm thiếu trường common/platforms.")
    return payload


def load_selectors(config_dir=None):
    """Return ``(selectors_dict, status_dict)`` with safe fallback on bad override.

    If ``CONFIG_DIR/uploader/selectors.override.json`` is missing, returns the
    bundled selectors cleanly with no warning. If present but unsafe (symlink,
    oversized, malformed JSON, unknown or security-sensitive keys), falls back
    to the bundled selectors and returns a Vietnamese warning in ``status``.
    """
    bundled = load_bundled_selectors()
    status = {
        "schema_version": bundled["schema_version"],
        "updated_at": bundled.get("updated_at", ""),
        "override_present": False,
        "override_active": False,
        "warning": "",
    }
    if not config_dir:
        return bundled, status

    override_path = Path(os.path.abspath(os.fspath(config_dir))) / "uploader" / OVERRIDE_FILENAME
    try:
        exists = override_path.exists() or override_path.is_symlink()
    except OSError:
        exists = False
    if not exists:
        return bundled, status

    status["override_present"] = True
    if not storage.safe_regular(str(override_path)):
        status["warning"] = (
            "Đã bỏ qua selectors.override.json vì là liên kết tượng trưng/junction hoặc không phải tệp thường; "
            "đang dùng bộ selector mặc định."
        )
        return bundled, status

    try:
        size = os.stat(override_path, follow_symlinks=False).st_size
        if size > MAX_OVERRIDE_BYTES:
            status["warning"] = (
                "Đã bỏ qua selectors.override.json vì vượt quá giới hạn 256 KiB; "
                "đang dùng bộ selector mặc định."
            )
            return bundled, status
        with open(override_path, "rb") as stream:
            raw = stream.read(MAX_OVERRIDE_BYTES + 1)
        if len(raw) > MAX_OVERRIDE_BYTES:
            status["warning"] = (
                "Đã bỏ qua selectors.override.json vì vượt quá giới hạn 256 KiB; "
                "đang dùng bộ selector mặc định."
            )
            return bundled, status
        override = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique_keys,
            parse_constant=_invalid_json_constant,
        )
        merged = _validate_and_merge_root(bundled, override)
        status["override_active"] = True
        status["updated_at"] = merged.get("updated_at", status["updated_at"])
        return merged, status
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
        status["warning"] = (
            f"Đã bỏ qua selectors.override.json không hợp lệ ({error}); "
            "đang dùng bộ selector mặc định."
        )
        return bundled, status
