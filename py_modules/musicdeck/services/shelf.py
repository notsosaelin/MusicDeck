"""
Local files with what's plugged in: music players, phones, USB sticks and audio CDs show in the
library's Devices tab (and a Devices row on Home) while they're there, as one media library, like the
Xbox's: play a CD, rip it to the Deck, copy an album from the Deck to a player and back.

Each device keeps its own index (a DeviceService, or an AudioCdService for a CD; started by the plugin
as it's plugged in, musicdeck/devices.py), and Local files speaks for it: everything of a device's goes
out with ids prefixed "dv~<device id>~", and anything that comes back with that prefix goes to the
device with it taken off. So a device's songs are Local files' songs to the rest of MusicDeck (the
queue, playlists, the stream server): one source, no mixing.

What's on a device is read only (no tag edits, artwork or look-ups: the files aren't the Deck's).
Copying (`copy_to`) goes either way: the Deck's music to a device, a device's to the Deck, into
<artist>/<album>/ under the device's Music folder or the library's first folder, files as they are. An
iPod's goes into its own library (IpodService, ipod.py) instead, as iTunes would put it there.
"""
import asyncio
import os
import shutil
from typing import Any, Callable, Optional

from .. import models
from .audiocd import AudioCdService
from .base import ServiceError, StreamSource
from .ipod import IpodService
from .local import LocalService

PREFIX = "dv~"
ID_KEYS = ("album_id", "artist_id")
#: what a device's files can't have done: they aren't the Deck's to change
READ_ONLY = ("tag_fields", "edit_tags", "undo_tags", "lookup", "lookup_changes", "apply_lookup",
             "set_album_art", "clear_album_art", "find_album_art", "set_artist_art", "clear_artist_art",
             "find_artist_art", "save_lyrics", "find_lyrics", "dismiss_review")
#: a phone's folders other than Music that hold music (devices.PHONE_MUSIC): copies go beside them, in Music
OTHER_MUSIC = ("download", "podcasts")


def on_device(item_id: Any) -> bool:
    return isinstance(item_id, str) and item_id.startswith(PREFIX)


def mark(found: Any, device_id: str) -> Any:
    """A device's tracks, collections, sections or pages with their ids and art made Local files'."""
    if isinstance(found, list):
        return [mark(x, device_id) for x in found]
    if not isinstance(found, dict):
        return found
    out = {k: mark(v, device_id) if isinstance(v, (dict, list)) else v for k, v in found.items()}
    # a collection (kind) or a track (duration): not a section's or a tab's own fields
    if isinstance(found.get("id"), str) and ("kind" in found or "duration" in found):
        out["id"] = f"{PREFIX}{device_id}~{found['id']}"
    for key in ID_KEYS:
        if isinstance(found.get(key), str) and found[key]:
            out[key] = f"{PREFIX}{device_id}~{found[key]}"
    art = found.get("art")
    if isinstance(art, str) and art:
        out["art"] = f"{PREFIX}{device_id}~{art}"
    elif isinstance(art, list):
        out["art"] = [f"{PREFIX}{device_id}~{a}" if isinstance(a, str) and a else a for a in art]
    return out


def split(item_id: str) -> tuple[str, str]:
    """"dv~<device>~<id>" as (device, id)."""
    device_id, _, inner = item_id[len(PREFIX):].partition("~")
    return device_id, inner


def unmark(track: dict[str, Any]) -> dict[str, Any]:
    """A track of a device's as the device knows it."""
    out = dict(track)
    for key in ("id", "art") + ID_KEYS:
        if on_device(out.get(key)):
            out[key] = split(out[key])[1]
    return out


class LocalLibrary(LocalService):
    """Local files, and the devices plugged in (`devices`, set by the plugin: [{"id", "name", "kind",
    "instance"}])."""

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.devices: Callable[[], list[dict[str, Any]]] = lambda: []
        self.copying: Optional[asyncio.Task[None]] = None
        self.copy_progress = {"running": False, "done": 0, "total": 0, "to": "", "error": ""}
        for name in READ_ONLY:
            if hasattr(self, name):
                setattr(self, name, self._read_only(getattr(self, name)))

    @staticmethod
    def _read_only(method: Callable[..., Any]) -> Callable[..., Any]:
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            if any(on_device(a) for a in args):
                raise ServiceError("Files on a plugged-in device aren't changed")
            return await method(*args, **kwargs)
        return guarded

    def _device(self, device_id: str) -> dict[str, Any]:
        found = next((d for d in self.devices() if d["id"] == device_id), None)
        if not found:
            raise ServiceError("That's on a device that isn't plugged in")
        return found

    def _route(self, item_id: str) -> tuple[dict[str, Any], str]:
        device_id, inner = split(item_id)
        return self._device(device_id), inner

    # --- the Devices tab and row ---

    def _card(self, device: dict[str, Any]) -> dict[str, Any]:
        """A device as a card: a CD opens as its album; a player, phone or stick as its albums."""
        instance = device["instance"]
        if isinstance(instance, AudioCdService):
            album = instance._album()
            return mark({**album, "subtitle": "Audio CD · " + models.count(len(instance.tracks), "track")},
                        device["id"])
        kind = instance.describe() if isinstance(instance, IpodService) else "Phone" if device["kind"] == "mtp" else "USB"
        songs = len(getattr(instance, "tracks", {}) or {})
        about = f"{kind} · " + ("Reading…" if instance.status().get("scanning") and not songs
                                else models.count(songs, "song"))
        cover = None
        albums = instance._albums() if hasattr(instance, "_albums") else {}
        if albums:
            tid, t = next(iter(albums.values()))[0]
            cover = f"{PREFIX}{device['id']}~{instance._art_id(tid, t)}"
        return models.collection(f"{PREFIX}{device['id']}~", "device", device["name"], about, cover)

    def _devices_section(self, display: str = "shelf") -> list[dict[str, Any]]:
        devices = self.devices()
        return [models.section("Devices", [self._card(d) for d in devices], display=display)] if devices else []

    def center_tabs(self) -> list[dict[str, Any]]:
        tabs = super().center_tabs()
        devices = self.devices()
        if not devices:
            return tabs
        art = [c["art"] for c in (self._card(d) for d in devices) if c.get("art")][:4]
        return tabs + [{"id": "devices", "title": "Devices", "group": "files",
                        "subtitle": models.count(len(devices), "device"), **({"art": art} if art else {})}]

    async def home(self) -> list[dict[str, Any]]:
        return self._devices_section() + await super().home()

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "devices":
            return self._devices_section(display="grid")
        if tab_id == "home":
            # first, as on the panel's Home: what was just plugged in is what's to be played
            return self._devices_section() + [s for s in await super().tab(tab_id) if s["title"] != "Devices"]
        return await super().tab(tab_id)

    # --- what's on a device, through Local files ---

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if not on_device(id):
            return await super().open(kind, id)
        device, inner = self._route(id)
        instance = device["instance"]
        if kind == "device":
            # the device itself: its albums
            card = self._card(device)
            page = await instance.open("list", "albums")
            return models.page(device["name"], card["subtitle"], card["art"],
                               collections=mark(page["collections"], device["id"]))
        return mark(await instance.open(kind, inner), device["id"])

    def current(self, collection: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not on_device(collection.get("id")):
            return super().current(collection)
        device_id, _ = split(collection["id"])
        return collection if any(d["id"] == device_id for d in self.devices()) else None

    async def search(self, query: str) -> dict[str, Any]:
        page = await super().search(query)
        for device in self.devices():
            try:
                found = await device["instance"].search(query)
            except ServiceError:
                continue
            if found.get("tracks"):
                page["extra"] = (page.get("extra") or []) + [
                    {"title": f"On {device['name']}", "tracks": mark(found["tracks"], device["id"])}]
        return page

    async def stream(self, track_id: str) -> StreamSource:
        if not on_device(track_id):
            return await super().stream(track_id)
        device, inner = self._route(track_id)
        return await device["instance"].stream(inner)

    async def art(self, art_id: str) -> StreamSource:
        if not on_device(art_id):
            return await super().art(art_id)
        device, inner = self._route(art_id)
        return await device["instance"].art(inner)

    async def star(self, track_id: str, starred: bool) -> None:
        if not on_device(track_id):
            return await super().star(track_id, starred)
        device, inner = self._route(track_id)
        await device["instance"].star(inner, starred)

    async def scrobble(self, track_id: str, submission: bool) -> None:
        if not on_device(track_id):
            return await super().scrobble(track_id, submission)
        device, inner = self._route(track_id)
        await device["instance"].scrobble(inner, submission)

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not on_device(track.get("id")):
            return await super().lyrics(track)
        device, _ = self._route(track["id"])
        return await device["instance"].lyrics(unmark(track))

    async def details(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not on_device(track.get("id")):
            return await super().details(track)
        device, _ = self._route(track["id"])
        found = await device["instance"].details(unmark(track))
        if found:
            found = {**found, "facts": [["On", device["name"]]] + list(found.get("facts") or [])}
        return found

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        if not on_device(track.get("id")):
            return await super().radio(track)
        device, _ = self._route(track["id"])
        return mark(await device["instance"].radio(unmark(track)), device["id"])

    async def collection_action(self, kind: str, collection_id: str, call: str) -> str:
        if not on_device(collection_id):
            raise ServiceError("Not something Local files does")
        device, inner = self._route(collection_id)
        if not hasattr(device["instance"], "collection_action"):
            raise ServiceError("Not something this device does")
        return await device["instance"].collection_action(kind, inner, call)

    def status(self) -> dict[str, Any]:
        return {**super().status(), "copy": dict(self.copy_progress)}

    # --- copying between the Deck and a device ---

    async def _files(self, kind: str, item_id: str) -> tuple[list[dict[str, Any]], str, str, LocalService]:
        """The files of a track, album, artist or playlist (their index records, "path" and tags), where
        they are ("" for the Deck, else a device's id), a playlist's name, and the index they're in."""
        source: LocalService = self
        device_id, inner = "", item_id
        if on_device(item_id):
            device, inner = self._route(item_id)
            if not isinstance(device["instance"], LocalService):
                raise ServiceError("A CD is ripped, not copied: Rip to Local files is on its album")
            source, device_id = device["instance"], device["id"]
        title = ""
        if kind == "track":
            ids = [inner]
        else:
            page = await source.open(kind, inner)
            ids = [t["id"] for t in page.get("tracks") or []]
            title = page.get("title", "") if kind == "playlist" else ""
        files = []
        for tid in dict.fromkeys(ids):
            t = source.tracks.get(tid)
            if t and os.path.isfile(t["path"]):
                files.append({**t, "tid": tid, "artist_folder": source._album_artist(t) or "Unknown artist",
                              "album_folder": t["album"] or "Unknown album"})
        if not files:
            raise ServiceError("Nothing there to copy")
        return files, device_id, title, source

    def _destination(self, target: str) -> tuple[str, str, Optional[LocalService]]:
        """Where a copy goes: (folder, its name for the user, the index to rescan after)."""
        if target == "deck":
            if not self.roots:
                raise ServiceError("Local files has no folder to copy into")
            return self.roots[0], "this Deck", self
        device = self._device(target)
        instance = device["instance"]
        if not isinstance(instance, LocalService) or not instance.roots:
            raise ServiceError("Music can't be copied to a CD")
        if isinstance(instance, IpodService):
            reason = instance.why_not()
            if reason:
                raise ServiceError(reason)
            return "", device["name"], instance
        roots = instance.roots
        music = next((r for r in roots if os.path.basename(r).lower() == "music"), None)
        if not music:
            base = roots[0]
            if os.path.basename(base).lower() in OTHER_MUSIC:
                base = os.path.dirname(base)
            inside = os.path.join(base, "Music")
            music = inside if os.path.isdir(inside) or device["kind"] == "mtp" else base
        return music, device["name"], instance

    async def copy_to(self, kind: str, item_id: str, target: str) -> str:
        """Copies a track, album, artist or playlist to a device ("dv..." id) or from one to the Deck
        ("deck"), in the background; returns what to tell the user. To an iPod, into its library (a
        playlist stays a playlist there)."""
        if self.copy_progress["running"]:
            raise ServiceError(f"Still copying to {self.copy_progress['to']}")
        files, source, playlist, index_from = await self._files(kind, item_id)
        if source == target or (target == "deck" and not source):
            raise ServiceError("That's already there")
        folder, name, index = self._destination(target)
        self.copy_progress = {"running": True, "done": 0, "total": len(files), "to": name, "error": ""}
        if isinstance(index, IpodService):
            self.copying = asyncio.ensure_future(self._copy_to_ipod(files, playlist, name, index))
        else:
            # an iPod's files are "ABCD.mp3": copied off it, they're named as their tags say
            named = isinstance(index_from, IpodService)
            self.copying = asyncio.ensure_future(self._copy(files, folder, name, index, named))
        return f"Copying {models.count(len(files), 'song')} to {name}"

    async def _copy(self, files: list[dict[str, Any]], folder: str, name: str, index: Optional[LocalService],
                    named: bool = False) -> None:
        copied = 0
        try:
            for f in files:
                where = os.path.join(folder, models.safe_name(f["artist_folder"], "Unknown artist"),
                                     models.safe_name(f["album_folder"], "Unknown album"))
                file_name = None
                if named:
                    number = f"{f['track']:02d} " if f.get("track") else ""
                    file_name = (number + models.safe_name(f.get("title") or "Track", "Track")
                                 + os.path.splitext(f["path"])[1].lower())
                copied += await asyncio.to_thread(self._copy_one, f["path"], where, file_name)
                self.copy_progress["done"] += 1
            self.copy_progress["running"] = False
            if index is not None and copied:
                index.rescan()
            if self.announce:
                self.announce(f"Copied {models.count(len(files), 'song')} to {name}" if copied or not files
                              else f"Already on {name}")
        except Exception as e:  # noqa: BLE001 - a full player, one unplugged mid-copy: said, not raised
            self.copy_progress.update(running=False, error=str(e) or type(e).__name__)
            if self.announce:
                self.announce(f"Copying to {name} stopped: {self.copy_progress['error']}")

    async def _copy_to_ipod(self, files: list[dict[str, Any]], playlist: str, name: str, ipod: IpodService) -> None:
        def progress(done: int) -> None:
            self.copy_progress["done"] = done
        try:
            added = await ipod.put(files, playlist, progress)
            self.copy_progress["running"] = False
            if self.announce:
                self.announce(f"Copied {models.count(added, 'song')} to {name}" if added else f"Already on {name}")
        except Exception as e:  # noqa: BLE001 - a full iPod, one unplugged mid-copy: said, not raised
            self.copy_progress.update(running=False, error=str(e) or type(e).__name__)
            if self.announce:
                self.announce(f"Copying to {name} stopped: {self.copy_progress['error']}")

    async def remove_from_device(self, kind: str, item_id: str) -> str:
        """Deletes a track, album, artist or playlist's songs from the device they're on (from an iPod's
        library too); returns what to tell the user."""
        if not on_device(item_id):
            raise ServiceError("Only music on a plugged-in device is removed here")
        files, device_id, _, index = await self._files(kind, item_id)
        device = self._device(device_id)
        if isinstance(index, IpodService):
            gone = await index.take_off([f["tid"] for f in files])
        else:
            def delete() -> int:
                n = 0
                for f in files:
                    try:
                        os.remove(f["path"])
                        n += 1
                    except OSError:
                        pass
                return n
            gone = await asyncio.to_thread(delete)
            index.rescan()
        return f"Removed {models.count(gone, 'song')} from {device['name']}"

    @staticmethod
    def _copy_one(path: str, folder: str, file_name: Optional[str] = None) -> int:
        """One file into a folder; a file of the same name and size there already is left as it is.
        A phone's folder (GVfs's) takes plain writes, as a stick's does."""
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, file_name or os.path.basename(path))
        if os.path.isfile(target) and os.path.getsize(target) == os.path.getsize(path):
            return 0
        try:
            shutil.copyfile(path, target)
        except BaseException:
            if os.path.exists(target):
                os.remove(target)
            raise
        return 1

    async def stop(self) -> None:
        if self.copying:
            self.copying.cancel()
        await super().stop()
