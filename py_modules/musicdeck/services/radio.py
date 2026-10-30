"""
Internet radio from Radio Browser (radio-browser.info): a free, open, community-maintained
directory of tens of thousands of stations. No account; favourite stations are kept in this
source's own config on the Deck.

Stations are tracks with "live": True. Streams are whatever the station broadcasts (MP3, AAC,
Ogg, or HLS), proxied like any other source. Most Icecast/Shoutcast streams also carry the song
on air as ICY metadata, which on_air() reads with a short side request.
"""
import asyncio
import contextlib
import json
import os
import random
import re
import time
from typing import Any, Optional
from urllib.parse import quote, unquote

import aiohttp

from .. import debuglog, models
from ..cache import Cache
from ..net import tls
from .base import MusicService, ServiceError, StreamSource, as_rows
from ..version import USER_AGENT

DIRECTORY = "https://all.api.radio-browser.info"
SERVER_TTL = 6 * 3600
STATION_TTL = 3600
STATIONS_KEPT = 1000
# stations saved or played, kept on disk so they play while the directory is down
STATIONS_ON_DISK = 300
# the last good answer of each list the directory gave, for when it fails
ANSWERS_KEPT = 40
RETRY_AFTER = 1.5
# once the directory has failed, it isn't asked again for this long: lists show their last good answer at once
# (Home and Browse wait for every source, so a directory that's down mustn't hold them up each time)
RESTING = 60
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


SONG_ART_TTL = 24 * 3600
SONG_ART_KEPT = 300
ITUNES = "https://itunes.apple.com"


# what stations put where a song should be: their playout system's codes ("PLAY_8_NEW", "ID_1234"),
# adverts and idents, web addresses
NOT_A_SONG = re.compile(r"^(unknown|advert\w*|commercial\w*|jingle\w*|station id|sweeper|promo|news|"
                        r"weather|traffic|live|on air|now playing|-+|\.+)$", re.I)
CODE_LIKE = re.compile(r"^[\w.]*[_\d][\w.]*$")
WEB = re.compile(r"https?://|www\.|\.(com|net|org|fm|radio|de|co\.uk|ru|it|fr)\b", re.I)


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def _stations_own(part: str, station: str) -> bool:
    """A half of a title that's the station talking: its name, its website."""
    plain = _plain(part)
    return bool(part) and (bool(WEB.search(part)) or bool(station and station in plain))


BY = re.compile(r"^(.+?)\s+by\s+(.+)$", re.I)


def tidy_on_air(song: Optional[dict[str, Any]], station: str) -> Optional[dict[str, Any]]:
    """What a station says is on air, as a song, or None when it isn't one. Some put the song in one
    half and themselves in the other ("Theme from Joy House by Jimmy Smith - Adroit Jazz on walmradio.com"):
    the station's half goes, and "<song> by <artist>" is read as just that."""
    if not song:
        return None
    plain_station = _plain(station)
    title, artist = song["title"], song["artist"]
    padded = False  # one half was the station's own: the other has to say "<song> by <artist>"
    if _stations_own(title, plain_station) and artist and not _stations_own(artist, plain_station):
        title, artist, padded = artist, "", True
    elif _stations_own(artist, plain_station) and title:
        artist, padded = "", True
    if not artist:
        by = BY.match(title)
        if by:
            title, artist = by.group(1).strip(), by.group(2).strip()
        elif padded:
            return None  # "Radio Bob - Rockt!": the station talking
    song = {**song, "title": title, "artist": artist, "text": " – ".join(x for x in (artist, title) if x)}
    return song if believable(song, station) else None


def believable(song: dict[str, Any], station: str) -> bool:
    """Whether what a station says is on air looks like a song: not its own name, not a playout
    system's code ("PLAY_8_NEW"), not an advert, ident or web address."""
    station = _plain(station)
    for part in (song["title"], song["artist"]):
        plain = _plain(part)
        if not part:
            continue
        if station and (plain == station or plain in station.split(" - ")):
            return False
        if NOT_A_SONG.match(part.strip()) or WEB.search(part):
            return False
        # one word with underscores or digits mixed in: a code, not a title ("PLAY_8_NEW", "ID1234");
        # a number alone can be a song ("1999")
        word = part.strip()
        if " " not in word and CODE_LIKE.match(word) and not word.isalpha() and not word.isdigit():
            return False
    return bool(song["title"])


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls()),
        # short: a slow mirror is tried again (_get), within the time a page waits
        timeout=aiohttp.ClientTimeout(total=8), headers={"User-Agent": USER_AGENT})


class RadioService(MusicService):
    """config: {"country": ISO code or "", "favourites": [station track, ...]}"""

    type = "radio"
    display_name = "Internet radio"
    features = frozenset({"star", "browse"})
    LIKE_WORDS = {"add": "Add to your stations", "remove": "Remove from your stations",
                  "added": "Added to your stations", "removed": "Removed from your stations"}

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.server = ""
        self.server_at = 0.0
        self.stations: Cache[dict[str, Any]] = Cache(STATION_TTL, STATIONS_KEPT)
        # songs' covers looked up for stations that name the song but send no picture: {"artist – title": url}
        self.song_art: Cache[str] = Cache(SONG_ART_TTL, SONG_ART_KEPT)
        # (when, the directory's genre names), for similar stations
        self.genres: Optional[tuple[float, set[str]]] = None
        # the last good answer to each request (path and query), shown when the directory fails
        self.answers: dict[str, Any] = {}
        self.failed_at = 0.0
        self.kept_path = os.path.join(runtime_dir, f"radio-{instance_id}.json")
        self.kept: dict[str, dict[str, Any]] = self._load_kept()

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

    def remove(self) -> None:
        with contextlib.suppress(OSError):
            os.remove(self.kept_path)

    # --- stations kept on disk ---

    def _load_kept(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self.kept_path, encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _keep(self, station: dict[str, Any]) -> None:
        """A station saved or played: its directory record, on disk (the latest last; the oldest go)."""
        uuid = station.get("stationuuid")
        if not uuid:
            return
        self.kept.pop(uuid, None)
        self.kept[uuid] = {k: station.get(k) for k in ("stationuuid", "name", "url", "url_resolved", "hls", "favicon",
                                                       "tags", "country", "state", "language", "codec", "bitrate",
                                                       "homepage") if station.get(k) not in (None, "")}
        favourites = self._favourite_ids()
        for old in [k for k in self.kept if k not in favourites][:max(0, len(self.kept) - STATIONS_ON_DISK)]:
            self.kept.pop(old, None)
        tmp = self.kept_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.kept, f)
            os.replace(tmp, self.kept_path)
        except OSError as e:
            debuglog.debug("radio", "stations not kept", error=str(e))

    async def _keep_favourites(self) -> None:
        """Favourites saved before stations were kept on disk: fetched once, while the directory answers."""
        missing = [i for i in self._favourite_ids() if i not in self.kept]
        if not missing:
            return
        with contextlib.suppress(ServiceError):
            for station in await self._get("/json/stations/byuuid", uuids=",".join(missing[:100])) or []:
                self._keep(station)

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
        """The directory's answer. A mirror that fails (it's often down to one server, which answers 503 for a while)
        is tried once more, a moment later, on whichever mirror is picked then; when that fails too, the last good
        answer to the same request is given, if there was one."""
        assert self.session
        query = {"hidebroken": "true", **{k: str(v).lower() if isinstance(v, bool) else v
                                          for k, v in params.items() if v is not None}}
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(query.items()))
        error = ServiceError("The radio directory isn't answering; try again soon")
        if self.failed_at and time.monotonic() - self.failed_at < RESTING:
            if key in self.answers:
                return self.answers[key]
            raise error
        for attempt in range(2):
            if attempt:
                self.server = ""  # another mirror, if there's another
                await asyncio.sleep(RETRY_AFTER)
            try:
                async with self.session.get(await self._base() + path, params=query) as resp:
                    if resp.status >= 500:
                        error = ServiceError(f"The radio directory is having trouble (HTTP {resp.status}); try again soon")
                        continue
                    if resp.status >= 400:
                        raise ServiceError(f"The radio directory returned HTTP {resp.status}")
                    answer = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                error = ServiceError(f"Could not reach the radio directory ({type(e).__name__}); try again soon")
                continue
            if not path.startswith("/json/url/"):  # a play counted: nothing to show later
                self.answers.pop(key, None)
                self.answers[key] = answer
                while len(self.answers) > ANSWERS_KEPT:
                    self.answers.pop(next(iter(self.answers)))
            self.failed_at = 0.0
            return answer
        self.server = ""
        self.failed_at = time.monotonic()
        if key in self.answers:
            debuglog.debug("radio", "directory failed: the last good answer given", path=path, error=str(error))
            return self.answers[key]
        raise error

    async def _stations(self, path: str, **params: Any) -> list[dict[str, Any]]:
        stations = await self._get(path, limit=params.pop("limit", LIST_SIZE), **params)
        for s in stations or []:
            self.stations.put(s["stationuuid"], s)
        return [self._station(s) for s in stations or [] if s.get("url_resolved") or s.get("url")]

    async def _station_info(self, uuid: str) -> dict[str, Any]:
        hit = self.stations.get(uuid)
        if hit:
            return hit
        if uuid in self.kept:
            # saved or played before: no need to wait for the directory (which may be down)
            return self.kept[uuid]
        found = await self._get("/json/stations/byuuid", uuids=uuid)
        if not found:
            raise ServiceError("This station is no longer listed")
        return self.stations.put(uuid, found[0])

    # --- mapping ---

    def _station(self, s: dict[str, Any]) -> dict[str, Any]:
        place = s.get("state") or (country_name(s["country"]) if s.get("country") else "")
        tags = ", ".join(t for t in (s.get("tags") or "").split(",")[:3] if t)
        uuid = s["stationuuid"]
        return models.track(uuid, (s.get("name") or "").strip(), place, tags,
                            art=s.get("favicon") or None, starred=uuid in self._favourite_ids(), live=True)

    @staticmethod
    def _tag(t: dict[str, Any]) -> dict[str, Any]:
        # some tags are stored encoded ("hip%20hop"): named as they read
        return models.collection(f"tag:{t['name']}", "list", unquote(t["name"]).title(),
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
        if any(isinstance(v, list) for v in got.values()):
            asyncio.ensure_future(self._keep_favourites())
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

    NOW_PLAYING = "radio"

    async def _genre_names(self) -> set[str]:
        """The directory's 150 biggest tags, which are its genres (kept for a day)."""
        if not self.genres or time.time() - self.genres[0] > SERVER_TTL * 4:
            try:
                tags = await self._get("/json/tags", order="stationcount", reverse=True, limit=150)
                self.genres = (time.time(), {t["name"].lower() for t in tags if t.get("name")})
            except ServiceError:
                return self.genres[1] if self.genres else set()
        return self.genres[1]

    async def details(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        s = await self._station_info(str(track.get("id", "")))
        tags = [t.strip() for t in (s.get("tags") or "").split(",") if t.strip()]
        quality = " ".join(x for x in ((s.get("codec") or "").upper(),
                                       f"{s['bitrate']} kbps" if s.get("bitrate") else "") if x)
        place = ", ".join(x for x in (s.get("state"), country_name(s["country"]) if s.get("country") else "") if x)
        facts = [[label, value] for label, value in (
            ("From", place), ("Language", (s.get("language") or "").title()), ("Genres", ", ".join(tags[:6])),
            ("Quality", quality), ("Website", s.get("homepage") or ""),
            ("Today", f"{s['clickcount']:,} listeners" if s.get("clickcount") else ""),
            ("Votes", f"{s['votes']:,}" if s.get("votes") else "")) if value]
        related = None
        # stations like it by its genre: its first tag that's one of the directory's genres ("pop", not
        # the "iheart" network a station is tagged with first)
        genres = await self._genre_names()
        genre = next((t for t in tags if t.lower() in genres), tags[0] if tags else "")
        if genre:
            try:
                similar = await self._stations(f"/json/stations/bytagexact/{quote(genre, safe='')}",
                                               order="clickcount", reverse=True, limit=21)
                similar = [x for x in similar if x["id"] != track.get("id")][:20]
                related = models.section(f"More {genre}", [], similar) if similar else None
            except ServiceError:
                pass
        return {"facts": facts, "related": related}

    TABS = (("home", "Home"), ("stations", "Your stations"), ("genres", "Genres"), ("countries", "Countries"),
            ("languages", "Languages"))
    SHARED_TABS = frozenset({"genres", "countries", "languages"})  # Home has the user's stations

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            # stations by their logos, as the genres show them
            return [{**s, "display": "covers"} for s in await self.home() if not models.is_categories(s)]
        if tab_id == "genres":
            tags = await self._get("/json/tags", order="stationcount", reverse=True, limit=100)
            tags = [self._tag(t) for t in tags if t.get("name")]
            # each a row of its stations, the biggest first
            return [models.section("Genres", tags, display="rows")]
        if tab_id == "stations":
            favourites = [{**f, "artist": f.get("artist", "").split(" · ")[0], "starred": True}
                          for f in self.config.get("favourites") or []]
            return [models.section("Your stations", [], favourites, display="grid")] if favourites else \
                [models.notice("Press Y on a station and choose Favourite to keep it here")]
        if tab_id == "countries":
            return [as_rows(s) for s in await self.browse() if s["title"] == "Countries"]
        if tab_id == "languages":
            languages = await self._get("/json/languages", order="stationcount", reverse=True, limit=80)
            return [models.section("Languages", [
                models.collection(f"language:{l['name']}", "list", l["name"].title(),
                                  models.count(l.get("stationcount") or 0, "station"))
                for l in languages if l.get("name") and (l.get("stationcount") or 0) >= 10], display="rows")]
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
            return models.page(unquote(id[4:]).title(), models.count(len(stations), "station"), tracks=stations)
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
            info = await self._station_info(track_id)
            favourites.insert(0, {**self._station(info), "starred": True})
            self._keep(info)
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
        song = parse_on_air(field(STREAM_TITLE), field(STREAM_URL))
        song = tidy_on_air(song, info.get("name") or "")
        if song and not song["art"] and song["title"] and song["artist"]:
            song["art"] = await self._song_art(song["artist"], song["title"])
        return song

    async def _song_art(self, artist: str, title: str) -> Optional[str]:
        """The cover of the song on air, from Apple's catalogue (no account): only for the song of exactly
        that title by that artist. Remembered a day, "no cover" too, so polling doesn't ask again."""
        key = f"{artist.casefold()} – {title.casefold()}"
        hit = self.song_art.get(key)
        if hit is not None:
            return hit or None
        url = ""
        assert self.session
        try:
            async with self.session.get(f"{ITUNES}/search", params={"term": f"{artist} {title}", "entity": "song",
                                                                     "limit": "5"},
                                        timeout=aiohttp.ClientTimeout(total=6)) as resp:
                results = (await resp.json(content_type=None)).get("results") or [] if resp.status == 200 else []
            same = lambda a, b: re.sub(r"\W+", " ", a.casefold()).strip() == re.sub(r"\W+", " ", b.casefold()).strip()  # noqa: E731
            match = next((r for r in results if same(r.get("trackName", ""), title) and same(r.get("artistName", ""), artist)), None)
            if match and match.get("artworkUrl100"):
                url = match["artworkUrl100"].replace("100x100bb", "600x600bb")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None  # not remembered: the next poll asks again
        self.song_art.put(key, url)
        return url or None

    # --- media ---

    async def _count_play(self, track_id: str) -> None:
        try:
            await self._get(f"/json/url/{track_id}")
        except ServiceError as e:
            debuglog.debug("radio", "click not counted", error=str(e))

    async def stream(self, track_id: str) -> StreamSource:
        info = await self._station_info(track_id)
        url = info.get("url_resolved") or info.get("url")
        if not url:
            raise ServiceError("This station has no stream address")
        # Radio Browser's etiquette: count a play, which also keeps its popularity lists honest (in the background:
        # the song doesn't wait for the directory)
        asyncio.ensure_future(self._count_play(track_id))
        self._keep(info)
        hls = bool(info.get("hls")) or ".m3u8" in url.split("?")[0]
        return StreamSource(url=url, hls=hls)

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith(("https://", "http://")):
            raise ServiceError("Bad art id")
        # some logo hosts (DuckDuckGo's icon cache among them) refuse requests that don't say who they are
        return StreamSource(url=art_id, headers={"User-Agent": USER_AGENT})
