import json
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
    "playback": {"levelling": "track", "scrobble": True, "queue_sync": True, "resume": True, "autoplay": True},
}


class Settings:
    """Small JSON-backed settings store. Writes are atomic so a crash can't truncate the file."""

    def __init__(self, directory: str):
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, "settings.json")
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
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
