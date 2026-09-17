# Configuration

Configuration and runtime state are intentionally kept outside the repository. Every directory must be owned by the current user with mode `0700`; every JSON file must be a regular owner-only file with mode `0600`. Symlinks are rejected.

## Profile location

New installations use:

```text
~/Library/Application Support/EmployeeOfTheMonth/telegram/<profile>/
```

The default profile name is `default`. Existing installations under `~/Library/Application Support/CodexPhoneBridge/telegram/` remain supported and are not moved automatically because the TDLib database and Keychain binding depend on the exact path.

## `config.json`

Created by `eotm configure`:

```json
{
  "api_id": 123456,
  "api_hash": "0123456789abcdef0123456789abcdef",
  "sender_phone": "+43123456789"
}
```

The file identifies the dedicated sender account. It is never printed by the CLI.

## `target.json`

Created by `eotm configure-target`:

```json
{
  "target_username": "@your_personal_account"
}
```

The service resolves only this exact public username and verifies that it still belongs to a regular private Telegram user who can receive calls. It never searches the contact list.

## `live.json`

Created by `eotm configure-live`:

```json
{
  "api_key": "sk-...",
  "voice": "cedar",
  "language": "en",
  "user_name": "Felix",
  "agent_name": "Employee of the Month",
  "structuring_model": "gpt-5.6-luna"
}
```

Fields other than `api_key` are optional:

| Field | Values | Default |
| --- | --- | --- |
| `voice` | A voice supported by GPT-Live 1 | `cedar` |
| `language` | `en` or `de` | `en` for new profiles; `de` for legacy profiles without the field |
| `user_name` | 1 to 80 printable characters | `you` in English, `du` in German |
| `agent_name` | 1 to 80 printable characters | `Employee of the Month` |
| `structuring_model` | A valid OpenAI model ID, or `null` | `gpt-5.6-luna` |

Setting `structuring_model` to `null` disables the direct Responses API classification path. T3 coordinator threads are then used as the slower fallback.

## `t3.json`

Create this file with mode `0600` after issuing a T3 session token:

```json
{
  "origin": "http://127.0.0.1:3773",
  "credentials_file": "/Users/you/.t3/eotm-session.json"
}
```

The referenced credentials file contains the JSON returned by `t3 auth session issue --json`, including its `token` field. It must be an owner-only regular file and must not be a symlink. Loopback HTTP is allowed. Any non-loopback origin must use HTTPS. User information, fragments, query strings and redirects are rejected. The token is sent only in the `Authorization` header.

## Generated private state

The profile can also contain:

| Path | Purpose |
| --- | --- |
| `database/`, `files/`, `key-salt` | Encrypted TDLib state and local key derivation salt |
| `database-keychain.json` | Binding metadata for the macOS Keychain entry, never the key itself |
| `conversations.json` | Durable operation, delivery and deduplication state, capped at 8 MiB |
| `call-history.json` | Up to eight bounded text transcripts, with common secret forms redacted |
| `watch-attempts.json` | Outbound attempt history for the bounded watcher |
| `service-health.json` | Current service PID, heartbeat and redacted health information |
| `session.lock`, `watch.lock` | Exclusive ownership locks |

Audio is not recorded. A process crash can lose the last unsaved second of a text transcript, but must not cause an automatic repeat of an uncertain message or T3 mutation.

## Environment and command-line values

Secrets are not accepted as normal command-line options. This avoids shell history and process-list exposure. Runtime paths such as `--library` and `--media-runtime` may be supplied explicitly when using a separately staged build.
