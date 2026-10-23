"""
Lyrics in one shape for every source:

    {"synced": bool, "lines": [{"t": seconds | None, "text": str}], "source": str, "title"?: str}

"title" names lines that aren't lyrics (an audiobook's chapters, as "Chapters").

Sources provide their own when they have them; otherwise `lookup` asks the lyrics sites, none of
which needs an account: LRCLIB (lrclib.net), a free, open database of time-synced lyrics, then
NetEase Cloud Music's public lyrics, as a backup when LRCLIB is down or only has unsynced text.
"""
import asyncio
import re
from typing import Any, Optional

import aiohttp

from .services.base import ServiceError

LRC_TIME = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
LRC_META = re.compile(r"^\[[a-z]+:.*\]$", re.I)
LRCLIB = "https://lrclib.net/api"
NETEASE = "https://music.163.com/api"
USER_AGENT = "MusicDeck (https://github.com/SteamDeckHomebrew)"
RETRY_DELAY = 1.0
# NetEase starts lyrics with credits: "作词 : Freddie Mercury" (lyrics by, composed by...), and in
# some uploads "Artist: Queen". The first kind is always a credit; the second only when stamped
# in the first second, before anything is sung.
CREDIT = re.compile(r"^\s*[\u3000-\u9fff]+\s*[:：]")
LEADING_CREDIT = re.compile(r"^\s*[^:：]{1,25}[:：]\s*\S")
# how far a match's length may be from the track's, in seconds
LENGTH_SLACK = 3
NETEASE_TRIES = 3
KARAOKE = re.compile(r"instrumental|karaoke|伴奏", re.I)
# LRCLIB slows to a crawl when it's struggling (it answers 503 when it's down): give up on it soon, so
# Now Playing moves on to its visualizer instead of looking for lyrics for long
LRCLIB_TIMEOUT = aiohttp.ClientTimeout(total=5)


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
    """LRCLIB, asked again once after a server error or a dropped connection (it has busy moments)."""
    try:
        return await _ask_lrclib(session, track)
    except ServiceError as e:
        if "429" in str(e):  # rate limited: asking again straight away won't help
            raise
        await asyncio.sleep(RETRY_DELAY)
        return await _ask_lrclib(session, track)


async def _ask_lrclib(session: aiohttp.ClientSession, track: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The track's lyrics from LRCLIB, or None if it has none. Raises ServiceError when LRCLIB can't be
    asked (offline, rate limited, down), so that isn't taken for "no lyrics" and remembered as such."""
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
        async with session.get(f"{LRCLIB}/get", params=params, headers=headers, timeout=LRCLIB_TIMEOUT) as resp:
            if resp.status not in (200, 404):  # 404: no exact match
                raise ServiceError(f"LRCLIB answered {resp.status}")
            found = await resp.json() if resp.status == 200 else None
        if not found:
            q = {"track_name": track["title"], "artist_name": track["artist"]}
            async with session.get(f"{LRCLIB}/search", params=q, headers=headers, timeout=LRCLIB_TIMEOUT) as resp:
                if resp.status != 200:
                    raise ServiceError(f"LRCLIB answered {resp.status}")
                results = await resp.json()
            duration = track.get("duration") or 0
            # prefer synced lyrics from a recording of about the same length
            results.sort(key=lambda r: (not r.get("syncedLyrics"), abs((r.get("duration") or 0) - duration)))
            found = results[0] if results else None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        raise ServiceError("Couldn't reach LRCLIB") from e
    if not found or found.get("instrumental"):
        return None
    if found.get("syncedLyrics"):
        return parse_lrc(found["syncedLyrics"], "LRCLIB")
    if found.get("plainLyrics"):
        return plain(found["plainLyrics"], "LRCLIB")
    return None


def _plainest(text: str) -> str:
    """A title or name reduced for comparing: lower case, without "(feat. …)", "[Remastered]" or
    " - Live" additions, letters and digits only."""
    text = re.sub(r"[(\[].*?[)\]]", " ", text.lower())
    text = text.split(" - ")[0]
    return re.sub(r"[\W_]+", " ", text).strip()


def _same_song(track: dict[str, Any], title: str, artists: str, seconds: float) -> bool:
    want, got = _plainest(track.get("title", "")), _plainest(title)
    if not want or not got or (want not in got and got not in want):
        return False
    artist_words = set(_plainest(track.get("artist", "")).split())
    if artist_words and not artist_words & set(_plainest(artists).split()):
        return False
    duration = track.get("duration") or 0
    return not duration or not seconds or abs(duration - seconds) <= LENGTH_SLACK


def _opening_credit(line: str) -> bool:
    """A blank or "Label: value" line stamped in the song's first second (see LEADING_CREDIT)."""
    stamp = LRC_TIME.match(line)
    if not stamp or int(stamp.group(1)) * 60 + float(stamp.group(2)) >= 1:
        return False
    text = LRC_TIME.sub("", line)
    return not text.strip() or bool(LEADING_CREDIT.match(text))


async def from_netease(session: aiohttp.ClientSession, track: dict[str, Any]) -> Optional[dict[str, Any]]:
    """NetEase Cloud Music's lyrics for the track, matched by title, artist and length; None if it has
    none (or knows the song as instrumental). Raises ServiceError when NetEase can't be asked.

    NetEase lists one song many times (uploads, remixes, live takes), often with the lyrics on only
    some of them, so the closest few matches are tried in turn."""
    if not track.get("title") or not track.get("artist"):
        return None
    headers = {"User-Agent": USER_AGENT}
    try:
        async with session.get(f"{NETEASE}/search/get", headers=headers,
                               params={"s": f"{track['title']} {track['artist']}", "type": "1", "limit": "30"}) as resp:
            if resp.status != 200:
                raise ServiceError(f"NetEase answered {resp.status}")
            songs = ((await resp.json(content_type=None)).get("result") or {}).get("songs") or []
        want = _plainest(track.get("title", ""))
        matches = [x for x in songs
                   if not KARAOKE.search(x.get("name", "")) and _same_song(
                       track, x.get("name", ""), ", ".join(a.get("name", "") for a in x.get("artists") or []),
                       (x.get("duration") or 0) / 1000)]
        # the same title before longer ones ("Song (Remix)"), then the closest length
        matches.sort(key=lambda x: (_plainest(x.get("name", "")) != want,
                                    abs((x.get("duration") or 0) / 1000 - (track.get("duration") or 0))))
        for match in matches[:NETEASE_TRIES]:
            async with session.get(f"{NETEASE}/song/lyric", headers=headers,
                                   params={"id": str(match["id"]), "lv": "1"}) as resp:
                if resp.status != 200:
                    raise ServiceError(f"NetEase answered {resp.status}")
                data = await resp.json(content_type=None)
            if data.get("pureMusic"):
                return None  # NetEase knows it as instrumental
            text = (data.get("lrc") or {}).get("lyric") or ""
            lines = [line for line in text.splitlines() if not CREDIT.match(LRC_TIME.sub("", line))]
            while lines and _opening_credit(lines[0]):
                lines.pop(0)
            found = parse_lrc("\n".join(lines), "NetEase")
            if found:
                return found
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError) as e:
        raise ServiceError("Couldn't reach NetEase") from e
    return None


async def lookup(session: aiohttp.ClientSession, track: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Lyrics from the lyrics sites: LRCLIB's synced lyrics when it has them, else NetEase's, else
    LRCLIB's unsynced ones. Both are asked at once, so a slow or failing LRCLIB doesn't hold up the
    backup. "None" only when every site answered that it has none; if one couldn't be asked, that's
    an error (so the answer isn't remembered and the next look tries again)."""
    backup_task = asyncio.ensure_future(from_netease(session, track))
    first: Optional[dict[str, Any]] = None
    failure: Optional[ServiceError] = None
    try:
        first = await from_lrclib(session, track)
    except ServiceError as e:
        failure = e
    if first and first["synced"]:
        backup_task.cancel()
        return first
    try:
        backup = await backup_task
    except ServiceError as e:
        backup = None
        failure = failure or e
    if backup and (backup["synced"] or not first):
        return backup
    if first:
        return first
    if failure:
        raise failure
    return None
