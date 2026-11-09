"""GitHub release checks and verified in-place updates for the portable EXE."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile
from threading import Lock

from vietsub.constants import (
    APP_VERSION,
    LATEST_RELEASE_API,
    LATEST_RELEASE_URL,
    POWERSHELL_EXE,
    UPDATE_CACHE_TTL_SECONDS,
    UPDATE_DOWNLOAD_MAX_BYTES,
)
from vietsub.validators import (
    is_newer_release,
    parse_release_version,
    parse_update_checksum,
    validate_release_download_url,
    validate_release_url,
)

update_cache_lock = Lock()
update_cache = {"checked_at": 0.0, "status": None}


def is_packaged_app():
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def supports_in_place_update():
    if not is_packaged_app():
        return False
    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    return not os.path.isdir(os.path.join(executable_dir, "_internal"))


def fetch_latest_release_payload():
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VietSub-Studio/{APP_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Không thể kiểm tra bản cập nhật từ GitHub.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Dữ liệu release từ GitHub không hợp lệ.")
    return payload


def release_asset_names(version):
    base_name = f"VietSub-Studio-Portable-v{version}.zip"
    return base_name, f"{base_name}.sha256.txt"


def select_update_assets(payload, version):
    zip_name, checksum_name = release_asset_names(version)
    assets = {
        str(item.get("name", "")): item
        for item in payload.get("assets", [])
        if isinstance(item, dict)
    }
    zip_asset = assets.get(zip_name)
    checksum_asset = assets.get(checksum_name)
    if not zip_asset or not checksum_asset:
        raise RuntimeError("Release mới chưa có đủ file ZIP và checksum để tự cập nhật.")
    for asset in (zip_asset, checksum_asset):
        validate_release_download_url(asset.get("browser_download_url"), version)
    return zip_asset, checksum_asset


def get_update_status(force=False):
    now = time.time()
    with update_cache_lock:
        cached = update_cache.get("status")
        if cached and not force and now - update_cache["checked_at"] < UPDATE_CACHE_TTL_SECONDS:
            return dict(cached)

        payload = fetch_latest_release_payload()
        latest_version = str(payload.get("tag_name", "")).strip().lstrip("v")
        parse_release_version(latest_version)
        release_url = validate_release_url(payload.get("html_url") or LATEST_RELEASE_URL)
        try:
            select_update_assets(payload, latest_version)
            install_assets_available = True
        except (RuntimeError, ValueError):
            install_assets_available = False
        status = {
            "current_version": APP_VERSION,
            "latest_version": latest_version,
            "update_available": is_newer_release(latest_version),
            "release_name": str(payload.get("name") or f"VietSub Studio v{latest_version}"),
            "release_url": release_url,
            "automatic_update_supported": supports_in_place_update() and install_assets_available,
        }
        update_cache.update({"checked_at": now, "status": status})
        return dict(status)


def download_release_asset(url, destination, max_bytes):
    request_headers = {
        "Accept": "application/octet-stream",
        "User-Agent": f"VietSub-Studio/{APP_VERSION}",
    }
    digest = hashlib.sha256()
    total_bytes = 0
    req = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response, open(destination, "wb") as output:
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


def prepare_update_executable(payload):
    if not supports_in_place_update():
        raise RuntimeError("Tự cập nhật tại chỗ chỉ hỗ trợ bản portable one-file.")

    latest_version = str(payload.get("tag_name", "")).strip().lstrip("v")
    parse_release_version(latest_version)
    if not is_newer_release(latest_version):
        raise RuntimeError("Bạn đang dùng phiên bản mới nhất.")
    zip_asset, checksum_asset = select_update_assets(payload, latest_version)
    zip_name, checksum_name = release_asset_names(latest_version)

    update_dir = tempfile.mkdtemp(prefix="VietSub-Studio-update-")
    staged_path = ""
    try:
        checksum_path = os.path.join(update_dir, checksum_name)
        download_release_asset(
            checksum_asset["browser_download_url"],
            checksum_path,
            1024 * 1024,
        )
        with open(checksum_path, encoding="utf-8-sig") as checksum_file:
            expected_hash = parse_update_checksum(checksum_file.read(), zip_name)

        zip_path = os.path.join(update_dir, zip_name)
        downloaded_hash, downloaded_size = download_release_asset(
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

        extracted_exe = os.path.join(update_dir, "VietSub Studio.exe")
        expected_member = "VietSub Studio/VietSub Studio.exe"
        try:
            with zipfile.ZipFile(zip_path) as archive:
                members = {
                    item.filename.replace("\\", "/"): item
                    for item in archive.infolist()
                    if not item.is_dir()
                }
                member = members.get(expected_member)
                if member is None or member.file_size <= 0:
                    raise RuntimeError("ZIP cập nhật không chứa đúng file VietSub Studio.exe.")
                if member.file_size > UPDATE_DOWNLOAD_MAX_BYTES:
                    raise RuntimeError("File EXE cập nhật vượt quá kích thước cho phép.")
                with archive.open(member) as source, open(extracted_exe, "wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        except (OSError, zipfile.BadZipFile) as error:
            raise RuntimeError("Không thể đọc ZIP cập nhật đã tải.") from error

        with open(extracted_exe, "rb") as executable_file:
            if executable_file.read(2) != b"MZ":
                raise RuntimeError("File EXE cập nhật không hợp lệ.")

        target_path = os.path.abspath(sys.executable)
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


# Runs after the app exits: swaps in the staged EXE, restarts it, and rolls back
# to the previous EXE if the new build exits within its first seconds.
UPDATE_HELPER_SCRIPT = r"""
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


def launch_update_helper(update_package):
    helper_env = os.environ.copy()
    helper_env.update({
        "VIETSUB_UPDATE_PID": str(os.getpid()),
        "VIETSUB_UPDATE_TARGET": update_package["target_path"],
        "VIETSUB_UPDATE_STAGED": update_package["staged_path"],
        "VIETSUB_UPDATE_TEMP": update_package["update_dir"],
    })
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    return subprocess.Popen(
        [
            POWERSHELL_EXE,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            UPDATE_HELPER_SCRIPT,
        ],
        env=helper_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )
