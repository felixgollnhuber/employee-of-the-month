"""Explicit database-key enrollment and automatic noninteractive macOS retrieval."""
import base64
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess

from .config import private_directory, read_private_json, write_private_json
from .control import GateError

MARKER = "database-keychain.json"
HELPER = Path.home()/"Library/Application Support/EmployeeOfTheMonth/bin/KeychainStore"
LEGACY_HELPER = Path.home()/"Library/Application Support/CodexPhoneBridge/bin/KeychainStore"
BUILD_HELPER = Path(__file__).resolve().parents[1]/".build/KeychainStore"
KEYCHAIN_SERVICE = "EmployeeOfTheMonth.Telegram.DatabaseKey.v1"


def keychain_configured(profile):
    marker=profile/MARKER
    return marker.exists() or marker.is_symlink()


def account_for(profile):
    descriptor = os.open(profile/"key-salt",os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(descriptor,"rb") as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise GateError("unsafe_database_salt")
        salt=stream.read(33)
    if len(salt)!=32:raise GateError("invalid_database_salt")
    return hashlib.sha256(str(profile.resolve()).encode()+b"\0"+salt).hexdigest()


def _legacy_profile(profile):
    return "CodexPhoneBridge" in Path(profile).resolve().parts


def helper_for(profile):
    return LEGACY_HELPER if _legacy_profile(profile) else HELPER


def install_helper(profile):
    # A stable installed executable preserves its Keychain identity across repo builds.
    helper = helper_for(profile)
    private_directory(helper.parent)
    if not helper.exists():
        if not BUILD_HELPER.is_file():raise GateError("build_keychain_helper_first")
        shutil.copyfile(BUILD_HELPER,helper)
        helper.chmod(0o700)
    info=helper.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:
        raise GateError("unsafe_keychain_helper")


def _helper(operation,account,key=None,profile=None):
    helper = helper_for(profile) if profile is not None else HELPER
    if not helper.is_file():raise GateError("installed_keychain_helper_missing")
    arguments = [str(helper),operation,account]
    if profile is None or not _legacy_profile(profile): arguments.append(KEYCHAIN_SERVICE)
    try:
        result=subprocess.run(arguments,
            input=None if key is None else key+"\n",capture_output=True,text=True,timeout=30)
    except (OSError,subprocess.TimeoutExpired):raise GateError("macos_keychain_unavailable") from None
    if result.returncode:raise GateError("macos_keychain_unavailable")
    return result.stdout.removesuffix("\n")


def cached_database_key(profile):
    marker=profile/MARKER
    if not keychain_configured(profile):return None
    config=read_private_json(profile,MARKER)
    account=account_for(profile)
    if config!={"version":1,"account":account}:
        raise GateError("keychain_database_binding_changed")
    key=_helper("get",account,None,profile)
    try:valid=len(base64.b64decode(key,validate=True))==32
    except (ValueError,TypeError):valid=False
    if not valid:raise GateError("invalid_cached_database_key")
    return key


def enrollment_dialog(_prompt):
    script='''tell application "System Events"
activate
set keyReply to display dialog "Enter the existing Telegram database passphrase once. After verification, the derived database key is stored in macOS Keychain so future calls do not need this prompt." with title "Employee of the Month - Remember login" default answer "" with hidden answer buttons {"Cancel", "Save in Keychain"} default button "Save in Keychain" cancel button "Cancel" giving up after 300
if gave up of keyReply then error number -128
return text returned of keyReply
end tell'''
    result=subprocess.run(["/usr/bin/osascript","-e",script],capture_output=True,text=True,timeout=310)
    if result.returncode:raise GateError("keychain_enrollment_cancelled")
    return result.stdout.removesuffix("\n")


def remember_database_key(profile,library,*,secret_input,emit=lambda value:None):
    from .auth import local_key,existing_authenticated_client
    private_directory(profile)
    if not (profile/"database").is_dir() or not (profile/"key-salt").is_file():
        raise GateError("existing_telegram_database_required")
    install_helper(profile)
    existing=cached_database_key(profile)
    key=existing or local_key(profile,secret_input=secret_input)
    # Verify the actual database and sender before saving anything in Keychain.
    with existing_authenticated_client(profile,library,database_key=key):
        emit({"telegram_database_key_verified":True,"calls_started":0})
    if existing is None:
        account=account_for(profile)
        _helper("set",account,key,profile)
        if _helper("get",account,None,profile)!=key:raise GateError("keychain_readback_failed")
        write_private_json(profile,MARKER,{"version":1,"account":account})
    emit({"database_key_saved_in_keychain":True,"passphrase_saved_as_plaintext":False,
          "automatic_unlock_enabled":True,"unchanged":existing is not None})
