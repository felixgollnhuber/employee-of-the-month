import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from telegram_bridge.control import GateError
from telegram_bridge.t3 import T3Client, T3Delegation, parse_coordinator
from test_handoff import snapshot


class Opener:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []
    def open(self, request, timeout):
        self.requests.append(request)
        if self.error: raise self.error
        return self
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,*args): return json.dumps(self.response).encode()


class T3Tests(unittest.TestCase):
    def test_followup_preserves_source_modes_and_does_not_restart_interrupted_work(self):
        client=T3Client('http://localhost','PRIVATE')
        source={'id':'source','latestTurn':{'state':'completed'},'runtimeMode':'full-access','interactionMode':'plan'}
        client.snapshot=Mock(return_value={'thread':source})
        client.dispatch=Mock(return_value={'sequence':1})
        client.start_source_followup('source','Bestätigt: PDF','phone-command')
        command=client.dispatch.call_args.args[0]
        self.assertEqual((command['runtimeMode'],command['interactionMode']),('full-access','plan'))
        self.assertNotIn('modelSelection',command)
        source['latestTurn']['state']='interrupted'
        client.dispatch.reset_mock()
        with self.assertRaises(GateError):client.start_source_followup('source','PDF','other')
        client.dispatch.assert_not_called()

    def test_thread_identity_is_verified_and_token_is_only_in_header(self):
        opener = Opener({'thread':{'id':'source'}})
        client = T3Client('http://127.0.0.1:3773','PRIVATE',opener=opener)
        client.snapshot('source')
        self.assertEqual(opener.requests[0].full_url,'http://127.0.0.1:3773/api/orchestration/threads/source')
        self.assertEqual(opener.requests[0].get_header('Authorization'),'Bearer PRIVATE')
        with self.assertRaises(GateError): client.snapshot('different')

    def test_unsafe_origin_and_thread_paths_are_rejected(self):
        for origin in ('http://example.com','https://user:secret@example.com','https://example.com/path'):
            with self.assertRaises(GateError): T3Client(origin,'PRIVATE')
        client = T3Client('http://localhost:3773','PRIVATE')
        with self.assertRaises(GateError): client.snapshot('../another')

    def test_dispatch_error_does_not_expose_response_body_or_token(self):
        error = HTTPError('http://localhost',403,'PRIVATE',{},None)
        client = T3Client('http://localhost','PRIVATE',opener=Opener(error=error))
        with self.assertRaises(GateError) as raised: client.dispatch({'type':'fixture'})
        self.assertEqual(str(raised.exception),'t3_http_403')

    def test_confirmed_answer_requires_quote_from_latest_user_statement(self):
        result = {'reply':'Verstanden','answer':{'intent':'decision','confirmed':True,'confirmation_quote':'Ja, Blau passt.','answers':{'farbe':'Blau'}}}
        transcript = [{'role':'assistant','text':'Soll ich Blau zurückgeben?'},{'role':'user','text':'Ja, Blau passt.'}]
        self.assertEqual(parse_coordinator(json.dumps(result),transcript),result)
        transcript[-1]['text'] = 'Nein, lieber Grün.'
        with self.assertRaises(GateError): parse_coordinator(json.dumps(result),transcript)
        result['answer'] = 'unstructured'
        with self.assertRaises(GateError): parse_coordinator(json.dumps(result),transcript)

    def test_new_speech_during_backend_work_prevents_stale_answer_submission(self):
        revision=[0]
        class Client:
            def snapshot(self,identifier):return snapshot()
            def create_coordinator(self,source):return 'coordinator'
            def run_coordinator(self,*args,**kwargs):
                revision[0]=1
                return json.dumps({'reply':'Verstanden','answer':{'intent':'decision','confirmed':True,'confirmation_quote':'Ja','answers':{'choice':'Blau'}}})
            def return_answer(self,*args,**kwargs):raise AssertionError('Stale answer submitted')
        bridge=T3Delegation(Client(),'t3-thread','request-1',context='Fixture')
        bridge.revision=lambda:revision[0]
        result=bridge([{'role':'user','text':'Ja'}],revision=0)
        self.assertIn('neue Aussage',result)
        self.assertFalse(bridge.completed)


if __name__ == '__main__': unittest.main()
