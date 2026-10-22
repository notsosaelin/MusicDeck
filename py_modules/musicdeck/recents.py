"""
What was played and searched lately, per source: the "Jump back in" row at the top of each source's
home, and the recent searches under an empty search field. Kept on the device in recents.json:
{"collections": {instance id: [collection, ...]}, "searches": {instance id: [query, ...]}}, newest
first. (The first version held only the collections, as {instance id: [...]}; that's read too.)
"""
import os
from typing import Any

from .settings import load_json, save_json

COLLECTIONS_LIMIT = 10
SEARCHES_LIMIT = 8
MAX_QUERY = 200
# what's kept of a collection: enough to show its row and open it again
KEYS = ("id", "kind", "title", "subtitle", "art")


def _lists(data: Any) -> dict[str, list]:
    return {k: v for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}


class Recents:
    def __init__(self, directory: str):
        self.path = os.path.join(directory, "recents.json")
        data = load_json(self.path)
        first_version = "collections" not in data and "searches" not in data
        self.collections = _lists(data if first_version else data.get("collections"))
        self.searches_by_instance = _lists(data.get("searches"))

    def _save(self) -> None:
        save_json(self.path, {"collections": self.collections, "searches": self.searches_by_instance})

    # --- albums and playlists ---

    def add(self, instance_id: str, collection: dict[str, Any]) -> None:
        """Puts a collection first (moving it there if it's already in the list)."""
        if not isinstance(collection, dict) or not collection.get("id") or not collection.get("kind"):
            return
        entry = {k: collection.get(k) for k in KEYS}
        others = [c for c in self.collections.get(instance_id, [])
                  if (c["kind"], c["id"]) != (entry["kind"], entry["id"])]
        self.collections[instance_id] = [entry, *others][:COLLECTIONS_LIMIT]
        self._save()

    def for_instance(self, instance_id: str) -> list[dict[str, Any]]:
        return list(self.collections.get(instance_id, []))

    # --- searches ---

    def add_search(self, instance_id: str, query: str) -> None:
        """Puts a search first; the same words differently capitalised count as the same search."""
        query = " ".join(query.split())[:MAX_QUERY] if isinstance(query, str) else ""
        if not query:
            return
        others = [q for q in self.searches_by_instance.get(instance_id, []) if q.lower() != query.lower()]
        self.searches_by_instance[instance_id] = [query, *others][:SEARCHES_LIMIT]
        self._save()

    def searches(self, instance_id: str) -> list[str]:
        return list(self.searches_by_instance.get(instance_id, []))

    def forget_search(self, instance_id: str, query: str = "") -> None:
        """Drops one search, or all of a source's ("")."""
        kept = [q for q in self.searches_by_instance.get(instance_id, []) if query and q != query]
        self.searches_by_instance[instance_id] = kept
        self._save()

    def forget(self, instance_id: str = "", collection_id: str = "") -> None:
        """Drops everything a source played and searched, or one collection (a deleted playlist)."""
        if instance_id:
            self.collections.pop(instance_id, None)
            self.searches_by_instance.pop(instance_id, None)
        if collection_id:
            self.collections = {k: [c for c in v if c["id"] != collection_id] for k, v in self.collections.items()}
        self._save()
