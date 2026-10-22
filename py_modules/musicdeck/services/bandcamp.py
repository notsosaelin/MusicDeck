"""
Bandcamp, signed out, the way NewPipe uses it: the public search API, the discover charts, and the
album data Bandcamp embeds in its own pages (the "tralbum" JSON its web player reads). Streams are
the 128 kbps MP3s that every visitor can play in full; tracks an artist keeps for buyers only are
left out. No account, so favourites live in MusicDeck playlists, on the Deck.
"""
import asyncio
import json
import random
import re
import ssl
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
import certifi

from ..cache import Cache
from .. import lyrics, models
from .base import MusicService, ServiceError, StreamSource

SEARCH = "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic"
DISCOVER = "https://bandcamp.com/api/discover/1/discover_web"
IMAGES = "https://f4.bcbits.com/img/"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")
# Genres shown on the home view, as Bandcamp's discover page names them
GENRES = [("electronic", "Electronic"), ("rock", "Rock"), ("hip-hop-rap", "Hip-hop / Rap"),
          ("alternative", "Alternative"), ("experimental", "Experimental"), ("punk", "Punk"),
          ("metal", "Metal"), ("ambient", "Ambient"), ("jazz", "Jazz"), ("folk", "Folk")]
HOME_GENRES = 4
PAGE_TTL = 15 * 60  # stream links in album data are signed and expire; re-read pages after this
PAGES_KEPT = 200
TRALBUM = re.compile(r'data-tralbum="([^"]+)"')
ENTITY = re.compile(r"&(#x[0-9a-fA-F]+|#\d+|quot|amp|lt|gt|apos);")
NAMED = {"quot": '"', "amp": "&", "lt": "<", "gt": ">", "apos": "'"}
DISCOGRAPHY_ITEM = re.compile(
    r'<li[^>]+data-item-id="(?:album|track)-\d+"[^>]*>.*?<a href="([^"]+)".*?'
    r'<img[^>]+?(?:data-original|src)="([^"]+)".*?<p class="title">\s*(.*?)\s*</p>', re.S)
TAGS = re.compile(r"<[^>]+>")


def _unescape(text: str) -> str:
    """HTML entities in an attribute (Decky's Python has no html module)."""
    def entity(m: re.Match) -> str:
        e = m.group(1)
        if e.startswith("#x"):
            return chr(int(e[2:], 16))
        if e.startswith("#"):
            return chr(int(e[1:]))
        return NAMED[e]
    return ENTITY.sub(entity, text)


def _art(art_id: Optional[int], size: int = 2) -> Optional[str]:
    """Album art by id (size 2 = 350 px)."""
    return f"{IMAGES}a{int(art_id):010d}_{size}.jpg" if art_id else None


def _clean_url(url: str) -> str:
    return url.split("?")[0]


class BandcampService(MusicService):
    type = "bandcamp"
    display_name = "Bandcamp"
    features = frozenset({"lyrics", "radio"})
    downloadable = True

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.pages: Cache[dict[str, Any]] = Cache(PAGE_TTL, PAGES_KEPT)

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
            timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": USER_AGENT})

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    # --- fetching ---

    async def _text(self, url: str) -> str:
        assert self.session
        try:
            async with self.session.get(url) as resp:
                if resp.status == 404:
                    raise ServiceError("Not found on Bandcamp")
                if resp.status >= 400:
                    raise ServiceError(f"Bandcamp returned HTTP {resp.status}")
                return await resp.text()
        except aiohttp.ClientError as e:
            raise ServiceError(f"Could not reach Bandcamp: {e}") from e

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        assert self.session
        try:
            async with self.session.post(url, json=body) as resp:
                if resp.status >= 400:
                    raise ServiceError(f"Bandcamp returned HTTP {resp.status}")
                return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise ServiceError(f"Could not reach Bandcamp: {e}") from e

    async def _tralbum(self, url: str) -> dict[str, Any]:
        """The album (or single track) data Bandcamp's own player uses, from its page."""
        url = _clean_url(url)
        hit = self.pages.get(url)
        if hit:
            return hit
        match = TRALBUM.search(await self._text(url))
        if not match:
            raise ServiceError("Bandcamp changed its page layout; MusicDeck needs an update")
        data = json.loads(_unescape(match.group(1)))
        data["url"] = url
        return self.pages.put(url, data)

    # --- mapping ---

    def _tracks(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        album_artist = data.get("artist") or ""
        album = (data.get("current") or {}).get("title", "") if data.get("item_type") == "album" else ""
        root = f"{urlparse(data['url']).scheme}://{urlparse(data['url']).netloc}"
        out = []
        for t in data.get("trackinfo") or []:
            if not (t.get("file") or {}).get("mp3-128") or not t.get("title_link"):
                continue  # buyers only, or not encoded yet
            out.append(models.track(urljoin(root, t["title_link"]), t.get("title", ""), t.get("artist") or album_artist,
                                    album, float(t.get("duration") or 0), art=_art(data.get("art_id")),
                                    album_id=data["url"] if album else None, artist_id=root))
        return out

    @staticmethod
    def _album(url: str, title: str, artist: str, art: Optional[str], year: str = "") -> dict[str, Any]:
        return models.collection(_clean_url(url), "album", title, " · ".join(x for x in [artist, year] if x), art)

    # --- browsing ---

    async def _discover(self, tag: str, size: int = 20) -> list[dict[str, Any]]:
        data = await self._post(DISCOVER, {"category_id": 0, "tag_norm_names": [tag], "geoname_id": 0,
                                           "slice": "top", "cursor": "*", "size": size,
                                           "include_result_types": ["a"]})
        return [self._album(x["item_url"], x.get("title", ""), x.get("band_name", ""),
                            _art((x.get("primary_image") or {}).get("image_id")), (x.get("release_date") or "")[:4])
                for x in data.get("results") or [] if x.get("item_url")]

    async def home(self) -> list[dict[str, Any]]:
        # a different handful of genres each visit keeps the home view fresh
        picks = random.sample(GENRES, HOME_GENRES)
        results = await asyncio.gather(*(self._discover(tag) for tag, _ in picks), return_exceptions=True)
        sections = [models.section(f"Top {name.lower()}", albums)
                    for (_, name), albums in zip(picks, results) if isinstance(albums, list) and albums]
        if not sections:
            raise next(r for r in results if isinstance(r, Exception))  # type: ignore[misc]
        sections.append(models.section("Genres", [models.collection(f"genre:{tag}", "list", name)
                                                  for tag, name in GENRES]))
        return sections

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("genre:"):
            tag = id[6:]
            name = dict(GENRES).get(tag, tag.title())
            return models.page(name, "Best-selling on Bandcamp", collections=await self._discover(tag, 60))
        if kind == "album":
            data = await self._tralbum(id)
            current = data.get("current") or {}
            year = (current.get("release_date") or data.get("album_release_date") or "")
            year = re.search(r"\d{4}", year).group(0) if re.search(r"\d{4}", year) else ""
            tracks = self._tracks(data)
            subtitle = " · ".join(x for x in [data.get("artist") or "", year] if x)
            if data.get("trackinfo") and not tracks:
                subtitle += " · for buyers only"
            return models.page(current.get("title", ""), subtitle, _art(data.get("art_id")), tracks=tracks)
        if kind == "artist":
            root = id.rstrip("/")
            html = await self._text(root + "/music")
            albums = []
            for href, img, title in DISCOGRAPHY_ITEM.findall(html):
                name = _unescape(TAGS.sub("", title)).split("\n")[0].strip()
                albums.append(self._album(urljoin(root + "/", href), name, "", img))
            name_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            image = re.search(r'<img[^>]+class="band-photo"[^>]+src="([^"]+)"', html)
            return models.page(_unescape(name_match.group(1)) if name_match else urlparse(root).netloc,
                               models.count(len(albums), "release"), image.group(1) if image else None,
                               collections=albums)
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        data = await self._post(SEARCH, {"search_text": query, "search_filter": "", "full_page": False,
                                         "fan_id": None})
        artists, albums, tracks = [], [], []
        for x in (data.get("auto") or {}).get("results") or []:
            if x.get("type") == "b" and x.get("item_url_root"):
                artists.append(models.collection(x["item_url_root"], "artist", x.get("name", ""), "Artist", x.get("img")))
            elif x.get("type") == "a" and x.get("item_url_path"):
                albums.append(self._album(x["item_url_path"], x.get("name", ""), x.get("band_name", ""), x.get("img")))
            elif x.get("type") == "t" and x.get("item_url_path"):
                root = x.get("item_url_root") or f"https://{urlparse(x['item_url_path']).netloc}"
                tracks.append(models.track(_clean_url(x["item_url_path"]), x.get("name", ""), x.get("band_name", ""),
                                           x.get("album_name") or "", art=x.get("img"), artist_id=root))
        return models.page(f"Results for “{query}”", collections=artists + albums, tracks=tracks, tracks_first=True)

    # --- features ---

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            data = await self._tralbum(track["id"])
        except ServiceError:
            return None
        info = next((t for t in data.get("trackinfo") or [] if t.get("lyrics")), None)
        return lyrics.plain(info["lyrics"], "Bandcamp") if info else None

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        """More from the same artist: tracks from a few of their other releases, shuffled."""
        if not track.get("artist_id"):
            return []
        page = await self.open("artist", track["artist_id"])
        albums = [c for c in page["collections"] if c["id"] != track.get("album_id")]
        random.shuffle(albums)
        found = await asyncio.gather(*(self._tralbum(a["id"]) for a in albums[:3]), return_exceptions=True)
        pool = [t for data in found if isinstance(data, dict) for t in self._tracks(data) if t["id"] != track["id"]]
        random.shuffle(pool)
        return pool

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        data = await self._tralbum(track_id)
        info = next((t for t in data.get("trackinfo") or [] if (t.get("file") or {}).get("mp3-128")), None)
        if not info:
            raise ServiceError("This track is only for buyers on Bandcamp")
        # the link is signed and short-lived: cached no longer than the page it came from
        return StreamSource(url=info["file"]["mp3-128"], content_type="audio/mpeg", expires=time.time() + PAGE_TTL)

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith("https://"):
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id)
