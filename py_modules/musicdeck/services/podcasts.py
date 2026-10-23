"""
Podcasts. Shows are found in Apple's podcast directory (search, lookups and the top charts are free
and need no account); episodes come from each show's own RSS feed, read by musicdeck.feeds. Nothing
is signed in to: subscriptions live in this source's config, the rest on the device.

config: {"country": ISO code for the charts ("" = United States),
         "subscriptions": [{"feed", "title", "author", "art"}]}
podcasts-<id>-progress.json: where each episode was left, so it resumes, and what's been played:
         {episode id: {"position", "duration", "done", "updated", "track"}}

Ids carry what's needed to play: a show is "p:" + its feed URL (base64), or "a:" + an Apple id from the
charts (looked up when opened); an episode is "e:" + its audio URL. Art ids are image URLs.
"""
import asyncio
import base64
import binascii
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from .. import models
from ..cache import Cache
from ..feeds import parse_feed
from ..net import tls
from ..settings import load_json, save_json
from .base import MusicService, ServiceError, StreamSource, rows_or_tiles

SEARCH = "https://itunes.apple.com/search"
LOOKUP = "https://itunes.apple.com/lookup"
CHARTS = "https://rss.marketingtools.apple.com/api/v2/{country}/podcasts/top/{count}/podcasts.json"
USER_AGENT = "MusicDeck/0.1.0"
FEED_TTL = 30 * 60
FEEDS_KEPT = 100  # a parsed feed can be large (hundreds of episodes)
CHARTS_TTL = 6 * 3600
# Apple's podcast categories (genre ids), for the Browse tab; each opens to its chart, which only the
# older iTunes feed offers by category
CATEGORIES = [(1489, "News"), (1303, "Comedy"), (1488, "True Crime"), (1324, "Society & Culture"),
              (1321, "Business"), (1545, "Sports"), (1310, "Music"), (1309, "TV & Film"), (1487, "History"),
              (1533, "Science"), (1318, "Technology"), (1512, "Health & Fitness"), (1304, "Education"),
              (1301, "Arts"), (1483, "Fiction"), (1305, "Kids & Family"), (1502, "Leisure"),
              (1314, "Religion & Spirituality"), (1511, "Government")]
CATEGORY_CHART = "https://itunes.apple.com/{country}/rss/toppodcasts/limit=50/genre={genre}/json"
CHARTS_SIZE = 25
NEW_EPISODES = 20
# a show's page lists at most this many episodes (some feeds go back over a thousand)
MAX_EPISODES = 300
# resuming replays a few seconds for context; this close to the end, an episode counts as played
RESUME_REWIND = 5
PLAYED_REMAINING = 60
COUNTRY = re.compile(r"^[A-Z]{2}$")  # ISO 3166-1 alpha-2, as for radio


def _encode(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _decode(value: str) -> str:
    try:
        url = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        url = ""
    if not url.startswith(("https://", "http://")):
        raise ServiceError("That isn't a podcast or an episode")
    return url


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls()),
        timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": USER_AGENT})


def _big_art(url: str) -> str:
    """Apple's artwork URLs name their size; ask for one big enough for the now-playing card."""
    return url.replace("100x100bb", "600x600bb").replace("160x160bb", "600x600bb") if url else url


def _date(epoch: float) -> str:
    if not epoch:
        return ""
    when = datetime.fromtimestamp(epoch, timezone.utc)
    return f"{when.day} {when:%b %Y}"


def _minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


class PodcastService(MusicService):
    type = "podcasts"
    display_name = "Podcasts"
    features = frozenset({"progress", "browse"})
    downloadable = True

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.feeds: Cache[dict[str, Any]] = Cache(FEED_TTL, FEEDS_KEPT)
        self.charts: tuple[float, list[dict[str, Any]]] = (0, [])
        self.progress_path = os.path.join(runtime_dir, f"podcasts-{instance_id}-progress.json")
        self.progress: dict[str, dict[str, Any]] = load_json(self.progress_path)

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        country = (config.get("country") or "").upper()
        return {"country": country if COUNTRY.match(country) else "",
                "subscriptions": (previous or {}).get("subscriptions") or config.get("subscriptions") or []}

    async def start(self) -> None:
        self.session = _session()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    def remove(self) -> None:
        try:
            os.remove(self.progress_path)
        except FileNotFoundError:
            pass

    # --- the directory and feeds ---

    async def _json(self, url: str, **params: Any) -> Any:
        assert self.session
        try:
            async with self.session.get(url, params=params or None) as resp:
                if resp.status >= 400:
                    raise ServiceError(f"The podcast directory returned HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise ServiceError("Couldn't reach the podcast directory") from e

    async def _feed(self, url: str) -> dict[str, Any]:
        hit = self.feeds.get(url)
        if hit:
            return hit
        assert self.session
        try:
            async with self.session.get(url) as resp:
                if resp.status >= 400:
                    raise ServiceError(f"This podcast's feed returned HTTP {resp.status}")
                podcast = parse_feed(await resp.read())
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ServiceError("Couldn't reach this podcast's feed") from e
        except ValueError as e:
            raise ServiceError("That address isn't a podcast feed") from e
        return self.feeds.put(url, podcast)

    async def _charts(self) -> list[dict[str, Any]]:
        if time.time() - self.charts[0] < CHARTS_TTL:
            return self.charts[1]
        country = (self.config.get("country") or "us").lower()
        data = await self._json(CHARTS.format(country=country, count=CHARTS_SIZE))
        shows = [models.collection(f"a:{r['id']}", "album", r.get("name") or "Podcast", r.get("artistName") or "",
                                   _big_art(r.get("artworkUrl100") or "") or None)
                 for r in (data.get("feed") or {}).get("results") or [] if r.get("id")]
        self.charts = (time.time(), shows)
        return shows

    # --- mapping ---

    def _subscribed(self, feed: str) -> bool:
        return any(s["feed"] == feed for s in self.config.get("subscriptions") or [])

    @staticmethod
    def _show(feed: str, title: str, author: str = "", art: Optional[str] = None) -> dict[str, Any]:
        return models.collection(f"p:{_encode(feed)}", "album", title, author, art or None)

    def _status(self, track_id: str, duration: float) -> str:
        """How much is left, or "Played"; "" for an episode not started."""
        p = self.progress.get(track_id)
        if not p:
            return ""
        if p.get("done"):
            return "Played"
        left = (p.get("duration") or duration or 0) - (p.get("position") or 0)
        return f"{_minutes(left)} left" if left > 0 else ""

    def _episode(self, show_title: str, feed: str, url: str, title: str, duration: float, published: float,
                 art: Optional[str]) -> dict[str, Any]:
        track_id = f"e:{_encode(url)}"
        status = self._status(track_id, duration)
        # the date and progress ("12 min left", "Played") sit where a song's artist would; the length
        # is already shown beside every track
        line = " · ".join(x for x in (_date(published), status) if x)
        return models.track(track_id, title, line, show_title, duration, art or None,
                            album_id=f"p:{_encode(feed)}" if feed else None)

    def _episodes(self, feed: str, podcast: dict[str, Any], limit: int = MAX_EPISODES) -> list[dict[str, Any]]:
        return [self._episode(podcast["title"], feed, e["url"], e["title"], e["duration"], e["published"], e["art"])
                for e in podcast["episodes"][:limit]]

    # --- browsing ---

    async def test(self) -> str:
        shows = await self._charts()
        return f"Connected to Apple's podcast directory · {len(shows)} top shows"

    async def home(self) -> list[dict[str, Any]]:
        subscriptions = self.config.get("subscriptions") or []
        feeds = await asyncio.gather(*[self._feed(s["feed"]) for s in subscriptions], return_exceptions=True)
        charts = await asyncio.gather(self._charts(), return_exceptions=True)
        sections = []
        unfinished = sorted((p for p in self.progress.values() if not p.get("done") and p.get("position", 0) > 30),
                            key=lambda p: p.get("updated", 0), reverse=True)
        if unfinished:
            listening = [{**p["track"], "artist": self._status(p["track"]["id"], p["track"].get("duration", 0))}
                         for p in unfinished[:10] if p.get("track")]
            sections.append(models.section("Continue listening", [], listening))
        latest = []
        for sub, podcast in zip(subscriptions, feeds):
            if isinstance(podcast, dict):
                latest += [(e["published"], self._episode(podcast["title"], sub["feed"], e["url"], e["title"],
                                                          e["duration"], e["published"], e["art"]))
                           for e in podcast["episodes"][:3]]
        if latest:
            latest.sort(key=lambda pair: pair[0], reverse=True)
            sections.append(models.section("New episodes", [], [t for _, t in latest[:NEW_EPISODES]]))
        if subscriptions:
            sections.append(models.section("Your podcasts", [self._show(s["feed"], s["title"], s.get("author", ""),
                                                                        s.get("art")) for s in subscriptions]))
        if isinstance(charts[0], list) and charts[0]:
            sections.append(models.section("Top podcasts", charts[0]))
        if not sections and isinstance(charts[0], Exception):
            raise charts[0]
        return sections

    async def _feed_of(self, id: str) -> str:
        if id.startswith("p:"):
            return _decode(id[2:])
        if id.startswith("a:") and id[2:].isdigit():
            found = await self._json(LOOKUP, id=id[2:], entity="podcast")
            feed = next((r.get("feedUrl") for r in found.get("results") or [] if r.get("feedUrl")), None)
            if not feed:
                raise ServiceError("This podcast doesn't publish a feed MusicDeck can read")
            return feed
        raise ServiceError("Unknown podcast")

    async def browse(self) -> list[dict[str, Any]]:
        return [models.section("Categories", [models.collection(f"genre:{g}", "list", name)
                                              for g, name in CATEGORIES])]

    async def _category(self, genre: int) -> list[dict[str, Any]]:
        country = (self.config.get("country") or "us").lower()
        data = await self._json(CATEGORY_CHART.format(country=country, genre=genre))
        shows = []
        for e in (data.get("feed") or {}).get("entry") or []:
            show_id = ((e.get("id") or {}).get("attributes") or {}).get("im:id")
            images = e.get("im:image") or []
            art = images[-1].get("label") if images else None
            if show_id:
                shows.append(models.collection(f"a:{show_id}", "album", (e.get("im:name") or {}).get("label") or "Podcast",
                                               (e.get("im:artist") or {}).get("label") or "",
                                               art.replace("170x170bb", "600x600bb") if art else None))
        return shows

    # --- media center ---

    NOW_PLAYING = "podcast"

    async def details(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        track_id, show = str(track.get("id", "")), str(track.get("album_id") or "")
        if not track_id.startswith("e:") or not show.startswith("p:"):
            return None
        feed = _decode(show[2:])
        podcast = await self._feed(feed)
        url = _decode(track_id[2:])
        episode = next((e for e in podcast["episodes"] if e["url"] == url), None)
        facts = [[label, value] for label, value in (
            ("Show", podcast["title"]), ("By", podcast["author"]),
            ("Published", _date(episode["published"]) if episode else "")) if value]
        others = [e for e in self._episodes(feed, podcast, 40) if e["id"] != track_id][:30]
        return {"notes": (episode or {}).get("description") or podcast.get("description") or "",
                "facts": facts,
                "related": models.section(f"More from {podcast['title']}", [], others) if others else None}

    TABS = (("home", "Home"), ("yours", "Your podcasts"), ("categories", "Categories"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "yours":
            home = await self.home()
            keep = {"Your podcasts": "grid", "New episodes": "grid", "Continue listening": "shelf"}
            # subscriptions first, then their new episodes; nothing until something's subscribed to
            order = list(keep)
            sections = sorted(({**s, "display": keep[s["title"]]} for s in home if s["title"] in keep),
                              key=lambda s: (order.index(s["title"]) + 1) % len(order))
            return sections or [models.notice("Open a podcast and choose Subscribe to keep it here, "
                                              "with its new episodes as they come out.")]
        if tab_id == "categories":
            return [rows_or_tiles(s) for s in await self.browse()]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("genre:"):
            if not id[6:].isdigit():
                raise ServiceError("Unknown category")
            shows = await self._category(int(id[6:]))
            return models.page(dict(CATEGORIES).get(int(id[6:]), "Podcasts"), "Top podcasts", collections=shows)
        feed = await self._feed_of(id)
        podcast = await self._feed(feed)
        subscribed = self._subscribed(feed)
        subtitle = " · ".join(x for x in (podcast["author"], models.count(len(podcast["episodes"]), "episode")) if x)
        return models.page(
            podcast["title"], subtitle, podcast["art"] or None, tracks=self._episodes(feed, podcast),
            action={"label": "Subscribed" if subscribed else "Subscribe",
                    "call": "unsubscribe" if subscribed else "subscribe"},
            notice=None if podcast["episodes"] else "This podcast has no episodes to play yet")

    async def search(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if query.startswith(("https://", "http://")):
            # a feed address, pasted: the podcast it's for
            podcast = await self._feed(query)
            return models.page(f"Results for “{query}”",
                               collections=[self._show(query, podcast["title"], podcast["author"], podcast["art"])])
        shows, episodes = await asyncio.gather(
            self._json(SEARCH, media="podcast", entity="podcast", term=query, limit=25),
            self._json(SEARCH, media="podcast", entity="podcastEpisode", term=query, limit=15),
            return_exceptions=True)
        if isinstance(shows, Exception):
            raise shows
        collections = [self._show(r["feedUrl"], r.get("collectionName") or "Podcast", r.get("artistName") or "",
                                  r.get("artworkUrl600") or _big_art(r.get("artworkUrl100") or ""))
                       for r in shows.get("results") or [] if r.get("feedUrl")]
        tracks = []
        for r in (episodes.get("results") or []) if isinstance(episodes, dict) else []:
            if not r.get("episodeUrl"):
                continue
            released = r.get("releaseDate") or ""
            try:
                published = datetime.fromisoformat(released.replace("Z", "+00:00")).timestamp()
            except ValueError:
                published = 0
            tracks.append(self._episode(r.get("collectionName") or "", r.get("feedUrl") or "", r["episodeUrl"],
                                        r.get("trackName") or "Episode", (r.get("trackTimeMillis") or 0) / 1000,
                                        published, r.get("artworkUrl600") or r.get("artworkUrl160")))
        return models.page(f"Results for “{query}”", collections=collections, extra=[
            {"title": "Episodes", "tracks": tracks}] if tracks else None)

    async def collection_action(self, kind: str, id: str, call: str) -> str:
        """Subscribe and unsubscribe; returns what to tell the user."""
        feed = await self._feed_of(id)
        others = [s for s in self.config.get("subscriptions") or [] if s["feed"] != feed]
        podcast = await self._feed(feed)
        if call == "subscribe":
            self.config["subscriptions"] = [{"feed": feed, "title": podcast["title"], "author": podcast["author"],
                                             "art": podcast["art"]}, *others]
            message = f"Subscribed to {podcast['title']}"
        elif call == "unsubscribe":
            self.config["subscriptions"] = others
            message = f"Unsubscribed from {podcast['title']}"
        else:
            raise ServiceError("Unknown action")
        if self.save_config:
            self.save_config(self.config)
        return message

    # --- where episodes were left ---

    def _save_progress(self) -> None:
        save_json(self.progress_path, self.progress)

    async def save_progress(self, track: dict[str, Any], position: float, duration: float) -> None:
        """The player reports how far an episode got (every few seconds while it plays, and when paused)."""
        if not isinstance(track, dict) or not str(track.get("id", "")).startswith("e:"):
            return
        duration = duration or track.get("duration") or 0
        done = bool(duration) and duration - position <= PLAYED_REMAINING
        keep = ("id", "title", "album", "duration", "art", "album_id")
        self.progress[track["id"]] = {"position": max(position, 0), "duration": duration, "done": done,
                                      "updated": time.time(), "track": {k: track.get(k) for k in keep}}
        self._save_progress()

    async def set_played(self, track: dict[str, Any], played: bool) -> None:
        if not isinstance(track, dict) or not str(track.get("id", "")).startswith("e:"):
            return
        if played:
            await self.save_progress(track, track.get("duration") or 0, track.get("duration") or 0)
            self.progress[track["id"]]["done"] = True
        else:
            self.progress.pop(track["id"], None)
        self._save_progress()

    async def resume_position(self, track_id: str) -> float:
        """Where to start an episode: where it was left (a little before), or the start."""
        p = self.progress.get(track_id)
        if not p or p.get("done"):
            return 0
        return max((p.get("position") or 0) - RESUME_REWIND, 0)

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        if not track_id.startswith("e:"):
            raise ServiceError("Unknown episode")
        return StreamSource(url=_decode(track_id[2:]), headers={"User-Agent": USER_AGENT})

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith(("https://", "http://")):
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id, headers={"User-Agent": USER_AGENT})
