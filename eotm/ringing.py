"""One explicitly requested real ringing test, with no media or audio device access."""
import subprocess
import time

from .auth import AuthGate, existing_authenticated_client
from .config import read_profile, require_target
from .control import CallSession, GateError, validate_protocol
from .live import RequestPump, resolve_target
from .media import inspect_media
from .descriptor import SUPPORTED_VERSION


def macos_passphrase(_prompt):
    # The answer stays in this process. Never place it in arguments, logs or files.
    script = '''tell application "System Events"
activate
set answer to display dialog "Enter the local database passphrase for Employee of the Month, not your Telegram password." with title "Employee of the Month" default answer "" with hidden answer buttons {"Cancel", "Start"} default button "Start" cancel button "Cancel" giving up after 180
if gave up of answer then error number -128
return text returned of answer
end tell'''
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script],
                                capture_output=True, text=True, timeout=190)
    except (OSError, subprocess.TimeoutExpired):
        raise AuthGate("local_passphrase_dialog_unavailable") from None
    if result.returncode:
        raise AuthGate("local_passphrase_dialog_cancelled_or_unavailable")
    # osascript appends exactly one line terminator; preserve password whitespace.
    return result.stdout.removesuffix("\n")


class RingingSession(CallSession):
    def __init__(self, send, protocol, clock=time.monotonic):
        super().__init__(send, clock=clock, start_timeout=30)
        self.protocol = validate_protocol(protocol)
        self.delivered = False
        self.answered = False

    def start(self, target_user_id, *, authorized=False, consent=False):
        if self.phase != "idle":
            raise GateError("session_already_used")
        if not authorized or consent is not True:
            raise GateError("explicit_call_consent_required")
        if type(target_user_id) is not int or not 0 < target_user_id < 2**53:
            raise GateError("explicit_target_required")
        self.target = target_user_id
        self.phase = "dialing"
        self.created_at = self.clock()
        self.send({"@type": "createCall", "user_id": target_user_id,
                   "protocol": self.protocol, "is_video": False, "@extra": self.create_tag})

    def handle(self, event):
        call = event.get("call", {}) if event.get("@type") == "updateCall" else {}
        if self.call_id is not None and call.get("id") == self.call_id:
            state = call.get("state", {})
            if not self.stopping and self.phase not in ("ended", "failed"):
                if state.get("@type") == "callStatePending" and state.get("is_received") is True:
                    self.delivered = True
                if state.get("@type") in ("callStateExchangingKeys", "callStateReady"):
                    self.answered = True
                    self.end("ring_test_answered")
                    return  # Never pass Ready to the audio-session implementation.
        super().handle(event)

    def status(self):
        return {**super().status(), "mode": "ringing-only", "audio_opened": False,
                "delivery_reported": self.delivered, "answer_reported": self.answered}


def ring_once(td, username, protocol, *, consent=False, max_seconds=20,
              emit=lambda status: None, clock=time.monotonic):
    if consent is not True:
        raise GateError("explicit_call_consent_required")
    if type(max_seconds) is not int or not 1 <= max_seconds <= 30:
        raise GateError("bounded_ring_duration_required")
    session = RingingSession(td.send, protocol, clock)
    pump = RequestPump(td, clock)
    target = resolve_target(pump, username)
    pump.on_update = session.handle
    pump.on_tick = session.tick
    last_status = None

    def report():
        nonlocal last_status
        current = session.status()
        if current != last_status:
            emit(current)
            last_status = current

    try:
        session.start(target.user_id, authorized=True, consent=True)
        deadline = clock() + max_seconds
        while session.phase not in ("ended", "failed", "end_unconfirmed"):
            if clock() >= deadline:
                session.end("ring_test_timeout")
            report()
            pump.step()
    finally:
        session.end("controller_exit")
        cleanup_until = clock() + 5
        while session.phase not in ("ended", "failed", "end_unconfirmed", "idle") and clock() < cleanup_until:
            try:
                pump.step()
            except Exception:
                break
        if session.phase == "ending":
            session.phase = "end_unconfirmed"
        report()
    return session.status()


def run_authorized_ring_test(profile, library, probe, *, call_authorized=False,
                             max_seconds=20, secret_input=None, emit=lambda status: None):
    if call_authorized is not True:
        raise GateError("explicit_call_consent_required")
    if type(max_seconds) is not int or not 1 <= max_seconds <= 30:
        raise GateError("bounded_ring_duration_required")
    target = require_target(read_profile(profile))
    metadata = inspect_media(probe)
    if SUPPORTED_VERSION not in metadata.get("library_versions", []) or metadata.get("max_layer") != 92:
        raise GateError("native_protocol_not_verified")
    protocol = {"@type": "callProtocol", "udp_p2p": True, "udp_reflector": True,
                "min_layer": 65, "max_layer": 92, "library_versions": [SUPPORTED_VERSION]}
    with existing_authenticated_client(profile, library, secret_input=secret_input) as td:
        emit({"mode": "ringing-only", "telegram_authenticated": True, "audio_opened": False})
        return ring_once(td, target, protocol, consent=True, max_seconds=max_seconds, emit=emit)
