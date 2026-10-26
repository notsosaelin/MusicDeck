import json
import logging
import os
import secrets
import tempfile
from typing import Any

DEFAULTS: dict[str, Any] = {
    # Each service instance: {"id", "type", "name", "config": {...}}
    "services": [],
    "active_service": None,
    # "replace" takes over the Soundtracks tab; "separate" adds an extra QAM tab instead
    "qam_mode": "replace",
    # a toast when a new song starts (src/songToasts.ts): "off", "games" (while a game runs) or "always"
    "song_toasts": "games",
    "volume": 0.8,
    "debug_log": False,
    "eq": {"enabled": False, "preset": "Flat", "gains": [0.0] * 10, "preamp": 0.0},
    # each output (speakers, headphones, a Bluetooth device...) its own EQ, in output_eqs:
    # {output id: {"name", "eq"}}; outputs without one yet use "eq" (main.py, audio_output.py)
    "eq_per_output": False,
    "output_eqs": {},
    # levelling: "off" | "track" | "album" (ReplayGain); scrobble/queue_sync talk to the source's server
    # game_pause: pause while a game runs; game_duck: the share of its volume music keeps while a game
    # is in front, 1 = as is (src/games.ts); crossfade: seconds, 0 = off (src/player.ts); shuffle_mode: "songs",
    # "albums" (whole albums, in random order) or "smart" (the better-rated sooner, no artist twice running)
    "playback": {"levelling": "track", "scrobble": True, "queue_sync": True, "resume": True, "autoplay": True,
                 "game_pause": False, "game_duck": 1.0, "crossfade": 0, "shuffle_mode": "songs"},
    # ListenBrainz scrobbling for every source; the token never goes to the frontend
    "listenbrainz": {"token": "", "user": ""},
    # Last.fm scrobbling: the user's own API account (key, secret) and the session the sign-in gave;
    # only the user name goes to the frontend (lastfm.py)
    "lastfm": {"key": "", "secret": "", "user": "", "session_key": ""},
    # music that starts with a game: [{"instance_id", "appid", "game", "collection": {id, kind, title, art}}],
    # one per source and game (it only plays while its source is live)
    "game_music": [],
    # MusicDeck in the Steam library (a shortcut that opens the media center): on or off, and its app id
    "library_entry": {"enabled": False, "appid": 0},
    # names given to plugged-in devices, {device id: name}, kept for the next time they're plugged in
    "device_names": {},
    # the media center (src/components/media): the tab it opens on, its look, which tabs show, and
    # minutes idle on Now Playing before the screen goes off (0 = never)
    "center": {"open_on": "last", "background": "art", "cards": "medium", "lyrics": "medium",
               "show_library": True, "show_browse": True, "idle_off": 0},
}


def load_json(path: str) -> dict[str, Any]:
    """A JSON object from disk, or {} when there's none. A file that can't be read (cut short by a
    crash or a full disk) is moved aside as <name>.corrupt rather than overwritten by the next save,
    so what was in it can still be recovered by hand."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    os.replace(path, path + ".corrupt")
    logging.getLogger("musicdeck").error(f"{path} couldn't be read; moved to {path}.corrupt")
    return {}


def save_json(path: str, data: Any) -> None:
    """Writes JSON atomically (a temporary file, then a rename), so a crash can't leave half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


class Settings:
    """Small JSON-backed settings store. Writes are atomic so a crash can't truncate the file."""

    def __init__(self, directory: str):
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, "settings.json")
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        self.data = load_json(self.path)
        for key, value in DEFAULTS.items():
            self.data.setdefault(key, json.loads(json.dumps(value)))

    def save(self) -> None:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)
        # settings hold service credentials
        os.chmod(self.path, 0o600)

    def get(self, key: str) -> Any:
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    # --- service instances ---

    def services(self) -> list[dict[str, Any]]:
        return self.data["services"]

    def find_service(self, instance_id: str) -> dict[str, Any] | None:
        return next((s for s in self.services() if s["id"] == instance_id), None)

    def upsert_service(self, service: dict[str, Any]) -> dict[str, Any]:
        if not service.get("id"):
            service["id"] = secrets.token_hex(6)
        existing = self.find_service(service["id"])
        if existing:
            existing.update(service)
        else:
            self.services().append(service)
        if not self.data.get("active_service"):
            self.data["active_service"] = service["id"]
        self.save()
        return service

    def remove_service(self, instance_id: str) -> None:
        self.data["services"] = [s for s in self.services() if s["id"] != instance_id]
        if self.data.get("active_service") == instance_id:
            self.data["active_service"] = self.services()[0]["id"] if self.services() else None
        self.save()
