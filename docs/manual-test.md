# Legacy desktop-audio test

The current production path streams Telegram PCM directly to GPT-Live and does not need virtual macOS audio devices. This page documents the older desktop-audio helper for contributors who still need to test it.

These commands are separate from `make check`. They can open audio devices and may require microphone permission. Never run them during another voice call.

Build the helper:

```sh
make build
make fixture
```

The helper expects exact, explicitly configured device UIDs and never changes the global default input or output device. A routing application must be installed and configured separately. This repository does not install or license one.

Common modes:

| Mode | Effect |
| --- | --- |
| `--check-devices` | Inventory and validate the two configured names without opening an audio engine. |
| `--prepare FILE` | Open the file, prepare both graphs and verify device binding without starting playback. |
| `--playback FILE` | Play the file into the configured outbound route. |
| `--meter-input FILE` | Measure the inbound route for six seconds without playback. |
| `--meter-return FILE` | Measure the return route for six seconds without playback. |

Metering stores aggregate levels only, not audio samples. A successful exit confirms received frames, not audible content or a correct remote reply. Stop the phone call and any separate voice session manually after a test.
