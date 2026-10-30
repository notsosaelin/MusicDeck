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

Burning (`burn`): an album, artist or playlist written to a blank CD-R or CD-RW in a drive as an audio
CD (burn.py, with cdrskin), its titles as CD-Text.
"""
import asyncio
import json
import os
import random
import shutil
import tempfile
import time
from typing import Any, Callable, Optional

from .. import burn, cd, models, trash
from .audiocd import AudioCdService
from .base import ServiceError, StreamSource
from .device import DeviceService, _size as device_size
from .ipod import IpodService
from .local import LocalService
from .phone import PhoneService

PREFIX = "dv~"
ID_KEYS = ("album_id", "artist_id")
#: what a device's files can't have done: they aren't the Deck's to change
READ_ONLY = ("tag_fields", "edit_tags", "undo_tags", "lookup", "lookup_changes", "apply_lookup",
             "set_album_art", "clear_album_art", "find_album_art", "set_artist_art", "clear_artist_art",
             "find_artist_art", "save_lyrics", "find_lyrics", "dismiss_review")
#: what each kind of device is called on its card
KIND_NAMES = {"mtp": "Phone", "ios": "iPhone", "disc": "Disc", "usb": "USB"}
#: Recently deleted: its cards' ids ("deleted:<batch>"), and how many deletions it keeps
DELETED = "deleted:"
#: the disc drive's card while nothing's in it
NO_DISC = "disc:none"
#: what's in a disc drive, shown as the one Disc card rather than as devices
DISC_KINDS = ("cd", "disc")
KEEP_DELETED = 50
#: what an album's folder may be left with and still go with it: artwork, a rip's logs and cue sheet, lyrics
LEFTOVERS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".cue", ".log", ".nfo", ".txt", ".m3u", ".m3u8",
             ".lrc", ".sfv", ".md5", ".accurip", ".pdf", ".ini", ".db"}
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
        # whether there's a disc drive (set by the plugin): an empty one shows as "No disc inserted"
        self.has_drive: Callable[[], bool] = lambda: False
        self.copying: Optional[asyncio.Task[None]] = None
        self.copy_progress = {"running": False, "done": 0, "total": 0, "to": "", "error": ""}
        self.burning: Optional[asyncio.Task[None]] = None
        # stage: "converting" (songs into CD audio), "erasing" (a CD-RW), "burning"; percent of the stage
        self.burn_progress: dict[str, Any] = {"running": False, "stage": "", "done": 0, "total": 0, "percent": 0,
                                              "title": "", "error": ""}
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
        kind = instance.describe() if isinstance(instance, IpodService) else KIND_NAMES.get(device["kind"], "USB")
        songs = len(getattr(instance, "tracks", {}) or {})
        about = f"{kind} · " + ("Reading…" if instance.status().get("scanning") and not songs
                                else models.count(songs, "song"))
        room = instance.space() if isinstance(instance, DeviceService) else None
        if room:
            about += f" · {device_size(room['free'])} free"
        cover = None
        albums = instance._albums() if hasattr(instance, "_albums") else {}
        if albums:
            tid, t = next(iter(albums.values()))[0]
            cover = f"{PREFIX}{device['id']}~{instance._art_id(tid, t)}"
        return models.collection(f"{PREFIX}{device['id']}~", "device", device["name"], about, cover)

    def _discs(self) -> list[dict[str, Any]]:
        """What's in the disc drives: audio CDs and data discs (MP3 CDs)."""
        return [d for d in self.devices() if d["kind"] in DISC_KINDS]

    def _plugged(self) -> list[dict[str, Any]]:
        """Everything else plugged in: players, phones, sticks."""
        return [d for d in self.devices() if d["kind"] not in DISC_KINDS]

    def disc_card(self) -> Optional[dict[str, Any]]:
        """The disc drive as one card: what's in it (a CD as its album, a data disc as itself), or "No disc
        inserted" while there's a drive and nothing in it; None without a drive."""
        discs = self._discs()
        if discs:
            card = self._card(discs[0])
            if discs[0]["kind"] == "disc":
                card = {**card, "subtitle": card["subtitle"].replace("Disc ·", "Data disc ·", 1)}
            return card
        if self.has_drive():
            return models.collection(NO_DISC, "list", "No disc inserted", "Disc drive")
        return None

    def _devices_section(self, display: str = "shelf") -> list[dict[str, Any]]:
        """The disc drive's card (its own row), then what else is plugged in."""
        disc, devices = self.disc_card(), self._plugged()
        sections = [models.section("Disc", [disc], display=display)] if disc else []
        if devices:
            sections.append(models.section("Devices", [self._card(d) for d in devices], display=display))
        return sections

    def center_tabs(self) -> list[dict[str, Any]]:
        tabs = super().center_tabs()
        deleted = self._deleted()
        if deleted:
            tabs = tabs + [{"id": "deleted", "title": "Recently deleted", "group": "files",
                            "subtitle": f"{len(deleted)} in the trash"}]
        devices = self._plugged()
        disc = self.disc_card()
        if not devices and not disc:
            return tabs
        art = [c["art"] for c in ([disc] if disc else []) + [self._card(d) for d in devices] if c.get("art")][:4]
        about = ", ".join(x for x in (disc["title"] if disc else "", models.count(len(devices), "device")
                                      if devices else "") if x)
        return tabs + [{"id": "devices", "title": "Disc and devices" if disc else "Devices", "group": "files",
                        "subtitle": about, **({"art": art} if art else {})}]

    async def home(self) -> list[dict[str, Any]]:
        return self._devices_section() + await super().home()

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "devices":
            return self._devices_section(display="grid")
        if tab_id == "deleted":
            cards = [self._deleted_card(b) for b in self._deleted()]
            return [models.section("Recently deleted", cards, display="grid")] if cards                 else [models.notice("Nothing deleted lately")]
        if tab_id == "home":
            # first, as on the panel's Home: what was just plugged in is what's to be played
            return self._devices_section() + [s for s in await super().tab(tab_id) if s["title"] not in ("Devices", "Disc")]
        return await super().tab(tab_id)

    # --- what's on a device, through Local files ---

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id == NO_DISC:
            return models.page("No disc inserted", "Disc drive", notice="Put in an audio CD to play or rip it, or a "
                               "disc of music files. A blank CD burns from an album's or playlist's menu.")
        if kind == "list" and id.startswith(DELETED):
            return self._open_deleted(id[len(DELETED):])
        if not on_device(id):
            return await super().open(kind, id)
        device, inner = self._route(id)
        instance = device["instance"]
        if kind == "device" and isinstance(instance, AudioCdService):
            # a CD is its album (desktop opens a device as itself)
            return mark(await instance.open("album", "disc"), device["id"])
        if kind == "device":
            # the device itself, as a media manager shows one: its songs, artists and playlists, then its
            # albums; how much room is left on it
            card = self._card(device)
            page = await instance.open("list", "albums")
            lists = [models.collection("tracks", "list", "Songs", models.count(len(instance.tracks), "song")),
                     models.collection("artists", "list", "Artists")]
            lists += [models.collection(pid, "playlist", p["title"], models.count(len(p["tracks"]), "song"))
                      for pid, p in sorted(instance.playlists.items(), key=lambda kv: kv[1]["title"].lower())]
            room = instance.space() if isinstance(instance, DeviceService) else None
            about = card["subtitle"]
            if room:
                # the card's "3.2 GB free", as "3.2 GB free of 16.0 GB"
                about = about.rsplit(" · ", 1)[0] + f" · {device_size(room['free'])} free of {device_size(room['total'])}"
            return models.page(device["name"], about, card["art"],
                               collections=mark(lists + page["collections"], device["id"]))
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
        if call == "restore" and collection_id.startswith(DELETED):
            return await self._restore(collection_id[len(DELETED):])
        if not on_device(collection_id):
            raise ServiceError("Not something Local files does")
        device, inner = self._route(collection_id)
        if not hasattr(device["instance"], "collection_action"):
            raise ServiceError("Not something this device does")
        if kind == "device" and isinstance(device["instance"], AudioCdService):
            kind, inner = "album", "disc"  # a CD's page is its album's (open)
        return await device["instance"].collection_action(kind, inner, call)

    def status(self) -> dict[str, Any]:
        return {**super().status(), "copy": dict(self.copy_progress), "burn": dict(self.burn_progress)}

    # --- copying between the Deck and a device ---

    def _record(self, track_id: str) -> Optional[dict[str, Any]]:
        """A song's file and tags from whichever index it's in (the Deck's, or a device's), with where
        it's from ("from": "" or a device id) and its id there ("tid")."""
        source: LocalService = self
        device_id, inner = "", track_id
        if on_device(track_id):
            try:
                device, inner = self._route(track_id)
            except ServiceError:
                return None
            if not isinstance(device["instance"], LocalService):
                return None
            source, device_id = device["instance"], device["id"]
        t = source.tracks.get(inner)
        if not t or not os.path.isfile(t["path"]):
            return None
        from_apple = isinstance(source, IpodService) or (device_id and self._device(device_id)["kind"] == "ios")
        own = source.album_art.get(source._album_key(t))  # the album's picture, set or found on the Deck
        return {**t, "tid": inner, "from": device_id, "from_ipod": bool(from_apple),
                "cover": os.path.join(source.art_dir, own) if own and source.art_dir else "",
                "artist_folder": source._album_artist(t) or "Unknown artist",
                "album_folder": t["album"] or "Unknown album"}

    async def _files(self, kind: str, item_id: str, ids: Optional[list[str]] = None,
                     title: str = "") -> tuple[list[dict[str, Any]], str, LocalService]:
        """The files of a track, album, artist or playlist (or of the songs `ids`: a MusicDeck playlist's),
        with a playlist's name and the index they're in."""
        source: LocalService = self
        inner = item_id
        if ids is None:
            if on_device(item_id):
                device, inner = self._route(item_id)
                if not isinstance(device["instance"], LocalService):
                    raise ServiceError("A CD is ripped, not copied: Rip to Local files is on its album")
                source = device["instance"]
            if kind == "track":
                ids = [item_id]
            else:
                page = await source.open(kind, inner)
                prefix = item_id[:len(item_id) - len(inner)]
                ids = [prefix + t["id"] for t in page.get("tracks") or []]
                title = page.get("title", "") if kind == "playlist" else ""
        files = [r for r in (self._record(tid) for tid in dict.fromkeys(ids)) if r]
        if not files:
            raise ServiceError("Nothing there to copy")
        return files, title, source

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

    async def copy_to(self, kind: str, item_id: str, target: str, ids: Optional[list[str]] = None,
                      title: str = "") -> str:
        """Copies a track, album, artist or playlist (or the songs `ids`, a MusicDeck playlist named
        `title`) to a device ("dv..." id) or from one to the Deck ("deck"), in the background; returns what
        to tell the user. To an iPod, into its library (a playlist stays a playlist there)."""
        if self.copy_progress["running"]:
            raise ServiceError(f"Still copying to {self.copy_progress['to']}")
        files, playlist, _ = await self._files(kind, item_id, ids, title)
        files = [f for f in files if f["from"] != ("" if target == "deck" else target)]
        if not files:
            raise ServiceError("That's already there")
        folder, name, index = self._destination(target)
        if isinstance(index, DeviceService):
            if not isinstance(index, IpodService) and not index.writable():
                raise ServiceError(f"{name} can only be read")
            await index.free_for(sum(int(f.get("size") or 0) for f in files))
        self.copy_progress = {"running": True, "done": 0, "total": len(files), "to": name, "error": ""}
        if isinstance(index, IpodService):
            self.copying = asyncio.ensure_future(self._copy_to_ipod(files, playlist, name, index))
        else:
            self.copying = asyncio.ensure_future(self._copy(files, folder, name, index))
        return f"Copying {models.count(len(files), 'song')} to {name}"

    async def _copy(self, files: list[dict[str, Any]], folder: str, name: str, index: Optional[LocalService]) -> None:
        copied = 0
        try:
            for f in files:
                where = os.path.join(folder, models.safe_name(f["artist_folder"], "Unknown artist"),
                                     models.safe_name(f["album_folder"], "Unknown album"))
                file_name = None
                # an iPod's files are "ABCD.mp3": copied off it, they're named as their tags say
                if f.get("from_ipod"):
                    number = f"{f['track']:02d} " if f.get("track") else ""
                    file_name = (number + models.safe_name(f.get("title") or "Track", "Track")
                                 + os.path.splitext(f["path"])[1].lower())
                source = await self._readable(f)
                if isinstance(index, PhoneService):
                    copied += await asyncio.to_thread(index.copy_in, source, where, file_name)
                else:
                    copied += await asyncio.to_thread(self._copy_one, source, where, file_name)
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

    async def _readable(self, f: dict[str, Any]) -> str:
        """A song's file to read: a phone's (on Windows) copied off it first, when it isn't on the PC."""
        try:
            instance = self._device(f["from"])["instance"] if f.get("from") else None
        except ServiceError:
            instance = None
        if isinstance(instance, PhoneService):
            return await asyncio.to_thread(instance.ensure, f["path"])
        return f["path"]

    async def _copy_to_ipod(self, files: list[dict[str, Any]], playlist: str, name: str, ipod: IpodService) -> None:
        def progress(done: int) -> None:
            self.copy_progress["done"] = done
        try:
            files = [{**f, "path": await self._readable(f)} for f in files]
            added = await ipod.put(files, playlist, progress)
            self.copy_progress["running"] = False
            if self.announce:
                self.announce(f"Copied {models.count(added, 'song')} to {name}" if added else f"Already on {name}")
        except Exception as e:  # noqa: BLE001 - a full iPod, one unplugged mid-copy: said, not raised
            self.copy_progress.update(running=False, error=str(e) or type(e).__name__)
            if self.announce:
                self.announce(f"Copying to {name} stopped: {self.copy_progress['error']}")

    # --- burning CDs ---

    async def burn_plan(self, kind: str, item_id: str, ids: Optional[list[str]] = None,
                        title: str = "") -> dict[str, Any]:
        return (await self._plan(kind, item_id, ids, title))[0]

    async def _plan(self, kind: str, item_id: str, ids: Optional[list[str]],
                    title: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """What burning a track, album, artist or playlist (or the songs `ids`) to CD would be: {"drive",
        "songs", "minutes", "capacity" (the disc's minutes), "erase" (a CD-RW with something on it),
        "title", "artist"}; or why it can't be (a ServiceError)."""
        cdrskin = burn.burner(self.runtime_dir)
        if not cdrskin:
            raise ServiceError("Burning CDs needs cdrskin, which this build doesn't have")
        if not shutil.which("ffmpeg"):
            raise ServiceError("Burning CDs needs ffmpeg")
        if self.burn_progress["running"]:
            raise ServiceError(f"Still burning {self.burn_progress['title']}")
        files, playlist, _ = await self._files(kind, item_id, ids, title)
        drives = cd.drives()
        if not drives:
            raise ServiceError("There's no CD drive plugged in")
        discs = [(d, await asyncio.to_thread(burn.probe, cdrskin, d)) for d in drives]
        usable = [(d, disc) for d, disc in discs if disc.usable]
        if not usable:
            if any(disc.status == "full" for _, disc in discs):
                raise ServiceError("The CD in the drive has something on it, and a CD-R can't be erased: "
                                   "put in a blank CD-R, or a CD-RW")
            if any(disc.status == "unsuitable" or (disc.media and not disc.media.startswith("CD"))
                   for _, disc in discs):
                raise ServiceError("Audio CDs are burned on CD-R or CD-RW, not DVDs")
            raise ServiceError("Put a blank CD in the drive")
        drive, disc = usable[0]
        needed = burn.frames_needed([float(f.get("duration") or 0) for f in files])
        minutes = -(-needed // (burn.FRAMES_PER_SECOND * 60))
        if needed > (disc.frames or burn.CD_80):
            raise ServiceError(f"That's {models.count(minutes, 'minute')} of music, and the CD holds {disc.minutes}")
        album, artist = self._disc_names(kind, files, playlist)
        return {"drive": drive, "songs": len(files), "minutes": minutes, "capacity": disc.minutes,
                "erase": disc.status != "blank", "title": album, "artist": artist}, files

    def _disc_names(self, kind: str, files: list[dict[str, Any]], playlist: str) -> tuple[str, str]:
        """The CD's title and artist for its CD-Text: an album's own, a playlist's name (by "Various
        Artists" unless it's all one artist's)."""
        artists = {f.get("albumartist") or f.get("artist") or "" for f in files}
        artist = artists.pop() if len(artists) == 1 else "Various Artists"
        if kind == "album" and files:
            return files[0].get("album") or "Untitled", artist
        if kind == "artist":
            return artist, artist
        return playlist or (files[0].get("album") if kind == "track" and files else "") or "Mix", artist

    async def burn(self, kind: str, item_id: str, ids: Optional[list[str]] = None, title: str = "") -> str:
        """Burns a track, album, artist or playlist (or the songs `ids`) to the blank CD in the drive, in
        the background; returns what to tell the user."""
        if self.copy_progress["running"]:
            raise ServiceError(f"Still copying to {self.copy_progress['to']}")
        plan, files = await self._plan(kind, item_id, ids, title)
        self.burn_progress = {"running": True, "stage": "converting", "done": 0, "total": len(files), "percent": 0,
                              "title": plan["title"], "error": ""}
        self.burning = asyncio.ensure_future(self._burn(files, plan))
        return f"Burning {plan['title']} to CD ({models.count(len(files), 'song')}, {plan['minutes']} min)"

    async def _burn(self, files: list[dict[str, Any]], plan: dict[str, Any]) -> None:
        progress = self.burn_progress
        work = tempfile.mkdtemp(prefix="burn-", dir=self.runtime_dir)
        cdrskin = burn.burner(self.runtime_dir) or "cdrskin"
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        try:
            wavs, sizes = [], []
            for n, f in enumerate(files, 1):
                wav = os.path.join(work, f"{n:02d}.wav")
                frames = await asyncio.to_thread(burn.to_cd_audio, ffmpeg, await self._readable(f), wav)
                wavs.append(wav)
                sizes.append(frames * burn.FRAME / 1024 / 1024)
                progress.update(done=n, percent=round(100 * n / len(files)))
            sheet = os.path.join(work, "cdtext.v07t")
            with open(sheet, "w", encoding="latin-1") as fh:
                fh.write(burn.cdtext_sheet(plan["title"], plan["artist"], files))
            if plan["erase"]:
                progress.update(stage="erasing", percent=0)
                await asyncio.to_thread(burn.erase, cdrskin, plan["drive"])
            progress.update(stage="burning", done=0, percent=0)
            total = sum(sizes) or 1

            def on_progress(track: int, written: int, _: int) -> None:
                before = sum(sizes[:max(0, track - 1)])
                progress.update(done=track, percent=min(100, round(100 * (before + written) / total)))
            await asyncio.to_thread(burn.burn, cdrskin, plan["drive"], wavs, sheet, on_progress)
            progress.update(running=False, percent=100, done=len(files))
            if self.announce:
                self.announce(f"Burned {plan['title']} to CD")
        except Exception as e:  # noqa: BLE001 - a disc that won't take it, a drive unplugged: said, not raised
            progress.update(running=False, error=str(e) or type(e).__name__)
            if self.announce:
                self.announce(f"Burning {plan['title']} stopped: {progress['error']}")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    async def remove_from_device(self, kind: str, item_id: str) -> str:
        """Deletes a track, album, artist or playlist's songs from the device they're on (from an iPod's
        library too); returns what to tell the user."""
        if not on_device(item_id):
            raise ServiceError("Only music on a plugged-in device is removed here")
        files, _, index = await self._files(kind, item_id)
        device = self._device(split(item_id)[0])
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

    # --- deleting (to the trash) and Recently deleted ---

    def _deleted_path(self) -> str:
        return os.path.join(self.runtime_dir, f"local-{self.instance_id}-deleted.json")

    def _deleted(self) -> list[dict[str, Any]]:
        """What was deleted lately that's still in the trash, newest first."""
        try:
            with open(self._deleted_path(), "r", encoding="utf-8") as f:
                batches = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return [b for b in batches if isinstance(b, dict) and any(trash.still_there(i) for i in b.get("items", []))]

    def _save_deleted(self, batches: list[dict[str, Any]]) -> None:
        tmp = self._deleted_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(batches[:KEEP_DELETED], f)
        os.replace(tmp, self._deleted_path())

    def _within_library(self, path: str) -> bool:
        return any(path.startswith(r + os.sep) for r in self.roots)

    def _plan_delete(self, paths: list[str]) -> list[str]:
        """What to move to the trash for these songs: each one and its lyrics beside it, or its whole
        folder when nothing but those, artwork and a rip's logs would be left in it."""
        planned: list[str] = []
        by_folder: dict[str, list[str]] = {}
        for p in paths:
            by_folder.setdefault(os.path.dirname(p), []).append(p)
        for folder, songs in by_folder.items():
            gone = set(songs)
            try:
                names = os.listdir(folder)
            except OSError:
                names = []
            rest = [n for n in names if os.path.join(folder, n) not in gone]
            leftovers_only = all(not os.path.isdir(os.path.join(folder, n))
                                 and (os.path.splitext(n)[1].lower() in LEFTOVERS or n.startswith("."))
                                 for n in rest)
            if folder not in self.roots and self._within_library(folder) and leftovers_only:
                planned.append(folder)
                continue
            for song in songs:
                planned.append(song)
                lrc = os.path.splitext(song)[0] + ".lrc"
                if os.path.isfile(lrc):
                    planned.append(lrc)
        return planned

    async def delete(self, kind: str, item_id: str) -> str:
        """Moves a song, album, folder or playlist file to the trash; returns what to tell the user."""
        if on_device(item_id):
            raise ServiceError("Music on a device is removed from it, not deleted here")
        if kind == "playlist":
            playlist = self.playlists.get(item_id)
            if not playlist or not playlist.get("path"):
                raise ServiceError("That playlist isn't a file in the library")
            title, songs, planned = playlist["title"], 0, [playlist["path"]]
            names = [playlist["title"]]
        elif kind == "folder":
            folder = os.path.abspath(item_id)
            if folder in self.roots or not self._within_library(folder):
                raise ServiceError("Only folders inside the library are deleted")
            inside = [t for t in self.tracks.values() if t["path"].startswith(folder + os.sep)]
            title, songs, planned = os.path.basename(folder), len(inside), [folder]
            names = [t["title"] for t in inside]
        elif kind in ("track", "album"):
            items = self._target(kind, item_id)
            if kind == "track" and items[0][1].get("cue"):
                # one file holds the whole album (a CUE sheet's): a song can't go on its own
                raise ServiceError("This song is one file with the rest of its album (a CUE sheet): delete the album")
            if kind == "track":
                title = items[0][1]["title"]
            else:
                title = items[0][1]["album"] or "Album"
            songs = len(items)
            names = [t["title"] for _, t in items]
            # a CUE album's songs share one file, and its sheet goes with it
            paths = list(dict.fromkeys([t["path"] for _, t in items] + [t["cue_sheet"] for _, t in items if t.get("cue_sheet")]))
            planned = self._plan_delete(paths)
        else:
            raise ServiceError("Only songs, albums, folders and playlists are deleted")

        def move() -> list[dict[str, str]]:
            done = []
            for path in planned:
                done.append(trash.trash(path))
            # an artist's folder left empty goes too (it's empty: nothing to keep)
            for path in planned:
                parent = os.path.dirname(path)
                if parent not in self.roots and self._within_library(parent):
                    try:
                        os.rmdir(parent)
                    except OSError:
                        pass
            return done

        moved = await asyncio.to_thread(move)
        if any(i["trashed"] for i in moved):
            batch = {"id": f"{random.getrandbits(48):012x}", "at": time.time(), "kind": kind, "title": title,
                     "songs": songs, "names": names[:40], "items": moved}
            self._save_deleted([batch] + self._deleted())
        self.rescan()
        self._browse_cache = None
        self.forget_browse_info()
        what = f"{title} ({models.count(songs, 'song')})" if kind in ("album", "folder") else title
        return f"Moved {what} to the trash"

    def _deleted_card(self, batch: dict[str, Any]) -> dict[str, Any]:
        ago = _ago(time.time() - batch.get("at", 0))
        about = models.count(batch.get("songs", 0), "song") if batch.get("kind") != "playlist" else "Playlist"
        return models.collection(f"{DELETED}{batch['id']}", "list", batch.get("title", ""), f"{about} · {ago}")

    def _open_deleted(self, batch_id: str) -> dict[str, Any]:
        batch = next((b for b in self._deleted() if b["id"] == batch_id), None)
        if not batch:
            raise ServiceError("That's no longer in the trash")
        names = batch.get("names") or []
        notice = ("In the trash: " + ", ".join(names[:12]) + ("…" if len(names) > 12 else "")) if names else None
        return models.page(batch.get("title", ""), self._deleted_card(batch)["subtitle"],
                           action={"label": "Restore", "call": "restore"}, notice=notice)

    async def _restore(self, batch_id: str) -> str:
        batches = self._deleted()
        batch = next((b for b in batches if b["id"] == batch_id), None)
        if not batch:
            raise ServiceError("That's no longer in the trash")
        back = await asyncio.to_thread(lambda: sum(trash.restore(i) for i in batch["items"]))
        self._save_deleted([b for b in batches if b["id"] != batch_id])
        self.rescan()
        self._browse_cache = None
        self.forget_browse_info()
        if not back:
            raise ServiceError("It couldn't be put back: something else is where it was")
        return f"Restored {batch.get('title', '')}"

    async def stop(self) -> None:
        if self.copying:
            self.copying.cancel()
        await super().stop()


def _ago(seconds: float) -> str:
    """"just now", "5 minutes ago", "3 hours ago", "2 days ago"."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{models.count(minutes, 'minute')} ago"
    if minutes < 60 * 24:
        return f"{models.count(minutes // 60, 'hour')} ago"
    return f"{models.count(minutes // (60 * 24), 'day')} ago"
