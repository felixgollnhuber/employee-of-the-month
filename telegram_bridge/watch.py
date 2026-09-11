"""Explicitly started, project-scoped pilot watcher. Never sends Telegram chat messages."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime

from .application import run_authorized_live_test, instructions_for_handoff
from .config import read_private_json, private_directory
from .control import GateError
from .handoff import _pending, pending_requests
from .keychain import cached_database_key
from .t3 import T3Client, T3Delegation, now, settle_after_call


def question_due(snapshot, request_id, delay_seconds, current_time=None):
    """Use the original question's timestamp, including across watcher restarts."""
    try:
        _,activity,_=_pending(snapshot,request_id)
        if delay_seconds == 0:return True
        created=activity.get("createdAt")
        if not isinstance(created,str):return False
        timestamp=datetime.fromisoformat(created.replace("Z","+00:00"))
        if timestamp.tzinfo is None:return False
        asked_at=timestamp.timestamp()
    except (ValueError,TypeError,GateError):return False
    current_time=time.time() if current_time is None else current_time
    return current_time-asked_at >= delay_seconds


def save_attempts(profile, attempts):
    fd, temporary = tempfile.mkstemp(prefix=".watch-", dir=profile)
    try:
        with os.fdopen(fd,"w") as stream:
            json.dump(attempts,stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,profile/"watch-attempts.json")
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def watch_project(profile, library, project_id, *, authorized=False, secret_input,
                  max_calls=1, watch_seconds=900, call_seconds=120, question_delay_seconds=180,
                  emit=lambda value:None):
    if authorized is not True: raise GateError("explicit_watcher_authorization_required")
    if type(max_calls) is not int or not 1 <= max_calls <= 20: raise GateError("bounded_watch_calls_required")
    if type(watch_seconds) is not int or not 1 <= watch_seconds <= 86400: raise GateError("bounded_watch_duration_required")
    if type(call_seconds) is not int or not 1 <= call_seconds <= 180: raise GateError("bounded_call_duration_required")
    if type(question_delay_seconds) is not int or not 0 <= question_delay_seconds <= 3600:
        raise GateError("invalid_question_delay")
    client = T3Client.from_profile(profile)
    shell = client.request("/api/orchestration/shell")
    if not any(p.get("id")==project_id for p in shell.get("projects",[])):
        raise GateError("explicit_t3_project_required")
    private_directory(profile)
    descriptor = os.open(profile/"watch.lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,"w") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ledger = profile/"watch-attempts.json"
        attempts = read_private_json(profile,ledger.name,max_bytes=262144) if ledger.exists() or ledger.is_symlink() else {}
        if not isinstance(attempts,dict): raise GateError("invalid_watcher_state")
        if len(attempts)>500: raise GateError("watcher_ledger_maintenance_required")
        passphrase=None
        if cached_database_key(profile) is None:
            # Legacy interactive setup until explicit Keychain enrollment.
            passphrase=secret_input("Lokale Datenbank-Passphrase für den T3-Anrufwächter: ")
            if not isinstance(passphrase,str) or len(passphrase)<16: raise GateError("local_passphrase_too_short")
        def legacy_input(_):
            if passphrase is None: raise GateError("automatic_database_unlock_unavailable")
            return passphrase
        count = 0; deadline = time.monotonic()+watch_seconds
        emit({"t3_watcher_started":True,"project_id":project_id,"max_calls":max_calls,"watch_seconds":watch_seconds,
              "question_delay_seconds":question_delay_seconds})
        while time.monotonic()<deadline and count<max_calls:
            shell = client.request("/api/orchestration/shell")
            candidates = [t for t in shell.get("threads",[]) if t.get("projectId")==project_id and t.get("hasPendingUserInput")
                          and not t.get("archivedAt") and not t.get("deletedAt")]
            for thread in candidates:
                if count>=max_calls: break
                snapshot = client.snapshot(thread["id"])
                for request_id in pending_requests(snapshot):
                    key = thread["id"]+":"+request_id
                    if key in attempts: continue
                    if not question_due(snapshot,request_id,question_delay_seconds):continue
                    try:
                        delegation = T3Delegation(client,thread["id"],request_id,emit=emit)
                    except GateError as error:
                        if str(error) in ("t3_question_no_longer_pending","t3_thread_unavailable"):
                            continue  # The user answered while we were preparing the call.
                        raise
                    attempts[key] = {"attempted_at":now(),"status":"starting"}
                    save_attempts(profile,attempts)  # Commit before dialing; no retry after an ambiguous crash.
                    count += 1
                    try:
                        result = run_authorized_live_test(profile,library,authorized=True,max_seconds=call_seconds,
                            secret_input=legacy_input,emit=emit,delegate=delegation,
                            instructions=instructions_for_handoff(delegation.packet))
                        attempts[key]["status"] = "answered" if delegation.completed else "open_after_"+result["phase"]
                    except BaseException:
                        attempts[key]["status"] = "attempt_failed_or_interrupted"
                        raise
                    finally:
                        settle_after_call(client, getattr(delegation,'coordinator_id',None), conversation_id=key+':'+attempts[key]['attempted_at'], emit=emit)
                        for covered in getattr(delegation,'request_ids_in_call',()):
                            covered_key=thread['id']+':'+covered
                            if covered_key not in attempts:
                                attempts[covered_key]={'attempted_at':attempts[key]['attempted_at'],
                                    'status':'covered_in_same_call','parent_request':request_id}
                        save_attempts(profile,attempts)
                    break
            if count<max_calls: time.sleep(min(2,max(0,deadline-time.monotonic())))
        emit({"t3_watcher_stopped":True,"calls_attempted":count})
