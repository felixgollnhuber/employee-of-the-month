import asyncio
import base64
import json
import threading
import unittest

from telegram_bridge.control import GateError
from telegram_bridge.live_voice import LiveVoice, decode_audio, session_start, wants_hangup


class FakeSocket:
    def __init__(self, initial=None):
        self.sent = []
        self.events = None
        self.initial = initial
        self.saw_audio = threading.Event()
    async def __aenter__(self):
        self.events = asyncio.Queue()
        return self
    async def __aexit__(self, *_): pass
    def __aiter__(self): return self
    async def __anext__(self): return json.dumps(await self.events.get())
    async def send(self, text):
        event = json.loads(text); self.sent.append(event)
        if event['type'] == 'session.start':
            await self.events.put(self.initial or {'type': 'session.started'})
        if event['type'] == 'session.input_audio.append':
            self.saw_audio.set()
            await self.events.put({'type':'session.output_audio.delta', 'delta':event['audio']})
        if event['type'] == 'session.close':
            await self.events.put({'type':'session.closed'})


class LiveVoiceTests(unittest.TestCase):
    def make_voice(self, socket, **kwargs):
        return LiveVoice('sk-fixture-only', instructions='Fixture', audio_out=kwargs.pop('audio_out', lambda _:None),
                         connector=lambda *a, **k: socket, **kwargs)

    def test_pcm_roundtrip_waits_for_start_and_close_receipt(self):
        socket = FakeSocket(); output = []; delivered = threading.Event()
        voice = self.make_voice(socket, audio_out=lambda data:(output.append(data), delivered.set()))
        voice.push_audio(b'\x12\x34'*160)
        voice.start()
        self.assertTrue(delivered.wait(2))
        voice.close()
        self.assertEqual(output, [b'\x12\x34'*160])
        self.assertEqual(socket.sent[0]['type'], 'session.start')
        self.assertEqual(socket.sent[-1]['type'], 'session.close')
        self.assertTrue(voice.close_confirmed)
        self.assertFalse(voice.thread.is_alive())

    def test_server_error_ends_call_and_does_not_log_private_message(self):
        socket = FakeSocket({'type':'error','error':{'code':'model_not_found','message':'PRIVATE'}})
        logs = []; failed = threading.Event()
        voice = self.make_voice(socket, emit=logs.append, on_failure=failed.set)
        with self.assertRaises(GateError): voice.start()
        self.assertTrue(failed.wait(1))
        self.assertNotIn('PRIVATE', json.dumps(logs))
        self.assertNotIn('sk-fixture-only', json.dumps(logs))

    def test_maximum_duration_closes_api_and_signals_telegram(self):
        failed = threading.Event(); voice = self.make_voice(FakeSocket(), max_seconds=1, on_failure=failed.set)
        voice.start()
        self.assertTrue(voice.finished.wait(3))
        self.assertTrue(failed.is_set())
        self.assertTrue(voice.close_confirmed)

    def test_pcm_and_budget_validation(self):
        for data in ('!', base64.b64encode(b'x').decode()):
            with self.assertRaises(GateError): decode_audio({'delta':data})
        with self.assertRaises(GateError): self.make_voice(FakeSocket(), max_seconds=0)
        voice = self.make_voice(FakeSocket())
        with self.assertRaises(GateError): voice.push_audio(b'x')
        config = session_start('Fixture')['session']
        self.assertEqual(config['model'], 'gpt-live-1')
        self.assertEqual(config['delegation'], {'type':'client'})
        self.assertEqual(config['audio']['format']['rate'],16000)
        self.assertEqual(config['audio']['output']['voice'],'cedar')
        self.assertEqual(session_start('Fixture','marin')['session']['audio']['output']['voice'],'marin')
        with self.assertRaises(GateError):session_start('Fixture','unknown')

    def test_hangup_intent_is_not_triggered_by_negation_or_quoted_discussion(self):
        for text in ('Bitte leg auf.', 'Kannst du jetzt bitte auflegen?', 'Okay, passt, dann kannst du auflegen.', 'Tschüss!'):
            self.assertTrue(wants_hangup(text), text)
        for text in ('Bitte nicht auflegen.', 'Wenn ich auflegen sage, was passiert?', 'Schreib den Text: Bitte leg auf.', 'Wie kann ich auflegen?'):
            self.assertFalse(wants_hangup(text), text)

    def test_spoken_hangup_says_goodbye_closes_live_and_signals_telegram(self):
        class Socket(FakeSocket):
            async def send(self, text):
                await super().send(text)
                if json.loads(text)['type']=='session.start':
                    await self.events.put({'type':'session.input_transcript.delta','delta':'Kannst du jetzt '})
                    await self.events.put({'type':'session.input_transcript.delta','delta':'bitte auflegen?'})
        socket = Socket(); hangup = threading.Event()
        voice = self.make_voice(socket, on_hangup=hangup.set, max_seconds=5)
        voice.start()
        self.assertTrue(voice.finished.wait(4))
        self.assertTrue(hangup.is_set())
        self.assertTrue(voice.close_confirmed)
        self.assertEqual(sum(e['type']=='session.instructions.append' for e in socket.sent),1)


if __name__ == '__main__': unittest.main()
