"""Explicit existing-thread messages, independent of Ask and task proposals."""
import hashlib
import re
import uuid

from .control import GateError
from .handoff import pending_requests
from .t3 import now


def parse_followup(text):
    # Deliberately require a message boundary. Never guess payload or target from history.
    intent = (re.search(r'(?i)\bthreads?\b', text) and re.search(
        r'(?i)\b(?:sende|senden|schicke|schicken|schick|sag|sage|sagen|schreib|schreibe|schreiben|übermittle|übermitteln|antworte|antworten|weiterleiten|weitergeben|Nachricht|Folgenachricht)\b', text))
    if not intent:
        return None
    match = re.fullmatch(
        r'(?is)\s*(?:bitte\s+)?(?:sende|schicke|schick|sag|sage|schreib|schreibe|übermittle|antworte)'
        r'\s+(?:(?:die|eine)\s+(?:Folgenachricht|Nachricht)\s+)?'
        r'(?:(?:an|in)\s+(?:den\s+)?|dem\s+)?(?:bestehenden\s+)?Thread\s+'
        r'(?P<target>.+?)\s*(?::|,\s*(?:dass|Folgenachricht)\s+|\s+(?:mit der|die) Nachricht\s+)\s*(?P<message>.+?)\s*', text)
    if not match:
        return {}
    target = match['target'].strip().strip('"„“»«')
    return {'target': target, 'text': match['message'].strip()}


class Followups:
    def __init__(self, conversations):
        self.c = conversations
        self.entries = conversations.data.setdefault('followups', {})

    def handle(self, text, event_id, *, cancelled=lambda: False):
        spec = parse_followup(text)
        if spec is None:
            return None
        if not spec or len(spec['text']) > 8000:
            return 'Bitte nenne Ziel und Nachricht ausdrücklich: Sende an Thread „vollständiger Titel oder Thread-ID“: deine Nachricht.'
        key = hashlib.sha256(event_id.encode()).hexdigest()
        existing = self.entries.get(key)
        if existing:
            if existing['input'] != text:
                raise GateError('followup_event_conflict')
            return self.receipt(existing)
        shell = self.c.client.request('/api/orchestration/shell')
        projects = {p['id']: p for p in self.c.projects(shell)}
        candidates = [t for t in shell.get('threads', []) if t.get('projectId') in projects
                      and not t.get('deletedAt') and not t.get('archivedAt') and not self.c.is_internal(t)
                      and spec['target'].casefold() in (t['id'].casefold(), t.get('title', '').casefold())]
        if len(candidates) != 1:
            choices = '\n'.join(f"{t['id']}: {t.get('title')} ({projects[t['projectId']].get('title', t['projectId'])})" for t in candidates)
            return ('Mehrere Threads passen. Bitte wiederhole die Folgenachricht mit der eindeutigen Thread-ID.\n' + choices
                    if candidates else 'Diesen Thread kann ich nicht eindeutig finden. Bitte wiederhole die Folgenachricht mit dem vollständigen Titel oder der Thread-ID.')
        target = candidates[0]
        snapshot = self.c.client.snapshot(target['id'])
        source = snapshot['thread']
        if (source.get('id') != target['id'] or source.get('projectId') != target['projectId']
                or source.get('title') != target.get('title') or source.get('deletedAt') or source.get('archivedAt')):
            raise GateError('followup_target_changed')
        if pending_requests(snapshot) or source.get('hasPendingUserInput') or target.get('hasPendingUserInput'):
            return 'Dieser Thread hat eine offene Rückfrage. Bitte beantworte sie über den zugehörigen Vorgang. Die Folgenachricht wurde nicht gesendet.'
        if cancelled():
            return 'Die Eingabe hat sich geändert. Es wurde nichts übertragen.'
        # A new utterance after an uncertain send must not bypass the durable reservation.
        uncertain = next((e for e in self.entries.values() if e['thread_id'] == source['id']
                          and e['command']['message']['text'] == spec['text'] and e['state'] != 'received'), None)
        if uncertain:
            return self.receipt(uncertain)
        command_id = 'voice-followup-' + key
        entry = {'input': text, 'thread_id': source['id'], 'project_id': source['projectId'],
                 'title': source.get('title'), 'state': 'reserved',
                 'command': {'type': 'thread.turn.start', 'commandId': command_id, 'threadId': source['id'],
                             'message': {'messageId': str(uuid.uuid5(uuid.NAMESPACE_URL, command_id)),
                                         'role': 'user', 'text': spec['text'], 'attachments': []},
                             'runtimeMode': source['runtimeMode'], 'interactionMode': source['interactionMode'],
                             'createdAt': now()}}
        self.entries[key] = entry
        self.c.store.save()  # Never retry an uncertain dispatch, including after a restart.
        try:
            self.c.client.dispatch(entry['command'])
        except GateError:
            pass
        return self.receipt(entry)

    def receipt(self, entry):
        if entry['state'] != 'received':
            try:
                source = self.c.client.snapshot(entry['thread_id'])['thread']
                if source.get('id') != entry['thread_id'] or source.get('projectId') != entry['project_id']:
                    raise GateError('followup_receipt_target_mismatch')
                message = entry['command']['message']
                if any(m.get('id') == message['messageId'] and m.get('role') == 'user'
                       and m.get('text') == message['text'] for m in source.get('messages', [])):
                    entry['state'] = 'received'
                    self.c.store.save()
            except GateError:
                pass
        if entry['state'] == 'received':
            return f'Deine Folgenachricht ist im bestehenden T3-Thread „{entry["title"]}“ angekommen. Die fachliche Bearbeitung ist damit noch nicht bestätigt.'
        return 'Die Zustellung ist noch unbestätigt. Ich sende nicht erneut und lege keinen zweiten Thread oder Auftrag an.'
