import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from telegram_bridge.auth import AuthGate
from telegram_bridge.control import GateError
from telegram_bridge.ringing import RingingSession, macos_passphrase, ring_once, run_authorized_ring_test
from test_live import FixtureTD, FixtureMedia


class RingingTests(unittest.TestCase):
    def test_no_call_without_consent_or_valid_duration(self):
        for kwargs in ({}, {"call_authorized": True, "max_seconds": 0}):
            with patch("telegram_bridge.ringing.read_profile") as profile:
                with self.assertRaises(GateError):
                    run_authorized_ring_test(Path("/unused"), Path("/unused"), Path("/unused"), **kwargs)
                profile.assert_not_called()

    def test_answer_ends_one_real_signaling_lifecycle_without_media(self):
        td = FixtureTD()
        statuses = []
        result = ring_once(td, "@allowed_target", FixtureMedia().protocol(), consent=True,
                           emit=statuses.append, clock=lambda: td.now)
        self.assertEqual(result["phase"], "ended")
        self.assertTrue(result["answer_reported"])
        self.assertFalse(result["audio_opened"])
        self.assertTrue(all(not s["media_connected"] for s in statuses))
        self.assertEqual(sum(x["@type"] == "createCall" for x in td.sent), 1)
        self.assertEqual(sum(x["@type"] == "discardCall" for x in td.sent), 1)

    def test_missing_answer_times_out_and_confirms_discard(self):
        td = FixtureTD()
        original = td.send
        def send(request):
            original(request)
            if request["@type"] == "createCall":
                td.events[-1]["call"]["state"] = {"@type": "callStatePending", "is_received": True}
        td.send = send
        result = ring_once(td, "@allowed_target", FixtureMedia().protocol(), consent=True,
                           max_seconds=1, clock=lambda: td.now)
        self.assertEqual(result["phase"], "ended")
        self.assertTrue(result["delivery_reported"])
        self.assertFalse(result["answer_reported"])
        self.assertLess(td.now, 3)

    def test_early_ready_is_replayed_without_opening_media_and_foreign_calls_ignored(self):
        requests = []
        session = RingingSession(requests.append, FixtureMedia().protocol())
        session.start(100001, authorized=True, consent=True)
        session.handle({"@type": "updateCall", "call": {"id": 77, "user_id": 55,
            "is_outgoing": False, "state": {"@type": "callStateReady"}}})
        session.handle({"@type": "updateCall", "call": {"id": 9, "user_id": 100001,
            "is_outgoing": True, "state": {"@type": "callStateReady", "encryption_key": "PRIVATE"}}})
        session.handle({"@type": "callId", "id": 9, "@extra": session.create_tag})
        self.assertTrue(session.answered)
        self.assertFalse(session.media_started)
        self.assertEqual([x["call_id"] for x in requests if x["@type"] == "discardCall"], [9])
        self.assertNotIn("PRIVATE", json.dumps(session.status()))

    def test_late_create_after_timeout_is_discarded_once_and_never_claims_end(self):
        requests = []
        now = [0]
        session = RingingSession(requests.append, FixtureMedia().protocol(), clock=lambda: now[0])
        session.start(100001, authorized=True, consent=True)
        session.end("ring_test_timeout")
        reply = {"@type": "callId", "id": 9, "@extra": session.create_tag}
        session.handle(reply)
        session.handle(reply)
        now[0] = 6
        session.tick()
        self.assertEqual(session.phase, "end_unconfirmed")
        self.assertEqual(sum(x["@type"] == "discardCall" for x in requests), 1)

    def test_target_privacy_rejection_precedes_call(self):
        td = FixtureTD()
        td.allowed = False
        with self.assertRaises(GateError):
            ring_once(td, "@allowed_target", FixtureMedia().protocol(), consent=True, clock=lambda: td.now)
        self.assertFalse(any(x["@type"] == "createCall" for x in td.sent))

    def test_dialog_preserves_whitespace_and_does_not_expose_error_output(self):
        with patch("telegram_bridge.ringing.subprocess.run", return_value=subprocess.CompletedProcess([], 0, " secret \n", "")) as run:
            self.assertEqual(macos_passphrase("ignored"), " secret ")
            self.assertTrue(run.call_args.kwargs["capture_output"])
        with patch("telegram_bridge.ringing.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "PRIVATE", "PRIVATE")):
            with self.assertRaises(AuthGate) as raised:
                macos_passphrase("ignored")
            self.assertNotIn("PRIVATE", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
