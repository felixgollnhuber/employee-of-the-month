"""Private pipe transport to the real tgcalls helper, with explicit audio gate."""
import base64
import json
from pathlib import Path
import subprocess
import threading
import time

from .control import GateError
from .descriptor import SUPPORTED_VERSION, decode_bytes, device_uid, normalize_ready

DEFAULT_RUNTIME = Path(".build/vendor/telegram-ios/bazel-bin/bridge_probe/media_runtime")


class NativeIPC:
    def __init__(self, executable=DEFAULT_RUNTIME, *, allow_audio=False, on_state=None, on_signal=None, on_pcm=None):
        self.on_state = on_state or (lambda _: None)
        self.on_signal = on_signal or (lambda _: None)
        self.on_pcm = on_pcm
        self.guard = threading.Condition()
        self.writer = threading.Lock()
        self.replies = {}
        self.sequence = 0
        self.closed = False
        self.failed = False
        self.closing = False
        command = [str(Path(executable).resolve(strict=True))]
        if allow_audio:
            command.append("--allow-audio")
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(1024 * 1024 + 2)
                if not line:
                    break
                if len(line) > 1024 * 1024 or not line.endswith("\n"):
                    raise GateError("invalid_native_frame")
                event = json.loads(line)
                if event.get("event") == "state":
                    state = event.get("state")
                    if state not in ("connected", "connecting", "reconnecting", "failed"):
                        raise GateError("invalid_native_state")
                    self.on_state(state)
                elif event.get("event") == "signaling":
                    data = bytes.fromhex(event["data_hex"])
                    if len(data) > 512 * 1024:
                        raise GateError("native_signaling_too_large")
                    self.on_signal(base64.b64encode(data).decode())
                elif event.get("event") == "pcm" and self.on_pcm:
                    data = bytes.fromhex(event["data_hex"])
                    if len(data) != 320:
                        raise GateError("invalid_native_pcm_frame")
                    self.on_pcm(data)
                elif type(event.get("id")) is int:
                    with self.guard:
                        if len(self.replies) >= 8:
                            raise GateError("native_reply_overflow")
                        self.replies[event["id"]] = event
                        self.guard.notify_all()
                else:
                    raise GateError("invalid_native_reply")
        except Exception:
            self.failed = True
        finally:
            with self.guard:
                self.closed = True
                self.guard.notify_all()
            if self.failed or not self.closing:
                self.on_state("failed")

    def request(self, op, timeout=8, **fields):
        with self.writer:
            self.sequence += 1
            request_id = self.sequence
            payload = json.dumps({"id": request_id, "op": op, **fields}, separators=(",", ":"), allow_nan=False)
            if len(payload) > 1024 * 1024:
                raise GateError("native_request_too_large")
            try:
                self.process.stdin.write(payload + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                raise GateError("native_transport_closed") from None
            deadline = time.monotonic() + timeout
            with self.guard:
                while request_id not in self.replies and not self.closed:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise GateError("native_reply_timeout")
                    self.guard.wait(remaining)
                result = self.replies.pop(request_id, None)
            if not result or result.get("ok") is not True:
                code = result.get("error") if result else None
                if code in ("audio_test_not_authorized", "exact_audio_devices_unavailable", "not_prepared",
                            "session_already_used", "unsupported_version_or_direction", "media_not_started", "pcm_buffer_rejected"):
                    raise GateError("native_" + code)
                raise GateError("native_request_rejected")
            return result

    def close(self):
        self.closing = True
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=7)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
            raise GateError("native_exit_unconfirmed") from None
        finally:
            self.reader.join(timeout=1)
            self.process.stdout.close()


class NativeMedia:
    def __init__(self, input_uid, output_uid, *, allow_audio=False, executable=DEFAULT_RUNTIME, on_pcm=None):
        self.input_uid, self.output_uid = device_uid(input_uid), device_uid(output_uid)
        if input_uid == output_uid:
            raise GateError("separate_audio_directions_required")
        self.executable = Path(executable).resolve(strict=True)
        self.available = allow_audio is True
        self.ipc = None
        self.used = False
        self.relay = 0
        self.on_pcm = on_pcm

    def descriptor(self, ready):
        return normalize_ready(ready, self.input_uid, self.output_uid)

    def protocol(self):
        # TDLib's pinned schema specifies minimum 65, maximum 92. Advertise only
        # the V2 implementation path explicitly supported by this adapter.
        return {"@type": "callProtocol", "udp_p2p": True, "udp_reflector": True,
                "min_layer": 65, "max_layer": 92, "library_versions": [SUPPORTED_VERSION]}

    def start(self, ready, on_state, on_signal):
        if not self.available or self.used:
            raise GateError("audio_not_authorized_or_session_used")
        self.used = True
        descriptor = self.descriptor(ready)
        self.ipc = NativeIPC(self.executable, allow_audio=True, on_state=on_state, on_signal=on_signal, on_pcm=self.on_pcm)
        try:
            self.ipc.request("prepare", descriptor=descriptor)
            self.ipc.request("start")
        except Exception as error:
            try:
                self.ipc.close()
            finally:
                self.ipc = None
            if isinstance(error, GateError):
                raise error
            raise GateError("native_media_start_failed") from None

    def receive_signaling(self, data):
        if self.ipc is None:
            raise GateError("native_media_not_started")
        self.ipc.request("signal", data_hex=decode_bytes(data).hex())

    def stop(self):
        if self.ipc is None:
            return
        ipc, self.ipc = self.ipc, None
        try:
            result = ipc.request("stop")
            if result.get("stopped") is not True:
                raise GateError("native_stop_unconfirmed")
            self.relay = int(result.get("relay_id", "0"))
        finally:
            ipc.close()

    def relay_id(self):
        return self.relay


class NativePcmMedia(NativeMedia):
    def __init__(self, *, on_pcm, allow_audio=False, executable=DEFAULT_RUNTIME):
        from .descriptor import PCM_INPUT, PCM_OUTPUT
        super().__init__(PCM_INPUT, PCM_OUTPUT, allow_audio=allow_audio, executable=executable, on_pcm=on_pcm)

    def descriptor(self, ready):
        from .descriptor import normalize_pcm_ready
        return normalize_pcm_ready(ready)

    def push_audio(self, data):
        if not data or len(data) % 2 or len(data) > 64000:
            raise GateError("invalid_live_pcm")
        ipc = self.ipc
        if ipc is None:
            raise GateError("native_media_not_started")
        ipc.request("pcm", data_hex=data.hex())


def validate_native_descriptor(ready, input_uid, output_uid, executable=DEFAULT_RUNTIME):
    """Construct a real native Descriptor only, never Meta::Create or devices."""
    ipc = NativeIPC(executable)
    try:
        result = ipc.request("prepare", descriptor=normalize_ready(ready, input_uid, output_uid))
        if result.get("audio_opened") is not False or result.get("call_created") is not False:
            raise GateError("unexpected_native_prepare_side_effect")
        status = ipc.request("status")
        if status.get("call_created") is not False or status.get("audio_authorized") is not False:
            raise GateError("unexpected_native_status")
        ipc.request("stop")
        return {"native_descriptor": "validated", "call_created": False, "audio_opened": False}
    finally:
        ipc.close()
