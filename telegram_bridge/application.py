"""Complete runtime composition, gated until a specifically authorized call test.

No CLI automatically enables this entry point. Account/target/routing configuration
must be supplied locally, and an existing login is required. Import is side-effect free.
"""
from .auth import existing_authenticated_client
from .config import read_profile, read_routing, require_target
from .control import GateError
from .live import LiveCallLoop
from .runtime import NativeMedia, DEFAULT_RUNTIME


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
