"""Verified auto-updater with SHA-256 and fail-closed Ed25519 signature checks."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
import zipfile
from urllib.parse import urlparse

GITHUB_REPOSITORY = "qvinh8726/VietSub-Studio"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
LATEST_RELEASE_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
UPDATE_CACHE_TTL_SECONDS = 15 * 60
UPDATE_DOWNLOAD_MAX_BYTES = 512 * 1024 * 1024
UPDATE_SIGNATURE_MAX_BYTES = 4096
# Every portable build since v1.3.0 extracts exactly this member from the release ZIP.
UPDATE_ZIP_EXE_MEMBER = "VietSub Studio/VietSub Studio.exe"

SIGNATURE_FORMAT_VERSION = "vietsub-ed25519-v1"
SIGNATURE_DOMAIN = "vietsub-studio-release-v1"
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")

# Fail-closed embedded Ed25519 public keys: {key_id_16_hex: raw_32_byte_pubkey_64_hex}.
# Generate an offline signing key outside the repo via `python tools/sign_release.py keygen`,
# then paste only the printed key_id -> public_key_hex mapping here before tagging v2.1.0+.
RELEASE_PUBLIC_KEYS: dict[str, str] = {}


def _prerelease_tokens(prerelease):
    if not prerelease:
        return ()
    tokens = []
    for part in prerelease.split("."):
        if not part:
            raise ValueError("Phiên bản release không đúng định dạng.")
        tokens.append((0, int(part)) if part.isdigit() else (1, part.lower()))
    return tuple(tokens)


class ReleaseVersion(tuple):
    """Semantic version 3-tuple whose comparisons preserve SemVer pre-release order."""

    def __new__(cls, major, minor, patch, prerelease=()):
        instance = super().__new__(cls, (major, minor, patch))
        instance.prerelease = tuple(prerelease)
        return instance

    def _sort_key(self):
        return (tuple(self), 1 if not self.prerelease else 0, self.prerelease)

    @staticmethod
    def _other_key(other):
        if isinstance(other, ReleaseVersion):
            return other._sort_key()
        if isinstance(other, tuple) and len(other) == 3:
            return (other, 1, ())
        return None

    def __eq__(self, other):
        key = self._other_key(other)
        return self._sort_key() == key if key is not None else NotImplemented

    def __ne__(self, other):
        eq = self.__eq__(other)
        return NotImplemented if eq is NotImplemented else not eq

    def __lt__(self, other):
        key = self._other_key(other)
        return self._sort_key() < key if key is not None else NotImplemented

    def __le__(self, other):
        key = self._other_key(other)
        return self._sort_key() <= key if key is not None else NotImplemented

    def __gt__(self, other):
        key = self._other_key(other)
        return self._sort_key() > key if key is not None else NotImplemented

    def __ge__(self, other):
        key = self._other_key(other)
        return self._sort_key() >= key if key is not None else NotImplemented

    def __hash__(self):
        return hash(self._sort_key())


def parse_release_version(value):
    if not isinstance(value, str):
        raise ValueError("Phiên bản release không hợp lệ.")
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
        value.strip(),
    )
    if not match:
        raise ValueError("Phiên bản release không đúng định dạng.")
    major, minor, patch = (int(part) for part in match.groups()[:3])
    prerelease = _prerelease_tokens(match.group(4))
    return ReleaseVersion(major, minor, patch, prerelease)


def is_newer_release(latest_version, current_version):
    return parse_release_version(latest_version) > parse_release_version(current_version)


def validate_release_url(value, repository=GITHUB_REPOSITORY):
    parsed = urlparse(value or "")
    expected_prefix = f"/{repository.lower()}/releases/"
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
        raise ValueError("Link cập nhật không thuộc GitHub chính thức.")
    if not parsed.path.lower().startswith(expected_prefix):
        raise ValueError("Link cập nhật không thuộc repository chính thức.")
    return value


def validate_release_download_url(value, version, repository=GITHUB_REPOSITORY):
    parsed = urlparse(value or "")
    expected_prefix = f"/{repository.lower()}/releases/download/v{version}/"
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
        raise ValueError("File cập nhật không thuộc GitHub chính thức.")
    if not parsed.path.lower().startswith(expected_prefix):
        raise ValueError("File cập nhật không thuộc đúng release chính thức.")
    return value


def release_asset_names(version):
    base_name = f"VietSub-Studio-Portable-v{version}.zip"
    return base_name, f"{base_name}.sha256.txt"


def release_signature_name(version):
    zip_name, _ = release_asset_names(version)
    return f"{zip_name}.sig"


def signed_release_asset_names(version):
    zip_name, checksum_name = release_asset_names(version)
    return zip_name, checksum_name, f"{zip_name}.sig"


def public_key_id(raw_public_bytes):
    if not isinstance(raw_public_bytes, (bytes, bytearray)) or len(raw_public_bytes) != 32:
        raise ValueError("Public key Ed25519 phải dài đúng 32 bytes.")
    return hashlib.sha256(bytes(raw_public_bytes)).hexdigest()[:16]


def canonical_release_message(version, zip_name, zip_sha256):
    normalized_version = str(version or "").strip().lstrip("v")
    parse_release_version(normalized_version)
    normalized_zip = os.path.basename(str(zip_name or "").strip())
    if not normalized_zip or normalized_zip != str(zip_name or "").strip():
        raise ValueError("Tên file ZIP khi ký không hợp lệ.")
    digest_hex = str(zip_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest_hex):
        raise ValueError("SHA-256 của file ZIP không hợp lệ.")
    return f"{SIGNATURE_DOMAIN}\n{normalized_version}\n{normalized_zip}\nsha256:{digest_hex}\n".encode("ascii")


def format_signature_text(key_id, signature_bytes):
    if not isinstance(key_id, str) or not re.fullmatch(r"[0-9a-f]{16}", key_id):
        raise ValueError("Key ID chữ ký Ed25519 không hợp lệ.")
    if not isinstance(signature_bytes, (bytes, bytearray)) or len(signature_bytes) != 64:
        raise ValueError("Chữ ký Ed25519 phải dài đúng 64 bytes.")
    sig_b64 = base64.b64encode(bytes(signature_bytes)).decode("ascii")
    return f"{SIGNATURE_FORMAT_VERSION} {key_id} {sig_b64}\n"


def parse_update_signature(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("File chữ ký Ed25519 của release không hợp lệ.")
    parts = lines[0].split()
    if len(parts) != 3:
        raise ValueError("File chữ ký Ed25519 của release không đúng định dạng.")
    scheme, key_id, sig_b64 = parts
    if scheme != SIGNATURE_FORMAT_VERSION:
        raise ValueError("Phiên bản chữ ký Ed25519 không được hỗ trợ.")
    if not re.fullmatch(r"[0-9a-f]{16}", key_id):
        raise ValueError("Key ID trong file chữ ký không hợp lệ.")
    try:
        signature_bytes = base64.b64decode(sig_b64.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError("Dữ liệu chữ ký Ed25519 không đúng base64.") from error
    if len(signature_bytes) != 64:
        raise ValueError("Chữ ký Ed25519 phải dài đúng 64 bytes.")
    return key_id, signature_bytes


def _import_ed25519_public_key(raw_bytes):
    from Cryptodome.PublicKey import ECC

    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) != 32:
        raise ValueError("Public key Ed25519 phải dài đúng 32 bytes.")
    raw = bytes(raw_bytes)
    if not any(raw) or len(set(raw)) <= 1:
        raise ValueError("Public key placeholder không được phép.")
    key = ECC.import_key(ED25519_SPKI_PREFIX + raw)
    if getattr(key, "curve", None) != "Ed25519":
        raise ValueError("Khóa không thuộc đường cong Ed25519.")
    return key


def load_trusted_public_keys(public_keys=None):
    """Load and validate embedded Ed25519 public keys (fail-closed, never env-overridable)."""
    source = RELEASE_PUBLIC_KEYS if public_keys is None else public_keys
    if not isinstance(source, dict) or not source:
        raise RuntimeError(
            "Khóa công khai Ed25519 xác thực bản cập nhật chưa được cấu hình; đã chặn tự cài đặt."
        )
    loaded = {}
    for key_id, hex_value in source.items():
        if not isinstance(key_id, str) or not re.fullmatch(r"[0-9a-f]{16}", key_id):
            raise RuntimeError("Khóa công khai xác thực bản cập nhật không hợp lệ.")
        if not isinstance(hex_value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", hex_value.strip()):
            raise RuntimeError("Khóa công khai xác thực bản cập nhật không hợp lệ.")
        try:
            raw = bytes.fromhex(hex_value.strip())
            if public_key_id(raw) != key_id:
                raise ValueError("Key ID mismatch")
            loaded[key_id] = _import_ed25519_public_key(raw)
        except Exception as error:
            raise RuntimeError("Khóa công khai xác thực bản cập nhật không hợp lệ.") from error
    if not loaded:
        raise RuntimeError(
            "Khóa công khai Ed25519 xác thực bản cập nhật chưa được cấu hình; đã chặn tự cài đặt."
        )
    return loaded


def has_valid_public_keys(public_keys=None):
    try:
        return bool(load_trusted_public_keys(public_keys))
    except RuntimeError:
        return False


def verify_release_signature(version, zip_name, zip_sha256, signature_text, *, public_keys=None):
    """Verify Ed25519 release signature over the canonical domain-separated release message."""
    from Cryptodome.Signature import eddsa

    trusted = load_trusted_public_keys(public_keys)
    try:
        key_id, signature_bytes = parse_update_signature(signature_text)
        message = canonical_release_message(version, zip_name, zip_sha256)
    except ValueError as error:
        raise RuntimeError(f"Chữ ký bản cập nhật không hợp lệ: {error}") from error
    key = trusted.get(key_id)
    if key is None:
        raise RuntimeError("Chữ ký bản cập nhật dùng khóa không nằm trong danh sách tin cậy.")
    try:
        eddsa.new(key, "rfc8032").verify(message, signature_bytes)
    except (ValueError, TypeError) as error:
        raise RuntimeError("Xác thực chữ ký Ed25519 thất bại; gói cập nhật không đáng tin cậy.") from error
    return key_id


def fetch_latest_release_payload(*, current_version, api_url=LATEST_RELEASE_API, urlopen=urllib.request.urlopen):
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VietSub-Studio/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Không thể kiểm tra bản cập nhật từ GitHub.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Dữ liệu release từ GitHub không hợp lệ.")
    return payload


def select_update_assets(payload, version, *, require_signature=True, repository=GITHUB_REPOSITORY):
    zip_name, checksum_name, sig_name = signed_release_asset_names(version)
    assets = {
        str(item.get("name", "")): item
        for item in payload.get("assets", [])
        if isinstance(item, dict)
    }
    zip_asset = assets.get(zip_name)
    checksum_asset = assets.get(checksum_name)
    if not zip_asset or not checksum_asset:
        raise RuntimeError("Release mới chưa có đủ file ZIP và checksum để tự cập nhật.")
    validate_release_download_url(zip_asset.get("browser_download_url"), version, repository=repository)
    validate_release_download_url(checksum_asset.get("browser_download_url"), version, repository=repository)
    if not require_signature:
        return zip_asset, checksum_asset
    sig_asset = assets.get(sig_name)
    if not sig_asset:
        raise RuntimeError("Release mới thiếu tệp chữ ký Ed25519 (.zip.sig) bắt buộc để tự cập nhật.")
    validate_release_download_url(sig_asset.get("browser_download_url"), version, repository=repository)
    return zip_asset, checksum_asset, sig_asset


def get_update_status(
    *,
    force=False,
    cache,
    cache_lock,
    current_version,
    fetch_payload,
    supports_update,
    public_keys=None,
):
    now = __import__("time").time()
    with cache_lock:
        cached = cache.get("status")
        if cached and not force and now - cache["checked_at"] < UPDATE_CACHE_TTL_SECONDS:
            return dict(cached)

        payload = fetch_payload()
        latest_version = str(payload.get("tag_name", "")).strip().lstrip("v")
        parse_release_version(latest_version)
        release_url = validate_release_url(payload.get("html_url") or LATEST_RELEASE_URL)
        try:
            select_update_assets(payload, latest_version, require_signature=True)
            install_assets_available = has_valid_public_keys(public_keys)
        except (RuntimeError, ValueError):
            install_assets_available = False
        status = {
            "current_version": current_version,
            "latest_version": latest_version,
            "update_available": is_newer_release(latest_version, current_version),
            "release_name": str(payload.get("name") or f"VietSub Studio v{latest_version}"),
            "release_url": release_url,
            "automatic_update_supported": bool(supports_update() and install_assets_available),
        }
        cache.update({"checked_at": now, "status": status})
        return dict(status)


def parse_update_checksum(text, expected_filename):
    for line in str(text or "").splitlines():
        match = re.fullmatch(r"\s*([0-9A-Fa-f]{64})\s+\*?(.+?)\s*", line)
        if match and match.group(2) == expected_filename:
            return match.group(1).lower()
    raise ValueError("File checksum của release không hợp lệ.")


def find_update_executable_member(archive):
    members = {
        item.filename.replace("\\", "/"): item
        for item in archive.infolist()
        if not item.is_dir()
    }
    member = members.get(UPDATE_ZIP_EXE_MEMBER)
    if member is None or member.file_size <= 0:
        raise RuntimeError("ZIP cập nhật không chứa đúng file VietSub Studio.exe.")
    if member.file_size > UPDATE_DOWNLOAD_MAX_BYTES:
        raise RuntimeError("File EXE cập nhật vượt quá kích thước cho phép.")
    return member


def download_release_asset(url, destination, max_bytes, *, current_version="0.0.0", urlopen=urllib.request.urlopen):
    request_headers = {
        "Accept": "application/octet-stream",
        "User-Agent": f"VietSub-Studio/{current_version}",
    }
    digest = hashlib.sha256()
    total_bytes = 0
    req = urllib.request.Request(url, headers=request_headers)
    try:
        with urlopen(req, timeout=30) as response, open(destination, "wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise RuntimeError("File cập nhật vượt quá kích thước cho phép.")
                digest.update(chunk)
                output.write(chunk)
    except OSError as error:
        raise RuntimeError("Không thể tải file cập nhật từ GitHub.") from error
    return digest.hexdigest(), total_bytes


def prepare_update_executable(
    payload,
    *,
    current_version,
    supports_update,
    current_executable,
    download_asset,
    public_keys=None,
):
    if not supports_update():
        raise RuntimeError("Tự cập nhật tại chỗ chỉ hỗ trợ bản portable one-file.")

    latest_version = str(payload.get("tag_name", "")).strip().lstrip("v")
    parse_release_version(latest_version)
    if not is_newer_release(latest_version, current_version):
        raise RuntimeError("Bạn đang dùng phiên bản mới nhất.")

    trusted_keys = load_trusted_public_keys(public_keys)
    zip_asset, checksum_asset, sig_asset = select_update_assets(
        payload, latest_version, require_signature=True
    )
    zip_name, checksum_name, sig_name = signed_release_asset_names(latest_version)

    update_dir = tempfile.mkdtemp(prefix="VietSub-Studio-update-")
    staged_path = ""
    try:
        checksum_path = os.path.join(update_dir, checksum_name)
        download_asset(
            checksum_asset["browser_download_url"],
            checksum_path,
            1024 * 1024,
        )
        with open(checksum_path, "r", encoding="utf-8-sig") as checksum_file:
            expected_hash = parse_update_checksum(checksum_file.read(), zip_name)

        sig_path = os.path.join(update_dir, sig_name)
        download_asset(
            sig_asset["browser_download_url"],
            sig_path,
            UPDATE_SIGNATURE_MAX_BYTES,
        )
        with open(sig_path, "r", encoding="utf-8-sig") as sig_file:
            signature_text = sig_file.read(UPDATE_SIGNATURE_MAX_BYTES + 1)
        try:
            key_id, _ = parse_update_signature(signature_text)
        except ValueError as error:
            raise RuntimeError(f"Chữ ký bản cập nhật không hợp lệ: {error}") from error
        if key_id not in trusted_keys:
            raise RuntimeError("Chữ ký bản cập nhật dùng khóa không nằm trong danh sách tin cậy.")

        zip_path = os.path.join(update_dir, zip_name)
        downloaded_hash, downloaded_size = download_asset(
            zip_asset["browser_download_url"],
            zip_path,
            UPDATE_DOWNLOAD_MAX_BYTES,
        )
        if downloaded_hash.lower() != expected_hash:
            raise RuntimeError("Checksum ZIP cập nhật không khớp; đã huỷ cài đặt.")
        api_digest = str(zip_asset.get("digest") or "").lower()
        if api_digest and api_digest != f"sha256:{downloaded_hash.lower()}":
            raise RuntimeError("Digest GitHub của file cập nhật không khớp.")
        declared_size = int(zip_asset.get("size") or 0)
        if declared_size and downloaded_size != declared_size:
            raise RuntimeError("Kích thước file cập nhật không khớp với GitHub.")

        verify_release_signature(
            latest_version,
            zip_name,
            downloaded_hash,
            signature_text,
            public_keys=public_keys,
        )

        extracted_exe = os.path.join(update_dir, "VietSub Studio.exe")
        try:
            with zipfile.ZipFile(zip_path) as archive:
                member = find_update_executable_member(archive)
                exe_digest = hashlib.sha256()
                with archive.open(member) as source, open(extracted_exe, "wb") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        exe_digest.update(chunk)
                        destination.write(chunk)
                names = {item.filename.replace("\\", "/") for item in archive.infolist() if not item.is_dir()}
                if "VietSub Studio/SHA256.txt" in names:
                    inner_text = archive.read("VietSub Studio/SHA256.txt").decode("ascii")
                    if parse_update_checksum(inner_text, "VietSub Studio.exe") != exe_digest.hexdigest():
                        raise RuntimeError("SHA256 của file EXE bên trong ZIP không khớp.")
        except (OSError, UnicodeDecodeError, ValueError, zipfile.BadZipFile) as error:
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError("Không thể đọc ZIP cập nhật đã tải.") from error

        with open(extracted_exe, "rb") as executable_file:
            if executable_file.read(2) != b"MZ":
                raise RuntimeError("File EXE cập nhật không hợp lệ.")

        target_path = os.path.abspath(current_executable)
        if not os.path.isfile(target_path):
            raise FileNotFoundError("Không tìm thấy file VietSub Studio.exe hiện tại.")
        staged_path = os.path.join(
            os.path.dirname(target_path),
            f".VietSub Studio.update-{uuid.uuid4().hex}.exe",
        )
        shutil.copy2(extracted_exe, staged_path)
        return {
            "version": latest_version,
            "target_path": target_path,
            "staged_path": staged_path,
            "update_dir": update_dir,
        }
    except Exception:
        if staged_path and os.path.exists(staged_path):
            try:
                os.remove(staged_path)
            except OSError:
                pass
        shutil.rmtree(update_dir, ignore_errors=True)
        raise


def launch_update_helper(update_package, *, popen=subprocess.Popen, powershell_exe="powershell.exe"):
    # PyInstaller >= 6.9 treats a process started from the same one-file executable
    # as a worker that reuses the parent's unpacked files. The updated app must
    # outlive this process, so it needs a fresh bootloader environment.
    helper_env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("_PYI_") and key.upper() != "_MEIPASS2"
    }
    helper_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    helper_env.update({
        "VIETSUB_UPDATE_PID": str(os.getpid()),
        "VIETSUB_UPDATE_TARGET": update_package["target_path"],
        "VIETSUB_UPDATE_STAGED": update_package["staged_path"],
        "VIETSUB_UPDATE_TEMP": update_package["update_dir"],
    })
    helper_script = r"""
$ErrorActionPreference = 'Stop'
$appPid = [int]$env:VIETSUB_UPDATE_PID
$target = $env:VIETSUB_UPDATE_TARGET
$staged = $env:VIETSUB_UPDATE_STAGED
$updateTemp = $env:VIETSUB_UPDATE_TEMP
$backup = "$target.old"
$targetDir = Split-Path -Parent $target
Wait-Process -Id $appPid -ErrorAction SilentlyContinue
$replaced = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        if (-not (Test-Path -LiteralPath $target)) {
            if (Test-Path -LiteralPath $backup) {
                Move-Item -LiteralPath $backup -Destination $target -Force
            } else {
                throw 'Không còn file ứng dụng gốc để thay thế.'
            }
        }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
        Move-Item -LiteralPath $target -Destination $backup -Force
        try {
            Move-Item -LiteralPath $staged -Destination $target -Force
        } catch {
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
            if (Test-Path -LiteralPath $backup) {
                Move-Item -LiteralPath $backup -Destination $target -Force
            }
            throw
        }
        $replaced = $true
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $replaced) {
    if (-not (Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $target -Force
    }
    if (Test-Path -LiteralPath $target) {
        Start-Process -FilePath $target -WorkingDirectory $targetDir
    }
    exit 1
}
try {
    $newProcess = Start-Process -FilePath $target -WorkingDirectory $targetDir -PassThru
    Start-Sleep -Seconds 8
    if ($newProcess.HasExited) { throw 'Bản cập nhật thoát trước khi khởi động ổn định.' }
} catch {
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    if (Test-Path -LiteralPath $backup) {
        Move-Item -LiteralPath $backup -Destination $target -Force
        Start-Process -FilePath $target -WorkingDirectory $targetDir
    }
    exit 1
}
if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
if (Test-Path -LiteralPath $updateTemp) { Remove-Item -LiteralPath $updateTemp -Recurse -Force }
"""
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    return popen(
        [
            powershell_exe,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            helper_script,
        ],
        env=helper_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )
