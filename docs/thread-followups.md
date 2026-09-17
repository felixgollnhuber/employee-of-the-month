# Follow-up messages to existing threads

Employee of the Month can send an explicitly addressed user turn to an existing T3 thread, including running, completed or settled threads.

## Target selection

The target must be an exact full title or a bounded thread ID. Matching is case-insensitive, but partial titles and similarity rankings never select a thread. When several active threads share a title, the service lists their projects and IDs and asks for the ID.

Archived, deleted and internal coordination threads are excluded. A target with an open structured question is also rejected because that question must use the separate answer path.

## Multi-turn voice context

During one call, the target and message can be supplied across several utterances. Only an exact thread reference from the current user transcript is remembered. The binding expires after a few user turns and is cleared by a topic change, cancellation, open-question answer or new task proposal.

Historical calls, the most recent operation and title similarity cannot select a target. A phrase such as "do it" works only when the current call already has one exact target and one explicit message.

## Delivery semantics

Before dispatch, the service reads the T3 shell and target snapshot, verifies project and thread identity, checks open questions and persists the exact `thread.turn.start` command with stable command and message IDs.

After an ambiguous transport result, it reads back the same message ID. It does not resend automatically. A matching message proves receipt by the thread, not successful domain work or a completed provider turn.

Tests cover English and German parsing, duplicate events, restart behavior, ambiguous titles, stale revisions, open questions and uncertain readback.
