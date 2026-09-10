import argparse
import getpass
import json
from pathlib import Path
import sys

from .config import ConfigError, profile_path, read_profile, write_profile, read_routing, write_routing, read_private_json
from .control import CallSession
from .native import offline_native_check


def emit(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def demo():
    # A deliberately labeled fixture engine. It never opens media or the network.
    class FixtureMedia:
        available = True

        def protocol(self):
            return {"@type": "callProtocol", "min_layer": 1, "max_layer": 1,
                    "library_versions": ["offline-fixture-not-for-network"],
                    "udp_p2p": False, "udp_reflector": True}

        def start(self, state, callback, signaling):
            self.callback = callback

        def stop(self):
            pass

        def relay_id(self):
            return 0

    outgoing = []
    media = FixtureMedia()
    session = CallSession(outgoing.append, media)

    def status():
        emit({"mode": "offline-simulation", **session.status()})

    status()
    session.start(100001, authorized=True, consent=True)
    status()
    session.handle({"@type": "callId", "id": 1, "@extra": session.create_tag})
    session.handle({"@type": "updateCall", "call": {"id": 1, "state": {
        "@type": "callStatePending", "is_received": True}}})
    status()
    session.handle({"@type": "updateCall", "call": {"id": 1, "state": {"@type": "callStateReady"}}})
    status()
    media.callback("connected")
    status()
    session.end()
    status()
    session.handle({"@type": "updateCall", "call": {"id": 1, "state": {"@type": "callStateDiscarded"}}})
    status()


def main():
    parser = argparse.ArgumentParser(description="Original-Voice Telegram bridge: offline control implementation")
    sub = parser.add_subparsers(dest="command", required=True)
    native = sub.add_parser("native-check", help="Load TDLib; no login, network setup or audio")
    native.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    media = sub.add_parser("media-check", help="Inspect linked native media capabilities; no media instance or devices")
    media.add_argument("--probe", type=Path, default=Path(".build/vendor/telegram-ios/bazel-bin/bridge_probe/media_probe"))
    sub.add_parser("demo", help="Offline start/status/end simulation; no real call")
    config = sub.add_parser("configure", help="Hidden local input only; sends nothing to Telegram")
    config.add_argument("--profile", default="default")
    config.add_argument("--sender-file", type=Path, help="Private 0600 JSON containing an already confirmed sender_phone; never a phone number on the command line")
    routing = sub.add_parser("configure-audio", help="Choose exact device UIDs locally; no audio or routing is started")
    routing.add_argument("--profile", default="default")
    auth = sub.add_parser("login", help="Explicit Telegram login; MAY REQUEST A LOGIN CODE, never creates accounts or calls")
    auth.add_argument("--profile", default="default")
    auth.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    preflight = sub.add_parser("preflight", help="Report local readiness without opening a session")
    preflight.add_argument("--profile", default="default")
    args = parser.parse_args()
    if args.command == "native-check":
        emit(offline_native_check(args.library))
    elif args.command == "media-check":
        from .media import inspect_media
        emit(inspect_media(args.probe))
    elif args.command == "demo":
        demo()
    elif args.command == "configure":
        path = profile_path(args.profile)
        if (path / "config.json").exists() or (path / "config.json").is_symlink():
            read_profile(path)  # Validate ownership/permissions and fields without printing them.
            emit({"configured": True, "unchanged": True, "login_requested": False,
                  "next_step": "API-Konfiguration bereits vorhanden. Für die Anmeldung nur python3 -m telegram_bridge login ausführen."})
            return 0
        if not sys.stdin.isatty():
            raise ConfigError("Interactive terminal required; do not paste secrets into task chat")
        print("Nur lokale Speicherung (0600), keine Anmeldung oder SMS-Anforderung.")
        sender = read_private_json(args.sender_file.parent, args.sender_file.name).get("sender_phone") if args.sender_file else None
        data = {
            "api_id": int(getpass.getpass("Eigene Telegram API-ID: ")),
            "api_hash": getpass.getpass("Eigener API-Hash: "),
            "sender_phone": sender or getpass.getpass("Bewusst gewählte Absendernummer (+...): "),
        }
        write_profile(path, data)
        emit({"configured": True, "login_requested": False})
    elif args.command == "preflight":
        try:
            profile = read_profile(profile_path(args.profile))
            configured = True
            target_present = bool(profile.get("target_username"))
        except (OSError, ConfigError, ValueError):
            configured = False
            target_present = False
        try:
            read_routing(profile_path(args.profile))
            routing_present = True
        except Exception:
            routing_present = False
        emit({"configuration_present_and_private": configured,
              "target_configured": target_present,
              "routing_present_and_private": routing_present,
              "telegram_authenticated": "not_checked_offline", "live_audio_verified": False,
              "original_voice_autostart_verified": False,
              "ready_for_live_call": False,
              "blockers": (["local_account_configuration"] if not configured else []) +
                  (["exact_audio_device_configuration"] if not routing_present else []) + [
                  "authenticated_session_not_checked", "authorized_live_audio_test_required",
                  "original_voice_start_not_verified"] + ([] if target_present else ["confirmed_target_required_before_call"])})
        return 2
    elif args.command == "configure-audio":
        if not sys.stdin.isatty():
            raise ConfigError("Interactive terminal required")
        from .runtime import NativeIPC
        ipc = NativeIPC()
        try:
            choices = ipc.request("devices")["devices"]
        finally:
            ipc.close()
        for index, device in enumerate(choices):
            print(index, device["name"], "input=" + str(device["input"]), "output=" + str(device["output"]))
        a = int(input("Eingangsgerät für Original-Codex-Ausgabe (Nummer): "))
        b = int(input("Ausgangsgerät zum Codex-Mikrofon (Nummer): "))
        if not 0 <= a < len(choices) or not 0 <= b < len(choices) or not choices[a]["input"] or not choices[b]["output"]:
            raise ConfigError("Invalid device direction")
        write_routing(profile_path(args.profile), choices[a]["uid"], choices[b]["uid"])
        emit({"routing_configured": True, "audio_opened": False, "routes_changed": False})
    elif args.command == "login":
        if not sys.stdin.isatty():
            raise ConfigError("Interactive terminal required for authorized login")
        from .auth import login
        print("Explizite Telegram-Anmeldung für das konfigurierte Absenderkonto; kann einen Code anfordern. Keine Anrufe.")
        login(profile_path(args.profile), args.library, emit)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        # Deliberately omit exception messages: native/profile errors may contain PII.
        emit({"error": type(error).__name__, "details": "Check local file permissions, dependencies and command inputs; no secrets logged"})
        sys.exit(1)
