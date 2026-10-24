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
MTP_ROOT = re.compile(r"activation_root=(mtp://[^\s]+)")


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
        if not re.match(r"^/dev/sd[a-z]+\d*$", source):
            continue  # the SD card (mmcblk), internal drives (nvme), anything not plugged in by USB
        if not (target.startswith(f"/run/media/{user}/") or target.startswith("/media/")):
            continue
        label = os.path.basename(target.rstrip("/")) or source
        found.append({"id": _device_id("usb:" + uuids.get(source, label)), "name": label, "kind": "usb",
                      "paths": [target]})
    return found


def phone_storage(gvfs_dir: str, usb_names: Optional[dict[str, tuple[str, str]]] = None) -> list[dict[str, Any]]:
    """Phones mounted by GVfs: /run/user/<uid>/gvfs/mtp:host=<name>/<storage>/Music (and the others in
    PHONE_MUSIC); a phone whose storages have none of them is played whole."""
    found = []
    try:
        hosts = sorted(n for n in os.listdir(gvfs_dir) if n.startswith("mtp:host="))
    except OSError:
        return found
    for host in hosts:
        root = os.path.join(gvfs_dir, host)
        try:
            storages = [os.path.join(root, s) for s in sorted(os.listdir(root))]
        except OSError:
            continue  # still mounting, or the phone said no
        paths = [os.path.join(s, folder) for s in storages for folder in PHONE_MUSIC
                 if os.path.isdir(os.path.join(s, folder))]
        name = phone_name(host.partition("=")[2], usb_names or {})
        found.append({"id": _device_id("mtp:" + host), "name": name, "kind": "mtp", "paths": paths or storages})
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
        mounted = set(re.findall(r"-> (mtp://[^\s]+)", text))
        for root in set(MTP_ROOT.findall(text)) - mounted:
            debuglog.debug("devices", "mounting a phone", root=root)
            try:
                mount = await asyncio.create_subprocess_exec(self.gio, "mount", root, stdout=asyncio.subprocess.DEVNULL,
                                                             stderr=asyncio.subprocess.DEVNULL, env=self._gio_env())
                await asyncio.wait_for(mount.wait(), 20)
            except (OSError, asyncio.TimeoutError):
                pass

    def _scan(self) -> list[dict[str, Any]]:
        try:
            with open("/proc/self/mounts", "r", encoding="utf-8", errors="replace") as f:
                mounts = f.read()
        except OSError:
            mounts = ""
        return (usb_storage(mounts, self.user, _uuids())
                + phone_storage(f"/run/user/{self.uid}/gvfs", _usb_names())
                + audio_cds(cd.drives(), cd.has_audio_disc, cd.read_toc, self.discs))

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
