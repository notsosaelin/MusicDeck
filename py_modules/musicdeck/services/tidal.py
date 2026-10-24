"""
TIDAL, following python-tidal (the library behind Mopidy-Tidal).

* Sign-in: TIDAL's device login. MusicDeck opens link.tidal.com with a one-time code in Steam's
  browser; the user approves there and MusicDeck receives tokens. It never sees the password, and
  only the refresh token is stored.
* Streams are TIDAL's own unencrypted files: a direct FLAC/AAC URL ("BTS" manifest), or DASH
  segments, which are turned into an HLS playlist for hls.js. Up to CD-quality FLAC: TIDAL only
  grants 24-bit "Max" to the browser-redirect sign-in, which Steam's browser can't hand back.
"""
import asyncio
import base64
import json
import re
import time
from typing import Any, Optional

import aiohttp

from .. import debuglog, lyrics, models
from ..net import tls
from .base import MusicService, ServiceError, SignIn, StreamSource

API = "https://api.tidal.com/v1/"
AUTH = "https://auth.tidal.com/v1/oauth2/"
IMAGES = "https://resources.tidal.com/images/"
SCOPE = "r_usr w_usr w_sub"
# python-tidal's device-login client (its Config.client_id / client_secret)
CLIENT_ID = base64.b64decode(base64.b64decode(b"WmxneVNuaGtiVzUw") + base64.b64decode(b"V2xkTE1HbDRWQT09")).decode()
CLIENT_SECRET = base64.b64decode(base64.b64decode(b"TVU1dU9VRm1SRUZxZUhKblNrWktZa3RPVjB4bFFY")
                                 + base64.b64decode(b"bExSMVpIYlVsT2RWaFFVRXhJVmxoQmRuaEJaejA9")).decode()
QUALITIES = {"LOW": "AAC 96 kbps", "HIGH": "AAC 320 kbps", "LOSSLESS": "FLAC 16-bit/44.1 kHz"}
DEFAULT_QUALITY = "LOSSLESS"
PAGE = 100
MAX_TRACKS = 2000
FAVOURITES_TTL = 300
# an album's, artist's (digits) or playlist's (a UUID) id, as it goes into a path
TIDAL_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# favorites/ids' keys, and the favourites paths, for each kind of collection
SAVE_KINDS = {"album": ("ALBUM", "albums", "albumIds"), "artist": ("ARTIST", "artists", "artistIds"),
              "playlist": ("PLAYLIST", "playlists", "uuids")}
# Decky's bundled Python has no xml package, so the (small, flat) DASH manifest is read with patterns
DASH_TAG = re.compile(r"<(?:\w+:)?(Representation|SegmentTemplate|S)\b([^>]*?)/?>")
DASH_ATTR = re.compile(r'([\w:]+)="([^"]*)"')


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls()),
        timeout=aiohttp.ClientTimeout(total=20))


def _image(uuid: Optional[str], size: int = 320) -> Optional[str]:
    """TIDAL image ids are UUIDs; the file lives at their dashes turned into path separators."""
    if not uuid:
        return None
    return f"{IMAGES}{uuid.replace('-', '/')}/{size}x{size}.jpg"


def _dash_elements(mpd: str) -> list[tuple[str, dict[str, str]]]:
    return [(m.group(1), {k: v.replace("&amp;", "&") for k, v in DASH_ATTR.findall(m.group(2))})
            for m in DASH_TAG.finditer(mpd)]


def dash_to_hls(mpd: str) -> str:
    """A DASH manifest (SegmentTemplate with a SegmentTimeline, as TIDAL writes them) as an HLS
    media playlist of the same fMP4 segments, which hls.js plays."""
    elements = _dash_elements(mpd)
    rep = next((attrs for tag, attrs in elements if tag == "Representation"), None)
    template = next((attrs for tag, attrs in elements if tag == "SegmentTemplate"), None)
    timeline = [attrs for tag, attrs in elements if tag == "S"]
    if rep is None or template is None or not timeline:
        raise ServiceError("TIDAL sent a stream layout MusicDeck can't play")
    rep_id = rep.get("id", "")
    bandwidth = rep.get("bandwidth", "")

    def fill(pattern: str, number: Optional[int] = None) -> str:
        url = pattern.replace("$RepresentationID$", rep_id).replace("$Bandwidth$", bandwidth)
        return url.replace("$Number$", str(number)) if number is not None else url

    timescale = int(template.get("timescale", "1"))
    number = int(template.get("startNumber", "1"))
    durations: list[float] = []
    for s in timeline:
        d = int(s.get("d", "0")) / timescale
        durations += [d] * (int(s.get("r", "0")) + 1)
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-PLAYLIST-TYPE:VOD",
             f"#EXT-X-TARGETDURATION:{int(max(durations, default=1)) + 1}", "#EXT-X-MEDIA-SEQUENCE:0",
             f'#EXT-X-MAP:URI="{fill(template.get("initialization", ""))}"']
    for i, d in enumerate(durations):
        lines += [f"#EXTINF:{d:.3f},", fill(template.get("media", ""), number + i)]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


async def _token_request(session: aiohttp.ClientSession, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
    async with session.post(AUTH + "token", data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **data}) as resp:
        return resp.status, await resp.json(content_type=None)


class TidalService(MusicService):
    """config: {"refresh_token", "user_id", "country_code", "username", "quality"}"""

    type = "tidal"
    display_name = "TIDAL"
    features = frozenset({"star", "playlists", "radio", "lyrics"})

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        return config.get("user_id") or None

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.access_token = ""
        self.token_expires = 0.0
        self.token_lock = asyncio.Lock()
        self.favourites: set[str] = set()
        self.favourites_at = 0.0
        # favourite albums, artists and playlists by kind (My Collection), from the same request; None
        # until TIDAL has answered
        self.collection: Optional[dict[str, set[str]]] = None

    @classmethod
    def sign_in(cls, options: dict[str, Any], runtime_dir: str) -> SignIn:
        return TidalLogin()

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """Sign-in happens through TidalLogin; saving only edits name/quality and keeps the tokens."""
        refresh = config.get("refresh_token") or (previous or {}).get("refresh_token")
        if not refresh:
            raise ServiceError("Sign in with TIDAL first")
        merged = {**(previous or {}), **{k: v for k, v in config.items() if v is not None}}
        merged["refresh_token"] = refresh
        if merged.get("quality") not in QUALITIES:
            merged["quality"] = DEFAULT_QUALITY
        return merged

    async def start(self) -> None:
        self.session = _session()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    # --- API ---

    async def _token(self) -> str:
        async with self.token_lock:
            if self.access_token and time.time() < self.token_expires - 60:
                return self.access_token
            assert self.session
            try:
                status, data = await _token_request(self.session, {
                    "grant_type": "refresh_token", "refresh_token": self.config.get("refresh_token", "")})
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach TIDAL: {e}") from e
            if status != 200 or "access_token" not in data:
                raise ServiceError("TIDAL signed you out; edit the source and sign in again")
            self.access_token = data["access_token"]
            self.token_expires = time.time() + int(data.get("expires_in", 3600))
            return self.access_token

    async def _request(self, method: str, path: str, params: Optional[dict[str, Any]] = None,
                       data: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None,
                       want_headers: bool = False) -> Any:
        assert self.session
        query = {"countryCode": self.config.get("country_code") or "US", **(params or {})}
        for attempt in range(2):
            auth = {"Authorization": f"Bearer {await self._token()}", **(headers or {})}
            try:
                async with self.session.request(method, API + path, params=query, data=data, headers=auth) as resp:
                    if resp.status == 401 and attempt == 0:
                        self.access_token = ""
                        continue
                    if resp.status == 404:
                        raise ServiceError("Not found on TIDAL")
                    if resp.status == 429:
                        raise ServiceError("TIDAL is rate-limiting MusicDeck; try again in a moment")
                    if resp.status >= 400:
                        body = await resp.json(content_type=None) if resp.content_type == "application/json" else {}
                        message = body.get("userMessage") or body.get("description")
                        debuglog.debug("tidal", "request failed", path=path, status=resp.status, message=message)
                        raise ServiceError(message or f"TIDAL returned HTTP {resp.status}")
                    text = await resp.text()
                    body = json.loads(text) if text else None
                    return (body, resp.headers) if want_headers else body
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach TIDAL: {e}") from e
        raise ServiceError("TIDAL signed you out; edit the source and sign in again")

    async def _get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, {k: v for k, v in params.items() if v is not None})

    async def _all(self, path: str, limit: int = MAX_TRACKS, **params: Any) -> list[dict[str, Any]]:
        """Every item of a paged list, up to `limit`."""
        items: list[dict[str, Any]] = []
        while len(items) < limit:
            page = await self._get(path, limit=min(PAGE, limit - len(items)), offset=len(items), **params)
            batch = page.get("items") or []
            items += batch
            if not batch or len(items) >= int(page.get("totalNumberOfItems") or 0):
                break
        return items

    def _user(self) -> str:
        return str(self.config.get("user_id") or "")

    async def test(self) -> str:
        sub = await self._get(f"users/{self._user()}/subscription")
        plan = ((sub or {}).get("subscription") or {}).get("type") or "subscription"
        quality = QUALITIES.get(self.config.get("quality") or DEFAULT_QUALITY, "")
        return f"Signed in as {self.config.get('username') or 'TIDAL user'} ({plan.title()}) · {quality}"

    # --- mapping ---

    def _track(self, t: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not t or not t.get("id") or t.get("allowStreaming") is False or t.get("streamReady") is False:
            return None
        if t.get("type") and t["type"] not in ("track", "TRACK"):
            return None  # videos in playlists
        album = t.get("album") or {}
        artists = t.get("artists") or ([t["artist"]] if t.get("artist") else [])
        title = t.get("title", "") + (f" ({t['version']})" if t.get("version") else "")
        tid = str(t["id"])
        return models.track(tid, title, ", ".join(a.get("name", "") for a in artists), album.get("title", ""),
                            float(t.get("duration") or 0), art=_image(album.get("cover")),
                            album_id=str(album["id"]) if album.get("id") else None,
                            artist_id=str(artists[0]["id"]) if artists and artists[0].get("id") else None,
                            starred=tid in self.favourites,
                            gain=models.gain(t.get("replayGain"), None, t.get("peak")))

    def _tracks(self, items: list[Any]) -> list[dict[str, Any]]:
        out = []
        for item in items or []:
            # playlist and favourite lists wrap the track in "item"; others don't
            t = self._track(item.get("item") if isinstance(item, dict) and "item" in item else item)
            if t:
                out.append(t)
        return out

    @staticmethod
    def _album(a: dict[str, Any]) -> dict[str, Any]:
        artists = ", ".join(x.get("name", "") for x in a.get("artists") or ([a["artist"]] if a.get("artist") else []))
        year = (a.get("releaseDate") or "")[:4]
        return models.collection(str(a["id"]), "album", a.get("title", ""), " · ".join(x for x in [artists, year] if x),
                                 _image(a.get("cover")))

    @staticmethod
    def _artist(a: dict[str, Any]) -> dict[str, Any]:
        return models.collection(str(a["id"]), "artist", a.get("name", ""), "Artist",
                                 _image(a.get("picture") or a.get("selectedAlbumCoverFallback")))

    @staticmethod
    def _playlist(p: dict[str, Any]) -> dict[str, Any]:
        creator = (p.get("creator") or {}).get("name") or ("TIDAL" if p.get("type") == "EDITORIAL" else "")
        count = p.get("numberOfTracks")
        sub = " · ".join(x for x in [creator, models.count(count, "track") if count else ""] if x)
        return models.collection(str(p["uuid"]), "playlist", p.get("title", ""), sub,
                                 _image(p.get("squareImage"), 480) if p.get("squareImage") else _image(p.get("image"), 480))

    @staticmethod
    def _mix(m: dict[str, Any]) -> dict[str, Any]:
        images = m.get("images") or {}
        art = next(((images.get(k) or {}).get("url") for k in ("MEDIUM", "LARGE", "SMALL") if images.get(k)), None)
        return models.collection(f"mix:{m['id']}", "playlist", m.get("title", ""), m.get("subTitle", ""), art)

    async def _load_favourites(self, force: bool = False) -> None:
        """Favourite track ids in one request, so hearts show on every list without asking per track; and
        the albums, artists and playlists in My Collection, for their pages' buttons."""
        if not force and time.time() - self.favourites_at < FAVOURITES_TTL:
            return
        try:
            ids = await self._get(f"users/{self._user()}/favorites/ids")
            self.favourites = {str(x) for x in (ids or {}).get("TRACK", [])}
            self.collection = {kind: {str(x) for x in (ids or {}).get(key, [])}
                               for kind, (key, _, _) in SAVE_KINDS.items()}
            self.favourites_at = time.time()
        except ServiceError as e:
            debuglog.debug("tidal", "favourites unavailable", error=str(e))

    # --- browsing ---

    def _module_section(self, module: dict[str, Any]) -> Optional[dict[str, Any]]:
        """A row of TIDAL's home page, for the module types MusicDeck can show."""
        items = (module.get("pagedList") or {}).get("items") or []
        kind = module.get("type")
        title = module.get("title") or ""
        if kind == "TRACK_LIST":
            tracks = self._tracks(items)
            return models.section(title, [], tracks) if tracks else None
        mappers = {"ALBUM_LIST": self._album, "PLAYLIST_LIST": self._playlist, "ARTIST_LIST": self._artist,
                   "MIX_LIST": self._mix}
        mapper = mappers.get(kind or "")
        if not mapper:
            return None
        collections = [mapper(i) for i in items if isinstance(i, dict)]
        return models.section(title, collections) if collections else None

    async def home(self) -> list[dict[str, Any]]:
        user = self._user()
        await self._load_favourites()
        playlists, albums, page = await asyncio.gather(
            self._get(f"users/{user}/playlistsAndFavoritePlaylists", limit=50, order="DATE", orderDirection="DESC"),
            self._get(f"users/{user}/favorites/albums", limit=50, order="DATE", orderDirection="DESC"),
            self._get("pages/home", deviceType="BROWSER", locale="en_US"),
            return_exceptions=True)
        sections = []
        if isinstance(playlists, dict):
            items = [self._playlist(i["playlist"]) for i in playlists.get("items", []) if i.get("playlist")]
            if items:
                sections.append(models.section("Your playlists", items))
        if isinstance(albums, dict):
            items = [self._album(i["item"]) for i in albums.get("items", []) if i.get("item")]
            if items:
                sections.append(models.section("Favourite albums", items))
        if isinstance(page, dict):
            for row in page.get("rows") or []:
                for module in row.get("modules") or []:
                    section = self._module_section(module)
                    if section:
                        sections.append(section)
        sections.append(models.section("Library", [models.collection("favourite-tracks", "list", "Favourite tracks")]))
        if all(isinstance(r, Exception) for r in (playlists, albums, page)):
            raise playlists  # type: ignore[misc]
        return sections

    # --- media center ---

    TABS = (("home", "Home"), ("playlists", "Playlists"), ("mixes", "Your mixes"), ("albums", "Albums"),
            ("artists", "Artists"), ("songs", "Songs"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            moved = ("Your playlists", "Favourite albums")
            return [s for s in await self.home() if s["title"] not in moved and not models.is_categories(s)]
        if tab_id in ("playlists", "mixes", "albums", "artists", "songs"):
            # the library's parts, each its own tile
            sections = await self.tab("library")
            fixed = {"Playlists", "Albums", "Artists", "Favourite tracks"}
            if tab_id == "mixes":
                mixes = [s for s in sections if s["title"] not in fixed]
                return mixes or [models.notice("No mixes yet: TIDAL makes them as you listen")]
            title = {"playlists": "Playlists", "albums": "Albums", "artists": "Artists", "songs": "Favourite tracks"}
            return [{**s, "display": "grid"} for s in sections if s["title"] == title[tab_id]]
        if tab_id == "library":
            user = self._user()
            await self._load_favourites()
            playlists, albums, artists, tracks, mixes = await asyncio.gather(
                self._all(f"users/{user}/playlistsAndFavoritePlaylists", limit=500, order="DATE",
                          orderDirection="DESC"),
                self._all(f"users/{user}/favorites/albums", limit=500, order="DATE", orderDirection="DESC"),
                self._all(f"users/{user}/favorites/artists", limit=200, order="DATE", orderDirection="DESC"),
                self.open("list", "favourite-tracks"),
                self._get("pages/my_collection_my_mixes", deviceType="BROWSER", locale="en_US"),
                return_exceptions=True)
            ok = lambda r: r if isinstance(r, list) else []  # noqa: E731
            sections = [models.section("Playlists", [self._playlist(i["playlist"]) for i in ok(playlists)
                                                     if i.get("playlist")], display="grid", mine=True)]
            if isinstance(mixes, dict):
                for row in mixes.get("rows") or []:
                    for module in row.get("modules") or []:
                        section = self._module_section(module)
                        if section:
                            sections.append({**section, "title": section["title"] or "Your mixes"})
            sections += [models.section("Albums", [self._album(i["item"]) for i in ok(albums) if i.get("item")],
                                        display="grid"),
                         models.section("Artists", [self._artist(i["item"]) for i in ok(artists) if i.get("item")]),
                         models.section("Favourite tracks", [], tracks["tracks"] if isinstance(tracks, dict) else [],
                                        display="grid")]
            return sections
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        await self._load_favourites()
        if kind == "list" and id == "favourite-tracks":
            items = await self._all(f"users/{self._user()}/favorites/tracks", order="DATE", orderDirection="DESC")
            tracks = self._tracks(items)
            for t in tracks:
                t["starred"] = True
            return models.page("Favourite tracks", models.count(len(tracks), "track"), tracks=tracks)
        if kind == "album":
            a, items = await asyncio.gather(self._get(f"albums/{id}"), self._all(f"albums/{id}/tracks"))
            c = self._album(a)
            return models.page(c["title"], c["subtitle"], c["art"], tracks=self._tracks(items),
                               save=models.saved(self._saved("album", id)))
        if kind == "artist":
            a, top, albums, singles = await asyncio.gather(
                self._get(f"artists/{id}"), self._get(f"artists/{id}/toptracks", limit=10),
                self._get(f"artists/{id}/albums", limit=50),
                self._get(f"artists/{id}/albums", limit=50, filter="EPSANDSINGLES"), return_exceptions=True)
            if isinstance(a, Exception):
                raise a
            releases = [x for r in (albums, singles) if isinstance(r, dict) for x in r.get("items", [])]
            releases.sort(key=lambda x: x.get("releaseDate") or "", reverse=True)
            c = self._artist(a)
            return models.page(c["title"], c["subtitle"], c["art"], collections=[self._album(x) for x in releases],
                               tracks=self._tracks(top.get("items", [])) if isinstance(top, dict) else [],
                               save=models.saved(self._saved("artist", id)))
        if kind == "playlist" and id.startswith("mix:"):
            page = await self._get("pages/mix", mixId=id[4:], deviceType="BROWSER", locale="en_US")
            title, tracks = page.get("title") or "Mix", []
            for row in page.get("rows") or []:
                for module in row.get("modules") or []:
                    if module.get("type") == "TRACK_LIST":
                        tracks += self._tracks((module.get("pagedList") or {}).get("items") or [])
                    elif module.get("type") == "MIX_HEADER":
                        title = (module.get("mix") or {}).get("title") or title
            return models.page(title, models.count(len(tracks), "track"),
                               next((t["art"] for t in tracks if t.get("art")), None), tracks=tracks)
        if kind == "playlist":
            p, items = await asyncio.gather(self._get(f"playlists/{id}"), self._all(f"playlists/{id}/items"))
            c = self._playlist(p)
            # the user's own playlists are in My Collection already: no button
            own = str((p.get("creator") or {}).get("id")) == self._user()
            return models.page(c["title"], c["subtitle"], c["art"], tracks=self._tracks(items),
                               save=None if own else models.saved(self._saved("playlist", id)))
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        await self._load_favourites()
        data = await self._get("search", query=query, types="ARTISTS,ALBUMS,TRACKS,PLAYLISTS", limit=50)
        artists = [self._artist(a) for a in (data.get("artists") or {}).get("items", [])[:10]]
        albums = [self._album(a) for a in (data.get("albums") or {}).get("items", [])]
        playlists = [self._playlist(p) for p in (data.get("playlists") or {}).get("items", [])]
        return models.page(f"Results for “{query}”", collections=artists + albums + playlists,
                           tracks=self._tracks((data.get("tracks") or {}).get("items", [])), tracks_first=True)

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        if starred:
            await self._request("POST", f"users/{self._user()}/favorites/tracks", data={"trackId": track_id})
            self.favourites.add(track_id)
        else:
            await self._request("DELETE", f"users/{self._user()}/favorites/tracks/{track_id}")
            self.favourites.discard(track_id)

    SAVES = frozenset(SAVE_KINDS)

    def _saved(self, kind: str, id: str) -> Optional[bool]:
        return None if self.collection is None else id in self.collection[kind]

    async def save(self, kind: str, id: str, on: bool) -> None:
        if not TIDAL_ID.match(id):
            raise ServiceError("Not found on TIDAL")
        _, path, field = SAVE_KINDS[kind]
        if on:
            await self._request("POST", f"users/{self._user()}/favorites/{path}", data={field: id})
        else:
            await self._request("DELETE", f"users/{self._user()}/favorites/{path}/{id}")
        if self.collection is not None:
            (self.collection[kind].add if on else self.collection[kind].discard)(id)

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        data = await self._get(f"tracks/{track['id']}/radio", limit=100)
        return [t for t in self._tracks(data.get("items", [])) if t["id"] != track["id"]]

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            data = await self._get(f"tracks/{track['id']}/lyrics")
        except ServiceError:
            return None
        source = f"TIDAL · {data['lyricsProvider']}" if data.get("lyricsProvider") else "TIDAL"
        if data.get("subtitles"):
            return lyrics.parse_lrc(data["subtitles"], source)
        return lyrics.plain(data.get("lyrics") or "", source)

    async def user_playlists(self) -> list[dict[str, Any]]:
        items = await self._all(f"users/{self._user()}/playlists", limit=500)
        return [self._playlist(p) for p in items]

    async def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        # TIDAL only accepts edits against the playlist version it last handed out (its ETag)
        _, headers = await self._request("GET", f"playlists/{playlist_id}", want_headers=True)
        await self._request("POST", f"playlists/{playlist_id}/items",
                            data={"trackIds": ",".join(track_ids), "onDupes": "SKIP", "onArtifactNotFound": "SKIP"},
                            headers={"If-None-Match": headers.get("ETag", "")})

    async def create_playlist(self, name: str, track_ids: list[str]) -> dict[str, Any]:
        created = await self._request("POST", f"users/{self._user()}/playlists", data={"title": name, "description": ""})
        if not (created or {}).get("uuid"):
            raise ServiceError("TIDAL didn't make the playlist")
        if track_ids:
            await self.add_to_playlist(created["uuid"], track_ids)
        return self._playlist(created)

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        quality = self.config.get("quality") or DEFAULT_QUALITY
        info = await self._get(f"tracks/{track_id}/playbackinfopostpaywall", playbackmode="STREAM",
                               audioquality=quality, assetpresentation="FULL")
        gain = models.gain(info.get("trackReplayGain"), info.get("albumReplayGain"),
                           info.get("trackPeakAmplitude"), info.get("albumPeakAmplitude"))
        manifest = base64.b64decode(info.get("manifest") or "").decode("utf-8", "replace")
        kind = info.get("manifestMimeType")
        debuglog.debug("tidal", "stream", track=track_id, quality=info.get("audioQuality"), manifest=kind,
                       bits=info.get("bitDepth"), rate=info.get("sampleRate"))
        # signed links; resolve again when replaying later
        expires = time.time() + 20 * 60
        if kind == "application/vnd.tidal.bts":
            bts = json.loads(manifest)
            if (bts.get("encryptionType") or "NONE") != "NONE":
                raise ServiceError("TIDAL only offers this track encrypted, which MusicDeck can't play")
            urls = bts.get("urls") or []
            if not urls:
                raise ServiceError("TIDAL didn't return a stream for this track")
            return StreamSource(url=urls[0], content_type=bts.get("mimeType"), gain=gain, expires=expires)
        if kind == "application/dash+xml":
            if "ContentProtection" in manifest:
                raise ServiceError("TIDAL only offers this track encrypted, which MusicDeck can't play")
            return StreamSource(playlist=dash_to_hls(manifest), hls=True, gain=gain, expires=expires)
        raise ServiceError(f"TIDAL sent an unknown stream type ({kind})")

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith("https://"):
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id)


class TidalLogin(SignIn):
    """TIDAL's device login: a short code the user approves on link.tidal.com, polled until done."""

    def __init__(self) -> None:
        super().__init__()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        async with _session() as session:
            try:
                async with session.post(AUTH + "device_authorization",
                                        data={"client_id": CLIENT_ID, "scope": SCOPE}) as resp:
                    data = await resp.json(content_type=None)
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach TIDAL: {e}") from e
        if "deviceCode" not in data:
            raise ServiceError(f"TIDAL sign-in failed: {data.get('error_description') or data.get('error') or resp.status}")
        url = data.get("verificationUriComplete") or f"{data.get('verificationUri')}/{data.get('userCode')}"
        self.url = url if url.startswith("http") else f"https://{url}"
        self.status = "waiting"
        self._task = asyncio.get_event_loop().create_task(
            self._poll(data["deviceCode"], int(data.get("interval") or 2), int(data.get("expiresIn") or 300)))

    async def _poll(self, device_code: str, interval: int, expires_in: int) -> None:
        try:
            deadline = time.time() + expires_in
            async with _session() as session:
                while True:
                    if time.time() > deadline:
                        raise ServiceError("The sign-in link expired; start again")
                    await asyncio.sleep(interval)
                    status, data = await _token_request(session, {
                        "device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "scope": SCOPE})
                    if status == 200 and "access_token" in data:
                        break
                    if data.get("error") == "expired_token":
                        raise ServiceError("The sign-in link expired; start again")
                headers = {"Authorization": f"Bearer {data['access_token']}"}
                async with session.get(API + "sessions", headers=headers) as resp:
                    sess = await resp.json(content_type=None)
                user_id, country = str(sess.get("userId") or ""), sess.get("countryCode") or "US"
                params = {"countryCode": country}
                async with session.get(f"{API}users/{user_id}", params=params, headers=headers) as resp:
                    user = await resp.json(content_type=None) if resp.status == 200 else {}
                async with session.get(f"{API}users/{user_id}/subscription", params=params, headers=headers) as resp:
                    sub = await resp.json(content_type=None) if resp.status == 200 else {}
            if not user_id:
                raise ServiceError("TIDAL didn't say which account signed in")
            if sub and not (sub.get("subscription") or {}).get("type"):
                raise ServiceError("This TIDAL account has no streaming subscription")
            name = user.get("firstName") or user.get("username") or user.get("email") or ""
            self.result = {"refresh_token": data.get("refresh_token", ""), "user_id": user_id, "country_code": country,
                           "username": name}
            self.status = "done"
            debuglog.debug("tidal", "signed in", user_id=user_id, country=country)
        except asyncio.CancelledError:
            self.status, self.error = "error", "Sign-in cancelled"
        except ServiceError as e:
            self.status, self.error = "error", str(e)
        except Exception as e:
            self.status, self.error = "error", f"Sign-in failed: {e}"

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
