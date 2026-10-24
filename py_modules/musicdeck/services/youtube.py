"""
YouTube Music and YouTube, signed out, the way NewPipe-style clients work:

* Browsing (home, search, artists, albums, playlists, radio, lyrics) comes from YouTube Music's
  internal API via ytmusicapi. No account or API key.
* Search also offers plain YouTube videos (via yt-dlp's search), so uploads that aren't labelled
  as music are still findable from the same source.
* Audio comes from yt-dlp. YouTube increasingly requires solving a JavaScript challenge to get
  stream URLs; yt-dlp first tries without a JS engine (fast, ~1 s) and falls back to the bundled
  QuickJS-NG binary (~5 s) when YouTube insists, so playback survives YouTube tightening the
  no-engine path.
* Both run in a worker process on the system's Python (see ytdlp_worker.py), not inside Decky's
  frozen interpreter, which lacks parts of the standard library yt-dlp needs. The worker holds most
  of the memory YouTube takes, so it's stopped after a few minutes without YouTube and started
  again (in about a second) when it's next needed.
"""
import asyncio
import itertools
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .. import debuglog, models, native, ytdlp_update
from ..background import spawn
from ..cache import Cache
from .base import LocalLikes, MusicService, ServiceError, StreamSource, rows_or_tiles

# Shipped in the plugin's bin/; a copy in the data folder also works (e.g. updated or side-loaded)
QJS_NAME = "qjs"
# Once the fast path has failed, go straight to QuickJS for a while instead of paying twice
NEEDS_JS_FOR = 6 * 3600
# the worker stops after this long without a request (it takes ~80 MB while running)
IDLE_STOP = 5 * 60
# what YouTube's ids look like; nothing else is put into a YouTube address for yt-dlp
VIDEO_ID = re.compile(r"^[\w-]{11}$")
# a "Moods & genres" category's params, as YouTube Music hands them out (opaque, URL-safe)
MOOD_PARAMS = re.compile(r"^[\w%=-]{4,200}$")
CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")
LYRICS_IDS_TTL = 6 * 3600
LYRICS_IDS_KEPT = 1000
# opus (itag 251, ~160 kbps) first, then AAC; both play natively in Steam's Chromium
AUDIO_FORMAT = "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio"
# for renderers that can't play Opus (most UPnP ones): AAC in MP4
COMPATIBLE_FORMAT = "bestaudio[ext=m4a]/bestaudio"


WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ytdlp_worker.py")
PY_MODULES = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
# A system Python to run yt-dlp in, as a command: Linux's own; on Windows, the py launcher an install
# from python.org adds, then python on the PATH
SYSTEM_PYTHONS = ([["py", "-3"], ["python3"], ["python"]] if native.WINDOWS
                  else [["/usr/bin/python3"], ["/usr/local/bin/python3"], ["python3"]])


def _system_python() -> Optional[list[str]]:
    """A complete system Python >= 3.10 (yt-dlp's minimum), as the command that starts it. Windows'
    "python" that only opens the Microsoft Store (WindowsApps) is passed over."""
    override = os.environ.get("MUSICDECK_PYTHON")  # for unusual distros (and tests)
    for candidate in ([[override]] if override else []) + SYSTEM_PYTHONS:
        name, args = candidate[0], candidate[1:]
        path = shutil.which(name) if os.sep not in name and "/" not in name else (name if os.path.isfile(name) else None)
        if not path or (native.WINDOWS and "windowsapps" in path.lower()):
            continue
        try:
            out = subprocess.run([path, *args, "-c", "import sys; print(sys.version_info >= (3, 10))"],
                                 capture_output=True, text=True, timeout=10, env=_clean_env(), **native.NO_WINDOW)
            if out.stdout.strip() == "True":
                return [path, *args]
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _clean_env(extra_path: Optional[str] = None) -> dict[str, str]:
    """Environment for the system Python: drop what PyInstaller (Decky) injects, which would make it
    load Decky's bundled libraries (e.g. an incompatible libssl) instead of the system's."""
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "_MEIPASS2"):
        env.pop(key, None)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    else:
        env.pop("LD_LIBRARY_PATH", None)
    # an installed yt-dlp update (see ytdlp_update.py) shadows the bundled copy
    env["PYTHONPATH"] = os.pathsep.join(p for p in (extra_path, PY_MODULES) if p)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class YouTubeWorker:
    """The worker process (ytdlp_worker.py); requests are JSON lines, answered out of order by id.
    Started on the first request, stopped after IDLE_STOP seconds with none."""

    def __init__(self, runtime_dir: str) -> None:
        self.runtime_dir = runtime_dir
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.pending: dict[int, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.lock = asyncio.Lock()
        self.reader: Optional[asyncio.Task] = None
        self.idle: Optional[asyncio.TimerHandle] = None

    @property
    def version(self) -> str:
        """The yt-dlp version the worker runs (or would), without starting it."""
        return ytdlp_update.installed_version(self.runtime_dir, PY_MODULES)

    async def _ensure(self) -> None:
        async with self.lock:
            if self.proc and self.proc.returncode is None:
                return
            python = await asyncio.to_thread(_system_python)
            if not python:
                raise ServiceError("YouTube playback needs Python 3.10 or newer on this system"
                                   + (" (python.org)" if native.WINDOWS else ""))
            self.proc = await asyncio.create_subprocess_exec(
                *python, "-u", WORKER_SCRIPT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=_clean_env(ytdlp_update.active_path(self.runtime_dir)),
                limit=8 * 1024 * 1024, **native.NO_WINDOW)
            assert self.proc.stdout
            await asyncio.wait_for(self.proc.stdout.readline(), 30)  # {"ready": true}
            debuglog.debug("youtube", "worker started", python=python)
            self.reader = asyncio.get_event_loop().create_task(self._read(self.proc))

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            future = self.pending.pop(msg.get("id"), None)
            if future and not future.done():
                if "error" in msg:
                    future.set_exception(ServiceError(f"YouTube: {msg['error'].splitlines()[0][:200]}"))
                else:
                    future.set_result(msg["result"])
        # worker died: fail whatever was waiting; the next request starts a fresh one
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ServiceError("YouTube helper stopped unexpectedly"))
        self.pending.clear()

    async def call(self, op: str, timeout: float = 60, **args: Any) -> Any:
        if self.idle:
            self.idle.cancel()
            self.idle = None
        rid = next(self.ids)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[rid] = future
        try:
            await self._ensure()
            assert self.proc and self.proc.stdin
            self.proc.stdin.write((json.dumps({"id": rid, "op": op, **args}) + "\n").encode())
            await self.proc.stdin.drain()
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(rid, None)
            if not self.pending:
                # one idle timer at a time (two requests finishing together would each set one)
                if self.idle:
                    self.idle.cancel()
                self.idle = asyncio.get_event_loop().call_later(
                    IDLE_STOP, lambda: spawn(self._stop_if_idle(), "YouTube helper idle stop"))

    async def _stop_if_idle(self) -> None:
        if not self.pending:
            debuglog.debug("youtube", "worker stopped while idle")
            await self.stop()

    async def restart(self) -> str:
        """A fresh worker (after an update); returns the yt-dlp version it loads."""
        await self.stop()
        self.proc = None
        return await self.call("version", timeout=60)

    async def stop(self) -> None:
        if self.idle:
            self.idle.cancel()
            self.idle = None
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        if self.reader:
            self.reader.cancel()


def _thumb(thumbnails: Optional[list[dict[str, Any]]], want: int = 300) -> Optional[str]:
    """Smallest thumbnail at least `want` px wide (sharp without being huge), else the largest."""
    if not thumbnails:
        return None
    ordered = sorted(thumbnails, key=lambda t: t.get("width") or 0)
    for t in ordered:
        if (t.get("width") or 0) >= want:
            return t.get("url")
    return ordered[-1].get("url")


def _seconds(item: dict[str, Any]) -> float:
    if item.get("duration_seconds"):
        return float(item["duration_seconds"])
    text = item.get("duration") or item.get("length") or ""
    if isinstance(text, (int, float)):
        return float(text)
    parts = [int(p) for p in re.findall(r"\d+", str(text))]
    total = 0
    for p in parts:
        total = total * 60 + p
    return float(total)


def _artists(item: dict[str, Any]) -> tuple[str, Optional[str]]:
    artists = item.get("artists") or []
    names = ", ".join(a.get("name", "") for a in artists if a.get("name"))
    first_id = next((a.get("id") for a in artists if a.get("id")), None)
    return names or item.get("author") or item.get("channel") or "", first_id


class YouTubeService(LocalLikes, MusicService):
    """config: {"include_videos": bool}  (search also lists plain YouTube videos; default on)"""

    type = "youtube"
    display_name = "YouTube Music"
    features = frozenset({"star", "radio", "lyrics", "browse"})
    downloadable = True

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self._needs_js_until = 0.0
        self.auto_update: Optional[asyncio.TimerHandle] = None
        # where each track's lyrics are (None: it has none), learnt from its watch playlist
        self._lyrics_ids: Cache[Optional[str]] = Cache(LYRICS_IDS_TTL, LYRICS_IDS_KEPT)
        self.worker = YouTubeWorker(runtime_dir)
        self.update_status = ""
        self.qjs_path = native.helper(QJS_NAME, runtime_dir)

    async def start(self) -> None:
        debuglog.debug("youtube", "javascript engine", qjs=self.qjs_path)

        if ytdlp_update.check_due(self.runtime_dir):
            # quietly keep yt-dlp current; a minute in, so it never competes with startup
            self.auto_update = asyncio.get_event_loop().call_later(
                60, lambda: spawn(self._auto_update(), "yt-dlp update"))

    async def stop(self) -> None:
        # a source removed (or restarted) in its first minute mustn't update and start a helper after
        if self.auto_update:
            self.auto_update.cancel()
        await self.worker.stop()

    def status(self) -> dict[str, Any]:
        return {"ytdlp": self.worker.version, "update": self.update_status}

    async def _auto_update(self) -> None:
        try:
            await self.update_ytdlp()
        except Exception as e:
            debuglog.debug("youtube", "automatic yt-dlp update failed", error=str(e)[:200])

    async def update_ytdlp(self) -> str:
        """Installs the newest yt-dlp if there is one; returns a short message for the UI."""
        before = self.worker.version
        self.update_status = "Checking…"
        try:
            result = await ytdlp_update.update(self.runtime_dir, before)
        except Exception as e:
            self.update_status = ""
            raise ServiceError(f"Couldn't check for updates: {e}") from e
        if not result["updated"]:
            self.update_status = ""
            return f"YouTube support is up to date (yt-dlp {before})"
        try:
            running = await self.worker.restart()
            if running != result["version"]:
                raise ServiceError(f"started {running or 'nothing'} instead of {result['version']}")
            ytdlp_update.confirm(self.runtime_dir)
            self.update_status = ""
            return f"Updated YouTube support to yt-dlp {running}"
        except Exception as e:
            ytdlp_update.rollback(self.runtime_dir)
            await self.worker.restart()
            self.update_status = ""
            raise ServiceError(f"The update didn't work and was rolled back ({e})") from e

    async def _music(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """A YouTube Music call (ytmusicapi, signed out), made by the worker."""
        try:
            return await self.worker.call("music", method=method, args=list(args), kwargs=kwargs, timeout=30)
        except ServiceError as e:
            debuglog.debug("youtube", "call failed", method=method, error=str(e)[:300])
            raise
        except asyncio.TimeoutError as e:
            raise ServiceError("YouTube Music didn't answer in time") from e

    async def test(self) -> str:
        await self._music("search", "test", filter="songs", limit=1)
        return "Connected to YouTube Music"

    # --- mapping ---

    def _track(self, s: dict[str, Any]) -> Optional[dict[str, Any]]:
        vid = s.get("videoId")
        if not vid:
            return None
        artist, artist_id = _artists(s)
        album = s.get("album") or {}
        return models.track(vid, s.get("title", ""), artist, album.get("name", "") if isinstance(album, dict) else "",
                            _seconds(s), art=_thumb(s.get("thumbnails") or s.get("thumbnail")),
                            album_id=album.get("id") if isinstance(album, dict) else None, artist_id=artist_id)

    def _tracks(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [t for t in (self._track(i) for i in items or []) if t]

    @staticmethod
    def _collection(item: dict[str, Any], kind_hint: Optional[str] = None) -> Optional[dict[str, Any]]:
        art = _thumb(item.get("thumbnails"))
        kind = kind_hint or item.get("resultType") or ""
        if item.get("playlistId") and not item.get("videoId") and kind not in ("album",):
            sub = item.get("description") or item.get("author") or ""
            if isinstance(sub, list):
                sub = ", ".join(a.get("name", "") for a in sub)
            return models.collection(item["playlistId"], "playlist", item.get("title", ""), str(sub)[:80], art)
        browse = item.get("browseId") or ""
        if kind in ("album", "single", "ep") or browse.startswith("MPRE"):
            artist, _ = _artists(item)
            sub = " · ".join(x for x in [artist, str(item.get("year") or "")] if x)
            return models.collection(browse, "album", item.get("title", ""), sub, art)
        if kind == "artist" or browse.startswith("UC"):
            return models.collection(browse, "artist", item.get("artist") or item.get("title", ""),
                                     item.get("subscribers") or "", art)
        if kind in ("playlist", "community playlist") or browse.startswith("VL"):
            return models.collection(browse, "playlist", item.get("title", ""), item.get("author") or "", art)
        return None

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        rows = await self._music("get_home", limit=8)
        sections = []
        for row in rows:
            contents = row.get("contents") or []
            tracks = self._tracks([c for c in contents if c.get("videoId")])
            items = [c for c in (self._collection(c) for c in contents if not c.get("videoId")) if c]
            if tracks or items:
                sections.append(models.section(row.get("title") or "For you", items, tracks))
        return sections

    async def browse(self) -> list[dict[str, Any]]:
        """YouTube Music's "Moods & genres" (each opens to its playlists) and its charts."""
        categories, charts = await asyncio.gather(self._music("get_mood_categories"),
                                                  self._music("get_charts", country="ZZ"), return_exceptions=True)
        sections = []
        if isinstance(charts, dict):
            # charts' shape varies by region and version: take whatever playlists it offers
            lists = [c for key in ("videos", "trending", "genres") for c in (charts.get(key) or [])
                     if isinstance(c, dict) and c.get("playlistId")]
            items = [c for c in (self._collection(c) for c in lists) if c]
            if items:
                sections.append(models.section("Charts", items))
        if isinstance(categories, dict):
            for title, entries in categories.items():
                items = [models.collection(f"mood:{e['params']}#{e.get('title', '')}", "list", e.get("title", ""))
                         for e in entries or [] if isinstance(e, dict) and MOOD_PARAMS.match(str(e.get("params", "")))]
                if items:
                    sections.append(models.section(str(title), items))
        if not sections:
            failure = categories if isinstance(categories, Exception) else charts
            if isinstance(failure, Exception):
                raise failure
        return sections

    # --- media center ---

    TABS = (("home", "Home"), ("new", "New"), ("charts", "Charts"), ("moods", "Moods"), ("genres", "Genres"))
    SHARED_TABS = frozenset({"home", "new", "charts", "moods", "genres"})

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "new":
            explore = await self._music("get_explore")
            new = [c for c in (self._collection(r, "album") for r in explore.get("new_releases") or []) if c]
            trending = self._tracks((explore.get("trending") or {}).get("items") or [])
            videos = self._tracks(explore.get("new_videos") or [])
            return [models.section("New albums and singles", new),
                    models.section("Trending", [], trending, display="grid"),
                    models.section("New music videos", [], videos, display="grid")]
        if tab_id == "charts":
            # the world's charts, and the US's (only theirs come by genre)
            world, us = await asyncio.gather(self._music("get_charts", country="ZZ"),
                                             self._music("get_charts", country="US"), return_exceptions=True)
            if isinstance(world, Exception) and isinstance(us, Exception):
                raise world

            def lists(charts: Any, key: str) -> list[dict[str, Any]]:
                found = charts.get(key) if isinstance(charts, dict) else None
                return [c for c in (self._collection(x) for x in found or [] if isinstance(x, dict)
                                    and x.get("playlistId")) if c]
            artists = [c for c in (self._collection(a, "artist") for a in
                                   (world.get("artists") if isinstance(world, dict) else None) or []
                                   if isinstance(a, dict)) if c]
            return [models.section("Top music videos", lists(world, "videos") + lists(us, "videos")
                                   + lists(world, "trending")),
                    models.section("Top artists", artists),
                    models.section("By genre", lists(us, "genres"))]
        if tab_id in ("moods", "genres"):
            sections = [s for s in await self.browse() if s["title"] != "Charts"]
            pick = [s for s in sections if (s["title"].lower() == "genres") == (tab_id == "genres")]
            return [rows_or_tiles(s) for s in pick]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id.startswith("mood:"):
            params, _, title = id[5:].partition("#")
            if not MOOD_PARAMS.match(params):
                raise ServiceError("That isn't a YouTube Music category")
            playlists = await self._music("get_mood_playlists", params)
            items = [c for c in (self._collection(p) for p in playlists or [] if isinstance(p, dict)) if c]
            return models.page(title or "Moods & genres", models.count(len(items), "playlist"), collections=items)
        if kind == "album":
            a = await self._music("get_album", id)
            artist, _ = _artists(a)
            art = _thumb(a.get("thumbnails"))
            tracks = self._tracks(a.get("tracks"))
            for t in tracks:  # album track lists omit per-track art and album id
                t["art"] = t["art"] or art
                t["album"] = t["album"] or a.get("title", "")
                t["album_id"] = t["album_id"] or id
            sub = " · ".join(x for x in [artist, str(a.get("year") or "")] if x)
            return models.page(a.get("title", ""), sub, art, tracks=tracks)
        if kind == "artist":
            try:
                a = await self._music("get_artist", id)
            except ServiceError:
                # not a YouTube Music artist (e.g. a label or live-session channel): show its uploads
                return await self._channel_page(id)
            songs = (a.get("songs") or {}).get("results") or []
            releases = [(a.get(k) or {}).get("results") or [] for k in ("albums", "singles")]
            collections = [c for c in (self._collection(r, "album") for group in releases for r in group) if c]
            videos = self._tracks((a.get("videos") or {}).get("results") or [])
            return models.page(a.get("name", ""), a.get("subscribers") or "", _thumb(a.get("thumbnails")),
                               collections=collections, tracks=self._tracks(songs),
                               extra=[{"title": "Videos", "tracks": videos}])
        if kind == "playlist":
            pid = id[2:] if id.startswith("VL") else id
            p = await self._music("get_playlist", pid, limit=300)
            author = p.get("author") or {}
            sub = author.get("name", "") if isinstance(author, dict) else str(author)
            return models.page(p.get("title", ""), sub, _thumb(p.get("thumbnails")), tracks=self._tracks(p.get("tracks")))
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        """Both catalogs at once, each category searched on its own so none is starved: YouTube Music's
        songs, albums, artists and playlists, plus YouTube's videos (any upload, music-labelled or not)."""
        jobs = {
            "songs": self._music("search", query, filter="songs", limit=40),
            "music_videos": self._music("search", query, filter="videos", limit=20),
            "albums": self._music("search", query, filter="albums", limit=20),
            "artists": self._music("search", query, filter="artists", limit=10),
            "playlists": self._music("search", query, filter="community_playlists", limit=20),
        }
        if self.config.get("include_videos", True):
            jobs["youtube"] = self._youtube_search(query, 40)
        done = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
        for name, value in done.items():
            if isinstance(value, Exception):
                debuglog.debug("youtube", "search part failed", part=name, error=str(value)[:200])
        if all(isinstance(v, Exception) for v in done.values()):
            raise next(iter(done.values()))
        got = {k: (v if isinstance(v, list) else []) for k, v in done.items()}

        # YouTube's own ranking first (it covers official videos too), then music videos it missed
        videos = list(got.get("youtube", []))
        seen = {v["id"] for v in videos}
        videos += [v for v in self._tracks(got["music_videos"]) if v["id"] not in seen]
        collections = [c for c in (self._collection(r, kind) for kind, key in
                                   (("artist", "artists"), ("album", "albums"), ("playlist", "playlists"))
                                   for r in got[key]) if c]
        return models.page(f"Results for “{query}”", collections=collections, tracks=self._tracks(got["songs"]),
                           extra=[{"title": "Videos", "tracks": videos}], tracks_first=True)

    async def _channel_page(self, channel_id: str) -> dict[str, Any]:
        if not CHANNEL_ID.match(channel_id):
            raise ServiceError("That isn't a YouTube channel")
        data = await self.worker.call("channel", channel_id=channel_id, count=60, timeout=45)
        return models.page(data.get("title") or "Channel", "YouTube channel", _thumb(data.get("thumbnails")),
                           tracks=self._flat_videos(data.get("entries") or []))

    def _flat_videos(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tracks = []
        for e in entries:
            # flat listings can include channels/playlists (YoutubeTab); only single videos are playable
            if not e.get("id") or e.get("live_status") == "is_live" or e.get("ie_key") not in (None, "Youtube"):
                continue
            tracks.append(models.track(e["id"], e.get("title") or "", e.get("channel") or e.get("uploader") or "", "",
                                       float(e.get("duration") or 0), art=_thumb(e.get("thumbnails")),
                                       artist_id=e.get("channel_id")))
        return tracks

    async def _youtube_search(self, query: str, count: int) -> list[dict[str, Any]]:
        """Plain YouTube search (any upload, not just what YouTube Music labels as music)."""
        entries = await self.worker.call("search", query=query, count=count, timeout=30)
        return self._flat_videos(entries)

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        data = await self._music("get_watch_playlist", videoId=track["id"], radio=True, limit=50)
        self._lyrics_ids.put(track["id"], data.get("lyrics"))
        return [t for t in self._tracks(data.get("tracks")) if t["id"] != track["id"]]

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        browse_id = self._lyrics_ids.get(track["id"])
        if track["id"] not in self._lyrics_ids:
            data = await self._music("get_watch_playlist", videoId=track["id"], limit=1)
            browse_id = self._lyrics_ids.put(track["id"], data.get("lyrics"))
        if not browse_id:
            return None
        found = None
        try:
            found = await self._music("get_lyrics", browse_id, timestamps=True)
        except ServiceError:
            pass
        if not found:
            return None
        body = found.get("lyrics")
        source = "YouTube Music"
        if found.get("hasTimestamps") and isinstance(body, list):
            lines = [{"t": (line.get("start_time") or 0) / 1000, "text": line.get("text") or ""} for line in body]
            return {"synced": True, "lines": lines, "source": source}
        if isinstance(body, str) and body.strip():
            return {"synced": False, "lines": [{"t": None, "text": x} for x in body.splitlines()], "source": source}
        return None

    # --- media ---

    async def _extract(self, video_id: str, use_js: bool, audio_format: str) -> dict[str, Any]:
        if not VIDEO_ID.match(video_id):
            raise ServiceError("That isn't a YouTube video")
        return await self.worker.call(
            "extract", video_id=video_id, js_path=self.qjs_path if use_js else None, format=audio_format,
            cachedir=os.path.join(self.runtime_dir, "yt-dlp-cache"), timeout=90 if use_js else 30)

    async def stream(self, track_id: str) -> StreamSource:
        return await self._stream(track_id, AUDIO_FORMAT)

    async def compatible_stream(self, track_id: str) -> StreamSource:
        return await self._stream(track_id, COMPATIBLE_FORMAT)

    async def _stream(self, track_id: str, audio_format: str) -> StreamSource:
        use_js = time.time() < self._needs_js_until
        info = None
        if not use_js:
            try:
                info = await self._extract(track_id, False, audio_format)
                if not info.get("url"):
                    info = None
            except Exception as e:
                debuglog.debug("youtube", "fast path failed, using QuickJS", track=track_id, error=str(e)[:200])
        if info is None:
            if not self.qjs_path:
                raise ServiceError("YouTube needs its JavaScript helper, which is missing; reinstall MusicDeck")
            started = time.time()
            info = await self._extract(track_id, True, audio_format)
            self._needs_js_until = time.time() + NEEDS_JS_FOR
            debuglog.debug("youtube", "resolved with QuickJS", track=track_id, seconds=round(time.time() - started, 1))
        url = info.get("url")
        if not url:
            raise ServiceError("YouTube didn't return an audio stream")
        expire = (parse_qs(urlparse(url).query).get("expire") or [None])[0]
        debuglog.debug("youtube", "format", track=track_id, format=info.get("format_id"), acodec=info.get("acodec"),
                       abr=info.get("abr"))
        return StreamSource(url=url, headers=dict(info.get("http_headers") or {}),
                            expires=float(expire) if expire and expire.isdigit() else None)

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith("https://"):
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id)
