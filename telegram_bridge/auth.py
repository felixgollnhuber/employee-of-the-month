"""Explicit local login only. Never registers a new account or reads chats."""
import base64
import fcntl
import getpass
import hashlib
import os
from pathlib import Path
import time

from .config import private_directory, read_profile
from .native import TDJson


class AuthGate(RuntimeError):
    pass


def auth_request(state, config, database, key, secret_input=getpass.getpass):
    kind = state.get("@type")
    if kind == "authorizationStateWaitTdlibParameters":
        return {"@type": "setTdlibParameters", "use_test_dc": False,
                "database_directory": str(database), "files_directory": str(database / "files"),
                "database_encryption_key": key, "use_file_database": False,
                "use_chat_info_database": False, "use_message_database": False,
                "use_secret_chats": False, "api_id": config["api_id"], "api_hash": config["api_hash"],
                "system_language_code": "de", "device_model": "Codex Phone Bridge",
                "system_version": "macOS", "application_version": "0.1-signaling"}
    if kind == "authorizationStateWaitPhoneNumber":
        return {"@type": "setAuthenticationPhoneNumber", "phone_number": config["sender_phone"]}
    if kind == "authorizationStateWaitCode":
        return {"@type": "checkAuthenticationCode", "code": secret_input("Telegram-Anmeldecode (nur lokal): ")}
    if kind == "authorizationStateWaitPassword":
        return {"@type": "checkAuthenticationPassword", "password": secret_input("Telegram-2FA-Passwort (nur lokal): ")}
    if kind in ("authorizationStateReady", "authorizationStateClosing", "authorizationStateClosed"):
        return None
    # Registration, paid SMS/Premium, QR and email flows require an explicit separate implementation.
    raise AuthGate("Unsupported authentication step; no account created or payment requested")


def local_key(profile, secret_input=getpass.getpass):
    salt_file = profile / "key-salt"
    try:
        fd = os.open(salt_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(salt_file, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if os.fstat(stream.fileno()).st_mode & 0o077:
                raise AuthGate("Unsafe key salt permissions")
            salt = stream.read(33)
        if len(salt) != 32:
            raise AuthGate("Invalid key salt")
    else:
        salt = os.urandom(32)
        with os.fdopen(fd, "wb") as stream:
            stream.write(salt)
    password = secret_input("Lokale Datenbank-Passphrase (mindestens 16 Zeichen, nicht Telegram-Passwort): ")
    if len(password) < 16:
        raise AuthGate("Database passphrase is too short")
    key = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024**2, dklen=32)
    return base64.b64encode(key).decode()


def login(profile, library, emit):
    config = read_profile(profile)
    private_directory(profile)
    fd = os.open(profile / "session.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AuthGate("Another process owns this profile") from None
        key = local_key(profile)
        database = profile / "database"
        private_directory(database)
        td = TDJson(library)
        td.open()
        try:
            td.send({"@type": "getAuthorizationState"})
            deadline = time.monotonic() + 180
            seen = set()
            while time.monotonic() < deadline:
                event = td.receive(0.2)
                if not event:
                    continue
                if event.get("@type") == "error":
                    raise AuthGate("Authentication request failed; no raw error data logged")
                if event.get("@type") == "updateAuthorizationState":
                    state = event.get("authorization_state", {})
                elif event.get("@type", "").startswith("authorizationState"):
                    state = event
                else:
                    continue  # No processing of messages, contacts or unrelated updates.
                kind = state.get("@type")
                if kind in seen:
                    continue
                seen.add(kind)
                if kind == "authorizationStateReady":
                    emit({"telegram_authenticated": True, "calls_started": 0, "media_ready": False})
                    return
                if kind in ("authorizationStateClosing", "authorizationStateClosed"):
                    raise AuthGate("Authorization closed before ready")
                emit({"login_step": kind})
                request = auth_request(state, config, database, key)
                if request:
                    td.send(request)
            raise AuthGate("Login timeout")
        finally:
            td.close()
