# Getting started

Employee of the Month is an experimental macOS service that connects Telegram calls, OpenAI GPT-Live 1 and T3 Code. A full installation compiles TDLib, Telegram's native call stack and a small local media bridge. Nothing in this guide creates a Telegram account for you.

## Before you begin

You need:

- an Apple Silicon Mac that can remain awake and online,
- Python 3.11 or later,
- Xcode Command Line Tools,
- Homebrew packages `cmake`, `gperf` and `openssl@3`,
- T3 Code running on the same Mac,
- two Telegram accounts: one sender account for the service and one personal account that receives calls,
- a Telegram API ID and API hash from [my.telegram.org](https://my.telegram.org/apps),
- an OpenAI project API key with access to `gpt-live-1`.

The native build downloads and compiles large upstream projects. Allow several gigabytes of free disk space and expect the first build to take a while.

## 1. Clone and verify the offline path

```sh
git clone https://github.com/felixgollnhuber/employee-of-the-month.git
cd employee-of-the-month
make check
```

`make check` is deliberately offline. It does not log in, open audio devices, contact T3 or OpenAI, send Telegram messages or place calls.

## 2. Build the runtime

```sh
make telegram-native
make telegram-runtime
make live-deps
make keychain-helper
source .build/live-venv/bin/activate
```

The build outputs stay under `.build/` and are not committed. Upstream source and binaries retain their own licenses.

## 3. Create the private profile

The setup commands prompt locally with hidden input where appropriate:

```sh
eotm configure
eotm configure-target
eotm configure-live --language en
```

Use the sender account's phone number in `configure`. Use the personal receiving account's public Telegram username in `configure-target`. The two accounts must be different.

Private files are stored outside the repository in an owner-only directory. See [configuration.md](configuration.md) for the exact files and upgrade behavior.

## 4. Configure T3 access

Employee of the Month needs a scoped T3 bearer token for thread snapshots and orchestration commands. With the T3 CLI available:

```sh
t3 auth session issue --json --label employee-of-the-month
```

Store the returned token in an owner-only JSON file and reference it from the profile's `t3.json`. Do not paste the token into an issue, shell history or task chat. The exact file schema is documented in [configuration.md](configuration.md#t3json).

The local T3 server normally listens on `http://127.0.0.1:3773`. Remote origins must use HTTPS. Redirects are rejected.

## 5. Log in to Telegram

```sh
eotm login
```

This is an explicit runtime action. TDLib may ask for a login code or existing two-factor password. The command does not create an account or place a call.

After the encrypted TDLib database exists, you can store its derived database key in the macOS Keychain:

```sh
eotm remember-login --passphrase-dialog
```

The passphrase itself is not written to the profile.

## 6. Check readiness

```sh
eotm preflight
```

The command validates local files and dependencies without opening a Telegram session, audio device or network connection.

## 7. Place one bounded test call

Only do this when no other process is using the same Telegram profile:

```sh
eotm voice-test --allow-call --seconds 60
```

This starts a real Telegram call and a billable GPT-Live session after the call connects. `--allow-call` is required. A missed or rejected call does not start the GPT-Live session.

## 8. Run the service

For all T3 projects:

```sh
eotm serve-t3 \
  --all-projects \
  --daemon \
  --allow-messages-and-calls \
  --allow-task-creation \
  --seconds 1200
```

The foreground command is the clearest first production-style test. Once it behaves correctly, follow [service.md](service.md) to stage a fixed release and install a LaunchAgent. Do not run a second watcher or service against the same Telegram profile.

## What to read next

- [Usage](usage.md) for calls, callbacks, text replies and thread messages.
- [Configuration](configuration.md) for every private file and option.
- [Architecture](architecture.md) for the data flow and safety properties.
- [Troubleshooting](troubleshooting.md) when a build, login, call or T3 operation fails.
