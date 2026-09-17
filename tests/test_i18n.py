import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from eotm.__main__ import main
from eotm.config import validate_live_config, profile_path, write_profile
from eotm.followups import parse_followup
from eotm.i18n import Locale
from eotm.live_voice import wants_hangup
from eotm.tasks import explicit_confirmation
from eotm.t3 import validate_connection_files


class LocaleTests(unittest.TestCase):
    def test_new_and_legacy_live_configuration_defaults(self):
        key = 'sk-fixture-only'
        current = validate_live_config({'api_key': key})
        legacy = validate_live_config({'api_key': key}, legacy=True)
        self.assertEqual(current['language'], 'en')
        self.assertEqual(legacy['language'], 'de')
        self.assertEqual(current['agent_name'], 'Employee of the Month')

    def test_english_and_german_prompts_are_complete(self):
        english = Locale('en', 'Alex', 'EOTM')
        german = Locale('de', 'Felix', 'Mitarbeiter des Monats')
        self.assertIn('Greet Alex', english.text('greeting'))
        self.assertIn('Begrüße Felix', german.text('greeting'))
        self.assertIn('Return JSON only', english.t3_dialog_prompt({'fixture': True}))
        self.assertIn('Antworte nur als JSON', german.t3_dialog_prompt({'fixture': True}))

    def test_parsers_accept_both_languages_without_accepting_qualifiers(self):
        self.assertTrue(explicit_confirmation('Yes, start'))
        self.assertTrue(explicit_confirmation('Ja, starte'))
        self.assertFalse(explicit_confirmation('Yes, start but use Claude'))
        self.assertTrue(wants_hangup('Please hang up now'))
        self.assertTrue(wants_hangup('Bitte leg jetzt auf'))
        self.assertFalse(wants_hangup('Do not hang up'))

    def test_english_followup_preserves_exact_target_and_payload(self):
        parsed = parse_followup('Send the thread "Checkout redesign": Please cover the error state.')
        self.assertEqual(parsed, {'target': 'Checkout redesign', 'text': 'Please cover the error state.'})


class SetupTests(unittest.TestCase):
    def test_profile_path_prefers_existing_legacy_profile_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / 'current'
            legacy = root / 'legacy'
            with patch('eotm.config.PROFILE_ROOT', current), patch('eotm.config.LEGACY_PROFILE_ROOT', legacy):
                self.assertEqual(profile_path('default'), current / 'default')
                (legacy / 'default').mkdir(parents=True)
                self.assertEqual(profile_path('default'), legacy / 'default')
                (current / 'default').mkdir(parents=True)
                self.assertEqual(profile_path('default'), current / 'default')

    def test_t3_credentials_are_private_and_token_stays_out_of_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / 'session.json'
            credential.write_text(json.dumps({'token': 'PRIVATE'}))
            os.chmod(credential, 0o600)
            config = validate_connection_files('http://127.0.0.1:3773', credential)
            self.assertEqual(config['credentials_file'], str(credential.resolve()))
            self.assertNotIn('token', config)
            os.chmod(credential, 0o644)
            with self.assertRaisesRegex(Exception, 'unsafe_t3_credentials_file'):
                validate_connection_files('http://127.0.0.1:3773', credential)

    def test_configure_t3_writes_only_the_private_file_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / 'profile'
            write_profile(profile, {'api_id': 123, 'api_hash': '0' * 32,
                                     'sender_phone': '+12025550123'})
            credential = root / 'session.json'
            credential.write_text(json.dumps({'token': 'PRIVATE'}))
            os.chmod(credential, 0o600)
            output = io.StringIO()
            with patch('sys.argv', ['eotm', 'configure-t3', '--origin', 'http://127.0.0.1:3773',
                                    '--credentials-file', str(credential)]), \
                 patch('eotm.__main__.profile_path', return_value=profile), redirect_stdout(output):
                self.assertEqual(main(), 0)
            saved = json.loads((profile / 't3.json').read_text())
            self.assertEqual(saved['credentials_file'], str(credential.resolve()))
            self.assertNotIn('PRIVATE', (profile / 't3.json').read_text())
            self.assertTrue(json.loads(output.getvalue())['t3_configured'])

    def test_install_service_stages_but_never_starts_launchd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            package = source / 'eotm'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('')
            (package / '__main__.py').write_text('')
            profile = root / 'profile'
            write_profile(profile, {'api_id': 123, 'api_hash': '0' * 32,
                                     'sender_phone': '+12025550123'})
            dependencies = []
            for name in ('python', 'libtdjson.dylib', 'media_runtime'):
                path = root / name
                path.write_text('fixture')
                dependencies.append(path)
            output = io.StringIO()
            with patch('sys.argv', ['eotm', 'install-service', '--root', str(source),
                                    '--python', str(dependencies[0]), '--library', str(dependencies[1]),
                                    '--media-runtime', str(dependencies[2])]), \
                 patch('eotm.__main__.profile_path', return_value=profile), \
                 patch('eotm.__main__.service_root', return_value=root / 'service'), redirect_stdout(output):
                self.assertEqual(main(), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result['service_staged'])
            self.assertFalse(result['launch_agent_started'])
            self.assertTrue(Path(result['plist']).is_file())


if __name__ == '__main__':
    unittest.main()
