"""Bounded, private text context for recent calls. Never stores audio or credentials."""
import copy
import json
import re
import threading
from datetime import datetime, timezone

from .config import read_private_json
from .daemon import atomic_private

MAX_CALLS = 8
MAX_TRANSCRIPT_CHARS = 24000


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def bounded_transcript(transcript):
    messages = []
    for message in transcript:
        if message.get('role') not in ('user', 'assistant') or not isinstance(message.get('text'), str): continue
        text = message['text']
        text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----', '[Schlüssel ausgeblendet]', text)
        text = re.sub(r'\bsk-[A-Za-z0-9_-]{20,}', '[API-Key ausgeblendet]', text)
        text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~-]+', 'Bearer [ausgeblendet]', text)
        text = re.sub(r'(?im)^([^\n]*(?:api[_ -]?key|password|passphrase|secret|access[_ -]?token)\s*[:=])[^\n]*', r'\1 [ausgeblendet]', text)
        messages.append({'role': message['role'], 'text': text[:8000]})
    # Preserve the initial subject and the latest exchange; structured task state
    # separately retains actual proposals, confirmations and T3 IDs.
    result = messages[:2]
    remaining = MAX_TRANSCRIPT_CHARS - sum(len(m['text']) for m in result)
    tail = []
    for message in reversed(messages[2:]):
        if remaining <= 0: break
        value = {**message, 'text': message['text'][-remaining:]}
        tail.append(value); remaining -= len(value['text'])
    return result + list(reversed(tail))


class CallHistory:
    def __init__(self, profile, *, background=True):
        self.path = profile/'call-history.json'
        self.lock = threading.RLock()
        self.flush_lock = threading.Lock()
        self.changed = threading.Event()
        self.stopped = threading.Event()
        self.writer = None
        self.background = background
        self.error = None
        self.data = read_private_json(profile, self.path.name, max_bytes=2*1024*1024) if self.path.exists() or self.path.is_symlink() else {'version': 1, 'calls': []}
        if self.data.get('version') != 1 or not isinstance(self.data.get('calls'), list):
            raise ValueError('invalid_call_history')
        for call in self.data['calls']:
            if call.get('state') == 'active': call['state'] = 'interrupted'

    def begin(self, identifier, **metadata):
        with self.lock:
            self.data['calls'].append({'id': identifier, 'started_at': timestamp(), 'state': 'active',
                                       'transcript': [], **metadata})
            self.data['calls'] = self.data['calls'][-MAX_CALLS:]
            self.changed.set()
            if self.background and self.writer is None:
                self.writer = threading.Thread(target=self._write_loop, name='call-context-writer', daemon=True)
                self.writer.start()

    def update(self, identifier, *, transcript=None, **metadata):
        with self.lock:
            call = next((c for c in self.data['calls'] if c['id'] == identifier), None)
            if call is None or self.stopped.is_set(): return
            if transcript is not None: call['transcript'] = bounded_transcript(transcript)
            call.update(metadata)
            self.changed.set()

    def finish(self, identifier, status):
        self.update(identifier, state='ended', ended_at=timestamp(), end_status=dict(status))

    def settle_candidates(self):
        """Ended or interrupted calls whose status coordinator was never handed to the settle queue."""
        with self.lock:
            return [(c['id'], c['coordinator_id']) for c in self.data['calls']
                    if c.get('state') != 'active' and c.get('coordinator_id') and not c.get('coordinator_settle')]

    def recent(self, exclude=None):
        with self.lock:
            calls = copy.deepcopy([c for c in self.data['calls'] if c['id'] != exclude][-5:])
        for call in calls:
            messages = call.get('transcript', [])
            selected = messages if len(messages) <= 8 else messages[:2]+messages[-6:]
            call['transcript'] = [{**m, 'text': m['text'][:400]} for m in selected]
        return calls

    def flush(self):
        with self.flush_lock:
            with self.lock:
                self.changed.clear()
                data = json.dumps(self.data, ensure_ascii=False).encode()
            try:
                atomic_private(self.path, data)
                self.error = None
            except Exception:
                self.error = 'call_history_write_failed'
                self.changed.set()
                raise

    def _write_loop(self):
        while not self.stopped.is_set():
            if not self.changed.wait(.5): continue
            try: self.flush()
            except Exception: pass
            self.stopped.wait(1)

    def close(self):
        self.stopped.set()
        self.changed.set()
        if self.writer: self.writer.join(timeout=3)
        self.flush()
