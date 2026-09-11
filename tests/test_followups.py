import copy
import json
import tempfile
import threading
import unittest
from unittest.mock import Mock

from telegram_bridge.conversations import ConversationStore, Conversations
from telegram_bridge.control import GateError
from telegram_bridge.followups import Followups, parse_followup
from telegram_bridge.service import VoiceConversation


TITLE = 'Nachrichten an abgeschlossene T3-Threads senden'
TEXT = f'Sende an Thread „{TITLE}“: Bitte prüfe auch den Fehlerfall.'


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = {'id': 'thread-a', 'projectId': 'project-a', 'title': TITLE,
                       'runtimeMode': 'approval-required', 'interactionMode': 'default',
                       'latestTurn': {'state': 'completed'}, 'messages': [], 'activities': []}
        self.shell = {'projects': [{'id': 'project-a', 'title': 'Bridge'}], 'threads': [self.source]}
        self.client = Mock()
        self.client.request.side_effect = lambda _: copy.deepcopy(self.shell)
        self.client.snapshot.side_effect = lambda _: {'thread': copy.deepcopy(self.source)}
        def dispatch(command):
            m = command['message']
            self.source['messages'].append({'id': m['messageId'], 'role': 'user', 'text': m['text']})
            return {'sequence': 1}
        self.client.dispatch.side_effect = dispatch
        self.restart()

    def restart(self):
        self.store = ConversationStore(self.tmp.name, '*', 123)
        self.c = Conversations(self.store, self.client, Mock())
        self.f = Followups(self.c)

    def test_running_completed_and_settled_use_existing_thread(self):
        for state, settled in [('running', None), ('completed', None), ('completed', 'settled')]:
            with self.subTest(state=state, settled=settled):
                self.source.update(latestTurn={'state': state}, settledOverride=settled)
                result = self.f.handle(TEXT, state + str(settled))
                self.assertIn('angekommen', result)
                command = self.client.dispatch.call_args.args[0]
                self.assertEqual(command['type'], 'thread.turn.start')
                self.assertEqual(command['threadId'], 'thread-a')
                self.assertEqual(command['runtimeMode'], 'approval-required')
                self.assertEqual(command['message']['text'], 'Bitte prüfe auch den Fehlerfall.')
        self.assertEqual(self.client.dispatch.call_count, 3)
        self.assertFalse(self.c.data['operations'])
        self.assertFalse(self.c.data.get('tasks'))
        self.client.create_coordinator.assert_not_called()

    def test_duplicate_event_after_restart_does_not_dispatch_again(self):
        self.f.handle(TEXT, 'event')
        self.restart()
        self.assertIn('angekommen', self.f.handle(TEXT, 'event'))
        self.client.dispatch.assert_called_once()

    def test_uncertain_dispatch_and_new_attempt_do_not_duplicate(self):
        self.client.dispatch.side_effect = GateError('timeout')
        self.assertIn('unbestätigt', self.f.handle(TEXT, 'event'))
        self.restart()
        self.assertIn('unbestätigt', self.f.handle(TEXT, 'event'))
        self.assertIn('unbestätigt', self.f.handle(TEXT, 'another-event'))
        self.client.dispatch.assert_called_once()
        entry = next(iter(self.f.entries.values()))
        m = entry['command']['message']
        self.source['messages'].append({'id': m['messageId'], 'role': 'user', 'text': m['text']})
        self.assertIn('angekommen', self.f.handle(TEXT, 'event'))

    def test_readback_failure_is_not_success(self):
        def dispatch(_):
            self.client.snapshot.side_effect = GateError('offline')
        self.client.dispatch.side_effect = dispatch
        self.assertIn('unbestätigt', self.f.handle(TEXT, 'event'))

    def test_open_question_is_never_answered_by_followup(self):
        self.source['activities'] = [{'kind': 'user-input.requested', 'payload': {
            'requestId': 'ask-a', 'questions': [{'id': 'q', 'question': 'Welches Format?'}]}}]
        self.assertIn('offene Rückfrage', self.f.handle(TEXT, 'event'))
        self.client.dispatch.assert_not_called()
        self.client.return_answer.assert_not_called()

    def test_shell_pending_flag_fails_closed(self):
        self.shell['threads'] = [{**self.source, 'hasPendingUserInput': True}]
        self.assertIn('offene Rückfrage', self.f.handle(TEXT, 'event'))
        self.client.dispatch.assert_not_called()

    def test_ambiguous_title_requires_id_across_projects(self):
        self.shell['projects'].append({'id': 'project-b', 'title': 'Other'})
        self.shell['threads'].append({**self.source, 'id': 'thread-b', 'projectId': 'project-b'})
        self.assertIn('Mehrere Threads', self.f.handle(TEXT, 'event'))
        self.client.dispatch.assert_not_called()
        self.assertIn('angekommen', self.f.handle('Sende an Thread thread-a: Hallo', 'event-2'))

    def test_unknown_partial_archived_deleted_internal_and_out_of_scope_rejected(self):
        for changes in ({'title': TITLE + ' extra'}, {'archivedAt': 'now'}, {'deletedAt': 'now'},
                        {'projectId': 'absent'}, {'title': 'Telefonbrücke - Gesprächskoordination'}):
            with self.subTest(changes=changes):
                self.shell['threads'] = [{**self.source, **changes}]
                self.assertIn('nicht eindeutig', self.f.handle(TEXT, 'event'))
        self.client.dispatch.assert_not_called()

    def test_identity_change_between_shell_and_snapshot_rejected(self):
        for field in ('id', 'projectId', 'title'):
            with self.subTest(field=field):
                changed = {**self.source, field: 'different'}
                self.client.snapshot.side_effect = lambda _, source=changed: {'thread': source}
                with self.assertRaisesRegex(GateError, 'target_changed'):
                    self.f.handle(TEXT, 'event')
        self.client.dispatch.assert_not_called()

    def test_cancellation_before_dispatch(self):
        self.assertIn('nichts übertragen', self.f.handle(TEXT, 'event', cancelled=lambda: True))
        self.client.dispatch.assert_not_called()

    def test_event_cannot_be_rebound_to_other_message(self):
        self.f.handle(TEXT, 'event')
        with self.assertRaisesRegex(GateError, 'event_conflict'):
            self.f.handle(TEXT + ' Anders.', 'event')
        self.client.dispatch.assert_called_once()

    def test_parser_separates_confirmation_question_and_incomplete_address(self):
        for text in ('Ja, starte', 'PDF', 'Wie ist der Status?'):
            self.assertIsNone(parse_followup(text))
        self.assertIsNone(parse_followup(TITLE))
        self.assertEqual(parse_followup('Sende bitte etwas an den Thread'), {})
        self.assertEqual(parse_followup('Kannst du dem Thread Titel bitte sagen, er soll prüfen?'), {})
        self.assertEqual(parse_followup('Eine Nachricht an Thread Titel'), {})
        self.assertIsNone(parse_followup('Nein, sende nichts an den Thread'))
        self.assertEqual(parse_followup('Sag dem Thread „Titel“, dass bitte geprüft werden soll'),
                         {'target': 'Titel', 'text': 'bitte geprüft werden soll'})

    def prepare_context_voice(self):
        voice = VoiceConversation(self.c, threading.RLock())
        self.c.discover = Mock()
        self.c.open_operations = Mock(return_value=[])
        self.c.status_text = Mock(return_value='Status')
        self.c.coordinator_source = Mock(return_value=None)
        return voice

    def test_voice_sends_deictic_message_to_last_exactly_named_thread(self):
        voice = self.prepare_context_voice()
        transcript = [{'role': 'user', 'text': f'Ich meine den completed Thread „{TITLE}“.'}]
        self.assertEqual(voice(transcript), 'Status')
        transcript += [
            {'role': 'assistant', 'text': 'Alles klar.'},
            {'role': 'user', 'text': 'Schick dort Test hin.'},
        ]
        self.assertIn('angekommen', voice(transcript))
        command = self.client.dispatch.call_args.args[0]
        self.assertEqual(command['threadId'], 'thread-a')
        self.assertEqual(command['message']['text'], 'Test')

    def test_voice_context_accepts_exact_title_without_word_thread(self):
        self.source['title'] = 'Bericht prüfen'
        voice = self.prepare_context_voice()
        transcript = [{'role': 'user', 'text': 'Es geht um „Bericht prüfen“.'}]
        self.assertEqual(voice(transcript), 'Status')
        transcript += [
            {'role': 'assistant', 'text': 'Alles klar.'},
            {'role': 'user', 'text': 'Schick dort Test hin.'},
        ]
        self.assertIn('angekommen', voice(transcript))
        self.assertEqual(self.client.dispatch.call_args.args[0]['threadId'], 'thread-a')

    def test_voice_mach_das_uses_declared_message_and_exact_thread(self):
        voice = self.prepare_context_voice()
        transcript = [{'role': 'user', 'text': f'Beim Thread „{TITLE}“: Die Nachricht ist Test'}]
        self.assertIn('mach das', voice(transcript).casefold())
        self.client.dispatch.assert_not_called()
        transcript += [
            {'role': 'assistant', 'text': 'Sag einfach mach das.'},
            {'role': 'user', 'text': 'Mach das.'},
        ]
        self.assertIn('angekommen', voice(transcript))
        self.assertEqual(self.client.dispatch.call_args.args[0]['message']['text'], 'Test')

    def test_title_match_uses_word_boundaries(self):
        self.source['title'] = 'Test'
        voice = self.prepare_context_voice()
        transcript = [{'role': 'user', 'text': 'Wie läuft der Testbericht?'}]
        self.assertEqual(voice(transcript), 'Status')
        transcript += [
            {'role': 'assistant', 'text': 'Alles klar.'},
            {'role': 'user', 'text': 'Schick dort Hallo hin.'},
        ]
        self.assertIn('Welchen T3-Thread', voice(transcript))
        self.client.dispatch.assert_not_called()

    def test_message_text_cannot_replace_bound_target(self):
        self.shell['threads'].append({**self.source, 'id': 'thread-b', 'title': 'Test'})
        voice = self.prepare_context_voice()
        transcript = [
            {'role': 'user', 'text': f'Ich meine den Thread „{TITLE}“.'},
            {'role': 'assistant', 'text': 'Alles klar.'},
            {'role': 'user', 'text': 'Schick dort Test hin.'},
        ]
        self.assertIn('angekommen', voice(transcript))
        command = self.client.dispatch.call_args.args[0]
        self.assertEqual(command['threadId'], 'thread-a')
        self.assertEqual(command['message']['text'], 'Test')

    def test_topic_change_invalidates_mach_das_binding(self):
        voice = self.prepare_context_voice()
        transcript = [{'role': 'user', 'text': f'Beim Thread „{TITLE}“: Die Nachricht ist Test'}]
        self.assertIn('mach das', voice(transcript).casefold())
        transcript += [
            {'role': 'assistant', 'text': 'Sag einfach mach das.'},
            {'role': 'user', 'text': 'PDF'},
        ]
        self.assertEqual(voice(transcript), 'Status')
        transcript += [
            {'role': 'assistant', 'text': 'Alles klar.'},
            {'role': 'user', 'text': 'Mach das.'},
        ]
        self.assertNotIn('angekommen', voice(transcript))
        self.client.dispatch.assert_not_called()

    def test_incomplete_explicit_request_keeps_target_for_plain_answer(self):
        voice = self.prepare_context_voice()
        transcript = [{'role': 'user', 'text': f'Schick bitte an den Thread „{TITLE}“'}]
        self.assertIn('Welche Nachricht', voice(transcript))
        transcript += [
            {'role': 'assistant', 'text': 'Welche Nachricht soll ich senden?'},
            {'role': 'user', 'text': 'Test'},
        ]
        self.assertIn('angekommen', voice(transcript))
        self.assertEqual(self.client.dispatch.call_args.args[0]['message']['text'], 'Test')

    def test_contextual_payload_is_not_silently_rewritten(self):
        voice = self.prepare_context_voice()
        transcript = [
            {'role': 'user', 'text': f'Ich meine den Thread „{TITLE}“.'},
            {'role': 'assistant', 'text': 'Alles klar.'},
            {'role': 'user', 'text': 'Schick dort Bitte prüfen!'},
        ]
        self.assertIn('angekommen', voice(transcript))
        self.assertEqual(self.client.dispatch.call_args.args[0]['message']['text'], 'Bitte prüfen!')

    def test_declared_payload_keeps_leading_word_and_punctuation(self):
        voice = self.prepare_context_voice()
        transcript = [{
            'role': 'user',
            'text': f'Beim Thread „{TITLE}“: Die Nachricht lautet Bitte prüfen!',
        }]
        self.assertIn('mach das', voice(transcript).casefold())
        transcript += [
            {'role': 'assistant', 'text': 'Sag einfach mach das.'},
            {'role': 'user', 'text': 'Mach das.'},
        ]
        self.assertIn('angekommen', voice(transcript))
        self.assertEqual(self.client.dispatch.call_args.args[0]['message']['text'], 'Bitte prüfen!')

    def test_voice_context_requests_target_and_accepts_exact_title(self):
        voice = self.prepare_context_voice()
        transcript = [{'role': 'user', 'text': 'Schick dort Test hin.'}]
        self.assertIn('Welchen T3-Thread', voice(transcript))
        self.client.dispatch.assert_not_called()
        transcript += [
            {'role': 'assistant', 'text': 'Welchen T3-Thread meinst du?'},
            {'role': 'user', 'text': TITLE},
        ]
        self.assertIn('angekommen', voice(transcript))
        self.assertEqual(self.client.dispatch.call_args.args[0]['message']['text'], 'Test')

    def test_voice_context_asks_for_ambiguous_target_and_accepts_id(self):
        self.shell['projects'].append({'id': 'project-b', 'title': 'Other'})
        self.shell['threads'].append({**self.source, 'id': 'thread-b', 'projectId': 'project-b'})
        voice = self.prepare_context_voice()
        transcript = [{'role': 'user', 'text': f'Es geht um den Thread „{TITLE}“.'}]
        voice(transcript)
        transcript += [
            {'role': 'assistant', 'text': 'Welchen meinst du?'},
            {'role': 'user', 'text': 'Schick dort Test hin.'},
        ]
        result = voice(transcript)
        self.assertIn('Mehrere Threads', result)
        self.assertIn('thread-a', result)
        self.assertIn('thread-b', result)
        self.client.dispatch.assert_not_called()
        transcript += [
            {'role': 'assistant', 'text': result},
            {'role': 'user', 'text': 'thread-a'},
        ]
        self.assertIn('angekommen', voice(transcript))
        self.assertEqual(self.client.dispatch.call_args.args[0]['threadId'], 'thread-a')

    def test_voice_context_asks_for_message_and_accepts_plain_answer(self):
        voice = self.prepare_context_voice()
        transcript = [
            {'role': 'user', 'text': f'Ich meine den Thread „{TITLE}“.'},
            {'role': 'assistant', 'text': 'Alles klar.'},
            {'role': 'user', 'text': 'Mach das.'},
        ]
        self.assertIn('Welche Nachricht', voice(transcript))
        self.client.dispatch.assert_not_called()
        transcript += [
            {'role': 'assistant', 'text': 'Welche Nachricht soll ich senden?'},
            {'role': 'user', 'text': 'Test'},
        ]
        self.assertIn('angekommen', voice(transcript))
        self.assertEqual(self.client.dispatch.call_args.args[0]['message']['text'], 'Test')

    def test_voice_contextual_send_honors_revision_gate(self):
        voice = self.prepare_context_voice()
        voice.revision = lambda: 3
        transcript = [
            {'role': 'user', 'text': f'Der Thread heißt „{TITLE}“.'},
            {'role': 'assistant', 'text': 'Alles klar.'},
            {'role': 'user', 'text': 'Schreib dort Test.'},
        ]
        self.assertIn('nichts übertragen', voice(transcript, revision=2))
        self.client.dispatch.assert_not_called()

    def test_voice_routes_before_attached_ask_and_invalidates_old_proposal(self):
        launcher = Mock()
        voice = VoiceConversation(self.c, threading.RLock(), operation_id='old-ask', launcher=launcher)
        voice.proposal_id = 'old-proposal'
        voice.revision = lambda: 4
        transcript = [{'role': 'user', 'text': TEXT}]
        self.assertIn('angekommen', voice(transcript, revision=4))
        self.assertIsNone(voice.proposal_id)
        self.assertIsNone(voice.operation_id)
        self.assertIn('angekommen', voice(transcript, revision=4))
        self.client.dispatch.assert_called_once()
        launcher.confirm.assert_not_called()
        launcher.propose.assert_not_called()
        self.c.discover = Mock()
        self.c.open_operations = Mock(return_value=[])
        self.c.status_text = Mock(return_value='Status')
        self.c.coordinator_source = Mock(return_value=None)
        voice(transcript + [{'role': 'user', 'text': 'Ja'}], revision=4)
        launcher.confirm.assert_not_called()
        self.client.dispatch.assert_called_once()

    def test_voice_does_not_reuse_historical_send_or_confirmation(self):
        voice = VoiceConversation(self.c, threading.RLock())
        self.c.discover = Mock()
        self.c.open_operations = Mock(return_value=[])
        self.c.status_text = Mock(return_value='Status')
        self.c.coordinator_source = Mock(return_value=None)
        self.assertEqual(voice([{'role': 'user', 'text': TEXT}, {'role': 'user', 'text': 'Ja'}]), 'Status')
        self.client.dispatch.assert_not_called()

    def test_voice_stale_revision_does_not_dispatch(self):
        voice = VoiceConversation(self.c, threading.RLock())
        voice.revision = lambda: 2
        self.assertIn('nichts übertragen', voice([{'role': 'user', 'text': TEXT}], revision=1))
        self.client.dispatch.assert_not_called()

    def test_changed_voice_segment_cannot_be_sent_twice(self):
        voice = VoiceConversation(self.c, threading.RLock())
        self.assertIn('angekommen', voice([{'role': 'user', 'text': TEXT}]))
        self.assertIn('nicht verlässlich', voice([{'role': 'user', 'text': TEXT + ' Noch etwas.'}]))
        self.client.dispatch.assert_called_once()

    def test_reservation_failure_prevents_dispatch(self):
        self.c.store.save = Mock(side_effect=GateError('disk_full'))
        with self.assertRaisesRegex(GateError, 'disk_full'):
            self.f.handle(TEXT, 'event')
        self.client.dispatch.assert_not_called()

    def test_spoken_message_boundary(self):
        self.assertIn('angekommen', self.f.handle(
            f'Sende an Thread „{TITLE}“ mit der Nachricht Bitte prüfe den Fehlerfall.', 'event'))

    def test_telegram_duplicate_message_delivers_once(self):
        message = {'id': 99, 'chat_id': 123, 'date': self.c.data['activated_at'], 'is_outgoing': False,
                   'sender_id': {'@type': 'messageSenderUser', 'user_id': 123},
                   'content': {'@type': 'messageText', 'text': {'text': TEXT}}}
        self.c.message(message)
        self.restart()
        self.c.message(message)
        self.client.dispatch.assert_called_once()
