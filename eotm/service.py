"""Explicit runtime: one TDLib owner for outbound calls, messages and incoming audio."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from contextlib import contextmanager
import fcntl
import os
import json
import queue
import re
import threading
import time
import uuid

from .auth import existing_authenticated_client
from .config import read_profile, require_target, private_directory
from .control import CallSession, GateError
from .conversations import ConversationStore, Conversations
from .live import RequestPump, resolve_target, QueuedMedia
from .t3 import T3Client
from .call_history import CallHistory
from .i18n import Locale


def voice_instructions(packet, recent_context, locale=None):
    """Rules for the live voice: it leads the conversation and delegates only what T3 must act on."""
    return (locale or Locale()).voice_instructions(recent_context, packet)


def status_context(brief, locale=None):
    return (locale or Locale()).status_context(brief, datetime.now().strftime('%H:%M'))


def build_media(config, delegate, *, call_seconds, emit, native_executable):
    """The live voice for one service call: rules, spoken greeting, wait tone and transcript journal."""
    from .live_voice import LivePcmMedia, DEFAULT_VOICE
    locale = Locale.from_config(config)
    packet = delegate.conversations.data['operations'][delegate.operation_id]['dialog']['packet'] if delegate.operation_id else None
    return LivePcmMedia(config.get('api_key'), instructions=voice_instructions(packet, delegate.recent_context(), locale),
        greeting=locale.text("greeting"), wait_tone=config.get('wait_tone', True) is not False,
        authorized=True, max_seconds=call_seconds, delegate=delegate, emit=emit,
        voice=config.get('voice', DEFAULT_VOICE), native_executable=native_executable,
        on_transcript=delegate.remember, locale=locale)


class VoiceConversation:
    def __init__(self, conversations, lock, operation_id=None, launcher=None, history=None):
        self.conversations, self.lock, self.operation_id = conversations, lock, operation_id
        self.cancelled = lambda: False
        self.revision = lambda: 0
        self.coordinator_id = None
        self.call_id = None
        self.call_key = None
        self.launcher = launcher
        self.proposal_id = None
        self.followup_context = None
        self.followup_context_floor = 0
        self.conversation_id = uuid.uuid4().hex
        self.history = history
        self.operations_in_call = {operation_id} if operation_id else set()
        self.snapshots = {}

    def status_snapshot(self, thread_id):
        """Status reads may be a few seconds old within one call. Answers and T3 commands always read fresh."""
        cached = self.snapshots.get(thread_id)
        if cached is None or time.monotonic() - cached[0] >= 15:
            cached = self.snapshots[thread_id] = (time.monotonic(), self.conversations.client.snapshot(thread_id))
        return cached[1]

    def call_context(self):
        return status_context(self.conversations.status_brief(snapshot=self.status_snapshot), self.conversations.locale)

    def remember(self, transcript=None):
        if self.history:
            self.history.update(self.conversation_id, transcript=transcript,
                call_key=self.call_key, operation_id=self.operation_id,
                proposal_id=self.proposal_id, coordinator_id=self.coordinator_id)

    def recent_context(self):
        orders = list((self.launcher.jobs if self.launcher else self.conversations.data.get('tasks', {})).values())
        orders = [o for o in orders if o['state'] not in ('cancelled', 'superseded') and self.conversations.in_scope(o['project_id'])][-5:]
        return {'previous_calls': self.history.recent(exclude=self.conversation_id) if self.history else [],
                'saved_orders': [{k:o.get(k) for k in ('id','project_id','project_title','title','state','modelSelection','created_at','thread_id')}
                                 | {'prompt':o.get('prompt','')[:1200]} for o in orders],
                'instruction': self.conversations.locale.text('history_instruction')}

    def discard_followup_context(self, user_turn):
        self.followup_context = None
        self.followup_context_floor = max(self.followup_context_floor, user_turn)

    def contextual_followup(self, followups, current, transcript, *, revision=None):
        from .followups import contextual_send, contextual_message, contextual_confirmation, contextual_cancel
        user_turn = sum(m.get('role') == 'user' for m in transcript)
        context = self.followup_context
        if context and user_turn - context['turn'] > 4:
            self.discard_followup_context(user_turn)
            context = None
        if contextual_cancel(current):
            self.discard_followup_context(user_turn)
            return None
        intent, message = contextual_send(current)
        confirmed = contextual_confirmation(current)
        declared = contextual_message(current)
        was_awaiting_message = bool(context and context.get('awaiting_message'))
        if context and not intent and not declared and re.search(
                r'(?i)\b(?:status|fortschritt|probleme|progress|problems|issues)\b|wie\s+läuft|how(?: is|\x27s)', current):
            self.discard_followup_context(user_turn)
            return None
        if self.operation_id is not None and context is None and not intent and not re.search(r'(?i)\bthreads?\b', current):
            return None

        user_messages = [m.get('text', '') for m in transcript if m.get('role') == 'user']
        address_text = current
        if context and context.get('awaiting_message'):
            address_text = ''
        elif declared:
            marker = re.search(
                r'(?i)\b(?:die\s+Nachricht|der\s+Text|Text|der\s+Inhalt|Inhalt|the\s+message|message|content)\s+'
                r'(?:ist|lautet|soll(?:\s+dort)?\s+sein|is|says|should\s+be)\b', current)
            address_text = current[:marker.start()] if marker else ''
        elif intent:
            verb = re.search(r'(?i)\b(?:send|tell|write|message|sende|schicke|schick|sag|sage|schreib|schreibe|übermittle)\b', current)
            address_text = current[:verb.start()] if verb else ''
        mentions = followups.mentioned_targets(address_text) if address_text.strip() else []
        selected_now = bool(mentions)
        mention_turn = user_turn
        if context is None and not mentions:
            first_turn = max(self.followup_context_floor + 1, user_turn - 4)
            for previous_turn in range(user_turn - 1, first_turn - 1, -1):
                previous = user_messages[previous_turn - 1]
                previous_intent, _ = contextual_send(previous)
                previous_declared = contextual_message(previous)
                previous_address = previous
                if previous_intent:
                    previous_verb = re.search(
                        r'(?i)\b(?:send|tell|write|message|sende|schicke|schick|sag|sage|schreib|schreibe|übermittle)\b', previous)
                    previous_address = previous[:previous_verb.start()] if previous_verb else ''
                elif previous_declared:
                    previous_marker = re.search(
                        r'(?i)\b(?:die\s+Nachricht|der\s+Text|Text|der\s+Inhalt|Inhalt|the\s+message|message|content)\s+'
                        r'(?:ist|lautet|soll(?:\s+dort)?\s+sein|is|says|should\s+be)\b', previous)
                    previous_address = previous[:previous_marker.start()] if previous_marker else ''
                mentions = followups.mentioned_targets(previous_address) if previous_address.strip() else []
                if mentions:
                    mention_turn = previous_turn
                    break
        if mentions:
            ids = [t['id'] for t in mentions]
            preserve = bool(context and context.get('awaiting_target'))
            context = self.followup_context = {
                'target_ids': ids, 'targets': mentions,
                'text': context.get('text') if preserve else None,
                'turn': mention_turn,
                'awaiting_message': context.get('awaiting_message', False) if preserve else False,
                'awaiting_target': context.get('awaiting_target', False) if preserve else False,
            }

        declared = declared if context else None
        if context and context.get('awaiting_message') and not intent and not confirmed and not declared:
            if not current.rstrip().endswith('?') and not re.match(
                    r'(?i)\s*(?:wer|wie|was|warum|weshalb|welch|wo|wann|who|how|what|why|which|where|when)\b', current):
                message = current.strip()
                intent = bool(message)
        if context and message:
            context['text'] = message
        if context and context.get('awaiting_target') and selected_now:
            intent = True
            if len(context['targets']) == 1:
                context['awaiting_target'] = False
        if declared and context:
            context['text'] = declared
            context['turn'] = user_turn
            if was_awaiting_message:
                intent = True
            elif not intent and not confirmed:
                if len(context['targets']) != 1:
                    return followups.ambiguous_reply(context['targets'])
                return self.conversations.locale.text('followup_readback', message=declared[:240],
                    title=context["targets"][0].get("title"))

        if not intent and not (confirmed and context):
            if context and not selected_now:
                self.discard_followup_context(user_turn)
            return None
        if context is None:
            self.followup_context = {
                'target_ids': [], 'targets': [], 'text': message, 'turn': user_turn,
                'awaiting_message': False, 'awaiting_target': True,
            }
            return self.conversations.locale.text('ask_thread')
        if len(context['targets']) != 1:
            context['awaiting_target'] = True
            context['turn'] = user_turn
            return followups.ambiguous_reply(context['targets'])
        message = context.get('text')
        if not message:
            context['awaiting_message'] = True
            context['turn'] = user_turn
            return self.conversations.locale.text('ask_message', title=context["targets"][0].get("title"))

        expected = self.revision() if revision is None else revision
        target = context['targets'][0]
        event_id = self.conversation_id + ':context:' + str(user_turn)
        self.discard_followup_context(user_turn)
        return followups.handle_spec({'target': target['id'], 'text': message}, current, event_id,
            cancelled=lambda: self.cancelled() or self.revision() != expected)

    def __call__(self, transcript, *, revision=None):
        self.remember(transcript)
        try: return self._respond(transcript, revision=revision)
        except GateError as error:
            self.conversations.emit({'voice_backend_gate': str(error)})
            if str(error) == 'selected_account_exhausted':
                return self.conversations.locale.text('quota_exhausted')
            return self.conversations.locale.text('unconfirmed_action')
        finally: self.remember()

    def _respond(self, transcript, *, revision=None):
        with self.lock:
            c = self.conversations
            if self.cancelled(): return c.locale.text('call_ended')
            from .tasks import last_user
            if not last_user(transcript).strip():
                return c.locale.text('greet_with_context')
            from .followups import Followups, parse_followup
            from .tasks import explicit_confirmation
            current = last_user(transcript)
            # Route explicit addressed messages before an attached Ask or proposal.
            # Any intervening intent invalidates the conversational confirmation slot.
            if (self.proposal_id and not explicit_confirmation(current)
                    and current.strip().casefold().rstrip('.!') not in ('nein', 'abbrechen', 'doch nicht', 'no', 'cancel', 'never mind')):
                self.proposal_id = None
            followups = Followups(c)
            parsed_followup = parse_followup(current)
            if parsed_followup is not None:
                self.operation_id = None
                if hasattr(self, 'prior_transcript'):
                    del self.prior_transcript
                if not parsed_followup:
                    mentions = followups.mentioned_targets(current)
                    self.followup_context = {
                        'target_ids': [t['id'] for t in mentions], 'targets': mentions,
                        'text': None, 'turn': sum(m.get('role') == 'user' for m in transcript),
                        'awaiting_message': len(mentions) == 1, 'awaiting_target': len(mentions) != 1,
                    }
                    if len(mentions) == 1:
                        return c.locale.text('ask_message', title=mentions[0].get('title'))
                    if len(mentions) > 1:
                        return followups.ambiguous_reply(mentions)
                    return c.locale.text('ask_thread_and_message')
                matches = followups.matching_targets(parsed_followup['target'])
                if len(matches) > 1:
                    self.followup_context = {
                        'target_ids': [t['id'] for t in matches], 'targets': matches,
                        'text': parsed_followup['text'],
                        'turn': sum(m.get('role') == 'user' for m in transcript),
                        'awaiting_message': False, 'awaiting_target': True,
                    }
                    return followups.ambiguous_reply(matches)
                self.discard_followup_context(sum(m.get('role') == 'user' for m in transcript))
                expected = self.revision() if revision is None else revision
                event_id = self.conversation_id + ':' + str(sum(
                    m.get('role') == 'user' for m in transcript))
                return followups.handle(current, event_id,
                    cancelled=lambda: self.cancelled() or self.revision() != expected)
            contextual = self.contextual_followup(followups, current, transcript, revision=revision)
            if contextual is not None:
                self.operation_id = None
                if hasattr(self, 'prior_transcript'):
                    del self.prior_transcript
                return contextual
            if self.launcher and self.proposal_id:
                from .tasks import explicit_confirmation, last_user
                if explicit_confirmation(last_user(transcript)):
                    expected = self.revision() if revision is None else revision
                    return self.launcher.confirm(self.proposal_id, transcript, revision=expected,
                        cancelled=lambda: self.cancelled() or self.revision() != expected)
                if last_user(transcript).strip().casefold().rstrip('.!') in ('nein', 'abbrechen', 'doch nicht', 'no', 'cancel', 'never mind'):
                    self.launcher.jobs[self.proposal_id]['state'] = 'cancelled'
                    c.store.save()
                    self.proposal_id = None
                    return c.locale.text('task_cancelled')
            if self.operation_id is None:
                c.discover(0)
            available = c.open_operations()
            if self.operation_id is not None and c.data['operations'][self.operation_id]['status'] == 'completed':
                self.operation_id = None
            if self.operation_id is not None:
                operation = c.data['operations'][self.operation_id]
                if not c.refresh(operation):
                    return c.locale.text('question_resolved')
                dialog = c.delegate(operation)
                dialog.cancelled, dialog.revision = self.cancelled, self.revision
                dialog.deferred = False
                # Keep Telegram context across callback and restart, without saving raw audio.
                combined = getattr(self, 'prior_transcript', operation['transcript']) + transcript
                if not hasattr(self, 'prior_transcript'):
                    self.prior_transcript = list(operation['transcript'])
                try:
                    result = dialog(combined, revision=revision)
                    operation['status'] = 'completed' if dialog.completed else 'deferred' if dialog.deferred else 'open'
                    operation['transcript'] = combined[-30:]
                    dialog.checkpoint()
                    return result
                finally:
                    dialog.cancelled, dialog.revision = lambda: False, lambda: 0
            from .tasks import last_user
            project_matches = [p for p in c.projects() if p.get('title') and p['title'].casefold() in last_user(transcript).casefold()]
            target_project = max(project_matches, key=lambda p: len(p['title']))['id'] if project_matches else None
            status = c.status_text(target_project, last_user(transcript), snapshot=self.status_snapshot)
            shell = c.client.request('/api/orchestration/shell')
            source = c.coordinator_source(shell)
            if source is None: return status
            task_data = {}
            if self.launcher:
                task_data = {'projects': [{'id': p['id'], 'title': p['title']} for p in c.projects()],
                             'providers': self.launcher.advisor.options(max_age=600),
                             'pending_proposal': self.launcher.jobs.get(self.proposal_id)}
            payload = {'status': status, 'operations': [{'id': o['id'], 'title': o['title'],
                       'project': c.project_title(o), 'questions': o['dialog']['packet']['questions']}
                       for o in available], **task_data, 'recent_call_context': self.recent_context(),
                       'transcript': transcript}
            prompt = c.locale.status_structuring_prompt(payload, allow_tasks=self.launcher is not None)
            def coordinator():
                if self.coordinator_id is None:
                    template = c.client.snapshot(source['id'])['thread']
                    if self.launcher: template = {**template, 'modelSelection': self.launcher.advisor.coordinator_selection()}
                    self.coordinator_id = c.client.create_coordinator(template)
                return c.client.run_coordinator(self.coordinator_id, prompt, cancelled=self.cancelled)
            raw = c.structure(prompt, coordinator)
            try: result = json.loads(raw)
            except (TypeError, ValueError): return c.locale.text('status_unavailable')
            if not isinstance(result, dict) or not isinstance(result.get('reply'), str): return c.locale.text('clarify')
            if self.cancelled() or (revision is not None and self.revision() != revision): return c.locale.text('repeat_current')
            resumed = result.get('resume_proposal_id')
            if self.launcher and isinstance(resumed, str):
                self.discard_followup_context(sum(m.get('role') == 'user' for m in transcript))
                reply = self.launcher.resume_proposal(resumed, transcript,
                    conversation_id=self.conversation_id, revision=self.revision() if revision is None else revision)
                self.proposal_id = resumed
                return reply
            if self.launcher and result.get('new_task') is not None:
                self.discard_followup_context(sum(m.get('role') == 'user' for m in transcript))
                proposal, answer = self.launcher.propose(result['new_task'], transcript,
                    conversation_id=self.conversation_id, revision=self.revision() if revision is None else revision)
                previous = self.launcher.jobs.get(self.proposal_id)
                if previous and previous['state'] == 'proposed':
                    previous.update(state='superseded', superseded_by=proposal['id'])
                    c.store.save()
                self.proposal_id = proposal['id']
                return answer
            selected = result.get('operation_id')
            if isinstance(selected, str) and selected in {o['id'] for o in available}:
                self.discard_followup_context(sum(m.get('role') == 'user' for m in transcript))
                self.operation_id = selected
                self.operations_in_call.add(selected)
                operation = c.data['operations'][selected]
                c.begin_incoming(operation, self.call_key or self.call_id)
                if self.call_id is not None:
                    c.data['calls'][self.call_key or str(self.call_id)]['operation_id'] = selected
                c.store.save()
            return result['reply'][:1100]


class TelegramService:
    """TDLib always runs on the caller thread; slow T3 work runs on one worker."""
    def __init__(self, td, conversations, media_factory, *, call_seconds=120, max_calls=1,
                 delay=180, emit=lambda value: None, clock=time.monotonic, allow_tasks=False, min_call_interval=0):
        self.td, self.conversations, self.media_factory = td, conversations, media_factory
        self.call_seconds, self.max_calls, self.delay = call_seconds, max_calls, delay
        self.emit, self.clock = emit, clock
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='telegram-dialog')
        self.jobs = []
        self.outgoing = queue.Queue(maxsize=128)
        conversations.send = self.outgoing.put_nowait
        self.session = self.media = self.voice = None
        self.calls = 0
        self.next_scan = 0
        self.stopping = False
        self.started = clock()
        self.pending_incoming = None
        self.session_id = uuid.uuid4().hex
        self.seen_calls = set()  # TDLib call IDs are scoped to a client lifetime.
        self.cancelled_calls = set()
        self.input_generation = 0
        self.recovered = False
        self.history = CallHistory(conversations.store.profile)
        self.min_call_interval = min_call_interval
        self.backend_error = None
        self.project_count = 0
        self.status_cache = None  # (clock, text): recent compact status, kept warm while idle
        self.status_checked_at = None
        self.launcher = None
        if allow_tasks:
            from .providers import ProviderAdvisor
            from .tasks import TaskLauncher
            self.launcher = TaskLauncher(conversations, ProviderAdvisor(conversations.client, locale=conversations.locale))

    def call_key(self, call_id):
        return self.session_id + ':' + str(call_id)

    def submit(self, fn, callback=lambda value: None):
        if len(self.jobs) >= 128: raise GateError('service_work_queue_full')
        def work():
            with self.lock: return fn()
        self.jobs.append((self.executor.submit(work), callback))

    def event(self, event):
        kind = event.get('@type')
        if kind == 'updateAuthorizationState' and event.get('authorization_state', {}).get('@type') != 'authorizationStateReady':
            raise GateError('authenticated_session_lost')
        if kind in ('updateMessageContent', 'updateMessageEdited'):
            if event.get('chat_id') == self.conversations.data['target_id'] and type(event.get('message_id')) is int:
                self.td.send({'@type': 'getMessage', 'chat_id': event['chat_id'], 'message_id': event['message_id'],
                              '@extra': 'edited-conversation-message'})
        elif kind == 'updateNewMessage' or (kind == 'message' and event.get('@extra') == 'edited-conversation-message'):
            message = event.get('message', {}) if kind == 'updateNewMessage' else event
            sender = message.get('sender_id', {})
            target = self.conversations.data['target_id']
            if (message.get('chat_id') == target and sender.get('@type') == 'messageSenderUser'
                    and sender.get('user_id') == target and message.get('is_outgoing') is False):
                # Text takes precedence over call setup; serialization prevents two decisions racing.
                if self.session: self.session.end('telegram_text_received')
                self.input_generation += 1
                self.submit(lambda: self.conversations.message(message))
        elif kind in ('updateMessageSendSucceeded', 'updateMessageSendFailed', 'message', 'error'):
            self.submit(lambda: self.conversations.delivery(event))
        elif kind == 'updateCall':
            call = event.get('call', {})
            identifier = str(call.get('id'))
            if call.get('state', {}).get('@type') in ('callStateDiscarded', 'callStateError'):
                self.cancelled_calls.add(identifier)
            if (call.get('is_outgoing') is False and call.get('state', {}).get('@type') == 'callStatePending'
                    and identifier not in self.seen_calls):
                self.seen_calls.add(identifier)
                if call.get('user_id') == self.conversations.data['target_id'] and call.get('is_video') is False:
                    if self.session is None and self.pending_incoming is None:
                        self.pending_incoming = call
                    # A busy call is left unanswered; never allocate a second media session.
            if self.pending_incoming and call.get('id') == self.pending_incoming['id']:
                if call.get('state', {}).get('@type') in ('callStateDiscarded', 'callStateError'):
                    self.pending_incoming = None
        if self.session:
            previous_id = self.session.call_id
            self.session.handle(event)
            if previous_id is None and self.session.call_id is not None:
                call_id, operation_id = self.session.call_id, self.voice.operation_id
                self.voice.call_id = call_id
                self.voice.call_key = self.call_key(call_id)
                def remember_outbound():
                    self.conversations.data['calls'][self.call_key(call_id)] = {'call_id': call_id, 'operation_id': operation_id, 'incoming': False}
                    self.conversations.data['operations'][operation_id]['attempt']['call_id'] = call_id
                    self.conversations.store.save()
                self.submit(remember_outbound)

    def start_call(self, operation_id=None, incoming=None):
        if self.stopping or self.session is not None: return
        if incoming and str(incoming['id']) in self.cancelled_calls: return
        self.voice = VoiceConversation(self.conversations, self.lock, operation_id, self.launcher, self.history)
        self.history.begin(self.voice.conversation_id, incoming=incoming is not None, operation_id=operation_id)
        self.media = QueuedMedia(self.media_factory(self.voice))
        self.session = CallSession(self.td.send, self.media, self.clock)
        current_session = self.session
        self.voice.cancelled = lambda: self.stopping or current_session.stopping or current_session.phase in ('ended', 'failed', 'end_unconfirmed')
        backend = self.media.backend
        self.voice.revision = lambda: backend.voice.input_revision
        self.call_deadline = self.clock() + self.call_seconds
        # An incoming call connects faster than any prefetch, so its warm status goes in before accepting.
        # An outgoing call rings long enough for a fresh one, which then includes the question it is about.
        warm = self.status_cache if incoming and self.status_cache and self.clock() - self.status_cache[0] <= 180 else None
        extend = getattr(backend, 'extend_instructions', None)
        has_status = bool(warm and extend and extend(warm[1]))
        if incoming:
            self.voice.call_id = incoming['id']
            self.voice.call_key = self.call_key(incoming['id'])
            self.session.accept(incoming, self.conversations.data['target_id'], authorized=True, consent=True)
        else:
            self.calls += 1
            self.session.start(self.conversations.data['target_id'], authorized=True, consent=True)
        self.emit({'conversation_call_started': True, 'operation_id': operation_id, 'incoming': incoming is not None})
        self.prefetch_context(self.voice, backend, status=not has_status)

    @staticmethod
    def failure(error):
        """Log-safe reason: gate codes are fixed strings, anything else only by type name."""
        return str(error) if isinstance(error, GateError) else type(error).__name__

    def warm_status(self):
        """Idle only. One broken thread must never stop scanning, so nothing escapes from here."""
        if self.status_checked_at is not None and self.clock() - self.status_checked_at < 60: return
        self.status_checked_at = self.clock()
        try: self.status_cache = (self.clock(), status_context(self.conversations.status_brief(), self.conversations.locale))
        except Exception as error:
            self.status_cache = None
            self.emit({'voice_context_prefetch_failed': self.failure(error)})

    def prefetch_context(self, voice, backend, status=True):
        """Optional work while the phone rings, so that no spoken turn has to wait for it.
        Nothing here may reach the service loop or end the call."""
        extend = getattr(backend, 'extend_instructions', None)
        def load_status():
            try: return voice.call_context()
            except Exception as error: self.emit({'voice_context_prefetch_failed': self.failure(error)})
        def deliver(text):
            if text and not extend(text): self.emit({'voice_context_prefetch_unused': True})  # Too late or too large.
        def warm_catalog():
            # Account reads start provider processes and take seconds.
            try: self.launcher.advisor.refresh(max_age=600)
            except Exception as error: self.emit({'provider_catalog_warmup_failed': self.failure(error)})
        try:
            if status and extend: self.submit(load_status, deliver)
            if self.launcher: self.submit(warm_catalog)
        except GateError as error: self.emit({'voice_context_prefetch_failed': str(error)})

    def settle_after_call(self, voice, status):
        """Worker-side: the call is fully over, so its coordination threads may settle.

        The status coordinator belongs to this call only. Operation coordinators settle via
        their attempt; a later text or callback turn wakes them again on the T3 side."""
        reason = status.get('reason') or status['phase']
        if voice.coordinator_id:
            self.conversations.request_settle(voice.coordinator_id, conversation_id=voice.conversation_id, reason=reason)
            self.history.update(voice.conversation_id, coordinator_settle='requested')
        for identifier in sorted(voice.operations_in_call):
            operation = self.conversations.data['operations'].get(identifier)
            if operation and operation.get('attempt'):
                self.conversations.settle_operation_coordinator(operation, reason)

    def begin_outgoing(self, identifier, generation):
        if identifier is None or generation != self.input_generation or self.pending_incoming: return
        def validate():
            operation = self.conversations.data['operations'][identifier]
            return self.conversations.refresh(operation) and operation['status'] == 'open'
        self.submit(validate, lambda valid: self.start_call(identifier)
                    if valid and generation == self.input_generation and not self.pending_incoming else None)

    def scan(self):
        self.conversations.discover(self.delay)
        self.project_count = len(self.conversations.projects())
        self.conversations.poll_dialogs()
        self.warm_status()
        if self.launcher: self.launcher.recover()
        if not self.recovered:
            self.conversations.recover()
            candidates = self.history.settle_candidates()
            for conversation_id, coordinator_id in candidates:
                self.conversations.request_settle(coordinator_id, conversation_id=conversation_id, reason='service_restart')
                self.history.update(conversation_id, coordinator_settle='requested')
            if candidates: self.history.flush()  # The background writer only runs once a call has begun.
            self.recovered = True
        self.conversations.settle_coordinators()
        if (self.max_calls is None or self.calls < self.max_calls) and self.pending_incoming is None:
            return self.conversations.reserve_attempt(self.delay, self.min_call_interval)
        return None

    def tick(self):
        for future, callback in list(self.jobs):
            if not future.done(): continue
            self.jobs.remove((future, callback))
            try:
                callback(future.result())
                self.backend_error = None
            except GateError as error:
                if not str(error).startswith(('t3_', 'account_', 'provider_', 'model_')): raise
                self.backend_error = str(error)
                self.next_scan = self.clock() + 15
                self.emit({'service_backend_unavailable': True, 'reason': self.backend_error})
        while True:
            try: request = self.outgoing.get_nowait()
            except queue.Empty: break
            self.td.send(request)
        if self.session:
            self.media.drain()
            self.session.tick()
            if self.clock() >= self.call_deadline: self.session.end('maximum_duration')
            if self.session.phase in ('ended', 'failed', 'end_unconfirmed'):
                status, identifier, voice = self.session.status(), self.voice.operation_id, self.voice
                self.emit({'conversation_call_ended': True, 'operation_id': identifier, **status})
                self.session._stop_media()
                self.history.finish(voice.conversation_id, status)
                self.session = self.media = self.voice = None
                self.status_cache = self.status_checked_at = None  # The call may have changed what is open.
                self.submit(lambda: self.settle_after_call(voice, status))
                if status['phase'] == 'end_unconfirmed' or status.get('media_cleanup_confirmed') is False:
                    raise GateError('service_call_cleanup_unconfirmed')
                if identifier and self.conversations.data['operations'][identifier]['attempt']:
                    self.submit(lambda: self.conversations.finish_attempt(identifier, status))
        if self.session is None and not self.jobs and self.pending_incoming:
            call, self.pending_incoming = self.pending_incoming, None
            def remember_call():
                self.conversations.data['calls'][self.call_key(call['id'])] = {'call_id': call['id'], 'operation_id': None, 'incoming': True}
                self.conversations.store.save()
            self.submit(remember_call, lambda _: self.start_call(incoming=call))
        elif self.session is None and not self.jobs and self.clock() >= self.next_scan:
            self.next_scan = self.clock() + 2
            generation = self.input_generation
            self.submit(self.scan, lambda identifier: self.begin_outgoing(identifier, generation))

    def close(self, *, drain_messages=False):
        self.stopping = True
        if self.session:
            self.session.end('service_exit')
            deadline = self.clock() + 5
            while self.clock() < deadline and self.session.phase not in ('ended', 'failed', 'end_unconfirmed'):
                event = self.td.receive(.1)
                if event: self.session.handle(event)
                self.session.tick()
            self.history.finish(self.voice.conversation_id, self.session.status())
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.session:
            # The worker is gone; queue the settle durably so the next service start performs it.
            self.settle_after_call(self.voice, self.session.status())
        self.history.close()
        if drain_messages:
            tags = []
            while True:
                try: request = self.outgoing.get_nowait()
                except queue.Empty: break
                self.td.send(request)
                tags.append(request['@extra'].removeprefix('conversation-'))
            deadline = time.monotonic() + 5
            while tags and time.monotonic() < deadline:
                event = self.td.receive(.1)
                if event: self.conversations.delivery(event)
                tags = [tag for tag in tags if self.conversations.data['outbox'][tag]['status'] not in ('sent','failed')]
            if tags: self.emit({'shutdown_unconfirmed_messages': len(tags)})


@contextmanager
def watcher_lease(profile):
    private_directory(profile)
    descriptor = os.open(profile / 'watch.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise GateError('existing_project_watcher_must_be_stopped') from None
        yield


def run_service(profile, library, project_id, *, authorized=False, seconds=3600, call_seconds=120,
                max_calls=1, question_delay=180, secret_input=None, emit=lambda value: None, native_executable=None,
                continuous=False, allow_tasks=False, stop_requested=lambda: False):
    if authorized is not True: raise GateError('explicit_service_authorization_required')
    if (type(seconds) is not int or not 1 <= seconds <= 86400 or type(call_seconds) is not int
            or not 1 <= call_seconds <= 1200 or not ((continuous and max_calls is None) or (type(max_calls) is int and 0 <= max_calls <= 20))
            or type(question_delay) is not int or not 0 <= question_delay <= 3600):
        raise GateError('bounded_service_settings_required')
    client = T3Client.from_profile(profile)
    if project_id != '*' and not any(p.get('id') == project_id for p in client.request('/api/orchestration/shell').get('projects', [])):
        raise GateError('explicit_t3_project_required')
    from .config import read_live_config
    config = read_live_config(profile)
    locale = Locale.from_config(config)
    def media_factory(delegate):
        return build_media(config, delegate, call_seconds=call_seconds, emit=emit, native_executable=native_executable)
    # existing_authenticated_client holds session.lock for this entire lifetime.
    startup_events = []
    def buffer(event):
        if event.get('@type') in ('updateNewMessage', 'updateCall', 'updateNewCallSignalingData',
                                  'updateMessageContent', 'updateMessageEdited',
                                  'updateMessageSendSucceeded', 'updateMessageSendFailed'):
            if len(startup_events) >= 512: raise GateError('service_startup_buffer_full')
            startup_events.append(event)
    with watcher_lease(profile), existing_authenticated_client(profile, library, secret_input=secret_input, on_update=buffer) as td:
        pump = RequestPump(td)
        pump.on_event = buffer
        target = resolve_target(pump, require_target(read_profile(profile)))
        store = ConversationStore(profile, project_id, target.user_id, expand_scope=project_id == '*')
        from .structurer import Structurer
        conversations = Conversations(store, client, lambda request: None, emit=emit,
                                      structurer=Structurer.from_config(config), locale=locale)
        service = TelegramService(td, conversations, media_factory, call_seconds=call_seconds,
                                  max_calls=max_calls, delay=question_delay, emit=emit, allow_tasks=allow_tasks,
                                  min_call_interval=180 if continuous else 0)
        emit({'telegram_service_started': True, 'project_id': project_id})
        try:
            for event in startup_events: service.event(event)
            startup_events.clear()
            deadline = float('inf') if continuous else time.monotonic() + seconds
            next_health = 0
            while time.monotonic() < deadline and not stop_requested():
                event = td.receive(.1)
                if event: service.event(event)
                service.tick()
                if time.monotonic() >= next_health:
                    from .daemon import write_health
                    write_health(profile, {'running': True, 'pid': os.getpid(), 'all_projects': project_id == '*',
                        'project_count': service.project_count, 'task_creation': allow_tasks,
                        'call_limit_seconds': call_seconds,
                        'recent_call_contexts': len(service.history.recent()), 'call_history_error': service.history.error,
                        'active_call': service.session.status() if service.session else None,
                        'backend_error': service.backend_error})
                    next_health = time.monotonic() + 10
        finally:
            service.close(drain_messages=True)
            from .daemon import write_health
            write_health(profile, {'running': False, 'pid': os.getpid()})
            emit({'telegram_service_stopped': True})
