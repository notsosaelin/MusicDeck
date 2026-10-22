"""
Lyrics in one shape for every source:

    {"synced": bool, "lines": [{"t": seconds | None, "text": str}], "source": str}

Sources provide their own when they have them; otherwise we ask LRCLIB (lrclib.net), a free, open
lyrics database with time-synced lyrics that needs no API key.
"""
import re
from typing import Any, Optional

import aiohttp

LRC_TIME = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
LRC_META = re.compile(r"^\[[a-z]+:.*\]$", re.I)
LRCLIB = "https://lrclib.net/api"
USER_AGENT = "MusicDeck (https://github.com/SteamDeckHomebrew)"


def parse_lrc(text: str, source: str) -> Optional[dict[str, Any]]:
    """Parses LRC ("[mm:ss.xx] line"); falls back to plain lines when there are no timestamps."""
    lines: list[dict[str, Any]] = []
    synced = False
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or LRC_META.match(raw) and not LRC_TIME.match(raw):
            continue
        stamps = LRC_TIME.findall(raw)
        words = LRC_TIME.sub("", raw).strip()
        if stamps:
            synced = True
            # a line can carry several timestamps when it repeats (choruses)
            lines.extend({"t": int(m) * 60 + float(s), "text": words} for m, s in stamps)
        else:
            lines.append({"t": None, "text": words})
    if not any(line["text"] for line in lines):
        return None
    if synced:
        lines = sorted((line for line in lines if line["t"] is not None), key=lambda line: line["t"])
    return {"synced": synced, "lines": lines, "source": source}


def plain(text: str, source: str) -> Optional[dict[str, Any]]:
    lines = [{"t": None, "text": line.strip()} for line in text.splitlines()]
    if not any(line["text"] for line in lines):
        return None
    return {"synced": False, "lines": lines, "source": source}


async def from_lrclib(session: aiohttp.ClientSession, track: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not track.get("title") or not track.get("artist"):
        return None
    params = {"track_name": track["title"], "artist_name": track["artist"]}
    if track.get("album"):
        params["album_name"] = track["album"]
    if track.get("duration"):
        params["duration"] = str(round(track["duration"]))
    headers = {"User-Agent": USER_AGENT}
    try:
        # /get is an exact match (title, artist, album, ±2s duration); /search is the fuzzy fallback
        async with session.get(f"{LRCLIB}/get", params=params, headers=headers) as resp:
            if resp.status == 200:
                found = await resp.json()
            else:
                found = None
        if not found:
            q = {"track_name": track["title"], "artist_name": track["artist"]}
            async with session.get(f"{LRCLIB}/search", params=q, headers=headers) as resp:
                results = await resp.json() if resp.status == 200 else []
            duration = track.get("duration") or 0
            # prefer synced lyrics from a recording of about the same length
            results.sort(key=lambda r: (not r.get("syncedLyrics"), abs((r.get("duration") or 0) - duration)))
            found = results[0] if results else None
    except (aiohttp.ClientError, ValueError):
        return None
    if not found or found.get("instrumental"):
        return None
    if found.get("syncedLyrics"):
        return parse_lrc(found["syncedLyrics"], "LRCLIB")
    if found.get("plainLyrics"):
        return plain(found["plainLyrics"], "LRCLIB")
    return None
