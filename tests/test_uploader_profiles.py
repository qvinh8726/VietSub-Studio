"""Private account registry, path containment and OS-backed profile lease tests."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from vietsub.uploader.profiles import (
    PLATFORMS, ProfileBusy, ProfileNotFound, ProfileRegistryError,
    ProfileStore, UnsafeProfilePath,
)


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "uploader"
        self.store = ProfileStore(self.root)

    def create(self, platform="tiktok", name="Account"):
        return self.store.create(platform, name)

    def test_all_six_platforms_have_independent_uuid_profile_config_and_state(self):
        self.assertEqual(set(PLATFORMS), {"tiktok", "youtube", "facebook", "douyin", "bilibili", "xiaohongshu"})
        accounts = [self.create(key) for key in PLATFORMS] + [self.create("tiktok", "Second account")]
        directories = set()
        states = set()
        configs = set()
        for account in accounts:
            identifier = account["id"]
            self.assertEqual(str(uuid.UUID(identifier)), identifier)
            self.assertEqual(account["session_status"], "unknown")
            self.assertEqual(self.store.get(identifier), account)
            directory = Path(self.store.profile_dir(identifier))
            state = Path(self.store.state_path(identifier))
            config = Path(self.store.config_path(identifier))
            self.assertTrue(directory.is_dir())
            self.assertEqual(directory.parent, state.parent)
            self.assertEqual(directory.parent.name, account["platform"])
            self.assertIn(identifier, directory.parts)
            self.assertFalse(state.exists())
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {
                "schema_version": 1, "id": identifier, "platform": account["platform"],
            })
            directories.add(str(directory))
            states.add(str(state))
            configs.add(str(config))
        self.assertEqual(len(directories), len(accounts))
        self.assertEqual(len(states), len(accounts))
        self.assertEqual(len(configs), len(accounts))
        self.assertEqual(self.store.list(), accounts)
        self.assertEqual(ProfileStore(self.root).list(), accounts)

    def test_metadata_never_exports_storage_values_paths_or_arbitrary_details(self):
        account = self.create()
        identifier = account["id"]
        snapshot = {"cookies": [{"name": "auth", "value": "secret-cookie-value"}],
                    "origins": [{"origin": "https://example.test", "localStorage": [{"name": "token", "value": "secret-token"}]}]}
        self.store.save_storage_state(identifier, snapshot)
        result = self.store.update_session(identifier, "alive", "Cookie: secret-cookie-value https://host/?token=secret-token")
        self.assertEqual(result["session_detail"], "session_alive")
        public = json.dumps([result, self.store.list(), self.store.get(identifier)])
        for value in ("secret-cookie-value", "secret-token", "storage_state", "localStorage", str(self.root)):
            self.assertNotIn(value, public)
            self.assertNotIn(value, self.store.registry_path.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(Path(self.store.state_path(identifier)).read_text(encoding="utf-8")), snapshot)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(Path(self.store.state_path(identifier)).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(Path(self.store.profile_dir(identifier)).stat().st_mode), 0o700)
        result["name"] = "mutated"
        self.assertEqual(self.store.get(identifier)["name"], "Account")

    def test_malformed_or_newer_registry_is_never_overwritten(self):
        self.create()
        for raw in (b"{truncated", b'{"schema_version":2,"accounts":[]}', b'{"schema_version":true,"accounts":[]}',
                    b'{"schema_version":2,"schema_version":1,"accounts":[]}',
                    b'{"schema_version":1,"accounts":[{"id":"../unsafe"}]}'):
            with self.subTest(raw=raw):
                self.store.registry_path.write_bytes(raw)
                with self.assertRaises(ProfileRegistryError):
                    ProfileStore(self.root)
                with self.assertRaises(ProfileRegistryError):
                    self.create()
                self.assertEqual(self.store.registry_path.read_bytes(), raw)

    def test_atomic_registry_replace_failure_preserves_previous_account_list(self):
        account = self.create()
        original = self.store.registry_path.read_bytes()
        original_replace = os.replace
        def fail_registry(source, target):
            if Path(target) == self.store.registry_path:
                raise OSError("simulated interrupted registry write")
            return original_replace(source, target)
        with patch("vietsub.uploader.profiles.os.replace", side_effect=fail_registry):
            with self.assertRaises(OSError):
                self.store.rename(account["id"], "New")
        self.assertEqual(self.store.registry_path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])
        self.assertEqual(self.store.get(account["id"])["name"], "Account")

    def test_atomic_snapshot_failure_preserves_prior_private_snapshot(self):
        identifier = self.create()["id"]
        old = {"cookies": [], "origins": []}
        self.store.save_storage_state(identifier, old)
        path = Path(self.store.state_path(identifier))
        with patch("vietsub.uploader.profiles.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.store.save_storage_state(identifier, {"cookies": [{"value": "new"}], "origins": []})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), old)
        self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_invalid_ids_names_platforms_and_session_statuses_fail_closed(self):
        identifier = self.create()["id"]
        for invalid in ("../outside", identifier.upper(), uuid.UUID(identifier).hex, "{" + identifier + "}", identifier + "/..", None):
            with self.subTest(identifier=invalid):
                with self.assertRaises(ValueError):
                    self.store.profile_dir(invalid)
                with self.assertRaises(ValueError):
                    self.store.state_path(invalid)
        with self.assertRaises(ProfileNotFound):
            self.store.get(str(uuid.uuid4()))
        with self.assertRaises(ValueError):
            self.create("../youtube")
        for name in ("", " ", "unsafe\nname", "\x00", "a" * 121):
            with self.assertRaises(ValueError):
                self.create(name=name)
        with self.assertRaises(ValueError):
            self.store.update_session(identifier, "authenticated")

    def test_same_profile_nonblocking_across_store_instances_and_threads(self):
        identifier = self.create()["id"]
        other = ProfileStore(self.root)
        failures = []
        with self.store.lease(identifier):
            with self.assertRaises(ProfileBusy):
                with other.lease(identifier):
                    self.fail("reentrant lease")
            def enter():
                try:
                    with other.lease(identifier):
                        failures.append("entered")
                except ProfileBusy:
                    failures.append("busy")
            worker = threading.Thread(target=enter)
            worker.start()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(failures, ["busy"])
        with other.lease(identifier):
            pass
        self.assertTrue((self.root / ".locks" / (identifier + ".lock")).exists())

    def test_cross_process_lease_is_nonblocking_and_ownership_is_not_lockfile_existence(self):
        identifier = self.create()["id"]
        script = """
import sys
from vietsub.uploader.profiles import ProfileStore, ProfileBusy
store = ProfileStore(sys.argv[1])
try:
    with store.lease(sys.argv[2]):
        print('owned')
except ProfileBusy:
    print('busy')
"""
        command = [sys.executable, "-c", script, str(self.root), identifier]
        with self.store.lease(identifier):
            result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
            self.assertEqual(result.stdout.strip(), "busy")
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(result.stdout.strip(), "owned")

    def test_os_releases_profile_lock_after_owner_process_exits(self):
        identifier = self.create()["id"]
        script = """
import os, sys
from vietsub.uploader.profiles import ProfileStore
lease = ProfileStore(sys.argv[1]).lease(sys.argv[2])
lease.__enter__()
os._exit(0)
"""
        subprocess.run([sys.executable, "-c", script, str(self.root), identifier],
                       capture_output=True, text=True, timeout=15, check=True)
        with self.store.lease(identifier):
            pass

    def test_other_profiles_can_be_used_while_one_is_busy_and_failure_releases_lease(self):
        first = self.create()["id"]
        second = self.create()["id"]
        with self.store.lease(first):
            with self.store.lease(second):
                self.store.update_session(first, "alive")
        with self.assertRaises(RuntimeError):
            with self.store.lease(first):
                raise RuntimeError("worker failed")
        with self.store.lease(first):
            pass

    def test_delete_is_record_only_and_rename_delete_refuse_busy_account(self):
        identifier = self.create()["id"]
        directory = Path(self.store.profile_dir(identifier))
        marker = directory / "private-cookie-database"
        marker.write_bytes(b"retained")
        with self.store.lease(identifier):
            for operation in (lambda: self.store.rename(identifier, "Renamed"), lambda: self.store.delete(identifier)):
                with self.assertRaises(ProfileBusy):
                    operation()
        self.assertEqual(self.store.rename(identifier, "Renamed")["name"], "Renamed")
        self.assertTrue(self.store.delete(identifier))
        self.assertEqual(marker.read_bytes(), b"retained")
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(ProfileNotFound):
            self.store.get(identifier)

    def test_deleted_or_colliding_uuid_never_reuses_old_browser_identity(self):
        identifier = self.create()["id"]
        self.store.delete(identifier)
        successor = uuid.uuid4()
        with patch("vietsub.uploader.profiles.uuid.uuid4", side_effect=[uuid.UUID(identifier), successor, uuid.uuid4(), uuid.uuid4()]):
            account = self.create()
        self.assertEqual(account["id"], str(successor))
        self.assertNotEqual(account["id"], identifier)

    def test_threaded_creates_do_not_lose_metadata(self):
        failures = []
        def create(index):
            try:
                ProfileStore(self.root).create("youtube", "Account " + str(index))
            except Exception as error:
                failures.append(error)
        workers = [threading.Thread(target=create, args=(index,)) for index in range(12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(self.store.list()), 12)

    def symlink(self, target, path, *, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Symlink creation unavailable: " + str(error))

    def test_root_symlink_is_refused(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        link = Path(self.temporary.name) / "linked"
        self.symlink(outside, link, directory=True)
        with self.assertRaises(UnsafeProfilePath):
            ProfileStore(link / "uploader")
        self.assertEqual(list(outside.iterdir()), [])

    def test_profile_platform_state_and_registry_symlinks_are_refused(self):
        identifier = self.create()["id"]
        directory = Path(self.store.profile_dir(identifier))
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        # The unused second account has an empty browser folder we can replace.
        directory.rmdir()
        self.symlink(outside, directory, directory=True)
        with self.assertRaises(UnsafeProfilePath):
            self.store.profile_dir(identifier)
        with self.assertRaises(UnsafeProfilePath):
            with self.store.lease(identifier):
                pass
        directory.unlink()
        directory.mkdir()
        state = Path(self.store.state_path(identifier))
        target = outside / "state.json"
        target.write_text('{"untouched":true}', encoding="utf-8")
        self.symlink(target, state)
        with self.assertRaises(UnsafeProfilePath):
            self.store.save_storage_state(identifier, {"cookies": [], "origins": []})
        self.assertEqual(target.read_text(encoding="utf-8"), '{"untouched":true}')
        state.unlink()
        saved_registry = self.store.registry_path.read_bytes()
        self.store.registry_path.unlink()
        target.write_bytes(saved_registry)
        self.symlink(target, self.store.registry_path)
        with self.assertRaises(UnsafeProfilePath):
            self.store.list()
        self.assertEqual(target.read_bytes(), saved_registry)

    def junction(self, target, path):
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            self.skipTest("Junction creation unavailable: " + result.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows directory junction test")
    def test_windows_junction_profile_is_refused_before_launch(self):
        identifier = self.create()["id"]
        directory = Path(self.store.profile_dir(identifier))
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        directory.rmdir()
        self.junction(outside, directory)
        with self.assertRaises(UnsafeProfilePath):
            self.store.profile_dir(identifier)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows directory junction test")
    def test_windows_root_and_nested_browser_junctions_are_refused(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        root_link = Path(self.temporary.name) / "linked"
        self.junction(outside, root_link)
        with self.assertRaises(UnsafeProfilePath):
            ProfileStore(root_link / "uploader")
        identifier = self.create()["id"]
        nested = Path(self.store.profile_dir(identifier)) / "Default"
        self.junction(outside, nested)
        with self.assertRaises(UnsafeProfilePath):
            with self.store.lease(identifier):
                self.fail("Nested junction entered by browser")
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
