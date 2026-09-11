"""Confirmed voice orders become durable, exactly-addressed T3 thread commands."""
import json
import re
import uuid
import time
from pathlib import Path

from .control import GateError
from .t3 import now


def last_user(transcript):
    return next((m.get('text', '') for m in reversed(transcript) if m.get('role') == 'user'), '')


def explicit_confirmation(text):
    normalized = re.sub(r'[.,!?]', ' ', text.casefold())
    normalized = ' '.join(normalized.split())
    action = r'(?:starte|leg los|mach(?: das| es)?(?: bitte)?)'
    return bool(re.fullmatch(r'(?:ja|genau|richtig|passt|' + action + r')(?: (?:bitte|genau|richtig|passt|' + action + r'|so|das))*', normalized))


class TaskLauncher:
    def __init__(self, conversations, advisor):
        self.c, self.advisor = conversations, advisor
        self.jobs = self.c.data.setdefault('tasks', {})

    def propose(self, spec, transcript, *, conversation_id, revision):
        if not isinstance(spec, dict): raise GateError('invalid_task_proposal')
        project = next((p for p in self.c.projects() if p['id'] == spec.get('project_id')), None)
        if project is None: raise GateError('explicit_existing_project_required')
        if project.get('workspaceRoot') and not Path(project['workspaceRoot']).is_dir():
            raise GateError('task_workspace_unavailable')
        for field, limit in (('title', 120), ('prompt', 6000), ('request_quote', 2000), ('reason', 600)):
            value = spec.get(field)
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
                raise GateError('incomplete_task_proposal')
        if spec['request_quote'] not in last_user(transcript): raise GateError('task_quote_not_in_user_input')
        if spec.get('complexity') not in ('simple', 'medium', 'complex'):
            raise GateError('task_complexity_required')
        selection = self.advisor.normalize(spec.get('modelSelection'))
        identifier = uuid.uuid4().hex
        proposal = {'id': identifier, 'project_id': project['id'], 'project_title': project['title'],
                    'title': spec['title'].strip(), 'prompt': spec['prompt'].strip(),
                    'modelSelection': selection, 'complexity': spec['complexity'], 'reason': spec['reason'],
                    'request_quote': spec['request_quote'], 'proposal_user_text': last_user(transcript),
                    'conversation_id': conversation_id, 'revision': revision, 'state': 'proposed',
                    'thread_id': str(uuid.uuid4()), 'created_at': now()}
        self.jobs[identifier] = proposal
        self.c.store.save()
        return proposal, self.proposal_text(proposal)

    def proposal_text(self, proposal):
        return (f"Ich schlage vor: {proposal['project_title'][:80]}, {proposal['title']}. "
            f"Auftrag: {proposal['prompt'][:350]} " + self.advisor.describe(proposal['modelSelection']) +
            f" Grund: {proposal['reason'].split('. ', 1)[0][:180]}. Soll ich diesen Auftrag jetzt in einem neuen T3-Thread starten?")

    def resume_proposal(self, identifier, transcript, *, conversation_id, revision):
        proposal = self.jobs.get(identifier)
        if proposal is None or proposal['state'] != 'proposed':
            raise GateError('pending_task_proposal_required')
        if not self.c.in_scope(proposal['project_id']): raise GateError('task_project_no_longer_available')
        self.advisor.refresh(force=True)
        reply = self.proposal_text(proposal)
        proposal.update(conversation_id=conversation_id, revision=revision,
                        proposal_user_text='', resume_request_quote=last_user(transcript), resumed_at=now())
        self.c.store.save()
        return reply

    def confirm(self, identifier, transcript, *, revision, cancelled=lambda: False):
        job = self.jobs[identifier]
        if job['state'] == 'started': return self.started_reply(job)
        if job['state'] != 'proposed':
            self.reconcile(job)
            return self.started_reply(job) if job['state'] == 'started' else 'Der Start ist noch unbestätigt. Ich lege keinen zweiten Thread an.'
        if cancelled() or revision <= job['revision'] or last_user(transcript) == job['proposal_user_text']:
            raise GateError('fresh_task_confirmation_required')
        if not explicit_confirmation(last_user(transcript)): raise GateError('explicit_task_confirmation_required')
        self.advisor.refresh(force=True)
        self.advisor.validate(job['modelSelection'])
        project = next((p for p in self.c.projects() if p['id'] == job['project_id']), None)
        if project is None: raise GateError('task_project_no_longer_available')
        if project.get('workspaceRoot') and not Path(project['workspaceRoot']).is_dir():
            raise GateError('task_workspace_unavailable')
        if cancelled(): raise GateError('task_confirmation_cancelled')
        timestamp = now()
        job['confirmation_quote'] = last_user(transcript)
        job['create_command'] = {'type': 'thread.create', 'commandId': 'voice-create-'+identifier,
            'threadId': job['thread_id'], 'projectId': job['project_id'], 'title': job['title'],
            'modelSelection': job['modelSelection'], 'runtimeMode': 'full-access', 'interactionMode': 'default',
            'branch': None, 'worktreePath': None, 'createdAt': timestamp}
        job['message_id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, 'voice-task-'+identifier))
        prompt = ('Der Nutzer hat diesen Auftrag im Sprachgespräch ausdrücklich bestätigt. Setze ihn um und prüfe das Ergebnis. '
                  'Lies zuerst die geltenden AGENTS.md und Projektregeln. Bewahre bestehende Änderungen; verwende für Codeänderungen '
                  'einen isolierten Git-Worktree, soweit die Projektregeln dies vorsehen oder parallele Arbeit sonst kollidiert. '
                  'Keine zusätzlichen Produktivaktionen, Veröffentlichungen oder Nachrichten ohne entsprechende Beauftragung. '
                  'Stelle notwendige Rückfragen über die normalen T3-Rückfragen.\n\nAuftrag:\n' + job['prompt'])
        job['start_command'] = {'type': 'thread.turn.start', 'commandId': 'voice-start-'+identifier,
            'threadId': job['thread_id'], 'message': {'messageId': job['message_id'], 'role': 'user', 'text': prompt, 'attachments': []},
            'runtimeMode': 'full-access', 'interactionMode': 'default', 'createdAt': timestamp}
        job['state'] = 'creating'
        self.c.store.save()  # Commit the exact commands and IDs before any mutation.
        try:
            self.c.client.dispatch(job['create_command'])
            job['state'] = 'created'
            self.c.store.save()
            self._start(job)
        except GateError:
            self.reconcile(job)
        return self.started_reply(job) if job['state'] == 'started' else 'Der Thread-Start ist noch nicht bestätigt. Ich prüfe denselben Vorgang und lege keinen zweiten an.'

    def _start(self, job):
        job['state'] = 'starting'
        self.c.store.save()
        self.c.client.dispatch(job['start_command'])
        deadline = time.monotonic() + 3
        while True:
            self.reconcile(job)
            if job['state'] in ('started', 'failed') or time.monotonic() >= deadline: break
            time.sleep(.1)

    def reconcile(self, job):
        if job['state'] in ('proposed', 'cancelled', 'superseded', 'started', 'failed'): return
        try: source = self.c.client.snapshot(job['thread_id'])['thread']
        except GateError: return
        if source.get('projectId') != job['project_id']: raise GateError('created_task_project_mismatch')
        if source.get('archivedAt') or source.get('deletedAt'): return
        if any(m.get('id') == job.get('message_id') for m in source.get('messages', [])):
            state = (source.get('latestTurn') or {}).get('state')
            job['state'] = 'started' if state in ('running', 'completed') else 'failed' if state in ('error', 'interrupted') else 'accepted'
            self.c.store.save()
        elif job['state'] in ('creating', 'created'):
            # The start command has never been attempted. Continue the confirmed job once.
            self._start(job)

    def recover(self):
        for job in list(self.jobs.values()):
            self.reconcile(job)
            if job['state'] == 'started' and not job.get('notice_reserved'):
                job['notice_reserved'] = True
                self.c.store.save()
                self.c.queue_text(self.started_reply(job), purpose='task_started')

    def started_reply(self, job):
        return f"Der neue T3-Thread „{job['title']}“ im Projekt {job['project_title']} hat deinen Auftrag erhalten und ist gestartet."
