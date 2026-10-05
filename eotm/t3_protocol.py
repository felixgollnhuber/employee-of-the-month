"""Translate T3 V2 projections into the bridge's durable conversation contract."""
import copy

from .control import GateError

PROTOCOL_HEADER = "x-t3-orchestration-protocol"
SHELL_LIMIT = 8 * 1024 * 1024
THREAD_LIMIT = 4 * 1024 * 1024
BOUNDED_THREAD_LIMIT = 8 * 1024 * 1024
RUN_STATES = {"idle": "idle", "preparing": "starting", "queued": "queued",
              "starting": "starting", "running": "running", "waiting": "running",
              "completed": "completed", "failed": "error", "interrupted": "interrupted",
              "cancelled": "interrupted", "rolled_back": "interrupted"}
BUSY_STATES = {"preparing", "queued", "starting", "running", "waiting"}


def run_state(status):
    if not isinstance(status, str) or status not in RUN_STATES:
        raise GateError("t3_unknown_run_status")
    return RUN_STATES[status]


def records(value):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise GateError("t3_invalid_projection")
    return value


def shell_snapshot(value):
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 2:
        raise GateError("t3_unsupported_protocol")
    result = copy.deepcopy(value)
    records(result.get("projects"))
    threads = records(result.get("threads"))
    seen = set()
    for thread in threads:
        identifier = thread.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise GateError("t3_ambiguous_thread_identity")
        seen.add(identifier)
        request = thread.get("pendingRuntimeRequest")
        if request is not None and not isinstance(request, dict):
            raise GateError("t3_invalid_projection")
        thread["hasPendingUserInput"] = bool(request and request.get("kind") == "user_input")
        thread["latestTurn"] = {"turnId": thread.get("latestRunId"), "state": run_state(thread.get("status"))}
    return result


def thread_snapshot(value, thread_id):
    projection = value.get("projection")
    if not isinstance(projection, dict) or not isinstance(projection.get("thread"), dict):
        raise GateError("t3_invalid_projection")
    thread = copy.deepcopy(projection["thread"])
    if thread.get("id") != thread_id:
        raise GateError("t3_snapshot_mismatch")
    nodes = records(projection.get("nodes", []))
    node_ids = {node["id"] for node in nodes if node.get("threadId") == thread_id and isinstance(node.get("id"), str)}
    runs = [run for run in records(projection.get("runs", [])) if run.get("threadId") == thread_id]
    latest = max(runs, key=lambda run: run.get("ordinal", 0), default=None)
    # Imported native histories may predate app-owned runs. Provider turns retain their status.
    if latest is None:
        provider_threads = {item.get("id") for item in records(projection.get("providerThreads", []))
                            if item.get("appThreadId") == thread_id}
        turns = [turn for turn in records(projection.get("providerTurns", []))
                 if turn.get("providerThreadId") in provider_threads]
        latest = max(turns, key=lambda turn: turn.get("ordinal", 0), default=None)
    status = latest.get("status") if latest else "idle"
    thread["latestTurn"] = {"turnId": latest.get("id") if latest else None, "state": run_state(status)}
    thread["session"] = {"status": "running" if status in BUSY_STATES else "idle"}
    thread["messages"] = []
    for message in records(projection.get("messages", [])):
        if message.get("threadId") != thread_id:
            continue
        message = copy.deepcopy(message)
        message["turnId"] = message.get("runId")
        thread["messages"].append(message)
    thread["messages"].sort(key=lambda message: (message.get("createdAt", ""), message.get("id", "")))
    items = list(records(projection.get("turnItems", [])))
    for visible in records(projection.get("visibleTurnItems", [])):
        if visible.get("visibility") == "local" and visible.get("sourceThreadId") == thread_id:
            if not isinstance(visible.get("item"), dict):
                raise GateError("t3_invalid_projection")
            items.append(visible["item"])
    questions = {}
    for item in items:
        if item.get("threadId") != thread_id or item.get("type") != "user_input_request":
            continue
        key = (item.get("requestId"), item.get("nodeId"))
        candidate = item.get("questions")
        if key in questions and questions[key] != candidate:
            raise GateError("t3_ambiguous_request_details")
        questions[key] = candidate
    thread["activities"] = []
    pending_input = False
    seen = set()
    for request in records(projection.get("runtimeRequests", [])):
        identifier = request.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen or request.get("nodeId") not in node_ids:
            raise GateError("t3_ambiguous_request_identity")
        seen.add(identifier)
        state = request.get("status")
        if state not in ("pending", "resolved", "expired", "cancelled"):
            raise GateError("t3_unknown_request_status")
        user_input = request.get("kind") == "user_input"
        prefix = "user-input" if user_input else "approval"
        payload = {"requestId": identifier}
        details = questions.get((identifier, request.get("nodeId")))
        if user_input and details is not None:
            payload["questions"] = copy.deepcopy(details)
        capability = request.get("responseCapability")
        if not isinstance(capability, dict) or capability.get("type") not in ("live", "message", "not_resumable"):
            raise GateError("t3_invalid_response_capability")
        resumable = capability["type"] != "not_resumable"
        if user_input and state == "pending" and resumable and not isinstance(details, list):
            raise GateError("t3_pending_question_details_missing")
        activity = {"id": "v2-request-" + identifier, "sequence": 0, "kind": prefix + ".requested",
                    "createdAt": request.get("createdAt"), "payload": payload}
        thread["activities"].append(activity)
        if state != "pending" or not resumable:
            resolved = {"requestId": identifier}
            if "answers" in request:
                resolved["answers"] = copy.deepcopy(request["answers"])
            kind = prefix + ".resolved" if state == "resolved" else (
                "provider.user-input.respond.failed" if user_input else "approval.resolved")
            thread["activities"].append({"id": "v2-result-" + identifier, "sequence": 1, "kind": kind,
                                         "createdAt": request.get("resolvedAt") or request.get("createdAt"), "payload": resolved})
        elif user_input:
            pending_input = True
    thread["hasPendingUserInput"] = pending_input
    return {"snapshotSequence": value.get("snapshotSequence"), "thread": thread}


def command_v2(command, source=None):
    """Keep durable IDs intact; never invent an approval or restart an interrupted run."""
    if not isinstance(command, dict):
        raise GateError("t3_invalid_command")
    kind = command.get("type")
    common = {key: command[key] for key in ("commandId", "threadId")}
    if kind == "thread.create":
        return {"type": kind, **common, "createdBy": "user", "creationSource": "mcp",
                **{key: copy.deepcopy(command[key]) for key in
                   ("projectId", "title", "modelSelection", "runtimeMode", "interactionMode", "branch", "worktreePath")}}
    if kind == "thread.settle":
        return {"type": kind, **common}
    if kind == "thread.user-input.respond":
        return {"type": "runtime-request.respond", **common,
                "requestId": command["requestId"], "answers": copy.deepcopy(command["answers"])}
    if kind != "thread.turn.start":
        raise GateError("t3_unsupported_command")
    if not isinstance(source, dict) or source.get("id") != command["threadId"]:
        raise GateError("t3_snapshot_mismatch")
    if source.get("archivedAt") or source.get("deletedAt"):
        raise GateError("t3_thread_unavailable")
    state = (source.get("latestTurn") or {}).get("state")
    if state in ("error", "interrupted"):
        raise GateError("t3_source_not_ready_for_followup")
    if source.get("hasPendingUserInput"):
        raise GateError("t3_followup_has_open_question")
    for field in ("runtimeMode", "interactionMode"):
        if command.get(field) != source.get(field):
            raise GateError("t3_thread_modes_changed")
    message = command["message"]
    if message.get("role") != "user":
        raise GateError("t3_invalid_command")
    result = {"type": "message.dispatch", **common, "createdBy": "user", "creationSource": "mcp",
              "messageId": message["messageId"], "text": message["text"],
              "attachments": copy.deepcopy(message.get("attachments", [])),
              "deliveryIntent": "auto", "dispatchMode": {"type": "start_immediately"}}
    if "modelSelection" in command:
        result["modelSelection"] = copy.deepcopy(command["modelSelection"])
    return result
