"""Explicit existing-thread messages, independent of Ask and task proposals."""
import hashlib
import re
import uuid

from .control import GateError
from .handoff import pending_requests
from .t3 import now


def parse_followup(text):
    # Explicit one-shot form. Contextual follow-ups are resolved separately and
    # may only use an exact thread mention from the current call.
    if re.match(
            r'(?is)^\s*(?:nein[,\s]+)?(?:bitte\s+)?'
            r'(?:sende|schicke|schick|sag|sage|schreib|schreibe|übermittle|antworte)\s+'
            r'(?:bitte\s+)?(?:nicht|nichts|keine)\b', text):
        return None
    match = re.fullmatch(
        r'(?is)\s*(?:bitte\s+)?(?:sende|schicke|schick|sag|sage|schreib|schreibe|übermittle|antworte)'
        r'\s+(?:(?:die|eine)\s+(?:Folgenachricht|Nachricht)\s+)?'
        r'(?:(?:an|in)\s+(?:den\s+)?|dem\s+)?(?:bestehenden\s+)?Thread\s+'
        r'(?P<target>.+?)\s*(?::|,\s*(?:dass|Folgenachricht)\s+|\s+(?:mit der|die) Nachricht\s+)\s*(?P<message>.+?)\s*', text)
    if not match:
        intent = (re.search(
            r'(?is)\b(?:sende|schicke|schick|sag|sage|schreib|schreibe|übermittle|antworte)\b.*\bthreads?\b', text)
            or re.search(r'(?is)^\s*(?:eine|die)\s+(?:Nachricht|Folgenachricht)\b.*\bthreads?\b', text)
            or re.search(r'(?is)^\s*kannst\s+du\b.*\bthreads?\b', text))
        return {} if intent else None
    target = match['target'].strip().strip('"„“»«')
    return {'target': target, 'text': match['message'].strip()}


def contextual_send(text):
    """Return (intent, payload) for a deictic send request in the current utterance."""
    verb = r'(?:sende|schicke|schick|sag|sage|schreib|schreibe|übermittle)'
    there = r'(?:dort|dahin|dorthin|da\s+hin)'
    if re.match(
            rf'(?is)^\s*(?:nein[,\s]+)?(?:bitte\s+)?{verb}\s+'
            r'(?:bitte\s+)?(?:nicht|nichts|keine)\b', text):
        return False, None
    if re.search(
            rf'(?is)\b{verb}\s+(?:bitte\s+)?{there}\s+'
            r'(?:nichts|keine\s+(?:Nachricht|Folgenachricht))\b', text):
        return False, None
    match = re.search(
        rf'(?is)\b{verb}\s+(?:bitte\s+)?'
        rf'(?:(?:die|eine)\s+(?:Nachricht|Folgenachricht)\s+)?{there}\s+(?P<message>.+?)\s*$', text)
    if not match:
        match = re.search(
            rf'(?is)\b{verb}\s+(?:bitte\s+)?'
            rf'(?P<message>.+?)\s+{there}(?:\s+hin)?[.!?]?\s*$', text)
    if match:
        message = match['message'].strip()
        structural = re.fullmatch(r'(?is)(?P<message>\S+)\s+hin[.!?]?', message)
        if structural and structural['message'].casefold() not in {'geh', 'komm'}:
            message = structural['message'].strip()
        return True, message
    intent = bool(re.search(rf'(?is)\b{verb}\b', text) and re.search(rf'(?is)\b{there}\b', text))
    return intent, None


def contextual_message(text):
    match = re.search(
        r'(?is)\b(?:die\s+)?(?:Nachricht|der\s+Text|Text|der\s+Inhalt|Inhalt)\s+'
        r'(?:ist|lautet|soll(?:\s+dort)?\s+sein)\s*[:,-]?\s*(?P<message>.+?)\s*$', text)
    return match['message'].strip() if match else None


def contextual_confirmation(text):
    normalized = ' '.join(re.sub(r'[.,!?]', ' ', text.casefold()).split())
    return normalized in {
        'mach das', 'mach es', 'tu das', 'ja mach das', 'ja mach es', 'ja tu das',
        'schick sie', 'schick sie ab', 'sende sie', 'ja schick sie', 'ja sende sie',
    }


def contextual_cancel(text):
    normalized = ' '.join(re.sub(r'[.,!?]', ' ', text.casefold()).split())
    return normalized in {'nein', 'abbrechen', 'doch nicht', 'lass das', 'nicht senden', 'nichts senden'}


class Followups:
    def __init__(self, conversations):
        self.c = conversations
        self.entries = conversations.data.setdefault('followups', {})

    def handle(self, text, event_id, *, cancelled=lambda: False):
        spec = parse_followup(text)
        if spec is None:
            return None
        if not spec:
            mentions = self.mentioned_targets(text)
            if len(mentions) == 1:
                return f'Welche Nachricht soll ich an den T3-Thread „{mentions[0].get("title")}“ senden?'
            if len(mentions) > 1:
                return self.ambiguous_reply(mentions)
            return 'Welchen T3-Thread meinst du, und welche Nachricht soll ich dorthin senden?'
        return self.handle_spec(spec, text, event_id, cancelled=cancelled)

    def mentioned_targets(self, text):
        shell = self.c.client.request('/api/orchestration/shell')
        projects = {p['id']: p for p in self.c.projects(shell)}
        folded = text.casefold()
        result = []
        for thread in shell.get('threads', []):
            if thread.get('projectId') not in projects or self.c.is_internal(thread):
                continue
            identifier = str(thread.get('id', ''))
            title = str(thread.get('title', ''))
            id_match = bool(identifier and re.search(r'(?<!\w)' + re.escape(identifier.casefold()) + r'(?!\w)', folded))
            title_match = bool(title and re.search(
                r'(?<!\w)' + re.escape(title.casefold()) + r'(?!\w)', folded))
            if id_match or title_match:
                result.append({**thread, 'project_title': projects[thread['projectId']].get('title', thread['projectId'])})
        return result

    @staticmethod
    def ambiguous_reply(candidates):
        choices = '\n'.join(
            f"{t['id']}: {t.get('title')} ({t.get('project_title', t['projectId'])})" for t in candidates)
        return 'Mehrere Threads passen. Welchen davon meinst du? Bitte nenne die eindeutige Thread-ID.\n' + choices

    def handle_spec(self, spec, input_text, event_id, *, cancelled=lambda: False):
        if (not isinstance(spec, dict) or not isinstance(spec.get('target'), str)
                or not isinstance(spec.get('text'), str) or not spec['text'].strip()
                or len(spec['text']) > 8000):
            raise GateError('invalid_followup_spec')
        key = hashlib.sha256(event_id.encode()).hexdigest()
        existing = self.entries.get(key)
        if existing:
            if existing['input'] != input_text:
                raise GateError('followup_event_conflict')
            return self.receipt(existing)
        shell = self.c.client.request('/api/orchestration/shell')
        projects = {p['id']: p for p in self.c.projects(shell)}
        candidates = [t for t in shell.get('threads', []) if t.get('projectId') in projects
                      and not t.get('deletedAt') and not t.get('archivedAt') and not self.c.is_internal(t)
                      and spec['target'].casefold() in (t['id'].casefold(), t.get('title', '').casefold())]
        if len(candidates) != 1:
            choices = [{**t, 'project_title': projects[t['projectId']].get('title', t['projectId'])} for t in candidates]
            return (self.ambiguous_reply(choices) if candidates
                    else 'Diesen T3-Thread kann ich nicht eindeutig finden. Es wurde nichts gesendet.')
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
        entry = {'input': input_text, 'thread_id': source['id'], 'project_id': source['projectId'],
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
