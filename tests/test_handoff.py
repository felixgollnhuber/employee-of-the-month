import copy
import json
import unittest

from eotm.control import GateError
from eotm.handoff import prepare_handoff, prepare_answer


def snapshot():
    return {"thread": {"id": "t3-thread", "projectId": "project", "title": "Fixture task",
        "messages": [{"text": "PRIVATE_HISTORY_NOT_FOR_VOICE"}],
        "activities": [{"id": "activity-1", "createdAt": "2026-09-11T10:00:00Z",
            "kind": "user-input.requested", "payload": {"requestId": "request-1",
            "questions": [{"id": "choice", "question": "Welche Variante?"}]}}]}}


class HandoffTests(unittest.TestCase):
    def test_roundtrip_keeps_t3_request_identity_and_selected_context(self):
        state = snapshot()
        before = copy.deepcopy(state)
        handoff, packet = prepare_handoff(state, "request-1", context="Nur die relevante Rückfrage.")
        self.assertNotIn("PRIVATE_HISTORY", json.dumps(packet))
        command = prepare_answer(handoff, state, {"choice": "Variante B"}, confirmed_by_user=True)
        self.assertEqual((command["type"], command["threadId"], command["requestId"]),
                         ("thread.user-input.respond", "t3-thread", "request-1"))
        self.assertEqual(state, before)

    def test_unconfirmed_or_wrong_thread_answer_is_rejected(self):
        state = snapshot()
        handoff, _ = prepare_handoff(state, "request-1", context="Kontext")
        with self.assertRaisesRegex(GateError, "confirmation"):
            prepare_answer(handoff, state, {"choice": "Ja"})
        state["thread"]["id"] = "another-thread"
        with self.assertRaisesRegex(GateError, "mismatch"):
            prepare_answer(handoff, state, {"choice": "Ja"}, confirmed_by_user=True)

    def test_resolved_or_changed_question_cannot_receive_late_answer(self):
        for change in ("resolved", "changed"):
            state = snapshot()
            handoff, _ = prepare_handoff(state, "request-1", context="Kontext")
            if change == "resolved":
                state["thread"]["activities"].append({"id": "activity-2", "createdAt": "2026-09-11T10:01:00Z",
                    "kind": "user-input.resolved", "payload": {"requestId": "request-1"}})
            else:
                state["thread"]["activities"][0]["payload"]["questions"][0]["question"] = "Neue Frage?"
            with self.assertRaises(GateError):
                prepare_answer(handoff, state, {"choice": "Ja"}, confirmed_by_user=True)

    def test_approval_request_is_not_treated_as_a_question(self):
        state = snapshot()
        state["thread"]["activities"][0]["kind"] = "approval.requested"
        with self.assertRaises(GateError):
            prepare_handoff(state, "request-1", context="Kontext")

    def test_retried_answer_has_stable_command_id_and_wrong_question_rejected(self):
        state = snapshot()
        handoff, _ = prepare_handoff(state, "request-1", context="Kontext")
        a = prepare_answer(handoff, state, {"choice": "Ja"}, confirmed_by_user=True)
        b = prepare_answer(handoff, state, {"choice": "Ja"}, confirmed_by_user=True)
        self.assertEqual(a["commandId"], b["commandId"])
        with self.assertRaises(GateError):
            prepare_answer(handoff, state, {"different": "Ja"}, confirmed_by_user=True)


if __name__ == "__main__":
    unittest.main()
