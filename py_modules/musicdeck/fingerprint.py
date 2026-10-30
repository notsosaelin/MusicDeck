"""
What a song is by how it sounds, as iTunes and MusicBrainz Picard tell: Chromaprint's fpcalc (bin/<platform>/fpcalc,
scripts/vendor.py) listens to the first two minutes and makes a fingerprint, and AcoustID's free service says which
MusicBrainz recordings sound like that, with the albums (release groups) they're on.

AcoustID asks each app for its own application key (free, registered once at acoustid.org): MusicDeck's is APP_KEY.
Without one, nothing is fingerprinted. At most three lookups a second, as AcoustID asks.
"""
import asyncio
import json
import os
import subprocess
import time
from collections import Counter
from typing import Any, Optional

import aiohttp

from . import native

# MusicDeck's AcoustID application key (acoustid.org/new-application): empty, fingerprinting is off
APP_KEY = ""
LOOKUP = "https://api.acoustid.org/v2/lookup"
LISTEN_SECONDS = 120  # how much of a song fpcalc listens to (AcoustID's own default)
FPCALC_TIMEOUT = 60
LOOKUP_GAP = 0.34  # seconds between lookups: three a second at most
SURE = 0.6  # AcoustID's score a match needs to count


class Fingerprinter:
    def __init__(self, http: aiohttp.ClientSession, runtime_dir: Optional[str], key: str = ""):
        self.http = http
        self.exe = native.helper("fpcalc", runtime_dir)
        self.key = key or APP_KEY
        self.last = 0.0
        self.pace = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return bool(self.exe and self.key)

    async def fingerprint(self, path: str) -> Optional[tuple[int, str]]:
        """(duration in seconds, fingerprint) of a song, or None when fpcalc can't read it."""
        if not self.exe or not os.path.isfile(path):
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.exe, "-json", "-length", str(LISTEN_SECONDS), path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            out, _ = await asyncio.wait_for(proc.communicate(), FPCALC_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return None
        try:
            got = json.loads(out)
            duration, print_ = int(float(got["duration"])), str(got["fingerprint"])
        except (ValueError, KeyError, TypeError):
            return None
        return (duration, print_) if duration > 0 and print_ else None

    async def lookup(self, duration: int, fingerprint: str) -> list[dict[str, Any]]:
        """The recordings that sound like it, best first: [{"score", "title", "artist", "albums": [titles]}]."""
        if not self.key:
            return []
        async with self.pace:
            wait = LOOKUP_GAP - (time.monotonic() - self.last)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()
            try:
                async with self.http.post(LOOKUP, data={"client": self.key, "meta": "recordings releasegroups compress",
                                                        "duration": str(duration), "fingerprint": fingerprint},
                                          timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return []
        if not isinstance(data, dict) or data.get("status") != "ok":
            return []
        found = []
        for result in data.get("results") or []:
            score = result.get("score") or 0
            for rec in result.get("recordings") or []:
                artists = " & ".join(a.get("name", "") for a in rec.get("artists") or [] if a.get("name"))
                albums = [g.get("title", "") for g in rec.get("releasegroups") or []
                          if g.get("title") and g.get("type", "Album") in ("Album", "EP", "Single")]
                if rec.get("title") and artists:
                    found.append({"score": score, "title": rec["title"], "artist": artists, "albums": albums})
        return sorted(found, key=lambda r: -r["score"])

    async def album_of(self, paths: list[str]) -> Optional[tuple[str, str]]:
        """(artist, album) that a few songs of one album agree on by how they sound, or None."""
        votes: Counter = Counter()
        heard = 0
        for path in paths:
            print_ = await self.fingerprint(path)
            if not print_:
                continue
            matches = [m for m in await self.lookup(*print_) if m["score"] >= SURE]
            if not matches:
                continue
            heard += 1
            # each song votes once for every album it's on (its best match's)
            best = matches[0]
            for album in dict.fromkeys(best["albums"]):
                votes[(best["artist"], album)] += 1
        if not votes:
            return None
        (artist, album), count = votes.most_common(1)[0]
        # most of the songs heard agree (one song alone is enough when it's the only one)
        return (artist, album) if count * 2 > heard or heard == 1 else None
