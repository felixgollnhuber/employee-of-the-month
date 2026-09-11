"""Apply a narrow fail-closed device-ID hook to the pinned macOS ADM source.

No audio algorithm, protocol, permissions or crypto changes. Never uses a
default device/index in the bridge build. Other WebRTC builds are unaffected.
"""
from pathlib import Path
import subprocess


def apply_guard(webrtc):
    relative = "modules/audio_device/mac/audio_device_mac.cc"
    changed = subprocess.check_output(["git", "-C", str(webrtc), "diff", "HEAD", "--name-only"], text=True).splitlines()
    if set(changed) - {relative}:
        raise RuntimeError("Unknown WebRTC source changes; preserving checkout")
    original = subprocess.check_output(["git", "-C", str(webrtc), "show", "HEAD:" + relative]).decode()
    declaration = '#ifdef CODEX_BRIDGE_STRICT_AUDIO\nextern "C" bool bridge_resolve_audio_device(bool input, uint32_t *result);\n#endif\n\n'
    anchor = "namespace webrtc {\n"
    function = "                                   const bool isInput) {\n"
    if original.count(anchor) != 1 or original.count(function) != 1:
        raise RuntimeError("Pinned ADM structure changed; refusing patch")
    patched = original.replace(anchor, declaration + anchor).replace(function, function +
        "#ifdef CODEX_BRIDGE_STRICT_AUDIO\n  return bridge_resolve_audio_device(isInput, &deviceId) ? 0 : -1;\n#endif\n")
    path = Path(webrtc) / relative
    if path.read_text() not in (original, patched):
        raise RuntimeError("Unknown ADM changes; preserving source")
    path.write_text(patched)


if __name__ == "__main__":
    import sys
    apply_guard(Path(sys.argv[1]))
