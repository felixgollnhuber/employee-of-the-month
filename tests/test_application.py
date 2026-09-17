from pathlib import Path
import unittest
from unittest.mock import patch

from eotm.application import run_authorized_call_test
from eotm.control import GateError
from eotm.config import ConfigError


class ApplicationTests(unittest.TestCase):
    def test_explicit_gate_precedes_config_native_and_auth_access(self):
        with patch("eotm.application.read_profile") as read:
            with patch("eotm.application.NativeMedia") as native:
                with self.assertRaises(GateError):
                    run_authorized_call_test(Path("/not-accessed"), Path("/not-loaded"))
                read.assert_not_called()
                native.assert_not_called()

    def test_missing_target_blocks_before_native_or_auth_access(self):
        with patch("eotm.application.read_profile", return_value={"api_id": 123}):
            with patch("eotm.application.NativeMedia") as native:
                with self.assertRaises(ConfigError):
                    run_authorized_call_test(Path("/fixture"), Path("/unused"), audio_and_call_authorized=True)
                native.assert_not_called()


if __name__ == "__main__": unittest.main()
