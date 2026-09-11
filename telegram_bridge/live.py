"""Authenticated target resolution and single-call event loop.

Importing this module does nothing. No CLI enables it without a native media backend.
The supplied TDJson client must already be explicitly opened/authenticated by its owner.
"""
from dataclasses import dataclass
import queue
import re
import time
import uuid

from .control import CallSession, GateError, validate_protocol


class RequestError(GateError):
    pass


class RequestPump:
    def __init__(self, td, clock=time.monotonic):
        self.td = td
        self.clock = clock
        self.on_update = lambda event: None
        self.on_tick = lambda: None
        self.on_event = lambda event: None

    def request(self, kind, timeout=10, **fields):
        tag = "rpc-" + uuid.uuid4().hex
        self.td.send({"@type": kind, "@extra": tag, **fields})
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            event = self.step()
            if event and event.get("@extra") == tag:
                if event.get("@type") == "error":
                    code = event.get("code")
                    raise RequestError("telegram_request_error_" + (str(code) if type(code) is int else "unknown"))
                return event
        raise RequestError("telegram_request_timeout")

    def step(self):
        event = self.td.receive(0.1)
        if event:
            self.on_event(event)
            if event.get("@type") == "updateAuthorizationState":
                state = event.get("authorization_state", {}).get("@type")
                if state != "authorizationStateReady":
                    raise GateError("authenticated_session_lost")
            # Ignore messages, contact data, files, photos and all unrelated updates.
            if event.get("@type") in ("updateCall", "updateNewCallSignalingData", "callId", "error", "ok"):
                self.on_update(event)
        self.on_tick()
        return event


def require_authorized(pump):
    state = pump.request("getAuthorizationState")
    if state.get("@type") != "authorizationStateReady":
        raise GateError("telegram_login_required")


@dataclass(frozen=True)
class ResolvedTarget:
    user_id: int


def resolve_target(pump, username):
    """Resolve only the explicitly configured public username, never a contact list."""
    if not isinstance(username, str) or not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", username):
        raise GateError("explicit_target_username_required")
    require_authorized(pump)
    me = pump.request("getMe")
    if me.get("@type") != "user" or type(me.get("id")) is not int:
        raise GateError("own_identity_not_verified")
    chat = pump.request("searchPublicChat", username=username[1:])
    chat_type = chat.get("type", {})
    if chat.get("@type") != "chat" or chat_type.get("@type") != "chatTypePrivate":
        raise GateError("target_is_not_a_private_user")
    target_id = chat_type.get("user_id")
    if type(target_id) is not int or not 0 < target_id < 2**53 or target_id == me["id"]:
        raise GateError("target_is_invalid_or_self")
    user = pump.request("getUser", user_id=target_id)
    if user.get("id") != target_id or user.get("type", {}).get("@type") != "userTypeRegular":
        raise GateError("target_is_not_a_regular_user")
    names = user.get("usernames", {}).get("active_usernames", [])
    if username[1:].casefold() not in [name.casefold() for name in names if isinstance(name, str)]:
        raise GateError("target_username_changed")
    full = pump.request("getUserFullInfo", user_id=target_id)
    if full.get("@type") != "userFullInfo" or full.get("can_be_called") is not True:
        raise GateError("target_disallows_calls")
    return ResolvedTarget(target_id)


class QueuedMedia:
    """Marshal engine callbacks onto the same thread as TDLib updates."""
    def __init__(self, backend):
        self.backend = backend
        self.available = backend.available
        self.events = queue.Queue(maxsize=128)
        self.overflow = False

    def _enqueue(self, callback, value):
        try:
            self.events.put_nowait((callback, value))
        except queue.Full:
            self.overflow = True

    def protocol(self):
        return self.backend.protocol()

    def start(self, state, on_state, on_signal):
        self.backend.start(state, lambda v: self._enqueue(on_state, v), lambda v: self._enqueue(on_signal, v))

    def receive_signaling(self, data):
        self.backend.receive_signaling(data)

    def relay_id(self):
        return self.backend.relay_id()

    def stop(self):
        self.backend.stop()

    def drain(self):
        if self.overflow:
            raise GateError("media_event_buffer_full")
        while True:
            try:
                callback, value = self.events.get_nowait()
            except queue.Empty:
                return
            callback(value)


class LiveCallLoop:
    def __init__(self, td, media, emit=lambda status: None, clock=time.monotonic):
        self.td = td
        self.media = QueuedMedia(media)
        self.emit = emit
        self.clock = clock

    def run(self, username, *, consent=False, max_seconds=180):
        # Fail BEFORE network/account/target operations if the engine isn't usable.
        if not self.media.available:
            raise GateError("native_media_engine_not_built")
        validate_protocol(self.media.protocol())
        if not consent:
            raise GateError("explicit_call_consent_required")
        if type(max_seconds) is not int or not 1 <= max_seconds <= 180:
            raise GateError("bounded_call_duration_required")
        pump = RequestPump(self.td, self.clock)
        target = resolve_target(pump, username)
        session = CallSession(self.td.send, self.media, self.clock)
        pump.on_update = session.handle
        pump.on_tick = lambda: (self.media.drain(), session.tick())
        last_status = None

        def report():
            nonlocal last_status
            current = session.status()
            if current != last_status:
                self.emit(current)
                last_status = current

        try:
            session.start(target.user_id, authorized=True, consent=True)
            deadline = self.clock() + max_seconds
            while session.phase not in ("ended", "failed", "end_unconfirmed"):
                if self.clock() >= deadline:
                    session.end("maximum_duration")
                report()
                pump.step()
        finally:
            session.end("controller_exit")
            # Try to confirm termination, but never loop indefinitely after auth loss.
            cleanup_until = self.clock() + 5
            while session.phase not in ("ended", "failed", "end_unconfirmed", "idle") and self.clock() < cleanup_until:
                try:
                    pump.step()
                except Exception:
                    break
            if session.phase == "ending":
                session.phase = "end_unconfirmed"
            report()
        return session.status()
