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


TITLE = 'Gesprächskoordination-Threads automatisch setteln'
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
        self.assertEqual(parse_followup('Sende bitte etwas an den Thread'), {})
        self.assertEqual(parse_followup('Kannst du dem Thread Titel bitte sagen, er soll prüfen?'), {})
        self.assertEqual(parse_followup('Eine Nachricht an Thread Titel'), {})
        self.assertEqual(parse_followup('Nein, sende nichts an den Thread'), {})
        self.assertEqual(parse_followup('Sag dem Thread „Titel“, dass bitte geprüft werden soll'),
                         {'target': 'Titel', 'text': 'bitte geprüft werden soll'})

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
