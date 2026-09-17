# Usage

Employee of the Month supports three conversation entry points: an automatic call for an open T3 question, a callback from you and a Telegram text reply.

## Automatic calls for T3 questions

The service watches structured T3 user-input requests. By default it waits three minutes before the first contact attempt. It calls at most once per question and never treats an approval request or a plain-text question as a structured request.

During the call, the voice agent explains the question from bounded task context. A decision is read back before it can be sent to T3. A clarification such as "which report do you mean?" is not a decision and cannot close the question.

If you do not answer, the service sends one short Telegram message with the question and a durable operation ID. It does not redial automatically.

## Callbacks and status questions

You can call the sender account back. The service accepts calls only from the configured personal Telegram account.

You can ask for the status of a project or thread, continue an open question, or resume a saved task proposal. Status information is prefetched at call start and refreshed when necessary. Historical call text provides context, not authorization. A new confirmation is always required for a new action.

## Telegram text replies

Reply to the service's message to preserve the exact operation binding. A clear answer can be passed to T3 without a second confirmation round. Clarifications are returned to the source thread and the explanation is brought back into the conversation.

Messages from other accounts, group chats and messages that predate the first service start are ignored.

## Send a message to an existing thread

English examples:

```text
Send the thread "Checkout redesign": Please cover the error state too.
Tell thread 123e4567-e89b-12d3-a456-426614174000 to add a migration note.
```

German examples:

```text
Sende an Thread "Checkout redesign": Bitte prüfe auch den Fehlerfall.
Sag dem Thread 123e4567-e89b-12d3-a456-426614174000, dass er die Migration dokumentieren soll.
```

Targets are matched only by an exact full title or a bounded thread ID. Similar titles are not guessed. When titles are duplicated, the service asks for the thread ID. A thread with an open structured question cannot receive a normal follow-up through this path.

Inside a live call, target and message may be provided over several turns. The binding lasts only for the current call and only for a few user utterances. A topic change, cancellation or conflicting target clears it.

## Start a new T3 task by voice

Describe the desired result and name an existing T3 project. The service proposes:

- the project and thread title,
- a concrete implementation prompt,
- the provider instance, model and reasoning effort,
- a short reason based on complexity and currently reported provider limits.

No thread is created until you confirm the exact proposal in the current conversation. The provider catalog and allowed options come from T3. Missing or stale quota data is reported as unknown, never treated as free capacity.

## Deferral and hang-up

"Not now" or "jetzt nicht" pauses the current operation. There is no timed reminder. A later message or callback can continue it.

A direct hang-up request ends the phone call but does not close an unresolved T3 question. Negated or quoted phrases such as "do not hang up" must not trigger the hang-up path.
