"""
New versions of MusicDeck, from its GitHub releases: the latest release's zip for this platform and its SHA-256.

Installing is left to Decky's own installer (the frontend asks it, `utilities/install_plugin`): it asks the user
first, downloads the zip, checks it against the SHA-256, replaces the plugin's folder and loads it again. Nothing
of MusicDeck's own is in that folder (settings and data live in Decky's settings and data folders), so an update
keeps everything.
"""
import asyncio
import re
from typing import Any, Optional

import aiohttp

from . import native, version

REPO = "notsosaelin/MusicDeck"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
# how often to look again while Steam runs
EVERY = 12 * 3600
FIRST = 90  # after starting, once the network's had time to come up


def parse(text: str) -> tuple[int, ...]:
    """'v0.36.2' -> (0, 36, 2); anything else in a part counts as 0."""
    parts = []
    for part in str(text).strip().lstrip("vV").split("-")[0].split("."):
        digits = re.match(r"\d+", part)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts) or (0,)


def newer(latest: str, current: str) -> bool:
    return parse(latest) > parse(current)


def zip_platform(platform_name: Optional[str] = None) -> str:
    """The zip a platform installs: Windows on Arm runs the x86_64 one."""
    platform_name = platform_name or native.platform_id()
    return "windows-x86_64" if platform_name.startswith("windows") else platform_name


def pick(release: dict[str, Any], platform_name: Optional[str] = None) -> Optional[dict[str, Any]]:
    """From a GitHub release: {"version", "url", "asset", "sha256"?, "sha256_url"?, "notes"} for this
    platform's zip, or None when the release has none."""
    tag = str(release.get("tag_name") or "")
    if not tag or release.get("draft") or release.get("prerelease"):
        return None
    want = f"-{zip_platform(platform_name)}.zip"
    assets = [a for a in release.get("assets") or [] if isinstance(a, dict)]
    found = next((a for a in assets if str(a.get("name", "")).startswith("MusicDeck-") and str(a.get("name")).endswith(want)), None)
    if not found or not found.get("browser_download_url"):
        return None
    out: dict[str, Any] = {"version": tag.lstrip("vV"), "url": found["browser_download_url"], "asset": found["name"],
                           "notes": str(release.get("body") or "")[:4000]}
    digest = str(found.get("digest") or "")
    if digest.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", digest[7:]):
        out["sha256"] = digest[7:]
    else:
        side = next((a for a in assets if a.get("name") == found["name"] + ".sha256"), None)
        if side and side.get("browser_download_url"):
            out["sha256_url"] = side["browser_download_url"]
    return out


async def check(session: aiohttp.ClientSession) -> Optional[dict[str, Any]]:
    """The newest release when it's newer than this one, with the SHA-256 Decky checks the zip against;
    None when this is the newest (or the release can't be checked: never an update without its hash)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": version.USER_AGENT}
    timeout = aiohttp.ClientTimeout(total=20)
    async with session.get(LATEST, headers=headers, timeout=timeout) as resp:
        if resp.status == 404:  # no release yet
            return None
        resp.raise_for_status()
        release = await resp.json()
    found = pick(release)
    if not found or not newer(found["version"], version.current()):
        return None
    if "sha256" not in found:
        url = found.pop("sha256_url", None)
        if not url:
            return None
        async with session.get(url, headers={"User-Agent": version.USER_AGENT}, timeout=timeout) as resp:
            resp.raise_for_status()
            text = (await resp.content.read(4096)).decode("utf-8", "replace")
        digest = re.search(r"\b[0-9a-fA-F]{64}\b", text)
        if not digest:
            return None
        found["sha256"] = digest.group().lower()
    return found


class Watcher:
    """Looks for a new version soon after starting and then twice a day; `found` holds what it last saw."""

    def __init__(self, session: aiohttp.ClientSession, on_found, log):
        self.session = session
        self.on_found = on_found
        self.log = log
        self.found: Optional[dict[str, Any]] = None
        self.checked = False
        self.task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self.task = asyncio.ensure_future(self._run())

    def stop(self) -> None:
        if self.task:
            self.task.cancel()

    async def check_now(self) -> Optional[dict[str, Any]]:
        try:
            found = await check(self.session)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            self.log.info(f"Couldn't look for a new version: {e}")
            return self.found
        self.checked = True
        fresh = found and (not self.found or self.found["version"] != found["version"])
        self.found = found
        if fresh:
            self.log.info(f"MusicDeck {found['version']} is out (this is {version.current()})")
            await self.on_found()
        return found

    async def _run(self) -> None:
        await asyncio.sleep(FIRST)
        while True:
            await self.check_now()
            await asyncio.sleep(EVERY)
