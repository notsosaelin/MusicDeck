"""
Backing up and restoring what MusicDeck keeps: preferences, sources, MusicDeck playlists with
their pictures, music set for games, and what's kept on the Deck per source (local and Steam
favourites, where podcast episodes were left, recent albums and searches). For moving to a new
Deck, or starting over.

A backup never holds sign-ins: tokens, passwords and keys (the plugin's SECRET_KEYS) are left out,
so a copied or shared backup file can't give anyone access to an account. Sources with accounts
come back with their address and user name, and say they need signing in again. Downloads aren't
included either: they download again.

Restoring adds what's missing and keeps what's there: a source or playlist already on this Deck
(same id) is left alone, so restoring twice changes nothing. Everything read from the file is
checked; a file that isn't a MusicDeck backup is refused whole.

The file: {"musicdeck_backup": 1, "created", "settings", "sources", "playlists",
           "playlist_art": {playlist id: {"ext", "data" (base64)}}, "files": {name: JSON}}
"""
import base64
import binascii
import json
import os
import re
from datetime import datetime
from typing import Any, Container, Iterable

FORMAT = 1
MAX_BYTES = 64 * 1024 * 1024
ID = re.compile(r"^[0-9a-f]{12}$")
# preferences that move with a backup (not the debug log, nor anything about this device)
PREFERENCES = ("qam_mode", "volume", "eq", "playback")
# per-source files in the data folder that hold the user's own data (indexes rebuild themselves)
SOURCE_FILES = ("local-{}-favourites.json", "steam-{}-favourites.json", "podcasts-{}-progress.json")
SHARED_FILES = ("recents.json",)
ART_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class BackupError(Exception):
    """A message for the user about why a backup couldn't be read."""


def _without(config: dict[str, Any], secret_keys: Iterable[str]) -> dict[str, Any]:
    return {k: v for k, v in config.items() if k not in secret_keys}


def build(settings: dict[str, Any], playlists: list[dict[str, Any]], art_dir: str, data_dir: str,
          secret_keys: Iterable[str]) -> dict[str, Any]:
    """Everything worth keeping, as one JSON-ready object, with no sign-ins in it."""
    secret_keys = set(secret_keys)
    sources = [{"id": s["id"], "type": s["type"], "name": s["name"], "config": _without(s["config"], secret_keys)}
               for s in settings.get("services") or []]
    art = {}
    for p in playlists:
        if p.get("art_file"):
            try:
                with open(os.path.join(art_dir, p["art_file"]), "rb") as f:
                    art[p["id"]] = {"ext": os.path.splitext(p["art_file"])[1].lower(),
                                    "data": base64.b64encode(f.read()).decode()}
            except OSError:
                pass
    files = {}
    names = [n.format(s["id"]) for s in sources for n in SOURCE_FILES] + list(SHARED_FILES)
    for name in names:
        try:
            with open(os.path.join(data_dir, name), encoding="utf-8") as f:
                files[name] = json.load(f)
        except (OSError, ValueError):
            pass
    return {
        "musicdeck_backup": FORMAT,
        "created": datetime.now().isoformat(timespec="seconds"),
        "settings": {**{k: settings.get(k) for k in PREFERENCES}, "active_service": settings.get("active_service"),
                     "game_music": settings.get("game_music") or [],
                     # the ListenBrainz account's name only; its token is a sign-in
                     "listenbrainz_user": (settings.get("listenbrainz") or {}).get("user", "")},
        "sources": sources,
        "playlists": [{k: v for k, v in p.items() if k != "art_file"} for p in playlists],
        "playlist_art": art,
        "files": files,
    }


def read(path: str) -> dict[str, Any]:
    """A backup file's contents, checked to be one. Raises BackupError."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise BackupError("That file is too large to be a MusicDeck backup")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise BackupError("That file isn't a MusicDeck backup") from e
    if not isinstance(data, dict) or data.get("musicdeck_backup") != FORMAT:
        raise BackupError("That file isn't a MusicDeck backup")
    for key, kind in (("settings", dict), ("sources", list), ("playlists", list), ("playlist_art", dict), ("files", dict)):
        if not isinstance(data.get(key, kind()), kind):
            raise BackupError("That backup is damaged")
    return data


def valid_source(s: Any, known_types: Container[str]) -> bool:
    return (isinstance(s, dict) and isinstance(s.get("id"), str) and ID.match(s["id"]) is not None
            and s.get("type") in known_types and isinstance(s.get("name"), str)
            and isinstance(s.get("config"), dict))


def valid_playlist(p: Any, source_ids: set[str]) -> bool:
    return (isinstance(p, dict) and isinstance(p.get("id"), str) and ID.match(p["id"]) is not None
            and p.get("instance_id") in source_ids and isinstance(p.get("name"), str)
            and isinstance(p.get("tracks"), list))  # tracks that aren't tracks are dropped on restore


def art_bytes(entry: Any) -> tuple[str, bytes] | None:
    """A playlist picture from the backup, as (extension, bytes), or None if it isn't a picture."""
    if not isinstance(entry, dict) or entry.get("ext") not in ART_TYPES or not isinstance(entry.get("data"), str):
        return None
    try:
        return entry["ext"], base64.b64decode(entry["data"], validate=True)
    except (binascii.Error, ValueError):
        return None


def count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def file_names(source_ids: Iterable[str]) -> set[str]:
    """The data files a backup may restore, for these sources."""
    return {n.format(i) for i in source_ids for n in SOURCE_FILES} | set(SHARED_FILES)
