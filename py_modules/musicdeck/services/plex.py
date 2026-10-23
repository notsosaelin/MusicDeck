"""
Plex Media Server music libraries, through the server's own API (the one Plex's apps use).

Sign-in is a Plex link code: MusicDeck shows four characters, the user enters them at plex.tv/link
on a phone or computer already signed in to Plex (or approves on Plex's page in Steam's browser),
and plex.tv hands over an account token. The account's servers come from plex.tv: the first one
the account owns is used unless another is picked, at the first address that answers (a typed
address wins). A server that lets this network in without signing in (an unclaimed server, or one
listing it under "allowed without auth") works with just its address.

Audio is the original file, with byte ranges; codecs Steam's browser engine can't decode are
transcoded to MP3 by the server. The server's token travels in headers the stream server adds,
never in URLs the UI sees.

Ids are Plex rating keys (digits); art ids are the server's image paths (/library/...).
"""
import asyncio
import platform
import random
import re
import secrets as pysecrets
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode

import aiohttp

from .. import debuglog, lyrics, models
from ..net import tls
from .base import MusicService, ServiceError, SignIn, StreamSource, rows_or_tiles

PRODUCT = "MusicDeck"
VERSION = "0.1.0"
PLEX_TV = "https://plex.tv/api/v2"
RESOURCES = "https://clients.plex.tv/api/v2/resources?includeHttps=1&includeRelay=1"
LINK_PAGE = "https://plex.tv/link"
APPROVE_PAGE = "https://app.plex.tv/auth/#!?"
LIBRARY = "com.plexapp.plugins.library"
# what Steam's browser engine decodes; anything else is transcoded to MP3 by the server
PLAYABLE = {"mp3", "aac", "flac", "opus", "vorbis", "pcm", "wav"}
TRACK, ALBUM, ARTIST = 10, 9, 8  # Plex's metadata type numbers
LIST_LIMIT = 500
RADIO_SIZE = 50
# star ratings are 0-10 (two per star): four stars or more counts as a favourite
FAVOURITE_RATING = 8
LINK_TIMEOUT = 15 * 60
CONNECT_TIMEOUT = 5
SERVER_LIST_TTL = 10 * 60
RATING_KEY = re.compile(r"^\d+$")
ART_PATH = re.compile(r"^/(library|playlists)/[\w/.\-]+$")


def _session(timeout: float = 20) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls()),
        timeout=aiohttp.ClientTimeout(total=timeout))


def _normalise_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url  # Plex servers answer plain HTTP on their own network
    return url


def _headers(client_id: str, token: str = "") -> dict[str, str]:
    """Plex identifies every client by these (and lists it under Devices). The transcoder also
    needs a platform it knows; Chrome's codecs are what Steam's browser engine plays."""
    headers = {"Accept": "application/json", "X-Plex-Product": PRODUCT, "X-Plex-Version": VERSION,
               "X-Plex-Client-Identifier": client_id, "X-Plex-Platform": "Chrome",
               "X-Plex-Device": "Steam Deck", "X-Plex-Device-Name": platform.node() or "Steam Deck"}
    if token:
        headers["X-Plex-Token"] = token
    return headers


async def _call(session: aiohttp.ClientSession, method: str, url: str, headers: dict[str, str],
                **kwargs: Any) -> Any:
    try:
        async with session.request(method, url, headers=headers, **kwargs) as resp:
            if resp.status == 401:
                raise ServiceError("Plex didn't accept the sign-in; edit the source and sign in again")
            if resp.status == 404:
                raise ServiceError("Not found on the Plex server")
            if resp.status >= 400:
                raise ServiceError(f"Plex returned HTTP {resp.status}")
            if "json" not in resp.headers.get("Content-Type", ""):
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise ServiceError("Couldn't reach Plex") from e


def _page(size: int) -> dict[str, int]:
    """The first `size` results (Plex ignores a size without a start)."""
    return {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": size}


def _rating_key(id: str) -> str:
    if not RATING_KEY.match(id or ""):
        raise ServiceError("That isn't a Plex item")
    return id


async def _servers(token: str, client_id: str) -> list[dict[str, Any]]:
    """The Plex Media Servers this account can use (its own first), from plex.tv."""
    async with _session() as session:
        found = await _call(session, "GET", RESOURCES, _headers(client_id, token)) or []
    servers = [r for r in found if "server" in (r.get("provides") or "").split(",")]
    return sorted(servers, key=lambda r: not r.get("owned"))


def _addresses(server: dict[str, Any]) -> list[str]:
    """Where a server might answer, best first: on this network, then over the internet, then
    through Plex's relay. Local addresses are also tried over plain HTTP, for routers that block
    the plex.direct names Plex's HTTPS addresses use."""
    connections = server.get("connections") or []
    ranked = sorted(connections, key=lambda c: (bool(c.get("relay")), not c.get("local")))
    urls: list[str] = []
    for c in ranked:
        urls.append(c.get("uri", "").rstrip("/"))
        if c.get("local") and c.get("address") and c.get("port"):
            urls.append(f"http://{c['address']}:{c['port']}")
    return [u for u in dict.fromkeys(urls) if u]


async def _first_answering(urls: list[str], headers: dict[str, str]) -> Optional[str]:
    """The best-ranked address that answers, asking all of them at once."""
    async def answers(url: str) -> bool:
        try:
            async with _session(CONNECT_TIMEOUT) as session:
                await _call(session, "GET", f"{url}/identity", headers)
            return True
        except ServiceError:
            return False
    results = await asyncio.gather(*[answers(u) for u in urls])
    return next((u for u, ok in zip(urls, results) if ok), None)


class PlexService(MusicService):
    """config: {"url" (typed, optional), "client_id", "token" (plex.tv), "username", "user_id",
    "server", "server_name", "servers" (for the dialog's picker), "server_url", "server_token"}"""

    type = "plex"
    display_name = "Plex"
    features = frozenset({"star", "scrobble", "lyrics", "radio", "playlists"})
    downloadable = True

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        who = config.get("user_id") or config.get("server_url")
        return f"{who}|{config.get('server')}" if who else None

    @classmethod
    def sign_in(cls, options: dict[str, Any], runtime_dir: str) -> Optional[SignIn]:
        return PlexLink(options.get("client_id") or "", browser=bool(options.get("browser")))

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """Finds the server to use: the one picked (or the account's own) at the typed address
        or the first of plex.tv's addresses that answers. Without a sign-in, the typed address
        must let MusicDeck in by itself."""
        previous = previous or {}
        client_id = config.get("client_id") or previous.get("client_id") or pysecrets.token_hex(12)
        token = config.get("token") or previous.get("token") or ""
        typed = _normalise_url(config.get("url") or "")
        result: dict[str, Any] = {"url": typed, "client_id": client_id}
        if token:
            async with _session() as session:
                user = await _call(session, "GET", f"{PLEX_TV}/user", _headers(client_id, token))
            servers = await _servers(token, client_id)
            if not servers and not typed:
                raise ServiceError("This Plex account has no servers; enter your server's address")
            wanted = config.get("server") or previous.get("server")
            server = next((s for s in servers if s.get("clientIdentifier") == wanted), servers[0] if servers else {})
            server_token = server.get("accessToken") or token
            result.update(token=token, username=user.get("username") or user.get("title") or "",
                          user_id=user.get("uuid") or str(user.get("id") or ""),
                          servers=[{"id": s.get("clientIdentifier"), "name": s.get("name", "Plex")} for s in servers])
            urls = [typed] if typed else _addresses(server)
        else:
            if not typed:
                raise ServiceError("Sign in with Plex, or enter the address of a server that doesn't need it")
            server, server_token, urls = {}, "", [typed]
        url = await _first_answering(urls, _headers(client_id, server_token))
        if not url:
            name = server.get("name") or "the server"
            raise ServiceError(f"Couldn't reach {name}; check it's running, or enter its address")
        async with _session() as session:
            identity = await _call(session, "GET", f"{url}/identity", _headers(client_id, server_token))
            # /identity answers anyone; the library tells whether this sign-in (or none) is let in
            try:
                await _call(session, "GET", f"{url}/library/sections", _headers(client_id, server_token))
            except ServiceError as e:
                if not token and "didn't accept" in str(e):
                    raise ServiceError("This server needs you to sign in with Plex") from e
                raise
        machine = (identity or {}).get("MediaContainer", {}).get("machineIdentifier", "")
        result.update(server=machine, server_url=url, server_name=server.get("name") or "Plex")
        if server_token:  # none for a server that lets this network in by itself
            result["server_token"] = server_token
        return result

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.sections: Optional[list[str]] = None

    async def start(self) -> None:
        self.session = _session()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    # --- API ---

    def _auth(self) -> dict[str, str]:
        return _headers(self.config.get("client_id", ""), self.config.get("server_token", ""))

    async def _request(self, method: str, path: str, **params: Any) -> Any:
        assert self.session
        return await _call(self.session, method, self.config["server_url"] + path, self._auth(),
                           params={k: str(v) for k, v in params.items() if v is not None})

    async def _metadata(self, path: str, **params: Any) -> list[dict[str, Any]]:
        data = await self._request("GET", path, **params)
        return ((data or {}).get("MediaContainer") or {}).get("Metadata") or []

    async def _music_sections(self) -> list[str]:
        if self.sections is None:
            data = await self._request("GET", "/library/sections")
            found = ((data or {}).get("MediaContainer") or {}).get("Directory") or []
            self.sections = [d["key"] for d in found if d.get("type") == "artist"]
        return self.sections

    async def _all(self, type: int, limit: int = LIST_LIMIT, **filters: Any) -> list[dict[str, Any]]:
        """Items of one type from every music library (most people have one)."""
        sections = await self._music_sections()
        found = await asyncio.gather(*[
            self._metadata(f"/library/sections/{s}/all", type=type, **filters, **_page(limit))
            for s in sections])
        return [item for items in found for item in items][:limit]

    async def test(self) -> str:
        sections = await self._music_sections()
        who = self.config.get("username")
        where = self.config.get("server_name") or "Plex"
        if not sections:
            return f"Connected to {where}, but it has no music libraries"
        return f"Signed in as {who} · {where}" if who else f"Connected to {where}"

    # --- mapping ---

    @staticmethod
    def _art(item: dict[str, Any]) -> Optional[str]:
        art = item.get("thumb") or item.get("parentThumb") or item.get("grandparentThumb") or item.get("composite")
        return art if art and ART_PATH.match(art) else None

    @staticmethod
    def _gain(item: dict[str, Any]) -> Optional[dict[str, float]]:
        """Loudness from Plex's analysis (Settings → Library → loudness analysis), when it has run."""
        for media in item.get("Media") or []:
            for part in media.get("Part") or []:
                for stream in part.get("Stream") or []:
                    if stream.get("streamType") == 2 and stream.get("gain") is not None:
                        return models.gain(stream.get("gain"), stream.get("albumGain"),
                                           stream.get("peak"), stream.get("albumPeak"))
        return None

    def _track(self, t: dict[str, Any]) -> dict[str, Any]:
        return models.track(
            t["ratingKey"], t.get("title", ""), t.get("originalTitle") or t.get("grandparentTitle", ""),
            t.get("parentTitle", ""), (t.get("duration") or 0) / 1000, art=self._art(t),
            album_id=t.get("parentRatingKey"), artist_id=t.get("grandparentRatingKey"),
            starred=(t.get("userRating") or 0) >= FAVOURITE_RATING, gain=self._gain(t))

    def _album(self, a: dict[str, Any]) -> dict[str, Any]:
        sub = " · ".join(str(x) for x in [a.get("parentTitle"), a.get("year")] if x)
        return models.collection(a["ratingKey"], "album", a.get("title", ""), sub, self._art(a))

    def _artist(self, a: dict[str, Any]) -> dict[str, Any]:
        return models.collection(a["ratingKey"], "artist", a.get("title", ""), "Artist", self._art(a))

    def _playlist(self, p: dict[str, Any]) -> dict[str, Any]:
        count = p.get("leafCount")
        return models.collection(p["ratingKey"], "playlist", p.get("title", ""),
                                 models.count(count, "track") if count is not None else "Playlist", self._art(p))

    def _tracks(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._track(t) for t in items if t.get("type") == "track"]

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        jobs = {
            "playlists": self._metadata("/playlists", playlistType="audio"),
            # Plex's ">>=" is "more than"
            "recent": self._all(TRACK, 30, sort="lastViewedAt:desc", **{"viewCount>>": 0}),
            "added": self._all(ALBUM, 20, sort="addedAt:desc"),
            "frequent": self._all(ALBUM, 20, sort="viewCount:desc", **{"viewCount>>": 0}),
            "favourites": self._all(ALBUM, 50, sort="titleSort", **{"userRating>>": FAVOURITE_RATING - 1}),
        }
        got = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
        if all(isinstance(v, Exception) for v in got.values()):
            raise next(iter(got.values()))  # type: ignore[misc]
        ok = {k: v for k, v in got.items() if isinstance(v, list)}
        sections = []
        if ok.get("playlists"):
            sections.append(models.section("Playlists", [self._playlist(p) for p in ok["playlists"]]))
        if ok.get("recent"):
            sections.append(models.section("Recently played", [], self._tracks(ok["recent"])))
        for key, title in [("added", "Recently added"), ("frequent", "Most played"), ("favourites", "Favourite albums")]:
            if ok.get(key):
                sections.append(models.section(title, [self._album(a) for a in ok[key]]))
        sections.append(models.section("Library", [
            models.collection("favourite-tracks", "list", "Favourite tracks"),
            models.collection("albums", "list", "All albums"),
            models.collection("artists", "list", "Artists"),
            models.collection("random", "list", "Random albums"),
        ]))
        return sections

    # --- media center ---

    TABS = (("home", "Home"), ("albums", "Albums"), ("artists", "Artists"), ("genres", "Genres"),
            ("library", "Library"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            return [s for s in await self.home() if s["title"] != "Playlists" and not models.is_categories(s)]
        if tab_id == "albums":
            lists = [("Recently added", "addedAt:desc"), ("Most played", "viewCount:desc"), ("Random", "random")]
            found = await asyncio.gather(*[self._all(ALBUM, 30, sort=sort) for _, sort in lists],
                                         self._all(ALBUM, sort="titleSort"), return_exceptions=True)
            sections = [models.section(title, [self._album(a) for a in albums])
                        for (title, _), albums in zip(lists, found) if isinstance(albums, list)]
            if isinstance(found[-1], list):
                sections.append(models.section("All albums", [self._album(a) for a in found[-1]], display="grid"))
            return sections
        if tab_id == "artists":
            return [models.section("Artists", (await self.open("list", "artists"))["collections"], display="grid")]
        if tab_id == "genres":
            found = await asyncio.gather(*[self._request("GET", f"/library/sections/{s}/genre", type=ALBUM)
                                           for s in await self._music_sections()], return_exceptions=True)
            seen: dict[str, str] = {}
            for data in found:
                if isinstance(data, dict):
                    for d in (data.get("MediaContainer") or {}).get("Directory") or []:
                        if str(d.get("key", "")).isdigit() and d.get("title"):
                            seen.setdefault(d["title"], d["key"])
            genres = [models.collection(f"genre:{key}#{title}", "list", title) for title, key in sorted(seen.items())]
            return [rows_or_tiles(models.section("Genres", genres))]
        if tab_id == "library":
            playlists, albums, tracks = await asyncio.gather(
                self._metadata("/playlists", playlistType="audio"),
                self._all(ALBUM, sort="titleSort", **{"userRating>>": FAVOURITE_RATING - 1}),
                self.open("list", "favourite-tracks"), return_exceptions=True)
            ok = lambda r: r if isinstance(r, list) else []  # noqa: E731
            return [models.section("Playlists", [self._playlist(p) for p in ok(playlists)], display="grid", mine=True),
                    models.section("Favourite albums", [self._album(a) for a in ok(albums)], display="grid"),
                    models.section("Favourite tracks", [], tracks["tracks"] if isinstance(tracks, dict) else [],
                                   display="grid")]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("genre:"):
            genre, _, name = id[6:].partition("#")
            if not genre.isdigit():
                raise ServiceError("Unknown genre")
            albums = await self._all(ALBUM, sort="titleSort", genre=genre)
            return models.page(name or "Genre", models.count(len(albums), "album"),
                               collections=[self._album(a) for a in albums])
        if kind == "list":
            if id == "favourite-tracks":
                tracks = self._tracks(await self._all(TRACK, sort="titleSort", **{"userRating>>": FAVOURITE_RATING - 1}))
                return models.page("Favourite tracks", models.count(len(tracks), "track"), tracks=tracks)
            if id == "artists":
                return models.page("Artists", collections=[self._artist(a) for a in await self._all(ARTIST, sort="titleSort")])
            if id in ("albums", "random"):
                albums = await self._all(ALBUM, sort="random" if id == "random" else "titleSort")
                return models.page("Random albums" if id == "random" else "All albums",
                                   collections=[self._album(a) for a in albums])
            raise ServiceError(f"Unknown list: {id}")
        key = _rating_key(id)
        if kind == "album":
            album = await self._of_type(key, "album")
            tracks = await self._metadata(f"/library/metadata/{key}/children")
            c = self._album(album)
            return models.page(c["title"], c["subtitle"], c["art"], tracks=self._tracks(tracks))
        if kind == "artist":
            artist = await self._of_type(key, "artist")
            albums = await self._metadata(f"/library/metadata/{key}/children")
            section = artist.get("librarySectionID")
            top = await self._metadata(f"/library/sections/{section}/all", type=TRACK, sort="viewCount:desc",
                                       **{"artist.id": key, **_page(10)}) if section else []
            c = self._artist(artist)
            albums = [a for a in albums if a.get("type") == "album"]
            return models.page(c["title"], models.count(len(albums), "album"), c["art"],
                               collections=[self._album(a) for a in albums], tracks=self._tracks(top))
        if kind == "playlist":
            playlist = await self._of_type(key, "playlist")
            tracks = self._tracks(await self._metadata(f"/playlists/{key}/items"))
            c = self._playlist({**playlist, "leafCount": len(tracks)})
            return models.page(c["title"], c["subtitle"], c["art"], tracks=tracks)
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        # the libraries' title filters (every word, any order) rather than /hubs/search, whose
        # fuzzy matching answers even nonsense with some track
        tracks, albums, artists, playlists = await asyncio.gather(
            self._all(TRACK, 50, title=query), self._all(ALBUM, 20, title=query),
            self._all(ARTIST, 10, title=query), self._metadata("/playlists", playlistType="audio"))
        words = query.lower().split()
        playlists = [p for p in playlists if all(w in p.get("title", "").lower() for w in words)]
        return models.page(f"Results for “{query}”",
                           collections=[self._artist(a) for a in artists] + [self._album(a) for a in albums]
                           + [self._playlist(p) for p in playlists],
                           tracks=self._tracks(tracks), tracks_first=True)

    # --- media ---

    async def _of_type(self, key: str, type: str) -> dict[str, Any]:
        """One item, which must be of the kind asked for (a track id opened as an album is not found)."""
        found = await self._metadata(f"/library/metadata/{_rating_key(key)}")
        if not found or found[0].get("type") != type:
            raise ServiceError("Not found on the Plex server")
        return found[0]

    async def _item(self, track_id: str) -> dict[str, Any]:
        try:
            return await self._of_type(track_id, "track")
        except ServiceError:
            raise ServiceError("Unknown track") from None

    async def stream(self, track_id: str) -> StreamSource:
        item = await self._item(track_id)
        media = (item.get("Media") or [{}])[0]
        part = (media.get("Part") or [{}])[0]
        if (media.get("audioCodec") or "").lower() in PLAYABLE and part.get("key"):
            url = self.config["server_url"] + part["key"]
        else:
            params = {"path": f"/library/metadata/{track_id}", "mediaIndex": 0, "partIndex": 0, "protocol": "http",
                      "directPlay": 0, "directStream": 0, "musicBitrate": 320,
                      # one transcode per stream: Plex ends a session's old transcode when it starts again
                      "session": pysecrets.token_hex(8)}
            url = f"{self.config['server_url']}/music/:/transcode/universal/start?{urlencode(params)}"
        return StreamSource(url=url, headers=self._auth(), gain=self._gain(item))

    async def art(self, art_id: str) -> StreamSource:
        if not ART_PATH.match(art_id or "") or ".." in art_id:
            raise ServiceError("Unknown image")
        query = urlencode({"width": 300, "height": 300, "minSize": 1, "upscale": 1, "url": art_id})
        return StreamSource(url=f"{self.config['server_url']}/photo/:/transcode?{query}", headers=self._auth())

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        """Favourite = five stars; taking it back clears the rating."""
        await self._request("PUT", "/:/rate", key=_rating_key(track_id), identifier=LIBRARY,
                            rating=10 if starred else -1)

    async def scrobble(self, track_id: str, submission: bool) -> None:
        """Now playing shows on the server's dashboard; a finished listen counts a play, which
        feeds "Recently played" and "Most played" in every Plex app."""
        key = _rating_key(track_id)
        if submission:
            await self._request("GET", "/:/scrobble", key=key, identifier=LIBRARY)
        else:
            await self._request("GET", "/:/timeline", ratingKey=key, key=f"/library/metadata/{key}",
                                state="playing", time=0, identifier=LIBRARY)

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Lyrics Plex has for the track: a .lrc/.txt beside the file, or (with Plex Pass) its
        online provider's. Plex sends them as timed lines; older servers send the file itself."""
        try:
            item = await self._item(track["id"])
        except ServiceError:
            return None
        streams = [s for m in item.get("Media") or [] for p in m.get("Part") or [] for s in p.get("Stream") or []
                   if s.get("streamType") == 4 and s.get("key")]
        if not streams:
            return None
        best = next((s for s in streams if s.get("codec") == "lrc"), streams[0])
        assert self.session
        try:
            async with self.session.get(self.config["server_url"] + best["key"], headers=self._auth()) as resp:
                if resp.status >= 400:
                    return None
                if "json" not in resp.headers.get("Content-Type", ""):
                    return lyrics.parse_lrc(await resp.text(errors="replace"), "Plex")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None
        found = ((data or {}).get("MediaContainer") or {}).get("Lyrics") or [{}]
        timed = bool(found[0].get("timed"))
        lines = [{"t": line.get("startOffset", 0) / 1000 if timed else None,
                  "text": "".join(span.get("text", "") for span in line.get("Span") or [])}
                 for line in found[0].get("Line") or []]
        if not any(line["text"].strip() for line in lines):
            return None
        return {"synced": timed, "lines": lines, "source": "Plex"}

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        """Sonically similar tracks when the server has analysed them (Plex Pass); otherwise the
        same artist mixed with the rest of the library."""
        key = _rating_key(track["id"])
        try:
            similar = self._tracks(await self._metadata(f"/library/metadata/{key}/nearest", limit=RADIO_SIZE))
        except ServiceError:
            similar = []
        if not similar:
            artist = track.get("artist_id") or ""
            same, others = await asyncio.gather(
                self._all(TRACK, RADIO_SIZE // 2, sort="random", **({"artist.id": artist} if RATING_KEY.match(artist) else {})),
                self._all(TRACK, RADIO_SIZE // 2, sort="random"))
            similar = self._tracks(same + others)
            random.shuffle(similar)
        unique = {t["id"]: t for t in similar if t["id"] != key}
        return list(unique.values())[:RADIO_SIZE]

    async def user_playlists(self) -> list[dict[str, Any]]:
        # smart playlists fill themselves, so only ordinary ones can be added to
        found = await self._metadata("/playlists", playlistType="audio")
        return [self._playlist(p) for p in found if not p.get("smart")]

    async def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        keys = ",".join(_rating_key(t) for t in track_ids)
        uri = f"server://{self.config.get('server')}/{LIBRARY}/library/metadata/{keys}"
        await self._request("PUT", f"/playlists/{_rating_key(playlist_id)}/items", uri=uri)


class PlexLink(SignIn):
    """A Plex PIN that becomes an account token once approved: either a four-character `code` the
    user enters at plex.tv/link on another device, or (`browser`) Plex's own sign-in page, `url`,
    opened in Steam's browser. The token belongs to the client id it was asked for, so that is kept
    with it."""

    def __init__(self, client_id: str, browser: bool = False):
        super().__init__()
        self.client_id = client_id or pysecrets.token_hex(12)
        self.browser = browser
        self._pin = 0
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        # the page takes Plex's long ("strong") codes; plex.tv/link takes the short ones
        async with _session() as session:
            pin = await _call(session, "POST", f"{PLEX_TV}/pins", _headers(self.client_id),
                              params={"strong": "true" if self.browser else "false"})
        self._pin = pin["id"]
        if self.browser:
            self.url = APPROVE_PAGE + urlencode({"clientID": self.client_id, "code": pin["code"],
                                                 "context[device][product]": PRODUCT}, quote_via=quote)
        else:
            self.code = pin["code"]
        self.status = "waiting"
        self._task = asyncio.get_event_loop().create_task(self._poll())

    async def _poll(self) -> None:
        try:
            deadline = time.time() + LINK_TIMEOUT
            async with _session() as session:
                while True:
                    await asyncio.sleep(2)
                    if time.time() > deadline:
                        raise ServiceError("The code expired; start again")
                    pin = await _call(session, "GET", f"{PLEX_TV}/pins/{self._pin}", _headers(self.client_id))
                    if pin and pin.get("authToken"):
                        break
            self.result = {"client_id": self.client_id, "token": pin["authToken"]}
            self.status = "done"
            debuglog.debug("plex", "signed in with a link code")
        except asyncio.CancelledError:
            self.status, self.error = "error", "Sign-in cancelled"
        except ServiceError as e:
            self.status, self.error = "error", str(e)
        except Exception as e:
            self.status, self.error = "error", f"Sign-in failed: {e}"

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
