from .bandcamp import BandcampService
from .base import MusicService, ServiceError, SignIn, StreamSource
from .jellyfin import JellyfinService
from .local import LocalService
from .podcasts import PodcastService
from .qobuz import QobuzService
from .radio import RadioService
from .soundcloud import SoundCloudService
from .spotify import SpotifyService
from .steam import SteamService
from .subsonic import SubsonicService
from .tidal import TidalService
from .youtube import YouTubeService

SERVICE_TYPES: dict[str, type[MusicService]] = {
    LocalService.type: LocalService,
    SteamService.type: SteamService,
    SubsonicService.type: SubsonicService,
    JellyfinService.type: JellyfinService,
    SoundCloudService.type: SoundCloudService,
    YouTubeService.type: YouTubeService,
    BandcampService.type: BandcampService,
    RadioService.type: RadioService,
    PodcastService.type: PodcastService,
    QobuzService.type: QobuzService,
    TidalService.type: TidalService,
    SpotifyService.type: SpotifyService,
}

# Shown in the UI as "coming soon" so the roadmap is visible without pretending they work.
PLANNED_TYPES: list[dict[str, str]] = []

__all__ = ["MusicService", "ServiceError", "SignIn", "StreamSource", "SERVICE_TYPES", "PLANNED_TYPES"]
