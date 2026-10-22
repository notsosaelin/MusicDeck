"""Installs the plugin's third-party pieces into the plugin folder:

* pure-Python packages into py_modules/ (Decky adds it to sys.path), and
* the QuickJS-NG binary into bin/qjs, the JavaScript engine yt-dlp uses for YouTube's challenges.

aiohttp and certifi are not installed: Decky Loader bundles them. Every package is pinned to a
version and the hash of its file on PyPI (requirements.txt), and pip refuses anything else.
Run before packaging:  python scripts/vendor.py
"""
import hashlib
import pathlib
import shutil
import stat
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
PY_MODULES = ROOT / "py_modules"

QJS_VERSION = "v0.17.0"
QJS_URL = f"https://github.com/quickjs-ng/quickjs/releases/download/{QJS_VERSION}/qjs-linux-x86_64"
# Pinned: upstream publishes no checksums, so this is the hash of the official release asset.
QJS_SHA256 = "0bfc02511a9f549c28b53880d988fc7cd5d361e90c5e8afdfcd7dc6774ceace5"

# Force portable pure-Python wheels: building on Windows/macOS must not pull in compiled wheels for
# the build machine (e.g. charset-normalizer's mypyc build) that can't load on the Deck.
subprocess.check_call([
    sys.executable, "-m", "pip", "install",
    "--target", str(PY_MODULES),
    "--upgrade", "--no-deps", "--no-compile", "--require-hashes",
    "--only-binary=:all:", "--platform", "any", "--implementation", "py", "--abi", "none",
    "-r", str(REQUIREMENTS),
])

# pip also drops console scripts and man pages for some packages; the plugin doesn't need them
for extra in ("bin", "share"):
    shutil.rmtree(PY_MODULES / extra, ignore_errors=True)

qjs = ROOT / "bin" / "qjs"
if not qjs.exists() or hashlib.sha256(qjs.read_bytes()).hexdigest() != QJS_SHA256:
    qjs.parent.mkdir(exist_ok=True)
    data = urllib.request.urlopen(QJS_URL).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != QJS_SHA256:
        raise SystemExit(f"QuickJS download checksum mismatch: {digest}")
    qjs.write_bytes(data)
    qjs.chmod(qjs.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print("vendored into py_modules/ and bin/")
