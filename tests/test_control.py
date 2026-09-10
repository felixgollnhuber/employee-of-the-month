import json
import unittest

from telegram_bridge.control import CallSession, GateError


class FixtureMedia:
    available = True

    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.received = []

    def protocol(self):
        # Deliberately not a real/advertised tgcalls version.
        return {"@type": "callProtocol", "min_layer": 1, "max_layer": 1,
                "library_versions": ["offline-fixture"], "udp_p2p": False, "udp_reflector": True}

    def start(self, state, callback, signaling):
        self.starts += 1
        self.callback = callback
        self.emit_signaling = signaling

    def receive_signaling(self, data):
        self.received.append(data)

    def stop(self):
        self.stops += 1

    def relay_id(self):
        return 19


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.media = FixtureMedia()
        self.now = 10.0
        self.session = CallSession(self.sent.append, self.media, clock=lambda: self.now)

    def start(self):
        self.session.start(100001, authorized=True, consent=True)

    def created(self):
        self.session.handle({"@type": "callId", "id": 7, "@extra": self.session.create_tag})

    def event(self, state, call_id=7, **fields):
        return {"@type": "updateCall", "call": {"id": call_id, "user_id": 100001,
                "is_outgoing": True, "state": {"@type": state, **fields}}}

    def test_gates_send_nothing(self):
        for kwargs in ({}, {"authorized": True}, {"authorized": True, "consent": False}):
            with self.assertRaises(GateError):
                self.session.start(100001, **kwargs)
        session = CallSession(self.sent.append)
        with self.assertRaisesRegex(GateError, "media"):
            session.start(100001, authorized=True, consent=True)
        self.assertEqual(self.sent, [])

    def test_audio_only_and_duplicate_start_rejected(self):
        self.start()
        self.assertIs(self.sent[0]["is_video"], False)
        with self.assertRaises(GateError):
            self.start()
        self.assertEqual(len(self.sent), 1)

    def test_ready_does_not_mean_audible_and_status_is_redacted(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateReady", encryption_key="NEVER_LOG_THIS", config="PRIVATE"))
        self.assertEqual(self.session.phase, "media_connecting")
        self.assertFalse(self.session.status()["media_connected"])
        self.assertNotIn("NEVER_LOG_THIS", json.dumps(self.session.status()))
        self.assertNotIn("100001", json.dumps(self.session.status()))
        self.media.callback("connected")
        self.assertEqual(self.session.phase, "active")

    def test_end_is_idempotent_and_requires_terminal_update(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateReady"))
        self.media.callback("connected")
        self.now += 3
        self.session.end(); self.session.end()
        self.assertEqual(self.media.stops, 1)
        ends = [x for x in self.sent if x["@type"] == "discardCall"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["duration"], 3)
        self.assertEqual(ends[0]["connection_id"], "19")
        self.session.handle({"@type": "ok", "@extra": self.session.end_tag})
        self.assertEqual(self.session.phase, "ending")
        self.session.handle(self.event("callStateDiscarded"))
        self.assertEqual(self.session.phase, "ended")

    def test_cancel_before_create_response_discards_late_call(self):
        self.start(); self.session.end(); self.created()
        self.assertEqual(self.sent[-1]["@type"], "discardCall")
        self.assertEqual(self.sent[-1]["call_id"], 7)
        self.assertEqual(self.media.starts, 0)

    def test_updates_before_response_are_replayed_only_for_owned_call(self):
        self.start()
        self.session.handle(self.event("callStatePending", is_received=True))
        self.session.handle(self.event("callStateReady", call_id=99))
        self.created()
        self.assertEqual(self.session.phase, "ringing")
        self.assertEqual(self.media.starts, 0)

    def test_foreign_terminal_update_does_not_end_owned_call(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateDiscarded", call_id=99))
        self.assertEqual(self.session.phase, "dialing")

    def test_duplicate_ready_starts_one_engine(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateReady"))
        self.session.handle(self.event("callStateReady"))
        self.assertEqual(self.media.starts, 1)

    def test_late_media_callback_cannot_revive_ended_call(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateReady"))
        self.session.handle(self.event("callStateDiscarded"))
        self.media.callback("connected")
        self.assertEqual(self.session.phase, "ended")

    def test_media_failure_discards_call(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateReady"))
        self.media.callback("failed")
        self.assertEqual(self.session.reason, "media_failed")
        self.assertEqual(self.sent[-1]["@type"], "discardCall")

    def test_signaling_is_scoped_and_stops_after_end(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateReady"))
        self.session.handle({"@type": "updateNewCallSignalingData", "call_id": 99, "data": "ignored"})
        self.session.handle({"@type": "updateNewCallSignalingData", "call_id": 7, "data": "aA=="})
        self.assertEqual(self.media.received, ["aA=="])
        self.media.emit_signaling("aQ==")
        self.assertEqual(self.sent[-1]["@type"], "sendCallSignalingData")
        self.session.end()
        count = len(self.sent)
        self.media.emit_signaling("aQ==")
        self.assertEqual(len(self.sent), count)

    def test_timeout_never_claims_confirmed_hangup(self):
        self.start(); self.created()
        self.now += 21; self.session.tick()
        self.assertEqual(self.session.phase, "ending")
        self.now += 6; self.session.tick()
        self.assertEqual(self.session.phase, "end_unconfirmed")

    def test_request_error_does_not_expose_message(self):
        self.start()
        self.session.handle({"@type": "error", "code": 400, "message": "PRIVATE_NUMBER", "@extra": self.session.create_tag})
        self.assertEqual(self.session.phase, "failed")
        self.assertNotIn("PRIVATE_NUMBER", json.dumps(self.session.status()))

    def test_terminal_call_cannot_be_reused(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateDiscarded"))
        with self.assertRaises(GateError):
            self.start()

    def test_signaling_before_ready_is_not_lost(self):
        self.start()
        self.session.handle({"@type": "updateNewCallSignalingData", "call_id": 7, "data": "aA=="})
        self.created()
        self.session.handle({"@type": "updateNewCallSignalingData", "call_id": 7, "data": "aQ=="})
        self.session.handle(self.event("callStateReady"))
        self.assertEqual(self.media.received, ["aA==", "aQ=="])

    def test_media_stop_failure_still_attempts_hangup(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateReady"))
        def failed_stop():
            raise RuntimeError("private engine details")
        self.media.stop = failed_stop
        self.session.end()
        self.assertFalse(self.session.status()["media_cleanup_confirmed"])
        self.assertEqual(self.sent[-1]["@type"], "discardCall")

    def test_reconnecting_is_not_reported_as_active_and_has_own_deadline(self):
        self.start(); self.created()
        self.session.handle(self.event("callStateReady")); self.media.callback("connected")
        self.now += 60; self.media.callback("reconnecting"); self.session.tick()
        self.assertEqual(self.session.phase, "media_reconnecting")
        self.assertFalse(self.session.status()["media_connected"])
        self.now += 2; self.media.callback("connected")
        self.assertEqual(self.session.phase, "active")
        self.media.callback("reconnecting"); self.now += 21; self.session.tick()
        self.assertEqual(self.session.phase, "ending")


if __name__ == "__main__":
    unittest.main()
