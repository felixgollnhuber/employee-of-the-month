"""Strict TDLib Ready -> native descriptor conversion. Contains private data.

Never print the returned dictionary. Relay mapping follows the pinned official
TelegramVoip/Sources/OngoingCallContext.swift (sorted reflector IDs, hex tags).
"""
import base64
import ipaddress
import json

from .control import GateError

SUPPORTED_VERSION = "12.0.0"
PCM_INPUT = "phonebridge.pcm.to-telegram"
PCM_OUTPUT = "phonebridge.pcm.from-telegram"


def normalize_pcm_ready(ready):
    # Logical endpoints implemented by our in-memory ADM, never CoreAudio UIDs.
    return {**normalize_ready(ready, PCM_INPUT, PCM_OUTPUT), "audio_mode": "pcm16"}


def decode_bytes(value, size=None):
    if not isinstance(value, str) or len(value) > 1024 * 1024:
        raise GateError("invalid_binary_payload")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise GateError("invalid_binary_payload") from None
    if size is not None and len(result) != size:
        raise GateError("invalid_binary_length")
    return result


def device_uid(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise GateError("explicit_device_uid_required")
    if value.casefold() == "default" or value.startswith("#") or any(ord(c) < 32 for c in value):
        raise GateError("default_device_forbidden")
    return value


def normalize_ready(ready, input_uid, output_uid):
    input_uid, output_uid = device_uid(input_uid), device_uid(output_uid)
    if input_uid == output_uid:
        raise GateError("separate_audio_directions_required")
    protocol = ready.get("protocol", {})
    if ready.get("@type") != "callStateReady" or protocol.get("library_versions") != [SUPPORTED_VERSION]:
        raise GateError("unsupported_negotiated_media_version")
    if type(ready.get("allow_p2p")) is not bool:
        raise GateError("missing_peer_privacy_policy")
    key = decode_bytes(ready.get("encryption_key"), 256)
    servers = ready.get("servers")
    if not isinstance(servers, list) or not 1 <= len(servers) <= 64:
        raise GateError("invalid_relay_set")
    ids = []
    for server in servers:
        try:
            value = int(server["id"])
        except (ValueError, TypeError, KeyError):
            raise GateError("invalid_relay_id") from None
        if not -(2**63) <= value < 2**63:
            raise GateError("invalid_relay_id")
        if server.get("type", {}).get("@type") == "callServerTypeTelegramReflector":
            ids.append(value)
    reflector_ids = {value: index + 1 for index, value in enumerate(sorted(set(ids)))}
    rtc_servers = []
    for server in servers:
        port = server.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise GateError("invalid_relay_port")
        addresses = [server.get("ip_address", ""), server.get("ipv6_address", "")]
        addresses = [address for address in addresses if address]
        if not addresses:
            raise GateError("missing_relay_address")
        try:
            addresses = [str(ipaddress.ip_address(address)) for address in addresses]
        except ValueError:
            raise GateError("invalid_relay_address") from None
        kind = server.get("type", {})
        if kind.get("@type") == "callServerTypeTelegramReflector":
            if type(kind.get("is_tcp")) is not bool:
                raise GateError("invalid_reflector_transport")
            entries = [{"id": reflector_ids[int(server["id"])], "login": "reflector",
                        "password": decode_bytes(kind.get("peer_tag"), 16).hex(),
                        "is_turn": True, "is_tcp": kind["is_tcp"]}]
        elif kind.get("@type") == "callServerTypeWebrtc":
            if any(type(kind.get(k)) is not bool for k in ("supports_stun", "supports_turn")):
                raise GateError("invalid_webrtc_relay")
            entries = []
            if kind["supports_stun"]:
                entries.append({"id": 0, "login": "", "password": "", "is_turn": False, "is_tcp": False})
            if kind["supports_turn"]:
                if any(not isinstance(kind.get(k), str) or len(kind[k]) > 4096 for k in ("username", "password")):
                    raise GateError("invalid_turn_credentials")
                entries.append({"id": 0, "login": kind["username"], "password": kind["password"],
                                "is_turn": True, "is_tcp": False})
        else:
            raise GateError("unsupported_relay_type")
        for address in addresses:
            for entry in entries:
                rtc_servers.append({**entry, "host": address, "port": port})
    if not rtc_servers:
        raise GateError("no_usable_relays")
    custom = ready.get("custom_parameters", "") or "{}"
    try:
        if len(custom) > 65536 or not isinstance(json.loads(custom), dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise GateError("invalid_custom_parameters") from None
    return {"version": SUPPORTED_VERSION, "key_hex": key.hex(), "outgoing": True,
            "input_uid": input_uid, "output_uid": output_uid,
            "enable_p2p": ready["allow_p2p"], "custom_parameters": custom,
            "rtc_servers": rtc_servers}
