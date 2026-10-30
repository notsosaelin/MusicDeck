"""
Qobuz with the user's own subscription, through the API its web player uses (the approach of
streamrip and qobuz-dl).

* App credentials: the web player's app id and request-signing secrets are read from its JS
  bundle, validated with a signed test request, cached for a day and refreshed if Qobuz rejects
  them.
* Sign-in: Qobuz's own OAuth page, opened in Steam's browser (the approach of Qobine). The password
  only ever goes to qobuz.com; a one-shot loopback listener catches the authorization code, which is
  exchanged for a user auth token. Only the token is stored.
* Streams: `track/getFileUrl` (signed) returns a direct FLAC/MP3 link at the chosen quality, which
  the stream server proxies like any other remote file.
"""
import asyncio
import base64
import socket
import hashlib
import ipaddress
import json
import os
import re
import secrets
import time
from collections import OrderedDict
from typing import Any, Optional

import aiohttp
from aiohttp import web

from .. import debuglog, models
from ..cast import netif
from ..feeds import notes_text
from ..net import tls
from .base import MusicService, ServiceError, SignIn, StreamSource, as_rows

API = "https://www.qobuz.com/api.json/0.2"
PLAYER = "https://play.qobuz.com"
# how long what's in the library (favourite albums and artists, playlists) is trusted before asking again
LIBRARY_TTL = 300
LIBRARY_LIMIT = 500
# an album's, artist's or playlist's id as it goes into a list parameter (a comma would add another)
QOBUZ_ID = re.compile(r"^[A-Za-z0-9]{1,64}$")
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
BUNDLE_RE = re.compile(r'<script src="(/resources/\d+\.\d+\.\d+-[a-z]\d{3}/bundle\.js)"></script>')
APP_ID_RE = re.compile(r'production:\{api:\{appId:"(?P<app_id>\d{9})",appSecret:"(\w{32})')
SEED_RE = re.compile(r'[a-z]\.initialSeed\("(?P<seed>[\w=]+)",window\.utimezone\.(?P<timezone>[a-z]+)\)')
APP_TTL = 24 * 3600
# A long-lived public track Qobuz itself uses for this check in open-source clients
TEST_TRACK = "19512574"
# The web player's OAuth key, used to exchange an authorization code (as Qobine does)
OAUTH_PRIVATE_KEY = "6lz8C03UDIC7"
OAUTH_TIMEOUT = 10 * 60

QUALITIES = {5: "MP3 320", 6: "FLAC 16-bit/44.1 kHz", 7: "FLAC 24-bit/96 kHz", 27: "FLAC 24-bit/192 kHz"}
# more of Qobuz's album lists, for the media center's Discover tab
#: Albums asked for at a time for a genre's page (most streamed three of these deep, then what's new)
GENRE_PAGE = 100
DISCOVER_FEATURED = [("press-awards", "Press awards"), ("best-sellers", "Best sellers"),
                     ("ideal-discography", "Ideal discography"), ("most-featured", "Most featured")]
FEATURED = [("new-releases-full", "New releases"), ("qobuzissims", "Qobuzissimes"),
            ("editor-picks", "Editor's picks"), ("most-streamed", "Most streamed")]
#: Qobuz's Discover page (discover/index), its containers in its own order, as Home's rows
DISCOVER_ROWS = [("new_releases", "New releases"), ("album_of_the_week", "Album of the week"),
                 ("playlists", "Playlists by Qobuz"), ("qobuzissims", "Qobuzissimes"),
                 ("most_streamed", "Most streamed"), ("press_awards", "Press awards"),
                 ("ideal_discography", "Ideal discography"), ("recent_releases", "Recent releases")]
#: an artist's releases (artist/page), by type, as its page's rows; "album" is the page's own Albums
RELEASE_ROWS = [("epSingle", "Singles & EPs"), ("live", "Live"), ("compilation", "Compilations"),
                ("other", "Other releases"), ("download", "More releases")]
#: what one artist row holds when Qobuz says there's more: the whole of it, asked for by type
RELEASES_LIMIT = 100


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT},
        connector=aiohttp.TCPConnector(ssl=tls()),
        timeout=aiohttp.ClientTimeout(total=20))


async def _scrape_app(session: aiohttp.ClientSession) -> dict[str, Any]:
    """App id + candidate secrets from the web player's bundle (see streamrip's QobuzSpoofer)."""
    async with session.get(f"{PLAYER}/login") as resp:
        match = BUNDLE_RE.search(await resp.text())
    if not match:
        raise ServiceError("Qobuz changed its web player; couldn't find app details")
    async with session.get(PLAYER + match.group(1)) as resp:
        bundle = await resp.text()
    app = APP_ID_RE.search(bundle)
    if not app:
        raise ServiceError("Qobuz changed its web player; couldn't find the app id")
    seeds: "OrderedDict[str, list[str]]" = OrderedDict()
    for m in SEED_RE.finditer(bundle):
        seeds[m.group("timezone")] = [m.group("seed")]
    # the bundle's ternaries evaluate the second timezone first
    keys = list(seeds)
    if len(keys) > 1:
        seeds.move_to_end(keys[1], last=False)
    names = "|".join(tz.capitalize() for tz in seeds)
    info_re = re.compile(rf'name:"\w+/(?P<timezone>{names})",info:"(?P<info>[\w=]+)",extras:"(?P<extras>[\w=]+)"')
    for m in info_re.finditer(bundle):
        seeds[m.group("timezone").lower()] += [m.group("info"), m.group("extras")]
    secrets = []
    for parts in seeds.values():
        try:
            secret = base64.standard_b64decode("".join(parts)[:-44]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if secret:
            secrets.append(secret)
    if not secrets:
        raise ServiceError("Qobuz changed its web player; couldn't find signing secrets")
    return {"app_id": app.group("app_id"), "secrets": secrets}


def _signature(track_id: str, format_id: int, ts: str, secret: str) -> str:
    raw = f"trackgetFileUrlformat_id{format_id}intentstreamtrack_id{track_id}{ts}{secret}"
    return hashlib.md5(raw.encode()).hexdigest()


class QobuzService(MusicService):
    """config: {"email", "user_id", "token", "quality"}  (password exchanged for token at save time)"""

    type = "qobuz"
    display_name = "Qobuz"
    features = frozenset({"star", "playlists", "scrobble", "browse", "radio", "more"})

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        return config.get("user_id") or None

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.app: Optional[dict[str, Any]] = None
        self._app_lock = asyncio.Lock()
        # ids of the favourite albums and artists and the playlists in the library, by kind; and
        # which of those playlists are the user's own
        self.library: Optional[dict[str, set[str]]] = None
        self.library_at = 0.0

    # --- credentials ---

    @staticmethod
    def _app_cache(runtime_dir: str) -> str:
        return os.path.join(runtime_dir, "qobuz-app.json")

    @classmethod
    async def _app_credentials(cls, session: aiohttp.ClientSession, runtime_dir: str,
                               refresh: bool = False, user_token: Optional[str] = None) -> dict[str, Any]:
        path = cls._app_cache(runtime_dir)
        if not refresh:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if time.time() - cached.get("at", 0) < APP_TTL and cached.get("secret"):
                    return cached
            except (FileNotFoundError, json.JSONDecodeError):
                pass
        app = await _scrape_app(session)
        app["secret"] = await cls._pick_secret(session, app, user_token)
        app["at"] = time.time()
        os.makedirs(runtime_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(app, f)
        return app

    @staticmethod
    async def _pick_secret(session: aiohttp.ClientSession, app: dict[str, Any], user_token: Optional[str]) -> str:
        """The bundle has several candidate secrets; the right one is accepted by a signed request."""
        headers = {"X-App-Id": app["app_id"]}
        if user_token:
            headers["X-User-Auth-Token"] = user_token
        for secret in app["secrets"]:
            ts = str(int(time.time()))
            params = {"request_ts": ts, "request_sig": _signature(TEST_TRACK, 5, ts, secret), "track_id": TEST_TRACK,
                      "format_id": 5, "intent": "stream"}
            async with session.get(f"{API}/track/getFileUrl", params=params, headers=headers) as resp:
                # 400 = bad signature; 200/401 = signature fine (401 only means no subscription/token)
                if resp.status in (200, 401):
                    return secret
        raise ServiceError("Qobuz rejected every signing secret from its web player")

    @classmethod
    def sign_in(cls, options: dict[str, Any], runtime_dir: str) -> SignIn:
        return QobuzLogin(runtime_dir, phone=bool(options.get("phone")))

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """Sign-in happens through OAuth (QobuzLogin); saving only edits name/quality and keeps the token."""
        quality = int(config.get("quality") or (previous or {}).get("quality") or 6)
        token = config.get("token") or (previous or {}).get("token")
        if not token:
            raise ServiceError("Sign in with Qobuz first")
        merged = {**(previous or {}), **{k: v for k, v in config.items() if v is not None}}
        merged["quality"] = quality
        merged["token"] = token
        return merged

    async def start(self) -> None:
        self.session = _session()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    async def _ensure_app(self, refresh: bool = False) -> dict[str, Any]:
        async with self._app_lock:
            if self.app is None or refresh:
                assert self.session
                self.app = await self._app_credentials(self.session, self.runtime_dir, refresh, self.config.get("token"))
            return self.app

    async def _get(self, path: str, **params: Any) -> Any:
        assert self.session
        for attempt in range(2):
            app = await self._ensure_app(refresh=attempt > 0)
            headers = {"X-App-Id": app["app_id"], "X-User-Auth-Token": self.config.get("token", "")}
            try:
                async with self.session.get(f"{API}/{path}", params={k: v for k, v in params.items() if v is not None},
                                            headers=headers) as resp:
                    if resp.status == 400 and attempt == 0:
                        continue  # app id / secret rotated: scrape fresh ones and retry once
                    if resp.status == 401:
                        raise ServiceError("Qobuz signed you out; edit the source and sign in again")
                    if resp.status == 404:
                        raise ServiceError("Not found on Qobuz")
                    if resp.status >= 400:
                        raise ServiceError(f"Qobuz returned HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach Qobuz: {e}") from e
        raise ServiceError("Qobuz rejected the request")

    async def test(self) -> str:
        data = await self._get("user/get", user_id=self.config.get("user_id"))
        plan = ((data.get("credential") or {}).get("parameters") or {}).get("short_label") or "subscription"
        return f"Signed in as {data.get('display_name') or self.config.get('email')} ({plan})"

    # --- mapping ---

    @staticmethod
    def _image(obj: dict[str, Any]) -> Optional[str]:
        image = obj.get("image") or {}
        if isinstance(image, dict):
            return image.get("large") or image.get("small") or image.get("thumbnail")
        return None

    # Qobuz answers in two shapes: the older endpoints' (album/get, catalog/search: "artist", "released_at",
    # "tracks_count", "streamable") and the newer ones' (discover/*, artist/page, radio/*, album/suggest: "artists",
    # "dates", "track_count", "rights"); each reads either

    @staticmethod
    def _main_artist(obj: dict[str, Any]) -> dict[str, Any]:
        """The first (main) artist: {"id", "name"}, its name a string whichever shape it came in."""
        if isinstance(obj.get("artist"), dict):
            main = obj["artist"]
        else:
            artists = [a for a in obj.get("artists") or [] if isinstance(a, dict)]
            main = next((a for a in artists if "main-artist" in (a.get("roles") or [])), artists[0] if artists else {})
        return {**main, "name": QobuzService._name(main.get("name"))} if main else {}

    @staticmethod
    def _name(value: Any) -> str:
        """A name, as a string or as artist/page's {"display": ...}."""
        return value.get("display", "") if isinstance(value, dict) else str(value or "")

    @staticmethod
    def _portrait(a: dict[str, Any]) -> Optional[str]:
        """An artist's picture: an old-style "image", or artist/page's portrait (a hash on Qobuz's image server)."""
        image = QobuzService._image(a)
        if image:
            return image
        portrait = (a.get("images") or {}).get("portrait") or {}
        if isinstance(portrait, dict) and re.fullmatch(r"[0-9a-f]{16,64}", str(portrait.get("hash", ""))):
            return f"https://static.qobuz.com/images/artists/covers/large/{portrait['hash']}.{portrait.get('format') or 'jpg'}"
        return None

    def _track(self, t: dict[str, Any], album: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        if not t.get("streamable", (t.get("rights") or {}).get("streamable", True)):
            return None
        album = t.get("album") or album or {}
        performer = ((t.get("performer") or {}).get("name") or self._main_artist(t).get("name")
                     or self._main_artist(album).get("name") or "")
        title = t.get("title", "").strip() + (f" ({t['version']})" if t.get("version") else "")
        audio = t.get("audio_info") or {}
        return models.track(str(t["id"]), title, performer, album.get("title", ""), float(t.get("duration") or 0),
                            art=self._image(album), album_id=album.get("id"),
                            artist_id=str(self._main_artist(album).get("id") or self._main_artist(t).get("id") or "")
                            or None,
                            starred=bool(t.get("favorited_at")),
                            gain=models.gain(audio.get("replaygain_track_gain"), None, audio.get("replaygain_track_peak")))

    def _tracks(self, items: list[dict[str, Any]], album: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        return [t for t in (self._track(i, album) for i in items or []) if t]

    def _album(self, a: dict[str, Any]) -> dict[str, Any]:
        released = models.utc_date(a.get("released_at")) if a.get("released_at") else None
        year = str(released.year) if released else str((a.get("dates") or {}).get("original") or "")[:4]
        hires = a.get("hires_streamable") or (a.get("rights") or {}).get("hires_streamable")
        sub = " · ".join(x for x in [self._main_artist(a).get("name", ""), year] if x) + (" · Hi-Res" if hires else "")
        # an edition ("Deluxe", "10th Anniversary Edition") tells two of an album apart
        title = (a.get("title") or "").strip() + (f" ({a['version']})" if a.get("version") else "")
        return models.collection(str(a["id"]), "album", title, sub, self._image(a))

    def _artist(self, a: dict[str, Any]) -> dict[str, Any]:
        count = a.get("albums_count")
        return models.collection(str(a["id"]), "artist", self._name(a.get("name")),
                                 models.count(count, "album") if count else "", self._portrait(a))

    def _playlist(self, p: dict[str, Any]) -> dict[str, Any]:
        # a list of covers ("images300"...), or {"covers": [...], "rectangle": ...} in the newer shape
        found = [p.get("images300"), p.get("images150"), p.get("images"), p.get("image")]
        found = [x.get("covers") if isinstance(x, dict) else x for x in found]
        images = next((x for x in found if isinstance(x, list) and x and isinstance(x[0], str)), [])
        sub = " · ".join(x for x in [(p.get("owner") or {}).get("name", ""),
                                      models.count(p.get("tracks_count") or 0, "track")] if x)
        return models.collection(str(p["id"]), "playlist", p.get("name") or p.get("title") or "", sub,
                                 images[0] if images else None)

    @staticmethod
    def _radio(kind: str, id: str, name: str, art: Optional[str] = None) -> dict[str, Any]:
        """A card for a radio Qobuz makes from a track, an artist or an album (opened by open())."""
        return models.collection(f"radio:{kind}:{id}", "list", f"{name} radio" if name else "Radio",
                                 "Endless, from Qobuz", art)

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        user_id = self.config.get("user_id")
        playlists, favourites, weekly, discover = await asyncio.gather(
            self._get("playlist/getUserPlaylists", user_id=user_id, limit=50),
            self._get("favorite/getUserFavorites", user_id=user_id, type="albums", limit=50),
            self._get("dynamic-tracks/get", type="weekly", limit=20),
            self._discover(), return_exceptions=True)
        sections = []
        # made for the user (Qobuz's own My Weekly Q), then theirs, then what Qobuz's Discover shows
        made = []
        if isinstance(weekly, dict) and weekly.get("title"):
            made.append(models.collection("weekly", "list", weekly["title"], weekly.get("baseline") or "",
                                          (weekly.get("images") or {}).get("large")))
        if made:
            sections.append(models.section("Made for you", made))
        if isinstance(weekly, dict) and (weekly.get("tracks") or {}).get("items"):
            # its songs too, to play from Home
            sections.append(models.section(weekly.get("title") or "My Weekly Q", [],
                                           self._tracks(weekly["tracks"]["items"])))
        if isinstance(playlists, dict):
            items = [self._playlist(p) for p in (playlists.get("playlists") or {}).get("items", [])]
            if items:
                sections.append(models.section("Your playlists", items))
        if isinstance(favourites, dict):
            albums = [self._album(a) for a in (favourites.get("albums") or {}).get("items", [])]
            if albums:
                sections.append(models.section("Favourite albums", albums))
        if isinstance(discover, list):
            sections += discover
        sections.append(models.section("Library", [models.collection("favourite-tracks", "list", "Favourite tracks")]))
        if all(isinstance(r, Exception) for r in (playlists, favourites, weekly, discover)):
            raise playlists if isinstance(playlists, Exception) else ServiceError("Qobuz didn't answer; try again")
        return sections

    async def _discover(self) -> list[dict[str, Any]]:
        """Qobuz's Discover page as rows: new releases, its picks and awards, its playlists. Older lists
        (album/getFeatured) when Discover doesn't answer."""
        try:
            data = await self._get("discover/index")
        except ServiceError:
            found = await asyncio.gather(*[self._get("album/getFeatured", type=kind, limit=20) for kind, _ in FEATURED],
                                         return_exceptions=True)
            return [models.section(title, [self._album(a) for a in (r.get("albums") or {}).get("items", [])])
                    for (_, title), r in zip(FEATURED, found) if isinstance(r, dict)]
        containers = data.get("containers") or {}
        rows = []
        for key, title in DISCOVER_ROWS:
            items = ((containers.get(key) or {}).get("data") or {}).get("items") or []
            if key == "playlists":
                cards = [self._playlist(p) for p in items if isinstance(p, dict) and p.get("id")]
            else:
                cards = [self._album(a) for a in items if isinstance(a, dict) and a.get("id")]
            if cards:
                rows.append(models.section(title, cards))
        return rows

    async def browse(self) -> list[dict[str, Any]]:
        data, themes = await asyncio.gather(self._get("genre/list"), self._themes(), return_exceptions=True)
        if isinstance(data, Exception):
            raise data
        genres = (data.get("genres") or {}).get("items") or []
        sections = [models.section("Genres", [models.collection(f"genre:{g['id']}#{g.get('name', '')}", "list",
                                                                g.get("name") or "Genre")
                                              for g in genres if str(g.get("id", "")).isdigit()])]
        if isinstance(themes, list) and themes:
            sections.append(models.section("Playlists by Qobuz", themes))
        return sections

    async def _themes(self) -> list[dict[str, Any]]:
        """Qobuz's playlist themes (Hi-Res, New releases, moods, genres...), each opening to its playlists."""
        data = await self._get("playlist/getTags")
        themes = []
        for t in data.get("tags") or []:
            slug = str(t.get("slug") or "")
            if not re.fullmatch(r"[a-z0-9-]{1,64}", slug):
                continue
            try:
                names = json.loads(t.get("name_json") or "{}")
            except ValueError:
                names = {}
            name = (names.get("en") or next(iter(names.values()), "") or slug) if isinstance(names, dict) else slug
            themes.append(models.collection(f"tag:{slug}#{name}", "list", name))
        return themes

    # --- media center ---

    TABS = (("home", "Home"), ("playlists", "Playlists"), ("albums", "Albums"), ("artists", "Artists"),
            ("songs", "Songs"), ("picks", "Playlists by Qobuz")) \
        + tuple((f"discover:{kind}", title) for kind, title in DISCOVER_FEATURED) + (("genres", "Genres"),)
    SHARED_TABS = frozenset({"picks", "genres", *(f"discover:{kind}" for kind, _ in DISCOVER_FEATURED)})

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            # what's yours is the Library tab's
            return [s for s in await self.home()
                    if s["title"] not in ("Favourite albums", "Library") and not models.is_categories(s)]
        if tab_id == "picks":
            # by theme, as Qobuz's own app has them: a row of each theme's playlists
            themes = await self._themes()
            if themes:
                return [as_rows(models.section("Playlists by Qobuz", themes))]
            playlists = await self._get("playlist/getFeatured", type="editor-picks", limit=60)
            return [models.section("Playlists by Qobuz", [self._playlist(p) for p in
                                                         (playlists.get("playlists") or {}).get("items", [])],
                                   display="grid")]
        if tab_id.startswith("discover:") and tab_id[9:] in dict(DISCOVER_FEATURED):
            result = await self._get("album/getFeatured", type=tab_id[9:], limit=60)
            return [models.section(dict(DISCOVER_FEATURED)[tab_id[9:]], [self._album(a) for a in
                                                                         (result.get("albums") or {}).get("items", [])],
                                   display="grid")]
        if tab_id in ("playlists", "albums", "artists", "songs"):
            # the library's parts, each its own tile
            sections = {s["title"]: s for s in await self.tab("library")}
            part = {"playlists": "Playlists", "albums": "Albums", "artists": "Artists", "songs": "Favourite tracks"}
            found = sections.get(part[tab_id])
            if not found:
                raise ServiceError("Qobuz didn't answer; try again")
            return [{**found, "display": "grid"}]
        if tab_id == "discover":
            kinds = DISCOVER_FEATURED
            albums, playlists = await asyncio.gather(
                asyncio.gather(*[self._get("album/getFeatured", type=k, limit=30) for k, _ in kinds],
                               return_exceptions=True),
                self._get("playlist/getFeatured", type="editor-picks", limit=30), return_exceptions=True)
            sections = []
            if isinstance(playlists, dict):
                items = [self._playlist(p) for p in (playlists.get("playlists") or {}).get("items", [])]
                sections.append(models.section("Playlists by Qobuz", items))
            for (_, title), result in zip(kinds, albums if isinstance(albums, list) else []):
                if isinstance(result, dict):
                    sections.append(models.section(title, [self._album(a) for a in
                                                           (result.get("albums") or {}).get("items", [])]))
            if not any(s["items"] for s in sections):
                raise ServiceError("Qobuz didn't answer; try again")
            return sections
        if tab_id == "genres":
            return [as_rows(s) for s in await self.browse()]
        if tab_id == "library":
            user_id = self.config.get("user_id")
            playlists, albums, artists, tracks = await asyncio.gather(
                self._get("playlist/getUserPlaylists", user_id=user_id, limit=500),
                self._get("favorite/getUserFavorites", user_id=user_id, type="albums", limit=500),
                self._get("favorite/getUserFavorites", user_id=user_id, type="artists", limit=500),
                self.open("list", "favourite-tracks"), return_exceptions=True)
            sections = [models.section("Playlists", [self._playlist(p) for p in
                                                     ((playlists.get("playlists") or {}).get("items", [])
                                                      if isinstance(playlists, dict) else [])],
                                       display="grid", mine=True)]
            if isinstance(albums, dict):
                sections.append(models.section("Albums", [self._album(a) for a in
                                                          (albums.get("albums") or {}).get("items", [])],
                                               display="grid"))
            if isinstance(artists, dict):
                sections.append(models.section("Artists", [self._artist(a) for a in
                                                           (artists.get("artists") or {}).get("items", [])]))
            if isinstance(tracks, dict):
                sections.append(models.section("Favourite tracks", [], tracks["tracks"], display="grid"))
            return sections
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("genre:"):
            genre, _, name = id[6:].partition("#")
            if not genre.isdigit():
                raise ServiceError("Unknown genre")
            # well past what a row shows: the most streamed three pages deep, then what's new
            found = await asyncio.gather(
                *[self._get("album/getFeatured", type="most-streamed", genre_ids=genre, limit=GENRE_PAGE, offset=n)
                  for n in range(0, 3 * GENRE_PAGE, GENRE_PAGE)],
                self._get("album/getFeatured", type="new-releases-full", genre_ids=genre, limit=GENRE_PAGE),
                return_exceptions=True)
            if all(isinstance(r, Exception) for r in found):
                raise next(r for r in found if isinstance(r, Exception))
            seen: set[str] = set()
            albums = [self._album(a) for result in found if isinstance(result, dict)
                      for a in (result.get("albums") or {}).get("items", []) if not (a["id"] in seen or seen.add(a["id"]))]
            return models.page(name or "Genre", "Most streamed and new on Qobuz", collections=albums)
        if kind == "list" and id == "favourite-tracks":
            data = await self._get("favorite/getUserFavorites", user_id=self.config.get("user_id"), type="tracks",
                                   limit=500)
            tracks = self._tracks((data.get("tracks") or {}).get("items", []))
            for t in tracks:
                t["starred"] = True
            return models.page("Favourite tracks", models.count(len(tracks), "track"), tracks=tracks)
        if kind == "list" and id.startswith("tag:"):
            slug, _, name = id[4:].partition("#")
            if not re.fullmatch(r"[a-z0-9-]{1,64}", slug):
                raise ServiceError("Unknown theme")
            data = await self._get("discover/playlists", tags=slug, limit=100)
            return models.page(name or "Playlists", "Playlists by Qobuz",
                               collections=[self._playlist(p) for p in data.get("items") or [] if p.get("id")])
        if kind == "list" and id == "weekly":
            data = await self._get("dynamic-tracks/get", type="weekly", limit=50)
            return models.page(data.get("title") or "My Weekly Q", data.get("baseline") or "",
                               (data.get("images") or {}).get("large"),
                               tracks=self._tracks((data.get("tracks") or {}).get("items", [])),
                               about=data.get("description") or "")
        if kind == "list" and id.startswith("radio:"):
            what, _, of = id[6:].partition(":")
            if what not in ("track", "artist", "album") or not QOBUZ_ID.match(of):
                raise ServiceError("Unknown radio")
            data = await self._get(f"radio/{what}", **{f"{what}_id": of})
            images = data.get("images") or {}
            return models.page(f"{data.get('title') or 'Qobuz'} radio", "Endless, from Qobuz",
                               images.get("large") or images.get("small"),
                               tracks=self._tracks((data.get("tracks") or {}).get("items", [])))
        if kind == "list" and id.startswith("label:"):
            label, _, name = id[6:].partition("#")
            if not label.isdigit():
                raise ServiceError("Unknown label")
            data = await self._get("label/get", label_id=label, extra="albums", limit=RELEASES_LIMIT)
            albums = (data.get("albums") or {}).get("items", [])
            return models.page(data.get("name") or name or "Label",
                               models.count((data.get("albums") or {}).get("total") or len(albums), "album"),
                               self._image(data), collections=[self._album(a) for a in albums],
                               about=notes_text(data.get("description") or ""))
        if kind == "album":
            a, library, like = await asyncio.gather(self._get("album/get", album_id=id), self._library(),
                                                    self._get("album/suggest", album_id=id), return_exceptions=True)
            if isinstance(a, BaseException):
                raise a
            c = self._album(a)
            artist = self._main_artist(a)
            # ways on from it: its radio, its label, its artist
            explore = [self._radio("album", id, a.get("title", ""), c["art"])]
            label = a.get("label") or {}
            if str(label.get("id", "")).isdigit():
                explore.append(models.collection(f"label:{label['id']}#{label.get('name', '')}", "list",
                                                 label.get("name") or "Label", "Label"))
            if artist.get("id"):
                explore.append(models.collection(str(artist["id"]), "artist", artist.get("name", ""), "Artist",
                                                 self._portrait(artist)))
            similar = [self._album(x) for x in ((like.get("albums") or {}).get("items") or [])
                       if isinstance(x, dict) and x.get("id")] if isinstance(like, dict) else []
            return models.page(c["title"], c["subtitle"], c["art"],
                               tracks=self._tracks((a.get("tracks") or {}).get("items", []), album=a),
                               save=models.saved(self._saved(library if not isinstance(library, BaseException)
                                                             else None, "album", id)),
                               shelves=[models.section("Explore", explore), models.section("More like this", similar)],
                               about=notes_text(a.get("description") or ""))
        if kind == "artist":
            return await self._artist_page(id)
        if kind == "playlist":
            p, library = await asyncio.gather(self._get("playlist/get", playlist_id=id, extra="tracks", limit=500),
                                              self._library())
            c = self._playlist(p)
            # the user's own playlists are theirs already: no button
            own = str((p.get("owner") or {}).get("id")) == str(self.config.get("user_id"))
            return models.page(c["title"], c["subtitle"], c["art"],
                               tracks=self._tracks((p.get("tracks") or {}).get("items", [])),
                               save=None if own else models.saved(self._saved(library, "playlist", id)))
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def _artist_page(self, id: str) -> dict[str, Any]:
        """An artist as Qobuz's own app shows one (artist/page): top tracks, albums, then a row for each other kind
        of release, their playlists, what they appear on, their radio and similar artists, and their biography."""
        if not QOBUZ_ID.match(id):
            raise ServiceError("Not found on Qobuz")
        # artist/page gives five similar artists; Qobuz's app shows more
        a, library, similar = await asyncio.gather(
            self._get("artist/page", artist_id=id, sort="release_date"), self._library(),
            self._get("artist/getSimilarArtists", artist_id=id, limit=20), return_exceptions=True)
        if isinstance(a, BaseException):
            # an artist artist/page doesn't know: the older page, their albums
            old = await self._get("artist/get", artist_id=id, extra="albums", limit=100)
            albums = sorted((old.get("albums") or {}).get("items", []), key=lambda x: x.get("released_at") or 0,
                            reverse=True)
            return models.page(old.get("name", ""), self._artist(old)["subtitle"], self._image(old),
                               collections=[self._album(x) for x in albums],
                               save=models.saved(self._saved(library if isinstance(library, dict) else None,
                                                             "artist", id)))
        name = self._name(a.get("name"))
        groups = {g.get("type"): g for g in a.get("releases") or [] if isinstance(g, dict)}
        # a kind of release Qobuz has more of than artist/page gives: all of it, by type
        fuller = [t for t, _ in [("album", ""), *RELEASE_ROWS] if (groups.get(t) or {}).get("has_more")]
        more = await asyncio.gather(*[self._get("artist/getReleasesList", artist_id=id, release_type=t,
                                                limit=RELEASES_LIMIT, sort="release_date") for t in fuller],
                                    return_exceptions=True)
        for t, got in zip(fuller, more):
            if isinstance(got, dict) and got.get("items"):
                groups[t] = {**groups[t], "items": got["items"]}
        releases = lambda t: [self._album(x) for x in (groups.get(t) or {}).get("items") or []  # noqa: E731
                              if isinstance(x, dict) and x.get("id")]
        art = self._portrait(a)
        shelves = [models.section(title, releases(t)) for t, title in RELEASE_ROWS]
        # the albums of others they're on (compilations, features), each once, as Qobuz's app shows them
        appears: dict[str, dict[str, Any]] = {}
        for t in a.get("tracks_appears_on") or []:
            album = t.get("album") if isinstance(t, dict) else None
            if isinstance(album, dict) and album.get("id") and str(album["id"]) not in appears:
                appears[str(album["id"])] = self._album(album)
        shelves.append(models.section("Appears on", list(appears.values())))
        shelves.append(models.section("Playlists", [self._playlist(p) for p in (a.get("playlists") or {}).get("items")
                                                    or [] if isinstance(p, dict) and p.get("id")]))
        shelves.append(models.section("Radio", [self._radio("artist", id, name, art)]))
        alike = ((similar.get("artists") or {}).get("items") if isinstance(similar, dict) else None) \
            or (a.get("similar_artists") or {}).get("items") or []
        shelves.append(models.section("Similar artists", [self._artist(s) for s in alike
                                                           if isinstance(s, dict) and s.get("id")]))
        category = a.get("artist_category") or ""
        return models.page(name, category if category and category.lower() != "performer" else "", art,
                           collections=releases("album"), tracks=self._tracks(a.get("top_tracks") or []),
                           shelves=shelves,
                           about=notes_text(((a.get("biography") or {}).get("content")) or ""),
                           save=models.saved(self._saved(library if isinstance(library, dict) else None, "artist", id)))

    async def search(self, query: str) -> dict[str, Any]:
        # as many of each as Qobuz's own apps show: the media center ends long rows in Show all
        data = await self._get("catalog/search", query=query, limit=60)
        artists = [self._artist(a) for a in (data.get("artists") or {}).get("items", [])[:16]]
        albums = [self._album(a) for a in (data.get("albums") or {}).get("items", [])]
        playlists = [self._playlist(p) for p in (data.get("playlists") or {}).get("items", [])]
        return models.page(f"Results for “{query}”", collections=artists + albums + playlists,
                           tracks=self._tracks((data.get("tracks") or {}).get("items", [])), tracks_first=True)

    async def search_more(self, query: str, part: str, offset: int) -> list[dict[str, Any]]:
        """The next of a search's songs, from `offset` on (Qobuz's own paging)."""
        if part != "Songs":
            raise ServiceError("There's no more of that")
        data = await self._get("track/search", query=query, limit=100, offset=max(0, int(offset)))
        return self._tracks((data.get("tracks") or {}).get("items", []))

    # --- features ---

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        """Qobuz's own radio from a track (radio/track): what its app plays after "Start radio"."""
        if not QOBUZ_ID.match(str(track.get("id", ""))):
            raise ServiceError("Not found on Qobuz")
        data = await self._get("radio/track", track_id=track["id"])
        return [t for t in self._tracks((data.get("tracks") or {}).get("items", [])) if t["id"] != str(track["id"])]

    async def star(self, track_id: str, starred: bool) -> None:
        await self._get("favorite/create" if starred else "favorite/delete", track_ids=track_id)

    SAVES = frozenset({"album", "artist", "playlist"})

    async def _library(self) -> Optional[dict[str, set[str]]]:
        """What's in the library, by kind: favourite albums and artists, and the playlists (the user's own
        and those subscribed to). None when Qobuz doesn't answer."""
        if self.library is not None and time.time() - self.library_at < LIBRARY_TTL:
            return self.library
        user_id = self.config.get("user_id")
        found = await asyncio.gather(
            self._get("favorite/getUserFavorites", user_id=user_id, type="albums", limit=LIBRARY_LIMIT),
            self._get("favorite/getUserFavorites", user_id=user_id, type="artists", limit=LIBRARY_LIMIT),
            self._get("playlist/getUserPlaylists", user_id=user_id, limit=LIBRARY_LIMIT), return_exceptions=True)
        if any(isinstance(f, BaseException) or not isinstance(f, dict) for f in found):
            debuglog.debug("qobuz", "library unavailable", errors=[str(f) for f in found if isinstance(f, BaseException)])
            return None
        ids = lambda data, key: {str(x["id"]) for x in (data.get(key) or {}).get("items", [])  # noqa: E731
                                 if isinstance(x, dict) and x.get("id") is not None}
        self.library = {"album": ids(found[0], "albums"), "artist": ids(found[1], "artists"),
                        "playlist": ids(found[2], "playlists")}
        self.library_at = time.time()
        return self.library

    @staticmethod
    def _saved(library: Optional[dict[str, set[str]]], kind: str, id: str) -> Optional[bool]:
        return None if library is None else id in library[kind]

    async def save(self, kind: str, id: str, on: bool) -> None:
        if not QOBUZ_ID.match(id):
            raise ServiceError("Not found on Qobuz")
        if kind == "playlist":
            await self._get("playlist/subscribe" if on else "playlist/unsubscribe", playlist_id=id)
        else:
            await self._get("favorite/create" if on else "favorite/delete", **{f"{kind}_ids": id})
        if self.library is not None:
            (self.library[kind].add if on else self.library[kind].discard)(id)

    async def scrobble(self, track_id: str, submission: bool) -> None:
        """Qobuz counts a play when the stream starts (as its web player reports it)."""
        if submission:
            return
        assert self.session
        app = await self._ensure_app()
        events = json.dumps([{"track_id": int(track_id), "date": int(time.time()),
                              "user_id": int(self.config.get("user_id") or 0),
                              "format_id": int(self.config.get("quality") or 6)}])
        async with self.session.post(f"{API}/track/reportStreamingStart", data={"events": events},
                                     headers={"X-App-Id": app["app_id"],
                                              "X-User-Auth-Token": self.config.get("token", "")}) as resp:
            debuglog.debug("qobuz", "reported stream start", track=track_id, status=resp.status)

    async def user_playlists(self) -> list[dict[str, Any]]:
        data = await self._get("playlist/getUserPlaylists", user_id=self.config.get("user_id"), limit=100)
        user_id = self.config.get("user_id")
        return [self._playlist(p) for p in (data.get("playlists") or {}).get("items", [])
                if str((p.get("owner") or {}).get("id")) == str(user_id)]

    async def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        await self._get("playlist/addTracks", playlist_id=playlist_id, track_ids=",".join(track_ids))

    async def create_playlist(self, name: str, track_ids: list[str]) -> dict[str, Any]:
        created = await self._get("playlist/create", name=name, is_public="false", is_collaborative="false")
        if not (created or {}).get("id"):
            raise ServiceError("Qobuz didn't make the playlist")
        if track_ids:
            await self.add_to_playlist(str(created["id"]), track_ids)
        return self._playlist({**created, "tracks_count": len(track_ids)})

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        return await self._stream(track_id, int(self.config.get("quality") or 6))

    async def compatible_stream(self, track_id: str) -> StreamSource:
        # TVs and receivers often struggle with hi-res FLAC (slow to start, or no sound): CD quality at most
        return await self._stream(track_id, min(int(self.config.get("quality") or 6), 6))

    async def _stream(self, track_id: str, quality: int) -> StreamSource:
        for attempt in range(2):
            app = await self._ensure_app(refresh=attempt > 0)
            ts = str(int(time.time()))
            data = None
            try:
                data = await self._get("track/getFileUrl", track_id=track_id, format_id=quality, intent="stream",
                                       request_ts=ts, request_sig=_signature(track_id, quality, ts, app["secret"]))
                break
            except ServiceError as e:
                if attempt == 1:
                    raise
                debuglog.debug("qobuz", "getFileUrl failed, refreshing app details", error=str(e))
        url = (data or {}).get("url")
        if not url:
            if (data or {}).get("sample"):
                raise ServiceError("Qobuz only offers a preview of this track on your plan")
            raise ServiceError("Qobuz didn't return a stream for this track")
        debuglog.debug("qobuz", "stream", track=track_id, format=data.get("format_id"), bit_depth=data.get("bit_depth"),
                       rate=data.get("sampling_rate"))
        # file links are short-lived; resolve again when replaying later
        return StreamSource(url=url, expires=time.time() + 20 * 60)

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith("https://"):
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id)


class QobuzLogin(SignIn):
    """One sign-in attempt: a loopback listener for Qobuz's OAuth redirect, then the code exchange.

    Qobuz redirects the browser to http://localhost:<port>/?code_autorisation=<code>; Steam's browser
    runs on the same device, so the listener (bound to 127.0.0.1 only) receives it directly.

    On a phone (`phone`, the page as a QR code), the redirect comes back over the home network instead: to this
    device's address there, at a path of random characters only that page knows, so nothing else on the network
    can hand it a code. The listener is there only while the sign-in waits.
    """

    def __init__(self, runtime_dir: str, phone: bool = False):
        super().__init__()
        self.runtime_dir = runtime_dir
        self.phone = phone
        self._path = "/" + secrets.token_urlsafe(18) if phone else "/"
        self.app_id = ""
        self._runner: Optional[web.AppRunner] = None
        self._code: asyncio.Future = asyncio.get_event_loop().create_future()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        async with _session() as session:
            app = await QobuzService._app_credentials(session, self.runtime_dir)
        self.app_id = app["app_id"]
        host = "localhost"
        if self.phone:
            host = next((a for a in netif.local_addresses()
                         if ipaddress.ip_address(a).is_private and not ipaddress.ip_address(a).is_loopback), "")
            if not host:
                raise ServiceError("This device isn't on a home network the phone could come back to; "
                                   "sign in here instead")
        web_app = web.Application()
        web_app.router.add_get(self._path, self._redirect)
        self._runner = web.AppRunner(web_app, access_log=None)
        await self._runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0" if self.phone else "127.0.0.1", 0))
        port = sock.getsockname()[1]
        await web.SockSite(self._runner, sock).start()
        self.url = (f"https://www.qobuz.com/signin/oauth?ext_app_id={self.app_id}"
                    f"&redirect_url=http://{host}:{port}{self._path if self.phone else ''}")
        if self.phone:
            self.qr = self.url
        self.status = "waiting"
        self._task = asyncio.get_event_loop().create_task(self._finish())

    async def _redirect(self, request: web.Request) -> web.Response:
        code = request.query.get("code_autorisation") or request.query.get("code")
        if code and not self._code.done():
            self._code.set_result(code)
        message = ("Signed in to Qobuz. You can close this page and go back to MusicDeck." if code
                   else "Qobuz didn't send a sign-in code. Go back to MusicDeck and try again.")
        return web.Response(text=f"<html><body style='font-family:sans-serif;padding:2em'><h2>{message}</h2>"
                                 f"</body></html>", content_type="text/html")

    async def _finish(self) -> None:
        try:
            code = await asyncio.wait_for(self._code, OAUTH_TIMEOUT)
            async with _session() as session:
                async with session.get(f"{API}/oauth/callback",
                                       params={"code": code, "private_key": OAUTH_PRIVATE_KEY},
                                       headers={"X-App-Id": self.app_id}) as resp:
                    if resp.status >= 400:
                        raise ServiceError(f"Qobuz sign-in failed (HTTP {resp.status})")
                    data = await resp.json(content_type=None)
                token = data.get("token") or data.get("user_auth_token")
                user_id = str(data.get("user_id") or "")
                if not token or not user_id:
                    raise ServiceError("Qobuz didn't return a sign-in token")
                headers = {"X-App-Id": self.app_id, "X-User-Auth-Token": token}
                async with session.get(f"{API}/user/get", params={"user_id": user_id}, headers=headers) as resp:
                    user = await resp.json(content_type=None) if resp.status == 200 else {}
            if not ((user.get("credential") or {}).get("parameters")):
                raise ServiceError("This Qobuz account has no streaming subscription")
            self.result = {"user_id": user_id, "token": token,
                           "email": user.get("email") or user.get("display_name") or ""}
            self.status = "done"
            debuglog.debug("qobuz", "signed in", user_id=user_id)
        except asyncio.TimeoutError:
            self.status, self.error = "error", "Sign-in timed out"
        except ServiceError as e:
            self.status, self.error = "error", str(e)
        except Exception as e:
            self.status, self.error = "error", f"Sign-in failed: {e}"
        finally:
            await self._stop_listening()

    async def close(self) -> None:
        if self._task and not self._task.done() and self._task is not asyncio.current_task():
            self._task.cancel()
        await self._stop_listening()

    async def _stop_listening(self) -> None:
        # close() and the cancelled sign-in task both end up here; only the first one cleans up
        runner, self._runner = self._runner, None
        if runner:
            await runner.cleanup()
