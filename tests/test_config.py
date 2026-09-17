import os
from pathlib import Path
import tempfile
import unittest

from eotm.config import ConfigError, profile_path, read_profile, write_profile, read_routing, write_routing


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "profile"
        self.fixture = {"api_id": 123, "api_hash": "0" * 32,
                        "sender_phone": "+12025550123", "target_username": "@offline_fixture"}

    def test_private_roundtrip_and_no_overwrite(self):
        write_profile(self.path, self.fixture)
        self.assertEqual(read_profile(self.path), self.fixture)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.path / "config.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            write_profile(self.path, self.fixture)

    def test_profile_traversal_rejected(self):
        with self.assertRaises(ConfigError):
            profile_path("../../repository")

    def test_symlink_config_rejected(self):
        write_profile(self.path, self.fixture)
        (self.path / "config.json").rename(self.path / "original")
        (self.path / "config.json").symlink_to(self.path / "original")
        with self.assertRaises(OSError):
            read_profile(self.path)

    def test_world_readable_configuration_rejected(self):
        write_profile(self.path, self.fixture)
        os.chmod(self.path / "config.json", 0o644)
        with self.assertRaises(ConfigError):
            read_profile(self.path)

    def test_no_arbitrary_fields_saved(self):
        write_profile(self.path, {**self.fixture, "message_history": "must not save"})
        self.assertNotIn("message_history", read_profile(self.path))

    def test_invalid_profile_writes_nothing(self):
        with self.assertRaises(ConfigError):
            write_profile(self.path, {**self.fixture, "sender_phone": "use my WhatsApp number"})
        self.assertFalse(self.path.exists())

    def test_private_routing_roundtrip(self):
        write_routing(self.path, "fixture.input", "fixture.output")
        self.assertEqual(read_routing(self.path), {"input_uid": "fixture.input", "output_uid": "fixture.output"})
        self.assertEqual((self.path / "routing.json").stat().st_mode & 0o777, 0o600)

    def test_routing_rejects_same_device(self):
        with self.assertRaises(ConfigError): write_routing(self.path, "same", "same")

    def test_login_profile_does_not_require_a_call_target(self):
        data = {key: value for key, value in self.fixture.items() if key != "target_username"}
        write_profile(self.path, data)
        self.assertEqual(read_profile(self.path), data)


if __name__ == "__main__":
    unittest.main()
