"""Offline Ed25519 release signing and verification CLI for VietSub Studio.

Keep the generated private key strictly outside the repository (e.g.
``%USERPROFILE%\\.vietsub-signing\\release-ed25519.pem``). Never commit private
keys to git or store them in CI secrets.
"""

import argparse
import getpass
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import eddsa

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vietsub import updater  # noqa: E402


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_inside_repo(target_path):
    resolved = Path(target_path).resolve()
    repo = REPO_ROOT.resolve()
    try:
        resolved.relative_to(repo)
        return True
    except ValueError:
        return False


def _restrict_private_key_permissions(path):
    """Restrict private key file permissions without invoking a shell."""
    target = Path(path)
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        icacls = os.path.join(system_root, "System32", "icacls.exe")
        if not os.path.isfile(icacls):
            icacls = shutil.which("icacls") or "icacls"
        username = os.environ.get("USERNAME") or getpass.getuser()
        if not username:
            raise RuntimeError("Không xác định được tài khoản Windows để giới hạn quyền private key.")
        domain = (os.environ.get("USERDOMAIN") or "").strip()
        principals = [f"{domain}\\{username}", username] if domain else [username]
        last_proc = None
        for principal in principals:
            proc = subprocess.run(
                [icacls, str(target), "/inheritance:r", "/grant:r", f"{principal}:(R,W)"],
                shell=False,
                check=False,
                capture_output=True,
                text=True,
            )
            last_proc = proc
            returncode = getattr(proc, "returncode", 0)
            if not isinstance(returncode, int) or returncode == 0:
                return
        detail = ((getattr(last_proc, "stderr", "") or getattr(last_proc, "stdout", "")) or "").strip()
        raise RuntimeError(
            f"Không thể giới hạn quyền truy cập private key bằng icacls "
            f"(exit {getattr(last_proc, 'returncode', 'unknown')}): {detail or 'lỗi không xác định'}"
        )
    else:
        os.chmod(target, 0o600)


def _resolve_passphrase(passphrase=None, passphrase_env=None, *, confirm=False):
    if passphrase is not None:
        secret = passphrase
    elif passphrase_env:
        secret = os.environ.get(passphrase_env, "")
        if not secret:
            raise ValueError(f"Biến môi trường passphrase '{passphrase_env}' đang trống.")
    else:
        secret = getpass.getpass("Nhập passphrase cho private key Ed25519 (tối thiểu 12 ký tự): ")
        if confirm:
            again = getpass.getpass("Nhập lại passphrase: ")
            if secret != again:
                raise ValueError("Passphrase nhập lại không khớp.")
    if not isinstance(secret, str) or len(secret) < 12:
        raise ValueError("Passphrase cho private key phải dài tối thiểu 12 ký tự.")
    return secret


def infer_version_from_zip(zip_path):
    name = Path(zip_path).name
    match = re.fullmatch(
        r"VietSub-Studio-Portable-v([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\.zip",
        name,
    )
    if not match:
        raise ValueError(
            f"Không suy ra được version từ tên file '{name}'. Hãy truyền thêm --version."
        )
    return match.group(1)


def resolve_zip_path(zip_path=None, release_dir=None, version=None):
    if zip_path and release_dir:
        raise ValueError("Chỉ chọn một trong --zip hoặc --release-dir.")
    if zip_path:
        path = Path(zip_path)
        if not path.is_file():
            raise FileNotFoundError(f"Không tìm thấy ZIP: {path}")
        resolved_version = str(version).strip().lstrip("v") if version else infer_version_from_zip(path)
        updater.parse_release_version(resolved_version)
        return path, resolved_version
    if release_dir:
        directory = Path(release_dir)
        if not directory.is_dir():
            raise FileNotFoundError(f"Không tìm thấy thư mục release: {directory}")
        if version:
            resolved_version = str(version).strip().lstrip("v")
            updater.parse_release_version(resolved_version)
            zip_name, _ = updater.release_asset_names(resolved_version)
            candidate = directory / zip_name
            if not candidate.is_file():
                raise FileNotFoundError(f"Không tìm thấy {zip_name} trong {directory}")
            return candidate, resolved_version
        zips = sorted(directory.glob("VietSub-Studio-Portable-v*.zip"))
        if len(zips) != 1:
            raise ValueError(
                f"Cần đúng 1 file ZIP release trong {directory} (tìm thấy {len(zips)})."
            )
        return zips[0], infer_version_from_zip(zips[0])
    raise ValueError("Cần chỉ định --zip hoặc --release-dir.")


def generate_keypair(
    out_path,
    *,
    pub_out_path=None,
    passphrase=None,
    passphrase_env=None,
    force=False,
    allow_repo_for_test=False,
):
    out_path = Path(out_path)
    if not allow_repo_for_test and _is_inside_repo(out_path):
        raise ValueError(
            "Từ chối lưu private key bên trong thư mục repository. Hãy chọn đường dẫn ngoài repo."
        )
    if out_path.exists() and not force:
        raise FileExistsError(f"File private key đã tồn tại: {out_path} (dùng --force để ghi đè).")
    secret = _resolve_passphrase(passphrase, passphrase_env, confirm=True)
    key = ECC.generate(curve="Ed25519")
    raw_pub = key.public_key().export_key(format="raw")
    key_id = updater.public_key_id(raw_pub)
    pub_hex = raw_pub.hex()

    encrypted_pem = key.export_key(
        format="PEM",
        use_pkcs8=True,
        passphrase=secret,
        protection="scryptAndAES256-CBC",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(encrypted_pem + "\n", encoding="ascii")
    _restrict_private_key_permissions(out_path)

    if pub_out_path:
        pub_target = Path(pub_out_path)
        pub_target.parent.mkdir(parents=True, exist_ok=True)
        pub_target.write_text(
            key.public_key().export_key(format="PEM") + "\n", encoding="ascii"
        )

    return {
        "private_key_path": str(out_path),
        "key_id": key_id,
        "public_key_hex": pub_hex,
    }


def _load_private_key(*, key_path=None, key_env=None, passphrase=None, passphrase_env=None):
    pem_text = ""
    if key_path:
        pem_text = Path(key_path).read_text(encoding="ascii")
    elif key_env:
        raw_env = os.environ.get(key_env, "")
        if not raw_env:
            raise ValueError(f"Biến môi trường key '{key_env}' đang trống.")
        pem_text = raw_env if "BEGIN " in raw_env else Path(raw_env).read_text(encoding="ascii")
    else:
        raise ValueError("Cần chỉ định --key hoặc --key-env.")

    secret = _resolve_passphrase(passphrase, passphrase_env, confirm=False)
    key = ECC.import_key(pem_text, passphrase=secret)
    if not key.has_private() or getattr(key, "curve", None) != "Ed25519":
        raise ValueError("File khóa không phải Ed25519 private key hợp lệ.")
    return key


def sign_release_zip(
    zip_path,
    *,
    version=None,
    key_path=None,
    key_env=None,
    passphrase=None,
    passphrase_env=None,
    sig_out_path=None,
    expected_public_keys=None,
):
    zip_path = Path(zip_path)
    resolved_version = (
        str(version).strip().lstrip("v") if version else infer_version_from_zip(zip_path)
    )
    updater.parse_release_version(resolved_version)
    key = _load_private_key(
        key_path=key_path,
        key_env=key_env,
        passphrase=passphrase,
        passphrase_env=passphrase_env,
    )
    raw_pub = key.public_key().export_key(format="raw")
    key_id = updater.public_key_id(raw_pub)
    pub_hex = raw_pub.hex()

    zip_sha256 = sha256_file(zip_path)
    message = updater.canonical_release_message(resolved_version, zip_path.name, zip_sha256)
    signature_bytes = eddsa.new(key, "rfc8032").sign(message)
    signature_text = updater.format_signature_text(key_id, signature_bytes)

    trusted_map = expected_public_keys
    if trusted_map is None:
        trusted_map = (
            updater.RELEASE_PUBLIC_KEYS
            if updater.has_valid_public_keys()
            else {key_id: pub_hex}
        )
    updater.verify_release_signature(
        resolved_version,
        zip_path.name,
        zip_sha256,
        signature_text,
        public_keys=trusted_map,
    )

    sig_path = Path(sig_out_path) if sig_out_path else Path(f"{zip_path}.sig")
    sig_path.write_bytes(signature_text.encode("ascii"))
    return sig_path, key_id, pub_hex


def verify_release_files(
    zip_path,
    *,
    version=None,
    sig_path=None,
    public_key_hex=None,
):
    zip_path = Path(zip_path)
    resolved_version = (
        str(version).strip().lstrip("v") if version else infer_version_from_zip(zip_path)
    )
    updater.parse_release_version(resolved_version)
    sig_file = Path(sig_path) if sig_path else Path(f"{zip_path}.sig")
    if not sig_file.is_file():
        raise FileNotFoundError(f"Không tìm thấy file chữ ký: {sig_file}")

    checksum_file = Path(f"{zip_path}.sha256.txt")
    zip_sha256 = sha256_file(zip_path)
    if checksum_file.is_file():
        expected_sha = updater.parse_update_checksum(
            checksum_file.read_text(encoding="ascii"), zip_path.name
        )
        if expected_sha != zip_sha256:
            raise RuntimeError("Checksum .zip.sha256.txt không khớp với file ZIP.")

    public_keys = None
    if public_key_hex:
        raw_pub = bytes.fromhex(public_key_hex.strip())
        public_keys = {updater.public_key_id(raw_pub): raw_pub.hex()}

    sig_text = sig_file.read_text(encoding="ascii")
    key_id = updater.verify_release_signature(
        resolved_version,
        zip_path.name,
        zip_sha256,
        sig_text,
        public_keys=public_keys,
    )
    return sig_file, key_id, zip_sha256


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline Ed25519 release signer for VietSub Studio.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_keygen = subparsers.add_parser("keygen", help="Generate encrypted Ed25519 signing key outside repo.")
    p_keygen.add_argument("--out", required=True, help="Output path for encrypted private key PEM (outside repo).")
    p_keygen.add_argument("--pub-out", default="", help="Optional path to save public PEM copy.")
    p_keygen.add_argument("--passphrase-env", default="", help="Read passphrase from environment variable.")
    p_keygen.add_argument("--force", action="store_true", help="Overwrite existing key file.")

    p_sign = subparsers.add_parser("sign", help="Sign a release ZIP and produce .zip.sig.")
    p_sign.add_argument("--zip", dest="zip_path", default="", help="Path to VietSub-Studio-Portable-vX.Y.Z.zip.")
    p_sign.add_argument("--release-dir", default="", help="Folder containing the release ZIP.")
    p_sign.add_argument("--version", default="", help="Override release version.")
    p_sign.add_argument("--key", dest="key_path", default="", help="Path to encrypted private key PEM.")
    p_sign.add_argument("--key-env", default="", help="Env var holding key PEM path or PEM text.")
    p_sign.add_argument("--passphrase-env", default="", help="Read key passphrase from env var.")
    p_sign.add_argument("--public-key-hex", default="", help="Optional expected 64-hex Ed25519 public key.")

    p_verify = subparsers.add_parser("verify", help="Verify .zip, .zip.sha256.txt, and .zip.sig.")
    p_verify.add_argument("--zip", dest="zip_path", default="", help="Path to VietSub-Studio-Portable-vX.Y.Z.zip.")
    p_verify.add_argument("--release-dir", default="", help="Folder containing release files.")
    p_verify.add_argument("--sig", dest="sig_path", default="", help="Optional path to .zip.sig.")
    p_verify.add_argument("--version", default="", help="Override release version.")
    p_verify.add_argument("--public-key-hex", default="", help="Optional 64-hex Ed25519 public key (defaults to embedded).")

    args = parser.parse_args(argv)
    if args.command == "keygen":
        info = generate_keypair(
            args.out,
            pub_out_path=args.pub_out or None,
            passphrase_env=args.passphrase_env or None,
            force=args.force,
        )
        print(f"Private key saved (outside repo): {info['private_key_path']}")
        print(f"KEY_ID={info['key_id']}")
        print(f"PUBLIC_KEY_HEX={info['public_key_hex']}")
        print(
            "\nPaste into vietsub/updater.py RELEASE_PUBLIC_KEYS:\n"
            f'    "{info["key_id"]}": "{info["public_key_hex"]}",'
        )
        return 0

    if args.command == "sign":
        zip_file, version = resolve_zip_path(
            zip_path=args.zip_path or None,
            release_dir=args.release_dir or None,
            version=args.version or None,
        )
        expected = None
        if args.public_key_hex:
            raw_pub = bytes.fromhex(args.public_key_hex.strip())
            expected = {updater.public_key_id(raw_pub): raw_pub.hex()}
        sig_file, key_id, _ = sign_release_zip(
            zip_file,
            version=version,
            key_path=args.key_path or None,
            key_env=args.key_env or None,
            passphrase_env=args.passphrase_env or None,
            expected_public_keys=expected,
        )
        print(f"Signed: {sig_file}")
        print(f"Key ID: {key_id}")
        return 0

    if args.command == "verify":
        zip_file, version = resolve_zip_path(
            zip_path=args.zip_path or None,
            release_dir=args.release_dir or None,
            version=args.version or None,
        )
        sig_file, key_id, zip_sha256 = verify_release_files(
            zip_file,
            version=version,
            sig_path=args.sig_path or None,
            public_key_hex=args.public_key_hex or None,
        )
        print(f"Verified OK: {zip_file.name}")
        print(f"SHA256:      {zip_sha256}")
        print(f"Signature:   {sig_file.name} (key_id={key_id})")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
