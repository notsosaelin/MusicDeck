"""
An iPod plugged in (in disk mode, as any iPod but the touch is): which one it is, whether MusicDeck can
put music on it, and doing so, through its own database (itunesdb.py) as iTunes does.

* Which iPod: iPod_Control/Device/SysInfoExtended (when iTunes or libgpod left one: its database version
  says how the iPod checks its database), else SysInfo's ModelNumStr ("xB147": an 80 GB iPod classic),
  else the USB product id (sysfs, no root needed), else how its database is signed now.
* Its FireWire GUID, which the iPod classic and the 3rd/4th generation nano sign their database with: in
  SysInfo(Extended), else the USB serial number, which is the GUID (libgpod's README.SysInfo).
* What can be synced: every iPod with a disk mode and a plain iTunesDB (1st-5.5th generation, photo,
  mini, nano 1G-4G, classic, every shuffle). Not the nano 5G (it reads SQLite databases signed with a key
  only an iTunes sync leaves behind), not the nano 6G/7G (hashAB, a closed library), not an iPod touch
  (no disk mode), and not a Mac-formatted (HFS+) iPod, which Linux mounts read only; those still play.
  An iPod running Rockbox isn't one of these: it's a player like any other (devices.py).
* Syncing: files go to iPod_Control/Music/Fnn/<4 letters>.<ext>, as iTunes names them; formats the iPod
  can't play are converted with ffmpeg (FLAC and other lossless to Apple Lossless, or AAC for a 1st/2nd
  generation shuffle; Ogg, Opus... to 256 kbps AAC). The database is read again, what the iPod counted
  since the last sync folded in, the new tracks added, and written to a temporary file and moved over the
  old one (kept on the Deck first). A shuffle gets its iTunesSD too.
"""
import os
import random
import re
import shutil
import string
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import itunesdb, native
from .devices import _unescape
from .itunesdb import HASH58, HASH72, HASHAB, HASH_NONE, ITunesDB, ITunesDBError

# model numbers (SysInfo's ModelNumStr without its first letter) by generation, from libgpod 0.8.3
MODELS = {
    "first": "8513 8541 8697 8709",
    "second": "8737 8740 8738 8741",
    "third": "8976 8946 9460 9244 8948 9245",
    "fourth": "9282 9787 9268 E436",
    "mini_1": "9160 9436 9435 9434 9437",
    "mini_2": "9800 9802 9804 9806 9801 9803 9805 9807",
    "photo": "A079 A127 9829 9585 9830 9586 S492",
    "shuffle_1": "9724 9725",
    "shuffle_2": "A546 A947 A949 A951 A953 C167 B225 B233 B231 B227 B228 B229 B518 B520 B522 B524 B526",
    "shuffle_3": "C306 C323 C381 C384 C387 B867 C164 C303 C307 C328 C331",
    "shuffle_4": "C584 C585 C749 C750 C751",
    "nano_1": "A350 A352 A004 A099 A005 A107",
    "video_1": "A002 A146 A003 A147 A452",
    "video_2": "A444 A446 A664 A448 A450",
    "nano_2": "A477 A426 A428 A487 A489 A725 A726 A497",
    "classic_1": "B029 B147 B145 B150",
    "classic_2": "B562 B565",
    "classic_3": "C293 C297",
    "nano_3": "A978 A980 B261 B249 B253 B257",
    "nano_4": "B480 B651 B654 B657 B660 B663 B666 B598 B732 B735 B739 B742 B745 B748 B751 B754 B903 B905 B907 B909 "
              "B911 B913 B915 B917 B918",
    "nano_5": "C027 C031 C034 C037 C040 C043 C046 C049 C050 C060 C062 C064 C066 C068 C070 C072 C074 C075",
    "nano_6": "C525 C688 C689 C690 C691 C692 C693 C526 C694 C695 C696 C697 C698 C699",
}
GENERATION_OF = {model: gen for gen, models in MODELS.items() for model in models.split()}
NAMES = {
    "first": "iPod (1st generation)", "second": "iPod (2nd generation)", "third": "iPod (3rd generation)",
    "fourth": "iPod (4th generation)", "photo": "iPod photo", "mini_1": "iPod mini", "mini_2": "iPod mini (2nd generation)",
    "shuffle_1": "iPod shuffle (1st generation)", "shuffle_2": "iPod shuffle (2nd generation)",
    "shuffle_3": "iPod shuffle (3rd generation)", "shuffle_4": "iPod shuffle (4th generation)",
    "nano_1": "iPod nano (1st generation)", "nano_2": "iPod nano (2nd generation)", "nano_3": "iPod nano (3rd generation)",
    "nano_4": "iPod nano (4th generation)", "nano_5": "iPod nano (5th generation)", "nano_6": "iPod nano (6th generation)",
    "nano_7": "iPod nano (7th generation)", "video_1": "iPod (5th generation)", "video_2": "iPod (5.5th generation)",
    "classic_1": "iPod classic", "classic_2": "iPod classic (120 GB)", "classic_3": "iPod classic (2009)",
}
# USB product ids in disk mode (wiki.yuo.be/dop:ipod_pids); 1266, 1267, 1302 and 1303 aren't confirmed there,
# and decide nothing on their own (a nano 6G/7G isn't synced anyway; a shuffle's iTunesSD says its format)
USB_PRODUCTS = {0x1201: "third", 0x1203: "fourth", 0x1204: "photo", 0x1205: "mini_1", 0x1209: "video_1",
                0x1261: "classic_1", 0x120A: "nano_1", 0x1260: "nano_2", 0x1262: "nano_3", 0x1263: "nano_4",
                0x1265: "nano_5", 0x1266: "nano_6", 0x1267: "nano_7", 0x1300: "shuffle_1", 0x1301: "shuffle_2",
                0x1302: "shuffle_3", 0x1303: "shuffle_4"}
SIGNED = {"classic_1": HASH58, "classic_2": HASH58, "classic_3": HASH58, "nano_3": HASH58, "nano_4": HASH58,
          "nano_5": HASH72, "nano_6": HASHAB, "nano_7": HASHAB}
# SysInfoExtended's DBVersion: 0-2 unsigned, 3 hash58, 4 hash72, 5 hashAB (libgpod)
DB_VERSIONS = {0: HASH_NONE, 1: HASH_NONE, 2: HASH_NONE, 3: HASH58, 4: HASH72, 5: HASHAB}
# what an iPod plays as it is; everything else is converted (lossless to Apple Lossless)
PLAYABLE = {".mp3": "MPEG audio file", ".m4a": "AAC audio file", ".m4b": "AAC audio book file",
            ".wav": "WAV audio file", ".aif": "AIFF audio file", ".aiff": "AIFF audio file"}
LOSSLESS_SOURCES = {".flac", ".wav", ".aif", ".aiff", ".ape", ".wv"}
NO_LOSSLESS = {"shuffle_1", "shuffle_2"}


@dataclass
class IpodInfo:
    root: str
    generation: str  # "classic_1", "nano_3"... or "" when it couldn't be told
    scheme: int  # how its database must be signed (itunesdb.HASH_*), -1 when unknown
    guid: Optional[bytes]
    compressed: bool  # its database is an iTunesCDB (nano 5G and later)
    writable_fs: bool
    fs: str = ""  # how it's formatted: "vfat" (Windows-formatted) or "hfsplus" (Mac-formatted)

    @property
    def model(self) -> str:
        return NAMES.get(self.generation, "iPod")

    @property
    def shuffle(self) -> bool:
        return self.generation.startswith("shuffle")

    def why_not(self) -> str:
        """Why music can't be put on this iPod, or "" when it can."""
        if not self.writable_fs and self.fs == "hfsplus":
            # Linux writes HFS+ only with its journal off; turning it off needs a Mac (or a reformat)
            return ("This iPod is Mac-formatted with journaling on, which Linux only reads. On a Mac, Disk Utility → "
                    "the iPod → File → Disable Journaling (or: diskutil disableJournal), then plug it in here again. "
                    "Or restore it with iTunes for Windows, which formats it for both")
        if not self.writable_fs:
            return "This iPod can only be read: it's mounted read only, or its files belong to another user"
        if self.compressed or self.generation == "nano_5" or self.scheme == HASH72:
            return f"The {self.model} keeps its library in a way only iTunes can sign; it can be played from, not synced"
        if self.generation in ("nano_6", "nano_7") or self.scheme == HASHAB:
            return f"The {self.model} signs its library with a key MusicDeck doesn't have; it can be played from, not synced"
        if self.scheme == HASH58 and not self.guid:
            return "This iPod's FireWire ID couldn't be found, which it needs to accept a new library"
        if self.scheme < 0:
            return "This iPod couldn't be identified, so MusicDeck won't risk changing its library"
        return ""


def control_dir(root: str) -> Optional[str]:
    """iPod_Control (any case: it's FAT32 or HFS+), or None for what isn't an iPod."""
    try:
        return next((os.path.join(root, n) for n in os.listdir(root) if n.lower() == "ipod_control"), None)
    except OSError:
        return None


def _find(directory: str, name: str) -> Optional[str]:
    try:
        return next((os.path.join(directory, n) for n in os.listdir(directory) if n.lower() == name.lower()), None)
    except OSError:
        return None


def _read(path: Optional[str]) -> bytes:
    if not path:
        return b""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def sysinfo(control: str) -> dict[str, str]:
    """SysInfo's "Key: value" lines, and the few SysInfoExtended keys used here (its XML read by
    pattern: Decky's Python has no XML tree parser)."""
    found: dict[str, str] = {}
    device = _find(control, "Device") or ""
    for line in _read(_find(device, "SysInfo")).decode("utf-8", "replace").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            found[key.strip()] = value.strip()
    xml = _read(_find(device, "SysInfoExtended")).decode("utf-8", "replace")
    for key in ("FireWireGUID", "SerialNumber", "FamilyID", "DBVersion"):
        m = re.search(rf"<key>{key}</key>\s*<(?:string|integer)>([^<]*)<", xml)
        if m:
            found["x" + key] = m.group(1).strip()
    return found


def usb_facts(source: str) -> tuple[Optional[int], str]:
    """(USB product id, serial number) of the device a /dev/sd* partition is on, from sysfs."""
    name = os.path.basename(source or "")
    if not name or not os.path.isdir("/sys/class/block"):
        return None, ""
    try:
        here = os.path.realpath(os.path.join("/sys/class/block", name))
    except OSError:
        return None, ""
    while here and os.path.dirname(here) != here:
        vendor = _read(os.path.join(here, "idVendor")).decode().strip()
        if vendor:
            product = _read(os.path.join(here, "idProduct")).decode().strip()
            serial = _read(os.path.join(here, "serial")).decode().strip()
            try:
                return (int(product, 16) if vendor.lower() == "05ac" else None), serial
            except ValueError:
                return None, serial
        here = os.path.dirname(here)
    return None, ""


def mount_type(root: str) -> str:
    """The file system a mount is ("vfat", "hfsplus"...), from /proc/self/mounts; "" when not found."""
    try:
        with open("/proc/self/mounts", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 2 and _unescape(parts[1]) == root:
                    return parts[2]
    except OSError:
        pass
    return ""


def database_path(control: str) -> tuple[Optional[str], bool]:
    """The database there is: (path, compressed?); iTunesCDB wins where both are (the nano 5G on)."""
    itunes = _find(control, "iTunes")
    if not itunes:
        return None, False
    cdb = _find(itunes, "iTunesCDB")
    if cdb:
        return cdb, True
    return _find(itunes, "iTunesDB"), False


def identify(root: str, source: str = "") -> Optional[IpodInfo]:
    control = control_dir(root)
    if not control:
        return None
    info = sysinfo(control)
    product, serial = usb_facts(source)
    generation = ""
    model = info.get("ModelNumStr", "")
    if model[1:5] in GENERATION_OF:
        generation = GENERATION_OF[model[1:5]]
    elif model[:4] in GENERATION_OF:
        generation = GENERATION_OF[model[:4]]
    elif product in USB_PRODUCTS:
        generation = USB_PRODUCTS[product]
    path, compressed = database_path(control)
    current = None
    if path:
        with open(path, "rb") as f:
            head = f.read(0x40)
        # how the database there is signed (its header's +0x30), without reading the rest of it
        current = int.from_bytes(head[0x30:0x32], "little") if head[:4] == b"mhbd" and len(head) >= 0x32 else None
    if info.get("xDBVersion", "").isdigit():
        scheme = DB_VERSIONS.get(int(info["xDBVersion"]), -1)
    elif generation:
        scheme = SIGNED.get(generation, HASH_NONE)
    elif current is not None:
        scheme = current
    else:
        scheme = -1
    # the database as it is was accepted by the iPod: a signature on it beats a guessed model's
    if current and current != scheme and not info.get("xDBVersion"):
        scheme = current
    guid = (itunesdb.guid_bytes(info.get("xFireWireGUID", "")) or itunesdb.guid_bytes(info.get("FirewireGuid", ""))
            or itunesdb.guid_bytes(serial))
    writable = os.access(control, os.W_OK)
    return IpodInfo(root, generation, scheme, guid, compressed, writable, mount_type(root))


def load(root: str) -> Optional[ITunesDB]:
    """Its database, None when it has none (or one that can't be read)."""
    control = control_dir(root)
    path = database_path(control)[0] if control else None
    data = _read(path)
    if not data:
        return None
    try:
        return ITunesDB.parse(data)
    except (ITunesDBError, IndexError, ValueError):
        return None


def on_disk(root: str, ipod_path: str) -> str:
    """A track's file: its database path ("/iPod_Control/Music/F01/ABCD.mp3") under the mount."""
    return os.path.join(root, *[p for p in ipod_path.split("/") if p])


# --- syncing ---

def _filetype(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".m4a":
        try:
            from mutagen.mp4 import MP4
            if str(getattr(MP4(path).info, "codec", "")).lower() == "alac":
                return "Apple Lossless audio file"
        except Exception:  # noqa: BLE001 - an unreadable file is still an AAC file by its name
            pass
    return PLAYABLE.get(ext, "")


def convert(path: str, generation: str, scratch: str) -> str:
    """The file as the iPod can play it: itself, or a converted copy in `scratch` (ffmpeg)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in PLAYABLE and not (ext in (".wav", ".aif", ".aiff") and generation in NO_LOSSLESS):
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ITunesDBError(f"{ext[1:].upper()} files need converting for the iPod, which needs ffmpeg")
    lossless = ext in LOSSLESS_SOURCES and generation not in NO_LOSSLESS
    out = os.path.join(scratch, f"{random.getrandbits(48):012x}.m4a")
    # Apple Lossless at CD quality (what every iPod with it plays), else 256 kbps AAC; tags kept
    codec = ["-c:a", "alac", "-sample_fmt", "s16p", "-ar", "44100"] if lossless else ["-c:a", "aac", "-b:a", "256k"]
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-map", "0:a:0",
                             "-map_metadata", "0", *codec, "-f", "ipod", out], capture_output=True, text=True,
                            **native.NO_WINDOW)
    if result.returncode != 0 or not os.path.isfile(out):
        raise ITunesDBError(f"Couldn't convert {os.path.basename(path)}: {result.stderr.strip()[-200:]}")
    return out


def _music_folder(control: str) -> str:
    """One of iPod_Control/Music's F00, F01... folders (iTunes spreads files over them)."""
    music = _find(control, "Music") or os.path.join(control, "Music")
    os.makedirs(music, exist_ok=True)
    folders = sorted(n for n in os.listdir(music) if re.fullmatch(r"[Ff]\d\d", n))
    if not folders:
        folders = ["F00"]
        os.makedirs(os.path.join(music, "F00"), exist_ok=True)
    return os.path.join(music, random.choice(folders))


def _new_name(folder: str, ext: str) -> str:
    while True:
        name = "".join(random.choices(string.ascii_uppercase, k=4)) + ext
        if not os.path.exists(os.path.join(folder, name)):
            return os.path.join(folder, name)


def _fold_extras(db: ITunesDB, itunes: str) -> list[str]:
    """What the iPod added since the last sync, put into the database: play counts, ratings and
    On-The-Go playlists. Returns the files to tidy once the database is written."""
    used = []
    counts = _find(itunes, "Play Counts")
    if counts:
        db.fold_play_counts(_read(counts))
        used.append(counts)
    otg = sorted(n for n in os.listdir(itunes) if n.lower().startswith("otgplaylistinfo"))
    for number, name in enumerate(otg, 1):
        db.add_on_the_go(_read(os.path.join(itunes, name)), "On-The-Go" if number == 1 else f"On-The-Go {number}")
        used.append(os.path.join(itunes, name))
    return used


def _write(info: IpodInfo, db: ITunesDB, itunes: str, used: list[str], backup_dir: str) -> None:
    """The database (and a shuffle's iTunesSD) written in full beside the old one, then moved over it;
    the old one kept on the Deck first. Then "Play Counts" is put aside and On-The-Go's files go, as
    libgpod does, as they're in the database now."""
    target = _find(itunes, "iTunesDB") or os.path.join(itunes, "iTunesDB")
    data = db.to_bytes(info.guid, info.scheme)
    if os.path.isfile(target):
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copyfile(target, os.path.join(backup_dir, "iTunesDB.previous"))
    temp = target + ".musicdeck"
    with open(temp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, target)
    if info.shuffle:
        sd = _find(itunes, "iTunesSD") or os.path.join(itunes, "iTunesSD")
        old_format = info.generation in ("shuffle_1", "shuffle_2") or (os.path.isfile(sd) and _read(sd)[:4] != b"bdhs"
                                                                       and info.generation not in ("shuffle_3", "shuffle_4"))
        with open(sd + ".musicdeck", "wb") as f:
            f.write(itunesdb.shuffle_db(db, old_format))
        os.replace(sd + ".musicdeck", sd)
    for path in used:
        try:
            if os.path.basename(path).lower() == "play counts":
                os.replace(path, path + ".bak")
            else:
                os.remove(path)
        except OSError:
            pass
    for name in ("iTunesShuffle", "iTunesStats"):
        path = _find(itunes, name)
        if path and info.shuffle:
            try:
                os.remove(path)
            except OSError:
                pass


def _open(info: IpodInfo) -> tuple[ITunesDB, str, list[str]]:
    """The database as it is on the iPod now (a new one for an iPod that has none), its folder, and
    what was folded into it."""
    control = control_dir(info.root)
    if not control:
        raise ITunesDBError("That's not an iPod any more")
    itunes = _find(control, "iTunes") or os.path.join(control, "iTunes")
    os.makedirs(itunes, exist_ok=True)
    path = _find(itunes, "iTunesDB")
    db = ITunesDB.parse(_read(path)) if path and _read(path) else itunesdb.new_database("iPod")
    return db, itunes, _fold_extras(db, itunes)


def add(info: IpodInfo, items: list[dict[str, Any]], playlist: str, backup_dir: str,
        progress: Callable[[int], None]) -> int:
    """Puts files on the iPod: items [{"path", plus tags: title, artist, album...}]; a playlist of them
    when `playlist` is given. Returns how many were added (one already there, same tags and size, isn't)."""
    reason = info.why_not()
    if reason:
        raise ITunesDBError(reason)
    db, itunes, used = _open(info)
    control = control_dir(info.root)
    assert control
    known = {(t.fields()["title"].lower(), t.fields()["artist"].lower(), t.fields()["album"].lower()): t.id
             for t in db.tracks}
    added, ids = 0, []
    with tempfile.TemporaryDirectory(prefix="musicdeck-ipod-") as scratch:
        for n, item in enumerate(items, 1):
            same = (str(item.get("title") or "").lower(), str(item.get("artist") or "").lower(),
                    str(item.get("album") or "").lower())
            if same in known:
                ids.append(known[same])
                progress(n)
                continue
            source = convert(item["path"], info.generation, scratch)
            size = os.path.getsize(source)
            if shutil.disk_usage(info.root).free < size + 8 * 1024 * 1024:
                raise ITunesDBError("The iPod is full")
            from .services.local import read_tags
            tags = read_tags(source)
            dest = _new_name(_music_folder(control), os.path.splitext(source)[1].lower())
            shutil.copyfile(source, dest)
            ipod_path = "/" + os.path.relpath(dest, info.root).replace(os.sep, "/")
            year = str(item.get("year") or tags.get("year") or "")[:4]
            track = db.add_track({**tags, **{k: v for k, v in item.items() if v not in (None, "")},
                                  "path": ipod_path, "filetype": _filetype(dest), "size": size,
                                  "duration": tags.get("duration") or item.get("duration") or 0,
                                  "bitrate": tags.get("bitrate") or 0, "rate": tags.get("rate") or 44100,
                                  "year": int(year) if year.isdigit() else 0})
            known[same] = track.id
            ids.append(track.id)
            added += 1
            progress(n)
    db.rebuild_master()
    if playlist and ids:
        db.add_playlist(playlist, ids)
    _write(info, db, itunes, used, backup_dir)
    return added


def remove(info: IpodInfo, ipod_paths: set[str], backup_dir: str) -> int:
    """Takes tracks (by their path on the iPod) off it: from its database, every playlist, and its disk."""
    reason = info.why_not()
    if reason:
        raise ITunesDBError(reason)
    db, itunes, used = _open(info)
    wanted = {p.lower() for p in ipod_paths}
    ids = {t.id for t in db.tracks if t.fields()["path"].lower() in wanted}
    gone = db.remove_tracks(ids)
    db.rebuild_master()
    _write(info, db, itunes, used, backup_dir)
    for t in gone:
        try:
            os.remove(on_disk(info.root, t.fields()["path"]))
        except OSError:
            pass
    return len(gone)
