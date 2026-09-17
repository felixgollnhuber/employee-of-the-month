# Security Policy

Employee of the Month holds sensitive material on your machine: a logged-in Telegram session, an OpenAI API key, a T3 Code access token and transcripts of your phone calls. It can also place calls, send Telegram messages and start work in T3 Code. Security reports are very welcome.

## Supported versions

The project has no stable release line yet. Security fixes land on `main`.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private vulnerability reporting instead: open the repository's **Security** tab and choose **Report a vulnerability**. Include:

- what an attacker can do and what they need (local user, a message from another Telegram account, a crafted T3 thread, ...),
- steps to reproduce, ideally with the offline test doubles in `tests/`,
- the commit you tested.

Never include real API keys, Telegram session files, phone numbers or call transcripts in a report.

You can expect a first reply within a week.

## Scope

Examples of what is in scope:

- Telegram accounts other than the configured target reaching the voice agent, project data or T3 commands.
- A spoken or written answer reaching T3 without the confirmation checks described in [docs/architecture.md](docs/architecture.md#safety-model).
- Secrets or transcripts being logged, written with loose permissions or sent to unexpected hosts.
- Commands being replayed after a crash or restart.

Out of scope: vulnerabilities in Telegram, TDLib, tgcalls, WebRTC, T3 Code or the OpenAI API themselves. Please report those upstream.

## Hardening already in place

- All private files live outside the repository with mode `0600` in `0700` directories. Symlinks and files owned by other users are rejected.
- The TDLib database is encrypted. Its key can be kept in the macOS Keychain instead of retyping a passphrase.
- Only the single configured Telegram user can talk to the agent, by text or by call.
- Remote T3 origins require HTTPS. HTTP redirects are rejected for T3 and OpenAI requests.
- Error output deliberately omits exception messages because they can contain personal data.
- Common secret formats (API keys, bearer tokens, private keys, `password: ...` lines) are redacted from task context and stored transcripts.
