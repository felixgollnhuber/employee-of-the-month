"""Single audio-only call reducer with an injected transport and media engine.

This module never logs raw Telegram updates: Ready carries an encryption key.
The caller must feed events from one authorized TDLib client in receive order.
"""
import time
import uuid


class GateError(RuntimeError):
    pass


class UnavailableMedia:
    available = False


def validate_protocol(protocol):
    if not isinstance(protocol, dict) or protocol.get("@type") != "callProtocol":
        raise GateError("media_protocol_missing")
    lo, hi = protocol.get("min_layer"), protocol.get("max_layer")
    if type(lo) is not int or type(hi) is not int or not 0 < lo <= hi:
        raise GateError("media_protocol_invalid")
    versions = protocol.get("library_versions")
    if not isinstance(versions, list) or not versions or not all(isinstance(v, str) and v for v in versions):
        raise GateError("media_versions_missing")
    if any(type(protocol.get(k)) is not bool for k in ("udp_p2p", "udp_reflector")):
        raise GateError("media_protocol_invalid")
    return {key: protocol[key] for key in (
        "@type", "min_layer", "max_layer", "library_versions", "udp_p2p", "udp_reflector")}


class CallSession:
    """Owns one call only. A new instance is required for a new call."""
    def __init__(self, send, media=None, clock=time.monotonic, start_timeout=20, end_timeout=5):
        self.send = send
        self.media = media or UnavailableMedia()
        self.clock = clock
        self.start_timeout = start_timeout
        self.end_timeout = end_timeout
        self.phase = "idle"
        self.reason = None
        self.call_id = None
        self.target = None
        self.created_at = None
        self.connected_at = None
        self.reconnecting_at = None
        self.stop_at = None
        self.stopping = False
        self.discard_sent = False
        self.media_started = False
        self.media_stopped = False
        self.media_cleanup_confirmed = None
        self.pending_updates = []
        self.pending_signaling = []
        self.create_tag = "create-" + uuid.uuid4().hex
        self.end_tag = "end-" + uuid.uuid4().hex
        self.create_resolved = False

    def status(self):
        return {"phase": self.phase, "reason": self.reason, "call_id": self.call_id,
                "media_connected": self.phase == "active", "audio_only": True,
                "media_cleanup_confirmed": self.media_cleanup_confirmed}

    def start(self, target_user_id, *, authorized=False, consent=False):
        if self.phase != "idle":
            raise GateError("session_already_used")
        if type(target_user_id) is not int or not 0 < target_user_id < 2**53:
            raise GateError("explicit_target_required")
        if not authorized:
            raise GateError("telegram_login_required")
        if not consent:
            raise GateError("explicit_call_consent_required")
        if not self.media.available:
            raise GateError("native_media_engine_not_built")
        protocol = validate_protocol(self.media.protocol())
        self.target = target_user_id
        self.phase = "dialing"
        self.created_at = self.clock()
        self.send({"@type": "createCall", "user_id": target_user_id,
                   "protocol": protocol, "is_video": False, "@extra": self.create_tag})

    def _stop_media(self):
        if self.media_started and not self.media_stopped:
            self.media_stopped = True
            try:
                self.media.stop()
                self.media_cleanup_confirmed = True
            except Exception:
                self.media_cleanup_confirmed = False
        self.pending_signaling.clear()

    def _discard(self):
        if self.call_id is None or self.discard_sent:
            return
        self.discard_sent = True
        # No claimed media connection duration until the engine reported Connected.
        duration = 0 if self.connected_at is None else max(0, int(self.clock() - self.connected_at))
        relay_id = str(self.media.relay_id()) if self.media_started else "0"
        self.send({"@type": "discardCall", "call_id": self.call_id,
                   "is_disconnected": self.reason in ("media_failed", "startup_timeout"),
                   "invite_link": "", "duration": duration, "is_video": False,
                   "connection_id": relay_id, "@extra": self.end_tag})

    def end(self, reason="requested"):
        if self.phase in ("idle", "ended", "failed") or self.stopping:
            return
        self.stopping = True
        self.reason = reason
        self.stop_at = self.clock()
        self.phase = "ending"
        self._stop_media()
        self._discard()

    def media_event(self, state):
        if state == "end_requested":
            self.end("caller_requested")
        elif state == "connected" and self.media_started and not self.stopping and self.phase in ("media_connecting", "media_reconnecting", "active"):
            self.phase = "active"
            self.connected_at = self.connected_at or self.clock()
            self.reconnecting_at = None
        elif state == "reconnecting" and self.phase == "active" and not self.stopping:
            self.phase = "media_reconnecting"
            self.reconnecting_at = self.clock()
        elif state == "failed":
            self.end("media_failed")

    def signaling_from_media(self, data):
        if self.call_id is not None and not self.stopping and self.media_started:
            self.send({"@type": "sendCallSignalingData", "call_id": self.call_id, "data": data})

    def handle(self, event):
        kind = event.get("@type")
        if event.get("@extra") == self.create_tag:
            if self.create_resolved:
                return
            if kind == "error":
                self.create_resolved = True
                self.phase = "failed"
                self.reason = "create_failed_" + str(event.get("code", "unknown"))
                self.pending_updates.clear()
                return
            if kind != "callId" or type(event.get("id")) is not int:
                return
            self.create_resolved = True
            self.call_id = event["id"]
            if self.stopping:
                self._discard()  # Covers cancellation while createCall was pending.
            updates, self.pending_updates = self.pending_updates, []
            for update in updates:
                self.handle(update)
            return
        if event.get("@extra") == self.end_tag and kind == "error":
            self.phase = "end_unconfirmed"
            self.reason = "discard_failed_" + str(event.get("code", "unknown"))
            return
        if kind == "updateNewCallSignalingData":
            if self.phase in ("idle", "ended", "failed") or self.stopping:
                return
            if self.call_id is None:
                if len(self.pending_updates) < 16:
                    self.pending_updates.append(event)
                else:
                    self.end("event_buffer_full")
            elif event.get("call_id") == self.call_id:
                if self.media_started:
                    try:
                        self.media.receive_signaling(event["data"])
                    except Exception:
                        self.end("media_failed")
                elif len(self.pending_signaling) < 32:
                    self.pending_signaling.append(event["data"])
                else:
                    self.end("event_buffer_full")
            return
        if kind != "updateCall" or self.phase == "idle":
            return
        call = event.get("call", {})
        if self.call_id is None:
            if call.get("is_outgoing") is True and call.get("user_id") == self.target:
                if len(self.pending_updates) < 16:
                    self.pending_updates.append(event)
                else:
                    self.end("event_buffer_full")
            return
        if call.get("id") != self.call_id:
            return  # Never control or expose someone else's incoming/outgoing call.
        state = call.get("state", {})
        state_type = state.get("@type")
        if state_type in ("callStateDiscarded", "callStateError"):
            self._stop_media()
            self.phase = "ended" if state_type == "callStateDiscarded" else "failed"
            self.reason = "remote_end" if state_type == "callStateDiscarded" else "telegram_call_error"
            return
        if self.stopping or self.phase in ("ended", "failed"):
            return
        if state_type == "callStatePending":
            self.phase = "ringing" if state.get("is_received") else "dialing"
        elif state_type == "callStateExchangingKeys":
            self.phase = "exchanging_keys"
        elif state_type == "callStateReady" and not self.media_started:
            self.phase = "media_connecting"
            self.media_started = True
            try:
                # Keep encryption material in memory; the engine owns format negotiation.
                self.media.start(state, self.media_event, self.signaling_from_media)
                buffered, self.pending_signaling = self.pending_signaling, []
                for data in buffered:
                    if not self.stopping:
                        self.media.receive_signaling(data)
            except Exception:
                self.end("media_failed")
        elif state_type == "callStateHangingUp":
            self.end("remote_hanging_up")

    def tick(self):
        if self.created_at is None or self.phase in ("ended", "failed"):
            return
        if not self.stopping and self.phase == "media_reconnecting" and self.clock() - self.reconnecting_at >= self.start_timeout:
            self.end("media_failed")
        elif not self.stopping and self.phase not in ("active", "media_reconnecting") and self.clock() - self.created_at >= self.start_timeout:
            self.end("startup_timeout")
        if self.stopping and self.clock() - self.stop_at >= self.end_timeout:
            self.phase = "end_unconfirmed"  # Never equate a timeout with confirmed hangup.
