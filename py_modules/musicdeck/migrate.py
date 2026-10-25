"""
Moving in from another player, and playlists out to them.

Import: iTunes' library file ("iTunes Library.xml" / "iTunes Music Library.xml", File > Library > Export Library),
which MusicBee also writes (Preferences > Library > "iTunes compatible XML"). Its songs are matched to Local files'
by path first (the same files, where they still are), then by artist, title and album (the files moved); each
match brings its play count, last play, skips, rating and love, and its plain playlists come as MusicDeck playlists
(smart playlists, folders and iTunes' own lists left out). Counts are only ever raised, never lowered.

Read with expat (Decky's Python has no plistlib on Linux): a plist is dicts of key/value, arrays, strings, integers,
dates and booleans, which is all this needs.

Export: MusicDeck playlists of Local files as .m3u8 files (absolute paths, #EXTINF lines), which every player opens.
"""
import os
import re
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from .health import norm

MAX_BYTES = 512 * 1024 * 1024
SKIPPED_PLAYLISTS = {"Library", "Music", "Movies", "TV Shows", "Podcasts", "Audiobooks", "Downloaded", "Purchased",
                     "Genius", "Recently Added", "Recently Played", "Top 25 Most Played", "90's Music", "Voice Memos"}


class MigrateError(ValueError):
    """Why a file couldn't be read, for the user."""


def read_plist(path: str) -> Any:
    """A plist XML file as Python values (dicts, lists, str, int, float, bool)."""
    from xml.parsers import expat  # here, not at the top: the plugin's start never depends on xml (test_backend)

    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise MigrateError(f"Couldn't open it: {e.strerror}")
    if size > MAX_BYTES:
        raise MigrateError("That file is too big to be a library")
    stack: list[Any] = []
    keys: list[Optional[str]] = []
    text: list[str] = []
    result: list[Any] = []

    def put(value: Any) -> None:
        if not stack:
            result.append(value)
        elif isinstance(stack[-1], list):
            stack[-1].append(value)
        else:
            stack[-1][keys[-1]] = value

    def start(tag, _attrs):
        text.clear()
        if tag == "dict":
            stack.append({})
            keys.append(None)
        elif tag == "array":
            stack.append([])
            keys.append(None)

    def end(tag):
        value = "".join(text)
        text.clear()
        if tag == "key":
            keys[-1] = value
        elif tag in ("dict", "array"):
            done = stack.pop()
            keys.pop()
            put(done)
        elif tag in ("string", "date", "data"):
            put(value)
        elif tag == "integer":
            put(int(value or 0))
        elif tag == "real":
            put(float(value or 0))
        elif tag in ("true", "false"):
            put(tag == "true")

    parser = expat.ParserCreate()
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = lambda chunk: text.append(chunk)
    # no entity tricks: a plist has none worth expanding
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        with open(path, "rb") as f:
            parser.ParseFile(f)
    except (expat.ExpatError, OSError) as e:
        raise MigrateError(f"That isn't a library file iTunes or MusicBee wrote ({e})")
    if not result or not isinstance(result[0], dict) or "Tracks" not in result[0]:
        raise MigrateError("That file has no songs in it: export the library from iTunes (File > Library > Export Library) "
                           "or MusicBee (Preferences > Library > iTunes compatible XML)")
    return result[0]


def location_path(location: str) -> str:
    """file://localhost/C:/Music/a%20b.mp3 -> C:/Music/a b.mp3; file:///home/me/x.flac -> /home/me/x.flac"""
    parsed = urlparse(location or "")
    if parsed.scheme != "file":
        return ""
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", path):  # Windows: drop the slash before the drive
        path = path[1:]
    return os.path.normpath(path)


def stars(rating: Any) -> int:
    """iTunes' 0-100 (20 a star) as 0-5."""
    return max(0, min(5, round(int(rating or 0) / 20))) if isinstance(rating, (int, float)) else 0


def match(library: dict[str, Any], tracks: dict[str, dict[str, Any]]) -> tuple[dict[str, str], int]:
    """{library track id: our track id}, and how many songs the library had."""
    by_path = {os.path.normcase(os.path.normpath(t["path"])): tid for tid, t in tracks.items()}
    by_name: dict[tuple, str] = {}
    for tid, t in tracks.items():
        by_name.setdefault((norm(t.get("artist") or t.get("albumartist") or ""), norm(t.get("title") or ""),
                            norm(t.get("album") or "")), tid)
    found: dict[str, str] = {}
    songs = library.get("Tracks") or {}
    for key, song in songs.items():
        if not isinstance(song, dict):
            continue
        path = location_path(song.get("Location", ""))
        tid = by_path.get(os.path.normcase(path)) if path else None
        if not tid:
            tid = by_name.get((norm(song.get("Artist") or song.get("Album Artist") or ""), norm(song.get("Name") or ""),
                               norm(song.get("Album") or "")))
        if tid:
            found[str(song.get("Track ID", key))] = tid
    return found, len(songs)


def playlists(library: dict[str, Any], found: dict[str, str]) -> list[tuple[str, list[str]]]:
    """The library's own plain playlists: [(name, [our track ids])], in order, empty ones left out."""
    out = []
    for p in library.get("Playlists") or []:
        if not isinstance(p, dict) or p.get("Master") or p.get("Distinguished Kind") or p.get("Smart Info") \
                or p.get("Folder") or p.get("Visible") is False or p.get("Name") in SKIPPED_PLAYLISTS:
            continue
        ids = [found[str(item.get("Track ID"))] for item in p.get("Playlist Items") or []
               if isinstance(item, dict) and str(item.get("Track ID")) in found]
        if ids:
            out.append((str(p.get("Name") or "Playlist")[:200], list(dict.fromkeys(ids))))
    return out


def m3u8(entries: list[tuple[str, str, float]]) -> str:
    """An extended M3U playlist of (path, "Artist - Title", seconds)."""
    lines = ["#EXTM3U"]
    for path, name, seconds in entries:
        lines += [f"#EXTINF:{round(seconds or -1)},{name}", path]
    return "\n".join(lines) + "\n"


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "Playlist"
