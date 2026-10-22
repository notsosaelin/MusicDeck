"""
The Internet Archive's Live Music Archive (archive.org/details/etree): over 250,000 concert
recordings that bands allow to be shared, free and without an account.

Each band is an archive.org collection inside "etree"; each show is an item whose files include
ready-made MP3s with titles and lengths. Search and lists use advancedsearch.php, a show's tracks
come from /metadata/<id>, audio from /download/<id>/<file> (which redirects to a storage server).

Ids: a band is "a:<collection>", a show "s:<item>", a track "t:<item>/<file name>". Art ids are
archive.org identifiers, for its thumbnails: shows and tracks use their band's (a logo or photo), as
a show's own is usually a generated waveform.
"""
import asyncio
import random
import re
from datetime import date
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from .. import models
from ..cache import Cache
from ..net import tls
from .base import MusicService, ServiceError, StreamSource

SEARCH = "https://archive.org/advancedsearch.php"
METADATA = "https://archive.org/metadata/{id}"
DOWNLOAD = "https://archive.org/download/{id}/{name}"
THUMBNAIL = "https://archive.org/services/img/{id}"
USER_AGENT = "MusicDeck/0.1.0"
SHOWS = "collection:etree AND mediatype:etree"
BANDS = "collection:etree AND mediatype:collection"
# the file kinds tried for a show's tracks, best for streaming first (FLAC only when there's nothing else)
FORMATS = ["VBR MP3", "MP3", "64Kbps MP3", "Ogg Vorbis", "Flac"]
METADATA_TTL = 3600
LIST_TTL = 30 * 60
KEPT = 200  # shows and lists kept in memory
# archive.org identifiers: letters, digits, dots, dashes, underscores; starting with a letter or digit
# (so ".." can't climb out of /download/<item>/)
SAFE_ID = re.compile(r"^[A-Za-z0-9][\w.\-]*$")
# a band's page lists its latest shows (the Grateful Dead have over 18,000)
BAND_SHOWS = 300
RADIO_SHOWS = 3
RADIO_SIZE = 25


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls()),
        timeout=aiohttp.ClientTimeout(total=25), headers={"User-Agent": USER_AGENT})


def _length(value: Any) -> float:
    """File lengths come as seconds ("512.3") or clock time ("08:30", "1:02:03")."""
    try:
        parts = [float(p) for p in str(value or "").split(":")]
    except ValueError:
        return 0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _item_id(value: str) -> str:
    if not SAFE_ID.match(value or ""):
        raise ServiceError("That isn't a Live Music Archive show")
    return value


class LiveMusicService(MusicService):
    type = "livemusic"
    display_name = "Live Music Archive"
    features = frozenset({"radio"})
    downloadable = True

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.shows: Cache[dict[str, Any]] = Cache(METADATA_TTL, KEPT)
        self.lists: Cache[list[dict[str, Any]]] = Cache(LIST_TTL, KEPT)
        # how many results each list query had in all (a page shows the first `rows`)
        self.totals: Cache[int] = Cache(LIST_TTL, KEPT)

    async def start(self) -> None:
        self.session = _session()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    # --- archive.org ---

    async def _json(self, url: str, params: Optional[list[tuple[str, Any]]] = None) -> Any:
        assert self.session
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status >= 400:
                    raise ServiceError(f"archive.org returned HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise ServiceError("Couldn't reach archive.org") from e

    async def _search(self, query: str, sort: str, rows: int, cache: bool = True) -> list[dict[str, Any]]:
        key = f"{query}|{sort}|{rows}"
        hit = self.lists.get(key)
        if cache and hit is not None:
            return hit
        fields = ["identifier", "title", "creator", "date", "venue", "coverage", "item_count", "collection"]
        params = [("q", query), ("sort[]", sort), ("rows", rows), ("output", "json")] + [("fl[]", f) for f in fields]
        data = await self._json(SEARCH, params)
        docs = (data.get("response") or {}).get("docs") or []
        self.totals.put(key, (data.get("response") or {}).get("numFound") or len(docs))
        return self.lists.put(key, docs)

    async def _metadata(self, item: str) -> dict[str, Any]:
        hit = self.shows.get(item)
        if hit:
            return hit
        data = await self._json(METADATA.format(id=item))
        if not data or not data.get("metadata"):
            raise ServiceError("This show isn't on archive.org any more")
        return self.shows.put(item, data)

    # --- mapping ---

    @staticmethod
    def _text(value: Any) -> str:
        return ", ".join(value) if isinstance(value, list) else str(value or "")

    @staticmethod
    def _band_of(collections: Any) -> Optional[str]:
        """The band collection a show belongs to (the others are archive.org's own groupings)."""
        listed = collections if isinstance(collections, list) else [collections] if collections else []
        return next((c for c in listed if isinstance(c, str) and c not in ("etree", "stream_only")
                     and SAFE_ID.match(c)), None)

    def _show(self, doc: dict[str, Any]) -> dict[str, Any]:
        """A show as an album: the date and place as its subtitle (the title already names the band)."""
        when = self._text(doc.get("date"))[:10]
        place = self._text(doc.get("venue")) or self._text(doc.get("coverage"))
        return models.collection(f"s:{doc['identifier']}", "album", self._text(doc.get("title")) or doc["identifier"],
                                 " · ".join(x for x in (when, place) if x),
                                 self._band_of(doc.get("collection")) or doc["identifier"])

    def _band(self, doc: dict[str, Any]) -> dict[str, Any]:
        count = doc.get("item_count")
        return models.collection(f"a:{doc['identifier']}", "artist", self._text(doc.get("title")) or doc["identifier"],
                                 models.count(int(count), "show") if isinstance(count, (int, float)) else "",
                                 doc["identifier"])

    def _tracks(self, item: str, data: dict[str, Any]) -> list[dict[str, Any]]:
        meta = data["metadata"]
        files = data.get("files") or []
        chosen = next((fmt for fmt in FORMATS if any(f.get("format") == fmt for f in files)), None)
        audio = [f for f in files if chosen and f.get("format") == chosen]

        def order(f: dict[str, Any]) -> tuple:
            # names like gd85-06-30d1t01.mp3 sort by disc and track; the track field breaks ties
            return (f.get("name", ""), int(f["track"]) if str(f.get("track", "")).isdigit() else 0)
        band = self._text(meta.get("creator"))
        album = self._text(meta.get("title")) or item
        band_id = self._band_of(meta.get("collection"))
        return [models.track(f"t:{item}/{f['name']}", self._text(f.get("title")) or f["name"].rsplit(".", 1)[0],
                             band, album, _length(f.get("length")), band_id or item, album_id=f"s:{item}",
                             artist_id=f"a:{band_id}" if band_id else None)
                for f in sorted(audio, key=order)]

    # --- browsing ---

    async def test(self) -> str:
        bands = await self._search(BANDS, "downloads desc", 1, cache=False)
        return "Connected to the Live Music Archive" if bands else "archive.org answered, but found no bands"

    async def home(self) -> list[dict[str, Any]]:
        today = date.today()
        jobs = {
            "today": self._search(f'{SHOWS} AND title:"{today:%m-%d}"', "downloads desc", 20),
            "trending": self._search(SHOWS, "week desc", 20),
            "bands": self._search(BANDS, "downloads desc", 30),
            "new": self._search(SHOWS, "addeddate desc", 20),
        }
        got = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
        sections = []
        if isinstance(got["today"], list) and got["today"]:
            # the title search also matches other dates with those numbers; keep the shows from this day
            shows = [d for d in got["today"] if self._text(d.get("date"))[5:10] == f"{today:%m-%d}"]
            if shows:
                sections.append(models.section("On this day", [self._show(d) for d in shows]))
        if isinstance(got["trending"], list):
            sections.append(models.section("Popular this week", [self._show(d) for d in got["trending"]]))
        if isinstance(got["bands"], list):
            sections.append(models.section("Bands", [self._band(d) for d in got["bands"]]))
        if isinstance(got["new"], list):
            sections.append(models.section("Just added", [self._show(d) for d in got["new"]]))
        if not sections:
            raise next(v for v in got.values() if isinstance(v, Exception))
        return sections

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if id.startswith("s:"):
            item = _item_id(id[2:])
            data = await self._metadata(item)
            tracks = self._tracks(item, data)
            meta = data["metadata"]
            subtitle = " · ".join(x for x in (self._text(meta.get("date"))[:10],
                                              self._text(meta.get("venue")) or self._text(meta.get("coverage"))) if x)
            return models.page(self._text(meta.get("title")) or item, subtitle,
                               self._band_of(meta.get("collection")) or item, tracks=tracks,
                               notice=None if tracks else "This recording has no files that can be streamed")
        if id.startswith("a:"):
            band = _item_id(id[2:])
            query = f"collection:{band} AND mediatype:etree"
            about, shows = await asyncio.gather(self._metadata(band), self._search(query, "date desc", BAND_SHOWS))
            total = self.totals.get(f"{query}|date desc|{BAND_SHOWS}", len(shows))
            subtitle = models.count(total, "show") + (f" · the latest {len(shows)}" if total > len(shows) else "")
            return models.page(self._text(about["metadata"].get("title")) or band, subtitle, band,
                               [self._show(d) for d in shows])
        raise ServiceError(f"Unknown collection: {kind} {id}")

    async def search(self, query: str) -> dict[str, Any]:
        # a phrase search on the words given, so punctuation in them can't break the query
        words = " ".join(re.findall(r"[\w'-]+", query))
        if not words:
            return models.page(f"Results for “{query}”")
        bands, shows = await asyncio.gather(
            self._search(f'{BANDS} AND title:({words})', "downloads desc", 10, cache=False),
            self._search(f"{SHOWS} AND ({words})", "downloads desc", 40, cache=False),
            return_exceptions=True)
        if isinstance(shows, Exception):
            raise shows
        collections = ([self._band(d) for d in bands] if isinstance(bands, list) else []) + [self._show(d) for d in shows]
        return models.page(f"Results for “{query}”", collections=collections)

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        """More by the same band: tracks from a few of its other popular shows, shuffled."""
        band = (track.get("artist_id") or "")[2:]
        if not band or not SAFE_ID.match(band):
            return []
        shows = await self._search(f"collection:{band} AND mediatype:etree", "downloads desc", 50)
        others = [d for d in shows if f"s:{d['identifier']}" != track.get("album_id")]
        picks = random.sample(others, min(RADIO_SHOWS, len(others)))
        found = await asyncio.gather(*[self._metadata(d["identifier"]) for d in picks], return_exceptions=True)
        tracks = [t for d, data in zip(picks, found) if isinstance(data, dict) for t in self._tracks(d["identifier"], data)]
        random.shuffle(tracks)
        return tracks[:RADIO_SIZE]

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        item, _, name = track_id[2:].partition("/") if track_id.startswith("t:") else ("", "", "")
        if not name or not SAFE_ID.match(item) or ".." in name:
            raise ServiceError("Unknown track")
        return StreamSource(url=DOWNLOAD.format(id=item, name=quote(name)), headers={"User-Agent": USER_AGENT})

    async def art(self, art_id: str) -> StreamSource:
        return StreamSource(url=THUMBNAIL.format(id=_item_id(art_id)), headers={"User-Agent": USER_AGENT})
