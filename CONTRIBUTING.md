# Contributing

Thanks for helping out! Employee of the Month places real phone calls, spends real API money and acts inside your T3 Code projects, so the project is deliberately strict about what runs when. Please read this page before opening a pull request.

## Ground rules

1. **Offline by default.** `make check` must never log in, open the network, touch audio devices or place a call. Anything that does is an explicit runtime action behind an `--allow-*` flag.
2. **Never commit private material.** No API keys, Telegram sessions, phone numbers, usernames, call recordings, transcripts, installers, app bundles or prebuilt binaries. The `.gitignore` covers the common cases; double-check your diff anyway.
3. **Fail closed.** If the bridge cannot confirm that something happened (an answer reached T3, a message was delivered, a call ended), it says so and does not retry blindly. Keep that property in new code.
4. **Keep measurements, user confirmation and open questions apart.** In docs and PR descriptions, say whether something was tested offline, tested against a real service, or confirmed by a person on a real call.

## Development setup

Requirements: macOS on Apple Silicon, Python 3.11+, the Xcode Command Line Tools (for `swiftc`).

```sh
git clone https://github.com/felixgollnhuber/employee-of-the-month.git
cd employee-of-the-month
make check
```

`make check` builds a small Swift test helper, generates a test tone and runs the full unit test suite. It needs no network access and no credentials.

Building the Telegram runtime (`make telegram-runtime`) is only needed to work on the native media code or to place real calls. See [docs/getting-started.md](docs/getting-started.md).

## Tests

- Tests live in `tests/` and use the standard library `unittest`. Run one file with `python3 -m unittest tests/test_followups.py`.
- Use the existing fakes for TDLib, T3 and the live voice socket instead of mocking the network.
- Bug fixes should come with a test that fails before the fix.
- Language heuristics (confirmation, cancel, hang-up and follow-up phrases) need tests for every supported language they touch.

## Code style

- Match the style of the surrounding module. The code base favours short, dense functions and explicit error codes (`GateError('t3_answer_rejected')`) over free-form exception messages.
- Error codes and log events are `snake_case` identifiers without personal data.
- Comments and docs are written in English.
- User-facing phrases go through `eotm/i18n.py` so every supported language stays in sync.

## Pull requests

- Keep PRs focused; separate refactors from behaviour changes.
- Describe what changed, why, and how you verified it (`make check`, a real call, a T3 run with a synthetic thread, ...).
- Update the docs in `docs/` when behaviour, configuration or CLI flags change.

## Reporting bugs and ideas

Open an issue using one of the templates. For security problems, follow [SECURITY.md](SECURITY.md) instead.
