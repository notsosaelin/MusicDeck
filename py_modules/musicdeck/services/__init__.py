"""
Every kind of source MusicDeck has. A source type's code is loaded the first time it's needed (a
source of that type is added, or its sign-in starts), not at start-up: most people use a few of
these, and the rest would only take memory.
"""
import importlib
from typing import Any, Optional

from .base import MusicService, ServiceError, SignIn, StreamSource

# (type, module, class, name shown in "Add a source", its group there: "yours" (files and your own
# servers), "streaming" (services), "radio" (stations, podcasts, live recordings))
CATALOGUE = [
    ("local", "local", "LocalService", "Local files", "yours"),
    ("steam", "steam", "SteamService", "Steam soundtracks", "yours"),
    ("subsonic", "subsonic", "SubsonicService", "Navidrome / Subsonic", "yours"),
    ("jellyfin", "jellyfin", "JellyfinService", "Jellyfin", "yours"),
    ("emby", "emby", "EmbyService", "Emby", "yours"),
    ("plex", "plex", "PlexService", "Plex", "yours"),
    ("audiobookshelf", "audiobookshelf", "AudiobookshelfService", "Audiobookshelf", "yours"),
    ("soulsync", "soulsync", "SoulSyncService", "SoulSync", "yours"),
    ("soundcloud", "soundcloud", "SoundCloudService", "SoundCloud", "streaming"),
    ("youtube", "youtube", "YouTubeService", "YouTube Music", "streaming"),
    ("bandcamp", "bandcamp", "BandcampService", "Bandcamp", "streaming"),
    ("radio", "radio", "RadioService", "Internet radio", "radio"),
    ("podcasts", "podcasts", "PodcastService", "Podcasts", "radio"),
    ("livemusic", "livemusic", "LiveMusicService", "Live Music Archive", "radio"),
    ("qobuz", "qobuz", "QobuzService", "Qobuz", "streaming"),
    ("tidal", "tidal", "TidalService", "TIDAL", "streaming"),
    ("spotify", "spotify", "SpotifyService", "Spotify", "streaming"),
    ("soulseek", "soulseek", "SoulseekService", "Soulseek", "sharing"),
]


class SourceTypes:
    """The source types by name, as a lookup that imports each one's module on first use.
    Tests can register their own (`types[name] = cls`) and take them away again."""

    def __init__(self, catalogue: list[tuple[str, str, str, str, str]]):
        self._where = {t: (module, cls) for t, module, cls, _, _ in catalogue}
        self._names = {t: name for t, _, _, name, _ in catalogue}
        self._groups = {t: group for t, _, _, _, group in catalogue}
        self._loaded: dict[str, type[MusicService]] = {}

    def get(self, type_: str, default: Any = None) -> Optional[type[MusicService]]:
        if type_ in self._loaded:
            return self._loaded[type_]
        if type_ not in self._where:
            return default
        module, cls = self._where[type_]
        loaded = getattr(importlib.import_module(f".{module}", __name__), cls)
        self._loaded[type_] = loaded
        return loaded

    def __contains__(self, type_: object) -> bool:
        return type_ in self._names

    def listing(self) -> list[dict[str, str]]:
        """[{"type", "display_name", "group"}] for "Add a source", without loading anything."""
        return [{"type": t, "display_name": name, "group": self._groups.get(t, "other")} for t, name in self._names.items()]

    def __setitem__(self, type_: str, cls: type[MusicService]) -> None:
        self._loaded[type_] = cls
        self._names[type_] = cls.display_name

    def update(self, classes: dict[str, type[MusicService]]) -> None:
        for type_, cls in classes.items():
            self[type_] = cls

    def pop(self, type_: str, default: Any = None) -> Any:
        self._names.pop(type_, None)
        self._where.pop(type_, None)
        return self._loaded.pop(type_, default)

    def __delitem__(self, type_: str) -> None:
        self.pop(type_)


SERVICE_TYPES = SourceTypes(CATALOGUE)

# Shown in the UI as "coming soon" so the roadmap is visible without pretending they work.
PLANNED_TYPES: list[dict[str, str]] = []

__all__ = ["MusicService", "ServiceError", "SignIn", "StreamSource", "SERVICE_TYPES", "PLANNED_TYPES"]
