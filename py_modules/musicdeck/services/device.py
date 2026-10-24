"""
A plugged-in music player, iPod, USB stick, phone or data disc, shown in Local files' Devices while it's
there (found by musicdeck/devices.py). It's Local files on the device's folders, read only as far as
the files themselves go: MusicDeck doesn't edit its tags, set its artwork, look its albums up or write
lyrics beside its songs, as the files aren't the Deck's. Its index (kept between plugs, so a known
device is ready at once) and favourites stay on the Deck.

What a media manager shows of a device: how much room is left on it (`space`), whether music can be
copied to it (`writable`: a CD-ROM or a write-protected card can't), and ejecting it (`eject`).
"""
import asyncio
import os
import shutil
import time
from typing import Any, Optional

from .local import LocalService

#: free space is read again after this long (a phone's comes over USB)
SPACE_SECONDS = 30


class DeviceService(LocalService):
    """config: {"paths": [the device's music folders], "kind": "usb" | "mtp" | "disc" | "ipod", "source",
    "mount": where it's mounted (the paths are inside it), "uri": a phone's GVfs address}"""

    type = "device"
    display_name = "Plugged-in device"
    features = LocalService.features - {"tags", "artwork"}

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        # nothing gets looked up or written for what arrives on it
        return {**config, "auto_art": False, "auto_tags": "off", "auto_lyrics": False, "lyrics_place": "musicdeck"}

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, self.prepare_config(config, None), runtime_dir)
        self._space: tuple[float, Optional[dict[str, int]]] = (0.0, None)

    def space(self) -> Optional[dict[str, int]]:
        """{"total", "free"} bytes on it, or None when it won't say (some phones)."""
        at, known = self._space
        if time.monotonic() - at < SPACE_SECONDS:
            return known
        root = self.config.get("mount") or (self.roots[0] if self.roots else "")
        try:
            usage = shutil.disk_usage(root)
            known = {"total": usage.total, "free": usage.free} if usage.total else None
        except OSError:
            known = None
        self._space = (time.monotonic(), known)
        return known

    def writable(self) -> bool:
        if self.config.get("kind") == "disc":
            return False
        roots = self.roots
        return bool(roots) and all(os.access(r, os.W_OK) for r in roots if os.path.isdir(r))

    def status(self) -> dict[str, Any]:
        return {**super().status(), "space": self.space(), "writable": self.writable()}

    async def free_for(self, size: int) -> None:
        """Raises when `size` bytes won't fit on it."""
        from .base import ServiceError
        self._space = (0.0, None)
        room = await asyncio.to_thread(self.space)
        if room and room["free"] < size + 16 * 1024 * 1024:
            raise ServiceError(f"Not enough room: {_size(size)} to copy, {_size(room['free'])} free")


def _size(n: float) -> str:
    """"3.2 GB", "540 MB"."""
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB", "MB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""
