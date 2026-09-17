import asyncio
import base64
import json
import threading
import time
import unittest

from telegram_bridge.control import GateError
from telegram_bridge.live_voice import LiveVoice, decode_audio, session_start, wants_hangup, mentions_hangup, is_farewell


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
        self.assertEqual(self.make_voice(FakeSocket(), max_seconds=1200).max_seconds, 1200)
        with self.assertRaises(GateError): self.make_voice(FakeSocket(), max_seconds=1201)
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

    def test_each_backend_reply_reports_its_duration_without_any_transcript_text(self):
        class Socket(FakeSocket):
            async def send(self, text):
                await super().send(text)
                if json.loads(text)['type']=='session.start':
                    await self.events.put({'type':'session.input_transcript.delta','delta':'Wie läuft der Bericht?'})
                    await self.events.put({'type':'session.delegation.created','delegation':{'id':'item_1','target':'client'}})
        socket = Socket(); events = []; spoken = threading.Event()
        def delegate(transcript):
            spoken.set()
            return 'Der Bericht läuft.'
        voice = self.make_voice(socket, delegate=delegate, emit=events.append)
        voice.start()
        self.assertTrue(spoken.wait(3))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any('backend_reply_seconds' in e for e in events): time.sleep(.01)
        voice.close()
        timing = next(e for e in events if 'backend_reply_seconds' in e)
        self.assertEqual(set(timing), {'backend_reply_seconds'})
        self.assertGreaterEqual(timing['backend_reply_seconds'], 0)
        self.assertTrue(any(e['type']=='session.commentary.append' for e in socket.sent))

    def test_context_added_before_the_session_starts_is_part_of_session_start(self):
        socket = FakeSocket()
        voice = self.make_voice(socket)
        self.assertTrue(voice.extend_instructions('\nStatus: Bericht läuft.'))
        voice.start()
        voice.close()
        self.assertEqual(socket.sent[0]['session']['instructions'], 'Fixture\nStatus: Bericht läuft.')

    def test_oversized_context_is_refused_without_raising_into_the_service_loop(self):
        voice = self.make_voice(FakeSocket())
        self.assertFalse(voice.extend_instructions('x'*50000))
        self.assertFalse(voice.extend_instructions(None))
        self.assertEqual(voice.instructions, 'Fixture')

    def test_context_after_session_start_is_refused_and_never_interrupts_speech(self):
        socket = FakeSocket()
        voice = self.make_voice(socket)
        voice.start()
        self.assertFalse(voice.extend_instructions('\nStatus: Bericht läuft.'))
        voice.close()
        self.assertEqual(socket.sent[0]['session']['instructions'], 'Fixture')
        self.assertFalse(any(e['type']=='session.instructions.append' for e in socket.sent))

    def speaking_socket(self, user_text, assistant_text=None, audio_seconds=0.0, *, later=None,
                        transcript_delay=0.0, opening=None, answer_at=None):
        """The caller speaks (deltas split like the API does); the model optionally answers with a transcript and
        audio. later=(seconds, text) is a further caller statement. transcript_delay lets the caller's transcript
        trail the model's audio burst. opening is an earlier, finished caller statement."""
        class Socket(FakeSocket):
            async def send(inner, text):
                await super().send(text)
                if json.loads(text)['type']=='session.start':
                    loop = asyncio.get_running_loop()
                    offset = 0.0
                    if opening:
                        await inner.events.put({'type':'session.input_transcript.delta','delta':opening})
                        offset = 1.7  # A pause above 1.5 s starts a new caller segment.
                    loop.call_later(offset + transcript_delay, lambda: asyncio.ensure_future(inner.caller(user_text)))
                    answer = answer_at if answer_at is not None else 0.0 if transcript_delay else 1.2
                    loop.call_later(offset + answer, lambda: asyncio.ensure_future(inner.answer()))
                    if later: loop.call_later(offset + later[0], lambda: asyncio.ensure_future(inner.caller(later[1])))
            async def caller(inner, words):
                half = len(words)//2
                for delta in (words[:half], words[half:]):
                    await inner.events.put({'type':'session.input_transcript.delta','delta':delta})
            async def answer(inner):
                if assistant_text is None: return
                await inner.events.put({'type':'session.output_transcript.delta','delta':assistant_text})
                chunk = base64.b64encode(b'\x01\x00'*1600).decode()  # 100 ms each
                for _ in range(int(audio_seconds*10)):
                    await inner.events.put({'type':'session.output_audio.delta','delta':chunk})
        return Socket()

    def paced_output(self):
        played = []
        def audio_out(data):
            time.sleep(len(data)/32000)
            played.append(time.monotonic())
        return played, audio_out

    def test_greeting_is_triggered_once_right_after_the_session_started(self):
        socket = FakeSocket()
        voice = self.make_voice(socket, greeting='Begrüße Felix jetzt.')
        voice.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(socket.sent) < 2: time.sleep(.01)
        voice.close()
        self.assertEqual(socket.sent[0]['type'], 'session.start')
        self.assertEqual(socket.sent[1], {'type':'session.instructions.append','delegation_id':None,'content':'Begrüße Felix jetzt.'})
        self.assertEqual(sum(e['type']=='session.instructions.append' for e in socket.sent), 1)

    def test_no_greeting_trigger_unless_requested(self):
        socket = FakeSocket()
        voice = self.make_voice(socket)
        voice.start(); time.sleep(.3); voice.close()
        self.assertFalse(any(e['type']=='session.instructions.append' for e in socket.sent))

    def test_hangup_waits_until_the_models_own_goodbye_has_been_played(self):
        socket = self.speaking_socket('Passt, dann kannst du auflegen', " Alles klar, mach's gut.", audio_seconds=1.5)
        played, audio_out = self.paced_output()
        hung_up = []
        voice = self.make_voice(socket, audio_out=audio_out, on_hangup=lambda: hung_up.append(time.monotonic()), max_seconds=15)
        voice.start()
        self.assertTrue(voice.finished.wait(10))
        self.assertEqual(len(hung_up), 1)
        self.assertEqual(len(played), 15)
        self.assertGreaterEqual(hung_up[0] - played[-1], 0.5)
        self.assertFalse(any(e['type']=='session.instructions.append' for e in socket.sent))
        self.assertTrue(voice.close_confirmed)

    def test_silent_model_is_prompted_once_for_a_goodbye_and_the_call_still_ends(self):
        socket = self.speaking_socket('Kannst du jetzt bitte auflegen?')
        hangup = threading.Event()
        voice = self.make_voice(socket, on_hangup=hangup.set, max_seconds=15)
        voice.start()
        self.assertTrue(voice.finished.wait(10))
        self.assertTrue(hangup.is_set())
        self.assertTrue(voice.close_confirmed)
        self.assertEqual(sum(e['type']=='session.instructions.append' for e in socket.sent),1)

    def test_fillers_before_the_request_end_the_call_once_the_model_says_farewell(self):
        events = []
        socket = self.speaking_socket(' Mhm. Das- dann kannst du jetzt auflegen. Danke', ' Mach ich. Tschau!', audio_seconds=0.8,
                                      opening='Wie ist der Stand')
        played, audio_out = self.paced_output()
        hangup = threading.Event()
        voice = self.make_voice(socket, audio_out=audio_out, on_hangup=hangup.set, emit=events.append, max_seconds=15)
        voice.start()
        self.assertTrue(voice.finished.wait(10))
        self.assertTrue(hangup.is_set())
        self.assertIn({'hangup_after_farewell': True}, events)
        self.assertFalse(any(e['type']=='session.instructions.append' for e in socket.sent))

    def ends(self, socket, seconds=10):
        played, audio_out = self.paced_output()
        hangup = threading.Event()
        voice = self.make_voice(socket, audio_out=audio_out, on_hangup=hangup.set, max_seconds=20)
        voice.start()
        finished = voice.finished.wait(seconds)
        if not finished: voice.close()
        return finished and hangup.is_set(), socket

    def test_taking_the_request_back_keeps_the_call_open(self):
        ended, _ = self.ends(self.speaking_socket('Okay, dann kannst du auflegen.', later=(2.6, 'Halt, eine Frage habe ich doch noch')), seconds=7)
        self.assertFalse(ended)

    def test_a_polite_reply_to_the_farewell_still_ends_the_call(self):
        ended, socket = self.ends(self.speaking_socket(' Mhm. Das- dann kannst du jetzt auflegen. Danke', ' Mach ich. Tschau!',
                                  audio_seconds=0.8, opening='Wie ist der Stand', later=(2.4, 'Tschau')))
        self.assertTrue(ended)

    def test_a_transcript_that_trails_the_goodbye_audio_does_not_cause_a_second_goodbye(self):
        ended, socket = self.ends(self.speaking_socket('Passt, dann kannst du auflegen', " Alles klar, mach's gut.",
                                  audio_seconds=1.5, transcript_delay=0.4), seconds=6)
        self.assertTrue(ended)
        self.assertFalse(any(e['type']=='session.instructions.append' for e in socket.sent))

    def test_a_goodbye_that_only_starts_during_the_final_wait_is_still_played_completely(self):
        socket = self.speaking_socket('Kannst du jetzt bitte auflegen?', ' Alles klar, tschüss!', audio_seconds=1.0, answer_at=6.0)
        played, audio_out = self.paced_output()
        voice = self.make_voice(socket, audio_out=audio_out, max_seconds=20)
        voice.start()
        self.assertTrue(voice.finished.wait(12))
        self.assertEqual(len(played), 10)

    def test_a_mirrored_greeting_at_the_start_of_a_call_is_not_a_farewell(self):
        ended, _ = self.ends(self.speaking_socket('Ciao', ' Ciao!', audio_seconds=0.4), seconds=5)
        self.assertFalse(ended)

    def test_mentioning_a_hangup_without_a_farewell_keeps_the_call_open(self):
        socket = self.speaking_socket('Ich musste vorhin auflegen, erzähl bitte weiter', ' Klar, also weiter mit dem Bericht.', audio_seconds=0.5,
                                      opening='Wie ist der Stand')
        played, audio_out = self.paced_output()
        hangup = threading.Event()
        voice = self.make_voice(socket, audio_out=audio_out, on_hangup=hangup.set, max_seconds=15)
        voice.start()
        self.assertFalse(voice.finished.wait(5))
        self.assertFalse(hangup.is_set())
        voice.close()

    def delegating_socket(self, echo=False):
        """The model delegates once; when the backend reply arrives, it speaks 300 ms of audio."""
        class Socket(FakeSocket):
            async def send(inner, text):
                await super().send(text)
                event = json.loads(text)
                if event['type']=='session.start':
                    inner.spoke_at = time.monotonic()
                    await inner.events.put({'type':'session.input_transcript.delta','delta':'Wie läuft der Bericht?'})
                    await inner.events.put({'type':'session.delegation.created','delegation':{'id':'item_1','target':'client'}})
                    if inner.echo: asyncio.get_running_loop().call_later(1.5, lambda: asyncio.ensure_future(
                        inner.events.put({'type':'session.input_transcript.delta','delta':' Mhm'})))
                if event['type']=='session.commentary.append':
                    for _ in range(3):
                        await inner.events.put({'type':'session.output_audio.delta','delta':base64.b64encode(b'\x01\x00'*1600).decode()})
        socket = Socket(); socket.echo = echo
        return socket

    def run_delegation(self, echo=False, **options):
        from telegram_bridge.live_voice import WAIT_TONE
        heard = []
        def audio_out(data):
            time.sleep(len(data)/32000)
            heard.append((time.monotonic(), data))
        window = {}
        def delegate(transcript):
            time.sleep(2.6)
            window['end'] = time.monotonic()
            return 'Der Bericht läuft.'
        socket = self.delegating_socket(echo)
        voice = self.make_voice(socket, delegate=delegate, audio_out=audio_out, max_seconds=15, **options)
        voice.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not any(d != WAIT_TONE for _, d in heard): time.sleep(.02)
        time.sleep(2.2)  # A tone after the model has answered would be a bug.
        voice.close()
        window['start'] = socket.spoke_at
        return voice, window, [t for t, d in heard if d == WAIT_TONE], [d for _, d in heard if d != WAIT_TONE]

    def test_wait_tone_sounds_only_while_the_backend_works_and_the_model_is_silent(self):
        voice, window, tones, speech = self.run_delegation(wait_tone=True)
        self.assertGreaterEqual(len(tones), 1)
        self.assertLessEqual(len(tones), 3)
        self.assertTrue(all(window['start'] + 0.65 <= t <= window['end'] + 0.5 for t in tones))  # Never while the caller talks.
        self.assertEqual(len(speech), 3)
        self.assertEqual(voice.output_bytes, 3*3200)  # Model audio only; the tone is not API output.

    def test_caller_speech_during_a_wait_is_reported_once_for_echo_diagnostics(self):
        events = []
        self.run_delegation(echo=True, wait_tone=True, emit=events.append)
        self.assertEqual(events.count({'caller_speech_during_wait': True}), 1)

    def test_no_wait_tone_unless_enabled(self):
        _, _, tones, speech = self.run_delegation()
        self.assertEqual(tones, [])
        self.assertEqual(len(speech), 3)

    def test_wait_tone_is_a_short_quiet_click_free_pcm_signal(self):
        import array
        from telegram_bridge.live_voice import WAIT_TONE
        samples = array.array('h'); samples.frombytes(WAIT_TONE)
        self.assertEqual(len(WAIT_TONE) % 2, 0)
        self.assertLessEqual(len(samples), 16000*0.4)
        self.assertGreaterEqual(max(abs(x) for x in samples), 800)
        self.assertLessEqual(max(abs(x) for x in samples), 2600)
        self.assertLessEqual(max(abs(samples[0]), abs(samples[-1])), 60)

    def test_loose_hangup_mentions_and_farewells(self):
        for text in (' Mhm. Das- dann kannst du jetzt auflegen. Danke', 'Okay, das wars, leg bitte auf', 'Gut, tschüss dann', 'Baba',
                     'Danke, das wärs. Schönen Abend noch'):
            self.assertTrue(mentions_hangup(text), text)
        for text in ('Bitte nicht auflegen.', 'Wie kann ich auflegen?', 'Schreib den Text: Bitte leg auf.', 'Der Kunde hat aufgelegt',
                     'Servus, wie ist der Stand?', 'x'*300+' auflegen', 'Moment, bevor du auflegst: eine Sache',
                     'Du kannst gleich auflegen, aber vorher eine Sache', 'Leg Wert auf Tests', 'Lege das Ticket auf Eis'):
            self.assertFalse(mentions_hangup(text), text)
        for text in (' Mach ich. Tschau!', " Alles klar, mach's gut.", ' Alles klar, bis bald.', 'Auf Wiederhören, Felix!',
                     'Wiederhören!', 'Dir auch, schönes Wochenende!', 'Alles klar, ich lege jetzt auf.', 'Schönen Abend noch!'):
            self.assertTrue(is_farewell(text), text)
        for text in (' Servus Felix! Aktuell habe ich keinen Auftrag.', ' Klar, also weiter mit dem Bericht.', "Ciao Felix! Worum geht's?",
                     'Die Tests laufen bis dann durch.', 'Hallo Felix, schönen Abend! Worum geht es?', 'Ich warte bis später auf das Ergebnis.',
                     'Tschüss sagt man in Wien eher selten, meistens hört man Baba oder Servus im Alltag.'):
            self.assertFalse(is_farewell(text), text)

if __name__ == '__main__': unittest.main()
