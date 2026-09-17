# Validation evidence and limits

This document separates offline verification, observed service state and human confirmation. It intentionally contains no credentials, phone numbers, account names or transcript dumps.

## Offline verification

The repository's primary gate is:

```sh
make check
```

It builds the small Swift helper, generates a local audio fixture, runs static offline checks and executes the Python unit suite. The suite covers authorization gates, target identity, TDLib call lifecycle, native media descriptors, GPT-Live framing, question binding, confirmation quotes, follow-up delivery, callback context, task creation, service recovery and LaunchAgent staging.

The gate does not open the network, audio devices, Telegram, T3, OpenAI or macOS Keychain.

## Historical live checks

Between September 10 and September 17, 2026, the author performed bounded tests on one Mac with the private runtime:

- one real Telegram and GPT-Live call confirmed intelligible audio in both directions,
- a corrected decision reached the exact pending T3 request,
- a spoken hang-up request closed GPT-Live and Telegram with confirmed media cleanup,
- a clarification round trip returned a real work-thread explanation without pretending the original decision was complete,
- a callback continued an open operation,
- a separate incoming call returned a bounded T3 status summary,
- the direct structuring path was activated and exercised in two real calls.

These observations validate specific paths at those times. They do not prove availability under network loss, every Telegram client version or every voice accent.

## LaunchAgent timer finding

A local reproduction showed that a background-classified LaunchAgent could coalesce a 10 ms callback loop to roughly 100 ms. The staged plist therefore uses `ProcessType=Interactive` and `LegacyTimers=true`. A synthetic timer test confirmed the expected callback frequency after the change. This test did not use audio or the network.

## Open live cases

The following cases still benefit from deliberate real-device testing:

- rejection and network loss during call setup,
- a Telegram text answer while the phone is still ringing,
- simultaneous open operations,
- a foreign account attempting to call or message the sender account,
- English-language end-to-end voice interaction,
- a fresh audible check of the wait tone and goodbye pacing on the installed release.

Do not convert an offline result into a live-service claim. Record future checks with the commit, fixed release ID, command, observed machine state and explicit human confirmation where audibility matters.
