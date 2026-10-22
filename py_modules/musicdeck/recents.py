"""
Albums and playlists played lately, per source: the "Jump back in" row at the top of each source's
home. Kept on the device in recents.json: {instance id: [collection, ...]}, newest first.
"""
import json
import os
import tempfile
from typing import Any

from .settings import load_json

LIMIT = 10
# what's kept of a collection: enough to show its row and open it again
KEYS = ("id", "kind", "title", "subtitle", "art")


class Recents:
    def __init__(self, directory: str):
        self.path = os.path.join(directory, "recents.json")
        self.data: dict[str, list[dict[str, Any]]] = {
            k: v for k, v in load_json(self.path).items() if isinstance(v, list)
        }

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def add(self, instance_id: str, collection: dict[str, Any]) -> None:
        """Puts a collection first (moving it there if it's already in the list)."""
        if not isinstance(collection, dict) or not collection.get("id") or not collection.get("kind"):
            return
        entry = {k: collection.get(k) for k in KEYS}
        others = [c for c in self.data.get(instance_id, [])
                  if (c["kind"], c["id"]) != (entry["kind"], entry["id"])]
        self.data[instance_id] = [entry, *others][:LIMIT]
        self._save()

    def for_instance(self, instance_id: str) -> list[dict[str, Any]]:
        return list(self.data.get(instance_id, []))

    def forget(self, instance_id: str = "", collection_id: str = "") -> None:
        """Drops a source's whole list, or one collection (a deleted playlist) wherever it appears."""
        if instance_id:
            self.data.pop(instance_id, None)
        if collection_id:
            self.data = {k: [c for c in v if c["id"] != collection_id] for k, v in self.data.items()}
        self._save()
