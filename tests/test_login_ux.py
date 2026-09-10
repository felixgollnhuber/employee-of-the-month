from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from telegram_bridge.auth import local_key, login
from telegram_bridge.config import write_profile
from telegram_bridge.__main__ import main


class LoginUXTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)

    def test_short_passphrase_retries_without_echo_and_reuses_salt(self):
        inputs = iter(["short-secret", "long-local-fixture-passphrase"])
        messages = []
        key = local_key(self.profile, lambda _: next(inputs), messages.append)
        self.assertEqual(len(messages), 1)
        self.assertIn("16 Zeichen", messages[0])
        self.assertNotIn("short-secret", messages[0])
        self.assertFalse((self.profile / "database").exists())
        salt = (self.profile / "key-salt").read_bytes()
        repeated = local_key(self.profile, lambda _: "long-local-fixture-passphrase", messages.append)
        self.assertEqual(key, repeated)
        self.assertEqual(salt, (self.profile / "key-salt").read_bytes())

    def test_cancel_after_short_passphrase_precedes_database_and_native_client(self):
        write_profile(self.profile, {"api_id": 123, "api_hash": "0" * 32, "sender_phone": "+12025550123"})
        inputs = iter(["short", KeyboardInterrupt()])
        def enter(_):
            value = next(inputs)
            if isinstance(value, BaseException): raise value
            return value
        with patch("telegram_bridge.auth.local_key", side_effect=lambda p: local_key(p, enter, lambda _: None)):
            with patch("telegram_bridge.auth.TDJson") as native:
                with self.assertRaises(KeyboardInterrupt): login(self.profile, Path("/unused"), lambda _: None)
                native.assert_not_called()
        self.assertFalse((self.profile / "database").exists())
        self.assertTrue((self.profile / "config.json").exists())

    def test_existing_config_skips_all_secret_prompts_and_preserves_file(self):
        write_profile(self.profile, {"api_id": 123, "api_hash": "0" * 32, "sender_phone": "+12025550123"})
        before = (self.profile / "config.json").read_bytes()
        output = io.StringIO()
        with patch("sys.argv", ["telegram_bridge", "configure"]), patch("telegram_bridge.__main__.profile_path", return_value=self.profile):
            with patch("telegram_bridge.__main__.getpass.getpass") as secret, redirect_stdout(output):
                self.assertEqual(main(), 0)
                secret.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertTrue(result["unchanged"])
        self.assertIn("login", result["next_step"])
        self.assertNotIn("0" * 32, output.getvalue())
        self.assertEqual(before, (self.profile / "config.json").read_bytes())

    def test_existing_database_gets_reuse_notice_not_reset(self):
        database = self.profile / "database"
        database.mkdir()
        marker = database / "untouched"
        marker.write_text("fixture")
        messages = []
        local_key(self.profile, lambda _: "long-local-fixture-passphrase", messages.append)
        self.assertIn("bisherige", messages[0])
        self.assertEqual(marker.read_text(), "fixture")


if __name__ == "__main__": unittest.main()
