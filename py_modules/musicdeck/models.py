"""
Plain-dict data model shared by every service, so the frontend renders one shape regardless of source.

Track:      {"id", "title", "artist", "album", "duration", "art",
             "album_id", "artist_id", "starred", "gain", "live"?}
            gain: {"track", "album", "track_peak", "album_peak"} in dB / linear peak, or None
            live: present (True) only for endless streams such as radio stations: no length, no seeking
Collection: {"id", "kind", "title", "subtitle", "art"}   kind: album | artist | playlist | folder | list | device
Page:       {"title", "subtitle", "art", "collections": [Collection], "tracks": [Track],
             "extra": [{"title", "tracks": [Track]}], "action", "notice"}
            extra = more titled track lists (e.g. "Videos")
            action = a button in the page header, or None. {"label", "url"}: steam:// URLs run in Steam
                     (e.g. "Install" for a soundtrack), others open in Steam's browser.
                     {"label", "call"}: asks the source to do something (collection_action, e.g.
                     subscribing to a podcast), then the page reloads.
            notice = a sentence shown above the content (why there's nothing to play yet), or None
            save = whether the page's album, artist or playlist is in the user's library on the service
                   (saved, followed, starred), for sources whose SAVES has its kind: {"on", "style"}, or None.
                   style: "library" (add to library; artists followed) or "favourite" (servers' favourites)
Section:    {"title", "items": [Collection], "tracks": [Track]}   a home row of collections and/or tracks

"art" is an opaque art id resolved by the service through the stream server (/art/...), or None.
Ids are opaque strings owned by the service that produced them.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def track(id: str, title: str, artist: str = "", album: str = "", duration: float = 0,
          art: Optional[str] = None, album_id: Optional[str] = None, artist_id: Optional[str] = None,
          starred: bool = False, gain: Optional[dict[str, float]] = None, live: bool = False) -> dict[str, Any]:
    t = {"id": id, "title": title, "artist": artist, "album": album, "duration": duration, "art": art,
         "album_id": album_id, "artist_id": artist_id, "starred": starred, "gain": gain}
    if live:
        t["live"] = True
    return t


def clean_track(track: dict[str, Any]) -> dict[str, Any]:
    """A track that came from the UI or a file (a playlist's, a like's, a backup's) as the model's fields
    only, each of its own kind; never UI-only state like "starred". Its "id" must already be checked."""
    def text(key: str, optional: bool = False) -> Optional[str]:
        value = track.get(key)
        return value[:500] if isinstance(value, str) else (None if optional else "")
    duration = track.get("duration")
    gain = track.get("gain")
    return {"id": track["id"], "title": text("title"), "artist": text("artist"), "album": text("album"),
            "duration": duration if isinstance(duration, (int, float)) and not isinstance(duration, bool) else 0,
            "art": text("art", True), "album_id": text("album_id", True), "artist_id": text("artist_id", True),
            "gain": {k: v for k, v in gain.items() if isinstance(k, str) and isinstance(v, (int, float))}
            if isinstance(gain, dict) else None}


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
         extra: Optional[list[dict[str, Any]]] = None, tracks_first: bool = False,
         action: Optional[dict[str, str]] = None, notice: Optional[str] = None,
         save: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    # tracks_first: show songs/videos above albums/artists/playlists (search results)
    return {"title": title, "subtitle": subtitle, "art": art,
            "collections": collections or [], "tracks": tracks or [],
            "extra": [e for e in extra or [] if e["tracks"]], "tracks_first": tracks_first,
            "action": action, "notice": notice, "save": save}


def saved(on: Optional[bool], style: str = "library") -> Optional[dict[str, Any]]:
    """A page's save state (Page.save): None when it isn't known (the service didn't say), so no
    button shows rather than a wrong one."""
    return None if on is None else {"on": bool(on), "style": style}


# How the media center shows a section (its tabs, see MusicService.tab):
#   "shelf": a row of cards, songs as a short list (the default, and how Home shows everything);
#   "rows": each item (a genre, mood, chart) becomes a row of its own music, loaded as it nears;
#   "tiles": a grid of names, for long lists of ways in (countries, years);
#   "grid": all its cards wrapping down the page, songs as a full list (all albums, all artists);
#   "covers": its tracks as a row of covers too, for tracks people pick by their picture (stations).
DISPLAYS = ("shelf", "rows", "tiles", "grid", "covers")


def section(title: str, items: list[dict[str, Any]], tracks: Optional[list[dict[str, Any]]] = None,
            display: str = "shelf", mine: bool = False) -> dict[str, Any]:
    """A titled group of collections and/or tracks. `mine`: the user's playlists, where the plugin
    adds the playlists made in MusicDeck."""
    s: dict[str, Any] = {"title": title, "items": items, "tracks": tracks or []}
    if display != "shelf":
        s["display"] = display
    if mine:
        s["mine"] = True
    return s


def notice(text: str) -> dict[str, Any]:
    """A note in place of a section, for a tab with nothing in it yet (how to fill it)."""
    return {"title": "", "items": [], "tracks": [], "notice": text}


def is_categories(s: dict[str, Any]) -> bool:
    """A section of ways in ("All albums", genres), not music to pick from."""
    return bool(s["items"]) and not s.get("tracks") and all(c["kind"] in ("list", "folder") for c in s["items"])


UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, fallback: str) -> str:
    """A name fit for a file or folder on any file system (a FAT32 player or stick's too)."""
    cleaned = UNSAFE_NAME.sub("_", name).strip().rstrip(".")
    return cleaned[:120] or fallback


def utc_date(seconds: Any) -> Optional[datetime]:
    """A Unix time as a date (UTC), before 1970 too: a 1959 album's release. Windows' gmtime and
    fromtimestamp refuse those ("[Errno 22] Invalid argument"), so this counts from 1970 instead. None
    for what isn't a time."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    try:
        return EPOCH + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return None


def count(n: int, noun: str) -> str:
    """"1 track", "3 tracks", "18,407 shows"."""
    return f"{n:,} {noun}{'' if n == 1 else 's'}"
