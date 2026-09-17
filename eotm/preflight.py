"""Report local prerequisites without authenticating or starting audio/network clients."""
import importlib.util

from .config import read_profile, read_live_config
from .runtime import DEFAULT_RUNTIME
from .keychain import keychain_configured


def offline_preflight(profile):
    try:
        config=read_profile(profile)
        configured=True
        target=bool(config.get('target_username'))
    except (OSError,ValueError,RuntimeError): configured=target=False
    try:
        live_config=read_live_config(profile)
        key=live_config.get('api_key')
        voice=live_config.get('voice','cedar')
        api= isinstance(key,str) and key.startswith('sk-')
    except (OSError,ValueError,RuntimeError): api=False;voice='cedar'
    native=DEFAULT_RUNTIME.is_file()
    dependencies=importlib.util.find_spec('websockets') is not None
    try:
        from .t3 import T3Client
        T3Client.from_profile(profile)
        t3=True
    except (OSError,ValueError,RuntimeError,KeyError): t3=False
    checks={'local_account_configuration':configured,'confirmed_target':target,
            'openai_api_key':api,'native_runtime':native,'live_dependencies':dependencies,
            'supported_voice':voice in ('cedar','marin')}
    return {'voice_backend':'gpt-live-1','voice':voice,'audio_route':'direct_pcm',
            'database_keychain_configured':keychain_configured(profile),
            'configuration_present_and_private':configured,'target_configured':target,
            'openai_api_key_configured':api,'native_runtime_present':native,
            'live_dependencies_present':dependencies,'t3_connection_configured':t3,
            'ready_for_call_attempt':all(checks.values()),
            'telegram_authenticated':'not_checked_offline','openai_api_access':'not_checked_offline',
            'blockers':[name for name,ok in checks.items() if not ok]}
