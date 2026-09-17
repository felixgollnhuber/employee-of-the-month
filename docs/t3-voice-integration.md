# T3 and voice integration

T3 owns work threads and provider sessions. Employee of the Month reads bounded thread snapshots and sends typed orchestration commands through T3's authenticated API. It never edits T3's database and never opens a second copy of a provider session.

## Read path

- `GET /api/orchestration/shell` supplies projects and thread summaries.
- `GET /api/orchestration/threads/:threadId` supplies one full snapshot.
- T3 RPC supplies the configured provider catalog and reported usage windows.

## Write path

- `thread.user-input.respond` answers one exact open structured request.
- `thread.turn.start` sends a normal follow-up or begins confirmed work.
- `thread.create` creates a confirmed voice task before its first turn starts.
- `thread.settle` removes completed technical coordination threads from the active view.

Every mutation has a stable command ID and is persisted before dispatch. The service reads the target back before it reports success.

## Question handoff

The handoff packet contains the T3 thread ID, project ID, request ID, question fingerprint, bounded context and the questions themselves. The service rechecks all of them immediately before returning an answer. A resolved, changed, moved, archived or deleted question is rejected.

New user speech invalidates a structuring result derived from an older transcript revision. A voice decision also needs a verbatim confirmation quote from the latest user statement.

## Coordinator fallback

The direct Responses API path is preferred for low-latency JSON structuring. If it fails or is disabled, a technical T3 coordinator thread performs the same classification contract. Technical coordinators can be settled after a call. Work threads are never settled automatically.
