"""Contract tests for the files the in-app updater downloads from GitHub Releases.

Every portable build from v1.3.0 onwards looks for exactly these asset names
and this ZIP layout. A drift silently disables automatic updates for every
existing user, so the expected values are spelled out literally here instead
of being read back from app.py.
"""

import hashlib
import importlib.util
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

try:
    from . import _isolation  # noqa: F401  python -m unittest tests.test_release_contract
except ImportError:
    import _isolation  # noqa: F401  python -m unittest discover -s tests

import app

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_EXE_MEMBER = "VietSub Studio/VietSub Studio.exe"
EXPECTED_ZIP_MEMBERS = {
    "VietSub Studio/VietSub Studio.exe",
    "VietSub Studio/SHA256.txt",
    "VietSub Studio/LICENSE",
    "VietSub Studio/HUONG_DAN_SU_DUNG.txt",
}


def load_make_release():
    spec = importlib.util.spec_from_file_location(
        "make_release", REPO_ROOT / "tools" / "make_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_release_matches_contract(test, release_dir, version, expected_exe_bytes=None):
    zip_name, checksum_name = app.release_asset_names(version)
    zip_path = Path(release_dir) / zip_name
    checksum_path = Path(release_dir) / checksum_name
    zip_bytes = zip_path.read_bytes()
    test.assertLessEqual(len(zip_bytes), app.UPDATE_DOWNLOAD_MAX_BYTES)

    checksum_text = checksum_path.read_bytes().decode("ascii")
    test.assertEqual(
        app.parse_update_checksum(checksum_text, zip_name),
        hashlib.sha256(zip_bytes).hexdigest(),
    )

    with zipfile.ZipFile(zip_path) as archive:
        names = {item.filename for item in archive.infolist() if not item.is_dir()}
        test.assertTrue(EXPECTED_ZIP_MEMBERS.issubset(names), sorted(names))
        test.assertTrue(all(name.startswith("VietSub Studio/") for name in names), sorted(names))
        test.assertFalse(any("/_internal/" in name for name in names), sorted(names))
        member = app.find_update_executable_member(archive)
        exe_bytes = archive.read(member)
        test.assertTrue(exe_bytes.startswith(b"MZ"))
        if expected_exe_bytes is not None:
            test.assertEqual(exe_bytes, expected_exe_bytes)
        inner_checksum = archive.read("VietSub Studio/SHA256.txt").decode("ascii")
        test.assertEqual(
            app.parse_update_checksum(inner_checksum, "VietSub Studio.exe"),
            hashlib.sha256(exe_bytes).hexdigest(),
        )


class ReleaseAssetContractTests(unittest.TestCase):
    def test_asset_names_match_what_published_updaters_expect(self):
        self.assertEqual(
            app.release_asset_names("1.2.3"),
            (
                "VietSub-Studio-Portable-v1.2.3.zip",
                "VietSub-Studio-Portable-v1.2.3.zip.sha256.txt",
            ),
        )
        self.assertEqual(app.UPDATE_ZIP_EXE_MEMBER, EXPECTED_EXE_MEMBER)

    def test_a_checksum_asset_without_the_txt_suffix_is_not_accepted(self):
        base_url = "https://github.com/qvinh8726/VietSub-Studio/releases/download/v1.2.3"
        payload = {
            "assets": [
                {
                    "name": "VietSub-Studio-Portable-v1.2.3.zip",
                    "browser_download_url": f"{base_url}/VietSub-Studio-Portable-v1.2.3.zip",
                },
                {
                    "name": "VietSub-Studio-Portable-v1.2.3.zip.sha256",
                    "browser_download_url": f"{base_url}/VietSub-Studio-Portable-v1.2.3.zip.sha256",
                },
            ]
        }
        with self.assertRaises(RuntimeError):
            app.select_update_assets(payload, "1.2.3")

    def test_checksum_lines_follow_the_sha256sum_format(self):
        digest = "a" * 64
        name = "VietSub-Studio-Portable-v1.2.3.zip"
        self.assertEqual(app.parse_update_checksum(f"{digest}  {name}\n", name), digest)
        self.assertEqual(app.parse_update_checksum(f"{digest.upper()} *{name}", name), digest)
        for invalid in (digest, f"{digest}  dist/{name}", f"{digest[:-1]}  {name}"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                app.parse_update_checksum(invalid, name)

    def test_flat_zip_is_rejected_and_nested_zip_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            flat_zip = Path(temporary_dir) / "flat.zip"
            nested_zip = Path(temporary_dir) / "nested.zip"
            with zipfile.ZipFile(flat_zip, "w") as archive:
                archive.writestr("VietSub Studio.exe", b"MZflat")
            with zipfile.ZipFile(nested_zip, "w") as archive:
                archive.writestr(EXPECTED_EXE_MEMBER, b"MZnested")

            with zipfile.ZipFile(flat_zip) as archive, self.assertRaises(RuntimeError):
                app.find_update_executable_member(archive)
            with zipfile.ZipFile(nested_zip) as archive:
                member = app.find_update_executable_member(archive)
                self.assertEqual(member.filename, EXPECTED_EXE_MEMBER)


class MakeReleaseTests(unittest.TestCase):
    def test_build_release_produces_exactly_the_updater_assets(self):
        make_release = load_make_release()
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable = Path(temporary_dir) / "VietSub Studio.exe"
            executable.write_bytes(b"MZ" + bytes(range(256)) * 4)
            output_dir = Path(temporary_dir) / "release"

            zip_path, checksum_path = make_release.build_release(executable, output_dir, version="1.2.3")

            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                sorted(app.release_asset_names("1.2.3")),
            )
            self.assertEqual(Path(zip_path).parent, output_dir)
            self.assertEqual(Path(checksum_path).parent, output_dir)
            assert_release_matches_contract(self, output_dir, "1.2.3", executable.read_bytes())

    def test_build_release_rejects_a_file_that_is_not_an_executable(self):
        make_release = load_make_release()
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable = Path(temporary_dir) / "VietSub Studio.exe"
            executable.write_bytes(b"not an exe")
            with self.assertRaises(ValueError):
                make_release.build_release(executable, Path(temporary_dir) / "release", version="1.2.3")

    def test_build_release_refuses_a_non_empty_output_directory_unless_cleaning(self):
        make_release = load_make_release()
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable = Path(temporary_dir) / "VietSub Studio.exe"
            executable.write_bytes(b"MZexe")
            output_dir = Path(temporary_dir) / "release"
            output_dir.mkdir()
            (output_dir / "stale.zip").write_bytes(b"old")

            with self.assertRaises(FileExistsError):
                make_release.build_release(executable, output_dir, version="1.2.3")
            make_release.build_release(executable, output_dir, version="1.2.3", clean=True)
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                sorted(app.release_asset_names("1.2.3")),
            )


class ReleaseMetadataTests(unittest.TestCase):
    def test_app_version_matches_the_newest_changelog_release(self):
        # The updater compares APP_VERSION with the release tag, and make_release.py
        # names the assets after it, so the changelog and the code must agree.
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        releases = re.findall(
            r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})\s*$", changelog, re.MULTILINE
        )
        self.assertTrue(releases, "CHANGELOG.md has no released version heading")
        self.assertEqual(app.APP_VERSION, releases[0][0])
        versions = [app.parse_release_version(version) for version, _ in releases]
        self.assertEqual(versions, sorted(versions, reverse=True), "newest release must come first")
        self.assertEqual(len(versions), len(set(versions)), "a version is listed twice")


class PublishedReleaseDirectoryTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RELEASE_DIR"), "RELEASE_DIR is not set")
    def test_release_directory_matches_the_updater_contract(self):
        release_dir = Path(os.environ["RELEASE_DIR"])
        self.assertEqual(
            sorted(path.name for path in release_dir.iterdir()),
            sorted(app.release_asset_names(app.APP_VERSION)),
        )
        assert_release_matches_contract(self, release_dir, app.APP_VERSION)


class UpdateHelperEnvironmentTests(unittest.TestCase):
    def test_restarted_executable_gets_a_fresh_pyinstaller_environment(self):
        package = {
            "target_path": r"C:\Apps\VietSub Studio.exe",
            "staged_path": r"C:\Apps\.VietSub Studio.update-1.exe",
            "update_dir": r"C:\Temp\VietSub-Studio-update-1",
        }
        inherited = {
            "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI123",
            "_PYI_ARCHIVE_FILE": r"C:\Apps\VietSub Studio.exe",
            "_MEIPASS2": r"C:\Temp\_MEI123",
        }
        with (
            patch.dict(os.environ, inherited),
            patch.object(app.subprocess, "Popen") as popen,
        ):
            app.launch_update_helper(package)

        helper_env = popen.call_args.kwargs["env"]
        self.assertEqual(helper_env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(helper_env["VIETSUB_UPDATE_TARGET"], package["target_path"])
        for key in inherited:
            self.assertNotIn(key, {name.upper() for name in helper_env})


if __name__ == "__main__":
    unittest.main()
