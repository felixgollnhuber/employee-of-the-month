import json
import unittest
from urllib.error import HTTPError

from eotm.control import GateError
from eotm.structurer import Structurer, DEFAULT_MODEL


class Opener:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error: raise self.error
        return self
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,*args): return json.dumps(self.response).encode()


def message(text):
    return {'type':'message','role':'assistant','content':[{'type':'output_text','text':text}]}


class StructurerTests(unittest.TestCase):
    def test_one_stateless_json_request_returns_the_message_text(self):
        opener = Opener({'output':[{'type':'reasoning','summary':[]}, message('{"reply":"Verstanden","answer":null}')]})
        structurer = Structurer('sk-fixture-only', opener=opener)
        self.assertEqual(structurer('Antworte mit JSON. Daten folgen.'), '{"reply":"Verstanden","answer":null}')
        (request, timeout), = opener.requests
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        self.assertEqual(request.get_header('Authorization'), 'Bearer sk-fixture-only')
        body = json.loads(request.data)
        self.assertEqual(body['model'], DEFAULT_MODEL)
        self.assertEqual(body['input'], 'Antworte mit JSON. Daten folgen.')
        self.assertIs(body['store'], False)
        self.assertEqual(body['reasoning'], {'effort':'none'})
        self.assertEqual(body['text'], {'format':{'type':'json_object'}})
        self.assertLessEqual(timeout, 15)

    def test_http_error_names_only_the_status(self):
        error = HTTPError('https://api.openai.com',401,'sk-fixture-only',{},None)
        structurer = Structurer('sk-fixture-only', opener=Opener(error=error))
        with self.assertRaises(GateError) as raised: structurer('JSON')
        self.assertEqual(str(raised.exception), 'structurer_http_401')

    def test_response_without_message_text_is_rejected(self):
        for response in ({'output':[]}, {'output':[{'type':'message','content':[{'type':'refusal','refusal':'Nein'}]}]}, {'error':'x'}, []):
            structurer = Structurer('sk-fixture-only', opener=Opener(response))
            with self.assertRaisesRegex(GateError, 'structurer_response_invalid'): structurer('JSON')

    def test_key_model_and_prompt_are_validated_before_any_request(self):
        opener = Opener({'output':[message('{}')]})
        with self.assertRaisesRegex(GateError, 'openai_api_key_required'): Structurer('PRIVATE', opener=opener)
        with self.assertRaisesRegex(GateError, 'invalid_structuring_model'): Structurer('sk-fixture-only', model='luna; rm', opener=opener)
        with self.assertRaisesRegex(GateError, 'invalid_structurer_prompt'): Structurer('sk-fixture-only', opener=opener)('x'*200001)
        self.assertEqual(opener.requests, [])

    def test_configuration_selects_model_and_can_switch_the_direct_path_off(self):
        self.assertEqual(Structurer.from_config({'api_key':'sk-fixture-only'}).model, DEFAULT_MODEL)
        self.assertEqual(Structurer.from_config({'api_key':'sk-fixture-only','structuring_model':'gpt-5.6-terra'}).model, 'gpt-5.6-terra')
        self.assertIsNone(Structurer.from_config({'api_key':'sk-fixture-only','structuring_model':None}))

    def test_profile_helper_reads_the_private_live_configuration(self):
        import os, tempfile
        from pathlib import Path
        from eotm.config import write_private_json
        from eotm.structurer import structurer_for_profile
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            write_private_json(Path(directory), 'live.json', {'api_key':'sk-fixture-only','structuring_model':'gpt-5.6-terra'})
            self.assertEqual(structurer_for_profile(Path(directory)).model, 'gpt-5.6-terra')


if __name__=='__main__':unittest.main()
