import base64
import copy
import unittest

from telegram_bridge.control import GateError
from telegram_bridge.descriptor import normalize_ready


def fixture_ready():
    return {"@type": "callStateReady", "protocol": {"library_versions": ["12.0.0"]},
            "encryption_key": base64.b64encode(bytes(range(256))).decode(), "allow_p2p": False,
            "custom_parameters": "{}", "servers": [
                {"id": "20", "ip_address": "192.0.2.1", "ipv6_address": "2001:db8::1", "port": 443,
                 "type": {"@type": "callServerTypeTelegramReflector", "peer_tag": base64.b64encode(b"a" * 16).decode(), "is_tcp": False}},
                {"id": "10", "ip_address": "192.0.2.2", "ipv6_address": "", "port": 443,
                 "type": {"@type": "callServerTypeTelegramReflector", "peer_tag": base64.b64encode(b"b" * 16).decode(), "is_tcp": True}},
                {"id": "30", "ip_address": "192.0.2.3", "ipv6_address": "", "port": 3478,
                 "type": {"@type": "callServerTypeWebrtc", "username": "fixture-turn-user", "password": "fixture-turn-password",
                          "supports_stun": True, "supports_turn": True}},
            ]}


class DescriptorTests(unittest.TestCase):
    def convert(self, ready=None):
        return normalize_ready(ready or fixture_ready(), "fixture.input.uid", "fixture.output.uid")

    def test_reflector_mapping_follows_sorted_ids_and_hex_tags(self):
        result = self.convert()
        self.assertEqual([s["id"] for s in result["rtc_servers"]], [2, 2, 1, 0, 0])
        self.assertEqual(result["rtc_servers"][0]["password"], (b"a" * 16).hex())
        self.assertIs(result["rtc_servers"][2]["is_tcp"], True)

    def test_key_direction_privacy_and_devices(self):
        result = self.convert()
        self.assertEqual(len(result["key_hex"]), 512)
        self.assertIs(result["outgoing"], True)
        self.assertIs(result["enable_p2p"], False)
        self.assertNotEqual(result["input_uid"], result["output_uid"])

    def test_stun_and_turn_are_separate_entries(self):
        servers = self.convert()["rtc_servers"]
        self.assertFalse(servers[-2]["is_turn"])
        self.assertEqual(servers[-2]["password"], "")
        self.assertTrue(servers[-1]["is_turn"])
        self.assertEqual(servers[-1]["password"], "fixture-turn-password")

    def test_unsupported_protocol_is_not_silently_downgraded(self):
        ready = fixture_ready(); ready["protocol"]["library_versions"] = ["13.0.0"]
        with self.assertRaises(GateError): self.convert(ready)

    def test_invalid_key_and_tag_rejected_without_echoing_them(self):
        for field in ("key", "tag"):
            ready = fixture_ready()
            if field == "key": ready["encryption_key"] = "PRIVATE_INVALID_KEY"
            else: ready["servers"][0]["type"]["peer_tag"] = "PRIVATE_INVALID_TAG"
            with self.assertRaises(GateError) as error: self.convert(ready)
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_no_default_or_same_device(self):
        for a, b in (("default", "output"), ("input", "DEFAULT"), ("same", "same"), ("#1", "output")):
            with self.assertRaises(GateError): normalize_ready(fixture_ready(), a, b)

    def test_unknown_server_types_rejected(self):
        ready = fixture_ready(); ready["servers"][0]["type"]["@type"] = "new-unhandled-type"
        with self.assertRaises(GateError): self.convert(ready)

    def test_invalid_port_or_address_rejected(self):
        for field, value in (("port", 1.5), ("port", 0), ("ip_address", "not-an-ip")):
            ready = fixture_ready(); ready["servers"][0][field] = value
            with self.assertRaises(GateError): self.convert(ready)

    def test_input_data_is_not_mutated(self):
        ready = fixture_ready(); before = copy.deepcopy(ready)
        self.convert(ready)
        self.assertEqual(ready, before)


if __name__ == "__main__": unittest.main()
