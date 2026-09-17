"""Local, owner-only configuration; never a repository file or CLI argument."""
import json
import os
from pathlib import Path
import re
import stat


class ConfigError(RuntimeError):
    pass


APP_SUPPORT = Path.home() / "Library/Application Support"
PROFILE_ROOT = APP_SUPPORT / "EmployeeOfTheMonth/telegram"
LEGACY_PROFILE_ROOT = APP_SUPPORT / "CodexPhoneBridge/telegram"


def profile_path(name="default"):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", name):
        raise ConfigError("Invalid profile name")
    current = PROFILE_ROOT / name
    legacy = LEGACY_PROFILE_ROOT / name
    return legacy if legacy.exists() and not current.exists() else current


def service_root(profile):
    """Keep existing installations under their legacy app root; use the new root otherwise."""
    profile = Path(profile).resolve()
    return profile.parents[1] / "service"


def validate(data):
    if type(data.get("api_id")) is not int or not 0 < data["api_id"] < 2**31:
        raise ConfigError("API ID must be a positive integer")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", data.get("api_hash", "")):
        raise ConfigError("API hash must contain 32 hexadecimal characters")
    if not re.fullmatch(r"\+[1-9][0-9]{6,14}", data.get("sender_phone", "")):
        raise ConfigError("Sender requires an explicitly chosen E.164 phone number")
    target = data.get("target_username")
    if target is not None and not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", target):
        raise ConfigError("Target requires an explicitly chosen Telegram @username")
    result = {key: data[key] for key in ("api_id", "api_hash", "sender_phone")}
    if target is not None:
        result["target_username"] = target
    return result


def require_target(data):
    target = data.get("target_username")
    if not isinstance(target, str) or not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", target):
        raise ConfigError("Confirmed call target is required before dialing")
    return target


def private_directory(path):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ConfigError("Profile directory must be owner-only (0700), owned by you and not a symlink")


def write_profile(path, data):
    data = validate(data)
    write_private_json(path, "config.json", data)


def write_private_json(path, name, data):
    private_directory(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(path / name, flags, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_profile(path):
    data = validate(read_private_json(path, "config.json"))
    target_file = path / "target.json"
    if target_file.exists() or target_file.is_symlink():
        target = require_target(read_private_json(path, "target.json"))
        if data.get("target_username") not in (None, target):
            raise ConfigError("Conflicting call targets in local profile")
        data["target_username"] = target
    return data


def write_target(path, username):
    """Add a confirmed recipient without rewriting API credentials or session data."""
    target = require_target({"target_username": username})
    current = read_profile(path)
    if current.get("target_username") == target:
        return False
    if current.get("target_username") is not None:
        raise ConfigError("A different call target is already configured")
    write_private_json(path, "target.json", {"target_username": target})
    return True


def read_private_json(path, name, *, max_bytes=8192):
    if not path.exists():
        raise ConfigError("No local profile configured")
    private_directory(path)
    fd = os.open(path / name, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ConfigError("Configuration must be an owner-only regular file (0600)")
        if info.st_size > max_bytes:
            raise ConfigError("Configuration is too large")
        return json.load(stream)


def write_routing(path, input_uid, output_uid):
    from .descriptor import device_uid
    a, b = device_uid(input_uid), device_uid(output_uid)
    if a == b:
        raise ConfigError("Separate input and output UIDs required")
    write_private_json(path, "routing.json", {"input_uid": a, "output_uid": b})


def read_routing(path):
    from .descriptor import device_uid
    data = read_private_json(path, "routing.json")
    a, b = device_uid(data.get("input_uid")), device_uid(data.get("output_uid"))
    if a == b:
        raise ConfigError("Separate input and output UIDs required")
    return {"input_uid": a, "output_uid": b}


def validate_live_config(data, *, legacy=False):
    if not isinstance(data, dict):
        raise ConfigError("Invalid live configuration")
    key = data.get("api_key")
    if not isinstance(key, str) or not key.startswith("sk-"):
        raise ConfigError("Invalid OpenAI key format")
    language = data.get("language", "de" if legacy else "en")
    if language not in ("en", "de"):
        raise ConfigError("Language must be en or de")
    voice = data.get("voice", "cedar")
    if voice not in ("cedar", "marin"):
        raise ConfigError("Unsupported GPT-Live voice")
    result = {"api_key": key, "language": language, "voice": voice}
    defaults = {"user_name": "Felix" if language == "de" else "the user",
                "agent_name": "Employee of the Month"}
    for field in ("user_name", "agent_name"):
        value = data.get(field, defaults[field])
        if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 80
                or any(ord(character) < 32 for character in value)):
            raise ConfigError("Invalid conversation name")
        result[field] = value.strip()
    model = data.get("structuring_model", "gpt-5.6-luna")
    if model is not None and (not isinstance(model, str)
                              or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", model)):
        raise ConfigError("Invalid structuring model")
    result["structuring_model"] = model
    wait_tone = data.get("wait_tone", True)
    if type(wait_tone) is not bool:
        raise ConfigError("wait_tone must be true or false")
    result["wait_tone"] = wait_tone
    return result


def read_live_config(profile):
    data = read_private_json(profile, "live.json")
    return validate_live_config(data, legacy="language" not in data)
