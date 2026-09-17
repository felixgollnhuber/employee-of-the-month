# Troubleshooting

## `make check` fails

- Confirm `python3 --version` is 3.11 or later.
- Run `xcode-select -p` and install Xcode Command Line Tools if `swiftc` is missing.
- Treat a Swift warning separately from a failing exit code.
- `make check` must not need network access, credentials, audio permissions or a running T3 instance.

## Native build fails

- Confirm `cmake`, `gperf` and OpenSSL 3 are installed.
- Make sure several gigabytes are free under `.build/`.
- Do not copy proprietary SDK source into the repository to work around a missing dependency.
- Re-run the exact failing build command before deleting caches. The first build is intentionally expensive.

## Telegram login is rejected

- The sender phone number must be E.164, such as `+43123456789`.
- The database directory and all profile files must be owned by you and private.
- Only use the login code delivered to the sender account. The project never creates an account.
- If a previous process owns the same profile, stop that exact process first.

## The service cannot call the target

- Verify the public username has not changed.
- The target must be a regular private user, not the sender account, bot or group.
- Telegram privacy settings must allow calls.
- Run `eotm preflight`, then use the bounded `ring-test` before a billable voice test.

## The call connects but is silent

- Confirm the native media runtime was built from the same checkout and passed its PCM self-test.
- Confirm GPT-Live starts only after the Telegram media connection reports connected.
- A LaunchAgent must use the precise-timer settings described in [service.md](service.md).
- Do not run audio tests while another real call is active.

## T3 requests fail

- Confirm T3 is running and the `origin` in `t3.json` is correct.
- Issue a fresh scoped token with `t3 auth session issue` if the session expired or was revoked.
- Remote T3 endpoints require HTTPS. Redirects and origins with embedded user information are rejected.
- A transport receipt only proves command acceptance. The service waits for thread-state readback before claiming completion.

## No automatic call happens

- Only structured T3 user-input requests qualify.
- The default delay is three minutes from the original request timestamp.
- Each question is dialed at most once automatically.
- `--max-calls 0`, a global call-spacing window or a previous durable attempt can suppress dialing.
- A settled, archived or internal coordinator thread is not selected as a normal source.

## A message is reported as unconfirmed

The service reserves every send before dispatch. If the connection fails at the wrong time, it cannot safely know whether the receiver accepted the message. It reads back the same message ID but does not resend blindly. Inspect the target thread or Telegram chat directly before taking another action.

## Safe information to include in an issue

Include the command name, redacted JSON event names, commit hash, macOS version, Python version and whether the result came from an offline test or a real service. Remove tokens, phone numbers, usernames, profile paths that identify a person and all call transcript text.
