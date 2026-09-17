<div align="center">

# 📞 Employee of the Month

**Your coding agents call you when they need you.**

When an agent in [T3 Code](https://github.com/pingdotgg/t3code) asks you a question and you are away from your desk,<br>
Employee of the Month rings your phone on Telegram, talks it through with you and hands your answer back to the agent.

[![CI](https://github.com/felixgollnhuber/employee-of-the-month/actions/workflows/ci.yml/badge.svg)](https://github.com/felixgollnhuber/employee-of-the-month/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: macOS on Apple Silicon](https://img.shields.io/badge/platform-macOS%20%C2%B7%20Apple%20Silicon-lightgrey)
![Status: experimental](https://img.shields.io/badge/status-experimental-orange)

[Getting started](docs/getting-started.md) · [Usage](docs/usage.md) · [Architecture](docs/architecture.md) · [Configuration](docs/configuration.md) · [CLI](docs/cli.md)

</div>

---

```text
📱  *ring ring*

🤖  Hi! The "Weekly report" task in Acme needs you. It wants to know whether
    the export should be a CSV or a PDF. The report goes to the board, so it
    is mostly read, not processed further.
🙂  Hmm, PDF then.
🤖  Got it: PDF for the weekly board report. Shall I pass that on?
🙂  Yes.
🤖  Done. The task has your answer and is carrying on.
🙂  Great. While I have you, how is the search feature going?
🤖  It is running. The last update says the index is built and the tests are next.
🙂  Perfect. You can hang up.
🤖  Bye!
```

## Why?

Coding agents are great at working on their own, right up until they need a decision. Then they wait, sometimes for hours, because you are in a meeting, on the train or cooking dinner.

Employee of the Month closes that gap. It watches your T3 Code projects. When a question has been waiting for a few minutes, it calls you on Telegram with a real-time voice agent that already knows the context. You talk like you would with a colleague. Once you confirm an answer, it goes straight back to the waiting thread.

## Features

- **📞 Calls you when an agent is blocked.** Watches every T3 Code project (or just one) and calls about open questions after a configurable delay. It dials at most once per question.
- **🗣️ Natural conversations.** Uses OpenAI's GPT-Live 1 speech-to-speech model, so you can interrupt, change your mind or ask "wait, which report?"
- **✅ Confirmed answers only.** The agent reads a decision back and waits for your "yes". The bridge checks that your confirmation is in the transcript before anything reaches T3.
- **💬 Telegram text fallback.** Missed the call? You get a short message with the question. Reply in the chat, or call back whenever it suits you.
- **📊 Status on demand.** Call in and ask "how is the search feature going?" The agent has a fresh snapshot of your tasks from the moment the call starts.
- **✉️ Messages to any thread.** "Send the thread *Checkout redesign*: please also cover the error state."
- **🚀 Start new work by voice.** Describe a feature. The agent proposes a project, model and reasoning effort based on your remaining provider limits, then starts a new T3 thread after you confirm.
- **🌍 English and German.** Pick the conversation language and how the agent addresses you.
- **🔒 Local and private.** Everything runs on your Mac. Secrets and transcripts live in owner-only files. Only your own Telegram account can reach the agent.
- **🛟 Crash-safe.** Every call, message and T3 command is written to disk before it happens and never replayed blindly after a restart.

## How it works

```text
┌──────────────┐  Telegram call  ┌───────────────────────────────┐   WebSocket   ┌──────────────┐
│ Your phone   │◀───────────────▶│ Employee of the Month (Mac)   │◀─────────────▶│ GPT-Live 1   │
│ (Telegram)   │  text messages  │                               │  PCM 16 kHz   │ (OpenAI API) │
└──────────────┘                 │ TDLib + tgcalls media runtime │  delegation   └──────────────┘
                                 │ conversation + safety logic   │
                                 └───────────────┬───────────────┘
                                                 │ HTTP API: read threads, answer
                                                 │ questions, send messages, create threads
                                                 ▼
                                 ┌───────────────────────────────┐
                                 │ T3 Code                       │
                                 │ your agent threads (Codex, …) │
                                 └───────────────────────────────┘
```

1. A T3 thread asks a structured question (T3's *ask user* feature).
2. The bridge notices it. After the delay (3 minutes by default), it calls your personal Telegram account from a separate sender account it is logged in to.
3. When you pick up, a native tgcalls runtime streams the call audio as raw PCM straight to a GPT-Live session. No virtual audio devices are involved. The session starts with the question, recent task messages and your latest calls.
4. The voice agent explains, discusses and reads your decision back. When something has to happen in T3, it delegates to the bridge. One small structuring request turns the transcript into checked JSON.
5. The bridge checks the question is still open and unchanged and that your confirmation is literally in the transcript. Then it answers the question through T3's API and waits until T3 reports it resolved.

The agent in T3 needs no special setup. From its point of view, you simply answered its question. More detail in [docs/architecture.md](docs/architecture.md).

## Requirements

| What | Why |
| --- | --- |
| A Mac with **Apple Silicon** that is awake and online | The Telegram media runtime is built locally for `darwin-arm64`. |
| **[T3 Code](https://github.com/pingdotgg/t3code)** running on that Mac | Source of the questions and target for answers. |
| **Two Telegram accounts** | A *sender* account the bridge logs in to, and your *personal* account it calls. |
| Your own **Telegram API ID** | Create one at [my.telegram.org](https://my.telegram.org/apps). |
| An **OpenAI API key** with access to GPT-Live 1 | Voice conversation and answer structuring. Billed separately from ChatGPT plans. |
| Python 3.11+, Xcode Command Line Tools, `cmake`, `gperf`, OpenSSL 3 | Building TDLib and the media runtime. |

## Setup tutorial

This is the complete path from a fresh clone to a first bounded call. Nothing starts automatically, and the offline check never opens the network, audio or a Telegram session.

> [!IMPORTANT]
> You need two Telegram accounts. Employee of the Month logs in as a dedicated sender account and calls your separate personal account. Do not configure both sides as the same account.

### 1. Install the build tools

Install Xcode Command Line Tools and the three Homebrew dependencies:

```sh
xcode-select --install
brew install cmake gperf openssl@3
```

You also need Python 3.11 or later, an Apple Silicon Mac and T3 Code running locally.

### 2. Clone and run the safe offline checks

```sh
git clone https://github.com/felixgollnhuber/employee-of-the-month.git
cd employee-of-the-month
make check
```

This builds only the small local test helper, generates a fixture and runs the unit tests. It does not log in, use credentials, touch audio devices, contact T3 or place a call.

### 3. Build the Telegram media runtime

```sh
make telegram-native
make telegram-runtime
make live-deps
make keychain-helper
source .build/live-venv/bin/activate
```

The first native build downloads and compiles TDLib, WebRTC and tgcalls. Plan for several gigabytes of free disk space and a longer build.

### 4. Create your private profile

Get a Telegram API ID and API hash from [my.telegram.org](https://my.telegram.org/apps), then run:

```sh
eotm configure
eotm configure-target
eotm configure-live --language en
```

- `configure` asks for the sender account's API credentials and phone number.
- `configure-target` asks for your personal account's public `@username`.
- `configure-live` asks for the OpenAI project API key and stores the conversation language. Add `--user-name Alex` if the agent should address you by name.

The prompts are local and hidden where appropriate. The resulting files live outside the repository with owner-only permissions.

### 5. Give the bridge scoped access to T3

Issue a dedicated T3 session token and save the JSON response in a private file:

```sh
mkdir -p "$HOME/.t3"
umask 077
t3 auth session issue --json --label employee-of-the-month > "$HOME/.t3/eotm-session.json"

eotm configure-t3 \
  --origin http://127.0.0.1:3773 \
  --credentials-file "$HOME/.t3/eotm-session.json"
```

Do not paste the token into a task, issue or shell argument. For a remote T3 instance, use HTTPS.

### 6. Log the sender account in to Telegram

```sh
eotm login
```

TDLib may ask for a login code and the sender account's existing two-factor password. The command does not create an account or place a call.

Once login succeeds, you can store the derived local database key in the macOS Keychain so the background service can restart without a passphrase prompt:

```sh
eotm remember-login --passphrase-dialog
```

### 7. Verify readiness and place one test call

```sh
eotm preflight
eotm voice-test --allow-call --seconds 60
```

The second command is the first real side effect: it calls your configured personal account. GPT-Live starts, and billing begins, only after the Telegram media connection is established. Keep this first test short and make sure no other watcher or service is using the same Telegram profile.

### 8. Run the service in the foreground

```sh
eotm serve-t3 \
  --all-projects \
  --daemon \
  --allow-messages-and-calls \
  --allow-task-creation \
  --seconds 1200
```

Leave it in the foreground until you have tested an actual T3 question, a missed-call message and a callback. Then follow [Running as a service](docs/service.md) to stage a fixed release and activate the LaunchAgent deliberately.

For screenshots-free, command-by-command explanations and upgrade notes, see the longer [getting started guide](docs/getting-started.md).

## Costs

You pay OpenAI directly for API usage:

- **GPT-Live 1:** $0.05 per active session minute, billed per second. A 5-minute call costs about $0.25. The session only starts once you pick up, so unanswered calls cost nothing.
- **Structuring requests:** a small `gpt-5.6-luna` request per delegated turn, usually a fraction of a cent per call. Set `"structuring_model": null` to use a T3 thread for this instead.

Prices as of September 2026. See [OpenAI pricing](https://developers.openai.com/api/docs/pricing). Telegram calls are free.

## Privacy and safety

- **Nothing leaves your Mac** except the call itself (Telegram), the conversation (OpenAI) and requests to your own T3 server.
- **No audio is recorded.** Text transcripts of recent calls are kept locally so the agent can pick up where you left off: at most 8 calls with 24,000 characters each.
- **Strict caller identity.** Calls and messages from any Telegram account other than your configured personal account are ignored.
- **Explicit gates.** Every command that can call, send messages or start T3 work requires an `--allow-…` flag. `make check` never touches the network.
- **No blind retries.** If the outcome of a T3 command or Telegram message is unclear after a crash, the bridge reports it and does not send again.

See [SECURITY.md](SECURITY.md) for how to report vulnerabilities.

## Status and limitations

Employee of the Month is an **experimental personal project** that is actively developed and used by its author. Expect rough edges:

- macOS on Apple Silicon only, and the Mac has to be awake.
- It relies on T3 Code's orchestration HTTP API. That API is not a stable public contract and may change.
- Only questions asked through T3's structured *ask user* feature trigger calls. Approval requests and plain-text questions do not.
- One configured person per installation.
- Voice recognition of short phrases like "hang up" works well, but is not perfect. Every call has a hard time limit (20 minutes at most).

## Documentation

| Guide | |
| --- | --- |
| [Getting started](docs/getting-started.md) | Build, configure and place your first call |
| [Usage](docs/usage.md) | What you can say and write, with examples |
| [Configuration](docs/configuration.md) | Profile files, language, voice and model settings |
| [CLI reference](docs/cli.md) | Every command and flag |
| [Running as a service](docs/service.md) | launchd setup, releases, health and logs |
| [Architecture](docs/architecture.md) | Components, call flow, state and safety model |
| [Troubleshooting](docs/troubleshooting.md) | Common problems and how to fix them |

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first. The short version: `make check` must stay offline, and no secrets go into the repository.

## Acknowledgements

Built on the shoulders of [TDLib](https://github.com/tdlib/td), [tgcalls](https://github.com/TelegramMessenger/tgcalls), [Telegram-iOS](https://github.com/TelegramMessenger/Telegram-iOS) and [WebRTC](https://webrtc.org/). They are downloaded at build time under their own licenses and are not part of this repository.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for pinned sources and license notes.

Employee of the Month is an independent project. It is not affiliated with or endorsed by Telegram, OpenAI or T3 Code.

## License

[MIT](LICENSE) © 2026 Felix Gollnhuber
