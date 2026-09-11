"""Bind one spoken answer to one pending T3 question, independently of voice transport.

Pure preparation only. The caller reads snapshots through T3's HTTP API and
dispatches the returned command there. Never edit T3's database or resume its
provider session in a second runtime.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import uuid

from .control import GateError


def _text(value, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GateError("invalid_handoff_field")
    return value


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _pending(snapshot, request_id):
    thread = snapshot.get("thread", {})
    if not isinstance(thread, dict) or thread.get("deletedAt") or thread.get("archivedAt"):
        raise GateError("t3_thread_unavailable")
    pending = None
    activities = sorted(thread.get("activities", []), key=lambda a: (
        type(a.get("sequence")) is int, a.get("sequence", 0), a.get("createdAt", ""),
        0 if a.get("kind") == "user-input.requested" else 1, a.get("id", "")))
    for activity in activities:
        payload = activity.get("payload") or {}
        if payload.get("requestId") != request_id:
            continue
        if activity.get("kind") == "user-input.requested":
            pending = activity
        elif activity.get("kind") in ("user-input.resolved", "provider.user-input.respond.failed"):
            pending = None
    if pending is None:
        raise GateError("t3_question_no_longer_pending")
    questions = pending["payload"].get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 10:
        raise GateError("invalid_handoff_questions")
    ids = [_text(q.get("id")) for q in questions]
    if len(set(ids)) != len(ids):
        raise GateError("ambiguous_handoff_questions")
    for q in questions:
        _text(q.get("question"), 8000)
    return thread, pending, questions


def pending_requests(snapshot):
    identifiers={(a.get("payload") or {}).get("requestId")
        for a in snapshot.get("thread",{}).get("activities",[]) if a.get("kind")=="user-input.requested"}
    result=[]
    for identifier in sorted(x for x in identifiers if isinstance(x,str)):
        try:_pending(snapshot,identifier)
        except GateError:continue
        result.append(identifier)
    return result


@dataclass(frozen=True)
class Handoff:
    id: str
    thread_id: str
    project_id: str
    request_id: str
    activity_id: str
    question_fingerprint: str


def prepare_handoff(snapshot, request_id, *, context):
    """Context is a caller-selected summary, never an automatic full-history dump."""
    _text(request_id)
    _text(context, 12000)
    thread, pending, questions = _pending(snapshot, request_id)
    handoff = Handoff(str(uuid.uuid4()), _text(thread.get("id")),
                      _text(thread.get("projectId")), request_id,
                      _text(pending.get("id")), _fingerprint(questions))
    packet = {
        "handoff_id": handoff.id, "source": "t3", "thread_id": handoff.thread_id,
        "thread_title": _text(thread.get("title"), 1000),
        "request_id": request_id, "context": context,
        "questions": json.loads(json.dumps(questions)),
        "instruction": "Besprich diese Rückfrage. Bestätige die verstandene Antwort mit dem Nutzer. "
                       "Gib sie ausschließlich für diese Rückfrage zurück; führe keine Projektänderungen selbst aus.",
    }
    return handoff, packet


def prepare_answer(handoff, snapshot, answers, *, confirmed_by_user=False):
    """Prepare T3's actual user-input response command; never infer approval."""
    if confirmed_by_user is not True:
        raise GateError("spoken_answer_confirmation_required")
    thread, pending, questions = _pending(snapshot, handoff.request_id)
    if thread.get("id") != handoff.thread_id or thread.get("projectId") != handoff.project_id:
        raise GateError("t3_handoff_target_mismatch")
    if pending.get("id") != handoff.activity_id or _fingerprint(questions) != handoff.question_fingerprint:
        raise GateError("t3_question_changed_during_call")
    if not isinstance(answers, dict) or set(answers) != {q["id"] for q in questions}:
        raise GateError("t3_answer_question_mismatch")
    for value in answers.values():
        _text(value, 8000)
    # Reuse this complete command for transport retries. A dispatch receipt only
    # confirms acceptance; wait for T3's user-input.resolved event for completion.
    command_id = "phone-answer-" + _fingerprint([handoff.id, answers])
    return {"type": "thread.user-input.respond", "commandId": command_id,
            "threadId": handoff.thread_id, "requestId": handoff.request_id,
            "answers": dict(answers), "createdAt": datetime.now(timezone.utc).isoformat()}
