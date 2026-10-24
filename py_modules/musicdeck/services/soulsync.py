"""
SoulSync (github.com/nezreka/soulsync), the self-hosted music server that fills a library from
Soulseek, TIDAL, Qobuz and more. MusicDeck plays the library of a SoulSync the user already runs.

* Sign-in: the server's address and an API key made in SoulSync (Settings > Advanced > SoulSync API),
  sent as `Authorization: Bearer sk_...`. It has to be an admin key: a read-only key only reaches
  /api/v1, and the audio isn't there. The key is checked on save, and stored like other sources'
  tokens (never shown, never in backups).
* Browsing: the REST API at /api/v1 (library artists, albums, tracks, recently added; `{"success",
  "data", "error", "pagination"}`). SoulSync allows 60 requests a minute from one address, so answers
  are kept a little while and pages ask for big pages rather than many.
* Audio: the route SoulSync's own web player uses, /stream/library-audio?path=<file>&track_id=<id>,
  with byte ranges. It serves only files inside SoulSync's library folders (and proxies Navidrome's
  stream for a library kept there). It isn't part of the documented API, so a SoulSync update could
  change it.
* Art: albums' `thumb_url`, a picture elsewhere on the web (Spotify's, AudioDB's) or a path on the
  SoulSync server, fetched with the key.
"""
import asyncio
import re
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit

import aiohttp

from .. import models
from ..cache import Cache
from ..net import tls
from .base import MusicService, ServiceError, StreamSource

API = "/api/v1"
#: SoulSync's ids are text, sometimes opaque; nothing that could climb out of a path
ID = re.compile(r"^[\w.:-]{1,128}$")
#: a picture's path on the SoulSync server
ART_PATH = re.compile(r"^/[\w/.%-]+(\?[\w=&.%-]*)?$")
CACHE_TTL = 120
PAGE = 200
MAX_ITEMS = 2000


def _list(data: Any, *keys: str) -> list[dict[str, Any]]:
    """The list in an answer's data, under the first of `keys` it has (or its only list)."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in keys:
        if isinstance(data.get(key), list):
            return [x for x in data[key] if isinstance(x, dict)]
    lists = [v for v in data.values() if isinstance(v, list)]
    return [x for x in lists[0] if isinstance(x, dict)] if len(lists) == 1 else []


def _year(value: Any) -> str:
    return str(value) if isinstance(value, int) and value > 0 else ""


class SoulSyncService(MusicService):
    """A SoulSync server's library.

    config: {"url", "token"}  (token = a SoulSync admin API key, sk_...)
    """

    type = "soulsync"
    display_name = "SoulSync"
    features: frozenset[str] = frozenset()
    downloadable = True

    TABS = (("home", "Home"), ("albums", "Albums"), ("artists", "Artists"), ("added", "Recently added"))
    TAB_GROUP_OVERRIDES = {"added": "listening"}

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        return config.get("url") or None

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache: Cache[Any] = Cache(CACHE_TTL, 200)
        # a track's file, learnt from the lists it came in (playing it needs it)
        self.files: Cache[str] = Cache(6 * 3600, 5000)

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        url = (config.get("url") or "").strip().rstrip("/")
        if url and "://" not in url:
            url = "http://" + url  # SoulSync runs at home, on port 8008, without TLS
        key = (config.get("api_key") or "").strip()
        return {"url": url, "token": key or (previous or {}).get("token", "")}

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """The key has to work, and reach the audio (an admin key)."""
        if not config.get("url"):
            raise ServiceError("Enter your SoulSync's address")
        if not config.get("token"):
            raise ServiceError("Enter an API key from SoulSync (Settings > Advanced > SoulSync API)")
        if (previous or {}).get("token") == config["token"] and (previous or {}).get("url") == config["url"]:
            return config
        headers = {"Authorization": f"Bearer {config['token']}"}
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls()),
                                             timeout=aiohttp.ClientTimeout(total=15)) as http:
                async with http.get(config["url"] + API + "/system/status", headers=headers) as resp:
                    if resp.status in (401, 403):
                        raise ServiceError("SoulSync didn't accept that API key")
                    if resp.status == 404:
                        raise ServiceError("SoulSync's API is off: turn it on in Settings > Advanced > SoulSync API")
                    if resp.status >= 400:
                        raise ServiceError(f"SoulSync returned HTTP {resp.status}")
                # without a path the stream route answers 400 to a key that may use it, 403 to a read-only one
                async with http.get(config["url"] + "/stream/library-audio", headers=headers) as resp:
                    if resp.status in (401, 403):
                        raise ServiceError("That's a read-only key: MusicDeck needs an admin key to play your music")
        except aiohttp.ClientError as e:
            raise ServiceError(f"Could not reach SoulSync: {e}") from e
        return config

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls()),
                                             timeout=aiohttp.ClientTimeout(total=20))

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.get('token', '')}"}

    async def _get(self, path: str, **params: Any) -> Any:
        """An API answer's data; cached a little while (SoulSync allows 60 requests a minute)."""
        params = {k: v for k, v in params.items() if v is not None}
        key = path + "?" + urlencode(sorted(params.items()))
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        body = await self._fetch(path, params)
        return self.cache.put(key, body.get("data") if isinstance(body, dict) else None)

    async def _fetch(self, path: str, params: dict[str, Any]) -> Any:
        if not self.config.get("url"):
            raise ServiceError("No SoulSync address set")
        assert self.session
        try:
            async with self.session.get(self.config["url"] + API + path, params=params,
                                        headers=self._headers()) as resp:
                body = await resp.json(content_type=None) if resp.content_type == "application/json" else {}
                if resp.status in (401, 403):
                    raise ServiceError("SoulSync didn't accept the API key; edit the source")
                if resp.status == 429:
                    raise ServiceError("SoulSync is limiting requests; try again in a minute")
                if resp.status == 404:
                    raise ServiceError("Not found on SoulSync")
                if resp.status >= 400 or (isinstance(body, dict) and body.get("success") is False):
                    error = (body.get("error") or {}) if isinstance(body, dict) else {}
                    raise ServiceError(error.get("message") or f"SoulSync returned HTTP {resp.status}")
                return body
        except aiohttp.ClientError as e:
            raise ServiceError(f"Could not reach SoulSync: {e}") from e

    async def _pages(self, path: str, keys: tuple[str, ...], **params: Any) -> list[dict[str, Any]]:
        """Every item of a paged list (up to MAX_ITEMS), PAGE at a time."""
        items: list[dict[str, Any]] = []
        page = 1
        while len(items) < MAX_ITEMS:
            assert self.session
            key = f"{path}#{page}?" + urlencode(sorted(params.items()))
            body = self.cache.get(key)
            if body is None:
                body = self.cache.put(key, await self._fetch(path, {**params, "page": page, "limit": PAGE}))
            batch = _list((body or {}).get("data"), *keys)
            items += batch
            more = ((body or {}).get("pagination") or {}).get("has_next")
            if not batch or not more:
                break
            page += 1
        return items

    async def test(self) -> str:
        status = await self._get("/system/status")
        version = (status or {}).get("version") if isinstance(status, dict) else None
        return f"Connected to SoulSync{' ' + version if version else ''}"

    # --- mapping ---

    def _album(self, a: dict[str, Any]) -> dict[str, Any]:
        artist = a.get("artist_name") or a.get("artist") or ""
        subtitle = " · ".join(x for x in [artist if isinstance(artist, str) else "", _year(a.get("year"))] if x)
        return models.collection(str(a["id"]), "album", a.get("title") or a.get("name") or "", subtitle,
                                 a.get("thumb_url") or None)

    @staticmethod
    def _artist(a: dict[str, Any]) -> dict[str, Any]:
        count = a.get("album_count")
        return models.collection(str(a["id"]), "artist", a.get("name", ""),
                                 models.count(count, "album") if isinstance(count, int) else "Artist",
                                 a.get("thumb_url") or None)

    def _track(self, t: dict[str, Any], art: Optional[str] = None) -> dict[str, Any]:
        tid = str(t["id"])
        if t.get("file_path"):
            self.files.put(tid, t["file_path"])
        duration = t.get("duration")
        return models.track(tid, t.get("title", ""), t.get("track_artist") or t.get("artist_name") or "",
                            t.get("album_title") or "", duration / 1000 if isinstance(duration, (int, float)) else 0,
                            art=art or t.get("thumb_url") or t.get("album_thumb_url") or None,
                            album_id=str(t["album_id"]) if t.get("album_id") else None,
                            artist_id=str(t["artist_id"]) if t.get("artist_id") else None)

    def _tracks(self, items: list[dict[str, Any]], art: Optional[str] = None) -> list[dict[str, Any]]:
        return [self._track(t, art) for t in items if t.get("id") is not None]

    # --- browsing ---

    async def _recent(self, kind: str, limit: int = 50) -> list[dict[str, Any]]:
        return _list(await self._get("/library/recently-added", type=kind, limit=limit), "items", kind)

    async def home(self) -> list[dict[str, Any]]:
        albums, tracks = await asyncio.gather(self._recent("albums", 30), self._recent("tracks", 30),
                                              return_exceptions=True)
        if isinstance(albums, Exception) and isinstance(tracks, Exception):
            raise albums
        sections = []
        if isinstance(albums, list) and albums:
            sections.append(models.section("Recently added", [self._album(a) for a in albums if a.get("id")]))
        if isinstance(tracks, list) and tracks:
            sections.append(models.section("New songs", [], self._tracks(tracks)))
        return sections or [models.notice("Your SoulSync library is empty so far")]

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "albums":
            albums = await self._pages("/library/albums", ("albums",))
            return [models.section("Albums", [self._album(a) for a in albums if a.get("id")], display="grid")]
        if tab_id == "artists":
            artists = await self._pages("/library/artists", ("artists",))
            return [models.section("Artists", [self._artist(a) for a in artists if a.get("id")], display="grid")]
        if tab_id == "added":
            albums = await self._recent("albums", 120)
            return [models.section("Recently added", [self._album(a) for a in albums if a.get("id")],
                                   display="grid")]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if not ID.match(id or ""):
            raise ServiceError("Not found on SoulSync")
        if kind == "album":
            data = await self._get(f"/library/albums/{id}")
            album = (data or {}).get("album") if isinstance(data, dict) else None
            if not isinstance(album, dict):
                raise ServiceError("Not found on SoulSync")
            tracks = _list(album, "tracks")
            if not tracks:
                tracks = _list(await self._get(f"/library/albums/{id}/tracks"), "tracks")
            tracks.sort(key=lambda t: (t.get("disc_number") or 1, t.get("track_number") or 0))
            c = self._album(album)
            return models.page(c["title"], c["subtitle"], c["art"], tracks=self._tracks(tracks, c["art"]))
        if kind == "artist":
            artist, albums = await asyncio.gather(self._get(f"/library/artists/{id}"),
                                                  self._get(f"/library/artists/{id}/albums"))
            a = (artist or {}).get("artist") if isinstance(artist, dict) else None
            if not isinstance(a, dict):
                raise ServiceError("Not found on SoulSync")
            records = sorted(_list(albums, "albums"), key=lambda x: x.get("year") or 0, reverse=True)
            name = a.get("name", "")
            return models.page(name, models.count(len(records), "album"), a.get("thumb_url") or None,
                               collections=[self._album({**x, "artist_name": x.get("artist_name") or name})
                                            for x in records if x.get("id")])
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        query = query.strip()
        artists, albums, by_title, by_artist = await asyncio.gather(
            self._get("/library/artists", search=query, limit=20),
            self._get("/library/albums", search=query, limit=40),
            self._get("/library/tracks", title=query, limit=60),
            self._get("/library/tracks", artist=query, limit=60), return_exceptions=True)
        if all(isinstance(r, Exception) for r in (artists, albums, by_title, by_artist)):
            raise next(r for r in (artists, albums, by_title, by_artist) if isinstance(r, Exception))
        ok = lambda r, *keys: [] if isinstance(r, Exception) else _list(r, *keys)  # noqa: E731
        tracks, seen = [], set()
        for t in ok(by_title, "tracks") + ok(by_artist, "tracks"):
            if t.get("id") is not None and str(t["id"]) not in seen:
                seen.add(str(t["id"]))
                tracks.append(t)
        return models.page(f"Results for “{query}”",
                           collections=[self._artist(a) for a in ok(artists, "artists") if a.get("id")]
                           + [self._album(a) for a in ok(albums, "albums") if a.get("id")],
                           tracks=self._tracks(tracks), tracks_first=True)

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        if not ID.match(track_id or ""):
            raise ServiceError("Not found on SoulSync")
        path = self.files.get(track_id)
        if not path:
            data = await self._get(f"/library/tracks/{track_id}")
            track = (data or {}).get("track") if isinstance(data, dict) else None
            path = (track or {}).get("file_path")
            if not path:
                raise ServiceError("SoulSync doesn't know where this song's file is")
            self.files.put(track_id, path)
        query = urlencode({"path": path, "track_id": track_id})
        return StreamSource(url=f"{self.config['url']}/stream/library-audio?{query}", headers=self._headers())

    async def art(self, art_id: str) -> StreamSource:
        """A cover elsewhere on the web as it is; one on the SoulSync server with the key."""
        parts = urlsplit(art_id or "")
        if parts.scheme in ("http", "https") and parts.netloc:
            own = parts.netloc == urlsplit(self.config.get("url", "")).netloc
            return StreamSource(url=art_id, headers=self._headers() if own else {})
        if ART_PATH.match(art_id or ""):
            return StreamSource(url=self.config["url"] + art_id, headers=self._headers())
        raise ServiceError("Unknown image")

