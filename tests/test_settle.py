"""Coordination threads settle in T3 once their phone conversation has fully ended."""
import unittest
from unittest.mock import Mock, patch

from telegram_bridge.call_history import CallHistory
from telegram_bridge.control import GateError
from telegram_bridge.service import TelegramService
from telegram_bridge.t3 import (T3Client, COORDINATOR_TITLE, settle_command_id, settle_after_call,
                                is_coordinator_thread)
from test_conversations import ConversationFixture, Client
from test_control import FixtureMedia


def coordinator(**fields):
    return {'id': 'coordinator', 'projectId': 'project', 'title': COORDINATOR_TITLE, 'archivedAt': None,
            'deletedAt': None, 'settledOverride': None, 'settledAt': None, 'session': {'status': 'stopped'},
            'latestTurn': {'turnId': 'turn', 'state': 'completed'}, 'messages': [], 'activities': [], **fields}


class SettleClientTests(unittest.TestCase):
    def make(self, thread):
        client = T3Client('http://localhost', 'PRIVATE')
        client.snapshot = Mock(return_value={'thread': thread})
        client.dispatch = Mock(return_value={'sequence': 1})
        return client

    def test_only_bridge_coordination_threads_can_be_settled(self):
        for title in ('Fixture task', 'Neue Suche', 'Telefonbrücke', ''):
            client = self.make(coordinator(title=title))
            with self.assertRaisesRegex(GateError, 'not_coordinator'):
                client.settle_coordinator('coordinator', 'phone-settle-x', wait=0)
            client.dispatch.assert_not_called()
        self.assertTrue(is_coordinator_thread({'title': COORDINATOR_TITLE + ' (Status)'}))
        self.assertFalse(is_coordinator_thread({'title': 'Aufgabe: ' + COORDINATOR_TITLE}))

    def test_states_that_t3_would_reject_are_skipped_without_a_command(self):
        cases = {
            'unavailable': [coordinator(archivedAt='2026-09-11T10:00:00Z'), coordinator(deletedAt='2026-09-11T10:00:00Z')],
            'already_settled': [coordinator(settledOverride='settled', settledAt='2026-09-11T10:00:00Z')],
            'busy': [coordinator(session={'status': 'running'}), coordinator(session={'status': 'starting'}),
                     coordinator(latestTurn={'turnId': 'turn', 'state': 'running'})],
            'blocked': [coordinator(activities=[{'kind': 'user-input.requested', 'payload': {'requestId': 'r1'}}]),
                        coordinator(activities=[{'kind': 'approval.requested', 'payload': {'requestId': 'a1'}}])],
        }
        for expected, threads in cases.items():
            for thread in threads:
                client = self.make(thread)
                self.assertEqual(client.settle_coordinator('coordinator', 'phone-settle-x', wait=0), expected)
                client.dispatch.assert_not_called()

    def test_settle_command_is_dispatched_and_confirmed_from_the_snapshot(self):
        thread = coordinator(activities=[{'kind': 'user-input.requested', 'payload': {'requestId': 'r1'}},
                                         {'kind': 'user-input.resolved', 'payload': {'requestId': 'r1'}}])
        client = self.make(thread)
        def dispatch(command):
            thread['settledOverride'] = 'settled'
            return {'sequence': 7}
        client.dispatch = Mock(side_effect=dispatch)
        self.assertEqual(client.settle_coordinator('coordinator', 'phone-settle-x', wait=0), 'settled')
        self.assertEqual(client.dispatch.call_args.args[0],
                         {'type': 'thread.settle', 'commandId': 'phone-settle-x', 'threadId': 'coordinator'})
        client = self.make(coordinator())
        self.assertEqual(client.settle_coordinator('coordinator', 'phone-settle-y', wait=0), 'unconfirmed')

    def test_command_ids_are_stable_per_attempt_and_fresh_per_retry(self):
        self.assertEqual(settle_command_id('c', 'call', 0), settle_command_id('c', 'call', 0))
        self.assertEqual(len({settle_command_id('c', 'call', i) for i in range(3)} | {settle_command_id('c', 'other', 0)}), 4)

    def test_single_call_runner_retries_bounded_and_never_fails_the_call(self):
        client = Mock()
        client.settle_coordinator.side_effect = ['busy', 'busy', 'settled']
        emitted = []
        self.assertEqual(settle_after_call(client, 'coordinator', conversation_id='call', emit=emitted.append, sleep=lambda _: None), 'settled')
        self.assertEqual(len({c.args[1] for c in client.settle_coordinator.call_args_list}), 3)
        self.assertEqual(emitted[-1]['t3_coordinator_settle'], 'settled')
        client.settle_coordinator.side_effect = GateError('t3_http_500')
        self.assertEqual(settle_after_call(client, 'coordinator', conversation_id='call', attempts=2, sleep=lambda _: None), 'error:t3_http_500')
        self.assertIsNone(settle_after_call(client, None, conversation_id='call'))


class SettlingClient(Client):
    def __init__(self):
        super().__init__()
        self.settled = []
        self.settle_results = []

    def settle_coordinator(self, thread_id, command_id):
        self.settled.append((thread_id, command_id))
        result = self.settle_results.pop(0) if self.settle_results else 'settled'
        if isinstance(result, Exception): raise result
        return result


class SettleQueueTests(ConversationFixture, unittest.TestCase):
    def setUp(self):
        with patch('test_conversations.Client', SettlingClient):
            super().setUp()

    def test_queue_is_idempotent_and_settles_once_between_calls(self):
        self.c.request_settle('coordinator', conversation_id='call-1', reason='remote_end')
        self.c.request_settle('coordinator', conversation_id='call-1', reason='remote_end')
        self.assertEqual(list(self.store.data['settle_queue']), ['coordinator'])
        self.c.settle_coordinators(current_time=1000)
        self.c.settle_coordinators(current_time=2000)
        self.assertEqual(len(self.client.settled), 1)
        self.assertEqual(self.store.data['settle_queue'], {})
        self.restart()
        self.assertEqual(self.store.data.get('settle_queue'), {})

    def test_busy_coordinator_is_retried_later_with_a_fresh_command_and_finally_abandoned(self):
        self.client.settle_results = ['busy', GateError('t3_http_500'), 'unconfirmed', 'settled']
        self.c.request_settle('coordinator', conversation_id='call-1', reason='remote_end')
        self.c.settle_coordinators(current_time=1790000000)
        self.c.settle_coordinators(current_time=1790000010)
        self.assertEqual(len(self.client.settled), 1)
        self.c.settle_coordinators(current_time=1790000040)
        self.c.settle_coordinators(current_time=1790000080)
        self.assertEqual(len(self.client.settled), 3)
        self.assertEqual(len({command for _, command in self.client.settled}), 3)
        self.assertIn('coordinator', self.store.data['settle_queue'])
        self.c.settle_coordinators(current_time=1790000120)
        self.assertEqual(self.store.data['settle_queue'], {})
        self.client.settle_results = ['blocked'] * 5
        self.c.request_settle('other', conversation_id='call-2', reason='remote_end')
        self.c.settle_coordinators(current_time=1790000000)
        self.c.settle_coordinators(current_time=1790000000 + 1800)
        self.assertEqual(self.store.data['settle_queue'], {})

    def test_wrong_target_is_dropped_without_retry(self):
        self.client.settle_results = [GateError('t3_settle_target_not_coordinator')]
        self.c.request_settle('t3-thread', conversation_id='call-1', reason='remote_end')
        self.c.settle_coordinators(current_time=1000)
        self.c.settle_coordinators(current_time=5000)
        self.assertEqual(len(self.client.settled), 1)
        self.assertEqual(self.store.data['settle_queue'], {})

    def test_every_attempt_end_queues_the_operation_coordinator_once(self):
        self.c.delegate(self.operation).coordinator_id = 'op-coordinator'
        self.c.delegate(self.operation).checkpoint()
        for phase, reason in (('ended', 'remote_end'), ('failed', 'media_failed'), ('interrupted', 'service_restart')):
            self.store.data.setdefault('settle_queue', {}).clear()
            self.c.reserve_attempt()
            self.c.finish_attempt(self.identifier, {'phase': phase, 'reason': reason})
            self.c.finish_attempt(self.identifier, {'phase': phase, 'reason': reason})
            entry = self.store.data['settle_queue']['op-coordinator']
            self.assertEqual((entry['reason'], entry['conversation_id']), (reason, self.operation['attempt']['id']))
            self.operation['attempt'] = None
        self.assertNotIn('t3-thread', self.store.data['settle_queue'])

    def test_restart_after_interrupted_call_settles_via_recovery(self):
        self.c.delegate(self.operation).coordinator_id = 'op-coordinator'
        self.c.delegate(self.operation).checkpoint()
        self.c.reserve_attempt()
        self.restart()
        self.c.recover()
        self.assertEqual(self.store.data['settle_queue']['op-coordinator']['reason'], 'service_restart')
        self.c.settle_coordinators(current_time=1e9)
        self.assertEqual([thread for thread, _ in self.client.settled], ['op-coordinator'])


class SettleServiceTests(ConversationFixture, unittest.TestCase):
    def setUp(self):
        with patch('test_conversations.Client', SettlingClient):
            super().setUp()
        self.td = Mock()
        self.td.receive.return_value = None
        self.clock = [10.]
        self.media = FixtureMedia()
        self.media.voice = Mock(input_revision=1)
        self.s = TelegramService(self.td, self.c, lambda delegate: self.media, clock=lambda: self.clock[0])
        self.s.next_scan = float('inf')
        self.s.max_calls = 0  # Scans never dial here; the tests start calls explicitly.
        self.s.scan()  # Startup recovery happens before any call, as in the real service loop.
        self.addCleanup(self.shutdown)

    def shutdown(self):
        if self.s.session: self.s.session.phase = 'ended'
        self.s.close()

    def drain(self):
        while self.s.jobs: self.s.tick()

    def start(self):
        self.c.delegate(self.operation).coordinator_id = 'op-coordinator'
        self.c.delegate(self.operation).checkpoint()
        identifier = self.c.reserve_attempt()
        self.s.start_call(identifier)
        self.s.voice.coordinator_id = 'status-coordinator'
        self.s.voice.remember()

    def test_regular_and_failed_call_ends_queue_both_coordinators_but_settle_only_between_calls(self):
        for phase, reason in (('ended', 'remote_end'), ('failed', 'media_failed')):
            self.start()
            self.s.session.phase, self.s.session.reason = phase, reason
            self.s.tick()
            self.drain()
            self.assertFalse(self.client.settled)  # Never during the call teardown itself, only between calls.
            queue = self.store.data['settle_queue']
            self.assertEqual(set(queue), {'status-coordinator', 'op-coordinator'})
            self.assertEqual({entry['reason'] for entry in queue.values()}, {reason})
            self.s.scan()
            self.assertEqual(sorted(thread for thread, _ in self.client.settled), ['op-coordinator', 'status-coordinator'])
            self.assertEqual(self.store.data['settle_queue'], {})
            self.client.settled.clear()
            self.operation['attempt'] = None
        self.assertTrue(all(call.get('coordinator_settle') == 'requested' for call in self.s.history.data['calls']))

    def test_service_exit_during_a_call_queues_durably_for_the_next_start(self):
        self.start()
        self.s.session.phase = 'ended'
        self.s.close()
        self.assertIn('status-coordinator', self.store.data['settle_queue'])
        self.assertFalse(self.client.settled)

    def test_crash_during_a_call_is_settled_after_restart_from_call_history(self):
        history = CallHistory(self.store.profile, background=False)
        history.begin('crashed-call', incoming=True)
        history.update('crashed-call', coordinator_id='status-coordinator')
        history.flush()
        self.s = TelegramService(self.td, self.c, lambda delegate: self.media, clock=lambda: self.clock[0])
        self.s.next_scan, self.s.max_calls = float('inf'), 0
        self.s.scan()
        self.assertEqual([thread for thread, _ in self.client.settled], ['status-coordinator'])
        self.s.scan()
        self.assertEqual(len(self.client.settled), 1)
        self.assertEqual(self.s.history.data['calls'][0]['state'], 'interrupted')

    def test_work_threads_are_never_settled(self):
        self.start()
        self.s.session.phase, self.s.session.reason = 'ended', 'remote_end'
        self.s.tick()
        self.drain()
        self.s.scan()
        self.assertNotIn('t3-thread', {thread for thread, _ in self.client.settled})
        self.assertNotIn('t3-thread', self.store.data.get('settle_queue', {}))
