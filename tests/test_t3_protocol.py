import copy
import json
import unittest
from unittest.mock import Mock

from eotm.control import GateError
from eotm.handoff import pending_requests, prepare_answer, prepare_handoff
from eotm.t3 import T3Client, blocking_requests
from eotm.t3_protocol import (BOUNDED_THREAD_LIMIT, SHELL_LIMIT, THREAD_LIMIT,
                              command_v2, shell_snapshot, thread_snapshot)

TIME = "2026-10-05T10:00:00Z"
QUESTIONS = [{"id": "choice", "header": "Format", "question": "Which format?",
              "options": [{"label": "PDF", "description": "Read the report"}]}]


def projection(*, status="running", request_status="pending", kind="user_input", capability="live"):
    return {"snapshotSequence": 10, "hasMoreHistory": True, "projection": {
        "thread": {"id": "thread", "projectId": "project", "title": "Fixture task",
                   "modelSelection": {"instanceId": "provider", "model": "fixture"},
                   "runtimeMode": "full-access", "interactionMode": "default",
                   "branch": None, "worktreePath": None, "archivedAt": None, "deletedAt": None},
        "runs": [{"id": "run", "threadId": "thread", "ordinal": 1, "status": status}],
        "nodes": [{"id": "node", "threadId": "thread", "runId": "run"}],
        "providerThreads": [], "providerTurns": [],
        "messages": [{"id": "message", "threadId": "thread", "runId": "run", "role": "assistant",
                      "text": "Fixture context", "streaming": False, "createdAt": TIME}],
        "runtimeRequests": [{"id": "request", "nodeId": "node", "kind": kind,
                             "status": request_status, "responseCapability": {"type": capability},
                             "createdAt": TIME, "resolvedAt": TIME if request_status != "pending" else None}],
        "turnItems": [{"id": "item", "threadId": "thread", "nodeId": "node", "type": "user_input_request",
                       "requestId": "request", "questions": copy.deepcopy(QUESTIONS)}],
        "visibleTurnItems": []}}


def shell():
    return {"schemaVersion": 2, "snapshotSequence": 10, "projects": [{"id": "project", "title": "Fixture"}],
            "threads": [{"id": "thread", "projectId": "project", "status": "waiting", "latestRunId": "run",
                         "pendingRuntimeRequest": {"id": "request", "kind": "user_input", "createdAt": TIME}}],
            "archivedThreads": []}


class Response:
    def __init__(self, value, content_type="application/json"):
        self.data = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.headers = {"Content-Type": content_type}
        self.read_sizes = []
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size):
        self.read_sizes.append(size)
        return self.data[:size]


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout == 8
        return self.responses.pop(0)


class Socket:
    def __init__(self, result=None, fail=False):
        self.result = {"sequence": 11} if result is None else result
        self.fail = fail
        self.sent = []
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def send(self, value): self.sent.append(json.loads(value))
    def recv(self, timeout):
        return json.dumps({"requestId": self.sent[-1]["id"], "exit": {
            "_tag": "Failure" if self.fail else "Success", "value": self.result,
            "cause": "PRIVATE_RESPONSE_NOT_FOR_LOGS"}})


class ProjectionTests(unittest.TestCase):
    def test_shell_recognizes_questions_without_treating_approvals_as_answers(self):
        value = shell(); before = copy.deepcopy(value)
        result = shell_snapshot(value)
        self.assertTrue(result["threads"][0]["hasPendingUserInput"])
        self.assertEqual(result["threads"][0]["latestTurn"], {"turnId": "run", "state": "running"})
        value["threads"][0]["pendingRuntimeRequest"]["kind"] = "tool_approval"
        self.assertFalse(shell_snapshot(value)["threads"][0]["hasPendingUserInput"])
        self.assertEqual(before["threads"][0]["pendingRuntimeRequest"]["kind"], "user_input")

    def test_unknown_version_status_and_duplicate_identity_fail_closed(self):
        for change in ("version", "status", "duplicate"):
            value = shell()
            if change == "version": value["schemaVersion"] = 3
            elif change == "status": value["threads"][0]["status"] = "new_future_status"
            else: value["threads"].append(copy.deepcopy(value["threads"][0]))
            with self.subTest(change=change), self.assertRaises(GateError): shell_snapshot(value)

    def test_question_roundtrip_preserves_fingerprint_and_durable_command_ids(self):
        value = projection(); before = copy.deepcopy(value)
        snapshot = thread_snapshot(value, "thread")
        self.assertEqual(pending_requests(snapshot), ["request"])
        handoff, packet = prepare_handoff(snapshot, "request", context="Selected context")
        self.assertEqual(packet["questions"], QUESTIONS)
        first = prepare_answer(handoff, snapshot, {"choice": "PDF"}, confirmed_by_user=True)
        second = prepare_answer(handoff, snapshot, {"choice": "PDF"}, confirmed_by_user=True)
        self.assertEqual(command_v2(first), command_v2(second))
        self.assertEqual(command_v2(first)["requestId"], "request")
        self.assertNotIn("decision", command_v2(first))
        self.assertEqual(value, before)
        value["projection"]["turnItems"][0]["questions"][0]["question"] = "Changed question?"
        with self.assertRaisesRegex(GateError, "question_changed"):
            prepare_answer(handoff, thread_snapshot(value, "thread"), {"choice": "PDF"}, confirmed_by_user=True)

    def test_confirmation_gate_still_precedes_answer_dispatch(self):
        snapshot = thread_snapshot(projection(), "thread")
        handoff, _ = prepare_handoff(snapshot, "request", context="Context")
        with self.assertRaisesRegex(GateError, "confirmation"):
            prepare_answer(handoff, snapshot, {"choice": "PDF"})

    def test_resolved_expired_cancelled_and_nonresumable_requests_cannot_be_answered(self):
        for state in ("resolved", "expired", "cancelled"):
            with self.subTest(state=state):
                snapshot = thread_snapshot(projection(request_status=state), "thread")
                self.assertEqual(pending_requests(snapshot), [])
                self.assertFalse(snapshot["thread"]["hasPendingUserInput"])
        self.assertEqual(pending_requests(thread_snapshot(projection(capability="not_resumable"), "thread")), [])

    def test_approval_and_other_runtime_requests_block_settlement_only(self):
        for kind in ("tool_approval", "dynamic_tool_call", "auth_refresh"):
            snapshot = thread_snapshot(projection(kind=kind), "thread")
            self.assertEqual(pending_requests(snapshot), [])
            self.assertEqual(blocking_requests(snapshot["thread"]), {"request"})

    def test_foreign_thread_nodes_and_inherited_questions_cannot_bind_an_answer(self):
        for change in ("foreign_thread", "foreign_node", "inherited"):
            value = projection()
            if change == "foreign_thread": value["projection"]["thread"]["id"] = "other"
            elif change == "foreign_node": value["projection"]["runtimeRequests"][0]["nodeId"] = "other-node"
            else:
                item = value["projection"]["turnItems"].pop()
                value["projection"]["visibleTurnItems"] = [{"visibility": "inherited", "sourceThreadId": "other", "item": item}]
            with self.subTest(change=change), self.assertRaises(GateError): thread_snapshot(value, "thread")

    def test_pending_control_plane_needs_complete_question_details(self):
        value = projection(); value["projection"]["turnItems"] = []
        with self.assertRaisesRegex(GateError, "details_missing"): thread_snapshot(value, "thread")
        value = projection(); duplicate = copy.deepcopy(value["projection"]["turnItems"][0])
        duplicate["questions"][0]["question"] = "Different question?"
        value["projection"]["turnItems"].append(duplicate)
        with self.assertRaisesRegex(GateError, "ambiguous_request_details"): thread_snapshot(value, "thread")

    def test_queued_runs_are_busy_and_are_not_reported_as_started(self):
        snapshot = thread_snapshot(projection(status="queued"), "thread")
        self.assertEqual(snapshot["thread"]["latestTurn"]["state"], "queued")
        self.assertEqual(snapshot["thread"]["session"]["status"], "running")

    def test_imported_history_uses_the_owning_provider_thread_and_turn(self):
        value = projection(); p = value["projection"]; p["runs"] = []
        p["providerThreads"] = [{"id": "native-thread", "appThreadId": "thread"}]
        p["providerTurns"] = [{"id": "native-turn", "providerThreadId": "native-thread", "ordinal": 1, "status": "completed"},
                              {"id": "foreign-turn", "providerThreadId": "other", "ordinal": 2, "status": "running"}]
        self.assertEqual(thread_snapshot(value, "thread")["thread"]["latestTurn"], {"turnId": "native-turn", "state": "completed"})

    def test_messages_keep_run_identity_and_exclude_foreign_context(self):
        value = projection(); value["projection"]["messages"].append({"id": "foreign", "threadId": "other", "text": "PRIVATE"})
        messages = thread_snapshot(value, "thread")["thread"]["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["turnId"], "run")


class TransportTests(unittest.TestCase):
    def test_protocol_header_and_larger_bounded_shell_read(self):
        value = shell(); value["padding"] = "x" * (THREAD_LIMIT + 10)
        response = Response(value); opener = Opener(response)
        client = T3Client("http://localhost", "PRIVATE", opener=opener)
        self.assertEqual(len(client.request("/api/orchestration/shell")["projects"]), 1)
        self.assertEqual(client.protocol, 2)
        self.assertEqual(opener.requests[0].get_header("X-t3-orchestration-protocol"), "2")
        self.assertEqual(response.read_sizes, [SHELL_LIMIT + 1])

    def test_html_and_nonobject_responses_are_rejected(self):
        for response in (Response(b"<html>PRIVATE</html>", "text/html"), Response([])):
            with self.subTest(content_type=response.headers), self.assertRaises(GateError) as raised:
                T3Client("http://localhost", "PRIVATE", opener=Opener(response)).request("/api/orchestration/shell")
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_protocol_downgrade_is_rejected(self):
        client = T3Client("http://localhost", "PRIVATE", opener=Opener(Response(shell()), Response({"projects": [], "threads": []})))
        client.request("/api/orchestration/shell")
        with self.assertRaisesRegex(GateError, "protocol_changed"): client.request("/api/orchestration/shell")

    def test_unsupported_protocol_cannot_enable_later_mutation(self):
        value = shell(); value['schemaVersion'] = 3
        connector = Mock(); client = T3Client("http://localhost", "PRIVATE",
            opener=Opener(Response(value), Response(value)), connector=connector)
        with self.assertRaisesRegex(GateError, "unsupported_protocol"): client.request("/api/orchestration/shell")
        self.assertIsNone(client.protocol)
        with self.assertRaisesRegex(GateError, "unsupported_protocol"):
            client.dispatch({"type": "thread.settle", "threadId": "thread", "commandId": "stable"})
        connector.assert_not_called()

    def test_v2_thread_reads_are_bounded_and_native_ids_are_encoded_once(self):
        identifier = "import:fixture%2Fid"
        value = projection(); value["projection"]["thread"]["id"] = identifier
        value["projection"]["runtimeRequests"] = []; value["projection"]["runs"] = []
        response = Response(value); opener = Opener(Response(shell()), response)
        client = T3Client("http://localhost", "PRIVATE", opener=opener)
        client.request("/api/orchestration/shell"); client.snapshot(identifier)
        self.assertTrue(opener.requests[-1].full_url.endswith("/threads/import%3Afixture%252Fid/bounded"))
        self.assertEqual(response.read_sizes, [BOUNDED_THREAD_LIMIT + 1])

    def test_oversized_bounded_thread_is_rejected(self):
        client = T3Client("http://localhost", "PRIVATE", opener=Opener(Response(b"x" * (BOUNDED_THREAD_LIMIT + 1))))
        client.protocol = 2
        with self.assertRaisesRegex(GateError, "too_large"): client.snapshot("thread")

    def test_rpc_preserves_command_ids_and_reports_only_bounded_failures(self):
        socket = Socket(); connector = Mock(return_value=socket)
        client = T3Client("http://localhost", "PRIVATE", connector=connector); client.protocol = 2
        command = {"type": "thread.settle", "commandId": "stable-id", "threadId": "thread"}
        self.assertEqual(client.dispatch(command), {"sequence": 11})
        sent = socket.sent[0]
        self.assertEqual(sent["tag"], "orchestration.dispatchCommand")
        self.assertEqual(sent["payload"], command)
        self.assertIn("orchestrationProtocol=2", connector.call_args.args[0])
        socket.fail = True
        with self.assertRaisesRegex(GateError, "^t3_dispatch_unconfirmed$"): client.dispatch(command)
        self.assertEqual(len(socket.sent), 2)
        self.assertEqual(socket.sent[0]["payload"], socket.sent[1]["payload"])

    def test_answer_readback_requires_the_same_answers(self):
        initial = projection(); handoff, _ = prepare_handoff(thread_snapshot(initial, "thread"), "request", context="Context")
        for answer in ("PDF", "CSV"):
            resolved = projection(request_status="resolved"); resolved["projection"]["runtimeRequests"][0]["answers"] = {"choice": answer}
            opener = Opener(Response(initial), Response(resolved)); socket = Socket()
            client = T3Client("http://localhost", "PRIVATE", opener=opener, connector=lambda *a, **k: socket); client.protocol = 2
            if answer == "PDF": self.assertTrue(client.return_answer(handoff, {"choice": "PDF"}, confirmed_by_user=True))
            else:
                with self.assertRaisesRegex(GateError, "readback_mismatch"):
                    client.return_answer(handoff, {"choice": "PDF"}, confirmed_by_user=True)
            self.assertEqual(socket.sent[0]["payload"]["type"], "runtime-request.respond")

    def test_metadata_facade_cannot_dispatch_arbitrary_commands(self):
        connector = Mock(); client = T3Client("http://localhost", "PRIVATE", connector=connector)
        with self.assertRaisesRegex(GateError, "unsupported_t3_metadata_method"):
            client.rpc("orchestration.dispatchCommand", {})
        connector.assert_not_called()


class CommandTests(unittest.TestCase):
    def test_new_thread_keeps_exact_target_model_workspace_and_modes(self):
        source = projection()["projection"]["thread"]
        command = {"type": "thread.create", "commandId": "create-id", "threadId": "thread",
                   **{key: source[key] for key in ("projectId", "title", "modelSelection", "runtimeMode", "interactionMode", "branch", "worktreePath")}}
        wire = command_v2(command)
        self.assertEqual(wire["commandId"], "create-id")
        for key in ("threadId", "projectId", "modelSelection", "branch", "worktreePath", "runtimeMode", "interactionMode"):
            self.assertEqual(wire[key], command[key])
        self.assertEqual((wire["createdBy"], wire["creationSource"]), ("user", "mcp"))

    def test_message_dispatch_keeps_text_and_message_ids_without_forcing_restart(self):
        source = thread_snapshot(projection(request_status="resolved"), "thread")["thread"]
        command = {"type": "thread.turn.start", "commandId": "stable", "threadId": "thread",
                   "runtimeMode": "full-access", "interactionMode": "default",
                   "message": {"messageId": "message-id", "role": "user", "text": "Exact payload!", "attachments": []}}
        wire = command_v2(command, source)
        self.assertEqual((wire["type"], wire["commandId"], wire["messageId"], wire["text"]),
                         ("message.dispatch", "stable", "message-id", "Exact payload!"))
        self.assertEqual(wire["deliveryIntent"], "auto")
        source["hasPendingUserInput"] = True
        with self.assertRaisesRegex(GateError, "open_question"): command_v2(command, source)
        source["hasPendingUserInput"] = False
        for state in ("interrupted", "error"):
            source["latestTurn"]["state"] = state
            with self.assertRaisesRegex(GateError, "not_ready"): command_v2(command, source)
        source["latestTurn"]["state"] = "completed"; source["interactionMode"] = "plan"
        with self.assertRaisesRegex(GateError, "modes_changed"): command_v2(command, source)


if __name__ == "__main__":
    unittest.main()
