# All-project service mode

The all-project service watches every non-deleted project exposed by one T3 Code instance. Projects and threads created after startup are discovered on later scans. Archived threads and internal call-coordination threads are excluded.

Use:

```sh
eotm serve-t3 \
  --all-projects \
  --daemon \
  --allow-messages-and-calls \
  --allow-task-creation \
  --seconds 1200
```

This command can place real calls, accept callbacks, send Telegram messages, start T3 turns and create T3 threads after explicit confirmation. It also starts billable GPT-Live sessions for connected calls. It is never part of `make check`.

## Contact behavior

- One TDLib client owns the configured profile.
- A structured T3 question is bound to its project, thread and request ID.
- The first automatic contact waits three minutes by default.
- Calls are spaced globally by at least three minutes.
- Each question receives at most one automatic dial attempt.
- A deferral pauses the operation until a message or callback. There is no timed reminder.
- Incoming calls can continue open operations or request a bounded status summary.

## Voice-created tasks

The service can turn a new feature request into a proposal containing a project, title, implementation prompt, provider instance, model and reasoning effort. Provider and model values must come from the current T3 catalog. Reported usage windows guide the recommendation, but unknown usage is never treated as free capacity.

A proposal does not create anything. A fresh confirmation in the current call is required. Before a confirmed start, limits and catalog options are checked again. The service persists stable thread, message and command IDs before dispatch and reconciles the same IDs after an ambiguous result.

## Operation

Use the fixed-release LaunchAgent workflow in [service.md](service.md). The Mac must stay awake and online, and T3 must remain reachable. Restarting Employee of the Month does not restart T3 threads or provider sessions.

The service writes owner-only health and conversation state. One call is limited to 20 minutes. The daemon itself has no planned end time. A graceful shutdown drains newly queued messages for a bounded period and never retries an older uncertain delivery blindly.

## Evidence boundary

The behavior above is covered by offline tests for multi-project discovery, global spacing, task confirmation, provider selection, idempotent commands, callback context and shutdown delivery. Historical real-call evidence is summarized in [evidence.md](evidence.md). Those observations show tested paths on one Mac at specific times and are not availability guarantees.
