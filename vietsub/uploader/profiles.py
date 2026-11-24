"""Private, versioned uploader accounts and exclusive browser-profile leases.

Only non-secret account metadata is returned by this module.  Each UUID has its
own platform directory, browser user-data directory and private storage snapshot.
Deleting an account removes its registry record, never its browser files.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import uuid


PLATFORMS = ("tiktok", "youtube", "facebook", "douyin", "bilibili", "xiaohongshu")
SCHEMA_VERSION = 1
SESSION_STATUSES = frozenset(("unknown", "alive", "expired", "needs_verification", "error"))
_MAX_REGISTRY_BYTES = 2 * 1024 * 1024
_SAFE_DETAILS = frozenset((
    "session_alive", "session_expired", "verification_required", "probe_failed",
    "login_completed", "login_cancelled", "login_timeout", "browser_unavailable",
    "upload_failed", "upload_completed", "login_required", "unknown",
))
_DEFAULT_DETAILS = {
    "unknown": "unknown", "alive": "session_alive", "expired": "session_expired",
    "needs_verification": "verification_required", "error": "probe_failed",
}
_PROCESS_LOCKS = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class ProfileStoreError(RuntimeError):
    """Account storage is unavailable; existing data has not been overwritten."""


class ProfileRegistryError(ProfileStoreError):
    """Malformed or unsupported registry.  Recovery requires explicit user action."""


class ProfileBusy(ProfileStoreError):
    """Another worker or process owns this browser profile."""


class ProfileNotFound(KeyError):
    """The canonical profile UUID is not in the registry."""


class UnsafeProfilePath(ProfileStoreError, ValueError):
    """A profile path includes a symlink, junction or unexpected file type."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate registry key")
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError("Invalid registry number")


def _profile_id(value):
    if not isinstance(value, str):
        raise ValueError("A canonical profile UUID is required.")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("A canonical profile UUID is required.") from error
    if str(parsed) != value:
        raise ValueError("A canonical profile UUID is required.")
    return value


def _name(value):
    if not isinstance(value, str):
        raise ValueError("Account name must be text.")
    value = value.strip()
    if not 1 <= len(value) <= 80 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Account name must contain 1-80 printable characters.")
    return value


def _assert_plain_path(path, *, kind=None):
    """Inspect lexical ancestors, rather than resolving away a redirect first.

    Windows directory junctions are reparse points but need not be symlinks.
    Refuse all reparse points, including on Python versions without isjunction.
    """
    path = Path(os.path.abspath(os.fspath(path)))
    for part in reversed((path, *path.parents)):
        try:
            info = os.lstat(part)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise UnsafeProfilePath("Cannot safely inspect uploader storage.") from error
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise UnsafeProfilePath("Uploader storage cannot contain symlinks or junctions.")
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise UnsafeProfilePath("An uploader storage ancestor is not a directory.")
        if part == path and kind == "directory" and not stat.S_ISDIR(info.st_mode):
            raise UnsafeProfilePath("Expected a private uploader directory.")
        if part == path and kind == "file" and not stat.S_ISREG(info.st_mode):
            raise UnsafeProfilePath("Expected a regular uploader file.")
    return path


def _assert_plain_tree(directory):
    """Chromium will read descendants itself: refuse redirected profile contents."""
    directory = _assert_plain_path(directory, kind="directory")
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                path = _assert_plain_path(entry.path)
                info = os.lstat(path)
                if stat.S_ISDIR(info.st_mode):
                    _assert_plain_tree(path)
                elif not stat.S_ISREG(info.st_mode):
                    raise UnsafeProfilePath("Browser profile contains an unsafe file type.")
    except OSError as error:
        raise UnsafeProfilePath("Cannot safely inspect browser profile contents.") from error


def _mkdir(path):
    path = _assert_plain_path(path, kind="directory")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise ProfileStoreError("Cannot create private uploader storage.") from error
    return _assert_plain_path(path, kind="directory")


def _open_private(path, flags):
    path = _assert_plain_path(path, kind="file")
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise ProfileStoreError("Cannot open private uploader storage.") from error
    try:
        _assert_plain_path(path, kind="file")
        opened, named = os.fstat(descriptor), os.lstat(path)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise UnsafeProfilePath("Uploader storage changed while opening it.")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _atomic_json(path, payload):
    """Same-directory replace, restrictive creation mode, and no partial JSON."""
    path = _assert_plain_path(path, kind="file")
    _mkdir(path.parent)
    temporary = path.parent / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    descriptor = _open_private(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_plain_path(temporary, kind="file")
        _assert_plain_path(path, kind="file")
        os.replace(temporary, path)
    finally:
        # Only our exact random temporary file may be unlinked, never a directory.
        _assert_plain_path(temporary, kind="file")
        if temporary.exists():
            temporary.unlink()


def _local_lock(path):
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def _file_lock(path, *, blocking, busy_error=ProfileBusy):
    """OS-owned locks survive neither close nor process exit; lockfiles stay put.

    Never unlink a lockfile: replacing its inode could let a second process lock
    a different file while the first process still owns the original one.
    """
    local = _local_lock(path)
    if not local.acquire(blocking=blocking):
        raise busy_error("The uploader profile is busy.")
    descriptor = None
    locked = False
    try:
        descriptor = _open_private(path, os.O_RDWR | os.O_CREAT)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EBUSY) or getattr(error, "winerror", None) in (32, 33, 36):
                raise busy_error("The uploader profile is busy.") from error
            raise ProfileStoreError("Cannot lock uploader storage.") from error
        locked = True
        yield
    finally:
        try:
            if descriptor is not None:
                try:
                    if locked:
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        finally:
            local.release()


class ProfileStore:
    """An uploader-only store rooted at ``CONFIG_DIR / 'uploader'``.

    ``list``/``get`` return fresh, whitelisted metadata, never filesystem paths,
    storage state, browser cookies or arbitrary exception/detail text.
    """

    def __init__(self, root):
        self.root = _mkdir(root)
        self.registry_path = self.root / "accounts.json"
        self._registry_lock = self.root / ".registry.lock"
        self._profiles = self.root / "profiles"
        self._locks = _mkdir(self.root / ".locks")
        with self._registry():
            self._read_registry()  # Fail closed immediately, including newer versions.

    @contextmanager
    def _registry(self):
        _assert_plain_path(self.root, kind="directory")
        with _file_lock(self._registry_lock, blocking=True, busy_error=ProfileStoreError):
            yield

    def _read_registry(self):
        path = _assert_plain_path(self.registry_path, kind="file")
        if not path.exists():
            return {"schema_version": SCHEMA_VERSION, "accounts": []}
        descriptor = _open_private(path, os.O_RDONLY)
        try:
            with os.fdopen(descriptor, "rb") as handle:
                raw = handle.read(_MAX_REGISTRY_BYTES + 1)
            if len(raw) > _MAX_REGISTRY_BYTES:
                raise ValueError("Registry too large")
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_invalid_json_constant)
            if not isinstance(payload, dict) or set(payload) != {"schema_version", "accounts"}:
                raise ValueError("Invalid registry")
            if type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
                raise ValueError("Unsupported schema")
            if not isinstance(payload["accounts"], list):
                raise ValueError("Invalid accounts")
            seen = set()
            required = {"id", "platform", "name", "session_status", "session_detail", "created_at", "updated_at", "session_checked_at"}
            for record in payload["accounts"]:
                if not isinstance(record, dict) or set(record) != required:
                    raise ValueError("Invalid account")
                identifier = _profile_id(record["id"])
                if identifier in seen or record["platform"] not in PLATFORMS or _name(record["name"]) != record["name"]:
                    raise ValueError("Invalid account")
                seen.add(identifier)
                if record["session_status"] not in SESSION_STATUSES or record["session_detail"] not in _SAFE_DETAILS:
                    raise ValueError("Invalid session")
                for key in ("created_at", "updated_at", "session_checked_at"):
                    value = record[key]
                    if key == "session_checked_at" and value is None:
                        continue
                    if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
                        raise ValueError("Invalid timestamp")
            return payload
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError) as error:
            raise ProfileRegistryError("Uploader account registry is invalid or uses an unsupported version; it was not overwritten.") from error

    @staticmethod
    def _find(payload, identifier):
        for record in payload["accounts"]:
            if record["id"] == identifier:
                return record
        raise ProfileNotFound(identifier)

    def create(self, platform, name):
        if platform not in PLATFORMS:
            raise ValueError("Unsupported uploader platform.")
        name = _name(name)
        with self._registry():
            payload = self._read_registry()
            for _ in range(8):
                identifier = str(uuid.uuid4())
                candidate = _assert_plain_path(self._profiles / identifier, kind="directory")
                if not candidate.exists() and all(record["id"] != identifier for record in payload["accounts"]):
                    break
            else:
                raise ProfileStoreError("Cannot allocate a new independent account UUID.")
            now = _now()
            record = {"id": identifier, "platform": platform, "name": name,
                      "session_status": "unknown", "session_detail": "unknown",
                      "created_at": now, "updated_at": now, "session_checked_at": None}
            directory = self._directory(record)
            _mkdir(directory / "browser")
            _atomic_json(directory / "config.json", {"schema_version": SCHEMA_VERSION, "id": identifier, "platform": platform})
            payload["accounts"].append(record)
            _atomic_json(self.registry_path, payload)
            return dict(record)

    def list(self):
        with self._registry():
            return [dict(record) for record in self._read_registry()["accounts"]]

    def get(self, identifier):
        identifier = _profile_id(identifier)
        with self._registry():
            return dict(self._find(self._read_registry(), identifier))

    def _directory(self, record):
        identifier = _profile_id(record["id"])
        if record["platform"] not in PLATFORMS:
            raise ValueError("Unsupported uploader platform.")
        _assert_plain_path(self.root, kind="directory")
        return _mkdir(self._profiles / identifier / record["platform"])

    def profile_dir(self, identifier):
        return str(_mkdir(self._directory(self.get(identifier)) / "browser"))

    def state_path(self, identifier):
        return str(_assert_plain_path(self._directory(self.get(identifier)) / "storage_state.json", kind="file"))

    def config_path(self, identifier):
        return str(_assert_plain_path(self._directory(self.get(identifier)) / "config.json", kind="file"))

    def artifact_dir(self, identifier):
        return str(_mkdir(self._directory(self.get(identifier)) / "artifacts"))

    def save_storage_state(self, identifier, state):
        """Private atomic snapshot; never included in account metadata or logs."""
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
            raise ValueError("Invalid browser storage snapshot.")
        _atomic_json(Path(self.state_path(identifier)), state)

    def update_session(self, identifier, status, detail=""):
        identifier = _profile_id(identifier)
        if status not in SESSION_STATUSES:
            raise ValueError("Unsupported uploader session status.")
        # Exception messages, URLs, cookies and arbitrary adapter text are not public metadata.
        safe_detail = detail if isinstance(detail, str) and detail in _SAFE_DETAILS else _DEFAULT_DETAILS[status]
        with self._registry():
            payload = self._read_registry()
            record = self._find(payload, identifier)
            record.update(session_status=status, session_detail=safe_detail, session_checked_at=_now(), updated_at=_now())
            _atomic_json(self.registry_path, payload)
            return dict(record)

    @contextmanager
    def lease(self, identifier):
        """Nonblocking across threads, store instances and OS processes."""
        identifier = _profile_id(identifier)
        self.get(identifier)
        path = _assert_plain_path(self._locks / (identifier + ".lock"), kind="file")
        with _file_lock(path, blocking=False):
            self.get(identifier)  # Deletion may have won the race before lock acquisition.
            _assert_plain_tree(self.profile_dir(identifier))
            self.state_path(identifier)
            self.config_path(identifier)
            yield

    def rename(self, identifier, name):
        name = _name(name)
        with self.lease(identifier):
            with self._registry():
                payload = self._read_registry()
                record = self._find(payload, _profile_id(identifier))
                record.update(name=name, updated_at=_now())
                _atomic_json(self.registry_path, payload)
                return dict(record)

    def delete(self, identifier, *, before_delete=None, clean_artifacts=False):
        """Remove the account record and optionally purge its screenshots; browser files stay intact."""
        with self.lease(identifier):
            if before_delete is not None:
                before_delete()
            artifact_dir = Path(self.artifact_dir(identifier)) if clean_artifacts else None
            with self._registry():
                payload = self._read_registry()
                self._find(payload, _profile_id(identifier))
                payload["accounts"] = [item for item in payload["accounts"] if item["id"] != identifier]
                _atomic_json(self.registry_path, payload)
            if artifact_dir is not None and artifact_dir.exists():
                _assert_plain_tree(artifact_dir)
                if artifact_dir.name == "artifacts" and artifact_dir.parent.parent.parent == self._profiles:
                    shutil.rmtree(artifact_dir, ignore_errors=True)
        return True
