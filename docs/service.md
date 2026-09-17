# Running as a service

Run the foreground command successfully before installing a background service. A LaunchAgent can place real calls, send messages and spend OpenAI API credit whenever the configured conditions are met.

## Exclusive profile ownership

One profile can have only one active TDLib client. The service holds `session.lock` and `watch.lock`. Stop any earlier watcher by its exact PID and command line before starting the service. Never kill processes by a broad name pattern.

## Fixed releases

The deployment helper stages a content-addressed release under:

```text
~/Library/Application Support/EmployeeOfTheMonth/service/releases/<release-id>/
```

Only the Python package, `libtdjson.dylib` and the native media runtime are copied. Credentials, transcripts and working-tree files are excluded. Editing or rebuilding the repository does not alter a running release.

The generated LaunchAgent uses `ProcessType=Interactive` and `LegacyTimers=true`. The native media path needs reliable 10 ms callbacks, which macOS can coalesce for a background-classified process.

## Installing the LaunchAgent

Stage the release after building TDLib and the native media runtime:

```sh
eotm install-service
```

The command only copies a fixed release and writes the private plist. It does not load or start anything. Review the generated plist before loading it.

Install and start it deliberately:

```sh
mkdir -p ~/Library/LaunchAgents
cp "$HOME/Library/Application Support/EmployeeOfTheMonth/service/launch-agent.plist" \
  "$HOME/Library/LaunchAgents/dev.felixgollnhuber.employee-of-the-month.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/dev.felixgollnhuber.employee-of-the-month.plist"
```

Loading the plist is a separate conscious runtime action. Staging alone never starts the service.

## Health and logs

`service-health.json` in the private profile contains the PID, last heartbeat, project scope, active call marker and redacted backend errors. The service log lives beside the staged releases and must remain owner-only.

T3 must be reachable and the Mac must be awake and online. Restarting this service does not restart T3 threads or their provider processes.

## Stop and update

```sh
launchctl bootout "gui/$(id -u)/dev.felixgollnhuber.employee-of-the-month"
```

Wait until the exact service process has exited before staging or starting another release. A graceful stop drains newly queued Telegram messages for a bounded period. Uncertain older sends are never retried automatically.

There is no automatic updater. Build, test, stage and activate each release explicitly.
