"""Installs the plugin's third-party pieces into the plugin folder:

* pure-Python packages into py_modules/ (Decky adds it to sys.path), and
* the QuickJS-NG binaries into bin/<platform>/qjs, the JavaScript engine yt-dlp uses for YouTube's
  challenges (Linux x86_64 and arm64, Windows x86_64), and
* on Windows, python.org's embeddable Python into bin/windows-x86_64/python/, for the yt-dlp worker.

aiohttp is not installed: Decky Loader bundles it. certifi is, for the yt-dlp worker (which runs in the
system's Python); inside Decky's process, Decky's own copy is found first. Every package is pinned to a
version and the hash of its file on PyPI (requirements.txt), and pip refuses anything else.
Run before packaging:  python scripts/vendor.py
"""
import hashlib
import io
import pathlib
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
PY_MODULES = ROOT / "py_modules"

QJS_VERSION = "v0.17.0"
QJS_RELEASE = f"https://github.com/quickjs-ng/quickjs/releases/download/{QJS_VERSION}"
# One per platform MusicDeck ships for (bin/<platform>/, see musicdeck/native.py): the release asset and
# its hash. Pinned: upstream publishes no checksums, so these are the hashes of the official assets.
# Windows on Arm runs the x86_64 build (QuickJS-NG publishes none for it).
QJS = {
    "linux-x86_64": ("qjs-linux-x86_64", "qjs", "0bfc02511a9f549c28b53880d988fc7cd5d361e90c5e8afdfcd7dc6774ceace5"),
    "linux-arm64": ("qjs-linux-aarch64", "qjs", "3372133484edf50a69f3c67903af41206d22a061e930e3cfb63269272ef56d2e"),
    "windows-x86_64": ("qjs-windows-x86_64.exe", "qjs.exe",
                       "2aeabf0092c3262d6b2609824418f7dd7ed1f1df939f73b2b15645230cac0d77"),
}

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

for platform_name, (asset, name, sha256) in QJS.items():
    qjs = ROOT / "bin" / platform_name / name
    if qjs.exists() and hashlib.sha256(qjs.read_bytes()).hexdigest() == sha256:
        continue
    qjs.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(f"{QJS_RELEASE}/{asset}").read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise SystemExit(f"QuickJS download checksum mismatch for {asset}: {digest}")
    qjs.write_bytes(data)
    qjs.chmod(qjs.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
# builds from before per-platform folders
(ROOT / "bin" / "qjs").unlink(missing_ok=True)

# Windows: python.org's embeddable Python, for the yt-dlp worker (Linux has a system Python; most
# Windows PCs have none). Its hash is the one python.org publishes for the file.
PYTHON_URL = "https://www.python.org/ftp/python/3.14.8/python-3.14.8-embed-amd64.zip"
PYTHON_SHA256 = "a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310"
python_dir = ROOT / "bin" / "windows-x86_64" / "python"
stamp = python_dir / ".source"
if not stamp.exists() or stamp.read_text().strip() != PYTHON_SHA256:
    data = urllib.request.urlopen(PYTHON_URL).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != PYTHON_SHA256:
        raise SystemExit(f"Python download checksum mismatch: {digest}")
    shutil.rmtree(python_dir, ignore_errors=True)
    python_dir.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(python_dir)
    stamp.write_text(PYTHON_SHA256 + "\n")
print("vendored into py_modules/ and bin/")
