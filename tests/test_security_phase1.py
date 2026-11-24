"""Phase 1 security hardening tests: Ed25519 updater, offline signer, redaction, and TESTING=False boundaries."""

import base64
import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import eddsa

try:
    from . import _isolation  # noqa: F401
except ImportError:
    import _isolation  # noqa: F401

import app
from tools import sign_release
from vietsub import redaction, updater
from vietsub.sources import cookies as cookie_utils


def _build_signed_update_fixture(root: Path, version="9.9.9", exe_bytes=b"MZnew-signed-exe"):
    key = ECC.generate(curve="Ed25519")
    raw_pub = key.public_key().export_key(format="raw")
    key_id = updater.public_key_id(raw_pub)
    trusted = {key_id: raw_pub.hex()}

    release_zip = root / "release.zip"
    with zipfile.ZipFile(release_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("VietSub Studio/VietSub Studio.exe", exe_bytes)
        archive.writestr(
            "VietSub Studio/SHA256.txt",
            f"{hashlib.sha256(exe_bytes).hexdigest()}  VietSub Studio.exe\n",
        )
    zip_bytes = release_zip.read_bytes()
    zip_hash = hashlib.sha256(zip_bytes).hexdigest()
    zip_name, checksum_name, sig_name = updater.signed_release_asset_names(version)
    checksum_bytes = f"{zip_hash}  {zip_name}\n".encode("ascii")
    msg = updater.canonical_release_message(version, zip_name, zip_hash)
    sig_text = updater.format_signature_text(key_id, eddsa.new(key, "rfc8032").sign(msg))
    base_url = f"https://github.com/qvinh8726/VietSub-Studio/releases/download/v{version}"
    payload = {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/qvinh8726/VietSub-Studio/releases/tag/v{version}",
        "assets": [
            {
                "name": zip_name,
                "browser_download_url": f"{base_url}/{zip_name}",
                "digest": f"sha256:{zip_hash}",
                "size": len(zip_bytes),
            },
            {
                "name": checksum_name,
                "browser_download_url": f"{base_url}/{checksum_name}",
                "size": len(checksum_bytes),
            },
            {
                "name": sig_name,
                "browser_download_url": f"{base_url}/{sig_name}",
                "size": len(sig_text.encode("ascii")),
            },
        ],
    }
    return key, key_id, trusted, zip_bytes, checksum_bytes, sig_text.encode("ascii"), payload


class Ed25519UpdaterSecurityTests(unittest.TestCase):
    def setUp(self):
        with app.update_cache_lock:
            app.update_cache.update({"checked_at": 0.0, "status": None})

    def test_rfc8032_vector_1_verifies_and_tamper_fails(self):
        pub_raw = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig_raw = bytes.fromhex(
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        )
        key = updater._import_ed25519_public_key(pub_raw)
        eddsa.new(key, "rfc8032").verify(b"", sig_raw)
        with self.assertRaises(ValueError):
            eddsa.new(key, "rfc8032").verify(b"\x00", sig_raw)

    def test_empty_embedded_public_keys_keeps_api_update_200_with_auto_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, _, _, _, payload = _build_signed_update_fixture(Path(tmp))
        with (
            patch.dict(updater.RELEASE_PUBLIC_KEYS, {}, clear=True),
            patch.object(app, "is_packaged_app", return_value=True),
            patch.object(app, "supports_in_place_update", return_value=True),
            patch.object(app, "fetch_latest_release_payload", return_value=payload),
            patch.dict(app.app.config, TESTING=False),
        ):
            client = app.app.test_client()
            res = client.get("/api/update", headers={app.API_TOKEN_HEADER: app.API_TOKEN})
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["update_available"])
        self.assertFalse(body["automatic_update_supported"])

    def test_fail_closed_rejects_empty_placeholder_corrupt_key_and_env_bypass(self):
        for bad_keys in (
            {},
            {"0" * 16: "0" * 64},
            {updater.public_key_id(b"\xaa" * 32): "aa" * 32},
            {"not-hex": "ab" * 32},
            {"a" * 16: "short"},
            {updater.public_key_id(b"\x01\x02" * 16): ("0304" * 16)},
        ):
            with self.subTest(bad_keys=bad_keys), self.assertRaises(RuntimeError):
                updater.load_trusted_public_keys(bad_keys)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "VietSub Studio.exe"
            exe.write_bytes(b"MZold")
            _, key_id, trusted, zip_bytes, chk_bytes, sig_bytes, payload = _build_signed_update_fixture(root)

            def fake_dl(url, dest, _limit):
                data = chk_bytes if url.endswith(".sha256.txt") else sig_bytes if url.endswith(".sig") else zip_bytes
                Path(dest).write_bytes(data)
                return hashlib.sha256(data).hexdigest(), len(data)

            # Even if env tries to disable/override public keys in frozen EXE, fail-closed rejects.
            with (
                patch.dict(os.environ, {
                    "VIETSUB_SKIP_UPDATE_SIGNATURE": "1",
                    "VIETSUB_UPDATE_PUBLIC_KEY": trusted[key_id],
                }),
                patch.dict(updater.RELEASE_PUBLIC_KEYS, {}, clear=True),
                patch.object(app, "is_packaged_app", return_value=True),
                patch.object(app.sys, "executable", str(exe)),
                patch.object(app, "download_release_asset", side_effect=fake_dl),
            ):
                with self.assertRaisesRegex(RuntimeError, "Khóa công khai"):
                    app.prepare_update_executable(payload)

    def test_tampered_sig_tampered_zip_missing_sig_and_version_replay_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "VietSub Studio.exe"
            exe.write_bytes(b"MZold")
            key, key_id, trusted, zip_bytes, chk_bytes, sig_bytes, payload = _build_signed_update_fixture(root)
            zip_name, chk_name, sig_name = updater.signed_release_asset_names("9.9.9")

            # 1. Tampered 1 byte in .zip.sig
            scheme, kid, b64 = sig_bytes.decode("ascii").strip().split()
            raw_sig = bytearray(base64.b64decode(b64))
            raw_sig[0] ^= 0xFF
            bad_sig_bytes = f"{scheme} {kid} {base64.b64encode(bytes(raw_sig)).decode('ascii')}\n".encode("ascii")

            def run_case(c_bytes, s_bytes, z_bytes, case_payload=payload):
                def dl(url, dest, _limit):
                    data = c_bytes if url.endswith(".sha256.txt") else s_bytes if url.endswith(".sig") else z_bytes
                    Path(dest).write_bytes(data)
                    return hashlib.sha256(data).hexdigest(), len(data)

                with (
                    patch.dict(updater.RELEASE_PUBLIC_KEYS, trusted, clear=True),
                    patch.object(app, "is_packaged_app", return_value=True),
                    patch.object(app.sys, "executable", str(exe)),
                    patch.object(app, "download_release_asset", side_effect=dl),
                ):
                    app.prepare_update_executable(case_payload)

            with self.assertRaisesRegex(RuntimeError, "Ed25519"):
                run_case(chk_bytes, bad_sig_bytes, zip_bytes)

            # 2. Attacker modifies 1 byte of ZIP AND recomputes ZIP SHA-256 + API digest to match!
            bad_zip = bytearray(zip_bytes)
            bad_zip[10] ^= 0x01
            bad_zip_bytes = bytes(bad_zip)
            bad_hash = hashlib.sha256(bad_zip_bytes).hexdigest()
            bad_chk = f"{bad_hash}  {zip_name}\n".encode("ascii")
            forged_payload = {
                **payload,
                "assets": [
                    {**payload["assets"][0], "digest": f"sha256:{bad_hash}", "size": len(bad_zip_bytes)},
                    payload["assets"][1],
                    payload["assets"][2],
                ],
            }
            with self.assertRaisesRegex(RuntimeError, "Ed25519"):
                run_case(bad_chk, sig_bytes, bad_zip_bytes, forged_payload)

            # 3. Missing .zip.sig
            no_sig_payload = {**payload, "assets": payload["assets"][:2]}
            with self.assertRaisesRegex(RuntimeError, "chữ ký Ed25519"):
                run_case(chk_bytes, sig_bytes, zip_bytes, no_sig_payload)

            # 4. Version replay: valid signature for 9.9.9 replayed on 9.9.10 release
            replay_zip, replay_chk, replay_sig = updater.signed_release_asset_names("9.9.10")
            replay_base = "https://github.com/qvinh8726/VietSub-Studio/releases/download/v9.9.10"
            z_hash = hashlib.sha256(zip_bytes).hexdigest()
            replay_payload = {
                "tag_name": "v9.9.10",
                "assets": [
                    {"name": replay_zip, "browser_download_url": f"{replay_base}/{replay_zip}", "digest": f"sha256:{z_hash}", "size": len(zip_bytes)},
                    {"name": replay_chk, "browser_download_url": f"{replay_base}/{replay_chk}"},
                    {"name": replay_sig, "browser_download_url": f"{replay_base}/{replay_sig}"},
                ],
            }
            with self.assertRaisesRegex(RuntimeError, "Ed25519"):
                run_case(f"{z_hash}  {replay_zip}\n".encode("ascii"), sig_bytes, zip_bytes, replay_payload)

            # No leftover temp dirs inside repo or staged file next to exe
            self.assertFalse(list(root.glob(".VietSub Studio.update-*.exe")))


class OfflineSignReleaseToolTests(unittest.TestCase):
    def test_keygen_refuses_paths_inside_repository(self):
        inside_repo = Path(sign_release.REPO_ROOT) / "temp-key.pem"
        with self.assertRaisesRegex(ValueError, "ngoài repo"):
            sign_release.generate_keypair(inside_repo, passphrase="super-secret-passphrase")

    def test_keygen_sign_verify_and_icacls_on_unicode_spaced_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "Khóa ký thử nghiệm"
            key_path = base / "release key.pem"
            zip_path = base / "VietSub-Studio-Portable-v2.1.0.zip"
            base.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("VietSub Studio/VietSub Studio.exe", b"MZtest-binary")
            sha_hex = sign_release.sha256_file(zip_path)
            Path(f"{zip_path}.sha256.txt").write_bytes(
                f"{sha_hex}  {zip_path.name}\n".encode("ascii")
            )

            with patch.object(sign_release.subprocess, "run") as mock_run:
                info = sign_release.generate_keypair(key_path, passphrase="my strong pass phrase 2026")
            if os.name == "nt":
                self.assertEqual(mock_run.call_count, 1)
                self.assertIsInstance(mock_run.call_args.args[0], list)
                self.assertFalse(mock_run.call_args.kwargs.get("shell", False))
                self.assertEqual(mock_run.call_args.args[0][1], str(key_path))

            pem_text = key_path.read_text(encoding="ascii")
            self.assertIn("BEGIN ENCRYPTED PRIVATE KEY", pem_text)

            sig_path, signed_kid, pub_hex = sign_release.sign_release_zip(
                zip_path, key_path=key_path, passphrase="my strong pass phrase 2026"
            )
            self.assertEqual(signed_kid, info["key_id"])
            self.assertTrue(sig_path.is_file())

            _, verified_kid, verified_sha = sign_release.verify_release_files(
                zip_path, public_key_hex=pub_hex
            )
            self.assertEqual(verified_kid, info["key_id"])
            self.assertEqual(verified_sha, sha_hex)


class RedactionAndTestingFalseLeakTests(unittest.TestCase):
    def setUp(self):
        redaction.clear_secrets()
        redaction.register_secret(app.API_TOKEN)

    def tearDown(self):
        redaction.clear_secrets()
        redaction.register_secret(app.API_TOKEN)

    def test_redaction_scrubs_secrets_and_paths_while_preserving_diagnostics(self):
        secret_key = "sk-live-secret9876543210"
        bearer = "Bearer eyJhbGciOiJIUzI1NiJ9.payloadvalue.sigvalue"
        dpapi_blob = "dpapi:" + base64.b64encode(b"0123456789abcdef").decode("ascii")
        line = (
            f"HTTP 429 from API: key={secret_key} Authorization: {bearer} "
            f"X-VietSub-Token: {app.API_TOKEN} Cookie: sessionid=abc987def654; "
            f"cfg={dpapi_blob} file C:\\Users\\NguyenVanA\\AppData\\Local\\Temp\\_MEI9999\\out.log "
            f"FFmpeg exit 1 (-c:v libx264) 00:01:02,500"
        )
        scrubbed_log = redaction.redact_text(line, redact_paths=False)
        self.assertNotIn(secret_key, scrubbed_log)
        self.assertNotIn("payloadvalue", scrubbed_log)
        self.assertNotIn(app.API_TOKEN, scrubbed_log)
        self.assertNotIn("abc987def654", scrubbed_log)
        self.assertNotIn("dpapi:", scrubbed_log)
        self.assertIn("HTTP 429", scrubbed_log)
        self.assertIn("FFmpeg exit 1 (-c:v libx264) 00:01:02,500", scrubbed_log)

        scrubbed_err = redaction.redact_error(line)
        self.assertNotIn("NguyenVanA", scrubbed_err)
        self.assertNotIn("_MEI9999", scrubbed_err)
        self.assertIn("<APP_RUNTIME>", scrubbed_err)
        self.assertIn("out.log", scrubbed_err)

    def test_logs_and_progress_tab_scrub_dpapi_secrets_and_tokens(self):
        dpapi_secret = "custom-dpapi-secret-value-xyz"
        protected = app.protect_text_secret(dpapi_secret)
        self.assertEqual(app.unprotect_config_secret(protected), dpapi_secret)

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "vietsub-studio.log"
            app.reset_progress("redact-log-job")
            with patch.object(app, "LOG_PATH", str(log_file)):
                app.log_to_app(
                    f"Lỗi dịch HTTP 500 token={app.API_TOKEN} key={dpapi_secret} Bearer secretBearerToken123 "
                    r"at C:\Users\Alice\Secret\raw.srt",
                    "error",
                )
            disk_text = log_file.read_text(encoding="utf-8")
            self.assertNotIn(app.API_TOKEN, disk_text)
            self.assertNotIn(dpapi_secret, disk_text)
            self.assertNotIn("secretBearerToken123", disk_text)
            self.assertNotIn("Alice", disk_text)
            self.assertIn("HTTP 500", disk_text)
            self.assertIn("raw.srt", disk_text)

            with patch.dict(app.app.config, TESTING=False):
                client = app.app.test_client()
                prog = client.get("/api/progress", headers={app.API_TOKEN_HEADER: app.API_TOKEN})
                self.assertEqual(prog.status_code, 200)
                joined = "\n".join(prog.get_json()["logs"])
                self.assertNotIn(dpapi_secret, joined)
                self.assertNotIn(app.API_TOKEN, joined)
                self.assertNotIn("Alice", joined)

    def test_cookie_parser_does_not_echo_token_fragment(self):
        with self.assertRaises(ValueError) as ctx:
            cookie_utils.parse_cookie_header("sessionid=supersecret999; invalid token fragment")
        self.assertNotIn("invalid token fragment", str(ctx.exception))
        self.assertNotIn("supersecret999", str(ctx.exception))

    def test_testing_false_api_error_responses_redact_paths_and_secrets_across_subsystems(self):
        headers = {app.API_TOKEN_HEADER: app.API_TOKEN}
        leak_msg = r"sk-proj-abc123def456ghi789 at C:\Users\PrivateUser\Desktop\secret.mp4"
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(app.app.config, TESTING=False),
                patch.object(app, "CONFIG_DIR", tmp),
                patch.object(app, "CONFIG_PATH", str(Path(tmp) / "cfg.json")),
                patch.object(app, "workflow_jobs", []),
            ):
                client = app.app.test_client()

                # 1. app.py /api/previews error redaction
                with patch.object(app, "create_video_preview", side_effect=OSError(leak_msg)):
                    r1 = client.post("/api/previews", json={"url": "https://youtu.be/x"}, headers=headers)
                self.assertEqual(r1.status_code, 500)
                self.assertNotIn("PrivateUser", r1.get_json()["error"])
                self.assertNotIn("sk-proj-abc123", r1.get_json()["error"])
                self.assertIn("secret.mp4", r1.get_json()["error"])

                # 2. Editor API errorhandler + EditorJobs task["error"] redaction
                from vietsub.editor.jobs import EditorJobs
                with patch.object(app.editor_api, "_project", side_effect=RuntimeError(leak_msg)):
                    r2 = client.get("/api/editor/projects/any-job", headers=headers)
                self.assertEqual(r2.status_code, 409)
                self.assertNotIn("PrivateUser", r2.get_json()["error"])
                self.assertNotIn("sk-proj-abc123", r2.get_json()["error"])

                local_jobs = EditorJobs()
                task = local_jobs.submit(
                    "",
                    "tts_preview",
                    lambda _id, _c, _u: (_ for _ in ()).throw(RuntimeError(leak_msg)),
                )
                for _ in range(50):
                    snap = local_jobs.snapshot(task["id"])
                    if snap["status"] == "error":
                        break
                    import time
                    time.sleep(0.02)
                self.assertEqual(snap["status"], "error")
                self.assertNotIn("PrivateUser", snap["error"])
                self.assertNotIn("sk-proj-abc123", snap["error"])

                # 3. Uploader API errorhandler redaction
                r3 = client.get("/api/uploader/projects/missing", headers=headers)
                self.assertEqual(r3.status_code, 404)
                with patch.object(app.uploader_api, "_project", side_effect=RuntimeError(leak_msg)):
                    r4 = client.get("/api/uploader/projects/any-job", headers=headers)
                self.assertEqual(r4.status_code, 409)
                self.assertNotIn("PrivateUser", r4.get_json()["error"])
                self.assertNotIn("sk-proj-abc123", r4.get_json()["error"])

                # 4. /api/open-folder remains functional for authorized result paths
                result_dir = Path(tmp) / "out-proj"
                result_dir.mkdir(exist_ok=True)
                with (
                    patch.object(app, "workflow_jobs", [{"id": "ok-1", "result": {"project_dir": str(result_dir)}}]),
                    patch.object(app.subprocess, "Popen") as popen,
                ):
                    r5 = client.post("/api/open-folder", json={"path": str(result_dir)}, headers=headers)
                self.assertEqual(r5.status_code, 200)
                popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
