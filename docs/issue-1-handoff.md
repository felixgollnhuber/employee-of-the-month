# Development handoff: original Telegram service issue

The original issue established the service architecture that now lives under `eotm/`:

- direct Telegram call audio through TDLib and tgcalls,
- GPT-Live 1 as the conversation layer,
- exact T3 project, thread and request binding,
- delayed one-time outbound calls,
- Telegram text fallback and callbacks,
- bounded status context and confirmed voice-created tasks,
- durable duplicate prevention across restarts,
- one exclusive TDLib client per profile.

## Required boundaries

Read [architecture.md](architecture.md) before changing call, conversation or T3 command flow.

- `make check` must remain offline and must not affect an installed service or active call.
- Real calls, logins, audio capture, playback and service activation are separate explicit runtime actions.
- A T3 dispatch receipt proves acceptance, not completed domain work.
- A clarification is not a decision. Historical confirmation is not current authorization.
- Never run two processes against the same Telegram profile.
- Do not update a fixed release merely by editing a working tree. Stage and activate a new release explicitly.

## Current source of truth

Public setup and operation are documented in the main README and [service.md](service.md). Historical live observations and remaining checks are in [evidence.md](evidence.md). The older repository and service identifiers remain only where compatibility with an existing private profile requires them.
