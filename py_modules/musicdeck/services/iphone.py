"""
An iPhone or iPod touch on Windows: the music Apple's sync put on it (iTunes_Control/Music), read over
AFC (ios.py) through Apple Mobile Device Service, into a mirror as a phone over MTP is (services/phone.py).
Read only, as on Linux: Apple's sync owns that folder (and its database), so nothing is copied to it.
Songs bought from Apple Music are protected, and don't play anywhere but on Apple's apps.
"""
import os
from typing import Any

from .. import ios
from .base import ServiceError
from .phone import PhoneService

#: the mirror's one "storage"
STORAGE = "iPhone"


class IphoneService(PhoneService):
    """config: {"kind": "ios", "udid", "device_id" (usbmux's number for it)}"""

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.udid = config.get("udid", "")
        self.device_id = int(config.get("device_id") or 0)

    def _afc(self) -> ios.Afc:
        record = ios.pair_record(self.udid)
        if not record:
            raise ServiceError("Unlock the iPhone and tap Trust (open Apple Devices or iTunes once, if it doesn't ask)")
        afc, _ = ios.Afc.open(self.device_id, record)
        return afc

    def _remote_list(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        afc = self._afc()
        try:
            space = afc.space()
            entries = [{"type": "file", "storage": STORAGE, "path": "iTunes_Control/Music/" + f["path"],
                        "id": f"{ios.MUSIC}/{f['path']}", "size": f["size"], "mtime": f["mtime"]}
                       for f in afc.walk(ios.MUSIC)]
        finally:
            afc.close()
        return [{"name": STORAGE, **space}], entries

    def _remote_fetch(self, wanted: list[tuple[str, str]]) -> set[str]:
        if not wanted:
            return set()
        got = set()
        afc = self._afc()
        try:
            for path, dest in wanted:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                try:
                    afc.read(path, dest)
                    got.add(path)
                except ios.IosError:
                    if os.path.exists(dest):
                        os.remove(dest)
        finally:
            afc.close()
        return got

    def _remote_put(self, storage: str, folder: str, path: str, name: str) -> str:
        raise ServiceError("An iPhone's music is Apple's sync's: MusicDeck plays it, and doesn't change it")

    def writable(self) -> bool:
        return False
