"""Synchronized translation glossaries & pronoun/style profiles.

Stores profiles atomically at ``CONFIG_DIR/glossaries.json`` with strict schema
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
import tempfile
import threading
import uuid

from vietsub import manifest, storage

__all__ = [
    "GLOSSARY_LOCK",
    "SCHEMA_VERSION",
    "DEFAULT_PROFILE_ID",
    "MAX_PROFILES",
    "MAX_ENTRIES_PER_PROFILE",
    "DEFAULT_PROMPT_MAX_CHARS",
    "glossary_path",
    "default_store",
    "load_store",
    "save_store",
    "list_glossaries",
    "get_glossary",
    "get_active_glossary",
    "create_glossary",
    "update_glossary",
    "delete_glossary",
    "set_active_glossary",
    "fingerprint",
    "format_prompt_block",
    "apply_glossary_to_cues",
]

GLOSSARY_LOCK = threading.RLock()
SCHEMA_VERSION = 1
DEFAULT_PROFILE_ID = "default"
MAX_PROFILES = 50
MAX_ENTRIES_PER_PROFILE = 300
MAX_FILE_BYTES = 1024 * 1024
DEFAULT_PROMPT_MAX_CHARS = 4000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
_CJK_RANGE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_LATIN_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9_\u00C0-\u024F\u1E00-\u1EFF]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Khoá trùng lặp trong glossaries.json: {key}")
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError("Giá trị JSON không hợp lệ trong glossaries.json.")


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


def _validate_entries(entries) -> list[dict]:
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError("Danh sách thuật ngữ (entries) phải là mảng.")
    if len(entries) > MAX_ENTRIES_PER_PROFILE:
        raise ValueError(f"Mỗi bộ từ điển tối đa {MAX_ENTRIES_PER_PROFILE} thuật ngữ.")
    normalized = []
    seen_sources: set[str] = set()
    for idx, item in enumerate(entries, 1):
        if not isinstance(item, dict) or set(item) - {"source", "target", "note"}:
            raise ValueError(f"Mục từ điển #{idx} không hợp lệ (chỉ gồm source, target, note).")
        source = _check_text(item.get("source", ""), f"Thuật ngữ gốc #{idx}", min_len=1, max_len=120)
        target = _check_text(item.get("target", ""), f"Bản dịch chuẩn #{idx}", min_len=1, max_len=120)
        note = _check_text(item.get("note", ""), f"Ghi chú #{idx}", min_len=0, max_len=120)
        key = source.casefold()
        if key in seen_sources:
            raise ValueError(f"Thuật ngữ gốc bị trùng trong bộ từ điển: '{source}'.")
        seen_sources.add(key)
        normalized.append({"source": source, "target": target, "note": note})
    return normalized


def _validate_profile(record, *, require_id: bool = True) -> dict:
    if not isinstance(record, dict):
        raise ValueError("Hồ sơ từ điển phải là object JSON.")
    allowed = {"id", "name", "style_note", "entries", "updated_at"}
    if set(record) - allowed:
        raise ValueError("Hồ sơ từ điển chứa trường không hỗ trợ.")
    identifier = record.get("id", "")
    if require_id:
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            raise ValueError("ID bộ từ điển không hợp lệ.")
    name = _check_text(record.get("name", ""), "Tên bộ từ điển", min_len=1, max_len=80)
    style_note = _check_text(
        record.get("style_note", ""),
        "Ghi chú xưng hô / văn phong",
        min_len=0,
        max_len=600,
        allow_newline=True,
    )
    entries = _validate_entries(record.get("entries", []))
    updated_at = record.get("updated_at", "")
    if not isinstance(updated_at, str) or len(updated_at) > 64:
        updated_at = ""
    return {
        "id": identifier,
        "name": name,
        "style_note": style_note,
        "entries": entries,
        "updated_at": updated_at,
    }


def default_store() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "active_id": DEFAULT_PROFILE_ID,
        "profiles": [
            {
                "id": DEFAULT_PROFILE_ID,
                "name": "Mặc định",
                "style_note": "",
                "entries": [],
                "updated_at": "",
            }
        ],
    }


def glossary_path(config_dir) -> Path:
    return Path(os.path.abspath(os.fspath(config_dir))) / "glossaries.json"


def load_store(config_dir, *, strict: bool = False) -> dict:
    """Read ``CONFIG_DIR/glossaries.json`` safely; never auto-creates on GET."""
    path = glossary_path(config_dir)
    with GLOSSARY_LOCK:
        if not path.exists() and not path.is_symlink():
            return default_store()
        if not storage.safe_regular(str(path)):
            if strict:
                raise ValueError("glossaries.json là liên kết không an toàn hoặc không phải tệp thường.")
            return default_store()
        try:
            with open(path, "rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("glossaries.json vượt quá giới hạn 1 MiB.")
            payload = json.loads(
                raw.decode("utf-8-sig"),
                object_pairs_hook=_unique_keys,
                parse_constant=_invalid_json_constant,
            )
            if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
                raise ValueError("glossaries.json thiếu schema_version hợp lệ.")
            if payload["schema_version"] != SCHEMA_VERSION:
                raise ValueError("Phiên bản glossaries.json không được hỗ trợ.")
            raw_profiles = payload.get("profiles")
            if not isinstance(raw_profiles, list) or len(raw_profiles) > MAX_PROFILES:
                raise ValueError("Danh sách bộ từ điển không hợp lệ.")
            profiles = []
            seen_ids: set[str] = set()
            for item in raw_profiles:
                validated = _validate_profile(item, require_id=True)
                if validated["id"] in seen_ids:
                    raise ValueError(f"ID bộ từ điển trùng lặp: {validated['id']}")
                seen_ids.add(validated["id"])
                profiles.append(validated)
            if DEFAULT_PROFILE_ID not in seen_ids:
                profiles.insert(0, default_store()["profiles"][0])
                seen_ids.add(DEFAULT_PROFILE_ID)
            active_id = payload.get("active_id", DEFAULT_PROFILE_ID)
            if not isinstance(active_id, str) or active_id not in seen_ids:
                active_id = DEFAULT_PROFILE_ID
            return {
                "schema_version": SCHEMA_VERSION,
                "active_id": active_id,
                "profiles": profiles,
            }
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            if strict:
                raise
            return default_store()


def save_store(config_dir, store: dict) -> dict:
    """Validate and atomically write ``CONFIG_DIR/glossaries.json``."""
    path = glossary_path(config_dir)
    if not isinstance(store, dict) or store.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Cấu trúc glossaries.json không hợp lệ.")
    raw_profiles = store.get("profiles")
    if not isinstance(raw_profiles, list) or not (1 <= len(raw_profiles) <= MAX_PROFILES):
        raise ValueError(f"Cần từ 1 đến {MAX_PROFILES} bộ từ điển.")
    profiles = []
    seen_ids: set[str] = set()
    for item in raw_profiles:
        validated = _validate_profile(item, require_id=True)
        if validated["id"] in seen_ids:
            raise ValueError(f"ID bộ từ điển trùng lặp: {validated['id']}")
        seen_ids.add(validated["id"])
        profiles.append(validated)
    if DEFAULT_PROFILE_ID not in seen_ids:
        raise ValueError("Không thể thiếu bộ từ điển mặc định.")
    active_id = store.get("active_id", DEFAULT_PROFILE_ID)
    if not isinstance(active_id, str) or active_id not in seen_ids:
        active_id = DEFAULT_PROFILE_ID
    clean = {
        "schema_version": SCHEMA_VERSION,
        "active_id": active_id,
        "profiles": profiles,
    }
    encoded = json.dumps(clean, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError("glossaries.json vượt quá giới hạn 1 MiB.")

    with GLOSSARY_LOCK:
        if (path.exists() or path.is_symlink()) and not storage.safe_regular(str(path)):
            raise ValueError("Đường dẫn glossaries.json không an toàn (symlink/junction); không ghi đè.")
        manifest._safe_path(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                prefix=".glossaries.",
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


def list_glossaries(config_dir) -> dict:
    return copy.deepcopy(load_store(config_dir))


def get_glossary(config_dir, glossary_id: str) -> dict:
    if not isinstance(glossary_id, str) or not _ID_RE.fullmatch(glossary_id):
        raise ValueError("ID bộ từ điển không hợp lệ.")
    store = load_store(config_dir)
    for profile in store["profiles"]:
        if profile["id"] == glossary_id:
            return copy.deepcopy(profile)
    raise KeyError("Không tìm thấy bộ từ điển.")


def get_active_glossary(config_dir, *, override_id: str | None = None) -> dict:
    store = load_store(config_dir)
    target_id = override_id if isinstance(override_id, str) and override_id else store["active_id"]
    for profile in store["profiles"]:
        if profile["id"] == target_id:
            return copy.deepcopy(profile)
    return copy.deepcopy(store["profiles"][0])


def create_glossary(config_dir, *, name: str, style_note: str = "", entries=None) -> dict:
    with GLOSSARY_LOCK:
        store = load_store(config_dir, strict=True)
        if len(store["profiles"]) >= MAX_PROFILES:
            raise ValueError(f"Đã đạt tối đa {MAX_PROFILES} bộ từ điển.")
        identifier = uuid.uuid4().hex[:16]
        profile = _validate_profile(
            {
                "id": identifier,
                "name": name,
                "style_note": style_note,
                "entries": entries if entries is not None else [],
                "updated_at": _now_iso(),
            },
            require_id=True,
        )
        store["profiles"].append(profile)
        save_store(config_dir, store)
        return copy.deepcopy(profile)


def update_glossary(config_dir, glossary_id: str, *, name=None, style_note=None, entries=None) -> dict:
    if not isinstance(glossary_id, str) or not _ID_RE.fullmatch(glossary_id):
        raise ValueError("ID bộ từ điển không hợp lệ.")
    with GLOSSARY_LOCK:
        store = load_store(config_dir, strict=True)
        for index, current in enumerate(store["profiles"]):
            if current["id"] == glossary_id:
                updated = _validate_profile(
                    {
                        "id": glossary_id,
                        "name": current["name"] if name is None else name,
                        "style_note": current["style_note"] if style_note is None else style_note,
                        "entries": current["entries"] if entries is None else entries,
                        "updated_at": _now_iso(),
                    },
                    require_id=True,
                )
                store["profiles"][index] = updated
                save_store(config_dir, store)
                return copy.deepcopy(updated)
        raise KeyError("Không tìm thấy bộ từ điển.")


def delete_glossary(config_dir, glossary_id: str) -> dict:
    if not isinstance(glossary_id, str) or not _ID_RE.fullmatch(glossary_id):
        raise ValueError("ID bộ từ điển không hợp lệ.")
    if glossary_id == DEFAULT_PROFILE_ID:
        raise ValueError("Không thể xoá bộ từ điển mặc định.")
    with GLOSSARY_LOCK:
        store = load_store(config_dir, strict=True)
        before = len(store["profiles"])
        store["profiles"] = [p for p in store["profiles"] if p["id"] != glossary_id]
        if len(store["profiles"]) == before:
            raise KeyError("Không tìm thấy bộ từ điển.")
        if store["active_id"] == glossary_id:
            store["active_id"] = DEFAULT_PROFILE_ID
        return save_store(config_dir, store)


def set_active_glossary(config_dir, glossary_id: str) -> dict:
    if not isinstance(glossary_id, str) or not _ID_RE.fullmatch(glossary_id):
        raise ValueError("ID bộ từ điển không hợp lệ.")
    with GLOSSARY_LOCK:
        store = load_store(config_dir, strict=True)
        if not any(p["id"] == glossary_id for p in store["profiles"]):
            raise KeyError("Không tìm thấy bộ từ điển để kích hoạt.")
        store["active_id"] = glossary_id
        return save_store(config_dir, store)


def fingerprint(profile: dict | None) -> str:
    """Stable hash of translation-relevant glossary content, or '' when empty."""
    if not isinstance(profile, dict):
        return ""
    style_note = (profile.get("style_note") or "").strip()
    entries = profile.get("entries") or []
    if not style_note and not entries:
        return ""
    canonical_entries = sorted(
        (
            str(item.get("source") or "").strip(),
            str(item.get("target") or "").strip(),
            str(item.get("note") or "").strip(),
        )
        for item in entries
        if isinstance(item, dict) and item.get("source") and item.get("target")
    )
    if not style_note and not canonical_entries:
        return ""
    payload = {"style_note": style_note, "entries": canonical_entries}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _entry_matches_text(source: str, text: str, text_lower: str) -> bool:
    if _CJK_RANGE_RE.search(source):
        return source in text
    return source.casefold() in text_lower


def format_prompt_block(
    profile: dict | None,
    *,
    source_text: str | None = None,
    max_chars: int = DEFAULT_PROMPT_MAX_CHARS,
) -> str:
    """Format a bounded glossary instruction block for API and Gemini prompts."""
    if not isinstance(profile, dict):
        return ""
    style_note = (profile.get("style_note") or "").strip()
    entries = [
        item for item in (profile.get("entries") or [])
        if isinstance(item, dict) and item.get("source") and item.get("target")
    ]
    if not style_note and not entries:
        return ""

    name = (profile.get("name") or "Từ điển").strip()
    lines = [f"QUY TẮC TỪ ĐIỂN & XƯNG HÔ BẮT BUỘC (Bộ: {name}):"]
    if style_note:
        lines.append(f"- Văn phong / xưng hô: {style_note}")

    if entries:
        ordered = list(entries)
        if isinstance(source_text, str) and source_text:
            lower = source_text.casefold()
            matched = [item for item in ordered if _entry_matches_text(item["source"], source_text, lower)]
            unmatched = [item for item in ordered if not _entry_matches_text(item["source"], source_text, lower)]
            ordered = matched + unmatched

        lines.append("- Bảng thuật ngữ cố định (bắt buộc dịch thống nhất):")
        current_len = sum(len(line) + 1 for line in lines)
        included = 0
        omitted = 0
        reserve_footer = 70
        for item in ordered:
            note = (item.get("note") or "").strip()
            bullet = f"  * {item['source']} => {item['target']}" + (f" ({note})" if note else "")
            if current_len + len(bullet) + 1 > max_chars - reserve_footer:
                omitted += 1
                continue
            lines.append(bullet)
            current_len += len(bullet) + 1
            included += 1
        if omitted > 0:
            footer = f"  (Đã ưu tiên {included}/{len(ordered)} mục sát nội dung đoạn hiện tại để gọn ngữ cảnh.)"
            if current_len + len(footer) + 1 <= max_chars:
                lines.append(footer)

    result = "\n".join(lines)
    return result[:max_chars]


def _entry_pattern(source: str) -> str:
    escaped = re.escape(source)
    starts_latin = bool(_LATIN_WORD_CHAR_RE.match(source[0]))
    ends_latin = bool(_LATIN_WORD_CHAR_RE.match(source[-1]))
    prefix = r"(?<!\w)" if starts_latin else ""
    suffix = r"(?!\w)" if ends_latin else ""
    return f"{prefix}{escaped}{suffix}"


def apply_glossary_to_cues(cues: list[dict], profile: dict | None) -> tuple[list[dict], dict]:
    """Single-pass safe glossary replacement on cue texts only (never timestamps/indices)."""
    result = [dict(cue) for cue in cues]
    stats = {
        "changed_cues": 0,
        "replacements": 0,
        "glossary_id": (profile or {}).get("id", DEFAULT_PROFILE_ID) if isinstance(profile, dict) else DEFAULT_PROFILE_ID,
        "glossary_name": (profile or {}).get("name", "Mặc định") if isinstance(profile, dict) else "Mặc định",
    }
    if not isinstance(profile, dict):
        return result, stats
    entries = [
        item for item in (profile.get("entries") or [])
        if isinstance(item, dict) and item.get("source") and item.get("target")
    ]
    if not entries:
        return result, stats

    # Longest source first so compound phrases win over sub-terms; single regex pass avoids chaining A->B->C.
    ordered = sorted(entries, key=lambda item: len(item["source"]), reverse=True)
    exact_map: dict[str, str] = {}
    fold_map: dict[str, str] = {}
    patterns = []
    for item in ordered:
        src = item["source"]
        tgt = item["target"]
        exact_map[src] = tgt
        fold_map[src.casefold()] = tgt
        patterns.append(_entry_pattern(src))
    regex = re.compile("|".join(patterns), flags=re.IGNORECASE | re.UNICODE)

    for cue in result:
        original = cue.get("text", "")
        if not isinstance(original, str) or not original:
            continue
        cue_hits = 0

        def replace(match):
            nonlocal cue_hits
            matched_text = match.group(0)
            replacement = exact_map.get(matched_text)
            if replacement is None:
                replacement = fold_map.get(matched_text.casefold(), matched_text)
            if replacement != matched_text:
                cue_hits += 1
            return replacement

        updated = regex.sub(replace, original)
        if updated != original:
            cue["text"] = updated
            stats["changed_cues"] += 1
            stats["replacements"] += cue_hits
    return result, stats
