"""
Plain-dict data model shared by every service, so the frontend renders one shape regardless of source.

Track:      {"id", "title", "artist", "album", "duration", "art",
             "album_id", "artist_id", "starred", "gain", "live"?}
            gain: {"track", "album", "track_peak", "album_peak"} in dB / linear peak, or None
            live: present (True) only for endless streams such as radio stations: no length, no seeking
Collection: {"id", "kind", "title", "subtitle", "art"}   kind: album | artist | playlist | folder
Page:       {"title", "subtitle", "art", "collections": [Collection], "tracks": [Track],
             "extra": [{"title", "tracks": [Track]}]}   extra = more titled track lists (e.g. "Videos")
Section:    {"title", "items": [Collection], "tracks": [Track]}   a home row of collections and/or tracks

"art" is an opaque art id resolved by the service through the stream server (/art/...), or None.
Ids are opaque strings owned by the service that produced them.
"""
from typing import Any, Optional


def track(id: str, title: str, artist: str = "", album: str = "", duration: float = 0,
          art: Optional[str] = None, album_id: Optional[str] = None, artist_id: Optional[str] = None,
          starred: bool = False, gain: Optional[dict[str, float]] = None, live: bool = False) -> dict[str, Any]:
    t = {"id": id, "title": title, "artist": artist, "album": album, "duration": duration, "art": art,
         "album_id": album_id, "artist_id": artist_id, "starred": starred, "gain": gain}
    if live:
        t["live"] = True
    return t


def gain(track_db: Optional[float] = None, album_db: Optional[float] = None,
         track_peak: Optional[float] = None, album_peak: Optional[float] = None) -> Optional[dict[str, float]]:
    """ReplayGain values, or None when the source has none (so the player leaves volume untouched)."""
    if track_db is None and album_db is None:
        return None
    return {"track": track_db if track_db is not None else album_db,
            "album": album_db if album_db is not None else track_db,
            "track_peak": track_peak or 1.0, "album_peak": album_peak or track_peak or 1.0}


def collection(id: str, kind: str, title: str, subtitle: str = "",
               art: Optional[str] = None) -> dict[str, Any]:
    return {"id": id, "kind": kind, "title": title, "subtitle": subtitle, "art": art}


def page(title: str, subtitle: str = "", art: Optional[str] = None,
         collections: Optional[list[dict[str, Any]]] = None,
         tracks: Optional[list[dict[str, Any]]] = None,
         extra: Optional[list[dict[str, Any]]] = None, tracks_first: bool = False) -> dict[str, Any]:
    # tracks_first: show songs/videos above albums/artists/playlists (search results)
    return {"title": title, "subtitle": subtitle, "art": art,
            "collections": collections or [], "tracks": tracks or [],
            "extra": [e for e in extra or [] if e["tracks"]], "tracks_first": tracks_first}


def section(title: str, items: list[dict[str, Any]], tracks: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    return {"title": title, "items": items, "tracks": tracks or []}


def count(n: int, noun: str) -> str:
    """"1 track", "3 tracks"."""
    return f"{n} {noun}{'' if n == 1 else 's'}"
