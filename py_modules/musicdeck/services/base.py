from dataclasses import dataclass, field
from typing import Any, Optional


class ServiceError(Exception):
    """An error whose message is safe to show the user."""


@dataclass
class StreamSource:
    """Where the stream server should get audio or art bytes from.

    Exactly one of `path` (a local file, served with Range support), `url` (a remote resource,
    proxied with Range passthrough) or `playlist` (HLS text, with `hls`) is set.
    """
    path: Optional[str] = None
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    content_type: Optional[str] = None
    # `url` is an HLS playlist; the server rewrites it and the frontend plays it with hls.js
    hls: bool = False
    # an HLS playlist built by the service itself (e.g. from a DASH manifest), used instead of `url`
    playlist: Optional[str] = None
    # epoch seconds after which `url` stops working (signed CDN links); cached until then
    expires: Optional[float] = None
    # ReplayGain-style loudness found while resolving (Spotify keeps it in the file header)
    gain: Optional[dict[str, float]] = None


# Optional capabilities a service can advertise; the UI only offers what's supported.
FEATURES = {
    "star",        # favourite / unfavourite tracks
    "scrobble",    # report now-playing and finished plays back to the source
    "lyrics",      # the source has its own lyrics (LRCLIB is used as a fallback for everyone)
    "radio",       # endless "more like this" from a track
    "queue_sync",  # save / restore the play queue on the server, to resume on another device
    "playlists",   # add tracks to the user's playlists
}


class MusicService:
    """One configured instance of a music source (a library folder, a Navidrome server, an account...)."""

    type: str = ""
    display_name: str = ""
    features: frozenset[str] = frozenset()

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        self.instance_id = instance_id
        self.config = config
        self.runtime_dir = runtime_dir

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Normalise a config submitted from the UI before it is saved, e.g. turn a password
        into a token so the plain password is never written to disk. `previous` is the stored
        config when editing an existing instance."""
        return config

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """Async counterpart of prepare_config for services that must sign in to turn a password into
        a token (the password is never stored). Raises ServiceError with a message for the user."""
        return config

    async def start(self) -> None:
        """Called once when the instance is created or its config changes."""

    async def stop(self) -> None:
        """Release sessions, cancel background work."""

    async def test(self) -> str:
        """Check the config works (credentials, reachability). Returns a short status message
        or raises ServiceError."""
        return "OK"

    def status(self) -> dict[str, Any]:
        """Extra state for the settings UI, e.g. scan progress."""
        return {}

    async def home(self) -> list[dict[str, Any]]:
        """Sections shown on the service's landing view."""
        raise NotImplementedError

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        """A Page for an album, artist, playlist or folder."""
        raise NotImplementedError

    async def search(self, query: str) -> dict[str, Any]:
        """A Page of results."""
        raise NotImplementedError

    async def stream(self, track_id: str) -> StreamSource:
        raise NotImplementedError

    async def art(self, art_id: str) -> StreamSource:
        raise NotImplementedError

    # --- optional features (see FEATURES) ---

    async def star(self, track_id: str, starred: bool) -> None:
        raise NotImplementedError

    async def scrobble(self, track_id: str, submission: bool) -> None:
        raise NotImplementedError

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Lyrics in the shape built by musicdeck.lyrics, or None if the source has none."""
        return None

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        """Tracks similar to `track`, for an endless queue."""
        raise NotImplementedError

    async def save_queue(self, track_ids: list[str], index: int, position: float) -> None:
        raise NotImplementedError

    async def load_queue(self) -> Optional[dict[str, Any]]:
        """{"tracks", "index", "position", "changed"} or None."""
        raise NotImplementedError

    async def user_playlists(self) -> list[dict[str, Any]]:
        """Playlists tracks can be added to (Collections)."""
        raise NotImplementedError

    async def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        raise NotImplementedError
