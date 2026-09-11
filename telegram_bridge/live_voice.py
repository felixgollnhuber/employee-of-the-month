"""GPT-Live primary WebSocket, bounded per-call lifetime and in-memory PCM only."""
import asyncio
import base64
import json
import queue
import re
import threading
import time

from .control import GateError
from .runtime import NativePcmMedia

LIVE_URL = "wss://api.openai.com/v1/live/sessions"
SAMPLE_RATE = 16000
DEFAULT_VOICE = "cedar"


def wants_hangup(text):
    """Recognize short, direct end-call requests, not discussion of hanging up."""
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    text = " ".join(text.split())
    if len(text) > 160 or re.search(r"\b(nicht|später|wenn|falls|warum|wie|ob)\b", text): return False
    prefix = r"(?:(?:okay|ok|passt|danke|gut|dann|jetzt|alles klar) )*"
    request = (r"(?:(?:bitte )?leg(?:e)? (?:jetzt )?(?:bitte )?auf|"
               r"(?:du )?(?:kannst|darfst) (?:jetzt )?(?:bitte )?auflegen|"
               r"(?:kannst|könntest|würdest) du (?:jetzt )?(?:bitte )?auflegen|"
               r"(?:bitte )?beende (?:jetzt )?(?:bitte )?(?:den anruf|das gespräch)|"
               r"ich möchte (?:den anruf|das gespräch) beenden|"
               r"wir können (?:jetzt )?auflegen|auf wiederhören|tschüss(?: bis bald)?|"
               r"(?:please )?(?:hang up|end the call))")
    return re.fullmatch(prefix + request + r"(?: bitte| danke)?", text) is not None


def session_start(instructions, voice=DEFAULT_VOICE):
    if voice not in ("cedar", "marin"):
        raise GateError("unsupported_live_voice")
    return {"type": "session.start", "event_id": "phone-start", "session": {
        "model": "gpt-live-1", "instructions": instructions,
        "audio": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "output": {"voice": voice}},
        "delegation": {"type": "client"}}}


def decode_audio(event):
    try:
        value = base64.b64decode(event["delta"], validate=True)
    except (KeyError, ValueError, TypeError):
        raise GateError("invalid_live_audio") from None
    if not value or len(value) % 2 or len(value) > 512000:
        raise GateError("invalid_live_audio")
    return value


class LiveVoice:
    def __init__(self, api_key, *, instructions, audio_out, on_failure=lambda: None,
                 on_hangup=lambda: None, delegate=None, max_seconds=120, emit=lambda status: None, connector=None,
                 voice=DEFAULT_VOICE):
        if not isinstance(api_key, str) or not api_key.startswith("sk-"):
            raise GateError("openai_api_key_required")
        if type(max_seconds) is not int or not 1 <= max_seconds <= 180:
            raise GateError("bounded_live_duration_required")
        self.api_key = api_key
        self.instructions, self.audio_out = instructions, audio_out
        session_start(instructions,voice)  # Validate before opening a connection or placing a call.
        self.voice = voice
        self.on_failure, self.delegate, self.emit = on_failure, delegate, emit
        self.on_hangup = on_hangup
        self.max_seconds, self.connector = max_seconds, connector
        self.input = queue.Queue(maxsize=1000)
        self.ready, self.stopping, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.started = False
        self.close_confirmed = False
        self.failure = None
        self.transcript = []
        self.transcript_chars = 0
        self.input_revision = 0
        self.input_bytes = self.output_bytes = 0
        self.last_user_at = None
        self.user_segment = None
        self.usage = None
        self.thread = None
        self._on_state = None

    def push_audio(self, data):
        if self.stopping.is_set(): return
        if not data or len(data) % 2 or len(data) > 64000:
            raise GateError("invalid_live_pcm")
        try: self.input.put_nowait(data)
        except queue.Full: raise GateError("live_input_backpressure") from None

    def start(self):
        if self.thread is not None: raise GateError("live_session_already_used")
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.thread.start()
        if not self.ready.wait(15) or not self.started:
            self.stopping.set()
            raise GateError(self.failure or "live_start_timeout")

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except BaseException as error:
            # Never emit websocket exception text, headers, keys or transcript.
            self.failure = str(error) if isinstance(error, GateError) else "live_transport_failed"
            self.emit({"live_error": self.failure})
            self.on_failure()
        finally:
            self.stopping.set(); self.ready.set(); self.finished.set()

    async def _run(self):
        if self.connector is None:
            from websockets.asyncio.client import connect
            connector = connect
        else: connector = self.connector
        async with connector(LIVE_URL, additional_headers={"Authorization": "Bearer " + self.api_key},
                             open_timeout=10, close_timeout=3, max_size=1024*1024, compression=None) as ws:
            closed = asyncio.Event()
            delegations = asyncio.Queue(maxsize=16)
            seen_delegations = set()
            hangup_at = None
            started_at = time.monotonic()
            async def send(value): await ws.send(json.dumps(value, ensure_ascii=False))
            async def receive():
                async for raw in ws:
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "session.started":
                        self.started = True; self.ready.set()
                        self.emit({"live_started": True, "model": "gpt-live-1", "voice": self.voice, "sample_rate": SAMPLE_RATE})
                    elif kind == "session.output_audio.delta":
                        data = decode_audio(event)
                        if not self.stopping.is_set():
                            for offset in range(0, len(data), 64000):
                                await asyncio.to_thread(self.audio_out, data[offset:offset+64000])
                            self.output_bytes += len(data)
                    elif kind in ("session.input_transcript.delta", "session.output_transcript.delta"):
                        text = event.get("delta", "")
                        if not isinstance(text, str) or len(text) > 16000: raise GateError("invalid_live_transcript")
                        self.transcript_chars += len(text)
                        if self.transcript_chars > 64000: raise GateError("live_context_limit")
                        role = "user" if kind == "session.input_transcript.delta" else "assistant"
                        if role == "user":
                            self.input_revision += 1
                            received = time.monotonic()
                            if self.user_segment is None or received - (self.last_user_at or 0) > 1.5:
                                self.user_segment = {"role":"user", "text":text}
                                self.transcript.append(self.user_segment)
                            else: self.user_segment["text"] += text
                            self.last_user_at = received
                        elif self.transcript and self.transcript[-1]["role"] == role:
                            self.transcript[-1]["text"] += text
                        else: self.transcript.append({"role": role, "text": text})
                    elif kind == "session.delegation.created":
                        delegation = event.get("delegation", {})
                        identifier = delegation.get("id")
                        if delegation.get("target") != "client" or not isinstance(identifier, str):
                            raise GateError("invalid_live_delegation")
                        if identifier not in seen_delegations:
                            seen_delegations.add(identifier)
                            if len(seen_delegations) > 32: raise GateError("live_delegation_limit")
                            delegations.put_nowait(identifier)
                    elif kind == "session.closed":
                        self.usage = event.get("usage")
                        self.close_confirmed = True; closed.set()
                        self.emit({"live_closed": True, "input_pcm_bytes": self.input_bytes,
                                   "output_pcm_bytes": self.output_bytes})
                        if not self.stopping.is_set(): self.on_failure()
                        return
                    elif kind == "error":
                        code = (event.get("error") or {}).get("code", "unknown")
                        code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", code) else "unknown"
                        raise GateError("live_api_" + code)
                if not closed.is_set(): raise GateError("live_closed_without_receipt")

            async def audio_sender():
                while not self.stopping.is_set():
                    try: data = self.input.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.005); continue
                    while not self.started and not self.stopping.is_set(): await asyncio.sleep(0.01)
                    if not self.stopping.is_set():
                        await send({"type": "session.input_audio.append", "audio": base64.b64encode(data).decode()})
                        self.input_bytes += len(data)

            async def delegate_worker():
                while True:
                    identifier = await delegations.get()
                    # Transcript deltas may trail the delegation metadata slightly.
                    await asyncio.sleep(0.25)
                    revision = self.input_revision
                    transcript = [dict(x) for x in self.transcript]
                    if self.delegate:
                        if hasattr(self.delegate,"revision"):
                            reply = await asyncio.to_thread(self.delegate, transcript, revision=revision)
                        else:
                            reply = await asyncio.to_thread(self.delegate, transcript)
                    else:
                        reply = "Dies ist ein Verbindungstest. Es ist noch keine konkrete T3-Rückfrage verbunden. Führe keine Projektaktionen aus."
                    if not isinstance(reply, str) or len(reply) > 1200: raise GateError("invalid_live_backend_reply")
                    if not self.stopping.is_set():
                        # Small chunks also stay below the documented append token limit.
                        for offset in range(0, len(reply), 240):
                            await send({"type": "session.commentary.append", "delegation_id": identifier,
                                        "content": reply[offset:offset+240]})

            await send(session_start(self.instructions,self.voice))
            tasks = [asyncio.create_task(receive()), asyncio.create_task(audio_sender()), asyncio.create_task(delegate_worker())]
            reader = tasks[0]
            try:
                while not self.stopping.is_set() and time.monotonic() - started_at < self.max_seconds:
                    if (hangup_at is None and self.last_user_at is not None
                        and time.monotonic() - self.last_user_at >= 0.8
                        and wants_hangup(self.user_segment["text"])):
                        hangup_at = time.monotonic() + 1.5
                        self.emit({"caller_requested_hangup":True})
                        await send({"type":"session.instructions.append", "delegation_id":None,
                                    "content":"Felix möchte auflegen. Verabschiede dich jetzt kurz und freundlich. Die Verbindung wird beendet."})
                    if hangup_at is not None and time.monotonic() >= hangup_at:
                        self.on_hangup()
                        self.stopping.set()
                        break
                    for task in tasks:
                        if task.done():
                            await task
                            if task is reader: return
                    await asyncio.sleep(0.025)
                if not self.stopping.is_set():
                    self.emit({"live_time_limit_reached": True})
                    self.on_failure()  # Ends the associated Telegram call as well.
            finally:
                self.stopping.set()
                for task in tasks[1:]: task.cancel()
                if self.started and not closed.is_set():
                    try:
                        await send({"type": "session.close"})
                        if not reader.done(): await asyncio.wait_for(closed.wait(), 8)
                    except Exception: pass
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if self.started and not self.close_confirmed:
                    self.emit({"live_closed": False, "final_usage_confirmed": False})

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive(): raise GateError("live_stop_timeout")
        if self.started and not self.close_confirmed: raise GateError("live_stop_unconfirmed")


class LivePcmMedia:
    def __init__(self, api_key, *, instructions, authorized=False, max_seconds=120,
                 delegate=None, emit=lambda status: None, voice=DEFAULT_VOICE):
        self.available = authorized is True
        self.stopping = False
        self.native = NativePcmMedia(on_pcm=self._from_phone, allow_audio=authorized)
        self.voice = LiveVoice(api_key, instructions=instructions, audio_out=self._to_phone,
                               max_seconds=max_seconds, delegate=delegate, emit=emit, voice=voice)

    def _from_phone(self, data):
        if not self.stopping: self.voice.push_audio(data)

    def _to_phone(self, data):
        if not self.stopping: self.native.push_audio(data)

    def protocol(self): return self.native.protocol()

    def start(self, ready, on_state, on_signal):
        if not self.available: raise GateError("explicit_live_call_authorization_required")
        self.voice.on_failure = lambda: on_state("failed")
        self.voice.on_hangup = lambda: on_state("end_requested")
        try:
            self.native.start(ready, on_state, on_signal)
            self.voice.start()
        except BaseException:
            self.stop()
            raise

    def receive_signaling(self, data): self.native.receive_signaling(data)
    def relay_id(self): return self.native.relay_id()

    def stop(self):
        self.stopping = True
        try: self.voice.close()
        finally: self.native.stop()
