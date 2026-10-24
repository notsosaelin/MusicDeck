"""
A plugged-in music player, iPod, USB stick or phone, played as a source while it's there (found by
musicdeck/devices.py). It's Local files on the device's folders, read only: MusicDeck doesn't edit its
tags, set its artwork, look its albums up or write lyrics beside its songs, as the files aren't the
Deck's. Its index (kept between plugs, so a known device is ready at once) and favourites stay on the Deck.
"""
from typing import Any, Optional

from .local import LocalService


class DeviceService(LocalService):
    """config: {"paths": [the device's music folders], "kind": "usb" | "mtp"}"""

    type = "device"
    display_name = "Plugged-in device"
    features = LocalService.features - {"tags", "artwork"}

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        # nothing gets looked up or written for what arrives on it
        return {**config, "auto_art": False, "auto_tags": "off", "auto_lyrics": False, "lyrics_place": "musicdeck"}

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, self.prepare_config(config, None), runtime_dir)
