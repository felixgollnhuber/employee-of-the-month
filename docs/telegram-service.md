# Telegram conversations and incoming calls

`serve-t3` combines outbound calls, Telegram text conversations and incoming calls in one TDLib client.

## Open-question lifecycle

An open structured T3 question receives a durable operation ID. The service reserves one automatic contact attempt after the configured delay. If the question is still open after that attempt, it queues one short Telegram message. A question answered externally in T3 receives no first follow-up.

Text replies are accepted only from the configured personal user in the private chat. Replying to a service message preserves its operation binding. An explicit operation ID may select an operation but cannot contradict the replied-to message.

A clear text decision does not need a second confirmation loop. A clarification remains a clarification and can cause the work thread to ask a new structured question.

## Incoming calls

Only the configured personal user can create an incoming media session. A callback can continue one open operation, select among several operations or ask for status. Foreign callers receive no project data and no native media instance.

Hang-up ends the call, not the unresolved question. A deferral pauses the operation without an automatic reminder.

## Durable state

`conversations.json` stores bounded operation metadata, IDs, selected context, message hashes, outbox reservations and settlement requests. It contains no API keys or audio. The file is capped at 8 MiB.

Before every call attempt, message and T3 mutation, the intended action is written and synced. A crash after reservation may leave an ambiguous result, so the service reconciles by ID and does not retry blindly.

## Coordinator settlement

Technical call coordinators are settled after a call ends, including timeouts and recoverable failures. The service verifies the coordinator title and ownership before every `thread.settle` command. Running turns, approvals and open user-input requests delay settlement. Work threads are never changed by this cleanup.

## Live limits

One service run accepts calls up to 20 minutes. A bounded non-daemon run accepts at most 20 automatic outbound calls. The daemon can run indefinitely but still enforces global call spacing and one automatic attempt per question.
