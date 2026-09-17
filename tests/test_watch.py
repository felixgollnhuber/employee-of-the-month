import json
import copy
from datetime import datetime,timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from eotm.control import GateError
from eotm.watch import pending_requests, question_due, watch_project
from test_handoff import snapshot


class WatchTests(unittest.TestCase):
    def test_only_pending_user_questions_are_selected(self):
        state=snapshot()
        self.assertEqual(pending_requests(state),['request-1'])
        state['thread']['activities'].append({'id':'later','createdAt':'2026-09-12','kind':'user-input.resolved','payload':{'requestId':'request-1'}})
        self.assertEqual(pending_requests(state),[])
        state['thread']['activities'][0]['kind']='approval.requested'
        self.assertEqual(pending_requests(state),[])

    def test_delay_uses_original_question_time_and_rejects_invalid_or_future_dates(self):
        state=snapshot()
        asked=datetime(2026,9,11,10,tzinfo=timezone.utc).timestamp()
        self.assertFalse(question_due(state,'request-1',180,asked+179))
        self.assertTrue(question_due(state,'request-1',180,asked+180))
        self.assertTrue(question_due(state,'request-1',180,asked+900))
        self.assertFalse(question_due(state,'request-1',180,asked-1))
        for invalid in ('invalid','2026-09-11T10:00:00',None):
            state['thread']['activities'][0]['createdAt']=invalid
            self.assertFalse(question_due(state,'request-1',180,asked+900))

    def test_question_is_called_after_three_minutes_but_not_if_answered_beforehand(self):
        for answered_at in (None,170):
            with self.subTest(answered_at=answered_at), tempfile.TemporaryDirectory() as tmp:
                elapsed=[0.0]
                asked=datetime(2026,9,11,10,tzinfo=timezone.utc).timestamp()
                class Client:
                    def request(self,path):
                        return {'projects':[{'id':'project'}], 'threads':[
                            {'id':'t3-thread','projectId':'project','hasPendingUserInput':True}]}
                    def snapshot(self,identifier):
                        state=copy.deepcopy(snapshot())
                        if answered_at is not None and elapsed[0]>=answered_at:
                            state['thread']['activities'].append({'id':'resolved','createdAt':'2026-09-11T10:02:50Z',
                                'kind':'user-input.resolved','payload':{'requestId':'request-1'}})
                        return state
                def sleep(seconds):elapsed[0]+=seconds
                def call(*args,**kwargs):
                    self.assertGreaterEqual(elapsed[0],180)
                    return {'phase':'ended'}
                with patch('eotm.watch.T3Client.from_profile',return_value=Client()), \
                     patch('eotm.watch.cached_database_key',return_value='fixture-key'), \
                     patch('eotm.watch.T3Delegation',return_value=SimpleNamespace(packet={},completed=False)), \
                     patch('eotm.watch.structurer_for_profile',return_value=None), \
                     patch('eotm.watch.run_authorized_live_test',side_effect=call) as dial, \
                     patch('eotm.watch.time.monotonic',side_effect=lambda:elapsed[0]), \
                     patch('eotm.watch.time.time',side_effect=lambda:asked+elapsed[0]), \
                     patch('eotm.watch.time.sleep',side_effect=sleep):
                    watch_project(Path(tmp),Path('/unused'),'project',authorized=True,
                        secret_input=Mock(side_effect=AssertionError('Unexpected prompt')),watch_seconds=200,question_delay_seconds=180)
                    self.assertEqual(dial.call_count,1 if answered_at is None else 0)
    def test_no_access_without_explicit_start(self):
        with patch('eotm.watch.T3Client') as client:
            with self.assertRaises(GateError):
                watch_project(Path('/unused'),Path('/unused'),'project',secret_input=lambda _:None)
            client.assert_not_called()

    def test_watcher_with_saved_key_never_prompts_for_passphrase(self):
        client=Mock()
        client.request.return_value={'projects':[{'id':'project'}],'threads':[]}
        secret=Mock(side_effect=AssertionError('Unexpected password prompt'))
        with tempfile.TemporaryDirectory() as tmp, \
             patch('eotm.watch.T3Client.from_profile',return_value=client), \
             patch('eotm.watch.cached_database_key',return_value='fixture-key'), \
             patch('eotm.watch.time.monotonic',side_effect=[0,0,2,2]), \
             patch('eotm.watch.time.sleep'):
            watch_project(Path(tmp),Path('/unused'),'project',authorized=True,secret_input=secret,watch_seconds=1)
            secret.assert_not_called()

    def test_attempt_is_saved_before_call_and_not_repeated_after_restart(self):
        class Client:
            def request(self,path):
                return {'projects':[{'id':'project'}], 'threads':[
                    {'id':'other','projectId':'unselected','hasPendingUserInput':True},
                    {'id':'t3-thread','projectId':'project','hasPendingUserInput':True}]}
            def snapshot(self,identifier):
                if identifier!='t3-thread':raise AssertionError('Wrong project selected')
                return snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            profile=Path(tmp)
            elapsed=[0.0]
            def sleep(_):elapsed[0]+=2
            def call(*args,**kwargs):
                saved=(profile/'watch-attempts.json').read_text()
                self.assertIn('t3-thread:request-1',saved)
                self.assertNotIn('PRIVATE PASSPHRASE',saved)
                return {'phase':'ended'}
            delegate=SimpleNamespace(packet={'fixture':True},completed=False)
            with patch('eotm.watch.T3Client.from_profile',return_value=Client()), \
                 patch('eotm.watch.T3Delegation',return_value=delegate), \
                 patch('eotm.watch.structurer_for_profile',return_value=None), \
                 patch('eotm.watch.run_authorized_live_test',side_effect=call) as dial, \
                 patch('eotm.watch.time.monotonic',side_effect=lambda:elapsed[0]), \
                 patch('eotm.watch.time.sleep',side_effect=sleep):
                for _ in range(2):
                    elapsed[0]=0
                    watch_project(profile,Path('/unused'),'project',authorized=True,
                        secret_input=lambda _:'PRIVATE PASSPHRASE FIXTURE',watch_seconds=1,max_calls=1,question_delay_seconds=0)
                self.assertEqual(dial.call_count,1)
            ledger=profile/'watch-attempts.json'
            self.assertEqual(ledger.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads(ledger.read_text())['t3-thread:request-1']['status'],'open_after_ended')


if __name__=='__main__':unittest.main()
