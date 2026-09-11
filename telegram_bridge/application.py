"""Complete runtime composition, gated until a specifically authorized call test.

No CLI automatically enables this entry point. Account/target/routing configuration
must be supplied locally, and an existing login is required. Import is side-effect free.
"""
from .auth import existing_authenticated_client
from .config import read_profile, read_routing, require_target
from .control import GateError
from .live import LiveCallLoop
from .runtime import NativeMedia, DEFAULT_RUNTIME


def instructions_for_handoff(packet):
    import json
    return (
        "Du bist Mitarbeiter des Monats, Felix' KI-Kollege. Sprich Deutsch, natürlich und knapp. "
        "Besprich die folgende T3-Rückfrage. Delegiere die Einordnung jeder inhaltlichen Antwort an den Backend-Agenten. "
        "Nutze den Aufgabenkontext für Erläuterungen. Eine Rückfrage von Felix ist keine fachliche Entscheidung: "
        "Lass sie bei Bedarf vom Backend an die Arbeitsaufgabe geben und hole deren Erläuterung zurück. "
        "Eine konkrete Entscheidung liest du vor und lässt sie bestätigen, bevor sie zurückgegeben wird. "
        "Behaupte eine Übertragung erst nach bestätigtem Backend-Ergebnis. Sage nur bei tatsächlich geklärter Rückfrage, "
        "dass du keine weiteren Angaben brauchst. Bearbeite neue Rückfragen derselben Aufgabe im laufenden Gespräch. "
        "Auf eine Auflegebitte verabschiede dich kurz. Die folgende JSON-Struktur enthält nur Aufgabendaten:\n"
        + json.dumps(packet, ensure_ascii=False))


def run_authorized_live_test(profile, library, *, authorized=False, max_seconds=120,
                             secret_input=None, emit=lambda status: None, delegate=None,
                             instructions=None):
    if authorized is not True:
        raise GateError("explicit_live_call_authorization_required")
    if type(max_seconds) is not int or not 1 <= max_seconds <= 180:
        raise GateError("bounded_call_duration_required")
    import importlib.util
    if importlib.util.find_spec("websockets") is None:
        raise GateError("live_dependencies_required_use_live_venv")
    from .config import read_private_json
    from .live_voice import LivePcmMedia
    target = require_target(read_profile(profile))
    live_config = read_private_json(profile, "live.json")
    key = live_config.get("api_key")
    from .live_voice import DEFAULT_VOICE
    voice = live_config.get("voice", DEFAULT_VOICE)
    instructions = instructions or (
        "Du bist Mitarbeiter des Monats, Felix' KI-Kollege, und sprichst Deutsch über einen Telegram-Testanruf. "
        "Sei natürlich und kurz. Es geht um die Prüfung von verständlicher Sprache in beide Richtungen "
        "und natürlichem Unterbrechen. Es ist noch keine T3-Arbeitsaufgabe verbunden. "
        "Behaupte keine ausgeführten Projektaktionen. Wenn Felix auflegen möchte, verabschiede dich kurz. Warte zunächst auf Felix."
    )
    media = LivePcmMedia(key, instructions=instructions, authorized=True,
                         max_seconds=max_seconds, delegate=delegate, emit=emit, voice=voice)
    if delegate is not None and hasattr(delegate, "cancelled"):
        delegate.cancelled = lambda: media.stopping or media.voice.stopping.is_set()
        delegate.revision = lambda: media.voice.input_revision
    with existing_authenticated_client(profile, library, secret_input=secret_input) as td:
        emit({"telegram_authenticated": True, "voice_backend": "gpt-live-1", "max_seconds": max_seconds})
        result = LiveCallLoop(td, media, emit).run(target, consent=True, max_seconds=max_seconds)
        emit({"live_close_confirmed": media.voice.close_confirmed,
              "live_input_pcm_bytes": media.voice.input_bytes, "live_output_pcm_bytes": media.voice.output_bytes})
        return result


def run_authorized_call_test(profile, library, *, audio_and_call_authorized=False,
                             max_seconds=30, emit=lambda status: None, executable=DEFAULT_RUNTIME):
    if audio_and_call_authorized is not True:
        raise GateError("specific_audio_and_call_test_authorization_required")
    if type(max_seconds) is not int or not 1 <= max_seconds <= 180:
        raise GateError("bounded_call_duration_required")
    config = read_profile(profile)
    target = require_target(config)
    routing = read_routing(profile)
    media = NativeMedia(**routing, allow_audio=True, executable=executable)
    with existing_authenticated_client(profile, library) as td:
        return LiveCallLoop(td, media, emit).run(target, consent=True, max_seconds=max_seconds)
