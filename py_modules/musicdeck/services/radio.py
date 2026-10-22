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
import ssl
import time
from typing import Any, Callable, Optional

import aiohttp
import certifi

from .. import debuglog, models
from .base import MusicService, ServiceError, StreamSource

DIRECTORY = "https://all.api.radio-browser.info"
USER_AGENT = "MusicDeck/0.1.0"  # Radio Browser asks clients to identify themselves
SERVER_TTL = 6 * 3600
STATION_TTL = 3600
LIST_SIZE = 50
STREAM_TITLE = re.compile(rb"StreamTitle='(.*?)';", re.S)
COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")  # ISO 3166-1 alpha-2; the settings dialog offers a list


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
        timeout=aiohttp.ClientTimeout(total=15), headers={"User-Agent": USER_AGENT})


class RadioService(MusicService):
    """config: {"country": ISO code or "", "favourites": [station track, ...]}"""

    type = "radio"
    display_name = "Internet radio"
    features = frozenset({"star"})

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.server = ""
        self.server_at = 0.0
        self.stations: dict[str, tuple[float, dict[str, Any]]] = {}
        # set by the plugin: persists config changes (favourite stations)
        self.save_config: Optional[Callable[[dict[str, Any]], None]] = None

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
        now = time.time()
        for s in stations or []:
            self.stations[s["stationuuid"]] = (now, s)
        return [self._station(s) for s in stations or [] if s.get("url_resolved") or s.get("url")]

    async def _station_info(self, uuid: str) -> dict[str, Any]:
        hit = self.stations.get(uuid)
        if hit and time.time() - hit[0] < STATION_TTL:
            return hit[1]
        found = await self._get("/json/stations/byuuid", uuids=uuid)
        if not found:
            raise ServiceError("This station is no longer listed")
        self.stations[uuid] = (time.time(), found[0])
        return found[0]

    # --- mapping ---

    def _station(self, s: dict[str, Any]) -> dict[str, Any]:
        quality = " ".join(x for x in [s.get("codec") or "", f"{s['bitrate']} kbps" if s.get("bitrate") else ""] if x)
        place = s.get("state") or s.get("country") or ""
        tags = ", ".join(t for t in (s.get("tags") or "").split(",")[:3] if t)
        uuid = s["stationuuid"]
        return models.track(uuid, (s.get("name") or "").strip(), " · ".join(x for x in [place, quality] if x), tags,
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
        favourites = [{**f, "starred": True} for f in self.config.get("favourites") or []]
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

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("tag:"):
            tag = id[4:]
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

    async def on_air(self, track_id: str) -> Optional[str]:
        """The song the station is playing, from the ICY metadata in its stream (None if it has none)."""
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
        match = STREAM_TITLE.search(block)
        title = match.group(1).decode("utf-8", "replace").strip() if match else ""
        return title or None

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
        return StreamSource(url=art_id)
