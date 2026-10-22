"""
Jellyfin, through its REST API (the one Jellyfin's own web and mobile apps use).

Sign-in is either Jellyfin's Quick Connect (MusicDeck shows a code, the user approves it in a
Jellyfin app they're already signed in to) or a username and password exchanged for an access
token on save. Only the token is stored. Requests carry the token in the Authorization header,
including audio and artwork, which the stream server proxies, so it never appears in a URL.

Audio is the original file (/universal lets the server transcode only what Chromium can't play),
with byte ranges, so seeking and every MusicDeck feature work as for local files.
"""
import asyncio
import json
import platform
import secrets as pysecrets
import time
from typing import Any, Optional

import aiohttp

from .. import debuglog, models
from ..net import tls
from .base import MusicService, ServiceError, SignIn, StreamSource

CLIENT = "MusicDeck"
VERSION = "0.1.0"
TICKS = 10_000_000  # Jellyfin times are in 100 ns ticks
# what Steam's browser engine decodes; anything else is transcoded to MP3 by the server
CONTAINERS = "opus,webm,mp3,aac,m4a,m4b,flac,webma,wav,ogg"
QUICK_CONNECT_TIMEOUT = 10 * 60
LIST_LIMIT = 500


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls()),
        timeout=aiohttp.ClientTimeout(total=20))


def _normalise_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


def _auth_header(device_id: str, token: str = "") -> dict[str, str]:
    """Jellyfin identifies every client by this header (and lists it under Devices)."""
    value = (f'MediaBrowser Client="{CLIENT}", Device="{platform.node() or "Steam Deck"}", '
             f'DeviceId="{device_id}", Version="{VERSION}"')
    if token:
        value += f', Token="{token}"'
    return {"Authorization": value}


async def _call(session: aiohttp.ClientSession, method: str, url: str, device_id: str, token: str = "",
                server: str = "Jellyfin", **kwargs: Any) -> Any:
    """One API request; `server` names the kind of server in error messages (Jellyfin or Emby)."""
    try:
        async with session.request(method, url, headers=_auth_header(device_id, token), **kwargs) as resp:
            if resp.status == 401:
                raise ServiceError(f"{server} didn't accept the sign-in; edit the source and sign in again")
            if resp.status == 404:
                raise ServiceError(f"Not found on the {server} server")
            if resp.status >= 400:
                raise ServiceError(f"{server} returned HTTP {resp.status}")
            text = await resp.text()
            return json.loads(text) if text.strip() else None
    except aiohttp.ClientError as e:
        raise ServiceError(f"Could not reach the {server} server: {e}") from e


class JellyfinService(MusicService):
    """config: {"url", "device_id", "user_id", "username", "token"}

    Emby (emby.py) shares this API, being the project Jellyfin forked from; it overrides the few
    places where they differ.
    """

    type = "jellyfin"
    display_name = "Jellyfin"
    features = frozenset({"star", "scrobble", "lyrics", "radio", "playlists"})
    downloadable = True
    # music playlists only (Emby reads this filter as "or" and returns everything; see emby.py)
    PLAYLIST_FILTER: dict[str, str] = {"MediaTypes": "Audio"}
    SIGN_IN_HINT = "Sign in with Quick Connect, or enter your username and password"

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        return f"{config['url']}|{config['user_id']}" if config.get("url") and config.get("user_id") else None

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def sign_in(cls, options: dict[str, Any], runtime_dir: str) -> SignIn:
        return JellyfinQuickConnect(options.get("url") or "")

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """A typed password becomes an access token here and is dropped; a Quick Connect sign-in
        arrives with its token already; editing without either keeps the stored token."""
        url = _normalise_url(config.get("url") or (previous or {}).get("url") or "")
        if not url:
            raise ServiceError("Enter the server address")
        # a Quick Connect token belongs to the device id it was issued to
        device_id = ((config.get("token") and config.get("device_id")) or (previous or {}).get("device_id")
                     or pysecrets.token_hex(16))
        result = {"url": url, "device_id": device_id, "username": (config.get("username") or "").strip()}
        if config.get("token"):  # Quick Connect
            result.update(token=config["token"], user_id=config.get("user_id"),
                          username=config.get("username") or result["username"])
        elif "password" in config and result["username"]:
            async with _session() as session:
                data = await _call(session, "POST", f"{url}/Users/AuthenticateByName", device_id,
                                   server=cls.display_name,
                                   json={"Username": result["username"], "Pw": config.get("password") or ""})
            result.update(token=data["AccessToken"], user_id=data["User"]["Id"])
        elif previous and previous.get("token") and previous.get("url") == url:
            result.update(token=previous["token"], user_id=previous.get("user_id"),
                          username=result["username"] or previous.get("username", ""))
        else:
            raise ServiceError(cls.SIGN_IN_HINT)
        return result

    async def start(self) -> None:
        self.session = _session()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    # --- API ---

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        assert self.session
        return await _call(self.session, method, self.config["url"] + path, self.config.get("device_id", ""),
                           self.config.get("token", ""), server=self.display_name, **kwargs)

    async def _get(self, path: str, **params: Any) -> Any:
        query = {k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items() if v is not None}
        return await self._request("GET", path, params=query)

    async def _items(self, **params: Any) -> list[dict[str, Any]]:
        data = await self._get("/Items", userId=self.config.get("user_id"), Recursive=True,
                               Fields="PrimaryImageAspectRatio,ProductionYear,ChildCount", **params)
        return (data or {}).get("Items") or []

    async def _item(self, id: str) -> dict[str, Any]:
        """One item (album, artist, playlist) as the signed-in user sees it."""
        return await self._get(f"/Items/{id}", userId=self.config.get("user_id"))

    async def test(self) -> str:
        info = await self._get("/System/Info")
        name = self.display_name
        return f"Signed in as {self.config.get('username') or name + ' user'} · {info.get('ServerName', name)} {info.get('Version', '')}".strip()

    # --- mapping ---

    def _art(self, item: dict[str, Any]) -> Optional[str]:
        if "Primary" in (item.get("ImageTags") or {}):
            return item["Id"]
        return item.get("AlbumId") if item.get("AlbumPrimaryImageTag") else None

    def _track(self, t: dict[str, Any]) -> dict[str, Any]:
        artists = t.get("ArtistItems") or []
        return models.track(
            t["Id"], t.get("Name", ""), ", ".join(t.get("Artists") or []) or t.get("AlbumArtist", ""),
            t.get("Album", ""), (t.get("RunTimeTicks") or 0) / TICKS, art=self._art(t), album_id=t.get("AlbumId"),
            artist_id=artists[0]["Id"] if artists else None,
            starred=bool((t.get("UserData") or {}).get("IsFavorite")),
            gain=models.gain(t.get("NormalizationGain")))

    def _album(self, a: dict[str, Any]) -> dict[str, Any]:
        sub = " · ".join(str(x) for x in [a.get("AlbumArtist"), a.get("ProductionYear")] if x)
        return models.collection(a["Id"], "album", a.get("Name", ""), sub, self._art(a))

    def _artist(self, a: dict[str, Any]) -> dict[str, Any]:
        return models.collection(a["Id"], "artist", a.get("Name", ""), "Artist", self._art(a))

    def _playlist(self, p: dict[str, Any]) -> dict[str, Any]:
        count = p.get("ChildCount")
        return models.collection(p["Id"], "playlist", p.get("Name", ""),
                                 models.count(count, "track") if count is not None else "Playlist", self._art(p))

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        jobs = {
            "playlists": self._items(IncludeItemTypes="Playlist", SortBy="SortName", **self.PLAYLIST_FILTER),
            "recent": self._items(IncludeItemTypes="Audio", SortBy="DatePlayed", SortOrder="Descending",
                                  Filters="IsPlayed", Limit=30),
            "added": self._items(IncludeItemTypes="MusicAlbum", SortBy="DateCreated", SortOrder="Descending", Limit=20),
            "frequent": self._items(IncludeItemTypes="MusicAlbum", SortBy="PlayCount", SortOrder="Descending",
                                    Filters="IsPlayed", Limit=20),
            "favourites": self._items(IncludeItemTypes="MusicAlbum", Filters="IsFavorite", SortBy="SortName", Limit=50),
        }
        got = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
        if all(isinstance(v, Exception) for v in got.values()):
            raise next(iter(got.values()))  # type: ignore[misc]
        ok = {k: v for k, v in got.items() if isinstance(v, list)}
        sections = []
        if ok.get("playlists"):
            sections.append(models.section("Playlists", [self._playlist(p) for p in ok["playlists"]]))
        if ok.get("recent"):
            sections.append(models.section("Recently played", [], [self._track(t) for t in ok["recent"]]))
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

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list":
            if id == "favourite-tracks":
                tracks = [self._track(t) for t in await self._items(IncludeItemTypes="Audio", Filters="IsFavorite",
                                                                     SortBy="SortName", Limit=LIST_LIMIT)]
                return models.page("Favourite tracks", models.count(len(tracks), "track"), tracks=tracks)
            if id == "artists":
                data = await self._get("/Artists/AlbumArtists", userId=self.config.get("user_id"), Limit=LIST_LIMIT)
                return models.page("Artists", collections=[self._artist(a) for a in (data or {}).get("Items", [])])
            if id in ("albums", "random"):
                albums = await self._items(IncludeItemTypes="MusicAlbum", Limit=LIST_LIMIT,
                                           SortBy="Random" if id == "random" else "SortName")
                return models.page("Random albums" if id == "random" else "All albums",
                                   collections=[self._album(a) for a in albums])
            raise ServiceError(f"Unknown list: {id}")
        if kind == "album":
            album = await self._item(id)
            tracks = await self._items(ParentId=id, IncludeItemTypes="Audio", SortBy="ParentIndexNumber,IndexNumber")
            c = self._album(album)
            return models.page(c["title"], c["subtitle"], c["art"], tracks=[self._track(t) for t in tracks])
        if kind == "artist":
            artist, albums, top = await asyncio.gather(
                self._item(id),
                self._items(ArtistIds=id, IncludeItemTypes="MusicAlbum", SortBy="ProductionYear,SortName",
                            SortOrder="Descending"),
                self._items(ArtistIds=id, IncludeItemTypes="Audio", SortBy="PlayCount,SortName",
                            SortOrder="Descending", Limit=10))
            c = self._artist(artist)
            return models.page(c["title"], models.count(len(albums), "album"), c["art"],
                               collections=[self._album(a) for a in albums], tracks=[self._track(t) for t in top])
        if kind == "playlist":
            playlist = await self._item(id)
            data = await self._get(f"/Playlists/{id}/Items", userId=self.config.get("user_id"))
            tracks = [self._track(t) for t in (data or {}).get("Items", []) if t.get("Type") == "Audio"]
            c = self._playlist({**playlist, "ChildCount": len(tracks)})
            return models.page(c["title"], c["subtitle"], c["art"], tracks=tracks)
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        tracks, albums, artists, playlists = await asyncio.gather(
            self._items(searchTerm=query, IncludeItemTypes="Audio", Limit=50),
            self._items(searchTerm=query, IncludeItemTypes="MusicAlbum", Limit=20),
            self._get("/Artists", userId=self.config.get("user_id"), searchTerm=query, Limit=10),
            self._items(searchTerm=query, IncludeItemTypes="Playlist", Limit=10, **self.PLAYLIST_FILTER))
        return models.page(f"Results for “{query}”",
                           collections=[self._artist(a) for a in (artists or {}).get("Items", [])]
                           + [self._album(a) for a in albums] + [self._playlist(p) for p in playlists],
                           tracks=[self._track(t) for t in tracks], tracks_first=True)

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        url = (f"{self.config['url']}/Audio/{track_id}/universal?UserId={self.config.get('user_id')}"
               f"&DeviceId={self.config.get('device_id')}&Container={CONTAINERS}&TranscodingContainer=mp3"
               f"&TranscodingProtocol=http&AudioCodec=mp3&MaxStreamingBitrate=999999999")
        return StreamSource(url=url, headers=_auth_header(self.config.get("device_id", ""), self.config.get("token", "")))

    async def art(self, art_id: str) -> StreamSource:
        return StreamSource(url=f"{self.config['url']}/Items/{art_id}/Images/Primary?maxHeight=300&quality=90",
                            headers=_auth_header(self.config.get("device_id", ""), self.config.get("token", "")))

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        method = "POST" if starred else "DELETE"
        try:
            await self._request(method, f"/UserFavoriteItems/{track_id}", params={"userId": self.config.get("user_id")})
        except ServiceError:
            # servers before 10.9 only have the per-user path
            await self._request(method, f"/Users/{self.config.get('user_id')}/FavoriteItems/{track_id}")

    async def scrobble(self, track_id: str, submission: bool) -> None:
        """Now playing shows in Jellyfin's dashboard; a finished listen marks the track played,
        which raises its play count and feeds "Recently played" and "Most played"."""
        if submission:
            await self._request("POST", f"/UserPlayedItems/{track_id}", params={"userId": self.config.get("user_id")})
        else:
            # the media source and session ids are optional for Jellyfin; Emby refuses the report without them
            await self._request("POST", "/Sessions/Playing", json={
                "ItemId": track_id, "MediaSourceId": track_id, "PlaySessionId": pysecrets.token_hex(8),
                "CanSeek": True, "PlayMethod": "DirectStream"})

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            data = await self._get(f"/Audio/{track['id']}/Lyrics")
        except ServiceError:
            return None  # no lyrics, or a server before 10.9
        entries = (data or {}).get("Lyrics") or []
        synced = bool(((data or {}).get("Metadata") or {}).get("IsSynced")) or any(
            (e.get("Start") or 0) > 0 for e in entries)
        lines = [{"t": (e.get("Start") or 0) / TICKS if synced else None, "text": e.get("Text", "")} for e in entries]
        if not any(line["text"].strip() for line in lines):
            return None
        return {"synced": synced, "lines": lines, "source": "Jellyfin"}

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        data = await self._get(f"/Items/{track['id']}/InstantMix", userId=self.config.get("user_id"), Limit=50)
        return [self._track(t) for t in (data or {}).get("Items", []) if t["Id"] != track["id"]]

    async def user_playlists(self) -> list[dict[str, Any]]:
        return [self._playlist(p) for p in await self._items(IncludeItemTypes="Playlist", SortBy="SortName",
                                                             **self.PLAYLIST_FILTER)]

    async def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        await self._request("POST", f"/Playlists/{playlist_id}/Items",
                            params={"ids": ",".join(track_ids), "userId": self.config.get("user_id")})


class JellyfinQuickConnect(SignIn):
    """Jellyfin's Quick Connect: the server issues a code, the user approves it in a Jellyfin app
    that is already signed in (Settings → Quick Connect), and the secret becomes a token."""

    def __init__(self, server: str):
        super().__init__()  # no url: the code is shown in MusicDeck's dialog instead
        self.server = _normalise_url(server)
        self.device_id = pysecrets.token_hex(16)
        self._secret = ""
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if not self.server:
            raise ServiceError("Enter the server address first")
        async with _session() as session:
            enabled = await _call(session, "GET", f"{self.server}/QuickConnect/Enabled", self.device_id)
            if enabled is False:
                raise ServiceError("Quick Connect is turned off on this server; sign in with your password")
            data = await _call(session, "POST", f"{self.server}/QuickConnect/Initiate", self.device_id)
        self._secret, self.code = data["Secret"], data["Code"]
        self.status = "waiting"
        self._task = asyncio.get_event_loop().create_task(self._poll())

    async def _poll(self) -> None:
        try:
            deadline = time.time() + QUICK_CONNECT_TIMEOUT
            async with _session() as session:
                while True:
                    await asyncio.sleep(2)
                    if time.time() > deadline:
                        raise ServiceError("The code expired; start again")
                    state = await _call(session, "GET", f"{self.server}/QuickConnect/Connect", self.device_id,
                                        params={"secret": self._secret})
                    if state and state.get("Authenticated"):
                        break
                data = await _call(session, "POST", f"{self.server}/Users/AuthenticateWithQuickConnect", self.device_id,
                                   json={"Secret": self._secret})
            self.result = {"url": self.server, "device_id": self.device_id, "token": data["AccessToken"],
                           "user_id": data["User"]["Id"], "username": data["User"].get("Name", "")}
            self.status = "done"
            debuglog.debug("jellyfin", "signed in with Quick Connect", user=self.result["username"])
        except asyncio.CancelledError:
            self.status, self.error = "error", "Sign-in cancelled"
        except ServiceError as e:
            self.status, self.error = "error", str(e)
        except Exception as e:
            self.status, self.error = "error", f"Sign-in failed: {e}"

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
