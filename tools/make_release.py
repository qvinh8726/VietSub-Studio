"""Package a built portable EXE into the two assets the in-app updater expects.

Usage (from the repository root, after PyInstaller has produced the EXE):

    python tools/make_release.py --exe "dist_portable/VietSub Studio.exe" --output release --clean

The output directory then contains exactly:

    VietSub-Studio-Portable-v<version>.zip
    VietSub-Studio-Portable-v<version>.zip.sha256.txt

Upload both files, unchanged, to the GitHub Release tagged v<version>.
The asset names, checksum format and ZIP layout are a contract with every
published updater since v1.3.0; tests/test_release_contract.py guards it.
"""

import argparse
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app  # noqa: E402  (needs the repository root on sys.path)

EXE_NAME = "VietSub Studio.exe"
ZIP_ROOT = "VietSub Studio"
BUNDLED_DOCUMENTS = ("LICENSE", "HUONG_DAN_SU_DUNG.txt")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_executable(exe_path):
    exe_path = Path(exe_path)
    if not exe_path.is_file():
        raise FileNotFoundError(f"Executable not found: {exe_path}")
    size = exe_path.stat().st_size
    if size <= 0 or size > app.UPDATE_DOWNLOAD_MAX_BYTES:
        raise ValueError(f"Executable size {size} is outside the updater limit.")
    with open(exe_path, "rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError(f"{exe_path} is not a Windows executable (missing MZ header).")
    return exe_path


def prepare_output_directory(output_dir, clean):
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        if not clean:
            raise FileExistsError(f"{output_dir} is not empty; pass --clean to replace it.")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def verify_release(zip_path, checksum_path, exe_sha256):
    zip_name = Path(zip_path).name
    expected_zip_sha = app.parse_update_checksum(
        Path(checksum_path).read_bytes().decode("ascii"), zip_name
    )
    if expected_zip_sha != sha256_file(zip_path):
        raise RuntimeError("Checksum file does not match the ZIP.")
    with zipfile.ZipFile(zip_path) as archive:
        member = app.find_update_executable_member(archive)
        digest = hashlib.sha256()
        with archive.open(member, "r") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        member_sha256 = digest.hexdigest()
        if member_sha256 != exe_sha256:
            raise RuntimeError("Executable inside the ZIP does not match the input EXE.")
        inner_checksum = archive.read(f"{ZIP_ROOT}/SHA256.txt").decode("ascii")
        if app.parse_update_checksum(inner_checksum, EXE_NAME) != exe_sha256:
            raise RuntimeError("SHA256.txt inside the ZIP does not match the input EXE.")


def build_release(
    exe_path,
    output_dir,
    *,
    version=None,
    clean=False,
    sign_key=None,
    sign_key_env=None,
    passphrase=None,
    passphrase_env=None,
    expected_public_keys=None,
):
    version = str(version or app.APP_VERSION).strip().lstrip("v")
    app.parse_release_version(version)
    exe_path = validate_executable(exe_path)
    documents = [REPO_ROOT / name for name in BUNDLED_DOCUMENTS]
    for document in documents:
        if not document.is_file():
            raise FileNotFoundError(f"Bundled document not found: {document}")

    output_dir = prepare_output_directory(output_dir, clean)
    zip_name, checksum_name = app.release_asset_names(version)
    zip_path = output_dir / zip_name
    checksum_path = output_dir / checksum_name

    exe_sha256 = sha256_file(exe_path)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe_path, f"{ZIP_ROOT}/{EXE_NAME}")
        archive.writestr(f"{ZIP_ROOT}/SHA256.txt", f"{exe_sha256}  {EXE_NAME}\n")
        for document in documents:
            archive.write(document, f"{ZIP_ROOT}/{document.name}")

    checksum_path.write_bytes(f"{sha256_file(zip_path)}  {zip_name}\n".encode("ascii"))
    verify_release(zip_path, checksum_path, exe_sha256)
    if sign_key or sign_key_env:
        from tools import sign_release

        sign_release.sign_release_zip(
            zip_path,
            version=version,
            key_path=sign_key,
            key_env=sign_key_env,
            passphrase=passphrase,
            passphrase_env=passphrase_env,
            expected_public_keys=expected_public_keys,
        )
    return zip_path, checksum_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exe", default=str(REPO_ROOT / "dist_portable" / EXE_NAME))
    parser.add_argument("--output", default=str(REPO_ROOT / "release"))
    parser.add_argument("--version", default=None, help="Defaults to app.APP_VERSION.")
    parser.add_argument("--clean", action="store_true", help="Replace a non-empty output directory.")
    parser.add_argument("--sign-key", default="", help="Optional Ed25519 private key PEM outside repo.")
    parser.add_argument("--sign-key-env", default="", help="Optional env var with Ed25519 private key PEM.")
    parser.add_argument("--passphrase-env", default="", help="Optional env var holding signing passphrase.")
    args = parser.parse_args(argv)

    zip_path, checksum_path = build_release(
        args.exe,
        args.output,
        version=args.version,
        clean=args.clean,
        sign_key=args.sign_key or None,
        sign_key_env=args.sign_key_env or None,
        passphrase_env=args.passphrase_env or None,
    )
    print(f"ZIP:      {zip_path}")
    print(f"Checksum: {checksum_path}")
    print(checksum_path.read_text(encoding="ascii").strip())
    sig_path = Path(f"{zip_path}.sig")
    if sig_path.is_file():
        print(f"Sig:      {sig_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
