"""
Keeps yt-dlp current between MusicDeck releases.

YouTube changes often and yt-dlp usually ships a fix within days, so waiting for a plugin update
isn't good enough. Updates are the official pure-Python wheels from PyPI, verified against PyPI's
published SHA-256, unpacked into the plugin's data folder. The yt-dlp worker puts that folder ahead
of the bundled copy; if an update won't start, it's removed and the bundled copy is used again.
"""
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import time
import zipfile
from typing import Any, Optional

import aiohttp
import certifi

from . import debuglog

PYPI = "https://pypi.org/pypi/{name}/json"
CHECK_EVERY = 7 * 24 * 3600


def updates_dir(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "ytdlp-update")


def _state_path(runtime_dir: str) -> str:
    return os.path.join(updates_dir(runtime_dir), "state.json")


def load_state(runtime_dir: str) -> dict[str, Any]:
    try:
        with open(_state_path(runtime_dir), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(runtime_dir: str, state: dict[str, Any]) -> None:
    os.makedirs(updates_dir(runtime_dir), exist_ok=True)
    tmp = _state_path(runtime_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, _state_path(runtime_dir))


def active_path(runtime_dir: str) -> Optional[str]:
    """The installed update to put first on the worker's PYTHONPATH, if any."""
    path = load_state(runtime_dir).get("active")
    return path if path and os.path.isdir(path) else None


def check_due(runtime_dir: str) -> bool:
    return time.time() - load_state(runtime_dir).get("checked_at", 0) > CHECK_EVERY


def _version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


async def _release(session: aiohttp.ClientSession, name: str, version: Optional[str] = None) -> dict[str, Any]:
    url = PYPI.format(name=name) if not version else f"https://pypi.org/pypi/{name}/{version}/json"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
    wheel = next((f for f in data["urls"] if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("py3-none-any.whl")),
                 None)
    if not wheel:
        raise RuntimeError(f"No pure-Python wheel for {name} {data['info']['version']}")
    return {"version": data["info"]["version"], "wheel": wheel, "requires": data["info"].get("requires_dist") or []}


async def _fetch_wheel(session: aiohttp.ClientSession, release: dict[str, Any], target: str) -> None:
    wheel = release["wheel"]
    async with session.get(wheel["url"]) as resp:
        resp.raise_for_status()
        blob = await resp.read()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != wheel["digests"]["sha256"]:
        raise RuntimeError(f"Checksum mismatch for {wheel['filename']}")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for member in z.namelist():
            # wheels are trusted after the checksum, but never let a path escape the target
            if member.startswith("/") or ".." in member.split("/"):
                raise RuntimeError(f"Unsafe path in {wheel['filename']}: {member}")
        z.extractall(target)


async def update(runtime_dir: str, current_version: str) -> dict[str, Any]:
    """Installs the latest yt-dlp (+ the yt-dlp-ejs version it requires) if newer than what runs.
    Returns {"updated": bool, "version": str}. The caller restarts the worker and calls
    `confirm` or `rollback` depending on whether it comes up."""
    state = load_state(runtime_dir)
    connector = aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=120)) as session:
        latest = await _release(session, "yt-dlp")
        state["checked_at"] = time.time()
        _save_state(runtime_dir, state)
        if current_version and _version_key(latest["version"]) <= _version_key(current_version):
            return {"updated": False, "version": current_version}
        pin = next((re.search(r"yt-dlp-ejs\s*==\s*([\w.]+)", r).group(1) for r in latest["requires"]
                    if re.search(r"yt-dlp-ejs\s*==", r)), None)
        ejs = await _release(session, "yt-dlp-ejs", pin)
        target = os.path.join(updates_dir(runtime_dir), latest["version"])
        staging = target + ".tmp"
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        await _fetch_wheel(session, latest, staging)
        await _fetch_wheel(session, ejs, staging)
    shutil.rmtree(target, ignore_errors=True)
    os.replace(staging, target)
    state.update(previous=state.get("active"), active=target, pending=latest["version"])
    _save_state(runtime_dir, state)
    debuglog.debug("youtube", "yt-dlp update installed", version=latest["version"], ejs=ejs["version"])
    return {"updated": True, "version": latest["version"]}


def confirm(runtime_dir: str) -> None:
    """The new version started fine: drop the one it replaced."""
    state = load_state(runtime_dir)
    old = state.pop("previous", None)
    state.pop("pending", None)
    if old and old != state.get("active"):
        shutil.rmtree(old, ignore_errors=True)
    _save_state(runtime_dir, state)


def rollback(runtime_dir: str) -> None:
    """The new version didn't start: go back to the previous update (or the bundled copy)."""
    state = load_state(runtime_dir)
    broken = state.get("active")
    state["active"] = state.pop("previous", None)
    state.pop("pending", None)
    if broken:
        shutil.rmtree(broken, ignore_errors=True)
    _save_state(runtime_dir, state)
    debuglog.debug("youtube", "yt-dlp update rolled back", broken=broken)
