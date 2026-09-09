"""Small binding to the official TDLib JSON C interface, not a userbot kit."""
import ctypes
import json
import pathlib
import time


class NativeError(RuntimeError):
    pass


class TDJson:
    def __init__(self, path):
        path = pathlib.Path(path).resolve(strict=True)
        self.lib = ctypes.CDLL(str(path))
        self.lib.td_execute.argtypes = [ctypes.c_char_p]
        self.lib.td_execute.restype = ctypes.c_char_p
        self.lib.td_create_client_id.argtypes = []
        self.lib.td_create_client_id.restype = ctypes.c_int
        self.lib.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]
        self.lib.td_send.restype = None
        self.lib.td_receive.argtypes = [ctypes.c_double]
        self.lib.td_receive.restype = ctypes.c_char_p
        # Disable library logs, which may otherwise contain private update data.
        self.execute({"@type": "setLogStream", "log_stream": {"@type": "logStreamEmpty"}})
        self.execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": 0})
        self.client_id = None

    @staticmethod
    def encode(request):
        return json.dumps(request, separators=(",", ":"), allow_nan=False).encode()

    def execute(self, request):
        result = self.lib.td_execute(self.encode(request))
        if result is None:
            raise NativeError("No synchronous TDLib response")
        return json.loads(result)

    def open(self):
        if self.client_id is not None:
            raise NativeError("Client already open")
        self.client_id = self.lib.td_create_client_id()

    def send(self, request):
        if self.client_id is None:
            raise NativeError("Client is not open")
        self.lib.td_send(self.client_id, self.encode(request))

    def receive(self, timeout=0.1):
        # One receiver per process. Copy/parse before the next native invocation.
        raw = self.lib.td_receive(timeout)
        if raw is None:
            return None
        event = json.loads(raw)
        if event.get("@client_id") != self.client_id:
            return None
        return event

    def close(self, timeout=5):
        if self.client_id is None:
            return
        self.send({"@type": "close"})
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            event = self.receive()
            if event and event.get("@type") == "updateAuthorizationState":
                if event.get("authorization_state", {}).get("@type") == "authorizationStateClosed":
                    self.client_id = None
                    return
        raise NativeError("TDLib close was not confirmed")


def offline_native_check(path):
    """No setTdlibParameters, login, database, contacts or call requests."""
    td = TDJson(path)
    parsed = td.execute({"@type": "getJsonValue", "json": '{"probe":true}'})
    if parsed.get("@type") != "jsonValueObject":
        raise NativeError("Native JSON parser check failed")
    td.open()
    version = None
    auth_state = None
    try:
        td.send({"@type": "getOption", "name": "version", "@extra": "version-probe"})
        until = time.monotonic() + 5
        while time.monotonic() < until and (version is None or auth_state is None):
            event = td.receive()
            if not event:
                continue
            if event.get("@extra") == "version-probe" and event.get("@type") == "optionValueString":
                version = event["value"]
            if event.get("@type") == "updateAuthorizationState":
                auth_state = event.get("authorization_state", {}).get("@type")
        if version is None or auth_state != "authorizationStateWaitTdlibParameters":
            raise NativeError("Unexpected native initialization state")
    finally:
        td.close()
    return {"mode": "native-offline", "tdlib_version": version,
            "authorization": "not_configured", "native_json": "ok", "close": "confirmed",
            "login_requested": False, "media_ready": False}
