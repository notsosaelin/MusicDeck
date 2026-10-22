from .bandcamp import BandcampService
from .base import MusicService, ServiceError, StreamSource
from .jellyfin import JellyfinService
from .local import LocalService
from .qobuz import QobuzService
from .radio import RadioService
from .soundcloud import SoundCloudService
from .spotify import SpotifyService
from .subsonic import SubsonicService
from .tidal import TidalService
from .youtube import YouTubeService

SERVICE_TYPES: dict[str, type[MusicService]] = {
    LocalService.type: LocalService,
    SubsonicService.type: SubsonicService,
    JellyfinService.type: JellyfinService,
    SoundCloudService.type: SoundCloudService,
    YouTubeService.type: YouTubeService,
    BandcampService.type: BandcampService,
    RadioService.type: RadioService,
    QobuzService.type: QobuzService,
    TidalService.type: TidalService,
    SpotifyService.type: SpotifyService,
}

# Shown in the UI as "coming soon" so the roadmap is visible without pretending they work.
PLANNED_TYPES: list[dict[str, str]] = []

__all__ = ["MusicService", "ServiceError", "StreamSource", "SERVICE_TYPES", "PLANNED_TYPES"]
