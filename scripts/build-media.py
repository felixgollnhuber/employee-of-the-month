#!/usr/bin/env python3
"""Build the pinned official Telegram media tree and a metadata-only probe.

Downloads build dependencies into .build, never installs system packages, opens
audio devices, logs in, or creates a call. No signing credentials are needed for
the macOS command-line target; the iOS application is not built.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import urllib.request

root = Path(__file__).resolve().parents[1]
pins = json.loads((root / "dependencies.json").read_text())
parent = pins["media_parent"]
source = root / ".build/vendor/telegram-ios"
tools = root / ".build/tools"
ownership_path = root / ".build/media-generated-hashes.json"
ownership = json.loads(ownership_path.read_text()) if ownership_path.exists() else {}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--jobs", type=int, default=4)
args = parser.parse_args()
if platform.system() != "Darwin" or platform.machine() != "arm64" or not 1 <= args.jobs <= 8:
    parser.error("Requires Apple Silicon macOS and 1 to 8 jobs")


def run(*command, cwd=root):
    subprocess.run(command, cwd=cwd, check=True)


def git_value(*command):
    return subprocess.check_output(["git", "-C", str(source), *command], text=True).strip()


def owned_write(path, data, baseline=None):
    key = str(path.relative_to(source))
    if path.exists():
        previous = path.read_bytes()
        if previous not in (data, baseline) and hashlib.sha256(previous).hexdigest() != ownership.get(key):
            parser.error("Preserving unknown changes in generated build inputs")
    path.write_bytes(data)
    ownership[key] = hashlib.sha256(data).hexdigest()
    ownership_path.write_text(json.dumps(ownership, indent=2) + "\n")


source.parent.mkdir(parents=True, exist_ok=True)
if not source.exists():
    run("git", "init", str(source))
    run("git", "-C", str(source), "remote", "add", "origin", parent["repository"])
    run("git", "-C", str(source), "fetch", "--filter=blob:none", "--depth", "1", "origin", parent["revision"])
    run("git", "-C", str(source), "sparse-checkout", "set", "submodules/TgVoipWebrtc", "submodules/ffmpeg", "third-party", "build-system")
    run("git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD")
if git_value("rev-parse", "HEAD") != parent["revision"]:
    parser.error("Preserving existing parent checkout with different revision")
modified = git_value("diff", "HEAD", "--name-only")
if set(modified.splitlines()) - {"third-party/webrtc/BUILD", "third-party/webrtc/webrtc"}:
    parser.error("Preserving modified tracked parent sources")
for relative, expected in ((parent["tgcalls_path"], pins["tgcalls"]["revision"]),
                           (parent["webrtc_path"], parent["webrtc_revision"])):
    if git_value("rev-parse", "HEAD:" + relative) != expected:
        parser.error("Incoherent dependency pins")
run("git", "-C", str(source), "sparse-checkout", "set", "submodules/TgVoipWebrtc", "submodules/ffmpeg", "third-party", "build-system")
run("git", "-C", str(source), "submodule", "update", "--init", "--depth", "1", "--",
    "submodules/TgVoipWebrtc/tgcalls", "third-party", "build-system/bazel-rules")
run("python3", str(root / "scripts/media-source-guard.py"), str(source / parent["webrtc_path"]))

upstream_build = subprocess.check_output(["git", "-C", str(source), "show", "HEAD:third-party/webrtc/BUILD"])
build_file = source / "third-party/webrtc/BUILD"
extended_build = upstream_build + b"\n" + (root / "native/macos_adm.BUILD").read_bytes()
owned_write(build_file, extended_build, upstream_build)

tools.mkdir(parents=True, exist_ok=True)
bazel = tools / ("bazel-" + pins["bazel"]["version"] + "-darwin-arm64")
if not bazel.exists():
    with urllib.request.urlopen(pins["bazel"]["url"], timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != pins["bazel"]["sha256"]:
        parser.error("Bazel checksum mismatch")
    bazel.write_bytes(data)
if hashlib.sha256(bazel.read_bytes()).hexdigest() != pins["bazel"]["sha256"]:
    parser.error("Existing Bazel checksum mismatch")
bazel.chmod(0o755)

# Required module declaration, no app signing or private configuration. The
# upstream tgcalls_core CLI target doesn't consume provisioning or API secrets.
configuration = source / "build-input/configuration-repository"
configuration.mkdir(parents=True, exist_ok=True)
for name, content in (("MODULE.bazel", 'module(name = "build_configuration")\n'), ("BUILD", "")):
    path = configuration / name
    if path.exists() and path.read_text() != content:
        parser.error("Preserving existing build configuration")
    path.write_text(content)
probe = source / "bridge_probe"
probe.mkdir(exist_ok=True)
for source_name, destination in (("media_probe.BUILD", "BUILD"), ("media_probe.cpp", "media_probe.cpp"),
                                 ("media_runtime.cpp", "media_runtime.cpp"), ("strict_devices.cpp", "strict_devices.cpp"),
                                 ("pcm_audio.cpp", "pcm_audio.cpp"), ("pcm_audio.h", "pcm_audio.h"),
                                 ("strict_devices.h", "strict_devices.h")):
    content = (root / "native" / source_name).read_bytes()
    path = probe / destination
    owned_write(path, content)
run(str(bazel), "--output_user_root=" + str(root / ".build/bazel-cache"), "build",
    "//bridge_probe:media_probe", "//bridge_probe:media_runtime", "--apple_platform_type=macos", "--cpu=darwin_arm64",
    "-c", "opt", "--jobs=" + str(args.jobs), cwd=source)
print("Built metadata probe:", source / "bazel-bin/bridge_probe/media_probe")
