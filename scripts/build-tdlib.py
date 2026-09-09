#!/usr/bin/env python3
"""Fetch a pinned official revision and build locally. No login/audio/system install."""
import argparse
import json
import pathlib
import platform
import shutil
import subprocess

root = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--jobs", type=int, default=4)
parser.add_argument("--openssl-root", type=pathlib.Path, default=pathlib.Path("/opt/homebrew/opt/openssl@3"))
args = parser.parse_args()
if platform.system() != "Darwin" or platform.machine() != "arm64":
    parser.error("This verified build recipe targets Apple Silicon macOS")
if not 1 <= args.jobs <= 8:
    parser.error("Choose 1 to 8 build jobs")
for executable in ("git", "cmake", "gperf", "clang++"):
    if not shutil.which(executable):
        parser.error(f"Missing build dependency: {executable}; not installed automatically")
if not (args.openssl_root / "include/openssl/ssl.h").exists():
    parser.error("OpenSSL development headers not found")
pin = json.loads((root / "dependencies.json").read_text())["tdlib"]
source = root / ".build/vendor/td"
build = root / ".build/tdlib"
source.parent.mkdir(parents=True, exist_ok=True)

def run(*command):
    subprocess.run(command, cwd=root, check=True)

if not source.exists():
    run("git", "init", str(source))
    run("git", "-C", str(source), "remote", "add", "origin", pin["repository"])
    run("git", "-C", str(source), "fetch", "--depth", "1", "origin", pin["revision"])
    run("git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD")
revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
if revision != pin["revision"]:
    parser.error("Existing source revision differs from pin; preserving checkout")
if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip():
    parser.error("Existing TDLib source has changes; preserving checkout")
run("cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DOPENSSL_ROOT_DIR=" + str(args.openssl_root),
    "-DTD_ENABLE_JNI=OFF")
run("cmake", "--build", str(build), "--target", "tdjson", "-j", str(args.jobs))
