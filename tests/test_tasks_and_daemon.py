import copy
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from telegram_bridge.conversations import ConversationStore, Conversations
from telegram_bridge.control import GateError
from telegram_bridge.providers import ProviderAdvisor, remaining_capacity
from telegram_bridge.tasks import TaskLauncher, explicit_confirmation
from telegram_bridge.daemon import prepare_install
from telegram_bridge.service import VoiceConversation, TelegramService
from test_conversations import ConversationFixture
from test_control import FixtureMedia


def provider(identifier='codex-1', remaining=60):
    return {'instanceId': identifier, 'driver': 'codex', 'displayName': identifier, 'enabled': True,
            'installed': True, 'status': 'ready', 'auth': {'status': 'authenticated'},
            'models': [{'slug': 'gpt-5.6-sol', 'name': 'GPT-5.6-Sol', 'capabilities': {'optionDescriptors': [
                {'id': 'reasoningEffort', 'type': 'select', 'options': [{'id': 'low'}, {'id': 'high', 'isDefault': True}]},
                {'id': 'serviceTier', 'type': 'select', 'options': [{'id': 'default', 'isDefault': True}, {'id': 'priority'}]}]}}],
            'usageLimits': limits(remaining)}


def limits(remaining=60):
    return {'checkedAt': datetime.now(timezone.utc).isoformat(), 'windows': [
        {'usedPercent': 100-remaining, 'resetsAt': (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}]}


class MultiProjectTests(ConversationFixture, unittest.TestCase):
    def test_coordinator_skips_stale_workspace_and_prefers_existing_repo(self):
        self.c.data['project_id'] = '*'
        root = Path(self.temp.name)/'valid'; root.mkdir(); (root/'.git').mkdir()
        shell = {'projects': [{'id':'missing','workspaceRoot':str(root/'absent')},
                              {'id':'valid','workspaceRoot':str(root)}],
                 'threads': [{'id':'stale-thread','projectId':'missing'}, {'id':'good-thread','projectId':'valid'}]}
        self.assertEqual(self.c.coordinator_source(shell)['id'], 'good-thread')
        (root/'.git').rmdir()
        self.assertIsNone(self.c.coordinator_source(shell))

    def test_scope_expansion_preserves_history_and_pinned_personal_identity(self):
        self.c.reserve_attempt()
        with self.assertRaises(GateError): ConversationStore(self.temp.name, '*', 123)
        store = ConversationStore(self.temp.name, '*', 123, expand_scope=True)
        self.assertIn(self.identifier, store.data['operations'])
        self.assertEqual(store.data['previous_project_id'], 'project')
        with self.assertRaises(GateError): ConversationStore(self.temp.name, '*', 999, expand_scope=True)
        with self.assertRaises(GateError): ConversationStore(self.temp.name, 'another', 123)

    def test_new_projects_and_threads_are_discovered_and_account_call_spacing_is_global(self):
        self.store.data['project_id'] = '*'
        first = copy.deepcopy(self.client.data)
        second = copy.deepcopy(first)
        second['thread'].update(id='peakshare-thread', projectId='peakshare', title='Feature')
        snapshots = {'t3-thread': first, 'peakshare-thread': second}
        self.client.snapshot = lambda identifier: copy.deepcopy(snapshots[identifier])
        shell = {'projects': [{'id': 'project', 'title': 'Bridge'}],
                 'threads': [{**first['thread'], 'hasPendingUserInput': True}]}
        self.client.request = lambda _: shell
        self.c.discover(0)
        self.assertEqual(len(self.c.data['operations']), 1)
        shell['projects'].append({'id': 'peakshare', 'title': 'PeakShare'})
        shell['threads'].append({**second['thread'], 'hasPendingUserInput': True})
        self.c.discover(0)
        self.assertEqual(len(self.c.data['operations']), 2)
        self.assertIsNotNone(self.c.reserve_attempt(0, min_interval=180))
        self.assertIsNone(self.c.reserve_attempt(0, min_interval=180))
        with patch('telegram_bridge.conversations.time.time', return_value=1790000181):
            self.assertIsNotNone(self.c.reserve_attempt(0, min_interval=180))


class AdvisorTests(unittest.TestCase):
    def make_advisor(self):
        config = {'providers': [provider('codex-1'), provider('codex-2')], 'settings': {'providerInstances': {
            'codex-1': {'config': {'homePath': '/first'}}, 'codex-2': {'config': {'shadowHomePath': '/second'}}}}}
        client = Mock(); client.rpc.return_value = config
        reader = Mock(side_effect=lambda cfg: {**limits(15 if cfg.get('homePath') else 75), 'account_group': 'first' if cfg.get('homePath') else 'second'})
        return ProviderAdvisor(client, reader), client, reader

    def test_both_codex_accounts_have_independent_usage_reads(self):
        advisor, _, reader = self.make_advisor()
        result = advisor.refresh()
        self.assertEqual([p['remaining_percent'] for p in result], [15, 75])
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(advisor.coordinator_selection()['instanceId'], 'codex-2')

    def test_unknown_stale_and_exhausted_limits_are_not_invented(self):
        self.assertIsNone(remaining_capacity({}))
        old = limits(); old['checkedAt'] = '2000-01-01T00:00:00Z'
        self.assertIsNone(remaining_capacity(old))
        self.assertEqual(remaining_capacity(limits(0)), 0)
        advisor, _, reader = self.make_advisor()
        reader.side_effect = OSError('PRIVATE')
        self.assertTrue(all(p['remaining_percent'] is None for p in advisor.refresh()))
        self.assertIn('nicht verlässlich', advisor.describe({'instanceId': 'codex-1', 'model': 'gpt-5.6-sol'}))

    def test_only_catalog_options_allowed_and_standard_tier_is_explicit(self):
        advisor, _, _ = self.make_advisor()
        selection = {'instanceId': 'codex-2', 'model': 'gpt-5.6-sol', 'options': [{'id': 'reasoningEffort', 'value': 'high'}]}
        normalized = advisor.normalize(selection)
        self.assertIn({'id': 'serviceTier', 'value': 'default'}, normalized['options'])
        for change in ({'model': 'made-up'}, {'instanceId': 'foreign'}, {'options': [{'id': 'reasoningEffort', 'value': 'ultra'}]}):
            with self.assertRaises(GateError): advisor.validate({**selection, **change})


class TaskTests(ConversationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.advisor, _, _ = AdvisorTests().make_advisor()
        self.launcher = TaskLauncher(self.c, self.advisor)
        self.source_snapshot = self.client.snapshot
        self.created = {}
        self.commands = []
        def snapshot(identifier):
            if identifier in self.created: return {'thread': copy.deepcopy(self.created[identifier])}
            if identifier == 't3-thread': return self.source_snapshot(identifier)
            raise GateError('t3_http_404')
        self.client.snapshot = snapshot
        def dispatch(command):
            self.commands.append(copy.deepcopy(command))
            if command['type'] == 'thread.create':
                self.created[command['threadId']] = {'id': command['threadId'], 'projectId': command['projectId'], 'messages': []}
            else:
                self.created[command['threadId']]['messages'].append({'id': command['message']['messageId']})
                self.created[command['threadId']]['latestTurn'] = {'state': 'running'}
            return {'sequence': len(self.commands)}
        self.client.dispatch = dispatch
        original = self.client.request
        def request(path):
            result = original(path)
            result['projects'][0]['title'] = 'Bridge'
            return result
        self.client.request = request
        self.spec = {'project_id': 'project', 'title': 'Neue Suche', 'prompt': 'Implementiere eine Suche.',
                     'request_quote': 'Baue eine Suche', 'complexity': 'medium', 'reason': 'Überschaubares Feature.',
                     'modelSelection': {'instanceId': 'codex-2', 'model': 'gpt-5.6-sol', 'options': [{'id': 'reasoningEffort', 'value': 'high'}]}}
        self.transcript = [{'role': 'user', 'text': 'Baue eine Suche im Projekt Bridge.'}]

    def propose(self):
        return self.launcher.propose(self.spec, self.transcript, conversation_id='test-call', revision=1)[0]

    def test_explicit_confirmed_order_starts_one_correct_thread_with_proposed_model(self):
        job = self.propose()
        self.assertFalse(self.commands)
        transcript = self.transcript + [{'role': 'user', 'text': 'Ja, starte'}]
        result = self.launcher.confirm(job['id'], transcript, revision=2)
        self.assertIn('gestartet', result)
        self.launcher.confirm(job['id'], transcript, revision=2)
        self.launcher.recover(); self.launcher.recover()
        self.assertEqual([c['type'] for c in self.commands], ['thread.create', 'thread.turn.start'])
        self.assertEqual(self.commands[0]['modelSelection']['instanceId'], 'codex-2')
        self.assertEqual(self.commands[0]['projectId'], 'project')
        self.assertIsNone(self.commands[0]['worktreePath'])
        self.assertEqual(len(self.sent), 1)

    def test_no_confirmation_wrong_project_and_new_speech_prevent_start(self):
        job = self.propose()
        for text in ('Nein', 'Ja, aber in PeakShare', 'Kannst du das?', 'Nicht starten'):
            with self.assertRaises(GateError): self.launcher.confirm(job['id'], [{'role': 'user', 'text': text}], revision=2)
        with self.assertRaises(GateError): self.launcher.confirm(job['id'], [{'role': 'user', 'text': 'Ja'}], revision=2, cancelled=lambda: True)
        with self.assertRaises(GateError): self.launcher.propose({**self.spec, 'project_id': 'missing'}, self.transcript, conversation_id='x', revision=1)
        self.assertFalse(self.commands)

    def test_ambiguous_create_timeout_reconciles_same_thread_without_duplicate(self):
        job = self.propose()
        dispatch = self.client.dispatch
        def timeout_after_accept(command):
            dispatch(command)
            if command['type'] == 'thread.create': raise GateError('t3_connection_or_response_failed')
        self.client.dispatch = timeout_after_accept
        result = self.launcher.confirm(job['id'], [{'role': 'user', 'text': 'Ja'}], revision=2)
        self.assertIn('gestartet', result)
        self.assertEqual(len(self.commands), 2)

    def test_start_timeout_without_confirmation_is_not_retried(self):
        job = self.propose()
        dispatch = self.client.dispatch
        def timeout(command):
            if command['type'] == 'thread.turn.start': raise GateError('t3_connection_or_response_failed')
            return dispatch(command)
        self.client.dispatch = Mock(side_effect=timeout)
        self.launcher.confirm(job['id'], [{'role': 'user', 'text': 'Ja'}], revision=2)
        self.launcher.recover(); self.launcher.recover()
        self.assertEqual(self.client.dispatch.call_count, 2)
        self.assertEqual(job['state'], 'starting')

    def test_voice_proposal_then_confirmation_uses_same_durable_job(self):
        voice = VoiceConversation(self.c, threading.RLock(), launcher=self.launcher)
        voice.revision = lambda: 1
        self.client.responses = [json.dumps({'reply': 'Vorschlag', 'operation_id': None, 'new_task': self.spec})]
        result = voice(self.transcript, revision=1)
        self.assertIn('Codex', result.replace('codex','Codex'))
        self.assertIn('Reasoning high', result)
        self.assertFalse(self.commands)
        voice.revision = lambda: 2
        result = voice(self.transcript+[{'role':'user','text':'Ja, starte'}], revision=2)
        self.assertIn('gestartet', result)


class LifecycleTests(ConversationFixture, unittest.TestCase):
    def test_call_id_reuse_after_restart_is_not_mistaken_for_old_update(self):
        self.c.data['calls']['7'] = {'incoming': True, 'operation_id': None}
        td = Mock(); media = FixtureMedia(); media.voice = Mock(input_revision=0)
        service = TelegramService(td, self.c, lambda _: media)
        try:
            service.event({'@type':'updateCall','call':{'id':7,'user_id':123,'is_outgoing':False,'is_video':False,'state':{'@type':'callStatePending'}}})
            self.assertIsNotNone(service.pending_incoming)
            self.assertNotEqual(service.call_key(7), '7')
        finally: service.close()

    def test_shutdown_drains_newly_queued_message_and_records_receipt(self):
        td = Mock(); service = TelegramService(td, self.c, lambda _: FixtureMedia())
        self.c.queue_text('Bestätigung')
        def send(request): td.receive.return_value = {'@type':'message','@extra':request['@extra'],'id':100,'chat_id':123}
        td.send.side_effect = send
        service.close(drain_messages=True)
        self.assertEqual(next(iter(self.c.data['outbox'].values()))['status'], 'sent')
        td.send.assert_called_once()

    def test_release_install_has_fixed_code_no_secrets_and_no_automatic_launch(self):
        root = Path(self.temp.name)/'source'; (root/'telegram_bridge').mkdir(parents=True)
        (root/'telegram_bridge/__init__.py').write_text('')
        (root/'secret.key').write_text('PRIVATE')
        for name in ('python','libtdjson.dylib','media_runtime'): (root/name).write_text('fixture')
        staged, release = prepare_install(root, Path(self.temp.name)/'service', self.store.profile,
                                         root/'python', root/'libtdjson.dylib', root/'media_runtime')
        import plistlib
        config = plistlib.loads(staged.read_bytes())
        self.assertIn('--all-projects',config['ProgramArguments'])
        self.assertIn('--daemon',config['ProgramArguments'])
        self.assertIn('--allow-task-creation',config['ProgramArguments'])
        self.assertTrue(config['KeepAlive'])
        self.assertFalse((release/'secret.key').exists())
        (root/'telegram_bridge/__init__.py').write_text('changed')
        self.assertEqual((release/'telegram_bridge/__init__.py').read_text(),'')
