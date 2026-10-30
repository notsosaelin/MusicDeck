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
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import aiohttp

from .. import lyrics, models
from ..cache import Cache
from ..net import tls
from .base import LocalLikes, MusicService, ServiceError, StreamSource

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
# an album page's tags (to Bandcamp's discover page for each), its "you may also like" albums, an artist's bio
TAG_LINK = re.compile(r'<a class="tag" href="https://bandcamp\.com/discover/([a-z0-9-]{1,64})[^"]*"\s*>([^<]{1,64})</a>')
RECOMMENDED = re.compile(
    r'<li class="recommended-album.*?<img class="album-art" src="([^"]+)".*?<a class="album-link" href="([^"]+)">'
    r'<span class="release-title">([^<]*)</span>.*?<span class="by-artist">by ([^<]*)</span>', re.S)
BIO = re.compile(r'<p id="bio-text"[^>]*>(.*?)</p>', re.S)
# Bandcamp Radio: its shows, and one show (its whole mix as one stream, and what's in it with timecodes)
RADIO_LIST = "https://bandcamp.com/api/bcweekly/3/list"
RADIO_SHOW = "https://bandcamp.com/api/bcweekly/3/get"
RADIO_PAGE = 60
SHOW = "bcradio:"  # a show's track id: this, then its number


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


class BandcampService(LocalLikes, MusicService):
    type = "bandcamp"
    display_name = "Bandcamp"
    features = frozenset({"star", "lyrics", "radio", "browse"})
    downloadable = True

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.pages: Cache[dict[str, Any]] = Cache(PAGE_TTL, PAGES_KEPT)

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls()),
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
        html = await self._text(url)
        match = TRALBUM.search(html)
        if not match:
            raise ServiceError("Bandcamp changed its page layout; MusicDeck needs an update")
        data = json.loads(_unescape(match.group(1)))
        data["url"] = url
        # what else the page shows, kept small (not the page): its tags, and "you may also like"
        data["_tags"] = [(tag, _unescape(name).strip()) for tag, name in TAG_LINK.findall(html)][:12]
        data["_liked"] = [(img, _clean_url(href), _unescape(title), _unescape(artist))
                          for img, href, title, artist in RECOMMENDED.findall(html)][:20]
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

    async def _discover(self, tag: str, size: int = 20, slice: str = "top") -> list[dict[str, Any]]:
        """A genre's albums on Bandcamp's discover page: best-selling ("top") or just out ("new")."""
        data = await self._post(DISCOVER, {"category_id": 0, "tag_norm_names": [tag], "geoname_id": 0,
                                           "slice": slice, "cursor": "*", "size": size,
                                           "include_result_types": ["a"]})
        return [self._album(x["item_url"], x.get("title", ""), x.get("band_name", ""),
                            _art((x.get("primary_image") or {}).get("image_id")), (x.get("release_date") or "")[:4])
                for x in data.get("results") or [] if x.get("item_url")]

    async def _shows(self, count: int) -> list[dict[str, Any]]:
        """Bandcamp Radio's latest shows, each a track: its whole mix, played as one."""
        assert self.session
        try:
            async with self.session.get(RADIO_LIST) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as e:
            raise ServiceError(f"Could not reach Bandcamp Radio: {e}") from e
        shows = []
        for x in (data.get("results") or [])[:count] if isinstance(data, dict) else []:
            if not str(x.get("id", "")).isdigit():
                continue
            title = " · ".join(t for t in (x.get("title"), x.get("subtitle")) if t)
            image = f"{IMAGES}{int(x['image_id']):010d}_10.jpg" if str(x.get("image_id", "")).isdigit() else None
            shows.append(models.track(f"{SHOW}{x['id']}", title, "Bandcamp Radio", (x.get("date") or "")[:11].strip(),
                                      0, art=image))
        return shows

    async def _show(self, show_id: str) -> dict[str, Any]:
        """One show: its stream ("audio_stream") and what's in it ("tracks", each with its "timecode")."""
        if not show_id.isdigit():
            raise ServiceError("Not a Bandcamp Radio show")
        hit = self.pages.get(SHOW + show_id)
        if hit:
            return hit
        assert self.session
        try:
            async with self.session.get(RADIO_SHOW, params={"id": show_id}) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as e:
            raise ServiceError(f"Could not reach Bandcamp Radio: {e}") from e
        if not isinstance(data, dict) or data.get("error"):
            raise ServiceError("Bandcamp Radio doesn't have that show")
        return self.pages.put(SHOW + show_id, data)

    async def home(self) -> list[dict[str, Any]]:
        # a different handful of genres each visit keeps the home view fresh
        picks = random.sample(GENRES, HOME_GENRES)
        results = await asyncio.gather(*(self._discover(tag) for tag, _ in picks), self._shows(12),
                                       return_exceptions=True)
        *results, shows = results
        sections = [models.section(f"Top {name.lower()}", albums)
                    for (_, name), albums in zip(picks, results) if isinstance(albums, list) and albums]
        if not sections:
            raise next(r for r in results if isinstance(r, Exception))  # type: ignore[misc]
        if isinstance(shows, list) and shows:
            sections.insert(1, models.section("Bandcamp Radio", [models.collection("radio", "list", "All shows",
                                                                                    "Bandcamp Radio")],
                                              shows, display="covers"))
        sections.append(models.section("Genres", [models.collection(f"genre:{tag}", "list", name)
                                                  for tag, name in GENRES]))
        return sections

    async def browse(self) -> list[dict[str, Any]]:
        return [models.section("Genres", [models.collection(f"genre:{tag}", "list", name) for tag, name in GENRES])]

    # --- media center ---

    TABS = (("home", "Home"), ("top", "Best-selling"), ("new", "New arrivals"))
    SHARED_TABS = frozenset({"home", "top", "new"})

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            return [s for s in await self.home() if not models.is_categories(s)]
        if tab_id in ("top", "new"):
            prefix = "genre" if tab_id == "top" else "new"
            return [models.section("Genres", [models.collection(f"{prefix}:{tag}", "list", name)
                                              for tag, name in GENRES], display="rows")]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("genre:"):
            tag = id[6:]
            name = dict(GENRES).get(tag, tag.title())
            return models.page(name, "Best-selling on Bandcamp", collections=await self._discover(tag, 60))
        if kind == "list" and id.startswith("new:"):
            tag = id[4:]
            name = dict(GENRES).get(tag, tag.title())
            return models.page(name, "New on Bandcamp", collections=await self._discover(tag, 60, "new"))
        if kind == "list" and id == "radio":
            return models.page("Bandcamp Radio", "Shows from Bandcamp, each a mix of what's on it",
                               tracks=await self._shows(RADIO_PAGE))
        if kind == "album":
            data = await self._tralbum(id)
            current = data.get("current") or {}
            year = (current.get("release_date") or data.get("album_release_date") or "")
            year = re.search(r"\d{4}", year).group(0) if re.search(r"\d{4}", year) else ""
            tracks = self._tracks(data)
            subtitle = " · ".join(x for x in [data.get("artist") or "", year] if x)
            if data.get("trackinfo") and not tracks:
                subtitle += " · for buyers only"
            # its tags (each opens the tag's best-sellers), its artist, then what Bandcamp says fans of it like
            explore = [models.collection(f"genre:{tag}", "list", name) for tag, name in data.get("_tags") or []]
            root = f"{urlparse(data['url']).scheme}://{urlparse(data['url']).netloc}"
            if data.get("artist"):
                explore.insert(0, models.collection(root, "artist", data["artist"], "Artist"))
            liked = [self._album(href, title, artist, img) for img, href, title, artist in data.get("_liked") or []]
            about = "\n".join(x.strip() for x in (current.get("about"), current.get("credits")) if x and x.strip())
            return models.page(current.get("title", ""), subtitle, _art(data.get("art_id")), tracks=tracks,
                               shelves=[models.section("Explore", explore), models.section("You may also like", liked)],
                               about=about)
        if kind == "artist":
            root = id.rstrip("/")
            html = await self._text(root + "/music")
            albums = []
            for href, img, title in DISCOGRAPHY_ITEM.findall(html):
                name = _unescape(TAGS.sub("", title)).split("\n")[0].strip()
                albums.append(self._album(urljoin(root + "/", href), name, "", img))
            name_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            image = re.search(r'<img[^>]+class="band-photo"[^>]+src="([^"]+)"', html)
            bio = BIO.search(html)
            return models.page(_unescape(name_match.group(1)) if name_match else urlparse(root).netloc,
                               models.count(len(albums), "release"), image.group(1) if image else None,
                               collections=albums,
                               about=_unescape(TAGS.sub("", bio.group(1).replace("<br>", "\n"))) if bio else "")
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        """Bandcamp's search as its own site's: everything (artists and albums, mostly), and tracks on their own (the
        first hardly ever has any)."""
        async def ask(only: str) -> list[dict[str, Any]]:
            data = await self._post(SEARCH, {"search_text": query, "search_filter": only, "full_page": False,
                                             "fan_id": None})
            return (data.get("auto") or {}).get("results") or []
        everything, songs = await asyncio.gather(ask(""), ask("t"), return_exceptions=True)
        if isinstance(everything, Exception) and isinstance(songs, Exception):
            raise everything
        found = [x for part in (everything, songs) if isinstance(part, list) for x in part]
        artists, albums, tracks = [], [], []
        seen: set[str] = set()
        for x in found:
            key = str(x.get("type")) + str(x.get("item_url_path") or x.get("item_url_root"))
            if key in seen:
                continue
            seen.add(key)
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
        if str(track.get("id", "")).startswith(SHOW):
            # a show's songs at their places in it, as synced lines: the one playing is lit as it goes
            show = await self._show(track["id"][len(SHOW):])
            lines = [{"t": float(t.get("timecode") or 0), "text": " – ".join(x for x in (t.get("artist"), t.get("title")) if x)}
                     for t in show.get("tracks") or [] if isinstance(t, dict)]
            return {"synced": True, "source": "Bandcamp Radio", "title": "In this show", "lines": lines} if lines else None
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
        if track_id.startswith(SHOW):
            show = await self._show(track_id[len(SHOW):])
            url = (show.get("audio_stream") or {}).get("mp3-128")
            if not url:
                raise ServiceError("Bandcamp Radio has no audio for that show")
            return StreamSource(url=url, content_type="audio/mpeg", expires=time.time() + PAGE_TTL)
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
