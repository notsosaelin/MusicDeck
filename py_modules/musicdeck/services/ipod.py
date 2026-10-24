"""
An iPod plugged in, in Local files' Devices with the other players (services/shelf.py), but read from its
own database rather than by its files' tags: an iPod keeps its songs in iPod_Control/Music/F00... under
made-up names, and lists what they are (and its playlists, plays and ratings) in iTunesDB (itunesdb.py).
Reading that takes a second where reading thousands of files' tags over USB takes minutes. An iPod with
no database (or one MusicDeck can't read) is read by its files, as any player.

Music copied to it goes into its database too, as iTunes puts it there (ipod.py), so Apple's own
software plays it.
"""
import asyncio
import os
from typing import Any, Callable, Optional

from .. import ipod
from ..itunesdb import ITunesDBError
from .base import ServiceError
from .device import DeviceService
from .local import FORMATS, _hash

VIDEO_KINDS = 0x02 | 0x20 | 0x40


class IpodService(DeviceService):
    """config: {"paths": [its mount], "kind": "ipod", "source": "/dev/sdb1"}"""

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.info: Optional[ipod.IpodInfo] = None
        self.ipod_name = ""
        self.sync_lock = asyncio.Lock()
        self.backup_dir = os.path.join(runtime_dir, f"ipod-{instance_id}")

    async def start(self) -> None:
        root = self.roots[0] if self.roots else ""
        self.info = await asyncio.to_thread(ipod.identify, root, self.config.get("source", ""), self.runtime_dir)
        await super().start()

    def name(self) -> str:
        return self.ipod_name

    def describe(self) -> str:
        return self.info.model if self.info else "iPod"

    def why_not(self) -> str:
        """Why music can't be copied to it ("" when it can)."""
        return self.info.why_not() if self.info else "This iPod couldn't be read"

    # --- reading ---

    @staticmethod
    def _album_folder(t: dict[str, Any]) -> str:
        # its files are spread over F00..F49 at random: the folder says nothing about the album
        return ""

    def _scan_blocking(self) -> None:
        root = self.roots[0] if self.roots else ""
        db = ipod.load(root)
        if db is None:
            super()._scan_blocking()
            return
        self.ipod_name = db.name
        tracks: dict[str, dict[str, Any]] = {}
        by_id: dict[int, str] = {}
        for t in db.tracks:
            f = t.fields()
            # its videos (movies, TV shows, music videos: media types 0x02, 0x40, 0x20) aren't music
            if not f["path"] or int.from_bytes(t.header[0xD0:0xD4], "little") & VIDEO_KINDS:
                continue
            path = ipod.on_disk(root, f["path"])
            ext = os.path.splitext(path)[1].lower()
            lossless = "Lossless" in f["filetype"] or ext in (".wav", ".aif", ".aiff")
            record = {"path": path, "mtime": 0.0, "size": f["size"], "added": f["added"], "title": f["title"],
                      "artist": f["artist"], "albumartist": f["albumartist"], "album": f["album"],
                      "track": f["track"], "disc": f["disc"], "year": str(f["year"] or ""), "genre": f["genre"],
                      "genres": [f["genre"]] if f["genre"] else [], "composer": f["composer"], "label": "",
                      "compilation": f["compilation"], "rating": f["rating"], "duration": f["duration"], "rg": None,
                      "format": "ALAC" if "Lossless" in f["filetype"] else FORMATS.get(ext, ext[1:].upper()),
                      "bits": 16 if lossless else 0, "rate": f["rate"], "bitrate": f["bitrate"], "channels": 2,
                      "ipod": f["path"]}
            tid = _hash(path)
            tracks[tid] = record
            by_id[f["id"]] = tid
            self.scan_progress["found"] = len(tracks)
        playlists = {}
        for p in db.playlists():
            if p.master or p.podcasts:
                continue
            ids = [by_id[i.track_id] for i in p.items if i.track_id in by_id]
            if ids:
                playlists[_hash(f"ipod-playlist:{p.header[0x1C:0x24].hex()}")] = {"path": "", "title": p.name,
                                                                                 "tracks": ids}
        self.tracks = tracks
        self.playlists = playlists
        self._changed()
        self.folder_covers = {}
        self._save_index()

    # --- syncing ---

    async def put(self, items: list[dict[str, Any]], playlist: str, progress: Callable[[int], None]) -> int:
        """Copies music to it (ipod.add), then reads it again."""
        if not self.info:
            raise ServiceError("This iPod couldn't be read")
        async with self.sync_lock:
            try:
                added = await asyncio.to_thread(ipod.add, self.info, items, playlist, self.backup_dir, progress)
            except ITunesDBError as e:
                raise ServiceError(str(e)) from e
        self.rescan()
        return added

    async def take_off(self, track_ids: list[str]) -> int:
        """Removes tracks of its (Local files' ids for them) from it."""
        if not self.info:
            raise ServiceError("This iPod couldn't be read")
        paths = {self.tracks[t]["ipod"] for t in track_ids if t in self.tracks and self.tracks[t].get("ipod")}
        if not paths:
            raise ServiceError("Those aren't on the iPod")
        async with self.sync_lock:
            try:
                gone = await asyncio.to_thread(ipod.remove, self.info, paths, self.backup_dir)
            except ITunesDBError as e:
                raise ServiceError(str(e)) from e
        self.rescan()
        return gone

    def status(self) -> dict[str, Any]:
        return {**super().status(), "model": self.describe(), "why_not": self.why_not()}

