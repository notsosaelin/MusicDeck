"""
Music players, iPods, USB sticks and phones plugged into the device, found as they come and go, each
then played as a source of its own while it's there (DeviceService), as the Xbox played an MP3 player.

* USB mass storage (MP3 players, iPods in disk mode, sticks, drives): SteamOS and Bazzite mount them
  under /run/media/<user>/<label> by themselves (udisks). Read from /proc/self/mounts: what's mounted
  from a /dev/sd* device there. The SD card (mmcblk) and internal drives are not "plugged in" and are
  left out: they're Local files' to scan if the user adds them there.
* Phones and newer players (MTP): the desktop's GIO/GVfs lists them (`gio mount -li`); MusicDeck mounts
  any that isn't yet (`gio mount mtp://...`) and finds it under /run/user/<uid>/gvfs/mtp:host=.../, where
  each storage's Music folder is what's played (a phone holds much more than music). GVfs runs in game
  mode on Bazzite; where it doesn't (or there's no gio), only USB storage is found. A phone shows up once
  it's set to "File transfer" (on Android, the USB notification).
* Audio CDs: a disc in a /dev/sr* drive (cd.py reads it), its table of contents read once when it goes
  in; played by AudioCdService, which can rip it to Local files.
* Linux only (on Windows, removable drives are drive letters; not yet).

A device's id comes from what identifies it (its file system's UUID, or the phone's MTP name), so it
keeps its index, favourites and playlists between plugs.
"""
import asyncio
import hashlib
import os
import re
import shutil
import urllib.parse
from typing import Any, Awaitable, Callable, Optional

from . import cd, debuglog

POLL_SECONDS = 3
# MTP needs a moment longer: `gio` is a process each time
MTP_EVERY = 2
#: folders on a phone's storage that hold music (the whole storage holds photos and apps' files too)
PHONE_MUSIC = ("Music", "Download", "Podcasts")
MTP_ROOT = re.compile(r"activation_root=((?:mtp|afc)://[^\s]+)")
#: an iPhone's or iPod touch's music (AFC's media share, as GVfs mounts it): what Apple's sync put there
IOS_MUSIC = ("iTunes_Control", "Music")


def _unescape(path: str) -> str:
    """/proc/self/mounts writes spaces and such as octal escapes (\\040)."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def _device_id(key: str) -> str:
    return "dev" + hashlib.sha1(key.encode()).hexdigest()[:9]


def _uuids() -> dict[str, str]:
    """{/dev/sdb1: its file system's UUID}"""
    found = {}
    try:
        for name in os.listdir("/dev/disk/by-uuid"):
            target = os.path.realpath(os.path.join("/dev/disk/by-uuid", name))
            found[target] = name
    except OSError:
        pass
    return found


SERIAL_TAG = re.compile(r"(?:^|[\s_-]+)S/?N\s*[:#]\s*[0-9A-Za-z]+", re.IGNORECASE)


def tidy_name(vendor: str, product: str, serial: str = "") -> str:
    """A device's name as it says it over USB, made readable: "HiBy" + "TRINKET-IOT-IDP _SN:AC53E709" is
    "HiBy TRINKET-IOT-IDP". Serial numbers out (the device's, and "SN:..." tags firmware leaves in its
    model), underscores as spaces, the maker once."""
    text = product.replace(serial, " ") if serial else product
    text = SERIAL_TAG.sub(" ", text).replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip(" -:")
    vendor = re.sub(r"\s+", " ", vendor.replace("_", " ")).strip()
    if vendor and not text.lower().startswith(vendor.lower()):
        text = f"{vendor} {text}".strip()
    return text or vendor


def _usb_names() -> dict[str, tuple[str, str]]:
    """{serial: (maker, product)} of what's plugged in by USB, as each says it (sysfs)."""
    found = {}
    try:
        for name in os.listdir("/sys/bus/usb/devices"):
            folder = os.path.join("/sys/bus/usb/devices", name)
            fields = []
            for field in ("serial", "manufacturer", "product"):
                try:
                    with open(os.path.join(folder, field), "r", encoding="utf-8", errors="replace") as f:
                        fields.append(f.read().strip())
                except OSError:
                    fields.append("")
            if fields[0]:
                found[fields[0]] = (fields[1], fields[2])
    except OSError:
        pass
    return found


def phone_name(host: str, usb_names: dict[str, tuple[str, str]]) -> str:
    """GVfs names a phone's folder <maker>_<model>_<serial>, spaces as underscores, URL-escaped
    ("HiBy_TRINKET-IOT-IDP__SN%3AAC53E709_ac53e709"). The device's own names (sysfs, matched by the
    serial at the end) keep their spaces; failing that, the folder's are used."""
    text = urllib.parse.unquote(host)
    for serial, (vendor, product) in usb_names.items():
        if product and text.endswith("_" + serial.replace(" ", "_")):
            return tidy_name(vendor, product, serial)
    head, _, serial = text.rpartition("_")
    if not head or not re.search(r"\d", serial) or len(serial) < 4:  # "Old_Walkman": no serial on the end
        head, serial = text, ""
    vendor, _, product = head.partition("_")
    return tidy_name(vendor, product, serial)


def usb_storage(mounts_text: str, user: str, uuids: dict[str, str]) -> list[dict[str, Any]]:
    """The USB mass storage mounted for `user` (MP3 players, iPods, sticks), from /proc/self/mounts."""
    found = []
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        source, target = parts[0], _unescape(parts[1])
        disc = bool(re.match(r"^/dev/sr\d+$", source))
        if not re.match(r"^/dev/sd[a-z]+\d*$", source) and not disc:
            continue  # the SD card (mmcblk), internal drives (nvme), anything not plugged in by USB
        if not (target.startswith(f"/run/media/{user}/") or target.startswith("/media/")):
            continue
        label = os.path.basename(target.rstrip("/")) or source
        # a data CD or DVD (MP3s burned for a car stereo) plays as a player, read only
        kind = "disc" if disc else "ipod" if is_ipod(target) else "usb"
        found.append({"id": _device_id("usb:" + uuids.get(source, label)), "name": label, "kind": kind,
                      "paths": [target], "source": source, "mount": target, "fs": parts[2],
                      "read_only": "ro" in (parts[3].split(",") if len(parts) > 3 else [])})
    return found


def is_ipod(root: str) -> bool:
    """An iPod with Apple's software (iPod_Control), read and synced through its database (ipod.py). One
    running Rockbox plays files as they are: a player like any other."""
    try:
        names = {n.lower() for n in os.listdir(root)}
    except OSError:
        return False
    return "ipod_control" in names and ".rockbox" not in names


def phone_storage(gvfs_dir: str, usb_names: Optional[dict[str, tuple[str, str]]] = None,
                  ios_names: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
    """Phones mounted by GVfs: /run/user/<uid>/gvfs/mtp:host=<name>/<storage>/Music (and the others in
    PHONE_MUSIC); a phone whose storages have none of them is played whole. iPhones and iPod touches
    (afc:host=<UDID>): the music Apple's sync put on them, iTunes_Control/Music, read only."""
    found = []
    try:
        hosts = sorted(n for n in os.listdir(gvfs_dir) if n.startswith(("mtp:host=", "afc:host=")))
    except OSError:
        return found
    for host in hosts:
        root = os.path.join(gvfs_dir, host)
        if host.startswith("afc:"):
            udid = host.partition("=")[2]
            music = os.path.join(root, *IOS_MUSIC)
            if "," in udid or not os.path.isdir(music):
                continue  # an app's documents share (",port="), or not trusted yet
            found.append({"id": _device_id("afc:" + udid), "name": (ios_names or {}).get(udid) or "iPhone",
                          "kind": "ios", "paths": [music], "mount": root, "uri": f"afc://{udid}/"})
            continue
        try:
            storages = [os.path.join(root, s) for s in sorted(os.listdir(root))]
        except OSError:
            continue  # still mounting, or the phone said no
        paths = [os.path.join(s, folder) for s in storages for folder in PHONE_MUSIC
                 if os.path.isdir(os.path.join(s, folder))]
        name = phone_name(host.partition("=")[2], usb_names or {})
        found.append({"id": _device_id("mtp:" + host), "name": name, "kind": "mtp", "paths": paths or storages,
                      "mount": root, "uri": f"mtp://{host.partition('=')[2]}/"})
    return found


def audio_cds(drives: list[str], has_disc: Callable[[str], bool], read_toc: Callable[[str], Any],
              known: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The audio CDs in the drives. A disc's table of contents is read once, when it goes in (`known`, by
    drive, keeps it while it stays)."""
    found = []
    for drive in drives:
        if not has_disc(drive):
            known.pop(drive, None)
            continue
        if drive not in known:
            try:
                toc = [list(t) for t in read_toc(drive)]
            except OSError as e:
                debuglog.debug("devices", "couldn't read a CD", drive=drive, error=str(e))
                continue
            disc = cd.disc_id([tuple(t) for t in toc])
            if not disc:
                continue
            known[drive] = {"id": _device_id("cd:" + disc), "name": "Audio CD", "kind": "cd", "paths": [],
                            "drive": drive, "toc": toc, "disc_id": disc}
        found.append(known[drive])
    return found


class DeviceWatcher:
    """Calls on_change(devices) whenever the set of plugged-in devices changes (and once at start)."""

    def __init__(self, on_change: Callable[[list[dict[str, Any]]], Awaitable[None]], logger: Any):
        self.on_change = on_change
        self.logger = logger
        self.task: Optional[asyncio.Task[None]] = None
        self.user = os.environ.get("USER") or ""
        self.uid = os.getuid() if hasattr(os, "getuid") else 0
        self.gio = shutil.which("gio")
        self.known: Optional[list[dict[str, Any]]] = None
        self.discs: dict[str, dict[str, Any]] = {}
        # iPhones' names by UDID (from GVfs), those asked to trust this computer, and who to tell
        self.ios_names: dict[str, str] = {}
        self.asked_trust: set[str] = set()
        # phones ejected: not mounted again until they're unplugged (no longer listed)
        self.ejected: set[str] = set()
        self.on_note: Optional[Callable[[str], Awaitable[None]]] = None

    @property
    def available(self) -> bool:
        return os.name == "posix" and os.path.exists("/proc/self/mounts")

    def start(self) -> None:
        if self.available and not self.task:
            self.task = asyncio.get_event_loop().create_task(self._run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            self.task = None

    def _gio_env(self) -> dict[str, str]:
        """gio talks to the desktop session's GVfs over its bus, which Decky's process isn't given."""
        run = f"/run/user/{self.uid}"
        return {**os.environ, "XDG_RUNTIME_DIR": run, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={run}/bus"}

    async def _mount_phones(self) -> None:
        """Mounts the phones GVfs sees and hasn't mounted (it lists them, it doesn't open them)."""
        if not self.gio:
            return
        try:
            proc = await asyncio.create_subprocess_exec(self.gio, "mount", "-li", stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL, env=self._gio_env())
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
        except (OSError, asyncio.TimeoutError):
            return
        text = out.decode(errors="replace")
        # an iPhone's name ("Sam's iPhone") as GVfs lists it, for its afc:host=<UDID> folder
        volume = ""
        for line in text.splitlines():
            named = re.match(r"\s*Volume\(\d+\):\s*(.+)$", line)
            if named:
                volume = named.group(1).strip()
            root = re.search(r"activation_root=afc://([^/\s]+)", line)
            if root and volume:
                self.ios_names[root.group(1)] = volume
        mounted = set(re.findall(r"-> ((?:mtp|afc)://[^\s]+)", text))
        listed = set(MTP_ROOT.findall(text))
        self.ejected &= listed
        self.asked_trust &= listed
        for root in listed - mounted - self.ejected:
            debuglog.debug("devices", "mounting a phone", root=root)
            try:
                mount = await asyncio.create_subprocess_exec(self.gio, "mount", root, stdout=asyncio.subprocess.DEVNULL,
                                                             stderr=asyncio.subprocess.DEVNULL, env=self._gio_env())
                code = await asyncio.wait_for(mount.wait(), 20)
            except (OSError, asyncio.TimeoutError):
                code = -1
            if code != 0 and root.startswith("afc://") and root not in self.asked_trust and self.on_note:
                # an iPhone opens only once it trusts this computer: it asks on its screen
                self.asked_trust.add(root)
                name = self.ios_names.get(root[len("afc://"):].strip("/"), "your iPhone")
                await self.on_note(f"Unlock {name} and tap Trust to see its music")

    def _scan(self) -> list[dict[str, Any]]:
        try:
            with open("/proc/self/mounts", "r", encoding="utf-8", errors="replace") as f:
                mounts = f.read()
        except OSError:
            mounts = ""
        return (usb_storage(mounts, self.user, _uuids())
                + phone_storage(f"/run/user/{self.uid}/gvfs", _usb_names(), self.ios_names)
                + audio_cds(cd.drives(), cd.has_audio_disc, cd.read_toc, self.discs))

    async def eject(self, device: dict[str, Any]) -> str:
        """Ejects a device as the desktop would ("Safely remove"): a stick or iPod unmounted and powered
        down, a phone's share closed, a disc's tray opened. Returns "" when done, else why not."""
        kind = device.get("kind")
        if kind == "cd":
            return "" if await asyncio.to_thread(cd.eject, device.get("drive", "")) else "The drive wouldn't open"
        if not self.gio:
            return "Ejecting needs GVfs (gio), which this system doesn't have"
        target = device.get("uri") if kind in ("mtp", "ios") else device.get("mount")
        if not target:
            return "MusicDeck doesn't know where that device is mounted"
        flag = "-u" if kind in ("mtp", "ios") else "-e"
        try:
            proc = await asyncio.create_subprocess_exec(self.gio, "mount", flag, target, stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.PIPE, env=self._gio_env())
            _, err = await asyncio.wait_for(proc.communicate(), 30)
        except (OSError, asyncio.TimeoutError):
            return "Ejecting took too long; something may still be using it"
        if proc.returncode != 0:
            message = err.decode(errors="replace").strip().splitlines()
            debuglog.debug("devices", "eject failed", target=target, error=message)
            busy = any("busy" in m.lower() for m in message)
            return "It's still in use; try again in a moment" if busy else (message[-1] if message else "It couldn't be ejected")
        if kind in ("mtp", "ios"):
            self.ejected.add(target)
        return ""

    async def _run(self) -> None:
        tick = 0
        while True:
            try:
                if tick % MTP_EVERY == 0:
                    await self._mount_phones()
                devices = await asyncio.to_thread(self._scan)
                if devices != self.known:
                    self.known = devices
                    await self.on_change(devices)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a device that misbehaves mustn't stop the watching
                self.logger.exception("Watching for plugged-in devices failed")
            tick += 1
            await asyncio.sleep(POLL_SECONDS)
