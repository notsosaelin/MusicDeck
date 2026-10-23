"""
Navidrome or any Subsonic-compatible server, through the Subsonic REST API (1.16.1) and the
OpenSubsonic extensions where the server has them (lyrics, ReplayGain). The password is turned into
the spec's salted token on save and never stored. Audio is the original file (format=raw).
"""
import asyncio
import hashlib
import secrets
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from .. import models
from ..net import tls
from .base import MusicService, ServiceError, StreamSource, rows_or_tiles

API_VERSION = "1.16.1"
CLIENT_NAME = "MusicDeck"


class SubsonicService(MusicService):
    """Navidrome or any Subsonic-compatible server.

    config: {"url", "username", "salt", "token"}  (token = md5(password + salt), per the Subsonic spec;
    the plain password only exists in the UI form and is converted by prepare_config)
    """

    type = "subsonic"
    display_name = "Navidrome / Subsonic"
    features = frozenset({"star", "scrobble", "lyrics", "radio", "queue_sync", "playlists", "browse"})
    downloadable = True

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        return f"{config['url']}|{config['username'].lower()}" if config.get("url") and config.get("username") else None

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        url = (config.get("url") or "").strip().rstrip("/")
        if url and "://" not in url:
            url = "https://" + url
        result: dict[str, Any] = {"url": url, "username": (config.get("username") or "").strip()}
        password = config.get("password")
        if password:
            salt = secrets.token_hex(8)
            result.update(salt=salt, token=hashlib.md5((password + salt).encode()).hexdigest())
        elif previous:
            # editing without retyping the password keeps the old credentials
            result.update(salt=previous.get("salt"), token=previous.get("token"))
        return result

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls()),
            timeout=aiohttp.ClientTimeout(total=20))

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    def _url(self, method: str, **params: Any) -> str:
        query = {"u": self.config.get("username", ""), "t": self.config.get("token", ""),
                 "s": self.config.get("salt", ""), "v": API_VERSION, "c": CLIENT_NAME, "f": "json"}
        query.update({k: v for k, v in params.items() if v is not None})
        return f"{self.config['url']}/rest/{method}?{urlencode(query, doseq=True)}"

    async def _call(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.config.get("url"):
            raise ServiceError("No server URL configured")
        assert self.session
        try:
            async with self.session.get(self._url(method, **params)) as resp:
                if resp.status != 200:
                    raise ServiceError(f"Server returned HTTP {resp.status}")
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise ServiceError(f"Could not reach server: {e}") from e
        data = body.get("subsonic-response", {})
        if data.get("status") != "ok":
            raise ServiceError(data.get("error", {}).get("message", "Unknown server error"))
        return data

    async def test(self) -> str:
        data = await self._call("ping")
        server = data.get("type") or "Subsonic"
        return f"Connected to {server.capitalize()} {data.get('serverVersion', '')}".strip()

    # --- mapping ---

    @staticmethod
    def _track(s: dict[str, Any]) -> dict[str, Any]:
        rg = s.get("replayGain") or {}  # OpenSubsonic; absent on plain Subsonic servers
        return models.track(
            s["id"], s.get("title", ""), s.get("displayArtist") or s.get("artist", ""), s.get("album", ""),
            s.get("duration", 0), art=s.get("coverArt"), album_id=s.get("albumId"), artist_id=s.get("artistId"),
            starred="starred" in s,
            gain=models.gain(rg.get("trackGain"), rg.get("albumGain"), rg.get("trackPeak"), rg.get("albumPeak")))

    @staticmethod
    def _album(a: dict[str, Any]) -> dict[str, Any]:
        subtitle = a.get("artist", "") + (f" · {a['year']}" if a.get("year") else "")
        return models.collection(a["id"], "album", a.get("name") or a.get("title", ""), subtitle,
                                 a.get("coverArt"))

    @staticmethod
    def _artist(a: dict[str, Any]) -> dict[str, Any]:
        count = a.get("albumCount")
        return models.collection(a["id"], "artist", a.get("name", ""),
                                 models.count(count, "album") if count is not None else "", a.get("coverArt"))

    @staticmethod
    def _playlist(p: dict[str, Any]) -> dict[str, Any]:
        return models.collection(p["id"], "playlist", p.get("name", ""), models.count(p.get("songCount", 0), "track"),
                                 p.get("coverArt"))

    # --- browsing ---

    async def _album_list(self, list_type: str, size: int = 20, **extra: Any) -> list[dict[str, Any]]:
        data = await self._call("getAlbumList2", type=list_type, size=size, **extra)
        return [self._album(a) for a in data.get("albumList2", {}).get("album", [])]

    async def home(self) -> list[dict[str, Any]]:
        playlists = (await self._call("getPlaylists")).get("playlists", {}).get("playlist", [])
        sections = []
        if playlists:
            sections.append(models.section("Playlists", [self._playlist(p) for p in playlists]))
        for title, list_type in [("Recently played", "recent"), ("Recently added", "newest"),
                                 ("Most played", "frequent"), ("Starred", "starred")]:
            items = await self._album_list(list_type)
            if items:
                sections.append(models.section(title, items))
        sections.append(models.section("Library", [
            models.collection("starred", "list", "Favourite tracks"),
            models.collection("alphabeticalByName", "list", "All albums"),
            models.collection("artists", "list", "Artists"),
            models.collection("random", "list", "Random albums"),
        ]))
        return sections

    async def browse(self) -> list[dict[str, Any]]:
        genres = (await self._call("getGenres")).get("genres", {}).get("genre", [])
        genres = sorted((g for g in genres if g.get("albumCount") and g.get("value")),
                        key=lambda g: -g["albumCount"])
        sections = []
        if genres:
            sections.append(models.section("Genres", [
                models.collection(f"genre:{g['value']}", "list", g["value"], models.count(g["albumCount"], "album"))
                for g in genres]))
        sections.append(models.section("Decades", [models.collection(f"decade:{d}", "list", f"{d}s")
                                                   for d in range(2020, 1950, -10)]))
        return sections

    # --- media center ---

    TABS = (("home", "Home"), ("albums", "Albums"), ("artists", "Artists"), ("genres", "Genres"),
            ("library", "Library"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            return [s for s in await self.home() if s["title"] != "Playlists" and not models.is_categories(s)]
        if tab_id == "albums":
            lists = [("Recently added", "newest"), ("Most played", "frequent"), ("Highest rated", "highest"),
                     ("Random", "random")]
            found = await asyncio.gather(*[self._album_list(t, 30) for _, t in lists],
                                         self._album_list("alphabeticalByName", 500), return_exceptions=True)
            sections = [models.section(title, albums) for (title, _), albums in zip(lists, found)
                        if isinstance(albums, list)]
            if isinstance(found[-1], list):
                sections.append(models.section("All albums", found[-1], display="grid"))
            return sections
        if tab_id == "artists":
            return [models.section("Artists", (await self.open("list", "artists"))["collections"], display="grid")]
        if tab_id == "genres":
            return [rows_or_tiles(s) for s in await self.browse()]
        if tab_id == "library":
            playlists, starred = await asyncio.gather(self._call("getPlaylists"), self._call("getStarred2"))
            starred = starred.get("starred2", {})
            return [models.section("Playlists", [self._playlist(p) for p in
                                                 playlists.get("playlists", {}).get("playlist", [])],
                                   display="grid", mine=True),
                    models.section("Starred albums", [self._album(a) for a in starred.get("album", [])]),
                    models.section("Starred artists", [self._artist(a) for a in starred.get("artist", [])]),
                    models.section("Starred tracks", [], [self._track(x) for x in starred.get("song", [])],
                                   display="grid")]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("genre:"):
            genre = id[6:]
            albums, songs = await asyncio.gather(
                self._album_list("byGenre", 500, genre=genre),
                self._call("getSongsByGenre", genre=genre, count=50))
            return models.page(genre, models.count(len(albums), "album"), collections=albums,
                               tracks=[self._track(x) for x in songs.get("songsByGenre", {}).get("song", [])])
        if kind == "list" and id.startswith("decade:") and id[7:].isdigit():
            start = int(id[7:])
            albums = await self._album_list("byYear", 500, fromYear=start, toYear=start + 9)
            return models.page(f"{start}s", models.count(len(albums), "album"), collections=albums)
        if kind == "list":
            if id == "artists":
                data = await self._call("getArtists")
                artists = [a for idx in data.get("artists", {}).get("index", []) for a in idx.get("artist", [])]
                return models.page("Artists", collections=[self._artist(a) for a in artists])
            if id == "starred":
                songs = (await self._call("getStarred2")).get("starred2", {}).get("song", [])
                return models.page("Favourite tracks", models.count(len(songs), "track"),
                                   tracks=[self._track(x) for x in songs])
            titles = {"alphabeticalByName": "All albums", "random": "Random albums"}
            if id not in titles:
                raise ServiceError(f"Unknown list: {id}")
            return models.page(titles[id], collections=await self._album_list(id, 500))
        if kind == "album":
            a = (await self._call("getAlbum", id=id))["album"]
            c = self._album(a)
            return models.page(c["title"], c["subtitle"], c["art"],
                               tracks=[self._track(s) for s in a.get("song", [])])
        if kind == "artist":
            a = (await self._call("getArtist", id=id))["artist"]
            albums = sorted(a.get("album", []), key=lambda x: x.get("year") or 0, reverse=True)
            try:
                top = (await self._call("getTopSongs", artist=a.get("name", ""), count=10)).get("topSongs", {})
                songs = top.get("song", [])
            except ServiceError:
                songs = []  # needs an external metadata agent (Last.fm) on the server
            return models.page(a.get("name", ""), models.count(len(albums), "album"), a.get("coverArt"),
                               collections=[self._album(x) for x in albums],
                               tracks=[self._track(x) for x in songs])
        if kind == "playlist":
            p = (await self._call("getPlaylist", id=id))["playlist"]
            return models.page(p.get("name", ""), "Playlist", p.get("coverArt"),
                               tracks=[self._track(s) for s in p.get("entry", [])])
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        data = await self._call("search3", query=query, artistCount=10, albumCount=20, songCount=50)
        result = data.get("searchResult3", {})
        return models.page(f"Results for “{query}”",
                           collections=[self._artist(a) for a in result.get("artist", [])]
                           + [self._album(a) for a in result.get("album", [])],
                           tracks=[self._track(s) for s in result.get("song", [])])

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        # format=raw: no server-side transcoding; Chromium handles mp3/flac/ogg/opus/aac natively
        return StreamSource(url=self._url("stream", id=track_id, format="raw"))

    async def art(self, art_id: str) -> StreamSource:
        return StreamSource(url=self._url("getCoverArt", id=art_id, size=300))

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        await self._call("star" if starred else "unstar", id=track_id)

    async def scrobble(self, track_id: str, submission: bool) -> None:
        # submission=false marks "now playing"; true records a finished play (play counts, Last.fm via server)
        await self._call("scrobble", id=track_id, submission=str(submission).lower())

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            data = await self._call("getLyricsBySongId", id=track["id"])
        except ServiceError:
            return None  # server without the OpenSubsonic songLyrics extension
        entries = data.get("lyricsList", {}).get("structuredLyrics", [])
        entries.sort(key=lambda e: not e.get("synced"))
        for entry in entries:
            lines = [{"t": (x["start"] / 1000) if entry.get("synced") and "start" in x else None,
                      "text": x.get("value", "")} for x in entry.get("line", [])]
            if any(line["text"].strip() for line in lines):
                return {"synced": bool(entry.get("synced")), "lines": lines, "source": "Navidrome"}
        return None

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        songs = (await self._call("getSimilarSongs2", id=track["id"], count=50)).get("similarSongs2", {}).get("song", [])
        if not songs and track.get("artist"):
            songs = (await self._call("getTopSongs", artist=track["artist"], count=50)).get("topSongs", {}).get("song", [])
        if not songs:
            songs = (await self._call("getRandomSongs", size=50)).get("randomSongs", {}).get("song", [])
        return [self._track(x) for x in songs if x["id"] != track["id"]]

    async def save_queue(self, track_ids: list[str], index: int, position: float) -> None:
        if not track_ids:
            return
        await self._call("savePlayQueue", id=track_ids, current=track_ids[max(index, 0)],
                         position=int(position * 1000))

    async def load_queue(self) -> Optional[dict[str, Any]]:
        q = (await self._call("getPlayQueue")).get("playQueue")
        if not q or not q.get("entry"):
            return None
        tracks = [self._track(x) for x in q["entry"]]
        current = q.get("current")
        index = next((i for i, t in enumerate(tracks) if t["id"] == current), 0)
        return {"tracks": tracks, "index": index, "position": (q.get("position") or 0) / 1000,
                "changed": q.get("changed"), "changed_by": q.get("changedBy")}

    async def user_playlists(self) -> list[dict[str, Any]]:
        playlists = (await self._call("getPlaylists")).get("playlists", {}).get("playlist", [])
        user = self.config.get("username")
        return [self._playlist(p) for p in playlists if not p.get("owner") or p.get("owner") == user]

    async def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        await self._call("updatePlaylist", playlistId=playlist_id, songIdToAdd=track_ids)
