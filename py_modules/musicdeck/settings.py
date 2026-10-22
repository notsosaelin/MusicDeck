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
    "volume": 0.8,
    "debug_log": False,
    "eq": {"enabled": False, "preset": "Flat", "gains": [0.0] * 10, "preamp": 0.0},
    # levelling: "off" | "track" | "album" (ReplayGain); scrobble/queue_sync talk to the source's server
    # game_pause: pause while a game runs (src/games.ts); crossfade: seconds, 0 = off (src/player.ts)
    "playback": {"levelling": "track", "scrobble": True, "queue_sync": True, "resume": True, "autoplay": True,
                 "game_pause": False, "crossfade": 0},
    # ListenBrainz scrobbling for every source; the token never goes to the frontend
    "listenbrainz": {"token": "", "user": ""},
    # music that starts with a game: [{"instance_id", "appid", "game", "collection": {id, kind, title, art}}],
    # one per source and game (it only plays while its source is live)
    "game_music": [],
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
