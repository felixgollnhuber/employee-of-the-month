import unittest

from eotm.control import GateError, UnavailableMedia
from eotm.live import LiveCallLoop, RequestError, RequestPump, resolve_target


class FixtureTD:
    def __init__(self):
        self.sent = []
        self.events = []
        self.now = 0.0
        self.user_id = 100001
        self.user_type = "userTypeRegular"
        self.username = "allowed_target"
        self.allowed = True
        self.private = True
        self.authorized = True

    def send(self, request):
        self.sent.append(request)
        kind = request["@type"]
        result = {
            "getAuthorizationState": {"@type": "authorizationStateReady" if self.authorized else "authorizationStateWaitTdlibParameters"},
            "getMe": {"@type": "user", "id": 200001},
            "searchPublicChat": {"@type": "chat", "type": {"@type": "chatTypePrivate" if self.private else "chatTypeSupergroup", "user_id": self.user_id}},
            "getUser": {"@type": "user", "id": self.user_id, "type": {"@type": self.user_type}, "usernames": {"active_usernames": [self.username]}},
            "getUserFullInfo": {"@type": "userFullInfo", "can_be_called": self.allowed},
            "createCall": {"@type": "callId", "id": 9},
            "discardCall": {"@type": "ok"},
        }.get(kind, {"@type": "ok"})
        self.events.append({**result, "@extra": request.get("@extra")})
        if kind == "createCall":
            self.events.append({"@type": "updateCall", "call": {"id": 9, "state": {"@type": "callStateReady"}}})
        elif kind == "discardCall":
            self.events.append({"@type": "updateCall", "call": {"id": 9, "state": {"@type": "callStateDiscarded"}}})

    def receive(self, timeout):
        self.now += timeout
        return self.events.pop(0) if self.events else None


class FixtureMedia:
    available = True

    def protocol(self):
        return {"@type": "callProtocol", "min_layer": 1, "max_layer": 1,
                "library_versions": ["offline-fixture"], "udp_p2p": False, "udp_reflector": True}

    def start(self, state, callback, signaling):
        callback("connected")

    def receive_signaling(self, data):
        pass

    def stop(self):
        pass

    def relay_id(self):
        return 0


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.td = FixtureTD()
        self.pump = RequestPump(self.td, clock=lambda: self.td.now)

    def test_resolve_only_explicit_username_not_contacts(self):
        target = resolve_target(self.pump, "@allowed_target")
        self.assertEqual(target.user_id, 100001)
        self.assertEqual([x["@type"] for x in self.td.sent], [
            "getAuthorizationState", "getMe", "searchPublicChat", "getUser", "getUserFullInfo"])

    def test_reject_self_bot_group_changed_username_and_privacy(self):
        for field, value in (("user_id", 200001), ("user_type", "userTypeBot"),
                             ("private", False), ("username", "different_user"), ("allowed", False)):
            self.setUp()
            setattr(self.td, field, value)
            with self.assertRaises(GateError):
                resolve_target(self.pump, "@allowed_target")
            self.assertFalse(any(x["@type"] == "createCall" for x in self.td.sent))

    def test_no_target_lookups_before_auth(self):
        self.td.authorized = False
        with self.assertRaises(GateError):
            resolve_target(self.pump, "@allowed_target")
        self.assertEqual(len(self.td.sent), 1)

    def test_media_gate_precedes_all_tdlib_requests(self):
        loop = LiveCallLoop(self.td, UnavailableMedia())
        with self.assertRaisesRegex(GateError, "media"):
            loop.run("@allowed_target", consent=True)
        self.assertEqual(self.td.sent, [])

    def test_bounded_lifecycle_connects_then_confirms_end(self):
        statuses = []
        loop = LiveCallLoop(self.td, FixtureMedia(), statuses.append, clock=lambda: self.td.now)
        result = loop.run("@allowed_target", consent=True, max_seconds=1)
        self.assertEqual(result["phase"], "ended")
        self.assertTrue(any(x["phase"] == "active" for x in statuses))
        self.assertEqual(sum(x["@type"] == "createCall" for x in self.td.sent), 1)
        self.assertEqual(sum(x["@type"] == "discardCall" for x in self.td.sent), 1)

    def test_target_query_timeout_is_bounded(self):
        self.td.send = lambda request: None
        with self.assertRaisesRegex(RequestError, "timeout"):
            resolve_target(self.pump, "@allowed_target")
        self.assertLess(self.td.now, 11)

    def test_unauthorized_side_effects_are_rejected(self):
        loop = LiveCallLoop(self.td, FixtureMedia())
        with self.assertRaises(GateError):
            loop.run("@allowed_target", consent=False)
        self.assertEqual(self.td.sent, [])


if __name__ == "__main__":
    unittest.main()
