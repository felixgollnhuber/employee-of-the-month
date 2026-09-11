"""Durable conversation state. Only the service's serialized worker uses this module."""
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import time
import hashlib
from datetime import datetime

from .config import private_directory, read_private_json
from .control import GateError
from .handoff import pending_requests, _pending
from .t3 import T3Delegation, selected_context, is_coordinator_thread, settle_command_id, SETTLE_TERMINAL
from .watch import question_due


class ConversationStore:
    def __init__(self, profile, project_id, target_id, *, expand_scope=False):
        self.profile = Path(profile)
        private_directory(self.profile)
        path = self.profile / 'conversations.json'
        self.data = read_private_json(self.profile, path.name, max_bytes=8*1024*1024) if path.exists() or path.is_symlink() else {
            'version': 1, 'project_id': project_id, 'target_id': target_id, 'activated_at': int(time.time()),
            'operations': {}, 'updates': {}, 'outbox': {}, 'calls': {}}
        if (expand_scope and project_id == '*' and self.data.get('target_id') == target_id
                and self.data.get('version') == 1 and self.data.get('project_id') != '*'):
            self.data['previous_project_id'] = self.data['project_id']
            self.data['project_id'] = '*'
        if (self.data.get('version') != 1 or self.data.get('project_id') != project_id
                or self.data.get('target_id') != target_id):
            raise GateError('conversation_store_scope_mismatch')
        self.save()

    def save(self):
        raw = json.dumps(self.data, ensure_ascii=False)
        if len(raw.encode()) > 8*1024*1024:
            raise GateError('conversation_store_maintenance_required')
        fd, name = tempfile.mkstemp(prefix='.conversations-', dir=self.profile)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.profile / 'conversations.json')
            directory = os.open(self.profile, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)


class Conversations:
    def __init__(self, store, client, send, *, emit=lambda value: None):
        self.store, self.client, self.send, self.emit = store, client, send, emit
        self.data = store.data
        self.delegates = {}
        legacy = store.profile / 'watch-attempts.json'
        self.legacy_attempts = read_private_json(store.profile, legacy.name, max_bytes=262144) if legacy.exists() or legacy.is_symlink() else {}
        if not isinstance(self.legacy_attempts, dict): raise GateError('invalid_watcher_state')

    def in_scope(self, project_id):
        return bool(project_id) and (self.data['project_id'] == '*' or project_id == self.data['project_id'])

    def projects(self, shell=None):
        shell = shell or self.client.request('/api/orchestration/shell')
        return [p for p in shell.get('projects', []) if self.in_scope(p.get('id')) and not p.get('deletedAt')]

    def is_internal(self, thread):
        return is_coordinator_thread(thread)

    SETTLE_RETRY_SECONDS = 30
    SETTLE_GIVE_UP_SECONDS = 1800

    def request_settle(self, coordinator_id, *, conversation_id, reason):
        """Durably note that the phone conversation behind a coordination thread has fully ended.

        Idempotent per coordinator: a later call on the same coordinator refreshes the entry.
        The actual T3 command runs from settle_coordinators, never during a live call."""
        if not isinstance(coordinator_id, str) or not coordinator_id: return
        queue = self.data.setdefault('settle_queue', {})
        previous = queue.get(coordinator_id) or {}
        queue[coordinator_id] = {'conversation_id': conversation_id, 'reason': reason,
                                 'requested_at': time.time(), 'attempts': 0, 'next_attempt_at': 0,
                                 'history': previous.get('history', [])[-4:]}
        self.store.save()

    def settle_coordinators(self, current_time=None):
        """Settle queued coordination threads in T3. Runs only between calls on the worker."""
        queue = self.data.get('settle_queue') or {}
        current_time = time.time() if current_time is None else current_time
        for coordinator_id, entry in list(queue.items()):
            if entry['next_attempt_at'] > current_time: continue
            command_id = settle_command_id(coordinator_id, entry['conversation_id'], entry['attempts'])
            try: result = self.client.settle_coordinator(coordinator_id, command_id)
            except GateError as error: result = 'error:' + str(error)
            entry['attempts'] += 1
            entry['history'] = (entry.get('history') or [])[-4:] + [result]
            done = result in SETTLE_TERMINAL or result == 'error:t3_settle_target_not_coordinator'
            if not done and current_time - entry['requested_at'] >= self.SETTLE_GIVE_UP_SECONDS:
                done = True
                self.emit({'coordinator_settle_abandoned': True, 'coordinator_id': coordinator_id, 'last_result': result})
            if done:
                del queue[coordinator_id]
                self.emit({'coordinator_settled': result in SETTLE_TERMINAL, 'coordinator_id': coordinator_id,
                           'result': result, 'reason': entry['reason']})
            else: entry['next_attempt_at'] = current_time + self.SETTLE_RETRY_SECONDS
            self.store.save()

    def settle_operation_coordinator(self, operation, reason):
        """The call attempt for this operation is over; its coordination thread may settle."""
        attempt = operation.get('attempt')
        if not attempt or attempt.get('coordinator_settle_requested'): return
        coordinator_id = self.delegate(operation).coordinator_id
        attempt['coordinator_settle_requested'] = True
        self.store.save()
        if coordinator_id:
            self.request_settle(coordinator_id, conversation_id=attempt['id'], reason=reason)

    def project_title(self, operation):
        return operation.get('project_title') or operation['dialog']['project_id']

    def coordinator_source(self, shell):
        projects = {p['id']: p for p in self.projects(shell)}
        preferred = self.data.get('previous_project_id', self.data['project_id'])
        candidates = [t for t in shell.get('threads', []) if t.get('projectId') in projects
                      and not t.get('archivedAt') and not t.get('deletedAt') and not self.is_internal(t)]
        candidates.sort(key=lambda t: (t['projectId'] == preferred, t.get('updatedAt', '')), reverse=True)
        for thread in candidates:
            project = projects[thread['projectId']]
            root = project.get('workspaceRoot')
            if root:
                repository_root = (project.get('repositoryIdentity') or {}).get('rootPath', root)
                if not Path(root).is_dir() or not (Path(repository_root)/'.git').exists(): continue
            return thread
        return None

    def delegate(self, operation):
        identifier = operation['id']
        if identifier not in self.delegates:
            self.delegates[identifier] = T3Delegation.restore(self.client, operation['dialog'], emit=self.emit)
        dialog = self.delegates[identifier]
        def checkpoint():
            operation['dialog'] = dialog.state()
            operation['request_id'] = dialog.handoff.request_id
            self.store.save()
        dialog.checkpoint = checkpoint
        return dialog

    def discover(self, delay=180):
        shell = self.client.request('/api/orchestration/shell')
        projects = {p['id']: p for p in self.projects(shell)}
        for thread in shell.get('threads', []):
            if (thread.get('projectId') not in projects or thread.get('archivedAt') or self.is_internal(thread)
                    or thread.get('deletedAt') or not thread.get('hasPendingUserInput')):
                continue
            snapshot = self.client.snapshot(thread['id'])
            for operation in list(self.data['operations'].values()):
                if (operation['thread_id'] == thread['id'] and operation['status'] in ('open', 'deferred')
                        and operation['dialog']['submitted']):
                    self.refresh(operation)
            for request_id in pending_requests(snapshot):
                if any(o['thread_id'] == thread['id'] and request_id in o['dialog']['request_ids_in_call']
                       for o in self.data['operations'].values()):
                    continue
                if not question_due(snapshot, request_id, delay): continue
                dialog = T3Delegation(self.client, thread['id'], request_id, emit=self.emit)
                activity = _pending(snapshot, request_id)[1]
                try:
                    asked_at = int(datetime.fromisoformat(activity['createdAt'].replace('Z', '+00:00')).timestamp())
                except (KeyError, ValueError, TypeError): asked_at = int(time.time())
                identifier = uuid.uuid4().hex[:12]
                self.data['operations'][identifier] = {
                    'id': identifier, 'thread_id': thread['id'], 'request_id': request_id,
                    'project_id': thread['projectId'], 'project_title': projects[thread['projectId']].get('title', thread['projectId']),
                    'title': dialog.packet['thread_title'], 'dialog': dialog.state(),
                    'transcript': [], 'status': 'open', 'attempt': None, 'first_message': None,
                    'created_at': max(asked_at, self.data['activated_at'])}
                if thread['id'] + ':' + request_id in self.legacy_attempts:
                    self.data['operations'][identifier]['attempt'] = {
                        'id': uuid.uuid4().hex, 'phase': 'imported_watcher_attempt'}
                self.delegates[identifier] = dialog
                self.store.save()
        return self.open_operations()

    def refresh(self, operation):
        dialog = self.delegate(operation)
        snapshot = self.client.snapshot(operation['thread_id'])
        dialog._validate_source(snapshot)
        if operation['status'] == 'completed' and dialog.completed: return False
        if dialog.submitted:
            dialog._observe_source(snapshot)
        elif dialog.handoff.request_id not in pending_requests(snapshot):
            operation['status'] = 'stale'
        if dialog.completed: operation['status'] = 'completed'
        elif dialog.deferred: operation['status'] = 'deferred'
        elif operation['status'] == 'completed': operation['status'] = 'open'
        dialog.checkpoint()
        return operation['status'] in ('open', 'deferred')

    def open_operations(self):
        result = []
        for operation in self.data['operations'].values():
            if operation['status'] in ('stale', 'completed'): continue
            try:
                if self.refresh(operation): result.append(operation)
            except GateError as error:
                if str(error) == 't3_thread_unavailable':
                    operation['status'] = 'stale'
                    self.store.save()
                else: raise
        return result

    def reserve_attempt(self, delay=0, min_interval=0):
        if time.time() - self.data.get('last_outgoing_at', 0) < min_interval: return None
        for operation in self.open_operations():
            if operation['status'] == 'open' and operation['attempt'] is None:
                if not question_due(self.client.snapshot(operation['thread_id']), operation['request_id'], delay):
                    continue
                operation['attempt'] = {'id': uuid.uuid4().hex, 'phase': 'starting'}
                self.data['last_outgoing_at'] = time.time()
                self.store.save()  # Before createCall. Never redial after a crash.
                return operation['id']
        return None

    def recover(self):
        for operation in list(self.data['operations'].values()):
            if operation['attempt'] and operation['first_message'] is None:
                self.finish_attempt(operation['id'], {'phase': 'interrupted', 'reason': 'service_restart'})

    def begin_incoming(self, operation, call_id):
        previous = operation['attempt']
        if previous and call_id is not None and previous.get('call_id') == call_id: return
        if previous:
            operation.setdefault('contact_history', []).append({**previous, 'first_message': operation['first_message']})
        operation['attempt'] = {'id': uuid.uuid4().hex, 'phase': 'incoming', 'call_id': call_id}
        operation['first_message'] = None
        self.store.save()

    def notice_key(self, operation):
        packet = operation['dialog']['packet']
        return hashlib.sha256(json.dumps([operation['request_id'], packet.get('source_reply')],
                                          ensure_ascii=False).encode()).hexdigest()

    def poll_dialogs(self):
        for operation in self.open_operations():
            dialog = self.delegate(operation)
            if (operation['status'] == 'open' and operation.get('last_notice')
                    and not dialog.mutation_uncertain and dialog.last_report
                    and operation['last_notice'] != self.notice_key(operation)):
                self.queue_text(dialog.last_report[:3500], operation=operation, purpose='source_reply')

    def queue_text(self, text, *, operation=None, reply_to=None, purpose='reply'):
        identifier = uuid.uuid4().hex
        item = {'id': identifier, 'operation_id': operation['id'] if operation else None,
                'request_id': operation['request_id'] if operation else None,
                'purpose': purpose, 'status': 'reserved', 'message_ids': [], 'text': text[:4000]}
        self.data['outbox'][identifier] = item
        if operation: operation['last_notice'] = self.notice_key(operation)
        if purpose == 'first': operation['first_message'] = identifier
        self.store.save()  # Ambiguous sends are never retried automatically.
        content = {'@type': 'inputMessageText', 'text': {'@type': 'formattedText', 'text': item['text'], 'entities': []},
                   'clear_draft': False}
        request = {'@type': 'sendMessage', 'chat_id': self.data['target_id'],
                   '@extra': 'conversation-' + identifier, 'input_message_content': content}
        if reply_to:
            request['reply_to'] = {'@type': 'inputMessageReplyToMessage', 'message_id': reply_to}
        self.send(request)

    def finish_attempt(self, identifier, status):
        operation = self.data['operations'][identifier]
        operation['attempt']['phase'] = status['phase']
        operation['attempt']['reason'] = status.get('reason')
        operation['attempt']['call_id'] = status.get('call_id', operation['attempt'].get('call_id'))
        self.store.save()
        self.settle_operation_coordinator(operation, status.get('reason') or status['phase'])
        if (not self.refresh(operation) or operation['status'] != 'open'
                or operation['first_message'] is not None or operation['attempt'].get('followup_suppressed')): return
        questions = ' '.join(q['question'] for q in self.delegate(operation).packet['questions'])
        self.queue_text(f"Ich brauche noch kurz deine Einschätzung zu {operation['title']} ({self.project_title(operation)}): {questions[:2500]}\n"
                        f"Antworte einfach hier oder ruf zurück. Vorgang {identifier}.", operation=operation, purpose='first')

    def delivery(self, event):
        tag = event.get('@extra', '')
        item = self.data['outbox'].get(tag.removeprefix('conversation-')) if isinstance(tag, str) else None
        message = event.get('message', event)
        if item is None:
            old = event.get('old_message_id')
            item = next((i for i in self.data['outbox'].values() if old in i['message_ids']), None) if old else None
        if item is None or message.get('chat_id', self.data['target_id']) != self.data['target_id']: return
        kind = event.get('@type')
        if kind in ('error', 'updateMessageSendFailed') and item['status'] != 'sent': item['status'] = 'failed'
        elif kind in ('message', 'updateMessageSendSucceeded'):
            mid = message.get('id')
            if type(mid) is int and mid not in item['message_ids']: item['message_ids'].append(mid)
            if item['status'] != 'sent':
                item['status'] = 'sent' if kind == 'updateMessageSendSucceeded' or not message.get('sending_state') else 'pending'
        self.store.save()

    def select(self, text, reply_to=None):
        explicit = [o for key, o in self.data['operations'].items() if re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', text)]
        linked = [self.data['operations'][i['operation_id']] for i in self.data['outbox'].values()
                  if reply_to and reply_to in i['message_ids'] and i['operation_id']]
        candidates = {o['id']: o for o in explicit + linked}
        if len(candidates) == 1: return next(iter(candidates.values()))
        if candidates: return None
        if reply_to: return None
        available = self.open_operations()
        return available[0] if len(available) == 1 else None

    def status_text(self, project_id=None, query=''):
        shell = self.client.request('/api/orchestration/shell')
        projects = {p['id']: p for p in self.projects(shell)}
        threads = [t for t in shell.get('threads', []) if t.get('projectId') in projects
                   and (project_id is None or t['projectId'] == project_id)
                   and not t.get('archivedAt') and not t.get('deletedAt') and not self.is_internal(t)]
        words = {w for w in re.findall(r'\w+', query.casefold()) if len(w) >= 4 and w not in {
            'status','steht','läuft','meine','meinen','meinem','bitte','fortschritt','projekt','thread','aufgabe'}}
        threads.sort(key=lambda t: (sum(w in t.get('title','').casefold() for w in words),
                                   bool(t.get('hasPendingUserInput')), t.get('updatedAt', '')), reverse=True)
        lines = [f'Ausschnitt: {min(12, len(threads))} von {len(threads)} aktiven Threads, neueste und offene zuerst.']
        for thread in threads[:12]:
            source = self.client.snapshot(thread['id'])['thread']
            state = (source.get('latestTurn') or {}).get('state', 'unbekannt')
            lines.append(f"{projects[thread['projectId']].get('title')}: {source.get('title', 'Aufgabe')}: {state}. {selected_context(source)[:1000]}")
        return '\n'.join(lines)[:20000] or 'Für dieses Projekt sind keine aktiven Aufgaben vorhanden.'

    def status_reply(self, text):
        shell = self.client.request('/api/orchestration/shell')
        thread = self.coordinator_source(shell)
        if thread is None: return 'Für dieses Projekt sind keine aktiven Aufgaben vorhanden.'
        coordinator = self.data.get('status_coordinator')
        if coordinator is None:
            coordinator = self.client.create_coordinator(self.client.snapshot(thread['id'])['thread'])
            self.data['status_coordinator'] = coordinator
            self.store.save()
        return self.client.run_coordinator(coordinator,
            'Du bist Mitarbeiter des Monats. Keine Tools, Dateizugriffe oder Projektaktionen. '
            'Beantworte die Statusfrage natürlich und knapp auf Deutsch, ausschließlich anhand dieser Daten. '
            'Nenne offene Fragen und Probleme nur, wenn sie belegt sind.\n' + json.dumps(
                {'question': text, 'status': self.status_text(query=text)}, ensure_ascii=False))[:3500]

    def respond(self, operation, text):
        previous_request = operation['request_id']
        if not self.refresh(operation) and operation['status'] == 'stale':
            return 'Diese Rückfrage ist inzwischen in T3 erledigt oder veraltet. Ich habe nichts übertragen.'
        dialog = self.delegate(operation)
        if previous_request != operation['request_id']:
            return 'Inzwischen gibt es eine neue Rückfrage: ' + ' '.join(q['question'] for q in dialog.packet['questions'])[:2500]
        if text.strip().casefold().rstrip('.!') in ('jetzt nicht', 'später', 'nicht jetzt', 'bitte später'):
            dialog.deferred = True
            operation['status'] = 'deferred'
            dialog.checkpoint()
            return 'Alles klar, ich warte. Melde dich hier oder ruf zurück, sobald es passt. Ich fasse nicht automatisch nach.'
        if operation['status'] == 'completed':
            if pending_requests(self.client.snapshot(operation['thread_id'])):
                return 'Inzwischen gibt es eine neue offene Rückfrage. Bitte antworte auf deren Nachricht oder nenne die neue Vorgangs-ID. Diese Korrektur habe ich nicht übertragen.'
            # An explicitly linked late correction is a new source turn, never a replay of the old Ask.
            dialog.completed = False
        operation['status'] = 'open'
        dialog.deferred = False
        operation['transcript'].append({'role': 'user', 'text': text})
        operation['transcript'] = operation['transcript'][-30:]
        dialog.adopted_revision = None  # This is fresh input, including after process restart.
        dialog.checkpoint()
        dialog.channel = 'text'
        try: result = dialog(operation['transcript'])
        finally: dialog.channel = 'voice'
        if dialog.completed:
            result = 'Alles klar, damit habe ich alles. Deine Antwort ist in T3 angekommen. Ich mache weiter.'
            operation['status'] = 'completed'
        elif dialog.deferred: operation['status'] = 'deferred'
        operation['transcript'].append({'role': 'assistant', 'text': result})
        dialog.checkpoint()
        return result

    def message(self, message):
        sender = message.get('sender_id', {})
        if (message.get('is_outgoing') is not False or message.get('chat_id') != self.data['target_id']
                or sender.get('@type') != 'messageSenderUser' or sender.get('user_id') != self.data['target_id']):
            return
        content = message.get('content', {})
        text = content.get('text', {}).get('text') if content.get('@type') == 'messageText' else None
        if not isinstance(text, str) or not 0 < len(text) <= 8000 or type(message.get('id')) is not int: return
        if type(message.get('date')) is not int or message['date'] < self.data['activated_at']: return
        key = str(message['id']) + ':' + hashlib.sha256(text.encode()).hexdigest()
        if key in self.data['updates']: return
        self.data['updates'][key] = 'processing'
        self.store.save()  # A crash cannot replay a decision.
        from .followups import Followups
        answer = Followups(self).handle(text, 'telegram:' + str(self.data['target_id']) + ':' + str(message['id']))
        if answer is not None:
            self.queue_text(answer, reply_to=message['id'])
            self.data['updates'][key] = 'handled'
            self.store.save()
            return
        self.discover(0)
        reply = message.get('reply_to') or {}
        reply_id = reply.get('message_id') if reply.get('chat_id', self.data['target_id']) in (0, self.data['target_id']) else None
        operation = self.select(text, reply_id)
        linked_request = next((i.get('request_id') for i in self.data['outbox'].values()
                               if reply_id and reply_id in i['message_ids']), None)
        if operation and (type(message.get('date')) is not int or message['date'] < operation['created_at']):
            self.data['updates'][key] = 'stale'
            self.store.save()
            return
        if re.search(r'(?i)\b(status|fortschritt|probleme)\b|wie läuft', text) and not reply_id:
            operation = None
            answer = self.status_reply(text)
        elif operation and linked_request and linked_request != operation['request_id']:
            answer = 'Die Nachricht gehört zu einer früheren Rückfrage. Aktuell offen: ' + ' '.join(
                q['question'] for q in self.delegate(operation).packet['questions'])[:2500]
        elif operation:
            if operation['attempt']:
                operation['attempt']['followup_suppressed'] = True
                self.store.save()
            answer = self.respond(operation, text)
        else:
            available = self.open_operations()
            answer = ('Welchen Vorgang meinst du? Bitte nenne die Vorgangs-ID oder antworte auf die passende Nachricht.\n'
                      + '\n'.join(f"{o['id']}: {o['title']}" for o in available)) if available else self.status_reply(text)
        self.queue_text(answer, operation=operation, reply_to=message['id'])
        self.data['updates'][key] = 'handled'
        self.store.save()
