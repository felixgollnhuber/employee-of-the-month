import asyncio
import base64
import threading
import unittest
from unittest.mock import Mock, patch

from telegram_bridge.live_voice import LivePcmMedia, PcmOutputPacer, LiveVoice
from test_live_voice import FakeSocket


class StartupTests(unittest.TestCase):
    def make_media(self):
        native = Mock()
        voice = Mock(started=False)
        with patch('telegram_bridge.live_voice.NativePcmMedia', return_value=native), patch('telegram_bridge.live_voice.LiveVoice', return_value=voice):
            media = LivePcmMedia('sk-fixture', instructions='Test', authorized=True)
        return media, native, voice

    def test_greeting_and_wait_tone_reach_the_voice_session(self):
        with patch('telegram_bridge.live_voice.NativePcmMedia', return_value=Mock()):
            media = LivePcmMedia('sk-fixture', instructions='Test', authorized=True, greeting='Begrüße Felix jetzt.', wait_tone=True)
            plain = LivePcmMedia('sk-fixture', instructions='Test', authorized=True)
        self.assertEqual((media.voice.greeting, media.voice.wait_tone), ('Begrüße Felix jetzt.', True))
        self.assertEqual((plain.voice.greeting, plain.voice.wait_tone), (None, False))

    def test_service_calls_ask_for_a_greeting_and_the_wait_tone_can_be_switched_off(self):
        from telegram_bridge.application import GREETING
        from telegram_bridge.service import build_media
        delegate = Mock(operation_id=None)
        delegate.recent_context.return_value = {'previous_calls': []}
        with patch('telegram_bridge.live_voice.LivePcmMedia') as media:
            build_media({'api_key': 'sk-fixture'}, delegate, call_seconds=1200, emit=lambda _: None, native_executable=None)
            build_media({'api_key': 'sk-fixture', 'wait_tone': False}, delegate, call_seconds=1200, emit=lambda _: None, native_executable=None)
        first, second = media.call_args_list
        self.assertEqual((first.kwargs['greeting'], first.kwargs['wait_tone']), (GREETING, True))
        self.assertIs(second.kwargs['wait_tone'], False)
        self.assertIn('ohne auf seine erste Aussage zu warten', GREETING)
        self.assertIs(first.kwargs['delegate'], delegate)

    def test_single_call_runner_passes_greeting_and_wait_tone_to_the_voice(self):
        from contextlib import contextmanager
        from pathlib import Path
        from telegram_bridge import application
        @contextmanager
        def client(*args, **kwargs): yield Mock()
        loop = Mock(); loop.return_value.run.return_value = {'phase': 'ended'}
        with patch('telegram_bridge.live_voice.LivePcmMedia') as media, patch.object(application, 'read_profile'), \
             patch.object(application, 'require_target', return_value='@fixture'), \
             patch('telegram_bridge.config.read_private_json', return_value={'api_key': 'sk-fixture'}), \
             patch.object(application, 'existing_authenticated_client', client), patch.object(application, 'LiveCallLoop', loop), \
             patch('importlib.util.find_spec', return_value=object()):
            application.run_authorized_live_test(Path('/unused'), Path('/unused'), authorized=True, delegate=Mock(),
                                                 instructions='Fixture', greeting=application.GREETING)
            application.run_authorized_live_test(Path('/unused'), Path('/unused'), authorized=True)
        with_task, plain = media.call_args_list
        self.assertEqual((with_task.kwargs['greeting'], with_task.kwargs['wait_tone']), (application.GREETING, True))
        self.assertEqual((plain.kwargs['greeting'], plain.kwargs['wait_tone']), (None, False))

    def test_voice_waits_for_connection_and_never_blocks_signaling_thread(self):
        media, native, voice = self.make_media()
        events = []
        media.start({}, events.append, lambda _:None)
        state = native.start.call_args.args[1]
        voice.start.assert_not_called()
        media._from_phone(b'\0'*320)
        voice.push_audio.assert_not_called()
        state('connecting')
        voice.start.assert_not_called()
        state('connected')
        voice.start.assert_called_once_with(wait=False)
        state('connected')
        voice.start.assert_called_once()
        voice.started = True
        media._from_phone(b'\0'*320)
        voice.push_audio.assert_called_once()
        self.assertIn('connected', events)
        media.stop()
        state('connected')
        voice.start.assert_called_once()

    def test_early_hangup_does_not_open_api_on_late_connection(self):
        media, native, voice = self.make_media()
        media.start({}, lambda _:None, lambda _:None)
        state = native.start.call_args.args[1]
        media.stop()
        state('connected')
        voice.start.assert_not_called()

    def test_api_burst_is_paced_and_cancel_is_immediate(self):
        clock = [0.]
        packets = []
        stopped = [False]
        def wait(delay):
            clock[0] += delay
            return stopped[0]
        pacer = PcmOutputPacer(lambda data: packets.append((clock[0], len(data))), wait, clock=lambda:clock[0])
        pacer.push(b'\0'*64000)
        self.assertEqual(len(packets), 20)
        self.assertAlmostEqual(packets[-1][0], 1.9)
        self.assertTrue(all(n == 3200 for _,n in packets))
        stopped[0] = True
        pacer.push(b'\0'*64000)
        self.assertEqual(len(packets), 20)

    def test_slow_playback_does_not_block_input_transcript_or_close_receipt(self):
        release = threading.Event()
        entered = threading.Event()
        class Socket(FakeSocket):
            async def send(self, raw):
                await super().send(raw)
                if json_type(raw) == 'session.start':
                    await self.events.put({'type':'session.output_audio.delta','delta':base64.b64encode(b'\0'*3200).decode()})
                    await self.events.put({'type':'session.input_transcript.delta','delta':'Hallo'})
        socket = Socket()
        def output(_):
            entered.set(); release.wait(2)
        voice = LiveVoice('sk-fixture', instructions='Test', audio_out=output, connector=lambda *a,**k:socket)
        try:
            voice.start()
            self.assertTrue(entered.wait(1))
            # Wait on the event loop without releasing the blocked audio consumer.
            import time
            until = time.monotonic()+1
            while not voice.transcript and time.monotonic()<until: time.sleep(.005)
            self.assertEqual(voice.transcript[0]['text'],'Hallo')
        finally:
            release.set(); voice.close()
        self.assertTrue(voice.close_confirmed)


def json_type(raw):
    import json
    return json.loads(raw)['type']
