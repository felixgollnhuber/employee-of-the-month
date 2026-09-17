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
            r'(?:(?:do\s+not|don\x27t)\s+)?(?:send|tell|write|message|reply|sende|schicke|schick|sag|sage|schreib|schreibe|übermittle|antworte)\s+'
            r'(?:bitte\s+|please\s+)?(?:nicht|nichts|keine|nothing|no\s+message)\b', text):
        return None
    match = re.fullmatch(
        r'(?is)\s*(?:(?:bitte|please)\s+)?(?:send|tell|write|message|reply|sende|schicke|schick|sag|sage|schreib|schreibe|übermittle|antworte)'
        r'\s+(?:(?:die|eine|a|the)\s+(?:Folgenachricht|Nachricht|follow-up|message)\s+)?'
        r'(?:(?:an|in|to)\s+(?:(?:den|the)\s+)?|dem\s+)?(?:(?:den|the)\s+)?(?:bestehenden\s+|existing\s+)?Thread\s+'
        r'(?P<target>.+?)\s*(?::|,\s*(?:dass|Folgenachricht|saying|that)\s+|\s+(?:mit der|die|with the) (?:Nachricht|message)\s+)\s*(?P<message>.+?)\s*', text)
    if not match:
        intent = (re.search(
            r'(?is)\b(?:send|tell|write|message|reply|sende|schicke|schick|sag|sage|schreib|schreibe|übermittle|antworte)\b.*\bthreads?\b', text)
            or re.search(r'(?is)^\s*(?:eine|die|a|the)\s+(?:Nachricht|Folgenachricht|message|follow-up)\b.*\bthreads?\b', text)
            or re.search(r'(?is)^\s*(?:kannst\s+du|can\s+you)\b.*\bthreads?\b', text))
        return {} if intent else None
    target = match['target'].strip().strip('"„“»«')
    return {'target': target, 'text': match['message'].strip()}


def contextual_send(text):
    """Return (intent, payload) for a deictic send request in the current utterance."""
    verb = r'(?:send|tell|write|message|sende|schicke|schick|sag|sage|schreib|schreibe|übermittle)'
    there = r'(?:there|to\s+it|dort|dahin|dorthin|da\s+hin)'
    if re.match(
            rf'(?is)^\s*(?:nein[,\s]+)?(?:bitte\s+)?{verb}\s+'
            r'(?:(?:bitte|please)\s+)?(?:nicht|nichts|keine|nothing|no\s+message)\b', text):
        return False, None
    if re.search(
            rf'(?is)\b{verb}\s+(?:bitte\s+)?{there}\s+'
            r'(?:nicht\b|nichts\b|keine\s+(?:Nachricht|Folgenachricht)\b|not\b|nothing\b|no\s+(?:message|follow-up)\b)', text):
        return False, None
    match = re.search(
        rf'(?is)\b{verb}\s+(?:bitte\s+)?'
            rf'(?:(?:die|eine|the|a)\s+(?:Nachricht|Folgenachricht|message|follow-up)\s+)?{there}\s+(?P<message>.+?)\s*$', text)
    if not match:
        match = re.search(
            rf'(?is)\b{verb}\s+(?:bitte\s+)?'
            rf'(?P<message>.+?)\s+{there}(?:\s+hin)?[.!?]?\s*$', text)
    if match:
        message = match['message'].strip()
        structural = re.fullmatch(r'(?is)(?P<message>\S+)\s+hin\.', message)
        if structural:
            message = structural['message'].strip()
        return True, message
    intent = bool(re.search(rf'(?is)\b{verb}\b', text) and re.search(rf'(?is)\b{there}\b', text))
    return intent, None


def contextual_message(text):
    match = re.search(
        r'(?is)\b(?:(?:die|the)\s+)?(?:Nachricht|der\s+Text|Text|der\s+Inhalt|Inhalt|message|content)\s+'
        r'(?:ist|lautet|soll(?:\s+dort)?\s+sein|is|says|should\s+be)\s*[:,-]?\s*(?P<message>.+?)\s*$', text)
    return match['message'].strip() if match else None


def contextual_confirmation(text):
    normalized = ' '.join(re.sub(r'[.,!?]', ' ', text.casefold()).split())
    return normalized in {
        'mach das', 'mach es', 'tu das', 'ja mach das', 'ja mach es', 'ja tu das',
        'schick sie', 'schick sie ab', 'sende sie', 'ja schick sie', 'ja sende sie',
        'do it', 'yes do it', 'send it', 'yes send it', 'go ahead', 'yes go ahead',
    }


def contextual_cancel(text):
    normalized = ' '.join(re.sub(r'[.,!?]', ' ', text.casefold()).split())
    return normalized in {'nein', 'abbrechen', 'doch nicht', 'lass das', 'nicht senden', 'nichts senden',
                          'no', 'cancel', 'never mind', 'do not send', 'don\x27t send', 'send nothing'}


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
                return self.c.locale.text('ask_message', title=mentions[0].get('title'))
            if len(mentions) > 1:
                return self.ambiguous_reply(mentions)
            return self.c.locale.text('ask_thread_and_message')
        return self.handle_spec(spec, text, event_id, cancelled=cancelled)

    def mentioned_targets(self, text):
        shell = self.c.client.request('/api/orchestration/shell')
        projects = {p['id']: p for p in self.c.projects(shell)}
        folded = text.casefold()
        result = []
        for thread in shell.get('threads', []):
            if (thread.get('projectId') not in projects or self.c.is_internal(thread)
                    or thread.get('deletedAt') or thread.get('archivedAt')):
                continue
            identifier = str(thread.get('id', ''))
            title = str(thread.get('title', ''))
            id_match = bool(identifier and re.search(
                r'(?<![A-Za-z0-9_-])' + re.escape(identifier.casefold()) + r'(?![A-Za-z0-9_-])', folded))
            title_folded = title.casefold()
            bare = folded.strip().strip('"„“»« .!?') == title_folded
            quoted = bool(title and re.search(
                r'["„“»«]\s*' + re.escape(title_folded) + r'\s*["„“»«]', folded))
            addressed = bool(title and re.search(
                r'\bthreads?\b\s*["„“»«]?\s*' + re.escape(title_folded) + r'(?!\w)', folded))
            title_match = bare or quoted or addressed
            if id_match or title_match:
                result.append({**thread, 'project_title': projects[thread['projectId']].get('title', thread['projectId'])})
        return result

    def matching_targets(self, target):
        shell = self.c.client.request('/api/orchestration/shell')
        projects = {p['id']: p for p in self.c.projects(shell)}
        return [{**thread, 'project_title': projects[thread['projectId']].get('title', thread['projectId'])}
                for thread in shell.get('threads', []) if thread.get('projectId') in projects
                and not thread.get('deletedAt') and not thread.get('archivedAt') and not self.c.is_internal(thread)
                and target.casefold() in (thread['id'].casefold(), thread.get('title', '').casefold())]

    def ambiguous_reply(self, candidates):
        choices = '\n'.join(
            f"{t['id']}: {t.get('title')} ({t.get('project_title', t['projectId'])})" for t in candidates)
        return self.c.locale.text('ambiguous_threads', choices=choices)

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
        candidates = self.matching_targets(spec['target'])
        if len(candidates) != 1:
            return (self.ambiguous_reply(candidates) if candidates
                    else self.c.locale.text('followup_not_found'))
        target = candidates[0]
        snapshot = self.c.client.snapshot(target['id'])
        source = snapshot['thread']
        if (source.get('id') != target['id'] or source.get('projectId') != target['projectId']
                or source.get('title') != target.get('title') or source.get('deletedAt') or source.get('archivedAt')):
            raise GateError('followup_target_changed')
        if pending_requests(snapshot) or source.get('hasPendingUserInput') or target.get('hasPendingUserInput'):
            return self.c.locale.text('followup_open_question')
        if cancelled():
            return self.c.locale.text('followup_changed')
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
            return self.c.locale.text('followup_received', title=entry['title'])
        return self.c.locale.text('followup_unconfirmed')
