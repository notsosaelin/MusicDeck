"""
SoundCloud through its v2 web API, the way NewPipe and other open-source clients do it: the public
web client's `client_id` is scraped from soundcloud.com's JS bundle (no API key or account), then
the same JSON endpoints the website uses serve search, artists, playlists, curated selections and
"related tracks" radio. Audio arrives as HLS, which the stream server rewrites for hls.js.
"""
import asyncio
import base64
import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import aiohttp

from .. import models
from ..cache import Cache
from ..net import tls
from .base import MusicService, ServiceError, StreamSource, rows_or_tiles

API = "https://api-v2.soundcloud.com"
# Genres for the Browse tab: (tag its search filters by, name shown). SoundCloud's per-genre charts are
# gone, so a genre is a tag search, tested to give playable tracks.
GENRES = [("hip-hop", "Hip-hop & Rap"), ("electronic", "Electronic"), ("pop", "Pop"), ("rnb", "R&B & Soul"),
          ("edm", "Dance & EDM"), ("house", "House"), ("deep house", "Deep House"), ("techno", "Techno"),
          ("dnb", "Drum & Bass"), ("dubstep", "Dubstep"), ("trap", "Trap"), ("phonk", "Phonk"), ("drill", "Drill"),
          ("jersey club", "Jersey Club"), ("hyperpop", "Hyperpop"), ("rock", "Rock"), ("indie", "Indie"),
          ("metal", "Metal"), ("lofi", "Lo-fi"), ("ambient", "Ambient"), ("jazz", "Jazz"), ("classical", "Classical"),
          ("piano", "Piano"), ("soundtrack", "Soundtrack"), ("folk", "Folk"), ("country", "Country"),
          ("latin", "Latin"), ("reggaeton", "Reggaeton"), ("afrobeats", "Afrobeats"), ("amapiano", "Amapiano"),
          ("reggae", "Reggae"), ("dancehall", "Dancehall"), ("k-pop", "K-pop"), ("disco", "Disco"),
          ("trance", "Trance")]
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
SCRIPT_SRC = re.compile(r'<script crossorigin src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"')
CLIENT_ID = re.compile(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"')
CLIENT_ID_TTL = 24 * 3600
# track objects kept to stream from (their stream links are fetched fresh each time)
TRACK_TTL = 6 * 3600
TRACKS_KEPT = 2000


def _signed_expiry(url: str) -> Optional[float]:
    """Expiry of a CloudFront-signed URL: the policy's DateLessThan, or the `expires` param."""
    query = parse_qs(urlparse(url).query)
    policy = (query.get("Policy") or [None])[0]
    if policy:
        try:
            # CloudFront's URL-safe base64 variant: '-' '_' '~' stand for '+' '=' '/'
            raw = base64.b64decode(policy.replace("-", "+").replace("_", "=").replace("~", "/"))
            statement = json.loads(raw)["Statement"][0]
            return float(statement["Condition"]["DateLessThan"]["AWS:EpochTime"])
        except (ValueError, KeyError, IndexError, TypeError):
            pass
    expires = (query.get("expires") or [None])[0]
    return float(expires) if expires and expires.isdigit() else None


def _art(url: Optional[str]) -> Optional[str]:
    # artwork URLs come in "-large" (100px); "-t300x300" is the size the UI wants
    return url.replace("-large.", "-t300x300.") if url else None



def _is_chart(section: dict[str, Any]) -> bool:
    """A home row of SoundCloud's charts ("Trending by genre")."""
    return any(word in section["title"].lower() for word in ("trending", "chart"))


class SoundCloudService(MusicService):
    """config: {} (no account needed)"""

    type = "soundcloud"
    display_name = "SoundCloud"
    features = frozenset({"radio", "browse"})
    downloadable = True

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_path = os.path.join(runtime_dir, "soundcloud-client.json")
        self.client_id: Optional[str] = None
        self.client_id_at = 0.0
        self._client_lock = asyncio.Lock()
        # tracks seen in listings, so stream() doesn't need another lookup for transcodings
        # SoundCloud's own track objects, which say how to stream them
        self._tracks: Cache[dict[str, Any]] = Cache(TRACK_TTL, TRACKS_KEPT)

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT},
            connector=aiohttp.TCPConnector(ssl=tls()),
            timeout=aiohttp.ClientTimeout(total=20))
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            self.client_id, self.client_id_at = cached["client_id"], cached["at"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            pass

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    # --- API plumbing ---

    async def _get_client_id(self, refresh: bool = False) -> str:
        async with self._client_lock:
            if self.client_id and not refresh and time.time() - self.client_id_at < CLIENT_ID_TTL:
                return self.client_id
            assert self.session
            try:
                async with self.session.get("https://soundcloud.com/") as resp:
                    html = await resp.text()
                # the id lives in one of the last bundles; search from the end
                for src in reversed(SCRIPT_SRC.findall(html)):
                    async with self.session.get(src) as resp:
                        match = CLIENT_ID.search(await resp.text())
                    if match:
                        self.client_id, self.client_id_at = match.group(1), time.time()
                        os.makedirs(self.runtime_dir, exist_ok=True)
                        with open(self.cache_path, "w", encoding="utf-8") as f:
                            json.dump({"client_id": self.client_id, "at": self.client_id_at}, f)
                        return self.client_id
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach SoundCloud: {e}") from e
            raise ServiceError("SoundCloud changed its website; couldn't find a client id")

    async def _get(self, path: str, **params: Any) -> Any:
        assert self.session
        for attempt in range(2):
            client_id = await self._get_client_id(refresh=attempt > 0)
            url = path if path.startswith("http") else API + path
            try:
                async with self.session.get(url, params={**params, "client_id": client_id}) as resp:
                    if resp.status in (401, 403) and attempt == 0:
                        continue  # stale client id: scrape a fresh one and retry once
                    if resp.status == 404:
                        raise ServiceError("Not found on SoundCloud")
                    if resp.status >= 400:
                        raise ServiceError(f"SoundCloud returned HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach SoundCloud: {e}") from e
        raise ServiceError("SoundCloud rejected the request")

    async def test(self) -> str:
        await self._get("/search/tracks", q="test", limit=1)
        return "Connected to SoundCloud"

    # --- mapping ---

    def _track(self, t: dict[str, Any]) -> dict[str, Any]:
        self._tracks.put(str(t["id"]), t)
        user = t.get("user") or {}
        meta = t.get("publisher_metadata") or {}
        artist = meta.get("artist") or user.get("username", "")
        return models.track(str(t["id"]), t.get("title", ""), artist, meta.get("album_title") or "",
                            (t.get("duration") or 0) / 1000, art=_art(t.get("artwork_url") or user.get("avatar_url")),
                            artist_id=str(user["id"]) if user.get("id") else None)

    @staticmethod
    def _user(u: dict[str, Any]) -> dict[str, Any]:
        followers = u.get("followers_count")
        subtitle = f"{followers:,} followers" if followers else ""
        return models.collection(str(u["id"]), "artist", u.get("username", ""), subtitle, _art(u.get("avatar_url")))

    @staticmethod
    def _playlist(p: dict[str, Any]) -> dict[str, Any]:
        if p.get("kind") == "system-playlist":
            # curated mixes ("Charts: Top 50", personalised stations) are addressed by URN
            return models.collection("urn:" + p["urn"], "playlist", p.get("title", ""),
                                     p.get("short_description") or "SoundCloud", _art(p.get("artwork_url")))
        user = (p.get("user") or {}).get("username", "")
        count = p.get("track_count")
        subtitle = " · ".join(x for x in [user, models.count(count, "track") if count else ""] if x)
        kind = "album" if p.get("is_album") else "playlist"
        return models.collection(str(p["id"]), kind, p.get("title", ""), subtitle,
                                 _art(p.get("artwork_url") or (p.get("tracks") or [{}])[0].get("artwork_url")))

    async def _hydrate(self, tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Playlists only embed the first few tracks fully; the rest are bare ids."""
        missing = [str(t["id"]) for t in tracks if "title" not in t]
        full: dict[str, dict[str, Any]] = {}
        for i in range(0, len(missing), 50):
            batch = await self._get("/tracks", ids=",".join(missing[i:i + 50]))
            full.update({str(t["id"]): t for t in batch})
        return [t if "title" in t else full.get(str(t["id"]), t) for t in tracks if "title" in t or str(t["id"]) in full]

    @staticmethod
    def _streams(t: dict[str, Any]) -> list[dict[str, Any]]:
        """The track's streams that can be played here. Go+ previews only offer 30-second snippets,
        and some label releases are only offered DRM-encrypted (cbc-/ctr-encrypted-hls), which needs
        a DRM system Steam's browser engine doesn't have."""
        return [tc for tc in (t.get("media") or {}).get("transcodings") or []
                if not tc.get("snipped") and "encrypted" not in (tc.get("format") or {}).get("protocol", "")]

    @classmethod
    def _playable(cls, t: dict[str, Any]) -> bool:
        return t.get("policy") not in ("SNIP", "BLOCK") and bool(cls._streams(t))

    def _tracks_of(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._track(t) for t in items if t.get("kind", "track") == "track" and self._playable(t)]

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        data = await self._get("/mixed-selections", limit=10)
        sections = []
        for selection in data.get("collection", []):
            items = (selection.get("items") or {}).get("collection", [])
            playlists = [self._playlist(p) for p in items if p.get("kind") in ("playlist", "system-playlist")]
            if playlists:
                sections.append(models.section(selection.get("title") or "Discover", playlists))
        return sections

    async def browse(self) -> list[dict[str, Any]]:
        return [models.section("Charts", [models.collection("chart:trending", "list", "Trending")]),
                models.section("Genres", [models.collection(f"genre:{tag}", "list", name) for tag, name in GENRES])]

    # --- media center ---

    TABS = (("home", "Home"), ("charts", "Charts"), ("genres", "Genres"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            return [s for s in await self.home() if not _is_chart(s)]
        if tab_id == "charts":
            # SoundCloud's own charts: what's trending now, and its playlists of what's trending by genre
            trending, home = await asyncio.gather(self.open("list", "chart:trending"), self.home(),
                                                  return_exceptions=True)
            sections = [s for s in home if _is_chart(s)] if isinstance(home, list) else []
            if isinstance(trending, dict) and trending["tracks"]:
                sections.append(models.section("Trending now", [], trending["tracks"], display="grid"))
            if not sections:
                raise trending if isinstance(trending, Exception) else ServiceError("No charts right now")
            return sections
        if tab_id == "genres":
            return [rows_or_tiles(s) for s in await self.browse() if s["title"] == "Genres"]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id == "chart:trending":
            chart = await self._get("/charts", kind="trending", genre="soundcloud:genres:all-music", limit=50)
            tracks = [entry.get("track") or {} for entry in chart.get("collection", [])]
            return models.page("Trending", "On SoundCloud", tracks=self._tracks_of(tracks))
        if kind == "list" and id.startswith("genre:"):
            tag = id[6:]
            # plenty of tracks: many are previews of Go+ tracks, which are left out
            tracks, playlists = await asyncio.gather(
                self._get("/search/tracks", q="", **{"filter.genre_or_tag": tag}, limit=80),
                self._get("/search/playlists_without_albums", q="", **{"filter.genre_or_tag": tag}, limit=12))
            return models.page(dict(GENRES).get(tag, tag.title()), "On SoundCloud",
                               collections=[self._playlist(p) for p in playlists.get("collection", [])],
                               tracks=self._tracks_of(tracks.get("collection", [])))
        if kind in ("playlist", "album"):
            if id.startswith("urn:"):
                p = await self._get(f"/system-playlists/{id[4:]}")
            else:
                p = await self._get(f"/playlists/{id}", representation="full")
            tracks = await self._hydrate(p.get("tracks", []))
            c = self._playlist(p)
            return models.page(c["title"], c["subtitle"], c["art"], tracks=self._tracks_of(tracks))
        if kind == "artist":
            user, top, playlists, albums = await asyncio.gather(
                self._get(f"/users/{id}"), self._get(f"/users/{id}/toptracks", limit=20),
                self._get(f"/users/{id}/playlists_without_albums", limit=20), self._get(f"/users/{id}/albums", limit=20))
            uploads = await self._get(f"/users/{id}/tracks", limit=50)
            collections = [self._playlist(p) for p in albums.get("collection", []) + playlists.get("collection", [])]
            # popular first, then the rest of their uploads
            seen = set()
            tracks = []
            for t in top.get("collection", []) + uploads.get("collection", []):
                if t["id"] not in seen:
                    seen.add(t["id"])
                    tracks.append(t)
            return models.page(user.get("username", ""), self._user(user)["subtitle"], _art(user.get("avatar_url")),
                               collections=collections, tracks=self._tracks_of(tracks))
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        tracks, users, playlists, albums = await asyncio.gather(
            self._get("/search/tracks", q=query, limit=40),
            self._get("/search/users", q=query, limit=6),
            self._get("/search/playlists_without_albums", q=query, limit=8),
            self._get("/search/albums", q=query, limit=6))
        return models.page(f"Results for “{query}”",
                           collections=[self._user(u) for u in users.get("collection", [])]
                           + [self._playlist(p) for p in albums.get("collection", []) + playlists.get("collection", [])],
                           tracks=self._tracks_of(tracks.get("collection", [])))

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        data = await self._get(f"/tracks/{track['id']}/related", limit=50)
        return self._tracks_of(data.get("collection", []))

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        t = self._tracks.get(track_id)
        if not t or "media" not in t:
            batch = await self._get("/tracks", ids=track_id)
            if not batch:
                raise ServiceError("Track not found")
            t = batch[0]
            self._tracks.put(track_id, t)

        def rank(tc: dict[str, Any]) -> tuple[int, int]:
            fmt = tc.get("format", {})
            # direct MP3 needs no HLS at all; otherwise AAC HLS (plays everywhere in MSE), best quality first
            if fmt.get("protocol") == "progressive":
                kind = 0
            elif "mp4a" in fmt.get("mime_type", ""):
                kind = 1
            else:
                kind = 2
            return kind, 0 if tc.get("quality") == "hq" else 1

        candidates = sorted(self._streams(t), key=rank)
        if not candidates:
            raise ServiceError("SoundCloud only offers this track copy-protected or as a preview, "
                               "which can't be played here")
        tc = candidates[0]
        data = await self._get(tc["url"], track_authorization=t.get("track_authorization", ""))
        url = data.get("url")
        if not url:
            raise ServiceError("SoundCloud didn't return a stream")
        hls = tc.get("format", {}).get("protocol") != "progressive"
        # The playlist link is signed for only ~2 minutes (its segments last hours), so cache it no
        # longer than its policy allows; replaying later re-resolves instead of hitting a 403.
        return StreamSource(url=url, hls=hls, expires=_signed_expiry(url) or time.time() + 90)

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith("https://"):
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id)
