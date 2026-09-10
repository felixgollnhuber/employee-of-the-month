#!/usr/bin/env python3
"""Real native IPC/Descriptor tests. Never pass --allow-audio or log payloads."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
from test_descriptor import fixture_ready
from telegram_bridge.descriptor import normalize_ready
from telegram_bridge.runtime import DEFAULT_RUNTIME, NativeIPC, NativeMedia, validate_native_descriptor
from telegram_bridge.control import GateError

exe = root / DEFAULT_RUNTIME
ready = fixture_ready()
descriptor = normalize_ready(ready, "fixture.input.uid", "fixture.output.uid")
checks = 0
result = validate_native_descriptor(ready, "fixture.input.uid", "fixture.output.uid", exe)
assert result == {"native_descriptor": "validated", "call_created": False, "audio_opened": False}
checks += 1

requests = [{"id": 1, "op": "prepare", "descriptor": descriptor},
            {"id": 2, "op": "start"}, {"id": 3, "op": "status"},
            {"id": 4, "op": "stop"}, {"id": 5, "op": "stop"}]
run = subprocess.run([str(exe)], input="\n".join(json.dumps(x) for x in requests) + "\n",
                     text=True, capture_output=True, timeout=10, check=True)
replies = {x["id"]: x for x in map(json.loads, run.stdout.splitlines())}
assert replies[1]["prepared"] is True
assert replies[1]["key_bytes"] == 256 and replies[1]["relay_count"] == 5 and replies[1]["audio_only"] is True
assert replies[2]["ok"] is False  # Native authorization gate, not a Python-only check.
assert replies[3]["call_created"] is False and replies[3]["audio_authorized"] is False
assert replies[4]["stopped"] is True and replies[5]["stopped"] is True
assert descriptor["key_hex"] not in run.stdout and "fixture-turn-password" not in run.stdout
assert run.stderr == ""
checks += 4

for invalid in ({**descriptor, "version": "unknown"}, {**descriptor, "key_hex": "bad"},
                {**descriptor, "input_uid": "default"}):
    ipc = NativeIPC(exe)
    try:
        try: ipc.request("prepare", descriptor=invalid)
        except GateError: pass
        else: raise AssertionError("Native invalid descriptor accepted")
        assert ipc.request("status")["call_created"] is False
    finally: ipc.close()
    checks += 1

backend = NativeMedia("fixture.input.uid", "fixture.output.uid", executable=exe)
assert backend.available is False
try: backend.start(ready, lambda _: None, lambda _: None)
except GateError: pass
else: raise AssertionError("Audio was enabled without consent")
assert backend.ipc is None
checks += 1
print(json.dumps({"mode": "native-runtime-offline", "checks_passed": checks,
                  "real_descriptor_constructed": True, "call_created": False, "audio_opened": False}))
