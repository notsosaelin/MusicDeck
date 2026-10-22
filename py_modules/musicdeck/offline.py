"""
Offline copies of MusicDeck playlists' tracks: "Download" on a playlist saves every track on the
Deck, and playback then reads the file, with no network needed.

Files are kept per (source, track), so a track in several downloaded playlists is stored once and
removed when no downloaded playlist holds it any more. Each track's artwork is kept beside it.

Only sources whose audio is the user's own or free to keep can be downloaded
(MusicService.downloadable): personal servers, SoundCloud, YouTube, Bandcamp, podcasts and the Live
Music Archive. Qobuz, TIDAL and Spotify can't: offline listening is part of what their subscriptions
license to their own apps, under DRM or terms MusicDeck doesn't sidestep. Local files and Steam
soundtracks are on the Deck already; radio is live.

offline/index.json: {"tracks": {"<instance> <track id>": {"file", "type", "bytes"}},
                     "art": {"<instance> <art id>": {"file", "type"}}}
"""
import asyncio
import hashlib
import json
import os
import shutil
import ssl
import tempfile
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urljoin

import aiohttp
import certifi

from . import debuglog
from .services.base import ServiceError, StreamSource
from .settings import load_json

# audio is fetched in pieces (YouTube throttles single long requests)
CHUNK = 8 * 1024 * 1024
# downloads stop rather than fill the disk
MIN_FREE = 1024 ** 3
# after this many failures in a row (no network, say), the rest wait for the next sync
GIVE_UP_AFTER = 3
EXTENSIONS = {"audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac", "audio/flac": ".flac",
              "audio/ogg": ".ogg", "audio/webm": ".webm", "audio/wav": ".wav", "image/jpeg": ".jpg",
              "image/png": ".png", "image/webp": ".webp"}

# (instance id, "stream" or "art", track or art id) -> where that audio or picture comes from now
Resolver = Callable[[str, str, str], Awaitable[StreamSource]]


def _key(instance_id: str, item_id: str) -> str:
    return f"{instance_id} {item_id}"


def _name(key: str, content_type: str) -> str:
    """A file name for a track or picture: ids can hold anything, so it's a hash."""
    ext = EXTENSIONS.get(content_type.split(";")[0].strip().lower(), "")
    return hashlib.sha256(key.encode()).hexdigest()[:32] + ext


def _partial(key: str) -> str:
    """Where a download is written until it's complete."""
    return hashlib.sha256(key.encode()).hexdigest()[:32] + ".part"


class OfflineStore:
    def __init__(self, directory: str, on_change: Callable[[], Awaitable[None]]):
        self.dir = os.path.join(directory, "offline")
        self.index_path = os.path.join(self.dir, "index.json")
        index = load_json(self.index_path)
        self.tracks: dict[str, dict[str, Any]] = index.get("tracks") if isinstance(index.get("tracks"), dict) else {}
        self.art: dict[str, dict[str, Any]] = index.get("art") if isinstance(index.get("art"), dict) else {}
        self.on_change = on_change
        # what the downloaded playlists hold: key -> (instance id, track)
        self.wanted: dict[str, tuple[str, dict[str, Any]]] = {}
        self.failed: dict[str, str] = {}
        self.current: Optional[str] = None
        self.stopped_for = ""  # why downloading stopped early (no space, no connection)
        self.worker: Optional[asyncio.Task] = None
        self.session: Optional[aiohttp.ClientSession] = None

    def _save(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"tracks": self.tracks, "art": self.art}, f)
        os.replace(tmp, self.index_path)

    # --- lookups (the stream server asks before going to the network) ---

    def _file(self, entry: Optional[dict[str, Any]]) -> Optional[StreamSource]:
        if not entry:
            return None
        path = os.path.join(self.dir, entry["file"])
        return StreamSource(path=path, content_type=entry.get("type")) if os.path.isfile(path) else None

    def audio(self, instance_id: str, track_id: str) -> Optional[StreamSource]:
        return self._file(self.tracks.get(_key(instance_id, track_id)))

    def picture(self, instance_id: str, art_id: str) -> Optional[StreamSource]:
        return self._file(self.art.get(_key(instance_id, art_id)))

    def has(self, instance_id: str, track_id: str) -> bool:
        return _key(instance_id, track_id) in self.tracks

    def status(self) -> dict[str, Any]:
        """For the UI: what's downloaded, what's left, and what went wrong."""
        pending = [k for k in self.wanted if k not in self.tracks and k not in self.failed]
        size = sum(e.get("bytes", 0) for e in self.tracks.values())
        size += sum(e.get("bytes", 0) for e in self.art.values())
        current = self.wanted.get(self.current or "")
        return {"tracks": len(self.tracks), "bytes": size, "pending": len(pending), "failed": len(self.failed),
                "current": current[1].get("title") if current else None, "stopped": self.stopped_for}

    # --- keeping the files in line with the downloaded playlists ---

    def sync(self, wanted: dict[tuple[str, str], dict[str, Any]], resolve: Resolver) -> None:
        """Sets what should be on the Deck ({(instance, track id): track}): files no longer wanted
        are deleted now, missing ones download in the background, one at a time."""
        self.wanted = {_key(i, t): (i, track) for (i, t), track in wanted.items()}
        self.failed = {k: v for k, v in self.failed.items() if k in self.wanted}
        self.stopped_for = ""
        self._prune()
        if self.worker is None or self.worker.done():
            self.worker = asyncio.ensure_future(self._work(resolve))

    def retry(self, resolve: Resolver) -> None:
        self.failed.clear()
        self.sync({(i, t["id"]): t for i, t in self.wanted.values()}, resolve)

    def _prune(self) -> None:
        arts = {_key(i, t["art"]) for i, t in self.wanted.values() if t.get("art")}
        gone = [k for k in self.tracks if k not in self.wanted]
        gone_art = [k for k in self.art if k not in arts]
        for table, keys in ((self.tracks, gone), (self.art, gone_art)):
            for k in keys:
                try:
                    os.remove(os.path.join(self.dir, table.pop(k)["file"]))
                except (FileNotFoundError, KeyError):
                    pass
        if gone or gone_art:
            self._save()

    async def stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except (asyncio.CancelledError, Exception):
                pass
        if self.session:
            await self.session.close()

    async def _work(self, resolve: Resolver) -> None:
        if self.session is None:
            # no overall timeout: an hour-long episode on a slow link is fine as long as bytes arrive
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60))
        failures = 0
        while True:
            todo = [k for k in self.wanted if k not in self.tracks and k not in self.failed]
            if not todo:
                break
            os.makedirs(self.dir, exist_ok=True)
            if shutil.disk_usage(self.dir).free < MIN_FREE:
                self.stopped_for = "Not enough free space"
                break
            key = todo[0]
            instance_id, track = self.wanted[key]
            self.current = key
            await self.on_change()
            try:
                await self._download_track(key, instance_id, track, resolve)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # one track failing mustn't stop the others
                message = str(e) if isinstance(e, ServiceError) else "Couldn't download it"
                debuglog.debug("offline", "failed", track=track.get("id"), error=str(e)[:200])
                self.failed[key] = message
                failures += 1
                if failures >= GIVE_UP_AFTER:
                    self.stopped_for = "Couldn't reach the source"
                    break
        self.current = None
        await self.on_change()

    async def _download_track(self, key: str, instance_id: str, track: dict[str, Any], resolve: Resolver) -> None:
        source = await resolve(instance_id, "stream", track["id"])
        if source.path:  # already a file on the Deck
            return
        self.tracks[key] = await self._fetch(source, key)
        art_key = _key(instance_id, track["art"]) if track.get("art") else None
        if art_key and art_key not in self.art:
            try:
                picture = await resolve(instance_id, "art", track["art"])
                if picture.path is None:
                    self.art[art_key] = await self._fetch(picture, art_key)
            except Exception as e:  # the music matters; a missing picture doesn't
                debuglog.debug("offline", "no artwork", track=track.get("id"), error=str(e)[:200])
        self._save()

    # --- fetching ---

    async def _fetch(self, source: StreamSource, key: str) -> dict[str, Any]:
        """Saves the source's bytes; returns its index entry. Nothing is left behind on failure."""
        partial = os.path.join(self.dir, _partial(key))
        try:
            with open(partial, "wb") as out:
                if source.playlist is not None:
                    raise ServiceError("This track's stream can't be saved")
                if source.hls:
                    content_type = await self._fetch_hls(source, out)
                else:
                    content_type = await self._fetch_file(source, out)
        except BaseException:
            try:
                os.remove(partial)
            except FileNotFoundError:
                pass
            raise
        entry = {"file": _name(key, content_type), "type": content_type, "bytes": os.path.getsize(partial)}
        os.replace(partial, os.path.join(self.dir, entry["file"]))
        return entry

    async def _fetch_file(self, source: StreamSource, out) -> str:
        """In CHUNK-sized ranges where the server takes them, else in one go."""
        assert self.session and source.url
        start, total, content_type = 0, None, source.content_type
        while total is None or start < total:
            headers = {**source.headers, "Range": f"bytes={start}-{start + CHUNK - 1}"}
            async with self.session.get(source.url, headers=headers) as resp:
                if resp.status >= 400:
                    raise ServiceError(f"The source returned HTTP {resp.status}")
                content_type = content_type or resp.headers.get("Content-Type", "application/octet-stream")
                async for piece in resp.content.iter_chunked(64 * 1024):
                    out.write(piece)
                    start += len(piece)
                if resp.status == 200:  # no ranges: that was all of it
                    return content_type
                total = int((resp.headers.get("Content-Range") or "/0").rsplit("/", 1)[1] or 0) or start
        return content_type or "application/octet-stream"

    async def _fetch_hls(self, source: StreamSource, out) -> str:
        """Joins an HLS stream's pieces into one file: fragmented MP4 (an init section, then the
        segments) or plain MP3/AAC segments. Encrypted and MPEG-TS streams can't be kept."""
        assert self.session and source.url
        url = source.url
        for _ in range(2):  # a master playlist names the media playlist to fetch next
            async with self.session.get(url, headers=source.headers) as resp:
                if resp.status >= 400:
                    raise ServiceError(f"The source returned HTTP {resp.status}")
                text = await resp.text()
                url = str(resp.url)
            if "#EXT-X-STREAM-INF" not in text:
                break
            lines = text.splitlines()
            url = urljoin(url, next(line.strip() for i, line in enumerate(lines)
                                    if i and lines[i - 1].startswith("#EXT-X-STREAM-INF") and line.strip()))
        if "#EXT-X-KEY" in text and "METHOD=NONE" not in text:
            raise ServiceError("This track's stream is encrypted and can't be saved")
        parts, init = [], None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-MAP:") and 'URI="' in line:
                init = urljoin(url, line.split('URI="', 1)[1].split('"', 1)[0])
            elif line and not line.startswith("#"):
                parts.append(urljoin(url, line))
        if not parts:
            raise ServiceError("This track's stream is empty")
        if init:
            content_type = "audio/mp4"
        else:
            first = parts[0].split("?")[0].lower()
            content_type = "audio/mpeg" if first.endswith(".mp3") else "audio/aac" if first.endswith(".aac") else ""
            if not content_type:
                raise ServiceError("This track's stream format can't be saved")
        for part in ([init] if init else []) + parts:
            async with self.session.get(part, headers=source.headers) as resp:
                if resp.status >= 400:
                    raise ServiceError(f"The source returned HTTP {resp.status}")
                async for piece in resp.content.iter_chunked(64 * 1024):
                    out.write(piece)
        return content_type
