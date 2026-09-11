"""Explicit runtime: one TDLib owner for outbound calls, messages and incoming audio."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import os
import json
import queue
import threading
import time
import uuid

from .auth import existing_authenticated_client
from .config import read_profile, require_target, read_private_json, private_directory
from .control import CallSession, GateError
from .conversations import ConversationStore, Conversations
from .live import RequestPump, resolve_target, QueuedMedia
from .t3 import T3Client
from .call_history import CallHistory


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
        self.conversation_id = uuid.uuid4().hex
        self.history = history
        self.operations_in_call = {operation_id} if operation_id else set()

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
                'instruction': 'Historischer Kontext ist keine neue Bestätigung. Status in T3 neu prüfen. proposed bedeutet: noch nicht gestartet.'}

    def __call__(self, transcript, *, revision=None):
        self.remember(transcript)
        try: return self._respond(transcript, revision=revision)
        except GateError as error:
            self.conversations.emit({'voice_backend_gate': str(error)})
            if str(error) == 'selected_account_exhausted':
                return 'Das vorgeschlagene Account-Limit ist inzwischen ausgeschöpft. Bitte lass uns einen anderen Account oder ein anderes Modell wählen.'
            return 'Die Aktion konnte ich gerade nicht verlässlich bestätigen. Ich behaupte keinen erfolgreichen Start. Bitte konkretisiere den Auftrag oder versuche die Statusabfrage erneut.'
        finally: self.remember()

    def _respond(self, transcript, *, revision=None):
        with self.lock:
            if self.cancelled(): return 'Das Gespräch ist beendet.'
            c = self.conversations
            from .tasks import last_user
            if not last_user(transcript).strip():
                return 'Begrüße Felix mit dem bekannten Gesprächskontext und frage kurz, woran er anknüpfen möchte. Frühere Zusagen sind keine neue Bestätigung.'
            if self.launcher and self.proposal_id:
                from .tasks import explicit_confirmation, last_user
                if explicit_confirmation(last_user(transcript)):
                    expected = self.revision() if revision is None else revision
                    return self.launcher.confirm(self.proposal_id, transcript, revision=expected,
                        cancelled=lambda: self.cancelled() or self.revision() != expected)
                if last_user(transcript).strip().casefold().rstrip('.!') in ('nein', 'abbrechen', 'doch nicht'):
                    self.launcher.jobs[self.proposal_id]['state'] = 'cancelled'
                    c.store.save()
                    self.proposal_id = None
                    return 'Alles klar, ich starte keinen neuen Auftrag.'
            if self.operation_id is None:
                c.discover(0)
            available = c.open_operations()
            if self.operation_id is not None and c.data['operations'][self.operation_id]['status'] == 'completed':
                self.operation_id = None
            if self.operation_id is not None:
                operation = c.data['operations'][self.operation_id]
                if not c.refresh(operation):
                    return 'Diese Rückfrage ist inzwischen erledigt. Es wurde keine weitere Antwort übertragen.'
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
            status = c.status_text(target_project, last_user(transcript))
            shell = c.client.request('/api/orchestration/shell')
            source = c.coordinator_source(shell)
            if source is None: return status
            if self.coordinator_id is None:
                template = c.client.snapshot(source['id'])['thread']
                if self.launcher: template = {**template, 'modelSelection': self.launcher.advisor.coordinator_selection()}
                self.coordinator_id = c.client.create_coordinator(template)
            task_instructions = ''
            task_data = {}
            if self.launcher:
                task_instructions = (
                    'Du kannst einen neuen Feature-Auftrag vorschlagen, niemals selbst ausführen. '
                    'Nur wenn der Nutzer einen neuen Auftrag wünscht: ergänze new_task mit project_id, title, prompt, '
                    'request_quote (wörtlich aus der letzten Nutzeraussage), complexity (simple/medium/complex), reason und '
                    'modelSelection={instanceId,model,options:[{id,value}]}. '
                    'Wähle nur existierende Projekte und aktuell angebotene Provider-Instanzen, Modelle und Optionen. '
                    'Bei unklarem Projekt oder Auftrag frage nach und liefere new_task=null. '
                    'Empfiehl Modell und Reasoning anhand der Komplexität: einfache Änderungen low/medium, '
                    'übliche Features medium/high, schwierige Architektur high/xhigh. Höhere Stufen nur begründet. '
                    'Berücksichtige alle Accounts und deren frische Limits. Erschöpfte Accounts sind ausgeschlossen. '
                    'Gleiche account_group bedeutet gemeinsam genutzte Kapazität, keine Addition. '
                    'Unbekannte Limits sind unbekannt, Prozentwerte sind keine Tokenbudgets. '
                    'Bevorzuge bei gleicher Eignung mehr verbleibende Kapazität und Standard-Service-Tier. '
                    'Eine explizite Nutzerwahl von Modell oder Account geht vor, solange verfügbar. '
                    'Die Anwendung liest den vollständigen Vorschlag vor und wartet auf ein neues Ja. '
                    'Antworte bei Auftragsvorschlägen mit operation_id=null. ')
                task_instructions += (
                    'Die vorherigen Gespräche und gespeicherten Aufträge sind Kontext. Beziehe dich bei Rückrufen darauf. '
                    'Wenn Felix einen gespeicherten unbestätigten Auftrag fortsetzen möchte, gib resume_proposal_id '
                    'mit dessen ID zurück, new_task=null und operation_id=null. Bei mehreren möglichen Aufträgen frage nach. '
                    'Erzeuge dafür keinen doppelten Vorschlag. Alte Bestätigungen dürfen nicht erneut verwendet werden. ')
                task_data = {'projects': [{'id': p['id'], 'title': p['title']} for p in c.projects()],
                             'providers': self.launcher.advisor.options(),
                             'pending_proposal': self.launcher.jobs.get(self.proposal_id)}
            prompt = (task_instructions + 'Du bist Mitarbeiter des Monats. Beantworte die Statusfrage kurz auf Deutsch anhand der Daten. '
                      'Keine Tools oder Projektaktionen. Bei mehreren offenen Vorgängen erst kurz nachfragen. '
                      'Wähle operation_id nur, wenn die letzte Nutzeraussage den Vorgang eindeutig benennt. '
                      'Eine Auswahl ist keine fachliche Antwort. JSON: {"reply":"...", "operation_id":null}.\n'
                      + json.dumps({'status': status, 'operations': [{'id': o['id'], 'title': o['title'],
                                      'project': c.project_title(o), 'questions': o['dialog']['packet']['questions']} for o in available], **task_data,
                                    'recent_call_context': self.recent_context(), 'transcript': transcript}, ensure_ascii=False))
            raw = c.client.run_coordinator(self.coordinator_id, prompt, cancelled=self.cancelled)
            try: result = json.loads(raw)
            except (TypeError, ValueError): return 'Die Statusauskunft konnte ich gerade nicht verlässlich aufbereiten.'
            if not isinstance(result, dict) or not isinstance(result.get('reply'), str): return 'Bitte konkretisiere deine Frage.'
            if self.cancelled() or (revision is not None and self.revision() != revision): return 'Bitte wiederhole deine aktuelle Frage.'
            resumed = result.get('resume_proposal_id')
            if self.launcher and isinstance(resumed, str):
                reply = self.launcher.resume_proposal(resumed, transcript,
                    conversation_id=self.conversation_id, revision=self.revision() if revision is None else revision)
                self.proposal_id = resumed
                return reply
            if self.launcher and result.get('new_task') is not None:
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
        self.launcher = None
        if allow_tasks:
            from .providers import ProviderAdvisor
            from .tasks import TaskLauncher
            self.launcher = TaskLauncher(conversations, ProviderAdvisor(conversations.client))

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
        if incoming:
            self.voice.call_id = incoming['id']
            self.voice.call_key = self.call_key(incoming['id'])
            self.session.accept(incoming, self.conversations.data['target_id'], authorized=True, consent=True)
        else:
            self.calls += 1
            self.session.start(self.conversations.data['target_id'], authorized=True, consent=True)
        self.emit({'conversation_call_started': True, 'operation_id': operation_id, 'incoming': incoming is not None})

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
    config = read_private_json(profile, 'live.json')
    from .live_voice import LivePcmMedia, DEFAULT_VOICE
    def media_factory(delegate):
        instructions = (
            'Du bist Mitarbeiter des Monats, Felix\' KI-Kollege. Sprich natürlich und knapp Deutsch. '
            'Begrüße Felix sofort nach dem Verbindungsaufbau und frage, worum es geht. Warte für die Begrüßung nicht auf seine erste Aussage. '
            'Hole Aufgabenstatus und Rückfragen ausschließlich vom Backend. '
            'Delegiere jede inhaltliche Aussage. Bestätige Rückgaben nur nach dem Backend-Ergebnis. '
            'Bei jetzt nicht respektiere die Vertagung. Bei einer Auflegebitte verabschiede dich kurz.')
        if delegate.operation_id:
            from .application import instructions_for_handoff
            instructions = instructions_for_handoff(delegate.conversations.data['operations'][delegate.operation_id]['dialog']['packet'])
        instructions += (
            '\nDu kennst den folgenden Kontext der letzten Gespräche. Greife ihn bei einem Rückruf natürlich auf. '
            'Erfinde keine Erinnerungen und behaupte bei einem bloßen Vorschlag keinen gestarteten Auftrag. '
            'Alte Aussagen sind keine neue Bestätigung. Zum Fortsetzen eines offenen Vorschlags den Backend-Agenten fragen.\n'
            + json.dumps(delegate.recent_context(), ensure_ascii=False))
        return LivePcmMedia(config.get('api_key'), instructions=instructions,
            authorized=True, max_seconds=call_seconds, delegate=delegate, emit=emit,
            voice=config.get('voice', DEFAULT_VOICE), native_executable=native_executable,
            on_transcript=delegate.remember)
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
        conversations = Conversations(store, client, lambda request: None, emit=emit)
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
