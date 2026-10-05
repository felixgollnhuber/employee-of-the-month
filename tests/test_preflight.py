import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from eotm.__main__ import main
from eotm.config import write_private_json, write_profile
from eotm.preflight import offline_preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name) / "profile"

    def test_malformed_profiles_report_blockers_without_opening_clients(self):
        for name, value, expected in (
            ("config.json", [], "local_account_configuration"),
            ("config.json", {"api_id": 123, "api_hash": [], "sender_phone": []}, "local_account_configuration"),
            ("target.json", {"target_username": []}, "confirmed_target"),
            ("live.json", [], "openai_api_key"),
            ("live.json", {"api_key": "sk-fixture-only", "voice": []}, "openai_api_key"),
            ("t3.json", {"origin": [], "credentials_file": "fixture"}, "t3"),
            ("t3.json", {"origin": "http://localhost", "credentials_file": []}, "t3"),
        ):
            with self.subTest(name=name, value=value):
                if name == "target.json":
                    write_profile(self.profile, {"api_id": 123, "api_hash": "0" * 32,
                                                 "sender_phone": "+12025550123"})
                write_private_json(self.profile, name, value)
                try:
                    with patch("eotm.native.TDJson", side_effect=AssertionError("Native client opened")), \
                         patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network opened")):
                        status = offline_preflight(self.profile)
                    if expected == "t3":
                        self.assertFalse(status["t3_connection_configured"])
                    else:
                        self.assertIn(expected, status["blockers"])
                    self.assertFalse(status["ready_for_call_attempt"])
                    self.assertNotIn("sk-fixture-only", json.dumps(status))
                finally:
                    (self.profile / name).unlink()
                    if name == "target.json":
                        (self.profile / "config.json").unlink()

    def test_cli_returns_json_and_blocked_exit_code_for_a_malformed_profile(self):
        write_private_json(self.profile, "config.json", [])
        output = io.StringIO()
        with patch("sys.argv", ["eotm", "preflight"]), \
             patch("eotm.__main__.profile_path", return_value=self.profile), redirect_stdout(output):
            self.assertEqual(main(), 2)
        self.assertIn("local_account_configuration", json.loads(output.getvalue())["blockers"])

    def test_valid_local_profile_remains_readable_offline(self):
        write_profile(self.profile, {"api_id": 123, "api_hash": "0" * 32,
                                     "sender_phone": "+12025550123", "target_username": "@offline_fixture"})
        status = offline_preflight(self.profile)
        self.assertTrue(status["configuration_present_and_private"])
        self.assertTrue(status["target_configured"])
        self.assertEqual(status["telegram_authenticated"], "not_checked_offline")


if __name__ == "__main__":
    unittest.main()
