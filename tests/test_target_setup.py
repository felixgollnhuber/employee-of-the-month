from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eotm.__main__ import main
from eotm.config import ConfigError, read_profile, write_profile, write_target, write_private_json


class TargetSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name)
        self.credentials = {"api_id": 123, "api_hash": "0" * 32, "sender_phone": "+12025550123"}
        write_profile(self.profile, self.credentials)

    def test_target_setup_preserves_credentials_database_and_salt(self):
        (self.profile / "database").mkdir()
        (self.profile / "database" / "td.binlog").write_bytes(b"encrypted fixture")
        (self.profile / "key-salt").write_bytes(b"s" * 32)
        files = [self.profile / name for name in ("config.json", "database/td.binlog", "key-salt")]
        before = {path: path.read_bytes() for path in files}
        self.assertTrue(write_target(self.profile, "@offline_fixture"))
        self.assertEqual(read_profile(self.profile), {**self.credentials, "target_username": "@offline_fixture"})
        self.assertEqual(before, {path: path.read_bytes() for path in files})
        target = self.profile / "target.json"
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(target.read_text()), {"target_username": "@offline_fixture"})

    def test_repeated_target_is_idempotent_and_different_target_is_rejected(self):
        write_target(self.profile, "@offline_fixture")
        before = (self.profile / "target.json").read_bytes()
        self.assertFalse(write_target(self.profile, "@offline_fixture"))
        with self.assertRaises(ConfigError):
            write_target(self.profile, "@another_fixture")
        self.assertEqual((self.profile / "target.json").read_bytes(), before)

    def test_invalid_target_or_missing_account_writes_nothing(self):
        with self.assertRaises(ConfigError):
            write_target(self.profile, "not a username")
        self.assertFalse((self.profile / "target.json").exists())
        with self.assertRaises(ConfigError):
            write_target(self.profile / "missing", "@offline_fixture")
        self.assertFalse((self.profile / "missing").exists())

    def test_legacy_target_remains_usable_and_conflicting_files_are_rejected(self):
        legacy = self.profile / "legacy"
        write_profile(legacy, {**self.credentials, "target_username": "@offline_fixture"})
        self.assertFalse(write_target(legacy, "@offline_fixture"))
        self.assertFalse((legacy / "target.json").exists())
        write_private_json(legacy, "target.json", {"target_username": "@another_fixture"})
        with self.assertRaises(ConfigError):
            read_profile(legacy)

    def test_unsafe_or_broken_symlink_target_is_not_treated_as_missing(self):
        write_target(self.profile, "@offline_fixture")
        target = self.profile / "target.json"
        target.chmod(0o644)
        with self.assertRaises(ConfigError):
            read_profile(self.profile)
        target.unlink()
        target.symlink_to(self.profile / "missing")
        with self.assertRaises(OSError):
            read_profile(self.profile)

    def test_cli_saves_target_without_printing_private_values_or_loading_runtime(self):
        output = io.StringIO()
        with patch("sys.argv", ["eotm", "configure-target"]), \
             patch("sys.stdin.isatty", return_value=True), \
             patch("eotm.__main__.profile_path", return_value=self.profile), \
             patch("eotm.__main__.getpass.getpass", return_value=" @offline_fixture "), \
             patch("eotm.auth.TDJson") as td, \
             patch("eotm.runtime.NativeIPC") as runtime, redirect_stdout(output):
            self.assertEqual(main(), 0)
            td.assert_not_called()
            runtime.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {
            "target_configured": True, "unchanged": False, "login_requested": False, "calls_started": 0})
        self.assertEqual(read_profile(self.profile)["target_username"], "@offline_fixture")


if __name__ == "__main__":
    unittest.main()
