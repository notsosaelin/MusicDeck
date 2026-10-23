"""Albums' details and covers from the public catalogues beside MusicBrainz: Apple's (the iTunes Search
API) and Deezer's. No account or key; metadata and pictures only, nothing is played from them.

They cover what MusicBrainz may not (new releases, small labels) and have large covers (1200 and 1000
pixels). Their releases have the shape MusicBrainz.release() returns, ids prefixed with the catalogue
("itunes:<id>", "deezer:<id>"), so the local source looks them up and applies them the same way.
"""
import asyncio
from typing import Any, Optional

import aiohttp

from . import version
from .pictures import download, plain
from .services.base import ServiceError

ITUNES = "https://itunes.apple.com"
DEEZER = "https://api.deezer.com"
SOURCES = {"itunes": "Apple Music", "deezer": "Deezer"}


def _year(date: Any) -> str:
    return str(date or "")[:4] if str(date or "")[:4].isdigit() else ""


def _big_itunes_art(url: str) -> str:
    """Apple's artwork at 1200 px: its links name their size ("100x100bb")."""
    return url.replace("100x100bb", "1200x1200bb") if url else ""


class Catalogues:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.headers = {"User-Agent": f"MusicDeck/{version.VERSION}"}

    async def _get(self, url: str, **params: Any) -> Any:
        try:
            async with self.session.get(url, params=params, headers=self.headers) as resp:
                if resp.status == 429 or resp.status == 403:
                    raise ServiceError("The catalogue is busy; try again in a moment")
                if resp.status >= 400:
                    raise ServiceError(f"The catalogue answered HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ServiceError(f"Couldn't reach the catalogue: {e}") from e

    # --- finding albums ---

    async def search(self, artist: str, album: str) -> list[dict[str, Any]]:
        """Albums of that name in both catalogues, as MusicBrainz.search() lists releases (with "source").
        One that can't be asked is left out."""
        found = await asyncio.gather(self._search_itunes(artist, album), self._search_deezer(artist, album),
                                     return_exceptions=True)
        return [r for part in found if isinstance(part, list) for r in part]

    @staticmethod
    def _score(title: str, artist: str, album: str, wanted_artist: str) -> int:
        """How sure a match by name is, as MusicBrainz scores: the same title, then the same artist."""
        score = 100 if plain(title) == plain(album) else 70 if plain(album) in plain(title) else 40
        if wanted_artist and plain(artist) != plain(wanted_artist):
            score -= 20
        return score

    async def _search_itunes(self, artist: str, album: str) -> list[dict[str, Any]]:
        data = await self._get(f"{ITUNES}/search", term=f"{artist} {album}".strip(), entity="album", limit=10)
        return [{"id": f"itunes:{r['collectionId']}", "group": "", "source": SOURCES["itunes"],
                 "title": r.get("collectionName", ""), "artist": r.get("artistName", ""),
                 "year": _year(r.get("releaseDate")), "country": r.get("country", ""), "tracks": int(r.get("trackCount") or 0),
                 "format": "Digital", "score": self._score(r.get("collectionName", ""), r.get("artistName", ""), album, artist)}
                for r in (data or {}).get("results") or [] if r.get("collectionId")]

    async def _search_deezer(self, artist: str, album: str) -> list[dict[str, Any]]:
        query = f'album:"{album}"' + (f' artist:"{artist}"' if artist else "")
        data = await self._get(f"{DEEZER}/search/album", q=query, limit=10)
        return [{"id": f"deezer:{r['id']}", "group": "", "source": SOURCES["deezer"],
                 "title": r.get("title", ""), "artist": (r.get("artist") or {}).get("name", ""), "year": "",
                 "country": "", "tracks": int(r.get("nb_tracks") or 0), "format": "Digital",
                 "score": self._score(r.get("title", ""), (r.get("artist") or {}).get("name", ""), album, artist)}
                for r in (data or {}).get("data") or [] if r.get("id")]

    # --- one album ---

    async def release(self, release_id: str) -> dict[str, Any]:
        """An album from a catalogue as MusicBrainz.release() gives one, with "cover" (a link)."""
        source, _, id = release_id.partition(":")
        if not id.isdigit():
            raise ServiceError("Not an album of these catalogues")
        if source == "itunes":
            return await self._itunes_album(id)
        if source == "deezer":
            return await self._deezer_album(id)
        raise ServiceError("Not an album of these catalogues")

    async def _itunes_album(self, id: str) -> dict[str, Any]:
        data = await self._get(f"{ITUNES}/lookup", id=id, entity="song", limit=200)
        results = (data or {}).get("results") or []
        album = next((r for r in results if r.get("wrapperType") == "collection"), None)
        if not album:
            raise ServiceError("Apple Music doesn't have that album")
        songs = sorted((r for r in results if r.get("wrapperType") == "track"),
                       key=lambda r: (r.get("discNumber") or 1, r.get("trackNumber") or 0))
        return {"id": f"itunes:{id}", "title": album.get("collectionName", ""), "artist": album.get("artistName", ""),
                "year": _year(album.get("releaseDate")),
                "genres": [album["primaryGenreName"]] if album.get("primaryGenreName") else [],
                "label": "", "group": "", "discs": max([r.get("discCount") or 1 for r in songs] or [1]),
                "tracks": [{"disc": r.get("discNumber") or 1, "track": r.get("trackNumber") or 0,
                            "title": r.get("trackName", ""), "artist": r.get("artistName", "")} for r in songs],
                "cover": _big_itunes_art(album.get("artworkUrl100", ""))}

    async def _deezer_album(self, id: str) -> dict[str, Any]:
        album = await self._get(f"{DEEZER}/album/{id}")
        if not album or album.get("error"):
            raise ServiceError("Deezer doesn't have that album")
        tracks = (await self._get(f"{DEEZER}/album/{id}/tracks", limit=500) or {}).get("data") or []
        return {"id": f"deezer:{id}", "title": album.get("title", ""), "artist": (album.get("artist") or {}).get("name", ""),
                "year": _year(album.get("release_date")),
                "genres": [g["name"] for g in (album.get("genres") or {}).get("data") or [] if g.get("name")][:3],
                "label": album.get("label") or "", "group": "",
                "discs": max([t.get("disk_number") or 1 for t in tracks] or [1]),
                "tracks": [{"disc": t.get("disk_number") or 1, "track": t.get("track_position") or 0,
                            "title": t.get("title", ""), "artist": (t.get("artist") or {}).get("name", "")}
                           for t in sorted(tracks, key=lambda t: (t.get("disk_number") or 1, t.get("track_position") or 0))],
                "cover": album.get("cover_xl") or ""}

    # --- covers ---

    async def cover(self, release: dict[str, Any]) -> Optional[tuple[bytes, str]]:
        """The cover of an album from release()."""
        return await download(self.session, release["cover"], self.headers) if release.get("cover") else None

    async def find_cover(self, artist: str, album: str) -> Optional[tuple[bytes, str]]:
        """A cover for an album by its name, Apple's first (the largest): only an album of exactly that
        title, by that artist when there is one."""
        for r in sorted(await self.search(artist, album), key=lambda r: r["source"] != SOURCES["itunes"]):
            if plain(r["title"]) != plain(album) or (artist and plain(r["artist"]) != plain(artist)):
                continue
            try:
                release = await self.release(r["id"])
            except ServiceError:
                continue
            picture = await self.cover(release)
            if picture:
                return picture
        return None
