# Architecture

Employee of the Month is one local coordinator around four external systems: Telegram, GPT-Live 1, T3 Code and the selected coding-agent providers inside T3.

## Components

```text
Personal Telegram account
        ^  call audio and messages
        |
Dedicated sender account via TDLib
        ^  encrypted Telegram call transport
        |
Native tgcalls/WebRTC runtime
        ^  raw PCM, 16 kHz mono
        |
Python service  <---->  OpenAI GPT-Live 1
        |
        +------------>  OpenAI Responses API for small JSON structuring requests
        |
        +------------>  T3 Code orchestration HTTP API
                              |
                              +----> Codex, Claude or another configured provider
```

The Python package owns conversation state, authorization gates, deduplication and all T3 mutations. The native process owns only Telegram call media and reports a bounded protocol over standard input and output.

## Call flow

1. The watcher reads T3 snapshots and finds a structured, still-open user-input request.
2. After the configured delay, it durably reserves one contact attempt before dialing.
3. TDLib resolves the exact configured target and creates the Telegram call.
4. GPT-Live starts only after the native media path reports a confirmed connection.
5. The service streams PCM between Telegram and GPT-Live. No virtual macOS audio device is required.
6. GPT-Live leads the conversation. It delegates only when T3 must be read freshly or changed.
7. A small stateless Responses API request converts the relevant transcript into checked JSON. If that request fails, a T3 coordinator thread is the fallback.
8. Python validates the current user quote, question identity, thread identity and transcript revision before dispatching to T3.
9. Success is reported only after T3 readback confirms the expected state.

## Why there is no second copy of the work thread

T3 remains the owner of the coding-agent session. The phone side receives a bounded context packet and returns an answer to one exact request or starts a new normal T3 turn. It never opens the provider session independently and never edits T3's database.

## Persistence model

Every operation that could be duplicated is assigned stable IDs and persisted before dispatch:

- outbound call attempts,
- Telegram messages,
- T3 user-input responses,
- follow-up turns,
- task thread creation and first-turn commands.

After a timeout or crash, the service first reads back the same target and ID. It does not blindly send again. This is duplicate prevention, not a guarantee that every ambiguous transport outcome can be recovered.

## Safety model

The most important boundaries are:

- No call, login, message-capable daemon or billable voice session is part of `make check`.
- Runtime actions require explicit command-line gates such as `--allow-call` or `--allow-messages-and-calls`.
- Only the configured Telegram user ID can reach the conversation or project data.
- A clarification is not a decision. A historical confirmation is not current authorization.
- The latest relevant user statement must contain the quote used for a T3 answer.
- New speech invalidates a result derived from an older transcript revision.
- Follow-up messages require an exact title or thread ID and cannot answer an open structured question.
- Remote T3 origins require HTTPS, and redirects are rejected.
- Private files reject symlinks, foreign ownership and loose permissions.
- Logs use bounded event codes and omit raw exception messages that may contain private data.

## Thread lifecycle

Technical coordinator threads are settled after a call ends so they leave T3's active view. The service only settles threads it created and verifies their title before every command. Work threads are never settled automatically. A later turn can reactivate a settled coordinator, which is settled again after the next call.

## Limits

- One service process and one TDLib client per profile.
- One configured person per installation.
- At most one automatic outbound attempt per question.
- At least three minutes between automatic calls by default.
- Calls are bounded to 20 minutes.
- The conversation ledger is capped at 8 MiB.
- At most eight call transcripts with 24,000 characters each are retained.

These limits are implementation safeguards, not service-level guarantees.
