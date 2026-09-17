# CLI reference

After `make live-deps` and activation of `.build/live-venv`, use the `eotm` command. `python -m eotm` is equivalent.

## Offline commands

| Command | Purpose |
| --- | --- |
| `eotm demo` | Run a fixture-only start, status and end simulation. |
| `eotm native-check` | Load TDLib without login, network setup or audio. |
| `eotm media-check` | Inspect the linked native media capabilities without opening media. |
| `eotm preflight` | Validate local profile and build readiness without opening a session. |

## Configuration commands

| Command | Purpose |
| --- | --- |
| `eotm configure` | Store Telegram API credentials and the sender phone number with hidden local input. |
| `eotm configure-target` | Store the personal recipient's public Telegram username. |
| `eotm configure-live` | Store the OpenAI key and conversation preferences. |
| `eotm configure-t3` | Store a validated T3 origin and private credentials-file reference without contacting T3. |
| `eotm configure-audio` | Configure the legacy desktop-audio path. No audio starts. |
| `eotm remember-login` | Verify the existing TDLib database and store its derived key in macOS Keychain. |
| `eotm install-service` | Stage a fixed private release and LaunchAgent plist without starting it. |

## Explicit runtime commands

| Command | Side effects |
| --- | --- |
| `eotm login` | Opens Telegram login and may request a login code. |
| `eotm ring-test --allow-call` | Places one bounded Telegram call without audio. |
| `eotm voice-test --allow-call` | Places one bounded call and starts billable GPT-Live audio after connection. |
| `eotm watch-t3 --project-id ID --allow-calls` | Watches one project for a bounded period and may place calls. |
| `eotm serve-t3 --all-projects --allow-messages-and-calls` | Runs the full Telegram and T3 service. |

Use `eotm <command> --help` for every option. Important service flags:

- `--project-id ID` or `--all-projects` selects the T3 scope.
- `--daemon` removes the planned service stop time.
- `--allow-task-creation` permits confirmed voice proposals to create T3 threads.
- `--max-calls 0` disables automatic outbound calls while leaving messages and incoming calls active.
- `--question-delay-seconds 180` controls the first-contact delay.
- `--seconds` bounds each call. The service accepts at most 1200 seconds.
- `--library` and `--media-runtime` select explicitly built native artifacts.

All commands emit JSON objects for machine-readable status. Error events intentionally omit raw exception text.
