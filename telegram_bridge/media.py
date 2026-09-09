"""Read real native media capabilities without creating a media instance."""
import json
from pathlib import Path
import subprocess

from .control import GateError


DEFAULT_PROBE = Path(".build/vendor/telegram-ios/bazel-bin/bridge_probe/media_probe")


def inspect_media(probe=DEFAULT_PROBE):
    result = subprocess.run([str(Path(probe).resolve(strict=True))], check=True,
                            capture_output=True, text=True, timeout=15)
    data = json.loads(result.stdout)
    if data.get("mode") != "native-media-metadata-only":
        raise GateError("unexpected_media_probe")
    if any(data.get(key) is not False for key in ("call_created", "audio_opened", "live_adapter_ready")):
        raise GateError("media_probe_is_not_offline")
    if type(data.get("max_layer")) is not int or data["max_layer"] <= 0:
        raise GateError("invalid_native_layer")
    if not data.get("library_versions"):
        raise GateError("missing_native_versions")
    return data
