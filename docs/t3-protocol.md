# T3 protocol compatibility

The bridge uses the configured T3 origin and bearer credential for both HTTP reads and WebSocket RPC. It does not open a second provider session or edit T3's database.

## V2 reads

Current orchestration endpoints require `x-t3-orchestration-protocol: 2`. Without it, T3 rejects the shell request with HTTP 400. The retired `/api/orchestration/snapshot` path can return the HTML application entry page with HTTP 200; this is not an API fallback. The client requires JSON responses.

The shell adapter validates `schemaVersion=2` and maps `pendingRuntimeRequest` and run status into the bridge's status/discovery contract. Approval requests remain separate from user-input questions. Native thread IDs containing colons or literal percent encodings are URL-encoded once as complete path components and verified against the returned thread identity.

V2 thread reads use `/api/orchestration/threads/:threadId/bounded`. The adapter uses the authoritative control-plane runtime requests and local question items; inherited or foreign-thread question details cannot authorize an answer. Missing question details, duplicate identities, conflicting questions and unknown lifecycle states fail closed.

The shell and bounded-thread response caps are 8 MiB. The existing 4 MiB cap remains for unbounded/legacy reads. These are finite transport budgets: a T3 bounded snapshot can exceed its usual server budget to include a complete large turn or required live control state. Call context and transcript retention limits remain independent of these HTTP response caps.

## V2 commands and receipts

V2 commands use `/ws?orchestrationProtocol=2` and `orchestration.dispatchCommand`:

| Bridge operation | V2 command |
| --- | --- |
| Create a task or technical coordinator | `thread.create` with creation metadata |
| Send a user message or start a turn | `message.dispatch` with automatic delivery intent |
| Return a confirmed structured answer | `runtime-request.respond` |
| Settle an owned technical coordinator | `thread.settle` |

The adapter keeps command, message, thread and request IDs unchanged. It checks current thread modes before message delivery, refuses interrupted/failed targets and does not force a restart. The server resolves automatic message delivery against its serialized thread state.

A command receipt confirms acceptance only. The bridge still reads back the exact message, run or runtime request before claiming success. V2 answer readback must contain the same answers. Transport errors never trigger an automatic command retry.

The metadata RPC facade remains limited to provider catalog reads. Command dispatch uses a separate internal RPC path. Legacy JSON snapshots are still decoded for existing clients and persisted conversation records, but a protocol change within a client lifetime is rejected. Completed/stale operations are not revived during restart recovery.

## Evidence

The adapter is covered by synthetic offline fixtures in `tests/test_t3_protocol.py`, including confirmation gates, request ownership, changed questions, stable IDs, response bounds, HTML rejection and answer readback. These tests open no network, audio, Telegram or Keychain session.

Read-only diagnosis against T3 Code Nightly `0.0.46-nightly.20261005.2676` on October 5, 2026 confirmed that the required header restores valid shell JSON. The measured shell was about 6.15 MB for 19 projects, and recent bounded thread snapshots could exceed 4 MiB. Provider catalog reads also worked with V2 negotiation. These observations do not prove spoken answer delivery or audible call behavior; those remain deliberate runtime checks.
