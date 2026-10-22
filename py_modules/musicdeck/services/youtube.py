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
* yt-dlp runs in a persistent worker process on the system's Python (see ytdlp_worker.py), not
  inside Decky's frozen interpreter, which lacks parts of the standard library yt-dlp needs.
"""
import asyncio
import itertools
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .. import debuglog, models, ytdlp_update
from .base import MusicService, ServiceError, StreamSource

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
# Shipped in the plugin's bin/; a copy in the data folder also works (e.g. updated or side-loaded)
QJS_NAME = "qjs"
# Once the fast path has failed, go straight to QuickJS for a while instead of paying twice
NEEDS_JS_FOR = 6 * 3600
# opus (itag 251, ~160 kbps) first, then AAC; both play natively in Steam's Chromium
AUDIO_FORMAT = "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio"


WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ytdlp_worker.py")
PY_MODULES = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SYSTEM_PYTHONS = ["/usr/bin/python3", "/usr/local/bin/python3", "python3"]


def _system_python() -> Optional[str]:
    """A complete system Python >= 3.10 (yt-dlp's minimum)."""
    override = os.environ.get("MUSICDECK_PYTHON")  # for unusual distros (and tests)
    for candidate in ([override] if override else []) + SYSTEM_PYTHONS:
        path = shutil.which(candidate) if os.sep not in candidate else (candidate if os.path.isfile(candidate) else None)
        if not path:
            continue
        try:
            out = subprocess.run([path, "-c", "import sys; print(sys.version_info >= (3, 10))"],
                                 capture_output=True, text=True, timeout=10, env=_clean_env())
            if out.stdout.strip() == "True":
                return path
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


class YtdlpWorker:
    """Long-lived yt-dlp process; requests are JSON lines, answered out of order by id."""

    def __init__(self, runtime_dir: str) -> None:
        self.runtime_dir = runtime_dir
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.pending: dict[int, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.lock = asyncio.Lock()
        self.reader: Optional[asyncio.Task] = None
        self.version = ""

    async def _ensure(self) -> None:
        async with self.lock:
            if self.proc and self.proc.returncode is None:
                return
            python = await asyncio.to_thread(_system_python)
            if not python:
                raise ServiceError("YouTube playback needs the system's Python 3.10 or newer")
            self.proc = await asyncio.create_subprocess_exec(
                python, "-u", WORKER_SCRIPT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=_clean_env(ytdlp_update.active_path(self.runtime_dir)),
                limit=8 * 1024 * 1024)
            assert self.proc.stdout
            ready = json.loads(await asyncio.wait_for(self.proc.stdout.readline(), 30))
            self.version = ready.get("version", "")
            debuglog.debug("youtube", "yt-dlp worker started", python=python, yt_dlp=self.version)
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
        await self._ensure()
        assert self.proc and self.proc.stdin
        rid = next(self.ids)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[rid] = future
        self.proc.stdin.write((json.dumps({"id": rid, "op": op, **args}) + "\n").encode())
        await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(rid, None)

    async def restart(self) -> str:
        await self.stop()
        self.proc = None
        await self._ensure()
        return self.version

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        if self.reader:
            self.reader.cancel()


def _ensure_stdlib_stubs() -> None:
    """Decky's frozen Python leaves out some stdlib modules; importing them can then pick up the
    system Python's copy, which may use newer syntax and fail. ytmusicapi only uses `webbrowser`
    for its OAuth sign-in, which we never call, so an empty stand-in is enough."""
    import sys
    import types
    try:
        import webbrowser  # noqa: F401
    except Exception as e:
        debuglog.debug("youtube", "stubbing webbrowser", error=str(e)[:200])
        sys.modules["webbrowser"] = types.ModuleType("webbrowser")


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


class YouTubeService(MusicService):
    """config: {"include_videos": bool}  (search also lists plain YouTube videos; default on)"""

    type = "youtube"
    display_name = "YouTube Music"
    features = frozenset({"radio", "lyrics"})
    downloadable = True

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self._ytm = None
        self._ytm_lock = threading.Lock()
        self._needs_js_until = 0.0
        self._lyrics_ids: dict[str, Optional[str]] = {}
        self.worker = YtdlpWorker(runtime_dir)
        self.update_status = ""
        self.qjs_path = next((p for p in (os.path.join(runtime_dir, "bin", QJS_NAME),
                                          os.path.join(PLUGIN_ROOT, "bin", QJS_NAME)) if os.path.isfile(p)), None)

    async def start(self) -> None:
        if self.qjs_path:
            # zips extracted on some systems lose the executable bit
            mode = os.stat(self.qjs_path).st_mode
            if not mode & stat.S_IXUSR:
                try:
                    os.chmod(self.qjs_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except OSError:
                    pass
        debuglog.debug("youtube", "javascript engine", qjs=self.qjs_path)

        if ytdlp_update.check_due(self.runtime_dir):
            # quietly keep yt-dlp current; a minute in, so it never competes with startup
            asyncio.get_event_loop().call_later(60, lambda: asyncio.ensure_future(self._auto_update()))

    async def stop(self) -> None:
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
        try:
            await self.worker._ensure()
        except ServiceError:
            pass  # a broken install is exactly when an update helps
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

    @property
    def ytm(self):
        with self._ytm_lock:
            if self._ytm is None:
                _ensure_stdlib_stubs()
                from ytmusicapi import YTMusic
                self._ytm = YTMusic()  # signed out
            return self._ytm

    async def _run(self, fn, *args, **kwargs):
        """ytmusicapi and yt-dlp block; keep them off the event loop."""
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except ServiceError:
            raise
        except Exception as e:
            debuglog.debug("youtube", "call failed", fn=getattr(fn, "__name__", str(fn)), error=str(e)[:300])
            raise ServiceError(f"YouTube: {str(e).splitlines()[0][:160]}") from e

    async def test(self) -> str:
        await self._run(self.ytm.search, "test", filter="songs", limit=1)
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
        rows = await self._run(self.ytm.get_home, limit=8)
        sections = []
        for row in rows:
            contents = row.get("contents") or []
            tracks = self._tracks([c for c in contents if c.get("videoId")])
            items = [c for c in (self._collection(c) for c in contents if not c.get("videoId")) if c]
            if tracks or items:
                sections.append(models.section(row.get("title") or "For you", items, tracks))
        return sections

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "album":
            a = await self._run(self.ytm.get_album, id)
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
                a = await self._run(self.ytm.get_artist, id)
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
            p = await self._run(self.ytm.get_playlist, pid, limit=300)
            author = p.get("author") or {}
            sub = author.get("name", "") if isinstance(author, dict) else str(author)
            return models.page(p.get("title", ""), sub, _thumb(p.get("thumbnails")), tracks=self._tracks(p.get("tracks")))
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        """Both catalogs at once, each category searched on its own so none is starved: YouTube Music's
        songs, albums, artists and playlists, plus YouTube's videos (any upload, music-labelled or not)."""
        ytm = self.ytm
        jobs = {
            "songs": self._run(ytm.search, query, filter="songs", limit=40),
            "music_videos": self._run(ytm.search, query, filter="videos", limit=20),
            "albums": self._run(ytm.search, query, filter="albums", limit=20),
            "artists": self._run(ytm.search, query, filter="artists", limit=10),
            "playlists": self._run(ytm.search, query, filter="community_playlists", limit=20),
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
        data = await self._run(self.ytm.get_watch_playlist, videoId=track["id"], radio=True, limit=50)
        self._lyrics_ids[track["id"]] = data.get("lyrics")
        return [t for t in self._tracks(data.get("tracks")) if t["id"] != track["id"]]

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        browse_id = self._lyrics_ids.get(track["id"])
        if track["id"] not in self._lyrics_ids:
            data = await self._run(self.ytm.get_watch_playlist, videoId=track["id"], limit=1)
            browse_id = self._lyrics_ids[track["id"]] = data.get("lyrics")
        if not browse_id:
            return None
        found = None
        try:
            found = await self._run(self.ytm.get_lyrics, browse_id, timestamps=True)
        except ServiceError:
            pass
        if not found:
            return None
        body = found.get("lyrics")
        source = "YouTube Music"
        if found.get("hasTimestamps") and isinstance(body, list):
            lines = [{"t": (getattr(l, "start_time", 0) or 0) / 1000, "text": getattr(l, "text", "")} for l in body]
            return {"synced": True, "lines": lines, "source": source}
        if isinstance(body, str) and body.strip():
            return {"synced": False, "lines": [{"t": None, "text": x} for x in body.splitlines()], "source": source}
        return None

    # --- media ---

    async def _extract(self, video_id: str, use_js: bool) -> dict[str, Any]:
        return await self.worker.call(
            "extract", video_id=video_id, js_path=self.qjs_path if use_js else None, format=AUDIO_FORMAT,
            cachedir=os.path.join(self.runtime_dir, "yt-dlp-cache"), timeout=90 if use_js else 30)

    async def stream(self, track_id: str) -> StreamSource:
        use_js = time.time() < self._needs_js_until
        info = None
        if not use_js:
            try:
                info = await self._extract(track_id, False)
                if not info.get("url"):
                    info = None
            except Exception as e:
                debuglog.debug("youtube", "fast path failed, using QuickJS", track=track_id, error=str(e)[:200])
        if info is None:
            if not self.qjs_path:
                raise ServiceError("YouTube needs its JavaScript helper, which is missing; reinstall MusicDeck")
            started = time.time()
            info = await self._extract(track_id, True)
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
