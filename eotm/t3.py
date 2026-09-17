"""T3 HTTP integration. All mutations use T3 commands, never its SQLite files."""
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import read_private_json
from .control import GateError
from .handoff import Handoff, prepare_handoff, prepare_answer, pending_requests
from .i18n import Locale


def now(): return datetime.now(timezone.utc).isoformat()


COORDINATOR_TITLE = "Employee of the Month - Call coordination"
LEGACY_COORDINATOR_TITLE = "Telefonbrücke - Gesprächskoordination"
SETTLE_TERMINAL = ("settled", "already_settled", "unavailable")


def is_coordinator_thread(thread):
    """Only threads the bridge created for call coordination carry this title prefix."""
    return isinstance(thread, dict) and str(thread.get("title", "")).startswith(
        (COORDINATOR_TITLE, LEGACY_COORDINATOR_TITLE))


def settle_command_id(coordinator_id, conversation_id, attempt):
    # T3 keeps a receipt per command ID and rejects a previously rejected ID for
    # good, so every attempt gets its own ID; the outcome stays idempotent.
    return "phone-settle-" + uuid.uuid5(uuid.NAMESPACE_URL,
        json.dumps([coordinator_id, conversation_id, int(attempt)])).hex


def blocking_requests(thread):
    """Open approvals or user-input requests; T3 refuses to settle a thread that has any."""
    open_ids = set()
    for activity in thread.get("activities", []):
        request_id = (activity.get("payload") or {}).get("requestId")
        if not isinstance(request_id, str): continue
        if activity.get("kind") in ("approval.requested", "user-input.requested"): open_ids.add(request_id)
        elif activity.get("kind") in ("approval.resolved", "user-input.resolved"): open_ids.discard(request_id)
    return open_ids


def recent_messages(thread):
    """Bounded recent task messages, excluding tool output and common secret forms."""
    messages=[m for m in thread.get('messages',[]) if m.get('role') in ('user','assistant') and not m.get('streaming')]
    selected=[]
    for message in messages[-4:]:
        text=message.get('text','')
        if not isinstance(text,str):continue
        text=re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----','[private key redacted]',text)
        text=re.sub(r'\bsk-[A-Za-z0-9_-]{20,}','[API key redacted]',text)
        text=re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~-]+','Bearer [redacted]',text)
        text=re.sub(r'(?im)^([^\n]*(?:api[_ -]?key|password|passphrase|secret|access[_ -]?token)\s*[:=])[^\n]*',r'\1 [redacted]',text)
        selected.append({'role':message['role'],'text':text[:2000]})
    return selected


def selected_context(thread):
    return json.dumps({'task_title':thread.get('title'),'recent_task_messages':recent_messages(thread)},ensure_ascii=False)[:11000]


def assistant_baseline(thread):
    return {m['id']:m.get('text','') for m in thread.get('messages',[]) if m.get('role')=='assistant' and m.get('id')}


def changed_assistant_reply(thread,baseline):
    replies=[m.get('text','') for m in thread.get('messages',[]) if m.get('role')=='assistant' and not m.get('streaming')
             and m.get('text') and baseline.get(m.get('id'))!=m.get('text')]
    return replies[-1] if replies else ''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise GateError("t3_redirect_rejected")


def validate_connection_files(origin, credentials_file, *, include_token=False):
    """Validate local T3 connection inputs without opening the network."""
    credential = Path(credentials_file).expanduser().resolve()
    fd = os.open(credential, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 8192):
            raise GateError("unsafe_t3_credentials_file")
        try: data = json.load(stream)
        except (TypeError, ValueError): raise GateError("invalid_t3_credentials_file") from None
        token = data.get("token") if isinstance(data, dict) else None
    T3Client(origin, token)
    config = {"origin": origin.rstrip("/"), "credentials_file": str(credential)}
    return (config, token) if include_token else config


class T3Client:
    def __init__(self, origin, token, *, opener=None):
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GateError("invalid_t3_origin")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise GateError("t3_remote_requires_https")
        if parsed.path not in ("", "/") or not isinstance(token, str) or not token:
            raise GateError("invalid_t3_connection")
        self.origin, self.token = origin.rstrip("/"), token
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    @classmethod
    def from_profile(cls, profile):
        config = read_private_json(profile, "t3.json")
        validated, token = validate_connection_files(
            config["origin"], config["credentials_file"], include_token=True)
        return cls(validated["origin"], token)

    def request(self, path, body=None):
        request = urllib.request.Request(self.origin + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
            headers={"Authorization":"Bearer " + self.token, "Content-Type":"application/json"})
        try:
            with self.opener.open(request, timeout=8) as response:
                data = response.read(4*1024*1024 + 1)
            if len(data) > 4*1024*1024: raise GateError("t3_response_too_large")
            return json.loads(data)
        except urllib.error.HTTPError as error:
            raise GateError("t3_http_" + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise GateError("t3_connection_or_response_failed") from None

    def snapshot(self, thread_id):
        if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",thread_id):
            raise GateError("invalid_t3_thread_id")
        result = self.request("/api/orchestration/threads/" + thread_id)
        if result.get("thread",{}).get("id") != thread_id: raise GateError("t3_snapshot_mismatch")
        return result

    def dispatch(self, command):
        result = self.request("/api/orchestration/dispatch", command)
        if type(result.get("sequence")) is not int: raise GateError("t3_dispatch_unconfirmed")
        return result

    def rpc(self, method, payload=None):
        """Read T3's authenticated provider catalog using its Effect RPC envelope."""
        if method not in ('server.getConfig', 'server.refreshProviders'):
            raise GateError('unsupported_t3_metadata_method')
        from websockets.sync.client import connect
        tag = uuid.uuid4().hex
        try:
            with connect(self.origin.replace('http', 'ws', 1) + '/ws',
                         additional_headers={'Authorization': 'Bearer ' + self.token},
                         open_timeout=8, close_timeout=2, max_size=8*1024*1024) as ws:
                ws.send(json.dumps({'_tag': 'Request', 'id': tag, 'tag': method,
                                    'payload': payload or {}, 'headers': []}))
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    event = json.loads(ws.recv(timeout=max(.1, deadline-time.monotonic())))
                    if event.get('requestId') != tag: continue
                    result = event.get('exit', {})
                    if result.get('_tag') != 'Success': raise GateError('t3_metadata_request_failed')
                    return result['value']
        except Exception:
            raise GateError('t3_metadata_unavailable') from None
        raise GateError('t3_metadata_timeout')

    def create_coordinator(self, source, *, title=COORDINATOR_TITLE, interaction_mode="default"):
        identifier = str(uuid.uuid4())
        self.dispatch({"type":"thread.create", "commandId":str(uuid.uuid4()), "threadId":identifier,
            "projectId":source["projectId"], "title":title,
            "modelSelection":source["modelSelection"], "runtimeMode":"approval-required", "interactionMode":interaction_mode,
            "branch":source.get("branch"), "worktreePath":source.get("worktreePath"), "createdAt":now()})
        return identifier

    def settle_coordinator(self, thread_id, command_id, *, wait=3, sleep=time.sleep):
        """Mark an ended call's coordination thread as settled in T3. Never touches work threads.

        Returns settled, already_settled, unavailable (archived or deleted), busy (a turn or
        session is still live), blocked (open approval or user input) or unconfirmed."""
        thread = self.snapshot(thread_id)["thread"]
        if not is_coordinator_thread(thread): raise GateError("t3_settle_target_not_coordinator")
        if thread.get("archivedAt") or thread.get("deletedAt"): return "unavailable"
        if thread.get("settledOverride") == "settled" and thread.get("settledAt"): return "already_settled"
        if ((thread.get("session") or {}).get("status") in ("starting", "running")
                or (thread.get("latestTurn") or {}).get("state") == "running"): return "busy"
        if blocking_requests(thread): return "blocked"
        self.dispatch({"type":"thread.settle", "commandId":command_id, "threadId":thread_id})
        deadline = time.monotonic() + wait
        while True:
            thread = self.snapshot(thread_id)["thread"]
            if thread.get("settledOverride") == "settled": return "settled"
            if time.monotonic() >= deadline: return "unconfirmed"
            sleep(0.2)

    def run_coordinator(self, thread_id, prompt, *, cancelled=lambda:False, timeout=75):
        previous_turn = (self.snapshot(thread_id)["thread"].get("latestTurn") or {}).get("turnId")
        message_id = str(uuid.uuid4())
        self.dispatch({"type":"thread.turn.start", "commandId":str(uuid.uuid4()), "threadId":thread_id,
            "message":{"messageId":message_id,"role":"user","text":prompt,"attachments":[]},
            "runtimeMode":"approval-required", "interactionMode":"default", "createdAt":now()})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not cancelled():
            thread = self.snapshot(thread_id)["thread"]
            messages = thread.get("messages",[])
            user = next((m for m in messages if m.get("id")==message_id),None)
            turn = thread.get("latestTurn") or {}
            if user and turn.get("turnId") and turn.get("turnId") != previous_turn:
                if turn.get("state") in ("error","interrupted"): raise GateError("t3_coordinator_failed")
                if turn.get("state") == "completed":
                    replies = [m.get("text","") for m in messages if m.get("role")=="assistant" and m.get("turnId")==turn["turnId"] and not m.get("streaming")]
                    if replies: return replies[-1]
            time.sleep(0.25)
        raise GateError("t3_coordinator_cancelled_or_timeout")

    def return_answer(self, handoff, answers, *, confirmed_by_user, cancelled=lambda:False):
        command = prepare_answer(handoff, self.snapshot(handoff.thread_id), answers,
                                 confirmed_by_user=confirmed_by_user)
        self.dispatch(command)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not cancelled():
            activities = self.snapshot(handoff.thread_id)["thread"].get("activities",[])
            relevant = [a for a in activities if (a.get("payload") or {}).get("requestId")==handoff.request_id]
            if any(a.get("kind")=="user-input.resolved" for a in relevant): return True
            if any(a.get("kind")=="provider.user-input.respond.failed" for a in relevant):
                raise GateError("t3_answer_rejected")
            time.sleep(0.2)
        raise GateError("t3_answer_completion_unconfirmed")

    def start_source_followup(self, thread_id, text, command_id):
        source=self.snapshot(thread_id)['thread']
        if source.get('deletedAt') or source.get('archivedAt') or (source.get('latestTurn')or{}).get('state')!='completed':
            raise GateError('t3_source_not_ready_for_followup')
        self.dispatch({'type':'thread.turn.start','commandId':command_id,'threadId':thread_id,
            'message':{'messageId':str(uuid.uuid5(uuid.NAMESPACE_URL,command_id)),'role':'user','text':text,'attachments':[]},
            'runtimeMode':source['runtimeMode'],'interactionMode':source['interactionMode'],'createdAt':now()})

    def wait_for_source_reply(self, thread_id, baseline, *, ignored_requests=(), cancelled=lambda:False, timeout=20):
        deadline=time.monotonic()+timeout
        while True:
            snapshot=self.snapshot(thread_id);source=snapshot['thread']
            fresh=[r for r in pending_requests(snapshot) if r not in ignored_requests]
            settled=(source.get('latestTurn')or{}).get('state') in ('completed','error','interrupted')
            if fresh or (settled and changed_assistant_reply(source,baseline)):return snapshot
            if cancelled() or time.monotonic()>=deadline:return snapshot
            time.sleep(.2)


def settle_after_call(client, coordinator_id, *, conversation_id, emit=lambda value:None,
                      attempts=5, sleep=time.sleep):
    """Bounded settle for single-call runners (call, watch-t3). A failure never fails the call."""
    if not coordinator_id: return None
    result = None
    for attempt in range(attempts):
        try: result = client.settle_coordinator(coordinator_id, settle_command_id(coordinator_id, conversation_id, attempt))
        except GateError as error:
            result = "error:" + str(error)
            if str(error) == "t3_settle_target_not_coordinator": break
        if result in SETTLE_TERMINAL: break
        if attempt + 1 < attempts: sleep(2)
    emit({"t3_coordinator_settle": result, "t3_coordinator_thread": coordinator_id})
    return result


def parse_coordinator(text, transcript):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"): text = text[7:-3].strip()
    try: result = json.loads(text)
    except (ValueError,TypeError): raise GateError("invalid_t3_coordinator_result") from None
    if not isinstance(result,dict):raise GateError("invalid_t3_coordinator_result")
    reply = result.get("reply")
    if not isinstance(reply,str) or not 1 <= len(reply) <= 1000:
        raise GateError("invalid_t3_coordinator_reply")
    answer = result.get("answer")
    if answer is not None:
        if not isinstance(answer,dict): raise GateError("invalid_t3_coordinator_answer")
        if answer.get('intent') not in ('decision','clarification'):
            raise GateError('explicit_response_intent_required')
        last_user = next((m.get("text","") for m in reversed(transcript) if m.get("role")=="user"), "")
        quote = answer.get("confirmation_quote")
        if answer.get("confirmed") is not True or not isinstance(quote,str) or not quote.strip() or quote not in last_user:
            raise GateError("voice_confirmation_not_in_transcript")
        if not isinstance(answer.get("answers"),dict): raise GateError("invalid_t3_coordinator_answer")
    return result


class T3Delegation:
    def __init__(self, client, thread_id, request_id, *, context=None, emit=lambda value:None,
                 structurer=None, locale=None):
        self.client, self.emit = client, emit
        self.structurer=structurer
        self.locale=locale or Locale()
        snapshot=client.snapshot(thread_id)
        self.context_override=context
        context=context or selected_context(snapshot["thread"])
        self.handoff,self.packet=prepare_handoff(snapshot,request_id,context=context)
        self.packet["instruction"] = self.locale.text("handoff_instruction")
        self.source=snapshot["thread"]
        self.thread_id=self.handoff.thread_id
        self.project_id=self.handoff.project_id
        self.coordinator_id=None
        self.completed=False
        self.cancelled=lambda:False
        self.revision=lambda:0
        self.submitted=False
        self.delivered_ids=set()
        self.request_ids_in_call={request_id}
        self.baseline=assistant_baseline(self.source)
        self.last_report=""
        self.adopted_revision=None
        self.deferred=False
        self.mutation_uncertain=False
        self.checkpoint=lambda:None

    def state(self):
        fields=('packet','source','thread_id','project_id','coordinator_id','completed',
                'submitted','baseline','last_report','adopted_revision','context_override',
                'deferred','mutation_uncertain')
        result = {**{k:getattr(self,k) for k in fields}, 'handoff':asdict(self.handoff),
                'delivered_ids':sorted(self.delivered_ids),
                'request_ids_in_call':sorted(self.request_ids_in_call)}
        result['source'] = {k:v for k,v in self.source.items() if k in (
            'id','projectId','modelSelection','runtimeMode','interactionMode','branch','worktreePath','title','latestTurn')}
        return result

    @classmethod
    def restore(cls, client, state, *, emit=lambda value:None):
        obj=cls.__new__(cls)
        obj.__dict__.update(state)
        obj.handoff=Handoff(**state['handoff'])
        obj.delivered_ids=set(state['delivered_ids'])
        obj.request_ids_in_call=set(state['request_ids_in_call'])
        obj.client,obj.emit=client,emit
        obj.structurer=None
        obj.locale=Locale()
        obj.cancelled=lambda:False
        obj.revision=lambda:0
        obj.checkpoint=lambda:None
        return obj

    def _coordinate(self,prompt):
        """One direct structuring request. The T3 coordination thread is only the slow fallback."""
        started=time.monotonic()
        if self.structurer is not None:
            try:
                result=self.structurer(prompt)
                self.emit({"structuring_seconds":round(time.monotonic()-started,2),"path":"direct"})
                return result
            except GateError as error:self.emit({"structurer_fallback":str(error)})
        started=time.monotonic()
        if self.coordinator_id is None:
            self.coordinator_id=self.client.create_coordinator(self.source)
            self.checkpoint()
            self.emit({"t3_coordinator_thread":self.coordinator_id})
        result=self.client.run_coordinator(self.coordinator_id,prompt,cancelled=self.cancelled)
        self.emit({"structuring_seconds":round(time.monotonic()-started,2),"path":"coordinator"})
        return result

    def _validate_source(self,snapshot):
        source=snapshot.get("thread",{})
        if source.get("id")!=self.thread_id or source.get("projectId")!=self.project_id:
            raise GateError("t3_handoff_target_mismatch")
        if source.get("archivedAt") or source.get("deletedAt"):
            raise GateError("t3_thread_unavailable")
        return source

    def _observe_source(self,snapshot):
        source=self._validate_source(snapshot)
        reply=changed_assistant_reply(source,self.baseline)
        self.source=source
        if reply:self.packet["source_reply"]=reply[:3000]
        fresh=[r for r in pending_requests(snapshot) if r not in self.delivered_ids]
        if fresh:
            self.handoff,self.packet=prepare_handoff(snapshot,fresh[0],
                context=self.context_override or selected_context(source))
            self.packet["instruction"] = self.locale.text("handoff_instruction")
            if reply:self.packet["source_reply"]=reply[:3000]
            self.request_ids_in_call.add(fresh[0])
            self.submitted=False;self.completed=False
            self.adopted_revision=self.revision()
            questions=" ".join(q["question"] for q in self.packet["questions"])
            self.last_report=(self.locale.text('task_explained_new_question', reply=reply[:650], questions=questions)
                              if reply else self.locale.text('new_question', questions=questions))
            return self.last_report[:1100]
        if self.submitted:
            self.packet["request_open"]=False
            self.packet["source_state"]=(source.get("latestTurn")or{}).get("state")
        if reply:
            self.last_report=self.locale.text('source_reply_decision' if self.completed else
                'source_reply_clarification', reply=reply)
        return None

    def __call__(self, transcript, *, revision=None):
        revision=self.revision() if revision is None else revision
        if self.cancelled():return self.locale.text('call_ended')
        if self.mutation_uncertain:
            return self.locale.text('mutation_unconfirmed')
        try:
            if self.submitted:
                update=self._observe_source(self.client.snapshot(self.thread_id))
                if update:return update
                if self.completed:return self.last_report[:1100] or self.locale.text('decision_arrived')
            if self.adopted_revision is not None and revision<=self.adopted_revision:
                return self.last_report[:1100]
            prompt=self.locale.t3_dialog_prompt({"handoff":self.packet,"transcript":transcript},
                                                channel=getattr(self,'channel','voice'))
            raw=self._coordinate(prompt)
            result=parse_coordinator(raw,transcript)
            if result.get('action')=='defer' and result.get('answer') is None:
                self.deferred=True
                self.checkpoint()
                return self.locale.text('deferred')
            if result.get("answer") is None:return result["reply"]
            if self.cancelled():return self.locale.text('input_not_sent_call_ended')
            if self.revision()!=revision:
                return self.locale.text('new_speech')
            answer=result["answer"];intent=answer["intent"]
            latest=self.client.snapshot(self.thread_id);source=self._validate_source(latest)
            answers=answer["answers"]
            if set(answers)!={q["id"] for q in self.packet["questions"]} or any(not isinstance(v,str) or not v.strip() or len(v)>8000 for v in answers.values()):
                raise GateError("t3_answer_question_mismatch")
            self.baseline=assistant_baseline(source)
            if self.submitted:
                # The previous Ask has been answered with a clarification. Its ID
                # must never be reused; a confirmed follow-up is a normal T3 turn.
                if pending_requests(latest):
                    return self._observe_source(latest) or self.locale.text('new_question_short')
                if (source.get("latestTurn")or{}).get("state")!="completed":
                    return self.locale.text('source_not_ready')
                label=self.locale.text('decision_label' if intent=="decision" else 'clarification_label')
                message=self.locale.text('followup_payload', label=label,
                                         answers=json.dumps(answers,ensure_ascii=False))
                command_id="phone-followup-"+uuid.uuid5(uuid.NAMESPACE_URL,self.handoff.id+json.dumps([intent,answers],sort_keys=True)).hex
                self.mutation_uncertain=True
                self.checkpoint()
                self.client.start_source_followup(self.thread_id,message,command_id)
                self.emit({"t3_followup_submitted":True,"response_intent":intent,"t3_thread_id":self.thread_id})
            else:
                self.mutation_uncertain=True
                self.checkpoint()
                self.client.return_answer(self.handoff,answers,confirmed_by_user=True,cancelled=self.cancelled)
                self.delivered_ids.add(self.handoff.request_id)
                self.emit({"t3_input_resolved":True,"t3_answer_resolved":intent=="decision",
                    "t3_clarification_returned":intent=="clarification","response_intent":intent,"t3_thread_id":self.thread_id})
            self.submitted=True
            self.completed=intent=="decision"
            self.mutation_uncertain=False
            self.checkpoint()
            self.adopted_revision=None
            self.last_report=self.locale.text('decision_arrived' if self.completed else 'clarification_delivered')
            updated=self.client.wait_for_source_reply(self.thread_id,self.baseline,
                ignored_requests=self.delivered_ids,cancelled=self.cancelled)
            new_question=self._observe_source(updated)
            return new_question or self.last_report[:1100]
        except GateError as error:
            self.emit({"t3_delegation_error":str(error)})
            return self.locale.text('submitted_unconfirmed' if self.submitted else 'return_unconfirmed')
