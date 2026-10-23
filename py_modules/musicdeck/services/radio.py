"""
Internet radio from Radio Browser (radio-browser.info): a free, open, community-maintained
directory of tens of thousands of stations. No account; favourite stations are kept in this
source's own config on the Deck.

Stations are tracks with "live": True. Streams are whatever the station broadcasts (MP3, AAC,
Ogg, or HLS), proxied like any other source. Most Icecast/Shoutcast streams also carry the song
on air as ICY metadata, which on_air() reads with a short side request.
"""
import asyncio
import random
import re
import time
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from .. import debuglog, models
from ..cache import Cache
from ..net import tls
from .base import ROWS_MAX, MusicService, ServiceError, StreamSource

DIRECTORY = "https://all.api.radio-browser.info"
USER_AGENT = "MusicDeck/0.1.0"  # Radio Browser asks clients to identify themselves
SERVER_TTL = 6 * 3600
STATION_TTL = 3600
STATIONS_KEPT = 1000
LIST_SIZE = 50
STREAM_TITLE = re.compile(rb"StreamTitle='(.*?)';", re.S)
STREAM_URL = re.compile(rb"StreamUrl='(.*?)';", re.S)
# iHeart and other broadcast automation systems put tags in the title:
#   Artist - text="Song" song_spot="M" amgArtworkURL="https://…" length="00:03:05" …
TITLE_TAG = re.compile(r'(\w+)="([^"]*)"')
# iHeart marks music "M"; the rest are station idents, ads and talk. Some streams send an unclosed
# url=" just before it, so it's found on its own rather than as a tag.
SONG_SPOT = re.compile(r'song_spot="?(\w)')
IMAGE_URL = re.compile(r"^https?://\S+\.(?:jpe?g|png|webp)(?:\?\S*)?$", re.I)
COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")  # ISO 3166-1 alpha-2; the settings dialog offers a list
# the names people use for countries the directory gives by their full official name
SHORT_NAMES = {"united states of america": "United States", "russian federation": "Russia",
               "united kingdom of great britain and northern ireland": "United Kingdom",
               "netherlands": "Netherlands", "republic of korea": "South Korea",
               "democratic people's republic of korea": "North Korea", "iran (islamic republic of)": "Iran",
               "islamic republic of iran": "Iran", "syrian arab republic": "Syria",
               "venezuela (bolivarian republic of)": "Venezuela", "bolivia (plurinational state of)": "Bolivia",
               "republic of moldova": "Moldova", "united republic of tanzania": "Tanzania",
               "lao people's democratic republic": "Laos", "viet nam": "Vietnam", "czechia": "Czechia",
               "philippines": "Philippines", "united arab emirates": "United Arab Emirates"}
SMALL_WORDS = {"of", "and", "the"}


def country_name(name: str) -> str:
    """"The United States Of America" as "United States"; "Bosnia And Herzegovina" as "Bosnia and Herzegovina"."""
    plain = name.strip()
    if plain.lower().startswith("the "):
        plain = plain[4:]
    short = SHORT_NAMES.get(plain.lower())
    if short:
        return short
    words = plain.split()
    return " ".join(w.lower() if i and w.lower() in SMALL_WORDS else w for i, w in enumerate(words))


def parse_on_air(title: str, stream_url: str = "") -> Optional[dict[str, Any]]:
    """What a station's ICY metadata says is playing: {"text", "title", "artist", "art"}.

    The plain form is "Artist - Title". Some stations add tags (see TITLE_TAG); then the song is in
    text= (or title=), the artist in artist= or before the first tag, and the cover in amgArtworkURL=.
    A few send the cover as StreamUrl instead. None when nothing (or no song) is on air.
    """
    spot = SONG_SPOT.search(title)
    if spot and spot.group(1).upper() != "M":
        return None
    tags = {k.lower(): v.strip() for k, v in TITLE_TAG.findall(title)}
    if tags:
        lead = TITLE_TAG.split(title, 1)[0].strip().rstrip("-–").strip()
        song = tags.get("text") or tags.get("title") or tags.get("song") or ""
        artist = tags.get("artist") or lead
        art = tags.get("amgartworkurl") or ""
    else:
        artist, _, song = title.partition(" - ")
        if not song:
            artist, song = "", artist
        art = ""
    song, artist = song.strip(), artist.strip()
    if not song and not artist:
        return None
    if not art.startswith(("https://", "http://")):
        # StreamUrl is usually the station's website; only an image link is a cover
        art = stream_url if IMAGE_URL.match(stream_url) else None
    return {"text": " – ".join(x for x in (artist, song) if x), "title": song, "artist": artist, "art": art}


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls()),
        timeout=aiohttp.ClientTimeout(total=15), headers={"User-Agent": USER_AGENT})


class RadioService(MusicService):
    """config: {"country": ISO code or "", "favourites": [station track, ...]}"""

    type = "radio"
    display_name = "Internet radio"
    features = frozenset({"star", "browse"})

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.server = ""
        self.server_at = 0.0
        self.stations: Cache[dict[str, Any]] = Cache(STATION_TTL, STATIONS_KEPT)

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        country = (config.get("country") or "").upper()
        return {"country": country if COUNTRY_CODE.match(country) else "",
                "favourites": (previous or {}).get("favourites") or config.get("favourites") or []}

    async def start(self) -> None:
        self.session = _session()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    # --- directory ---

    async def _base(self) -> str:
        """Radio Browser is run by volunteers on a few mirrors; pick one at random, as they ask."""
        if self.server and time.time() - self.server_at < SERVER_TTL:
            return self.server
        assert self.session
        try:
            async with self.session.get(f"{DIRECTORY}/json/servers") as resp:
                servers = [s["name"] for s in await resp.json(content_type=None) if s.get("name")]
        except (aiohttp.ClientError, ValueError):
            servers = []
        self.server = f"https://{random.choice(servers)}" if servers else DIRECTORY
        self.server_at = time.time()
        return self.server

    async def _get(self, path: str, **params: Any) -> Any:
        assert self.session
        query = {"hidebroken": "true", **{k: str(v).lower() if isinstance(v, bool) else v
                                          for k, v in params.items() if v is not None}}
        try:
            async with self.session.get(await self._base() + path, params=query) as resp:
                if resp.status >= 400:
                    raise ServiceError(f"The radio directory returned HTTP {resp.status}")
                return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            self.server = ""  # try another mirror next time
            raise ServiceError(f"Could not reach the radio directory: {e}") from e

    async def _stations(self, path: str, **params: Any) -> list[dict[str, Any]]:
        stations = await self._get(path, limit=params.pop("limit", LIST_SIZE), **params)
        for s in stations or []:
            self.stations.put(s["stationuuid"], s)
        return [self._station(s) for s in stations or [] if s.get("url_resolved") or s.get("url")]

    async def _station_info(self, uuid: str) -> dict[str, Any]:
        hit = self.stations.get(uuid)
        if hit:
            return hit
        found = await self._get("/json/stations/byuuid", uuids=uuid)
        if not found:
            raise ServiceError("This station is no longer listed")
        return self.stations.put(uuid, found[0])

    # --- mapping ---

    def _station(self, s: dict[str, Any]) -> dict[str, Any]:
        place = s.get("state") or s.get("country") or ""
        tags = ", ".join(t for t in (s.get("tags") or "").split(",")[:3] if t)
        uuid = s["stationuuid"]
        return models.track(uuid, (s.get("name") or "").strip(), place, tags,
                            art=s.get("favicon") or None, starred=uuid in self._favourite_ids(), live=True)

    @staticmethod
    def _tag(t: dict[str, Any]) -> dict[str, Any]:
        return models.collection(f"tag:{t['name']}", "list", t["name"].title(),
                                 models.count(t.get("stationcount") or 0, "station"))

    def _favourite_ids(self) -> set[str]:
        return {f["id"] for f in self.config.get("favourites") or []}

    # --- browsing ---

    async def test(self) -> str:
        stats = await self._get("/json/stats")
        return f"Connected to Radio Browser · {stats.get('stations', 0):,} stations"

    async def home(self) -> list[dict[str, Any]]:
        country = self.config.get("country")
        jobs = {
            "popular": self._stations("/json/stations/topclick"),
            "trending": self._stations("/json/stations/search", order="clicktrend", reverse=True),
            "genres": self._get("/json/tags", order="stationcount", reverse=True, limit=24),
        }
        if country:
            jobs["local"] = self._stations(f"/json/stations/bycountrycodeexact/{country}", order="clickcount",
                                           reverse=True)
        got = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
        sections = []
        # favourites saved before stations dropped "MP3 128 kbps" from their subtitle keep only the place
        favourites = [{**f, "artist": f.get("artist", "").split(" · ")[0], "starred": True}
                      for f in self.config.get("favourites") or []]
        if favourites:
            sections.append(models.section("Your stations", [], favourites))
        if isinstance(got.get("local"), list) and got["local"]:
            sections.append(models.section("Popular near you", [], got["local"][:20]))
        if isinstance(got.get("trending"), list):
            sections.append(models.section("Trending", [], got["trending"][:20]))
        if isinstance(got.get("popular"), list):
            sections.append(models.section("Most listened", [], got["popular"][:20]))
        if isinstance(got.get("genres"), list):
            sections.append(models.section("Genres", [self._tag(t) for t in got["genres"]]))
        if not sections:
            raise next(v for v in got.values() if isinstance(v, Exception))
        return sections

    async def browse(self) -> list[dict[str, Any]]:
        tags, countries = await asyncio.gather(
            self._get("/json/tags", order="stationcount", reverse=True, limit=60),
            self._get("/json/countries", order="stationcount", reverse=True, limit=60), return_exceptions=True)
        sections = []
        if isinstance(tags, list):
            sections.append(models.section("Genres", [self._tag(t) for t in tags if t.get("name")]))
        if isinstance(countries, list):
            sections.append(models.section("Countries", [
                models.collection(f"country:{c['iso_3166_1']}#{c.get('name') or ''}", "list",
                                  country_name(c.get("name") or c["iso_3166_1"]),
                                  models.count(c.get("stationcount") or 0, "station"))
                for c in countries if COUNTRY_CODE.match(c.get("iso_3166_1") or "")]))
        if not sections:
            raise tags if isinstance(tags, Exception) else countries  # type: ignore[misc]
        return sections

    # --- media center ---

    TABS = (("home", "Home"), ("genres", "Genres"), ("countries", "Countries"), ("languages", "Languages"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            # stations by their logos, as the genres show them
            return [{**s, "display": "covers"} for s in await self.home() if not models.is_categories(s)]
        if tab_id == "genres":
            tags = await self._get("/json/tags", order="stationcount", reverse=True, limit=100)
            tags = [self._tag(t) for t in tags if t.get("name")]
            # the biggest as rows of their stations, the rest to pick from
            return [models.section("Genres", tags[:ROWS_MAX], display="rows"),
                    models.section("More genres", tags[ROWS_MAX:], display="tiles")]
        if tab_id == "countries":
            return [{**s, "display": "tiles"} for s in await self.browse() if s["title"] == "Countries"]
        if tab_id == "languages":
            languages = await self._get("/json/languages", order="stationcount", reverse=True, limit=80)
            return [models.section("Languages", [
                models.collection(f"language:{l['name']}", "list", l["name"].title(),
                                  models.count(l.get("stationcount") or 0, "station"))
                for l in languages if l.get("name") and (l.get("stationcount") or 0) >= 10], display="tiles")]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("country:"):
            code, _, name = id[8:].partition("#")
            if not COUNTRY_CODE.match(code):
                raise ServiceError("Unknown country")
            stations = await self._stations(f"/json/stations/bycountrycodeexact/{code}", order="clickcount",
                                            reverse=True, limit=100)
            return models.page(country_name(name) if name else code, models.count(len(stations), "station"), tracks=stations)
        if kind == "list" and id.startswith("language:"):
            language = quote(id[9:], safe="")
            stations = await self._stations(f"/json/stations/bylanguageexact/{language}", order="clickcount",
                                            reverse=True, limit=100)
            return models.page(id[9:].title(), models.count(len(stations), "station"), tracks=stations)
        if kind == "list" and id.startswith("tag:"):
            tag = quote(id[4:], safe="")
            stations = await self._stations(f"/json/stations/bytagexact/{tag}", order="clickcount", reverse=True,
                                            limit=100)
            return models.page(tag.title(), models.count(len(stations), "station"), tracks=stations)
        raise ServiceError(f"Unknown collection: {kind} {id}")

    async def search(self, query: str) -> dict[str, Any]:
        stations, tags = await asyncio.gather(
            self._stations("/json/stations/search", name=query, order="clickcount", reverse=True),
            self._get(f"/json/tags/{query}", order="stationcount", reverse=True, limit=10),
            return_exceptions=True)
        if isinstance(stations, Exception):
            raise stations
        genres = [self._tag(t) for t in tags if t.get("stationcount")] if isinstance(tags, list) else []
        return models.page(f"Results for “{query}”", collections=genres, tracks=stations, tracks_first=True)

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        favourites = [f for f in self.config.get("favourites") or [] if f["id"] != track_id]
        if starred:
            favourites.insert(0, {**self._station(await self._station_info(track_id)), "starred": True})
        self.config["favourites"] = favourites
        if self.save_config:
            self.save_config(self.config)

    async def on_air(self, track_id: str) -> Optional[dict[str, Any]]:
        """The song the station is playing, from the ICY metadata in its stream (None if it has none);
        see parse_on_air."""
        info = await self._station_info(track_id)
        url = info.get("url_resolved") or info.get("url")
        if not url or info.get("hls"):
            return None
        assert self.session
        try:
            async with self.session.get(url, headers={"Icy-MetaData": "1"},
                                        timeout=aiohttp.ClientTimeout(total=8)) as resp:
                interval = int(resp.headers.get("icy-metaint") or 0)
                if not interval:
                    return None
                await resp.content.readexactly(interval)
                length = (await resp.content.readexactly(1))[0] * 16
                block = await resp.content.readexactly(length) if length else b""
        except (aiohttp.ClientError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
            return None
        def field(pattern: re.Pattern[bytes]) -> str:
            match = pattern.search(block)
            if not match:
                return ""
            try:
                return match.group(1).decode("utf-8").strip()
            except UnicodeDecodeError:  # older Shoutcast servers send Latin-1
                return match.group(1).decode("latin-1").strip()
        return parse_on_air(field(STREAM_TITLE), field(STREAM_URL))

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        info = await self._station_info(track_id)
        url = info.get("url_resolved") or info.get("url")
        if not url:
            raise ServiceError("This station has no stream address")
        try:
            # Radio Browser's etiquette: count a play, which also keeps its popularity lists honest
            await self._get(f"/json/url/{track_id}")
        except ServiceError as e:
            debuglog.debug("radio", "click not counted", error=str(e))
        hls = bool(info.get("hls")) or ".m3u8" in url.split("?")[0]
        return StreamSource(url=url, hls=hls)

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith(("https://", "http://")):
            raise ServiceError("Bad art id")
        # some logo hosts (DuckDuckGo's icon cache among them) refuse requests that don't say who they are
        return StreamSource(url=art_id, headers={"User-Agent": USER_AGENT})
