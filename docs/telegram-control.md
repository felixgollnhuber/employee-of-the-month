# Telegram control and native media

This document describes the lower-level call controller. Most users should start with [getting-started.md](getting-started.md).

## Authorization order

The controller fails closed in this order:

1. validate the explicit runtime gate and bounded duration,
2. validate native media capabilities,
3. open the existing encrypted TDLib database,
4. verify the logged-in sender identity,
5. resolve only the configured public target username,
6. verify it is a different regular private user that can receive calls,
7. create the call.

No contact list is read. No account is created. The target phone number is not required or stored.

## Call ownership

One `CallSession` owns one call ID. Updates received before the create response are buffered and replayed only after ownership is confirmed. Updates for other calls cannot start media or end the owned call.

Call phases distinguish dialing, connecting, active, reconnecting, ending, ended, failed and unconfirmed end. A TDLib ready event alone is not described as audible. Media connection must also be confirmed.

## Native protocol

The tgcalls runtime exchanges newline-delimited JSON with Python. Binary values use validated base64 fields. Descriptor validation checks protocol layer, library version, key length, server addresses, reflector mapping and media direction before any engine starts.

Incoming and outgoing media use separate cryptographic direction flags. Signaling is scoped to the owned call and is rejected after shutdown.

## PCM bridge

The native runtime and GPT-Live exchange signed 16-bit, 16 kHz mono PCM in memory. Output is paced in real time before it reaches Telegram. The service does not write audio recordings.

## Explicit test commands

- `eotm ring-test --allow-call` places a short call without audio and ends when answered or timed out.
- `eotm voice-test --allow-call` adds GPT-Live audio after media connection.
- `eotm watch-t3 --allow-calls` is a bounded one-project watcher.
- `eotm serve-t3 --allow-messages-and-calls` runs the full service.

Each command requires its own authorization flag. An offline build or test never implies permission for a real call.
