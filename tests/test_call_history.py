import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from telegram_bridge.call_history import CallHistory, bounded_transcript, MAX_CALLS, MAX_TRANSCRIPT_CHARS
from telegram_bridge.service import VoiceConversation
import test_tasks_and_daemon as task_fixtures


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)

    def test_history_survives_restart_without_normal_hangup(self):
        history = CallHistory(self.profile, background=False)
        history.begin('call-1', incoming=True)
        history.update('call-1', transcript=[{'role':'user','text':'Wir besprechen die Suche für helth.'}], proposal_id='pending')
        history.flush()
        restored = CallHistory(self.profile, background=False)
        self.assertEqual(restored.recent()[0]['state'], 'interrupted')
        self.assertEqual(restored.recent()[0]['proposal_id'], 'pending')
        self.assertIn('helth', restored.recent()[0]['transcript'][0]['text'])
        self.assertEqual(os.stat(history.path).st_mode & 0o777, 0o600)

    def test_transcript_is_bounded_and_secret_forms_are_redacted(self):
        messages = [{'role':'user','text':'Erstes Thema'}, {'role':'assistant','text':'Frage'}]
        messages += [{'role':'user','text':'x'*7000} for _ in range(10)]
        messages.append({'role':'user','text':'Letzte Korrektur: sk-proj-'+'z'*40})
        result = bounded_transcript(messages)
        self.assertLessEqual(sum(len(m['text']) for m in result), MAX_TRANSCRIPT_CHARS)
        self.assertEqual(result[0]['text'], 'Erstes Thema')
        self.assertIn('Letzte Korrektur', result[-1]['text'])
        self.assertNotIn('sk-proj-', json.dumps(result))

    def test_only_recent_calls_are_retained_and_current_call_is_excluded(self):
        history = CallHistory(self.profile, background=False)
        for i in range(12):
            history.begin(str(i)); history.finish(str(i), {'phase':'ended'})
        history.close()
        self.assertEqual(len(history.data['calls']), MAX_CALLS)
        self.assertNotIn('11', [c['id'] for c in history.recent(exclude='11')])
        self.assertEqual(history.recent()[-1]['end_status']['phase'], 'ended')

    def test_background_writer_flushes_on_close(self):
        history = CallHistory(self.profile)
        history.begin('one')
        history.update('one', transcript=[{'role':'user','text':'Noch nicht bestätigt.'}])
        history.close()
        self.assertFalse(history.writer.is_alive())
        self.assertIn('Noch nicht bestätigt', history.path.read_text())

    def test_api_transcripts_are_saved_without_backend_delegation(self):
        from telegram_bridge.live_voice import LiveVoice
        from test_live_voice import FakeSocket
        import time
        class Socket(FakeSocket):
            async def send(self, raw):
                await super().send(raw)
                if json.loads(raw)['type'] == 'session.start':
                    await self.events.put({'type':'session.input_transcript.delta','delta':'Mein Thema bleibt helth.'})
                    await self.events.put({'type':'session.output_transcript.delta','delta':'Verstanden.'})
        history = CallHistory(self.profile, background=False)
        history.begin('call')
        voice = LiveVoice('sk-fixture', instructions='Test', audio_out=lambda _:None,
                          connector=lambda *a,**k:Socket(),
                          on_transcript=lambda transcript:history.update('call', transcript=transcript))
        try:
            voice.start()
            until = time.monotonic()+1
            while len(history.recent()[0]['transcript']) < 2 and time.monotonic() < until: time.sleep(.005)
        finally:
            voice.close(); history.close()
        saved = json.loads(history.path.read_text())['calls'][0]['transcript']
        self.assertEqual([m['role'] for m in saved], ['user','assistant'])
        self.assertIn('helth', saved[0]['text'])


class CallbackContextTests(unittest.TestCase):
    def setUp(self):
        self.f = task_fixtures.TaskTests('test_explicit_confirmed_order_starts_one_correct_thread_with_proposed_model')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.history = CallHistory(self.f.store.profile, background=False)

    def test_callback_reuses_same_proposal_but_requires_new_confirmation_after_readback(self):
        job = self.f.propose()
        self.history.begin('old-call')
        self.history.update('old-call', transcript=[{'role':'user','text':'Die Suche war unser Thema.'},
                                                    {'role':'user','text':'Ja, starte'}], proposal_id=job['id'])
        self.history.finish('old-call', {'phase':'ended','reason':'maximum_duration'})
        self.history.flush()
        restored = CallHistory(self.f.store.profile, background=False)
        voice = VoiceConversation(self.f.c, threading.RLock(), launcher=self.f.launcher, history=restored)
        restored.begin(voice.conversation_id)
        self.f.client.responses = [json.dumps({'reply':'Fortsetzen','operation_id':None,'resume_proposal_id':job['id']})]
        voice.revision = lambda: 1
        reply = voice([{'role':'user','text':'Ja'}], revision=1)
        self.assertIn('Soll ich diesen Auftrag', reply)
        self.assertFalse(self.f.commands)
        self.assertEqual(voice.proposal_id, job['id'])
        self.assertEqual(len(self.f.launcher.jobs), 1)
        self.assertIn('Die Suche war unser Thema', self.f.client.prompts[-1])
        self.assertIn('Historischer Kontext', self.f.client.prompts[-1])
        voice.revision = lambda: 2
        reply = voice([{'role':'user','text':'Ja'}], revision=2)
        self.assertIn('gestartet', reply)
        self.assertEqual(self.f.commands[0]['threadId'], job['thread_id'])
        self.assertEqual(len(self.f.commands), 2)

    def test_greeting_cannot_replay_an_old_confirmation(self):
        self.f.propose()
        voice = VoiceConversation(self.f.c, threading.RLock(), launcher=self.f.launcher, history=self.history)
        reply = voice([{'role':'assistant','text':'Willkommen zurück.'}], revision=0)
        self.assertIn('keine neue Bestätigung', reply)
        self.assertFalse(self.f.commands)
        self.assertFalse(self.f.client.prompts)

    def test_new_call_context_includes_legacy_saved_order_before_first_delegation(self):
        job = self.f.propose()
        voice = VoiceConversation(self.f.c, threading.RLock(), launcher=self.f.launcher, history=self.history)
        context = voice.recent_context()
        self.assertEqual(context['saved_orders'][0]['id'], job['id'])
        self.assertEqual(context['saved_orders'][0]['state'], 'proposed')
        self.assertFalse(self.f.commands)

    def test_regular_dialog_is_journaled_even_without_a_task(self):
        voice = VoiceConversation(self.f.c, threading.RLock(), history=self.history)
        self.history.begin(voice.conversation_id)
        voice.remember([{'role':'user','text':'Beim nächsten Mal weiter darüber sprechen.'}])
        self.history.flush()
        restored = CallHistory(self.f.store.profile, background=False)
        self.assertIn('nächsten Mal', restored.recent()[0]['transcript'][0]['text'])
