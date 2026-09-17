import argparse
import getpass
import json
from pathlib import Path
import sys

from .config import (ConfigError, profile_path, read_profile, write_profile, read_routing,
                     write_routing, read_private_json, write_target, validate_live_config,
                     read_live_config, service_root)
from .control import CallSession
from .native import offline_native_check
from .keychain import keychain_configured


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
    parser = argparse.ArgumentParser(description="Telegram voice conversations for T3 Code questions")
    sub = parser.add_subparsers(dest="command", required=True)
    native = sub.add_parser("native-check", help="Load TDLib; no login, network setup or audio")
    native.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    media = sub.add_parser("media-check", help="Inspect linked native media capabilities; no media instance or devices")
    media.add_argument("--probe", type=Path, default=Path(".build/vendor/telegram-ios/bazel-bin/bridge_probe/media_probe"))
    sub.add_parser("demo", help="Offline start/status/end simulation; no real call")
    config = sub.add_parser("configure", help="Hidden local input only; sends nothing to Telegram")
    config.add_argument("--profile", default="default")
    config.add_argument("--sender-file", type=Path, help="Private 0600 JSON containing an already confirmed sender_phone; never a phone number on the command line")
    live_config = sub.add_parser("configure-live", help="Hidden local OpenAI project key input; no API request")
    live_config.add_argument("--profile", default="default")
    live_config.add_argument("--language", choices=("en", "de"), default="en")
    live_config.add_argument("--voice", choices=("cedar", "marin"), default="cedar")
    live_config.add_argument("--user-name")
    live_config.add_argument("--agent-name", default="Employee of the Month")
    live_config.add_argument("--structuring-model", default="gpt-5.6-luna")
    live_config.add_argument("--no-wait-tone", action="store_true")
    t3_config = sub.add_parser("configure-t3", help="Save a validated T3 origin and private credentials-file path; no API request")
    t3_config.add_argument("--profile", default="default")
    t3_config.add_argument("--origin", required=True)
    t3_config.add_argument("--credentials-file", required=True, type=Path)
    target = sub.add_parser("configure-target", help="Save the confirmed recipient locally; no login, lookup or call")
    target.add_argument("--profile", default="default")
    routing = sub.add_parser("configure-audio", help="Choose exact device UIDs locally; no audio or routing is started")
    routing.add_argument("--profile", default="default")
    auth = sub.add_parser("login", help="Explicit Telegram login; MAY REQUEST A LOGIN CODE, never creates accounts or calls")
    auth.add_argument("--profile", default="default")
    auth.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    remember = sub.add_parser("remember-login", help="Verify existing database and save its derived key in macOS Keychain")
    remember.add_argument("--profile", default="default")
    remember.add_argument("--passphrase-dialog", action="store_true")
    remember.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    preflight = sub.add_parser("preflight", help="Report local readiness without opening a session")
    preflight.add_argument("--profile", default="default")
    ring = sub.add_parser("ring-test", help="One real Telegram call; ends on answer or timeout, no audio")
    ring.add_argument("--profile", default="default")
    ring.add_argument("--allow-call", action="store_true")
    ring.add_argument("--seconds", type=int, default=20, choices=range(1, 31))
    ring.add_argument("--passphrase-dialog", action="store_true", help="Hidden local macOS passphrase dialog")
    ring.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    ring.add_argument("--probe", type=Path, default=Path(".build/vendor/telegram-ios/bazel-bin/bridge_probe/media_probe"))
    voice = sub.add_parser("voice-test", help="One real Telegram conversation with billed GPT-Live 1; no macOS audio devices")
    voice.add_argument("--profile", default="default")
    voice.add_argument("--allow-call", action="store_true")
    voice.add_argument("--seconds", type=int, default=120, choices=range(1, 181))
    voice.add_argument("--passphrase-dialog", action="store_true")
    voice.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    voice.add_argument("--t3-thread", help="Source T3 thread containing a pending question")
    voice.add_argument("--t3-request", help="Exact pending T3 user-input request ID")
    voice.add_argument("--context-file", type=Path, help="Optional selected task context, maximum 12000 characters")
    watch = sub.add_parser("watch-t3", help="Bounded project watcher; call once per pending T3 question")
    watch.add_argument("--profile", default="default")
    watch.add_argument("--project-id", required=True)
    watch.add_argument("--allow-calls", action="store_true")
    watch.add_argument("--max-calls", type=int, default=1)
    watch.add_argument("--watch-seconds", type=int, default=900)
    watch.add_argument("--seconds", type=int, default=120)
    watch.add_argument("--question-delay-seconds", type=int, default=180,
                       help="Wait after a question is created before calling (default: 180)")
    watch.add_argument("--passphrase-dialog", action="store_true")
    watch.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    service = sub.add_parser("serve-t3", help="Telegram voice agent for one or all T3 projects")
    service.add_argument("--profile", default="default")
    scope = service.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project-id")
    scope.add_argument("--all-projects", action="store_true")
    service.add_argument("--daemon", action="store_true", help="Run until a stop signal instead of a fixed service duration")
    service.add_argument("--allow-task-creation", action="store_true", help="Start explicitly confirmed voice tasks as new T3 threads")
    service.add_argument("--allow-messages-and-calls", action="store_true")
    service.add_argument("--service-seconds", type=int, default=3600)
    service.add_argument("--seconds", type=int, default=120)
    service.add_argument("--max-calls", type=int)
    service.add_argument("--question-delay-seconds", type=int, default=180)
    service.add_argument("--passphrase-dialog", action="store_true")
    service.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    service.add_argument("--media-runtime", type=Path, help="Separately built native runtime with incoming call audio")
    install = sub.add_parser("install-service", help="Stage a fixed private release and LaunchAgent plist; never starts it")
    install.add_argument("--profile", default="default")
    install.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    install.add_argument("--python", type=Path, default=Path(sys.executable))
    install.add_argument("--library", type=Path, default=Path(".build/tdlib/libtdjson.dylib"))
    install.add_argument("--media-runtime", type=Path,
                         default=Path(".build/vendor/telegram-ios/bazel-bin/bridge_probe/media_runtime"))
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
                  "next_step": "Configuration already exists. Run eotm login to authenticate the sender account."})
            return 0
        if not sys.stdin.isatty():
            raise ConfigError("Interactive terminal required; do not paste secrets into task chat")
        print("Local owner-only storage only. This command does not log in or request a code.")
        sender = read_private_json(args.sender_file.parent, args.sender_file.name).get("sender_phone") if args.sender_file else None
        data = {
            "api_id": int(getpass.getpass("Telegram API ID: ")),
            "api_hash": getpass.getpass("Telegram API hash: "),
            "sender_phone": sender or getpass.getpass("Dedicated sender phone number (+...): "),
        }
        write_profile(path, data)
        emit({"configured": True, "login_requested": False})
    elif args.command == "configure-target":
        if not sys.stdin.isatty():
            raise ConfigError("Interactive terminal required")
        path = profile_path(args.profile)
        read_profile(path)
        username = getpass.getpass("Public Telegram @username of your personal receiving account: ").strip()
        changed = write_target(path, username)
        emit({"target_configured": True, "unchanged": not changed,
              "login_requested": False, "calls_started": 0})
    elif args.command == "preflight":
        from .preflight import offline_preflight
        status=offline_preflight(profile_path(args.profile))
        emit(status)
        return 0 if status["ready_for_call_attempt"] else 2
    elif args.command == "configure-live":
        from .config import write_private_json
        path=profile_path(args.profile)
        if (path/"live.json").exists() or (path/"live.json").is_symlink():
            read_live_config(path)
            emit({"live_key_configured":True,"unchanged":True})
        else:
            if not sys.stdin.isatty(): raise ConfigError("Interactive terminal required")
            key=getpass.getpass("OpenAI project API key (stored locally only): ")
            data = {"api_key": key, "language": args.language, "voice": args.voice,
                    "agent_name": args.agent_name, "structuring_model": args.structuring_model,
                    "wait_tone": not args.no_wait_tone}
            if args.user_name: data["user_name"] = args.user_name
            write_private_json(path,"live.json",validate_live_config(data))
            emit({"live_key_configured":True,"api_called":False})
    elif args.command == "configure-t3":
        from .config import write_private_json
        from .t3 import validate_connection_files
        path = profile_path(args.profile)
        read_profile(path)
        if (path/"t3.json").exists() or (path/"t3.json").is_symlink():
            current = read_private_json(path, "t3.json")
            validate_connection_files(current["origin"], current["credentials_file"])
            emit({"t3_configured": True, "unchanged": True, "api_called": False})
        else:
            config = validate_connection_files(args.origin, args.credentials_file)
            write_private_json(path, "t3.json", config)
            emit({"t3_configured": True, "api_called": False})
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
        a = int(input("Input device for the legacy desktop voice output (number): "))
        b = int(input("Output device for the legacy desktop voice microphone (number): "))
        if not 0 <= a < len(choices) or not 0 <= b < len(choices) or not choices[a]["input"] or not choices[b]["output"]:
            raise ConfigError("Invalid device direction")
        write_routing(profile_path(args.profile), choices[a]["uid"], choices[b]["uid"])
        emit({"routing_configured": True, "audio_opened": False, "routes_changed": False})
    elif args.command == "voice-test":
        if not args.allow_call:
            raise ConfigError("Explicit --allow-call required")
        if not args.passphrase_dialog and not sys.stdin.isatty() and not keychain_configured(profile_path(args.profile)):
            raise ConfigError("Interactive terminal or local passphrase dialog required")
        from .application import run_authorized_live_test, instructions_for_handoff
        from .ringing import macos_passphrase
        delegate = None
        instructions = greeting = None
        if bool(args.t3_thread) != bool(args.t3_request) or (args.context_file and not args.t3_thread):
            raise ConfigError("Both T3 thread and request ID are required")
        if args.t3_thread:
            from .t3 import T3Client, T3Delegation
            from .i18n import Locale
            context = None
            if args.context_file:
                with args.context_file.open() as stream: context = stream.read(12001)
            from .structurer import structurer_for_profile
            locale = Locale.from_config(read_live_config(profile_path(args.profile)))
            delegate = T3Delegation(T3Client.from_profile(profile_path(args.profile)), args.t3_thread,
                                    args.t3_request, context=context, emit=emit,
                                    structurer=structurer_for_profile(profile_path(args.profile)), locale=locale)
            instructions = instructions_for_handoff(delegate.packet, locale=locale)
            greeting = locale.text("greeting")
        try:
            result = run_authorized_live_test(profile_path(args.profile), args.library,
                authorized=True, max_seconds=args.seconds,
                secret_input=macos_passphrase if args.passphrase_dialog else None, emit=emit,
                delegate=delegate, instructions=instructions, greeting=greeting)
        finally:
            if delegate is not None:
                from .t3 import settle_after_call
                settle_after_call(delegate.client, delegate.coordinator_id,
                                  conversation_id=args.t3_thread+':'+args.t3_request, emit=emit)
        return 0 if result["phase"] == "ended" else 2
    elif args.command == "serve-t3":
        if not args.allow_messages_and_calls:
            raise ConfigError("Explicit --allow-messages-and-calls required")
        from .service import run_service
        from .ringing import macos_passphrase
        import signal
        import threading
        stopping = threading.Event()
        def stop_service(_signum, _frame): stopping.set()
        signal.signal(signal.SIGTERM, stop_service)
        signal.signal(signal.SIGINT, stop_service)
        max_calls = args.max_calls if args.max_calls is not None else None if args.daemon else 1
        run_service(profile_path(args.profile), args.library, '*' if args.all_projects else args.project_id, authorized=True,
                    seconds=args.service_seconds, call_seconds=args.seconds, max_calls=max_calls,
                    continuous=args.daemon, allow_tasks=args.allow_task_creation, stop_requested=stopping.is_set,
                    question_delay=args.question_delay_seconds,
                    native_executable=args.media_runtime,
                    secret_input=macos_passphrase if args.passphrase_dialog else getpass.getpass, emit=emit)
    elif args.command == "watch-t3":
        if not args.allow_calls: raise ConfigError("Explicit --allow-calls required")
        if not args.passphrase_dialog and not sys.stdin.isatty() and not keychain_configured(profile_path(args.profile)):
            raise ConfigError("Interactive terminal or local passphrase dialog required")
        import importlib.util
        if importlib.util.find_spec("websockets") is None: raise ConfigError("Use .build/live-venv/bin/python")
        from .watch import watch_project
        from .ringing import macos_passphrase
        import signal
        def stop_watch(_signum,_frame):raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,stop_watch)
        watch_project(profile_path(args.profile),args.library,args.project_id,authorized=True,
            secret_input=macos_passphrase if args.passphrase_dialog else getpass.getpass,
            max_calls=args.max_calls,watch_seconds=args.watch_seconds,call_seconds=args.seconds,
            question_delay_seconds=args.question_delay_seconds,emit=emit)
    elif args.command == "ring-test":
        if not args.allow_call:
            raise ConfigError("Explicit --allow-call required")
        if not args.passphrase_dialog and not sys.stdin.isatty() and not keychain_configured(profile_path(args.profile)):
            raise ConfigError("Interactive terminal or local passphrase dialog required")
        from .ringing import macos_passphrase, run_authorized_ring_test
        result = run_authorized_ring_test(profile_path(args.profile), args.library, args.probe,
            call_authorized=True, max_seconds=args.seconds,
            secret_input=macos_passphrase if args.passphrase_dialog else None, emit=emit)
        return 0 if result["phase"] == "ended" else 2
    elif args.command == "remember-login":
        from .keychain import remember_database_key, enrollment_dialog
        if not args.passphrase_dialog and not sys.stdin.isatty() and not keychain_configured(profile_path(args.profile)):
            raise ConfigError("Interactive terminal or local passphrase dialog required")
        remember_database_key(profile_path(args.profile),args.library,
            secret_input=enrollment_dialog if args.passphrase_dialog else getpass.getpass,emit=emit)
    elif args.command == "install-service":
        from .daemon import prepare_install
        profile = profile_path(args.profile)
        read_profile(profile)
        staged, release = prepare_install(args.root, service_root(profile), profile,
                                           args.python, args.library, args.media_runtime)
        emit({"service_staged": True, "launch_agent_started": False,
              "plist": str(staged), "release": str(release)})
    elif args.command == "login":
        if not sys.stdin.isatty():
            raise ConfigError("Interactive terminal required for authorized login")
        from .auth import login
        print("Explicit Telegram login for the configured sender account. This may request a code but never places a call.")
        login(profile_path(args.profile), args.library, emit)
    return 0


def run():
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        # Deliberately omit exception messages: native/profile errors may contain PII.
        emit({"error": type(error).__name__, "details": "Check local file permissions, dependencies and command inputs; no secrets logged"})
        sys.exit(1)


if __name__ == "__main__":
    run()
