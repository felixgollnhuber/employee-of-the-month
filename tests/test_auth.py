from pathlib import Path
import unittest

from eotm.auth import AuthGate, auth_request


class AuthTests(unittest.TestCase):
    config = {"api_id": 123, "api_hash": "0" * 32, "sender_phone": "+12025550123"}

    def request(self, kind):
        return auth_request({"@type": kind}, self.config, Path("/fixture"), "fixture-key", lambda _: "fixture-secret")

    def test_no_message_database_or_secret_chats(self):
        result = self.request("authorizationStateWaitTdlibParameters")
        for key in ("use_file_database", "use_chat_info_database", "use_message_database", "use_secret_chats"):
            self.assertIs(result[key], False)
        self.assertEqual(result["database_encryption_key"], "fixture-key")

    def test_existing_sender_only(self):
        result = self.request("authorizationStateWaitPhoneNumber")
        self.assertEqual(result["@type"], "setAuthenticationPhoneNumber")
        self.assertEqual(result["phone_number"], self.config["sender_phone"])

    def test_local_secret_inputs(self):
        self.assertEqual(self.request("authorizationStateWaitCode")["code"], "fixture-secret")
        self.assertEqual(self.request("authorizationStateWaitPassword")["password"], "fixture-secret")

    def test_no_automatic_account_creation_or_payment(self):
        for kind in ("authorizationStateWaitRegistration", "authorizationStateWaitPremiumPurchase",
                     "authorizationStateWaitEmailAddress", "authorizationStateWaitOtherDeviceConfirmation"):
            with self.assertRaises(AuthGate):
                self.request(kind)

    def test_ready_sends_no_calls(self):
        self.assertIsNone(self.request("authorizationStateReady"))


if __name__ == "__main__":
    unittest.main()
