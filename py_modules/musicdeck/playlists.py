"""
MusicDeck's own playlists, stored on the device (like NewPipe's local playlists).

They work for any source, including ones without accounts (SoundCloud, YouTube), and keep each
track's full metadata, so they open instantly and survive the source changing. Playlists are
per-source: a playlist belongs to one service instance and only holds that source's tracks.

Layout is intended to grow into offline playback: a playlist can be marked for download, and
downloaded files are tracked separately by (instance, track id), so one file serves every
playlist that contains the track.
"""
import json
import os
import secrets
import shutil
import tempfile
import time
from typing import Any, Optional

from . import models
from .settings import load_json

PREFIX = "md:"  # collection ids for MusicDeck playlists, so they never collide with service ids
ART_PREFIX = "mdart:"  # art ids for custom playlist artwork, served from the art folder
ART_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_ART_BYTES = 15 * 1024 * 1024


class PlaylistStore:
    def __init__(self, directory: str):
        self.path = os.path.join(directory, "playlists.json")
        self.art_dir = os.path.join(directory, "playlist-art")
        playlists = load_json(self.path).get("playlists")
        self.playlists: list[dict[str, Any]] = playlists if isinstance(playlists, list) else []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "playlists": self.playlists}, f)
        os.replace(tmp, self.path)

    @staticmethod
    def is_ours(collection_id: str) -> bool:
        return collection_id.startswith(PREFIX)

    def _find(self, collection_id: str) -> dict[str, Any]:
        p = self._get(collection_id)
        if p is None:
            raise KeyError(collection_id)
        return p

    def _get(self, collection_id: str) -> Optional[dict[str, Any]]:
        pid = collection_id[len(PREFIX):] if collection_id.startswith(PREFIX) else collection_id
        return next((p for p in self.playlists if p["id"] == pid), None)

    @staticmethod
    def _collection(p: dict[str, Any]) -> dict[str, Any]:
        if p.get("art_file"):
            # the timestamp makes the URL change when the picture does, so nothing shows a cached old one
            art = f"{ART_PREFIX}{p['id']}:{int(p.get('art_set', 0))}"
        else:
            art = next((t.get("art") for t in p["tracks"] if t.get("art")), None)
        return models.collection(PREFIX + p["id"], "playlist", p["name"], models.count(len(p["tracks"]), "track"), art)

    def for_instance(self, instance_id: str) -> list[dict[str, Any]]:
        ours = [p for p in self.playlists if p["instance_id"] == instance_id]
        ours.sort(key=lambda p: p.get("updated", 0), reverse=True)
        return [self._collection(p) for p in ours]

    def page(self, collection_id: str) -> dict[str, Any]:
        p = self._find(collection_id)
        c = self._collection(p)
        return models.page(p["name"], c["subtitle"], c["art"], tracks=list(p["tracks"]))

    def create(self, instance_id: str, name: str, tracks: Optional[list[dict[str, Any]]] = None,
               source: Optional[dict[str, str]] = None) -> dict[str, Any]:
        now = time.time()
        p = {"id": secrets.token_hex(6), "instance_id": instance_id, "name": name.strip() or "New playlist",
             "tracks": self._fresh([], tracks or []), "created": now, "updated": now,
             # where a saved copy came from ({"kind", "id"}), and the future offline flag
             "source": source, "offline": False}
        self.playlists.append(p)
        self._save()
        return self._collection(p)

    def find_copy(self, instance_id: str, source: dict[str, str]) -> Optional[dict[str, Any]]:
        """The playlist saved from this source's album or playlist, if there is one."""
        return next((p for p in self.playlists if p["instance_id"] == instance_id and p.get("source") == source), None)

    @staticmethod
    def _clean(track: dict[str, Any]) -> dict[str, Any]:
        # keep the model fields only; never persist UI-only state like "starred"
        keys = ("id", "title", "artist", "album", "duration", "art", "album_id", "artist_id", "gain")
        return {k: track.get(k) for k in keys}

    @classmethod
    def _fresh(cls, have: list[dict[str, Any]], tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The tracks not in `have` yet, once each (anything without an id isn't a track)."""
        seen = {t["id"] for t in have}
        fresh = []
        for t in tracks:
            if isinstance(t, dict) and t.get("id") and t["id"] not in seen:
                seen.add(t["id"])
                fresh.append(cls._clean(t))
        return fresh

    def add(self, collection_id: str, tracks: list[dict[str, Any]]) -> int:
        """Appends tracks that aren't already in the playlist; returns how many were added (0 when
        the playlist is gone)."""
        p = self._get(collection_id)
        if p is None:
            return 0
        fresh = self._fresh(p["tracks"], tracks)
        p["tracks"].extend(fresh)
        p["updated"] = time.time()
        self._save()
        return len(fresh)

    # remove, rename and clear_art do nothing for a playlist that's gone (deleted from another
    # screen, or by a page left over from a reload); the views refresh afterwards
    def remove(self, collection_id: str, track_id: str) -> None:
        p = self._get(collection_id)
        if p is None:
            return
        p["tracks"] = [t for t in p["tracks"] if t["id"] != track_id]
        p["updated"] = time.time()
        self._save()

    def rename(self, collection_id: str, name: str) -> None:
        p = self._get(collection_id)
        if p is None:
            return
        p["name"] = name.strip() or p["name"]
        p["updated"] = time.time()
        self._save()

    def set_art(self, collection_id: str, image_path: str) -> None:
        """Copies the picture into MusicDeck's own folder, so moving or deleting the original is fine."""
        p = self._get(collection_id)
        if p is None:
            raise ValueError("That playlist was deleted")
        ext = os.path.splitext(image_path)[1].lower()
        if ext not in ART_TYPES:
            raise ValueError("Pick a JPG, PNG, WebP or GIF image")
        if os.path.getsize(image_path) > MAX_ART_BYTES:
            raise ValueError("That image is too large (15 MB max)")
        os.makedirs(self.art_dir, exist_ok=True)
        self._remove_art_file(p)
        target = os.path.join(self.art_dir, p["id"] + ext)
        shutil.copyfile(image_path, target)
        p["art_file"] = os.path.basename(target)
        p["art_set"] = time.time()
        p["updated"] = time.time()
        self._save()

    def clear_art(self, collection_id: str) -> None:
        p = self._get(collection_id)
        if p is None:
            return
        self._remove_art_file(p)
        p.pop("art_file", None)
        p["updated"] = time.time()
        self._save()

    def _remove_art_file(self, p: dict[str, Any]) -> None:
        if p.get("art_file"):
            try:
                os.remove(os.path.join(self.art_dir, p["art_file"]))
            except FileNotFoundError:
                pass

    def art_path(self, art_id: str) -> Optional[str]:
        """Resolves an "mdart:<id>:<version>" art id to the stored picture."""
        if not art_id.startswith(ART_PREFIX):
            return None
        pid = art_id[len(ART_PREFIX):].split(":")[0]
        p = next((x for x in self.playlists if x["id"] == pid), None)
        if not p or not p.get("art_file"):
            return None
        path = os.path.join(self.art_dir, p["art_file"])
        return path if os.path.isfile(path) else None

    def delete(self, collection_id: str) -> None:
        pid = collection_id[len(PREFIX):]
        p = next((x for x in self.playlists if x["id"] == pid), None)
        if p:
            self._remove_art_file(p)
        self.playlists = [p for p in self.playlists if p["id"] != pid]
        self._save()

    def delete_for_instance(self, instance_id: str) -> None:
        for p in self.playlists:
            if p["instance_id"] == instance_id:
                self._remove_art_file(p)
        self.playlists = [p for p in self.playlists if p["instance_id"] != instance_id]
        self._save()
