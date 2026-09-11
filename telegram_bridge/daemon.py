"""Private launchd deployment of a fixed release, independently of working-tree edits."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile

from .config import private_directory
from .control import GateError

LABEL = 'eu.colibrie.codex-phone-bridge'


def atomic_private(path, data):
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.service-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def write_health(profile, status):
    atomic_private(profile/'service-health.json', json.dumps({**status,
        'checked_at': datetime.now(timezone.utc).isoformat()}).encode())


def prepare_install(root, service_root, profile, python, library, native_runtime):
    """Stage only. The caller deliberately bootstraps launchd after checking session ownership."""
    root, service_root = Path(root).resolve(), Path(service_root).resolve()
    for path in (python, library, native_runtime):
        if not Path(path).is_file(): raise GateError('service_dependency_missing')
    private_directory(service_root)
    files = sorted((root/'telegram_bridge').glob('*.py'))
    digest = hashlib.sha256()
    for path in files: digest.update(path.name.encode()); digest.update(path.read_bytes())
    digest.update(Path(native_runtime).read_bytes())
    release_id = digest.hexdigest()[:16]
    releases = service_root/'releases'; private_directory(releases)
    release = releases/release_id
    if not release.exists():
        temporary = Path(tempfile.mkdtemp(prefix='.release-', dir=releases))
        try:
            package = temporary/'telegram_bridge'; package.mkdir(mode=0o700)
            for path in files: shutil.copy2(path, package/path.name)
            shutil.copy2(library, temporary/'libtdjson.dylib')
            shutil.copy2(native_runtime, temporary/'media_runtime')
            os.replace(temporary, release)
        finally:
            if temporary.exists(): shutil.rmtree(temporary)
    log = service_root/'service.log'
    if not log.exists(): log.touch(mode=0o600)
    args = [str(Path(python).absolute()), '-u', '-m', 'telegram_bridge', 'serve-t3',
            '--profile', profile.name, '--all-projects', '--daemon', '--allow-messages-and-calls',
            '--allow-task-creation', '--seconds', '180', '--question-delay-seconds', '180',
            '--library', str(release/'libtdjson.dylib'), '--media-runtime', str(release/'media_runtime')]
    plist = {'Label': LABEL, 'ProgramArguments': args, 'WorkingDirectory': str(release),
             'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30, 'ExitTimeOut': 120,
             'ProcessType': 'Background', 'StandardOutPath': str(log), 'StandardErrorPath': str(log),
             'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                                      'PYTHONUNBUFFERED': '1'}}
    staged = service_root/'launch-agent.plist'
    atomic_private(staged, plistlib.dumps(plist))
    atomic_private(service_root/'installed.json', json.dumps({'release_id': release_id,
        'release': str(release), 'label': LABEL, 'profile': str(profile),
        'prepared_at': datetime.now(timezone.utc).isoformat()}).encode())
    return staged, release
