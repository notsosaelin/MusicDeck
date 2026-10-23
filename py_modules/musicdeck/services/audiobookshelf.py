"""
Audiobookshelf (audiobookshelf.org): a self-hosted server for audiobooks and podcasts, through the
REST API its own apps use.

Sign-in is a username and password, exchanged on save for Audiobookshelf's tokens (the password is
never stored): an access token that lasts an hour and a refresh token that renews it, replaced as
they're used. Servers older than 2.26 hand out one token that doesn't expire instead. An API key
(made by an admin under Settings → API Keys) can be pasted as the password, with no username.
Tokens travel in headers the stream server adds, never in URLs the UI sees.

Books and podcasts are both "albums": a book's audio files and a podcast's episodes are its
tracks. A book file holding several chapters lists them as synced "lyrics", so the full-screen
view can jump between them. Where each book and episode was left is kept by the server (feature
"progress") and shared with Audiobookshelf's own apps: a book resumes where any of them left it.

config: {"url", "username", "user_id", "token" (access token or API key), "refresh_token"}

Ids: book "b:<item>", podcast "p:<item>", author "a:<author>", series "s:<library>:<series>",
     library "l:<library>", collection "c:<id>", playlist "pl:<id>"
     tracks: a book's audio file "f:<item>:<ino>", an episode "e:<item>:<episode>"
     art: an item's cover "i:<item>", an author's photo "a:<author>"
"""
import asyncio
import base64
import json
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from .. import feeds, models
from ..net import tls
from .base import MusicService, ServiceError, StreamSource

LIST_LIMIT = 500
SHELF_SIZE = 12
# books in "Continue listening" are fetched one by one, to find the file each was left in
CONTINUE_BOOKS = 8
# what Steam's browser engine decodes (Audiobookshelf's codec names, as ffprobe reports them)
PLAYABLE = {"mp3", "aac", "flac", "opus", "vorbis"}
# resuming replays a few seconds for context; this close to the end, a book or episode is finished
RESUME_REWIND = 5
PLAYED_REMAINING = 60
# how long a fetched book or podcast, and the user's progress, are reused
ITEM_TTL = 5 * 60
PROGRESS_TTL = 10
ITEM_CACHE = 64
# an access token this close to expiring is renewed before it's handed to the stream server
TOKEN_MARGIN = 5 * 60
ID = re.compile(r"^[\w-]{1,64}$")  # Audiobookshelf ids are UUIDs; inodes are digits
# the server's own shelves, in the order shown, with MusicDeck's names for them
SHELVES = {
    "newest-episodes": "New episodes",
    "recently-added": "Recently added",
    "continue-series": "Continue series",
    "listen-again": "Listen again",
    "discover": "Discover",
    "recent-series": "Recent series",
    "newest-authors": "Authors",
}


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls()),
        timeout=aiohttp.ClientTimeout(total=20))


def _normalise_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


def _id(value: str) -> str:
    """An id from MusicDeck's own ids, checked before it goes into a request path."""
    if not ID.match(value or ""):
        raise ServiceError("Not found on the Audiobookshelf server")
    return value


def _date(ms: Optional[float]) -> str:
    if not ms:
        return ""
    when = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return f"{when.day} {when:%b %Y}"


def _minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def _expiry(token: str) -> Optional[float]:
    """When a token (a JWT) expires, or None if it doesn't say (old tokens, most API keys)."""
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _meta(item: dict[str, Any]) -> dict[str, Any]:
    return (item.get("media") or {}).get("metadata") or {}


async def _call(session: aiohttp.ClientSession, method: str, url: str, token: str = "",
                headers: Optional[dict[str, str]] = None, **kwargs: Any) -> Any:
    """One API request. Raises ServiceError; a refused token is "auth" (see _request)."""
    sent = {**({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
    try:
        async with session.request(method, url, headers=sent, **kwargs) as resp:
            if resp.status == 401:
                raise ServiceError("auth")
            if resp.status == 404:
                raise ServiceError("Not found on the Audiobookshelf server")
            if resp.status == 429:
                raise ServiceError("Audiobookshelf asked to slow down; try again in a minute")
            if resp.status >= 400:
                raise ServiceError(f"Audiobookshelf returned HTTP {resp.status}")
            if "json" not in resp.headers.get("Content-Type", ""):
                return None  # "OK" and the like
            return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise ServiceError(f"Could not reach the Audiobookshelf server: {e or 'timed out'}") from e


def _tokens(user: dict[str, Any]) -> dict[str, str]:
    """The tokens a sign-in or refresh returned: access + refresh, or the old never-expiring one."""
    if user.get("accessToken"):
        return {"token": user["accessToken"], "refresh_token": user.get("refreshToken") or ""}
    if user.get("token"):
        return {"token": user["token"], "refresh_token": ""}
    raise ServiceError("Audiobookshelf didn't hand out a token")


class AudiobookshelfService(MusicService):
    type = "audiobookshelf"
    display_name = "Audiobookshelf"
    features = frozenset({"progress", "lyrics"})
    downloadable = True

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.refreshing: Optional[asyncio.Lock] = None
        self.libraries: list[dict[str, Any]] = []
        # item id -> (fetched at, expanded item), least recently used first
        self.items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        # "<item>" or "<item>:<episode>" -> the user's progress there, as the server reports it
        self.progress: dict[str, dict[str, Any]] = {}
        self.progress_at = 0.0

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        return f"{config['url']}|{config['user_id']}" if config.get("url") and config.get("user_id") else None

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """A username and password become tokens here and are dropped; a password with no username
        is an API key; editing without either keeps the stored tokens."""
        url = _normalise_url(config.get("url") or (previous or {}).get("url") or "")
        if not url:
            raise ServiceError("Enter the server address")
        username = (config.get("username") or "").strip()
        password = config.get("password") or ""
        async with _session() as session:
            try:
                if username and "password" in config:
                    data = await _call(session, "POST", f"{url}/login", headers={"x-return-tokens": "true"},
                                       json={"username": username, "password": password})
                    user = (data or {}).get("user") or {}
                    return {"url": url, "username": user.get("username") or username, "user_id": user.get("id"),
                            **_tokens(user)}
                if password:  # an API key
                    me = await _call(session, "GET", f"{url}/api/me", password.strip())
                    return {"url": url, "username": me.get("username") or "", "user_id": me.get("id"),
                            "token": password.strip(), "refresh_token": ""}
            except ServiceError as e:
                if str(e) == "auth":
                    raise ServiceError("Audiobookshelf didn't accept that username and password" if username
                                       else "Audiobookshelf didn't accept that API key") from e
                raise
        if previous and previous.get("token") and previous.get("url") == url:
            return {**previous, "username": previous.get("username", "")}
        raise ServiceError("Enter your username and password, or an API key as the password")

    async def start(self) -> None:
        self.session = _session()
        self.refreshing = asyncio.Lock()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    # --- API ---

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """A request as the signed-in user; an expired access token is renewed once and retried."""
        assert self.session and self.refreshing
        token = self.config.get("token", "")
        try:
            return await _call(self.session, method, self.config["url"] + path, token, **kwargs)
        except ServiceError as e:
            if str(e) != "auth":
                raise
        async with self.refreshing:
            # another request may have renewed it while this one waited
            if self.config.get("token") == token and not await self._refresh():
                raise ServiceError("Audiobookshelf didn't accept the sign-in; edit the source and sign in again")
        try:
            return await _call(self.session, method, self.config["url"] + path, self.config.get("token", ""), **kwargs)
        except ServiceError as e:
            if str(e) == "auth":
                raise ServiceError("Audiobookshelf didn't accept the sign-in; edit the source and sign in again") from e
            raise

    async def _refresh(self) -> bool:
        """Swaps the refresh token for new tokens (both are replaced) and saves them."""
        refresh = self.config.get("refresh_token")
        if not refresh:
            return False
        assert self.session
        try:
            data = await _call(self.session, "POST", self.config["url"] + "/auth/refresh",
                               headers={"x-refresh-token": refresh})
            self.config.update(_tokens((data or {}).get("user") or {}))
        except ServiceError:
            return False
        if self.save_config:
            self.save_config(self.config)
        return True

    async def _get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, params={k: str(v) for k, v in params.items() if v is not None})

    async def _libraries(self) -> list[dict[str, Any]]:
        if not self.libraries:
            data = await self._get("/api/libraries")
            self.libraries = [lib for lib in (data or {}).get("libraries") or []
                              if lib.get("mediaType") in ("book", "podcast")]
        return self.libraries

    async def _item(self, item_id: str) -> dict[str, Any]:
        """A book or podcast with its files, chapters and episodes (kept a few minutes)."""
        item_id = _id(item_id)
        hit = self.items.get(item_id)
        if hit and time.time() - hit[0] < ITEM_TTL:
            self.items.move_to_end(item_id)
            return hit[1]
        item = await self._get(f"/api/items/{item_id}", expanded=1)
        self.items[item_id] = (time.time(), item)
        while len(self.items) > ITEM_CACHE:
            self.items.popitem(last=False)
        return item

    async def _load_progress(self, fresh: bool = False) -> None:
        """The user's progress everywhere, from the server (where other apps update it too)."""
        if not fresh and time.time() - self.progress_at < PROGRESS_TTL:
            return
        me = await self._get("/api/me")
        self.progress = {(f"{p['libraryItemId']}:{p['episodeId']}" if p.get("episodeId") else p["libraryItemId"]): p
                         for p in (me or {}).get("mediaProgress") or [] if p.get("libraryItemId")}
        self.progress_at = time.time()

    # --- mapping ---

    def _art(self, item: dict[str, Any]) -> Optional[str]:
        return f"i:{item['id']}" if (item.get("media") or {}).get("coverPath") else None

    def _collection(self, item: dict[str, Any]) -> dict[str, Any]:
        meta = _meta(item)
        if item.get("mediaType") == "podcast":
            return models.collection(f"p:{item['id']}", "album", meta.get("title") or "Podcast",
                                     meta.get("author") or "", self._art(item))
        return models.collection(f"b:{item['id']}", "album", meta.get("title") or "Book",
                                 meta.get("authorName") or "", self._art(item))

    @staticmethod
    def _author(author: dict[str, Any]) -> dict[str, Any]:
        books = author.get("numBooks")
        return models.collection(f"a:{author['id']}", "artist", author.get("name") or "Author",
                                 models.count(books, "book") if books else "",
                                 f"a:{author['id']}" if author.get("imagePath") else None)

    def _series(self, library_id: str, series: dict[str, Any], books: list[dict[str, Any]]) -> dict[str, Any]:
        """A series, pictured by its first book's cover."""
        return models.collection(f"s:{library_id}:{series['id']}", "album", series.get("name") or "Series",
                                 models.count(len(books), "book") if books else "Series",
                                 self._art(books[0]) if books else None)

    def _book_tracks(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        """A book's audio files, in order; several files are named after the chapter each starts."""
        media = item.get("media") or {}
        meta = _meta(item)
        files = media.get("tracks") or []
        chapters = media.get("chapters") or []
        authors = meta.get("authors") or []
        p = self.progress.get(item["id"]) or {}
        at = p.get("currentTime") or 0
        tracks = []
        for f in files:
            start, length = f.get("startOffset") or 0, f.get("duration") or 0
            if len(files) == 1:
                title = meta.get("title") or "Book"
            else:
                title = (next((c["title"] for c in chapters if abs((c.get("start") or 0) - start) < 1 and c.get("title")), None)
                         or (f.get("metaTags") or {}).get("tagTitle") or f"Part {f.get('index') or len(tracks) + 1}")
            # where the user is, per file: finished files are "Played", the current one shows what's left
            if p.get("isFinished") or (at >= start + length - 1 and length):
                status = "Played" if p else ""
            elif start <= at < start + length and at > 0:
                status = f"{_minutes(start + length - at)} left"
            else:
                status = ""
            line = " · ".join(x for x in (meta.get("authorName") or "", status) if x)
            tracks.append(models.track(
                f"f:{item['id']}:{f.get('ino')}", title, line, meta.get("title") or "", length, self._art(item),
                album_id=f"b:{item['id']}", artist_id=f"a:{authors[0]['id']}" if len(authors) == 1 else None))
        return tracks

    def _episode(self, item: dict[str, Any], episode: dict[str, Any]) -> dict[str, Any]:
        p = self.progress.get(f"{item['id']}:{episode['id']}") or {}
        duration = episode.get("duration") or (episode.get("audioFile") or {}).get("duration") or 0
        if p.get("isFinished"):
            status = "Played"
        elif p.get("currentTime"):
            status = f"{_minutes((p.get('duration') or duration) - p['currentTime'])} left"
        else:
            status = ""
        line = " · ".join(x for x in (_date(episode.get("publishedAt")), status) if x)
        return models.track(f"e:{item['id']}:{episode['id']}", episode.get("title") or "Episode", line,
                            _meta(item).get("title") or "", duration, self._art(item), album_id=f"p:{item['id']}")

    def _episodes(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        episodes = sorted((item.get("media") or {}).get("episodes") or [],
                          key=lambda e: e.get("publishedAt") or 0, reverse=True)
        return [self._episode(item, e) for e in episodes[:LIST_LIMIT]]

    # --- browsing ---

    async def test(self) -> str:
        await self._load_progress(fresh=True)
        self.libraries = []
        libraries = await self._libraries()
        noun = "library" if len(libraries) == 1 else "libraries"
        return f"Signed in as {self.config.get('username') or 'you'} · {len(libraries)} {noun}"

    async def home(self) -> list[dict[str, Any]]:
        libraries = await self._libraries()
        shelves, _ = await asyncio.gather(
            asyncio.gather(*[self._get(f"/api/libraries/{lib['id']}/personalized", limit=SHELF_SIZE)
                             for lib in libraries], return_exceptions=True),
            self._load_progress())
        several = len(libraries) > 1
        continuing: list[tuple[float, Any]] = []  # (last update, a track or a book item id)
        sections = []
        for lib, found in zip(libraries, shelves):
            if isinstance(found, Exception):
                continue
            for shelf in found:
                entities = shelf.get("entities") or []
                if shelf.get("id") == "continue-listening":
                    for e in entities:
                        if shelf.get("type") == "episode" and e.get("recentEpisode"):
                            key = f"{e['id']}:{e['recentEpisode']['id']}"
                            continuing.append(((self.progress.get(key) or {}).get("lastUpdate") or 0,
                                               self._episode(e, e["recentEpisode"])))
                        elif shelf.get("type") == "book":
                            continuing.append(((self.progress.get(e["id"]) or {}).get("lastUpdate") or 0, e["id"]))
                    continue
                title = SHELVES.get(shelf.get("id"))
                if not title:
                    continue
                if several:
                    title = f"{title} · {lib.get('name')}"
                if shelf.get("type") == "episode":
                    sections.append(models.section(title, [], [self._episode(e, e["recentEpisode"])
                                                               for e in entities if e.get("recentEpisode")]))
                elif shelf.get("type") == "authors":
                    sections.append(models.section(title, [self._author(a) for a in entities]))
                elif shelf.get("type") == "series":
                    sections.append(models.section(title, [self._series(lib["id"], s, s.get("books") or [])
                                                          for s in entities]))
                else:
                    sections.append(models.section(title, [self._collection(e) for e in entities]))
        sections.sort(key=lambda s: list(SHELVES.values()).index(s["title"].split(" · ")[0]))
        if continuing:
            sections.insert(0, models.section("Continue listening", [], await self._continue(continuing)))
        sections.append(models.section("Libraries", [
            models.collection(f"l:{lib['id']}", "folder", lib.get("name") or "Library",
                              "Podcasts" if lib.get("mediaType") == "podcast" else "Audiobooks")
            for lib in libraries]))
        return [s for s in sections if s["items"] or s["tracks"]]

    async def _continue(self, continuing: list[tuple[float, Any]]) -> list[dict[str, Any]]:
        """"Continue listening": episodes as they are, books as the file each was left in."""
        continuing.sort(key=lambda pair: pair[0], reverse=True)
        book_ids = [x for _, x in continuing if isinstance(x, str)][:CONTINUE_BOOKS]
        books = dict(zip(book_ids, await asyncio.gather(*[self._item(b) for b in book_ids], return_exceptions=True)))
        tracks = []
        for _, x in continuing:
            if not isinstance(x, str):
                tracks.append(x)
                continue
            item = books.get(x)
            if not isinstance(item, dict):
                continue
            files = self._book_tracks(item)
            current = next((t for t in files if t["artist"].endswith(" left")), files[0] if files else None)
            if current:
                tracks.append(current)
        return tracks

    # --- media center ---

    NOW_PLAYING = "book"

    async def details(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        kind, _, rest = str(track.get("id", "")).partition(":")
        item_id, _, sub = rest.partition(":")
        if kind not in ("f", "e") or not item_id:
            return None
        item = await self._item(item_id)
        meta = _meta(item)
        if kind == "e":
            episode = next((e for e in (item.get("media") or {}).get("episodes") or [] if e.get("id") == sub), {})
            return {"notes": feeds.notes_text(episode.get("description") or meta.get("description") or ""),
                    "facts": [["Podcast", meta.get("title") or ""]]}
        series = ", ".join(s.get("name", "") + (f" #{s['sequence']}" if s.get("sequence") else "")
                           for s in meta.get("series") or [] if s.get("name"))
        duration = (item.get("media") or {}).get("duration") or 0
        facts = [[label, value] for label, value in (
            ("By", meta.get("authorName") or ""), ("Read by", meta.get("narratorName") or ""),
            ("Series", series), ("Published", str(meta.get("publishedYear") or "")),
            ("Length", _minutes(duration) if duration else ""),
            ("Genres", ", ".join(meta.get("genres") or []))) if value]
        return {"notes": feeds.notes_text(meta.get("description") or ""), "facts": facts}

    TABS = (("home", "Home"), ("library", "Library"), ("authors", "Authors"), ("series", "Series"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            return [s for s in await self.home() if not models.is_categories(s)]
        libraries = await self._libraries()
        books = [lib for lib in libraries if lib.get("mediaType") == "book"]
        several = len(libraries) > 1
        if tab_id == "library":
            pages = await asyncio.gather(*[self.open("folder", f"l:{lib['id']}") for lib in libraries],
                                         return_exceptions=True)
            return [models.section(lib.get("name") or "Library", page["collections"], display="grid")
                    for lib, page in zip(libraries, pages) if isinstance(page, dict)]
        if tab_id == "authors":
            found = await asyncio.gather(*[self._get(f"/api/libraries/{lib['id']}/authors") for lib in books],
                                         return_exceptions=True)
            return [models.section(f"Authors · {lib.get('name')}" if several else "Authors",
                                   [self._author(a) for a in (data or {}).get("authors") or []], display="grid")
                    for lib, data in zip(books, found) if isinstance(data, dict)]
        if tab_id == "series":
            found = await asyncio.gather(*[self._get(f"/api/libraries/{lib['id']}/series", limit=LIST_LIMIT,
                                                     sort="name") for lib in books], return_exceptions=True)
            return [models.section(f"Series · {lib.get('name')}" if several else "Series",
                                   [self._series(lib["id"], s, s.get("books") or [])
                                    for s in (data or {}).get("results") or []], display="grid")
                    for lib, data in zip(books, found) if isinstance(data, dict)]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        prefix, _, rest = id.partition(":")
        if prefix in ("b", "p"):
            item, _ = await asyncio.gather(self._item(rest), self._load_progress())
            meta = _meta(item)
            if prefix == "p":
                episodes = self._episodes(item)
                subtitle = " · ".join(x for x in (meta.get("author"), models.count(len(episodes), "episode")) if x)
                return models.page(meta.get("title") or "Podcast", subtitle, self._art(item), tracks=episodes,
                                   notice=None if episodes else "No episodes on the server yet")
            tracks = self._book_tracks(item)
            people = [meta.get("authorName") or ""]
            if meta.get("narratorName"):
                people.append(f"read by {meta['narratorName']}")
            duration = (item.get("media") or {}).get("duration") or 0
            subtitle = " · ".join(x for x in (", ".join(filter(None, people)), duration and _minutes(duration)) if x)
            return models.page(meta.get("title") or "Book", subtitle, self._art(item), tracks=tracks,
                               notice=None if tracks else "This book has no audio (an ebook only)")
        if prefix == "a":
            author = await self._get(f"/api/authors/{_id(rest)}", include="items")
            items = author.get("libraryItems") or []
            return models.page(author.get("name") or "Author", models.count(len(items), "book"),
                               f"a:{author['id']}" if author.get("imagePath") else None,
                               collections=[self._collection(i) for i in items])
        if prefix == "s":
            library_id, _, series_id = rest.partition(":")
            # filtered by series, the server lists the books in series order, each with its series
            series = base64.b64encode(_id(series_id).encode()).decode()
            data = await self._get(f"/api/libraries/{_id(library_id)}/items", filter=f"series.{series}",
                                   limit=LIST_LIMIT)
            items = (data or {}).get("results") or []
            name = ((_meta(items[0]).get("series") or {}).get("name") if items else None) or "Series"
            return models.page(name, models.count(len(items), "book"), self._art(items[0]) if items else None,
                               collections=[self._collection(i) for i in items])
        if prefix == "l":
            library_id = _id(rest)
            lib = next((x for x in await self._libraries() if x["id"] == library_id), None)
            if not lib:
                raise ServiceError("Not found on the Audiobookshelf server")
            data = await self._get(f"/api/libraries/{library_id}/items", sort="media.metadata.title",
                                   limit=LIST_LIMIT, minified=1)
            items = (data or {}).get("results") or []
            noun = "podcast" if lib.get("mediaType") == "podcast" else "book"
            return models.page(lib.get("name") or "Library", models.count((data or {}).get("total") or len(items), noun),
                               collections=[self._collection(i) for i in items])
        if prefix == "c":
            data = await self._get(f"/api/collections/{_id(rest)}")
            books = data.get("books") or []
            return models.page(data.get("name") or "Collection", models.count(len(books), "book"),
                               collections=[self._collection(b) for b in books])
        if prefix == "pl":
            return await self._playlist(_id(rest))
        raise ServiceError("Not found on the Audiobookshelf server")

    async def _playlist(self, playlist_id: str) -> dict[str, Any]:
        """A playlist mixes whole books and single episodes; books expand into their files."""
        data, _ = await asyncio.gather(self._get(f"/api/playlists/{playlist_id}"), self._load_progress())
        tracks = []
        for entry in data.get("items") or []:
            item = entry.get("libraryItem") or {}
            if not item.get("id"):
                continue
            if entry.get("episode"):
                tracks.append(self._episode(item, entry["episode"]))
            else:
                tracks.extend(self._book_tracks(await self._item(item["id"])))
        return models.page(data.get("name") or "Playlist", models.count(len(tracks), "track"), tracks=tracks)

    async def search(self, query: str) -> dict[str, Any]:
        libraries = await self._libraries()
        found = await asyncio.gather(*[self._get(f"/api/libraries/{lib['id']}/search", q=query, limit=25)
                                       for lib in libraries], return_exceptions=True)
        if found and all(isinstance(f, Exception) for f in found):
            raise found[0]
        await self._load_progress()
        collections, authors, series, episodes = [], [], [], []
        for lib, results in zip(libraries, found):
            if isinstance(results, Exception):
                continue
            for hit in (results.get("book") or []) + (results.get("podcast") or []):
                if hit.get("libraryItem"):
                    collections.append(self._collection(hit["libraryItem"]))
            authors += [self._author(a) for a in results.get("authors") or []]
            series += [self._series(lib["id"], s["series"], s.get("books") or [])
                       for s in results.get("series") or [] if (s.get("series") or {}).get("id")]
            for hit in results.get("episodes") or []:
                item = hit.get("libraryItem") or {}
                if item.get("recentEpisode"):
                    episodes.append(self._episode(item, item["recentEpisode"]))
        return models.page(f"Results for “{query}”", collections=authors + collections + series, tracks=episodes)

    # --- where books and episodes were left ---

    async def _place(self, track_id: str) -> tuple[str, str, float, float, float]:
        """(progress path, cache key, where the track starts in its book, its length, the book's length)."""
        prefix, _, rest = track_id.partition(":")
        item_id, _, sub = rest.partition(":")
        item = await self._item(item_id)
        if prefix == "e":
            episode = next((e for e in (item.get("media") or {}).get("episodes") or [] if e.get("id") == sub), None)
            if not episode:
                raise ServiceError("This episode is no longer on the server")
            duration = episode.get("duration") or (episode.get("audioFile") or {}).get("duration") or 0
            return f"{item_id}/{_id(sub)}", f"{item_id}:{sub}", 0, duration, duration
        if prefix == "f":
            f = next((f for f in (item.get("media") or {}).get("tracks") or [] if str(f.get("ino")) == sub), None)
            if not f:
                raise ServiceError("This part of the book is no longer on the server")
            return (item_id, item_id, f.get("startOffset") or 0, f.get("duration") or 0,
                    (item.get("media") or {}).get("duration") or 0)
        raise ServiceError("Not found on the Audiobookshelf server")

    async def _set_progress(self, path: str, key: str, current: float, total: float, finished: bool) -> None:
        body = {"currentTime": current, "duration": total, "progress": min(current / total, 1) if total else 0,
                "isFinished": finished}
        await self._request("PATCH", f"/api/me/progress/{path}", json=body)
        self.progress[key] = {**self.progress.get(key, {}), **body, "lastUpdate": time.time() * 1000}

    async def save_progress(self, track: dict[str, Any], position: float, duration: float) -> None:
        """The player reports how far a track got (every few seconds while it plays, and when paused)."""
        path, key, start, length, total = await self._place(str(track.get("id", "")))
        current = start + max(position, 0)
        await self._set_progress(path, key, current, total, bool(total) and total - current <= PLAYED_REMAINING)

    async def set_played(self, track: dict[str, Any], played: bool) -> None:
        """Marking a book's file played moves the book to the end of it (finishing the book at its last
        file); unplayed moves it back to the file's start."""
        path, key, start, length, total = await self._place(str(track.get("id", "")))
        if played:
            current = start + length
            await self._set_progress(path, key, current, total, total - current <= PLAYED_REMAINING)
        else:
            await self._set_progress(path, key, start, total, False)

    async def resume_position(self, track_id: str) -> float:
        """Where to start a track: where the user is in it (as any of their apps left it), or its start."""
        path, key, start, length, total = await self._place(track_id)
        await self._load_progress(fresh=True)
        p = self.progress.get(key) or {}
        at = p.get("currentTime") or 0
        if p.get("isFinished") or not start <= at < start + length:
            return 0
        return max(at - start - RESUME_REWIND, 0)

    # --- chapters, as synced "lyrics" ---

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not str(track.get("id", "")).startswith("f:"):
            return None
        _, key, start, length, _ = await self._place(track["id"])
        chapters = ((await self._item(key)).get("media") or {}).get("chapters") or []
        inside = [c for c in chapters if start - 1 < (c.get("start") or 0) < start + length - 1]
        if len(inside) < 2:
            return None
        return {"synced": True, "source": "", "title": "Chapters",
                "lines": [{"t": max((c.get("start") or 0) - start, 0), "text": c.get("title") or ""} for c in inside]}

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        prefix, _, rest = track_id.partition(":")
        item_id, _, sub = rest.partition(":")
        item = await self._item(item_id)
        media = item.get("media") or {}
        if prefix == "e":
            episode = next((e for e in media.get("episodes") or [] if e.get("id") == sub), None)
            audio = (episode or {}).get("audioFile")
        elif prefix == "f":
            audio = next((f for f in media.get("tracks") or [] if str(f.get("ino")) == sub), None)
        else:
            audio = None
        if not audio or not audio.get("ino"):
            raise ServiceError("Not found on the Audiobookshelf server")
        codec = (audio.get("codec") or "").lower()
        if codec not in PLAYABLE and not codec.startswith("pcm"):
            raise ServiceError(f"This file's format ({codec or 'unknown'}) can't be played in Steam")
        headers, expires = await self._auth()
        return StreamSource(url=f"{self.config['url']}/api/items/{_id(item_id)}/file/{_id(str(audio['ino']))}",
                            headers=headers, content_type=audio.get("mimeType"), expires=expires)

    async def art(self, art_id: str) -> StreamSource:
        prefix, _, rest = art_id.partition(":")
        paths = {"i": "/api/items/{}/cover", "a": "/api/authors/{}/image"}
        if prefix not in paths:
            raise ServiceError("Bad art id")
        headers, _ = await self._auth()
        return StreamSource(url=self.config["url"] + paths[prefix].format(_id(rest)) + "?width=600", headers=headers)

    async def _auth(self) -> tuple[dict[str, str], Optional[float]]:
        """Headers the stream server sends upstream, and when they stop working. Its requests don't
        go through _request, so a token about to expire is renewed here first."""
        token = self.config.get("token", "")
        expires = _expiry(token)
        if expires and expires - time.time() < TOKEN_MARGIN:
            assert self.refreshing
            async with self.refreshing:
                if self.config.get("token") == token:
                    await self._refresh()
            token = self.config.get("token", "")
            expires = _expiry(token)
        return {"Authorization": f"Bearer {token}"}, expires
