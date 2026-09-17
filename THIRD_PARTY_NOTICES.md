# Third-party notices

The repository does not vendor or redistribute the large upstream source trees or their compiled binaries. Build scripts download pinned revisions into `.build/` for a local build. Those projects remain under their own licenses.

| Component | Pinned source | License noted by this project | Use |
| --- | --- | --- | --- |
| TDLib | [tdlib/td](https://github.com/tdlib/td) | Boost Software License 1.0 | Telegram client and signaling |
| tgcalls | [TelegramMessenger/tgcalls](https://github.com/TelegramMessenger/tgcalls) | LGPL 3.0 | Telegram call media |
| Telegram-iOS | [TelegramMessenger/Telegram-iOS](https://github.com/TelegramMessenger/Telegram-iOS) | GPL 2.0 | Parent dependency tree used for the compatible native build |
| WebRTC fork | [ali-fareed/webrtc](https://github.com/ali-fareed/webrtc) | See upstream notices | Audio device module and transport dependencies |
| Bazel | [bazelbuild/bazel](https://github.com/bazelbuild/bazel) | Apache 2.0 | Native build tool |
| websockets | [python-websockets/websockets](https://github.com/python-websockets/websockets) | BSD 3-Clause | GPT-Live and T3 WebSocket clients |

Exact revisions and the Bazel checksum are recorded in `dependencies.json`.

The MIT license in [LICENSE](LICENSE) applies only to this repository's own material. Anyone distributing a combined binary or source bundle is responsible for reviewing and satisfying the corresponding upstream license obligations. This notice is informational and is not legal advice.
