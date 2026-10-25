"""
A phone or player plugged in by MTP on Windows (mtp.py): Local files on a mirror of it on the PC,
<data>/phones/<its id>/<storage>/<its folders>, which holds what's been read off it rather than all of it.

* Indexing: the phone says what's in its music folders (Music, Download, Podcasts, as on Linux), with
  each file's size and date. A song new or changed since the last index is copied into the mirror to
  read its tags; covers, lyrics and playlist files (small) are always there. Then the index is Local
  files' own: the paths are the mirror's, so covers, details and playlists work as they do anywhere.
* Songs stay in the mirror up to CACHE_BYTES, the least lately played going first; one that's gone is
  copied off the phone again when it's played (a second or two over USB).
* Copying to it: into the phone itself (mtp-helper put), and the mirror too, so it's known at once.
* Its free space is the phone's storages', not the PC's.
"""
import asyncio
import os
import shutil
import time
from typing import Any, Optional

from .. import devices, mtp
from .base import ServiceError, StreamSource
from .device import DeviceService
from .local import AUDIO_EXTENSIONS, PLAYLIST_EXTENSIONS

#: the most the mirror keeps of a phone's songs
CACHE_BYTES = 2 * 1024 ** 3
#: what's always copied: small, and read by name (a folder's cover, a song's lyrics)
SIDECARS = (".jpg", ".jpeg", ".png", ".webp", ".lrc") + tuple(PLAYLIST_EXTENSIONS)


def mirror_path(mirror: str, storage: str, path: str) -> str:
    return os.path.join(mirror, storage, *[p for p in path.split("/") if p])


class PhoneService(DeviceService):
    """config: {"kind": "mtp", "wpd": its Windows Portable Devices id}; "paths" is its mirror."""

    watches = False

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        self.mirror = os.path.join(runtime_dir, "phones", instance_id)
        os.makedirs(self.mirror, exist_ok=True)
        super().__init__(instance_id, {**config, "paths": [self.mirror], "mount": self.mirror}, runtime_dir)
        self.exe = mtp.helper(runtime_dir)
        self.device = config.get("wpd", "")
        self.objects: dict[str, str] = {}  # mirror path -> the phone's object id
        self.storages: list[dict[str, Any]] = []

    # --- talking to it: MTP here, AFC for an iPhone (IosService) ---

    def _remote_list(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(storages: {"name", "free", "total"}, entries: {"type", "storage", "path", "id", "size", "mtime"})"""
        if not self.exe:
            raise ServiceError("Phones on Windows need MusicDeck's mtp-helper, which this build doesn't have")
        return mtp.listing(self.exe, self.device, devices.PHONE_MUSIC)

    def _remote_fetch(self, wanted: list[tuple[str, str]]) -> set[str]:
        """Objects (id, local path) copied off it; the ids that came."""
        return mtp.fetch(self.exe, self.device, wanted) if self.exe else set()

    def _remote_put(self, storage: str, folder: str, path: str, name: str) -> str:
        if not self.exe:
            raise ServiceError("Phones on Windows need MusicDeck's mtp-helper, which this build doesn't have")
        return mtp.put(self.exe, self.device, storage, folder, path, name)

    # --- reading it ---

    def _listing(self, previous: dict[str, dict[str, Any]]) -> Any:
        storages, entries = self._remote_list()
        self.storages = storages
        files = []
        for e in entries:
            name = e["path"].rsplit("/", 1)[-1]
            if e["type"] != "file" or name.startswith("."):
                continue
            local = mirror_path(self.mirror, e["storage"], e["path"])
            self.objects[local] = e["id"]
            files.append((local, int(e.get("size") or 0), float(e.get("mtime") or 0), e["id"]))
        wanted = []
        for local, size, mtime, object_id in files:
            ext = os.path.splitext(local)[1].lower()
            here = os.path.isfile(local) and os.path.getsize(local) == size
            old = previous.get(local)
            known = old and old["size"] == size and old["mtime"] == mtime
            if ext in SIDECARS and not here or ext in AUDIO_EXTENSIONS and not known and not here:
                wanted.append((object_id, local))
        self._remote_fetch(wanted)
        self._tidy({local for local, _, _, _ in files})
        for local, size, mtime, _ in files:
            ext = os.path.splitext(local)[1].lower()
            if ext in AUDIO_EXTENSIONS or ext in PLAYLIST_EXTENSIONS:
                # a song not read (the phone wouldn't give it) is left out, not indexed without its tags
                if os.path.isfile(local) or (previous.get(local) or {}).get("size") == size:
                    yield local, size, mtime

    def _scan_blocking(self) -> None:
        super()._scan_blocking()
        self._evict()

    def _tidy(self, listed: set[str]) -> None:
        """What's gone from the phone goes from the mirror."""
        for directory, _, names in os.walk(self.mirror):
            for name in names:
                path = os.path.join(directory, name)
                if path not in listed:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    def _evict(self) -> None:
        """Songs beyond CACHE_BYTES out of the mirror, the least lately used first (a song's mtime in
        the mirror is when it was last played or read; its date on the phone is in the index)."""
        songs = []
        for directory, _, names in os.walk(self.mirror):
            for name in names:
                if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                    path = os.path.join(directory, name)
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    songs.append((st.st_mtime, st.st_size, path))
        total = sum(size for _, size, _ in songs)
        for _, size, path in sorted(songs):
            if total <= CACHE_BYTES:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass

    def ensure(self, path: str) -> str:
        """A song of it as a file on the PC: the mirror's, copied off the phone if it isn't there."""
        if not os.path.isfile(path):
            object_id = self.objects.get(path)
            if not object_id or object_id not in self._remote_fetch([(object_id, path)]):
                raise ServiceError("The phone didn't give that song (is it still plugged in and unlocked?)")
        now = time.time()
        os.utime(path, (now, now))
        return path

    async def stream(self, track_id: str) -> StreamSource:
        t = self.tracks.get(track_id)
        if t:
            await asyncio.to_thread(self.ensure, t["path"])
        return await super().stream(track_id)

    async def art(self, art_id: str) -> StreamSource:
        try:
            return await super().art(art_id)
        except (ServiceError, OSError):
            kind, _, tid = art_id.partition(":")
            t = self.tracks.get(tid.partition(".")[0]) if kind == "t" else None
            if not t or os.path.isfile(t["path"]):
                raise
            await asyncio.to_thread(self.ensure, t["path"])
            return await super().art(art_id)

    # --- what a media manager shows of it ---

    def space(self) -> Optional[dict[str, int]]:
        total = sum(int(s.get("total") or 0) for s in self.storages)
        return {"total": total, "free": sum(int(s.get("free") or 0) for s in self.storages)} if total else None

    def writable(self) -> bool:
        return bool(self.exe and self.device)

    # --- copying to it ---

    def copy_in(self, path: str, folder: str, file_name: Optional[str] = None) -> int:
        """A file onto the phone, into `folder` (a mirror folder: <storage>/<folders>, or <folders> on its
        first storage), and into the mirror. One of the same name and size already there is left."""
        name = file_name or os.path.basename(path)
        parts = [p for p in os.path.relpath(folder, self.mirror).replace("\\", "/").split("/") if p not in ("", ".")]
        names = [s["name"] for s in self.storages]
        if not names:
            raise ServiceError("The phone hasn't said where its music goes yet")
        storage = parts.pop(0) if parts and parts[0] in names else names[0]
        local = mirror_path(self.mirror, storage, "/".join(parts + [name]))
        size = os.path.getsize(path)
        if local in self.objects and os.path.isfile(local) and os.path.getsize(local) == size:
            return 0
        self.objects[local] = self._remote_put(storage, "/".join(parts), path, name)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        shutil.copyfile(path, local)
        return 1
