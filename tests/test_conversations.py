import copy
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import json

from eotm.conversations import ConversationStore, Conversations
from eotm.control import GateError, CallSession
from eotm.descriptor import normalize_pcm_ready
from eotm.service import TelegramService, VoiceConversation, run_service, watcher_lease
from eotm.live import RequestPump
from test_control import FixtureMedia
from test_descriptor import fixture_ready
from test_t3_dialog import DialogClient, reply


class Client(DialogClient):
    def request(self, path):
        return {'projects': [{'id': self.data['thread']['projectId']}],
                'threads': [{**self.data['thread'], 'hasPendingUserInput': True}]}


class ConversationFixture:
    def setUp(self):
        clock_patch = patch('eotm.conversations.time.time', return_value=1790000000)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        os.chmod(self.temp.name, 0o700)
        self.client = Client()
        self.sent = []
        self.store = ConversationStore(self.temp.name, self.client.data['thread']['projectId'], 123)
        self.c = Conversations(self.store, self.client, self.sent.append)
        self.operation = self.c.discover(0)[0]
        self.identifier = self.operation['id']
        self.addCleanup(self.temp.cleanup)

    def direct(self, *responses):
        """Structurer fixture: the T3 coordination thread must stay unused."""
        queue, prompts = list(responses), []
        def structurer(prompt):
            prompts.append(prompt)
            return queue.pop(0)
        self.c.structurer = structurer
        self.client.create_coordinator = lambda _: self.fail('coordinator thread created')
        return prompts

    def message(self, text, mid=55, **fields):
        return {'id': mid, 'chat_id': 123, 'is_outgoing': False, 'date': int(time.time()),
                'sender_id': {'@type': 'messageSenderUser', 'user_id': 123},
                'content': {'@type': 'messageText', 'text': {'text': text}}, **fields}

    def restart(self):
        self.store = ConversationStore(self.temp.name, self.client.data['thread']['projectId'], 123)
        self.c = Conversations(self.store, self.client, self.sent.append)
        self.operation = self.store.data['operations'][self.identifier]


class ConversationTests(ConversationFixture, unittest.TestCase):
    def test_status_brief_is_compact_and_names_open_questions_without_private_history(self):
        brief = self.c.status_brief()
        self.assertIn('Fixture task', brief)
        self.assertIn('Welche Variante?', brief)
        self.assertIn(self.identifier, brief)
        self.assertNotIn('PRIVATE_HISTORY', brief)
        self.assertLessEqual(len(brief), 6000)

    def test_text_answer_and_text_status_use_the_structurer(self):
        self.direct(reply('decision', 'PDF', 'PDF'), '{"reply":"Der Bericht ist fertig."}')
        self.c.message(self.message('PDF'))
        self.assertEqual(self.operation['status'], 'completed')
        self.c.message(self.message('Wie ist der Status?', 56))
        self.assertIn('Der Bericht ist fertig.', self.sent[-1]['input_message_content']['text']['text'])
        self.assertNotIn('status_coordinator', self.store.data)
        self.assertEqual(self.client.prompts, [])

    def test_no_answer_reject_hangup_network_loss_each_get_one_message(self):
        self.c.reserve_attempt()
        for reason in ('startup_timeout', 'remote_end', 'caller_requested', 'media_failed', 'service_restart'):
            self.c.finish_attempt(self.identifier, {'phase': 'ended', 'reason': reason})
            self.restart()
        self.assertEqual(len(self.sent), 1)
        self.assertIn(self.identifier, self.sent[0]['input_message_content']['text']['text'])
        self.assertIsNone(self.c.reserve_attempt())

    def test_answer_during_setup_suppresses_first_message(self):
        self.c.reserve_attempt()
        self.client.responses = [reply('decision', 'PDF', 'PDF')]
        self.c.message(self.message('PDF'))
        self.c.finish_attempt(self.identifier, {'phase': 'ended'})
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(any(i['purpose'] == 'first' for i in self.store.data['outbox'].values()))
        self.assertEqual(self.operation['status'], 'completed')

    def test_external_t3_answer_suppresses_first_message(self):
        self.c.reserve_attempt()
        self.client.return_answer(self.c.delegate(self.operation).handoff, {'choice': 'PDF'})
        self.c.finish_attempt(self.identifier, {'phase': 'ended'})
        self.assertFalse(self.sent)
        self.assertEqual(self.operation['status'], 'stale')

    def test_multiturn_clarification_survives_restart(self):
        self.client.responses = [reply('clarification', 'Welcher Bericht?', 'Welcher Bericht?'), reply('decision', 'PDF', 'PDF')]
        self.c.message(self.message('Welcher Bericht?'))
        self.assertEqual(self.operation['status'], 'open')
        self.assertIn('Vorstand', self.sent[-1]['input_message_content']['text']['text'])
        self.restart()
        self.c.message(self.message('PDF', 56))
        self.assertEqual(len(self.client.ask_answers), 1)
        self.assertEqual(len(self.client.followups), 1)
        self.assertEqual(self.operation['status'], 'completed')
        self.assertIn('damit habe ich alles', self.sent[-1]['input_message_content']['text']['text'])

    def test_duplicate_update_and_uncertain_send_are_not_replayed(self):
        self.client.responses = [reply('decision', 'PDF', 'PDF')]
        message = self.message('PDF')
        self.c.message(message)
        self.restart()
        self.c.message(message)
        self.assertEqual(len(self.client.ask_answers), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(next(iter(self.store.data['outbox'].values()))['status'], 'reserved')

    def test_unconfirmed_t3_mutation_never_claims_completion_or_retries(self):
        self.client.responses = [reply('decision', 'PDF', 'PDF')]
        self.client.return_answer = Mock(side_effect=GateError('t3_answer_completion_unconfirmed'))
        self.c.message(self.message('PDF'))
        self.assertNotEqual(self.operation['status'], 'completed')
        self.restart()
        self.c.message(self.message('PDF', 56))
        self.assertEqual(self.client.return_answer.call_count, 1)
        self.assertIn('unbestätigt', self.sent[-1]['input_message_content']['text']['text'])

    def test_deferral_has_no_automatic_followup_or_redial(self):
        self.c.reserve_attempt()
        self.c.message(self.message('jetzt nicht'))
        self.c.finish_attempt(self.identifier, {'phase': 'ended'})
        self.restart()
        self.c.recover()
        self.assertIsNone(self.c.reserve_attempt())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.operation['status'], 'deferred')

    def test_foreign_group_outgoing_and_stale_messages_cannot_decide(self):
        for fields in ({'chat_id': -1}, {'sender_id': {'@type': 'messageSenderUser', 'user_id': 666}},
                       {'sender_id': {'@type': 'messageSenderChat', 'user_id': 123}}, {'is_outgoing': True}, {'date': 1}):
            self.c.message(self.message('PDF', **fields))
        self.assertFalse(self.sent)
        self.assertFalse(self.client.ask_answers)

    def test_multiple_operations_require_selection_and_reply_maps_final_message_id(self):
        other = copy.deepcopy(self.operation)
        other['id'] = 'abcdef123456'
        self.store.data['operations'][other['id']] = other
        self.c.message(self.message('PDF'))
        self.assertIn('Welchen Vorgang', self.sent[-1]['input_message_content']['text']['text'])
        self.c.reserve_attempt()
        self.c.finish_attempt(self.identifier, {'phase': 'ended'})
        sent = self.sent[-1]
        self.c.delivery({'@type': 'message', '@extra': sent['@extra'], 'id': -10, 'chat_id': 123,
                         'sending_state': {'@type': 'messageSendingStatePending'}})
        self.c.delivery({'@type': 'updateMessageSendSucceeded', 'old_message_id': -10,
                         'message': {'id': 100, 'chat_id': 123}})
        self.assertEqual(self.c.select('PDF', 100)['id'], self.identifier)
        self.assertIsNone(self.c.select(other['id'], 100))

    def test_linked_correction_creates_followup_and_edit_is_deduplicated(self):
        self.client.responses = [reply('decision', 'PDF', 'PDF'), reply('decision', 'CSV', 'CSV')]
        self.c.message(self.message('PDF'))
        self.c.delivery({'@type': 'message', '@extra': self.sent[-1]['@extra'], 'id': 100, 'chat_id': 123})
        correction = self.message('CSV', 56, reply_to={'chat_id': 123, 'message_id': 100})
        self.c.message(correction)
        self.c.message(correction)
        self.assertEqual(len(self.client.ask_answers), 1)
        self.assertEqual(len(self.client.followups), 1)
        self.assertIn('CSV', self.client.followups[0][1])

    def test_restart_after_call_reservation_sends_followup_without_redial(self):
        self.c.reserve_attempt()
        self.restart()
        self.c.recover()
        self.c.recover()
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.c.reserve_attempt())
        self.assertEqual(os.stat(self.store.profile / 'conversations.json').st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(GateError, 'scope_mismatch'):
            ConversationStore(self.temp.name, 'other-project', 123)

    def test_each_callback_has_one_followup_and_keeps_operation_identity(self):
        self.c.reserve_attempt()
        self.c.finish_attempt(self.identifier, {'phase': 'ended', 'call_id': 7})
        self.c.begin_incoming(self.operation, 8)
        self.c.begin_incoming(self.operation, 8)
        self.c.finish_attempt(self.identifier, {'phase': 'ended', 'call_id': 8})
        self.c.finish_attempt(self.identifier, {'phase': 'ended', 'call_id': 8})
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(len(self.operation['contact_history']), 1)
        self.assertTrue(all(self.identifier in m['input_message_content']['text']['text'] for m in self.sent))

    def test_watcher_attempt_import_prevents_another_call(self):
        self.store.data['operations'].clear()
        self.store.save()
        path = Path(self.temp.name) / 'watch-attempts.json'
        path.write_text(json.dumps({'t3-thread:request-1': {'status': 'open_after_ended'}}))
        path.chmod(0o600)
        self.c = Conversations(self.store, self.client, self.sent.append)
        operation = self.c.discover(0)[0]
        self.assertEqual(operation['attempt']['phase'], 'imported_watcher_attempt')
        self.assertIsNone(self.c.reserve_attempt())
        self.c.recover()
        self.assertEqual(len(self.sent), 1)

    def test_service_cannot_compete_with_a_waiting_watcher(self):
        with watcher_lease(Path(self.temp.name)):
            with self.assertRaisesRegex(GateError, 'existing_project_watcher'):
                with watcher_lease(Path(self.temp.name)): self.fail('second lease acquired')

    def test_late_reply_to_completed_question_cannot_answer_new_ask(self):
        self.client.responses = [reply('decision', 'PDF', 'PDF')]
        self.c.message(self.message('PDF'))
        self.c.delivery({'@type': 'message', '@extra': self.sent[-1]['@extra'], 'id': 100, 'chat_id': 123})
        ask = copy.deepcopy(self.client.data['thread']['activities'][0])
        ask['id'] = 'new-activity'
        ask['payload']['requestId'] = 'request-2'
        ask['createdAt'] = '2026-09-11T10:03:00Z'
        self.client.data['thread']['activities'].append(ask)
        self.c.message(self.message('Doch CSV', 56, reply_to={'chat_id': 123, 'message_id': 100}))
        self.assertEqual(len(self.client.ask_answers), 1)
        self.assertFalse(self.client.followups)
        self.assertIn('neue offene Rückfrage', self.sent[-1]['input_message_content']['text']['text'])

    def test_later_source_explanation_is_sent_once_without_another_user_message(self):
        self.client.responses = [reply('clarification', 'Welcher Bericht?', 'Welcher Bericht?')]
        self.c.message(self.message('Welcher Bericht?'))
        self.client.data['thread']['messages'].append({'id': 'later-explanation', 'role': 'assistant',
            'streaming': False, 'text': 'Die aktualisierte Erläuterung aus der Aufgabe.'})
        self.c.poll_dialogs()
        self.c.poll_dialogs()
        self.restart()
        self.c.poll_dialogs()
        self.assertEqual(len(self.sent), 2)
        self.assertIn('aktualisierte Erläuterung', self.sent[-1]['input_message_content']['text']['text'])

    def test_text_clarification_during_call_does_not_get_redundant_first_message(self):
        self.c.reserve_attempt()
        self.client.responses = [reply('clarification', 'Welcher Bericht?', 'Welcher Bericht?')]
        self.c.message(self.message('Welcher Bericht?'))
        self.c.finish_attempt(self.identifier, {'phase': 'ended'})
        self.restart()
        self.c.recover()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.operation['status'], 'open')


class ServiceTests(ConversationFixture, unittest.TestCase):
    # Reuse fixture setup, not the conversation test cases.
    def setUp(self):
        super().setUp()
        self.td = Mock()
        self.td.receive.return_value = None
        self.clock = [10.]
        self.media = FixtureMedia()
        self.media.voice = Mock(input_revision=1)
        self.s = TelegramService(self.td, self.c, lambda delegate: self.media, clock=lambda: self.clock[0])
        self.s.next_scan = float('inf')
        self.addCleanup(self.shutdown)

    def shutdown(self):
        if self.s.session:
            self.s.session.phase = 'ended'
        self.s.close()

    def drain(self):
        deadline = time.monotonic() + 2
        while self.s.jobs and time.monotonic() < deadline:
            self.s.tick()
            time.sleep(.001)
        self.s.tick()
        self.assertFalse(self.s.jobs)

    def incoming(self, user=123, cid=7, state='callStatePending'):
        return {'@type': 'updateCall', 'call': {'id': cid, 'user_id': user, 'is_outgoing': False,
                'is_video': False, 'state': {'@type': state}}}

    def test_incoming_owner_only_and_duplicate_updates(self):
        self.s.event(self.incoming(666, cid=8))
        self.s.tick()
        self.assertIsNone(self.s.session)
        self.s.event(self.incoming())
        self.s.tick()
        self.drain()
        self.s.event(self.incoming())
        self.assertEqual([x.args[0]['@type'] for x in self.td.send.call_args_list], ['acceptCall'])
        self.assertFalse(self.s.session.outgoing)

    def test_incoming_hangup_before_worker_finishes_is_not_accepted(self):
        self.s.event(self.incoming())
        self.s.tick()
        self.s.event(self.incoming(state='callStateDiscarded'))
        self.drain()
        self.assertFalse(self.td.send.called)

    def test_message_cancels_dialing_before_t3_worker(self):
        identifier = self.c.reserve_attempt()
        self.s.start_call(identifier)
        self.s.event({'@type': 'updateNewMessage', 'message': self.message('jetzt nicht')})
        self.assertTrue(self.s.session.stopping)
        self.drain()
        self.assertEqual(self.operation['status'], 'deferred')

    def test_message_invalidates_queued_outbound_start(self):
        identifier = self.c.reserve_attempt()
        generation = self.s.input_generation
        self.s.event({'@type': 'updateNewMessage', 'message': self.message('jetzt nicht')})
        self.s.begin_outgoing(identifier, generation)
        self.drain()
        self.assertIsNone(self.s.session)

    def test_callback_restores_text_context_and_new_call_can_request_status(self):
        self.c.message(self.message('jetzt nicht'))
        voice = VoiceConversation(self.c, self.s.lock)
        self.client.responses = ['{"reply":"Es geht um den Bericht. PDF oder CSV?","operation_id":"' + self.identifier + '"}',
                                 reply('decision', 'PDF', 'Ja PDF')]
        voice([{'role': 'user', 'text': 'Ich rufe wegen des Berichts zurück.'}])
        self.assertEqual(voice.operation_id, self.identifier)
        voice([{'role': 'user', 'text': 'Ja PDF'}])
        self.assertTrue(self.c.delegate(self.operation).completed)
        self.assertIn('action=defer', self.client.prompts[-1])
        self.client.responses = ['{"reply":"Der Bericht ist fertig.","operation_id":null}']
        fresh = VoiceConversation(self.c, self.s.lock)
        self.assertIn('fertig', fresh([{'role': 'user', 'text': 'Wie läuft es?'}]))
        self.assertEqual(len(self.client.ask_answers), 1)

    def test_voice_status_and_answer_use_the_structurer_without_coordinator_threads(self):
        prompts = self.direct('{"reply":"Es geht um den Bericht. PDF oder CSV?","operation_id":"' + self.identifier + '"}',
                              reply('decision', 'PDF', 'Ja PDF'))
        voice = VoiceConversation(self.c, self.s.lock)
        self.assertIn('PDF oder CSV', voice([{'role': 'user', 'text': 'Ich rufe wegen des Berichts zurück.'}]))
        self.assertEqual(voice.operation_id, self.identifier)
        voice([{'role': 'user', 'text': 'Ja PDF'}])
        self.assertTrue(self.c.delegate(self.operation).completed)
        self.assertEqual(self.client.ask_answers, [('request-1', {'choice': 'PDF'})])
        self.assertEqual(len(prompts), 2)
        self.assertEqual(self.client.prompts, [])
        self.assertIsNone(voice.coordinator_id)
        self.assertIsNone(self.c.delegate(self.operation).coordinator_id)

    def test_failing_structurer_falls_back_to_the_status_coordinator(self):
        tried = []
        def structurer(prompt):
            tried.append(prompt)
            raise GateError('structurer_http_500')
        self.c.structurer = structurer
        self.client.responses = ['{"reply":"Der Bericht ist fertig.","operation_id":null}']
        voice = VoiceConversation(self.c, self.s.lock)
        self.assertIn('fertig', voice([{'role': 'user', 'text': 'Wie läuft es?'}]))
        self.assertEqual(voice.coordinator_id, 'coordinator')
        self.assertEqual(len(tried), 1)

    def test_call_start_prefetches_status_into_the_voice_instructions(self):
        self.media.extend_instructions = Mock(return_value=True)
        self.s.event(self.incoming())
        self.s.tick()
        self.drain()
        self.assertIsNotNone(self.s.session)
        text = self.media.extend_instructions.call_args.args[0]
        self.assertIn('Fixture task', text)
        self.assertIn('Welche Variante?', text)

    def test_idle_scan_keeps_a_warm_status_that_is_in_place_before_an_incoming_call_is_accepted(self):
        order = []
        self.media.extend_instructions = Mock(side_effect=lambda text: order.append('context') or True)
        self.td.send.side_effect = lambda request: order.append(request['@type'])
        self.s.scan()
        self.s.event(self.incoming())
        self.s.tick()
        self.drain()
        self.assertEqual(order[:2], ['context', 'acceptCall'])
        self.media.extend_instructions.assert_called_once()
        text = self.media.extend_instructions.call_args.args[0]
        self.assertIn('Fixture task', text)
        self.assertIn('Welche Variante?', text)

    def test_outgoing_calls_load_a_fresh_status_that_includes_their_own_question(self):
        order = []
        self.media.extend_instructions = Mock(side_effect=lambda text: order.append('context') or True)
        self.td.send.side_effect = lambda request: order.append(request['@type'])
        identifier = self.s.scan()
        self.assertEqual(identifier, self.identifier)
        self.s.status_cache = (self.s.status_cache[0], 'VERALTET: Offene Rückfragen: keine')
        self.s.start_call(identifier)
        self.drain()
        self.assertEqual(order[0], 'createCall')
        self.media.extend_instructions.assert_called_once()
        text = self.media.extend_instructions.call_args.args[0]
        self.assertNotIn('VERALTET', text)
        self.assertIn(identifier, text)

    def test_a_finished_call_invalidates_the_warm_status(self):
        self.s.scan()
        self.assertIsNotNone(self.s.status_cache)
        self.s.event(self.incoming())
        self.s.tick()
        self.drain()
        self.s.session.phase, self.s.session.reason = 'ended', 'remote_end'
        self.s.tick()
        self.drain()
        self.assertIsNone(self.s.session)
        self.assertIsNone(self.s.status_cache)

    def test_outdated_warm_status_is_replaced_by_a_fresh_prefetch(self):
        self.media.extend_instructions = Mock(return_value=True)
        self.s.scan()
        self.s.status_cache = (self.s.status_cache[0], 'VERALTET')
        self.clock[0] += 1000
        self.s.event(self.incoming())
        self.s.tick()
        self.drain()
        text = self.media.extend_instructions.call_args.args[0]
        self.assertNotIn('VERALTET', text)
        self.assertIn('Fixture task', text)

    def test_broken_status_data_during_the_idle_scan_never_stops_the_service(self):
        events = []
        self.s.emit = events.append
        self.c.status_brief = Mock(side_effect=KeyError('title'))
        self.s.scan()
        self.assertIsNone(self.s.status_cache)
        self.assertIn({'voice_context_prefetch_failed': 'KeyError'}, events)

    def test_structuring_time_is_reported_separately_from_the_whole_backend_reply(self):
        events = []
        self.c.emit = events.append
        self.direct('{"reply":"Läuft.","operation_id":null}')
        VoiceConversation(self.c, self.s.lock)([{'role': 'user', 'text': 'Wie läuft es?'}])
        timing = next(e for e in events if 'structuring_seconds' in e)
        self.assertEqual(timing['path'], 'direct')
        self.assertGreaterEqual(timing['structuring_seconds'], 0)

    def test_failed_status_prefetch_never_fails_the_call(self):
        events = []
        self.s.emit = events.append
        self.media.extend_instructions = Mock(return_value=True)
        self.s.event(self.incoming())
        self.s.tick()
        self.client.request = Mock(side_effect=GateError('t3_connection_or_response_failed'))
        self.drain()
        self.assertIsNotNone(self.s.session)
        self.media.extend_instructions.assert_not_called()
        self.assertIn({'voice_context_prefetch_failed': 't3_connection_or_response_failed'}, events)
        self.assertIsNone(self.s.backend_error)

    def test_unexpected_t3_data_during_prefetch_never_reaches_the_service_loop(self):
        events = []
        self.s.emit = events.append
        self.media.extend_instructions = Mock(return_value=True)
        self.c.status_brief = Mock(side_effect=KeyError('title'))
        self.s.event(self.incoming())
        self.s.tick()
        self.drain()
        self.assertIsNotNone(self.s.session)
        self.media.extend_instructions.assert_not_called()
        self.assertIn({'voice_context_prefetch_failed': 'KeyError'}, events)

    def test_status_snapshots_are_reused_within_one_call_but_not_for_answers(self):
        self.direct('{"reply":"Läuft.","operation_id":null}', '{"reply":"Weiter so.","operation_id":null}')
        self.c.discover = Mock(return_value=[])
        self.c.open_operations = Mock(return_value=[])
        fetched = []
        original = self.client.snapshot
        self.client.snapshot = lambda identifier: (fetched.append(identifier), original(identifier))[1]
        voice = VoiceConversation(self.c, self.s.lock)
        voice([{'role': 'user', 'text': 'Wie läuft es?'}])
        first = len(fetched)
        voice([{'role': 'user', 'text': 'Wie läuft es?'}, {'role': 'assistant', 'text': 'Läuft.'}, {'role': 'user', 'text': 'Und sonst?'}])
        self.assertGreater(first, 0)
        self.assertEqual(len(fetched), first)

    def test_voice_rules_answer_from_context_and_delegate_only_actions(self):
        from eotm.service import voice_instructions
        from eotm.application import instructions_for_handoff
        for text in (voice_instructions(None, {'previous_calls': []}),
                     voice_instructions({'questions': []}, {'previous_calls': []}), instructions_for_handoff({'questions': []})):
            self.assertNotIn('Delegiere jede', text)
            self.assertNotIn('Delegiere die Einordnung jeder', text)
            self.assertNotIn('ausschließlich vom Backend', text)
            self.assertIn('Delegiere nur', text)
            self.assertIn('erst nach', text)

    def test_service_authorization_fails_before_any_runtime_access(self):
        with self.assertRaisesRegex(GateError, 'authorization'):
            run_service(None, None, 'project')

    def test_edit_fetch_is_scoped_and_verified_before_processing(self):
        self.s.event({'@type': 'updateMessageContent', 'chat_id': 999, 'message_id': 1})
        self.assertFalse(self.td.send.called)
        self.s.event({'@type': 'updateMessageContent', 'chat_id': 123, 'message_id': 55})
        self.assertEqual(self.td.send.call_args.args[0]['@type'], 'getMessage')
        self.s.event({'@type': 'message', '@extra': 'edited-conversation-message', **self.message('jetzt nicht')})
        self.drain()
        self.assertEqual(self.operation['status'], 'deferred')

    def test_telegram_pump_can_preserve_startup_messages_during_identity_lookup(self):
        buffered = []
        class TD:
            def send(inner, request):
                inner.events = [{'@type': 'updateNewMessage', 'message': {'id': 1}},
                                {'@type': 'user', '@extra': request['@extra'], 'id': 123}]
            def receive(inner, _): return inner.events.pop(0)
        pump = RequestPump(TD())
        pump.on_event = buffered.append
        self.assertEqual(pump.request('getMe')['id'], 123)
        self.assertEqual(buffered[0]['@type'], 'updateNewMessage')


class IncomingMediaTests(unittest.TestCase):
    def test_incoming_direction_reaches_native_descriptor_and_media_starts_once(self):
        ready = fixture_ready()
        media = FixtureMedia()
        seen = []
        media.start = lambda state, *args: seen.append(normalize_pcm_ready(state))
        sent = []
        session = CallSession(sent.append, media)
        call = {'id': 7, 'user_id': 123, 'is_outgoing': False, 'is_video': False,
                'state': {'@type': 'callStatePending'}}
        session.accept(call, 123, authorized=True, consent=True)
        session.handle({'@type': 'updateCall', 'call': {**call, 'state': ready}})
        session.handle({'@type': 'updateCall', 'call': {**call, 'state': ready}})
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0]['outgoing'], False)
        self.assertNotIn('key_hex', session.status())

    def test_foreign_or_video_accept_gate_sends_nothing(self):
        sent = []
        for fields in ({'user_id': 999}, {'is_video': True}, {'is_outgoing': True}):
            session = CallSession(sent.append, FixtureMedia())
            call = {'id': 7, 'user_id': 123, 'is_outgoing': False, 'is_video': False,
                    'state': {'@type': 'callStatePending'}, **fields}
            with self.assertRaises(GateError): session.accept(call, 123, authorized=True, consent=True)
        self.assertFalse(sent)
