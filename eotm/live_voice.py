"""GPT-Live primary WebSocket, bounded per-call lifetime and in-memory PCM only."""
import array
import asyncio
import base64
import json
import math
import queue
import re
import sys
import threading
import time

from .control import GateError
from .runtime import NativePcmMedia
from .i18n import Locale

LIVE_URL = "wss://api.openai.com/v1/live/sessions"
SAMPLE_RATE = 16000
DEFAULT_VOICE = "cedar"


def wants_hangup(text):
    """Recognize short, direct end-call requests, not discussion of hanging up."""
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    text = " ".join(text.split())
    if len(text) > 160 or re.search(r"\b(nicht|später|wenn|falls|warum|wie|ob|not|do not|don t|later|if|why|how|whether)\b", text): return False
    prefix = r"(?:(?:okay|ok|yes|all right|thanks|passt|danke|gut|dann|jetzt|alles klar) )*"
    request = (r"(?:(?:bitte )?leg(?:e)? (?:jetzt )?(?:bitte )?auf|"
               r"(?:du )?(?:kannst|darfst) (?:jetzt )?(?:bitte )?auflegen|"
               r"(?:kannst|könntest|würdest) du (?:jetzt )?(?:bitte )?auflegen|"
               r"(?:bitte )?beende (?:jetzt )?(?:bitte )?(?:den anruf|das gespräch)|"
               r"ich möchte (?:den anruf|das gespräch) beenden|"
               r"wir können (?:jetzt )?auflegen|auf wiederhören|tschüss(?: bis bald)?|"
               r"(?:please )?hang up(?: now)?|(?:you can|we can) hang up(?: now)?|"
               r"(?:please )?end (?:the )?(?:call|conversation)(?: now)?|"
               r"i want to end (?:the )?(?:call|conversation)|goodbye|bye(?: for now)?|"
               r"talk to you later|that(?: is| s) all(?: for today)?)")
    return re.fullmatch(prefix + request + r"(?: bitte| danke| please| thanks)?", text) is not None


def _words(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def mentions_hangup(text):
    """Loose hint for natural speech with fillers. It never ends a call alone, only together
    with the model's own farewell right after it."""
    text = _words(text)
    if len(text) > 200 or re.search(r"\b(nicht|später|wenn|falls|warum|wie|ob|bevor|vorher|nachdem|erst|gleich|aber|"
                                    r"not|do not|don t|later|if|why|how|whether|before|after|first|but|"
                                    r"schreib\w*|text|nachricht|message|thread|sende|schick\w*|send|write)\b", text):
        return False
    return re.search(r"\b(?:aufleg(?:en|e|st)|auf wiederhören|tschüss|tschau|ciao|baba|pfiat|das w[aä]rs|das w[aä]r s|"
                     r"schönen (?:tag|abend)|schönes wochenende|bis bald|hang up|goodbye|bye|talk to you later|"
                     r"have a (?:nice|good) (?:day|evening|weekend)|that(?: is| s) all)\b|"
                     r"\blege?(?: \w+){0,2} auf(?: bitte| danke)*$", text) is not None


def is_farewell(text):
    """The model's closing words: at the end of what it said, never a question or a mirrored greeting."""
    if text.rstrip().endswith("?"): return False
    closing = (r"(?:tschüss|tschau|ciao|baba|pfiat di|mach s gut|machs gut|(?:auf )?wiederhören|ich lege(?: \w+)? auf|"
               r"beende (?:das gespräch|den anruf)(?: jetzt)?|goodbye|bye|talk to you later|i(?: am| m) hanging up)(?: \w+){0,3}")
    wishes = r"(?:bis bald|bis dann|bis später|bis zum nächsten mal|schönen (?:tag|abend)|schönes wochenende|gute nacht|see you|talk soon|have a (?:nice|good) (?:day|evening|weekend)|good night)(?: felix| now| still| noch| dir| euch)?"
    return re.search(r"\b(?:" + closing + "|" + wishes + r")$", _words(text)) is not None


def retracts(text):
    """Caller speech after a hang-up request. Courtesies, farewells and short noises do not take it back."""
    if wants_hangup(text) or mentions_hangup(text) or is_farewell(text): return False
    text = _words(text)
    return len(text.split()) >= 4 or re.search(r"\b(halt|stopp?|warte|moment|doch nicht|nicht auflegen|wait|hold on|never mind|do not hang up|don t hang up)\b", text) is not None


# Hang-up pacing. The goodbye must be generated, paced to the phone and cross the Telegram
# jitter buffer before the call ends; a fixed 1.5 s cut it off in real calls.
HANGUP_SILENCE = 0.8         # the caller has finished the sentence
GOODBYE_PROMPT_AFTER = 2.0   # the model has not begun a goodbye on its own
GOODBYE_START_TIMEOUT = 3.0  # after prompting it
GOODBYE_LIMIT = 10.0         # hard cap after the request
LOOSE_LIMIT = 6.0            # a loose mention without farewell is dropped
AUDIO_IDLE = 0.5             # model audio fully handed to the phone path
GOODBYE_TAIL = 0.6           # delivery to the handset


def _wait_tone():
    """Two soft rising notes, about 230 ms, quiet and faded: audible proof that the backend is
    working, without clicks and without masking speech."""
    samples = array.array("h")
    for frequency, seconds in ((587.33, .09), (0, .03), (783.99, .11)):
        count, fade = int(SAMPLE_RATE*seconds), int(SAMPLE_RATE*.015)
        for n in range(count):
            envelope = .5 - .5*math.cos(math.pi*min(1, n/fade, (count-1-n)/fade))
            samples.append(int(2000*envelope*math.sin(2*math.pi*frequency*n/SAMPLE_RATE)) if frequency else 0)
    if sys.byteorder == "big": samples.byteswap()
    return samples.tobytes()


WAIT_TONE = _wait_tone()
WAIT_TONE_QUIET = 0.7     # never over or right after the model's own speech
WAIT_TONE_INTERVAL = 1.6


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
                 voice=DEFAULT_VOICE, on_transcript=lambda transcript: None, greeting=None, wait_tone=False,
                 locale=None):
        if not isinstance(api_key, str) or not api_key.startswith("sk-"):
            raise GateError("openai_api_key_required")
        if type(max_seconds) is not int or not 1 <= max_seconds <= 1200:
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
        self.on_transcript = on_transcript
        self.transcript_chars = 0
        self.input_revision = 0
        self.input_bytes = self.output_bytes = 0
        self.last_user_at = None
        self.user_segment = None
        self.usage = None
        self.thread = None
        self._on_state = None
        self.instructions_lock = threading.Lock()
        self.instructions_sent = False
        # The model only speaks first when an appended instruction asks for it after session.started.
        self.greeting = greeting
        self.audio_received_at = self.last_played_at = self.model_played_at = 0.0
        self.playing = False
        self.wait_tone = wait_tone
        self.locale = locale or Locale()
        self.waiting_since = None  # A delegation is being answered by the backend.
        self.wait_speech_reported = False

    def extend_instructions(self, text):
        """Add prefetched context while the phone still rings. Refused once session.start is out:
        a later instructions.append may interrupt the model's current speech. Never raises:
        optional context must not end a call or the service loop."""
        with self.instructions_lock:
            if self.instructions_sent or self.stopping.is_set(): return False
            if not isinstance(text, str) or len(self.instructions) + len(text) > 48000: return False
            self.instructions += text
            return True

    def push_audio(self, data):
        if self.stopping.is_set(): return
        if not data or len(data) % 2 or len(data) > 64000:
            raise GateError("invalid_live_pcm")
        try: self.input.put_nowait(data)
        except queue.Full: raise GateError("live_input_backpressure") from None

    def start(self, *, wait=True):
        if self.thread is not None: raise GateError("live_session_already_used")
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.thread.start()
        if not wait: return
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
            output_audio = asyncio.Queue(maxsize=160)
            seen_delegations = set()
            hangup = judged = None
            greeted = False
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
                            self.audio_received_at = time.monotonic()
                            for offset in range(0, len(data), 6400):
                                try: output_audio.put_nowait((data[offset:offset+6400], True))
                                except asyncio.QueueFull: raise GateError('live_output_backpressure') from None
                    elif kind in ("session.input_transcript.delta", "session.output_transcript.delta"):
                        text = event.get("delta", "")
                        if not isinstance(text, str) or len(text) > 16000: raise GateError("invalid_live_transcript")
                        self.transcript_chars += len(text)
                        if self.transcript_chars > 64000: raise GateError("live_context_limit")
                        role = "user" if kind == "session.input_transcript.delta" else "assistant"
                        if role == "user":
                            if self.waiting_since is not None and not self.wait_speech_reported:
                                # If the wait tone echoed into the recognizer, pending answers would be discarded.
                                self.wait_speech_reported = True
                                self.emit({"caller_speech_during_wait": True})
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
                        self.on_transcript([dict(message) for message in self.transcript])
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

            async def audio_player():
                while not self.stopping.is_set():
                    data, counted = await output_audio.get()
                    self.playing = True
                    try: await asyncio.to_thread(self.audio_out, data)
                    finally:
                        self.playing = False
                        self.last_played_at = time.monotonic()
                    if counted:  # The wait tone is neither API output nor the model speaking.
                        self.output_bytes += len(data)
                        self.model_played_at = self.last_played_at

            async def wait_tone():
                last = 0.0
                while not self.stopping.is_set():
                    await asyncio.sleep(0.05)
                    now = time.monotonic()
                    quiet = (output_audio.empty() and not self.playing
                             and now - max(self.audio_received_at, self.last_played_at, self.last_user_at or 0) >= WAIT_TONE_QUIET)
                    if (self.waiting_since is not None and hangup is None and quiet
                            and now - self.waiting_since >= 0.4 and now - last >= WAIT_TONE_INTERVAL):
                        last = now
                        output_audio.put_nowait((WAIT_TONE, False))

            async def delegate_worker():
                while True:
                    identifier = await delegations.get()
                    self.waiting_since, self.wait_speech_reported = time.monotonic(), False
                    # Transcript deltas may trail the delegation metadata slightly.
                    await asyncio.sleep(0.25)
                    revision = self.input_revision
                    transcript = [dict(x) for x in self.transcript]
                    asked_at = time.monotonic()
                    if self.delegate:
                        if hasattr(self.delegate,"revision"):
                            reply = await asyncio.to_thread(self.delegate, transcript, revision=revision)
                        else:
                            reply = await asyncio.to_thread(self.delegate, transcript)
                    else:
                        reply = self.locale.text("connection_test")
                    if not isinstance(reply, str) or len(reply) > 1200: raise GateError("invalid_live_backend_reply")
                    # The silence Felix hears per delegation; a duration only, never any text.
                    self.emit({"backend_reply_seconds": round(time.monotonic()-asked_at, 2)})
                    self.waiting_since = None  # The answer is imminent; no tone over it.
                    if not self.stopping.is_set():
                        # Small chunks also stay below the documented append token limit.
                        for offset in range(0, len(reply), 240):
                            await send({"type": "session.commentary.append", "delegation_id": identifier,
                                        "content": reply[offset:offset+240]})

            with self.instructions_lock:
                self.instructions_sent = True
                start = session_start(self.instructions,self.voice)
            await send(start)
            tasks = [asyncio.create_task(receive()), asyncio.create_task(audio_sender()),
                     asyncio.create_task(delegate_worker()), asyncio.create_task(audio_player())]
            if self.wait_tone: tasks.append(asyncio.create_task(wait_tone()))
            reader = tasks[0]
            try:
                while not self.stopping.is_set() and time.monotonic() - started_at < self.max_seconds:
                    if not self.started and time.monotonic() - started_at >= 15:
                        raise GateError('live_start_timeout')
                    now = time.monotonic()
                    if self.started and self.greeting and not greeted:
                        greeted = True
                        await send({"type":"session.instructions.append", "delegation_id":None, "content":self.greeting})
                    segment = self.user_segment
                    if (hangup is None and segment is not None and self.last_user_at is not None
                            and now - self.last_user_at >= HANGUP_SILENCE and judged != (id(segment), segment["text"])):
                        judged = (id(segment), segment["text"])
                        mode = "strict" if wants_hangup(segment["text"]) else "loose" if mentions_hangup(segment["text"]) else None
                        if mode == "loose" and sum(m["role"] == "user" for m in self.transcript) < 2:
                            mode = None  # An opening "Ciao" answered with "Ciao!" is a greeting.
                        if mode:
                            hangup = {"mode":mode, "at":now, "speech_end":self.last_user_at, "segment":segment,
                                      "text":segment["text"], "prompted_at":None, "end_at":None, "armed_at":None}
                            if mode == "strict": self.emit({"caller_requested_hangup":True})
                    if (hangup is not None and hangup["end_at"] is not None and self.audio_received_at > hangup["armed_at"]
                            and now - hangup["at"] < GOODBYE_LIMIT):
                        hangup["end_at"] = None  # A late goodbye has just begun; let it finish.
                    if hangup is not None and hangup["end_at"] is None:
                        index = next((i for i, m in enumerate(self.transcript) if m is hangup["segment"]), None)
                        later = self.transcript[index+1:] if index is not None else []
                        reply = " ".join(m["text"] for m in later if m["role"] == "assistant")
                        newer = " ".join([hangup["segment"]["text"][len(hangup["text"]):]]
                                         + [m["text"] for m in later if m["role"] == "user"]).strip()
                        # Transcripts may trail the model's audio burst; real-time playback still overlaps them.
                        began = max(self.audio_received_at, self.model_played_at) > hangup["speech_end"]
                        played = (began and output_audio.empty() and not self.playing
                                  and now - max(self.audio_received_at, self.last_played_at) >= AUDIO_IDLE)
                        elapsed = now - hangup["at"]
                        end = False
                        if newer and retracts(newer):
                            self.emit({"hangup_withdrawn":True})
                            hangup = None
                        elif hangup["mode"] == "loose":
                            # Natural wording with fillers: only the model's own farewell confirms the intent.
                            if is_farewell(reply):
                                end = played or elapsed >= GOODBYE_LIMIT
                                if end: self.emit({"hangup_after_farewell":True})
                            elif played or elapsed >= LOOSE_LIMIT: hangup = None
                        else:
                            if not began and hangup["prompted_at"] is None and elapsed >= GOODBYE_PROMPT_AFTER:
                                hangup["prompted_at"] = now
                                await send({"type":"session.instructions.append", "delegation_id":None,
                                            "content":self.locale.text("hangup_instruction")})
                            unanswered = (not began and hangup["prompted_at"] is not None
                                          and now - hangup["prompted_at"] >= GOODBYE_START_TIMEOUT)
                            end = played or unanswered or elapsed >= GOODBYE_LIMIT
                        if end: hangup["end_at"], hangup["armed_at"] = now + GOODBYE_TAIL, now
                    if hangup is not None and hangup["end_at"] is not None and now >= hangup["end_at"]:
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


class PcmOutputPacer:
    """At most 100 ms of PCM ahead of real time, regardless of API burst size."""
    def __init__(self, send, wait, clock=time.monotonic):
        self.send, self.wait, self.clock = send, wait, clock
        self.next_at = 0

    def push(self, data):
        for offset in range(0, len(data), 3200):
            if self.wait(max(0, self.next_at-self.clock())): return
            chunk = data[offset:offset+3200]
            self.send(chunk)
            self.next_at = max(self.next_at, self.clock()) + len(chunk)/(SAMPLE_RATE*2)


class LivePcmMedia:
    def __init__(self, api_key, *, instructions, authorized=False, max_seconds=120,
                 delegate=None, emit=lambda status: None, voice=DEFAULT_VOICE, native_executable=None,
                 on_transcript=lambda transcript: None, greeting=None, wait_tone=False, locale=None):
        self.available = authorized is True
        self.stopping = False
        self.connected = threading.Event()
        self.stop_event = threading.Event()
        self.lifecycle_lock = threading.Lock()
        self.voice_start_requested = False
        self.emit = emit
        native_options = {} if native_executable is None else {'executable': native_executable}
        self.native = NativePcmMedia(on_pcm=self._from_phone, allow_audio=authorized, **native_options)
        self.voice = LiveVoice(api_key, instructions=instructions, audio_out=self._to_phone,
                               max_seconds=max_seconds, delegate=delegate, emit=emit, voice=voice, on_transcript=on_transcript,
                               greeting=greeting, wait_tone=wait_tone, locale=locale)
        self.output_pacer = PcmOutputPacer(self._send_pcm, self.stop_event.wait)

    def _from_phone(self, data):
        if not self.stopping and self.connected.is_set() and self.voice.started:
            self.voice.push_audio(data)

    def _to_phone(self, data):
        if not self.stopping: self.output_pacer.push(data)

    def _send_pcm(self, data):
        while not self.connected.wait(.1):
            if self.stopping: return
        if not self.stopping: self.native.push_audio(data)

    def protocol(self): return self.native.protocol()

    def extend_instructions(self, text): return self.voice.extend_instructions(text)

    def start(self, ready, on_state, on_signal):
        if not self.available: raise GateError("explicit_live_call_authorization_required")
        self.voice.on_failure = lambda: on_state("failed")
        self.voice.on_hangup = lambda: on_state("end_requested")
        def native_state(state):
            self.emit({'media_transport_state': state})
            with self.lifecycle_lock:
                if self.stopping: return
                if state == 'connected':
                    self.connected.set()
                    if not self.voice_start_requested:
                        self.voice_start_requested = True
                        self.voice.start(wait=False)
                elif state in ('reconnecting', 'failed'):
                    self.connected.clear()
            on_state(state)
        try:
            self.native.start(ready, native_state, on_signal)
        except BaseException:
            self.stop()
            raise

    def receive_signaling(self, data): self.native.receive_signaling(data)
    def relay_id(self): return self.native.relay_id()

    def stop(self):
        with self.lifecycle_lock:
            self.stopping = True
            self.stop_event.set()
        try: self.voice.close()
        finally: self.native.stop()
