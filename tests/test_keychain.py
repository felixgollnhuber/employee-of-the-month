import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from telegram_bridge.auth import AuthGate, existing_authenticated_client
from telegram_bridge.config import write_profile
from telegram_bridge.control import GateError
from telegram_bridge.keychain import cached_database_key, remember_database_key, account_for, MARKER


class KeychainTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.profile=Path(tmp.name)
        (self.profile/'database').mkdir(mode=0o700)
        (self.profile/'key-salt').write_bytes(b's'*32)
        os.chmod(self.profile/'key-salt',0o600)
        write_profile(self.profile,{'api_id':123,'api_hash':'0'*32,'sender_phone':'+12025550123'})
        self.key=base64.b64encode(b'k'*32).decode()

    def test_unconfigured_profile_does_not_touch_keychain(self):
        with patch('telegram_bridge.keychain._helper') as helper:
            self.assertIsNone(cached_database_key(self.profile))
            helper.assert_not_called()

    def test_stored_key_bypasses_every_passphrase_prompt(self):
        td=Mock()
        td.receive.side_effect=[{'@type':'authorizationStateWaitTdlibParameters'},{'@type':'authorizationStateReady'}]
        secret=Mock(side_effect=AssertionError('Unexpected prompt'))
        with patch('telegram_bridge.keychain.cached_database_key',return_value=self.key), \
             patch('telegram_bridge.auth.local_key') as derive, \
             patch('telegram_bridge.auth.TDJson',return_value=td), \
             patch('telegram_bridge.live.RequestPump') as pump:
            pump.return_value.request.return_value={'phone_number':'12025550123'}
            with existing_authenticated_client(self.profile,Path('/unused'),secret_input=secret) as opened:
                self.assertIs(opened,td)
            secret.assert_not_called();derive.assert_not_called()
        parameters=next(x.args[0] for x in td.send.call_args_list if x.args[0]['@type']=='setTdlibParameters')
        self.assertEqual(parameters['database_encryption_key'],self.key)
        td.close.assert_called_once()

    def test_failed_verification_never_saves_key(self):
        with patch('telegram_bridge.keychain.install_helper'), \
             patch('telegram_bridge.auth.local_key',return_value=self.key), \
             patch('telegram_bridge.auth.existing_authenticated_client',side_effect=AuthGate('fixture failure')), \
             patch('telegram_bridge.keychain._helper') as helper:
            with self.assertRaises(AuthGate):
                remember_database_key(self.profile,Path('/unused'),secret_input=Mock())
            helper.assert_not_called()
        self.assertFalse((self.profile/MARKER).exists())

    def test_verified_key_is_saved_only_in_keychain_and_marker_is_bound_to_salt(self):
        @contextmanager
        def authenticated(*args,**kwargs):
            self.assertEqual(kwargs['database_key'],self.key)
            yield None
        with patch('telegram_bridge.keychain.install_helper'), \
             patch('telegram_bridge.auth.local_key',return_value=self.key), \
             patch('telegram_bridge.auth.existing_authenticated_client',side_effect=authenticated), \
             patch('telegram_bridge.keychain._helper',side_effect=lambda op,*args:self.key if op=='get' else 'stored'):
            remember_database_key(self.profile,Path('/unused'),secret_input=Mock())
            marker=(self.profile/MARKER).read_text()
            self.assertNotIn(self.key,marker)
            self.assertEqual(json.loads(marker),{'version':1,'account':account_for(self.profile)})
            self.assertEqual(cached_database_key(self.profile),self.key)
            (self.profile/'key-salt').write_bytes(b't'*32)
            with self.assertRaisesRegex(GateError,'binding_changed'):cached_database_key(self.profile)

    def test_keychain_failure_does_not_fall_back_to_prompt(self):
        with patch('telegram_bridge.keychain.cached_database_key',side_effect=GateError('macos_keychain_unavailable')), \
             patch('telegram_bridge.auth.local_key') as derive:
            with self.assertRaises(GateError):
                with existing_authenticated_client(self.profile,Path('/unused')):pass
            derive.assert_not_called()


if __name__=='__main__':unittest.main()
