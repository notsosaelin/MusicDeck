"""MusicBrainz and the Cover Art Archive, for the local source: finding an album's release to fill in its
tags, and its front cover when the files have none. No account; asked only when the user looks something
up or asks for missing artwork.

MusicBrainz allows one request a second from each client and asks for a User-Agent naming the app
(https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting): requests here queue behind each other and
keep that pace. Pictures come from coverartarchive.org, which has no such limit.
"""
import asyncio
import time
from typing import Any, Optional

import aiohttp

from . import version
from .pictures import download, plain
from .services.base import ServiceError

API = "https://musicbrainz.org/ws/2"
CAA = "https://coverartarchive.org"
SPACING = 1.1  # seconds between MusicBrainz requests
COVER_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def _year(date: Any) -> str:
    return str(date or "")[:4] if str(date or "")[:4].isdigit() else ""


def _credit(credits: Any) -> str:
    """An artist credit as MusicBrainz writes it out ("A feat. B")."""
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits or [] if isinstance(c, dict))


def _quote(text: str) -> str:
    """A phrase for MusicBrainz's search syntax (Lucene): quoted, its own quotes and backslashes escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MusicBrainz:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.lock = asyncio.Lock()
        self.last = 0.0
        self.headers = {"User-Agent": f"MusicDeck/{version.VERSION} ( Decky Loader music plugin for SteamOS )",
                        "Accept": "application/json"}

    async def _get(self, path: str, **params: Any) -> Any:
        async with self.lock:
            wait = self.last + SPACING - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self.session.get(f"{API}/{path}", params={**params, "fmt": "json"},
                                            headers=self.headers) as resp:
                    if resp.status == 503:
                        raise ServiceError("MusicBrainz is busy; try again in a moment")
                    if resp.status == 404:
                        raise ServiceError("Not found on MusicBrainz")
                    if resp.status >= 400:
                        raise ServiceError(f"MusicBrainz returned HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise ServiceError(f"Couldn't reach MusicBrainz: {e}") from e
            finally:
                self.last = time.monotonic()

    async def search(self, artist: str, album: str, tracks: int = 0) -> list[dict[str, Any]]:
        """Releases that may be this album, best first: {"id", "group", "title", "artist", "year", "country",
        "tracks", "format", "score"}. One with the album's track count goes ahead of one without."""
        query = f"release:{_quote(album)}"
        if artist and artist.lower() not in ("various artists", "unknown artist"):
            query += f" AND artist:{_quote(artist)}"
        data = await self._get("release/", query=query, limit=10)
        found = []
        for r in data.get("releases") or []:
            media = r.get("media") or []
            found.append({
                "id": r.get("id", ""), "group": (r.get("release-group") or {}).get("id", ""),
                "title": r.get("title", ""), "artist": _credit(r.get("artist-credit")),
                "year": _year(r.get("date")), "country": r.get("country") or "",
                "tracks": sum(m.get("track-count", 0) for m in media),
                "format": " + ".join(sorted({m.get("format") or "" for m in media} - {""})),
                "score": int(r.get("score") or 0),
            })
        return sorted(found, key=lambda r: (r["tracks"] != tracks if tracks else False, -r["score"]))[:8]

    async def release(self, mbid: str) -> dict[str, Any]:
        """One release: {"id", "title", "artist", "year", "genres", "label", "group",
        "tracks": [{"disc", "track", "title", "artist"}]} in disc and track order."""
        r = await self._get(f"release/{mbid}", inc="recordings artist-credits release-groups labels genres")
        group = r.get("release-group") or {}
        # the genres people voted for most, the release group's when the release has none of its own
        votes = r.get("genres") or group.get("genres") or []
        genres = [g["name"].title() for g in sorted(votes, key=lambda g: -int(g.get("count") or 0))[:3] if g.get("name")]
        labels = [li.get("label", {}).get("name") for li in r.get("label-info") or [] if li.get("label")]
        tracks = []
        for medium in r.get("media") or []:
            for t in medium.get("tracks") or []:
                tracks.append({"disc": int(medium.get("position") or 1), "track": int(t.get("position") or 0),
                               "title": t.get("title") or (t.get("recording") or {}).get("title", ""),
                               "artist": _credit(t.get("artist-credit")) or _credit(r.get("artist-credit"))})
        return {"id": r.get("id", mbid), "title": r.get("title", ""), "artist": _credit(r.get("artist-credit")),
                # the year it first came out, as the library shows it
                "year": _year(group.get("first-release-date")) or _year(r.get("date")),
                "genres": genres, "label": labels[0] if labels else "", "group": group.get("id", ""),
                "discs": len(r.get("media") or []), "tracks": tracks}

    async def by_disc(self, disc_id: str) -> Optional[tuple[str, int]]:
        """The release a CD with this disc ID belongs to, and which of its discs it is: (release id,
        disc number), or None when MusicBrainz doesn't know the disc."""
        try:
            data = await self._get(f"discid/{disc_id}", inc="", cdstubs="no")
        except ServiceError as e:
            if "Not found" in str(e):
                return None
            raise
        for r in data.get("releases") or []:
            for medium in r.get("media") or []:
                if any(d.get("id") == disc_id for d in medium.get("discs") or []):
                    return r.get("id", ""), int(medium.get("position") or 1)
        releases = data.get("releases") or []
        return (releases[0].get("id", ""), 1) if releases else None

    async def cover(self, release_id: str, group_id: str = "") -> Optional[tuple[bytes, str]]:
        """The front cover (500 px) of a release, else of its release group: (bytes, extension), or None."""
        paths = [f"release/{release_id}/front-500"] + ([f"release-group/{group_id}/front-500"] if group_id else [])
        for path in paths:
            picture = await download(self.session, f"{CAA}/{path}", self.headers, COVER_TYPES)
            if picture:
                return picture
        return None

    async def find_cover(self, artist: str, album: str) -> Optional[tuple[bytes, str]]:
        """The cover of the best match for an album, for filling in missing artwork."""
        for r in (await self.search(artist, album))[:3]:
            # a match by name alone: a weak one, or another title, could be the wrong record's cover
            if r["score"] < 90:
                break
            if plain(r["title"]) != plain(album):
                continue
            cover = await self.cover(r["id"], r["group"])
            if cover:
                return cover
        return None
