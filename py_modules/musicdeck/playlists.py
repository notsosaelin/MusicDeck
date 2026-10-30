"""
MusicDeck's own playlists, stored on the device (like NewPipe's local playlists).

They work for any source, including ones without accounts (SoundCloud, YouTube), and keep each
track's full metadata, so they open instantly and survive the source changing. A playlist mixes
sources: each track says which it's from ("source", an instance id; a paired device's songs
"peer~<device>~<its source>"); "instance_id" is the source it was made from, where it's listed.

They're the same on every paired device (peers.py): each device asks the others for theirs, and
the latest change to a playlist wins; a deleted one is remembered a while ("deleted"), so it
doesn't come back from a device that still has it.

A playlist can be marked for download ("offline"); the files themselves are kept by
offline.py per (instance, track id), so one file serves every playlist that contains the track.
"""
import json
import os
import secrets
import shutil
import tempfile
import re
import time
from typing import Any, Callable, Optional

from . import models
from .settings import load_json

PREFIX = "md:"  # collection ids for MusicDeck playlists, so they never collide with service ids
ART_PREFIX = "mdart:"  # art ids for custom playlist artwork, served from the art folder
ART_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_ART_BYTES = 15 * 1024 * 1024
#: what says where a song from a paired device is from: that device, its source there, the source's kind and name
ORIGIN_KEYS = ("dev", "inst", "type", "name")
#: a playlist's id (made here: 12 hex digits)
ID = re.compile(r"^[0-9a-f]{6,32}$")
#: how long a deleted playlist is remembered, so a paired device that still has it doesn't bring it back
DELETED_FOR = 90 * 24 * 3600


class PlaylistStore:
    def __init__(self, directory: str):
        self.path = os.path.join(directory, "playlists.json")
        self.art_dir = os.path.join(directory, "playlist-art")
        data = load_json(self.path)
        playlists = data.get("playlists")
        self.playlists: list[dict[str, Any]] = playlists if isinstance(playlists, list) else []
        # from before playlists mixed sources: each track is its playlist's source's
        for p in self.playlists:
            for t in p.get("tracks") or []:
                if isinstance(t, dict):
                    t.setdefault("source", p.get("instance_id", ""))
        deleted = data.get("deleted")
        self.deleted: dict[str, float] = (
            {k: v for k, v in deleted.items() if isinstance(v, (int, float))} if isinstance(deleted, dict) else {})

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "playlists": self.playlists, "deleted": self.deleted}, f)
        os.replace(tmp, self.path)

    @staticmethod
    def is_ours(collection_id: str) -> bool:
        return collection_id.startswith(PREFIX)

    def _find(self, collection_id: str) -> dict[str, Any]:
        p = self._get(collection_id)
        if p is None:
            raise KeyError(collection_id)
        return p

    def find(self, collection_id: str) -> Optional[dict[str, Any]]:
        """The stored playlist ({"id", "instance_id", "name", "tracks", ...}), or None."""
        return self._get(collection_id)

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

    def every(self) -> list[dict[str, Any]]:
        """Every playlist, the latest changed first, each saying its source ("source": where it was made)."""
        ours = sorted(self.playlists, key=lambda p: p.get("updated", 0), reverse=True)
        return [{**self._collection(p), "source": p["instance_id"]} for p in ours]

    def page(self, collection_id: str) -> dict[str, Any]:
        p = self._find(collection_id)
        c = self._collection(p)
        return models.page(p["name"], c["subtitle"], c["art"], tracks=list(p["tracks"]))

    def create(self, instance_id: str, name: str, tracks: Optional[list[dict[str, Any]]] = None,
               source: Optional[dict[str, str]] = None) -> dict[str, Any]:
        now = time.time()
        p = {"id": secrets.token_hex(6), "instance_id": instance_id, "name": name.strip() or "New playlist",
             "tracks": self._fresh([], tracks or [], instance_id), "created": now, "updated": now,
             # where a saved copy came from ({"kind", "id"}), and whether it's downloaded
             "source": source, "offline": False}
        self.playlists.append(p)
        self._save()
        return self._collection(p)

    def find_copy(self, instance_id: str, source: dict[str, str]) -> Optional[dict[str, Any]]:
        """The playlist saved from this source's album or playlist, if there is one."""
        return next((p for p in self.playlists if p["instance_id"] == instance_id and p.get("source") == source), None)

    @classmethod
    def _fresh(cls, have: list[dict[str, Any]], tracks: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
        """The tracks not in `have` yet, once each (anything without an id isn't a track), each saying its source
        (its own "source", else `source`); one from a paired device keeps where it's from there ("origin")."""
        seen = {(t.get("source"), t["id"]) for t in have}
        fresh = []
        for t in tracks:
            if not (isinstance(t, dict) and isinstance(t.get("id"), str) and t["id"]):
                continue
            own = t["source"] if isinstance(t.get("source"), str) and t["source"] else source
            if (own, t["id"]) in seen:
                continue
            seen.add((own, t["id"]))
            clean = {**models.clean_track(t), "source": own}
            if isinstance(t.get("origin"), dict):
                clean["origin"] = {k: v for k, v in t["origin"].items() if k in ORIGIN_KEYS and isinstance(v, str)}
            fresh.append(clean)
        return fresh

    def add(self, collection_id: str, tracks: list[dict[str, Any]], source: Optional[str] = None) -> int:
        """Appends tracks that aren't already in the playlist (from `source` when they don't say: the playlist's own
        by default); returns how many were added (0 when the playlist is gone)."""
        p = self._get(collection_id)
        if p is None:
            return 0
        fresh = self._fresh(p["tracks"], tracks, source or p["instance_id"])
        p["tracks"].extend(fresh)
        # a song of the original taken out of a saved copy and put back: no longer kept out
        if p.get("hidden"):
            back = {t["id"] for t in fresh if t["source"] == p["instance_id"]}
            p["hidden"] = [i for i in p["hidden"] if i not in back]
        p["updated"] = time.time()
        self._save()
        return len(fresh)

    def following(self) -> list[dict[str, Any]]:
        """The playlists saved from a source's playlist or album, which follow it (follow())."""
        return [p for p in self.playlists if isinstance(p.get("source"), dict)]

    def follow(self, collection_id: str, tracks: list[dict[str, Any]]) -> bool:
        """A saved copy brought up to date with its original's `tracks` (as the source has them now): the original's
        songs in its order, less those the user took out ("hidden"), then the songs the user added; "seen" is what the
        original had, so a song not in it is the user's. Whether its songs changed."""
        p = self._get(collection_id)
        if p is None or not isinstance(p.get("source"), dict):
            return False
        home = p["instance_id"]
        mine = [t for t in p["tracks"] if t.get("source") == home]
        # a copy saved before copies followed: every song of the source's in it was the original's
        seen = set(p["seen"]) if isinstance(p.get("seen"), list) else {t["id"] for t in mine}
        hidden = set(p.get("hidden") or [])
        original = self._fresh([], tracks, home)
        if not original and p["tracks"]:
            return False  # an empty answer from a source that had songs: kept as it is (a hiccup, or gone)
        kept = [t for t in original if t["id"] not in hidden]
        added = [t for t in p["tracks"] if not (t.get("source") == home and t["id"] in seen)]
        now = kept + self._fresh(kept, added, home)
        before = [(t.get("source"), t["id"]) for t in p["tracks"]]
        ids = [t["id"] for t in original]
        changed = before != [(t["source"], t["id"]) for t in now]
        if changed:
            p["tracks"] = now
            p["updated"] = time.time()
        if changed or p.get("seen") != ids:
            p["seen"] = ids
            p["hidden"] = [i for i in ids if i in hidden]
            self._save()
        return changed

    def retarget(self, instance_id: str, ids: dict[str, str]) -> int:
        """A source's songs known by new ids (Local files' files moved: an id is its path's): its playlists follow."""
        changed = 0
        for p in self.playlists:
            for t in p["tracks"]:
                if t.get("source") == instance_id and t["id"] in ids:
                    t["id"] = ids[t["id"]]
                    changed += 1
        if changed:
            self._save()
        return changed

    # remove, rename and clear_art do nothing for a playlist that's gone (deleted from another
    # screen, or by a page left over from a reload); the views refresh afterwards
    def remove(self, collection_id: str, track_id: str, source: Optional[str] = None) -> None:
        """Takes a track out (`source`'s, when given: two sources' songs can share an id)."""
        p = self._get(collection_id)
        if p is None:
            return
        gone = [t for t in p["tracks"] if t["id"] == track_id and (not source or t.get("source") == source)]
        p["tracks"] = [t for t in p["tracks"] if not any(t is g for g in gone)]
        # a song of the original a saved copy follows: it stays out when the original's songs are taken in again
        if isinstance(p.get("source"), dict) and any(g.get("source") == p["instance_id"] for g in gone):
            p["hidden"] = list(dict.fromkeys([*(p.get("hidden") or []), track_id]))
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

    def offline(self) -> list[dict[str, Any]]:
        """The playlists marked for download."""
        return [p for p in self.playlists if p.get("offline")]

    def is_offline(self, collection_id: str) -> bool:
        p = self._get(collection_id)
        return bool(p and p.get("offline"))

    def set_offline(self, collection_id: str, on: bool) -> bool:
        """Marks a playlist for download or not; False when it's gone."""
        p = self._get(collection_id)
        if p is None:
            return False
        p["offline"] = on
        self._save()
        return True

    def restore(self, p: dict[str, Any], art: Optional[tuple[str, bytes]]) -> bool:
        """Adds a playlist from a backup (checked by backup.valid_playlist) unless one with its id is
        here already; `art` is its picture as (extension, bytes). Returns whether it was added."""
        if self._get(p["id"]) is not None:
            return False
        restored = {"id": p["id"], "instance_id": p["instance_id"], "name": p["name"][:200] or "Playlist",
                    "tracks": self._fresh([], p["tracks"], p["instance_id"]), "created": p.get("created") or time.time(),
                    "updated": time.time(), "source": p.get("source") if isinstance(p.get("source"), dict) else None,
                    "offline": p.get("offline") is True}
        if art and art[0] in ART_TYPES and len(art[1]) <= MAX_ART_BYTES:
            os.makedirs(self.art_dir, exist_ok=True)
            with open(os.path.join(self.art_dir, p["id"] + art[0]), "wb") as f:
                f.write(art[1])
            restored["art_file"], restored["art_set"] = p["id"] + art[0], time.time()
        self.playlists.append(restored)
        self._save()
        return True

    def delete(self, collection_id: str) -> None:
        pid = collection_id[len(PREFIX):]
        p = next((x for x in self.playlists if x["id"] == pid), None)
        if p:
            self._remove_art_file(p)
            self.deleted[pid] = time.time()
        self.playlists = [p for p in self.playlists if p["id"] != pid]
        self._save()

    def delete_for_instance(self, instance_id: str) -> None:
        """A source removed: its songs leave every playlist; one made from it goes when nothing else is left in it,
        else it's listed under the source of its first song now."""
        kept = []
        for p in self.playlists:
            p["tracks"] = [t for t in p["tracks"] if t.get("source") != instance_id]
            if p["instance_id"] == instance_id:
                if not p["tracks"]:
                    self._remove_art_file(p)
                    continue
                p["instance_id"] = p["tracks"][0]["source"]
            kept.append(p)
        self.playlists = kept
        self._save()

    # --- the same on every paired device ---

    def shared(self, origin: Callable[[str], dict[str, str]]) -> dict[str, Any]:
        """What a paired device is given: every playlist, each track saying where it's from in terms another device
        can use (`origin` of its source: {"dev", "inst", "type", "name"}), and what was deleted lately."""
        now = time.time()
        self.deleted = {k: v for k, v in self.deleted.items() if now - v < DELETED_FOR}

        def track(t: dict[str, Any]) -> dict[str, Any]:
            out = {k: v for k, v in t.items() if k not in ("source", "origin")}
            # a song this device has from another one: where it's from there, as it was told
            peer = str(t.get("source", "")).startswith("peer~") and t.get("origin")
            out["origin"] = t["origin"] if peer else origin(t.get("source", ""))
            return out
        def follows(p: dict[str, Any]) -> dict[str, Any]:
            # a saved copy: what it follows and what the user changed of it, so another device keeps those changes
            if not isinstance(p.get("source"), dict):
                return {"follows": None}
            return {"follows": {"source": p["source"], "seen": p.get("seen"), "hidden": p.get("hidden") or []}}
        return {"playlists": [{"id": p["id"], "name": p["name"], "created": p.get("created", 0),
                               "updated": p.get("updated", 0), "home": origin(p["instance_id"]),
                               "tracks": [track(t) for t in p["tracks"]], **follows(p)} for p in self.playlists],
                "deleted": dict(self.deleted)}

    @staticmethod
    def _follows(f: Any, home: str) -> dict[str, Any]:
        """A paired device's "follows" (shared()) as this one keeps it: what a saved copy follows and the user's changes
        to it, when it's of a source here (its ids are the service's own on both); else nothing to change. A device that
        doesn't know a copy's original (one saved before devices told each other) never makes this one forget it."""
        ids = lambda v: [i for i in v if isinstance(i, str)][:20000] if isinstance(v, list) else None
        source = f.get("source") if isinstance(f, dict) else None
        if not (isinstance(source, dict) and isinstance(source.get("kind"), str) and isinstance(source.get("id"), str)
                and home and not home.startswith("peer~")):
            return {}
        return {"source": {"kind": source["kind"], "id": source["id"]}, "seen": ids(f.get("seen")),
                "hidden": ids(f.get("hidden")) or []}

    def take(self, theirs: Any, here: Callable[[dict[str, str]], str]) -> bool:
        """A paired device's playlists (as shared() gives them) into these: each one newer there replaces this one's
        (or is added), each deleted there since it last changed here goes. `here`: an origin as a source here.
        Whether anything changed."""
        if not isinstance(theirs, dict):
            return False
        changed = False
        deleted = theirs.get("deleted")
        for pid, when in (deleted.items() if isinstance(deleted, dict) else []):
            if not isinstance(pid, str) or not isinstance(when, (int, float)):
                continue
            p = self._get(pid)
            if p is not None and p.get("updated", 0) <= when:
                self._remove_art_file(p)
                self.playlists = [x for x in self.playlists if x is not p]
                changed = True
            if when > self.deleted.get(pid, 0):
                self.deleted[pid] = when
        for r in theirs.get("playlists") or []:
            if not (isinstance(r, dict) and isinstance(r.get("id"), str) and ID.match(r["id"])
                    and isinstance(r.get("name"), str) and isinstance(r.get("tracks"), list)):
                continue
            updated = r["updated"] if isinstance(r.get("updated"), (int, float)) else 0
            if self.deleted.get(r["id"], 0) >= updated:
                continue
            p = self._get(r["id"])
            if p is not None and p.get("updated", 0) >= updated:
                continue
            tracks = [{**t, "source": here(t["origin"])} for t in r["tracks"]
                      if isinstance(t, dict) and isinstance(t.get("origin"), dict)]
            fresh = self._fresh([], tracks, "")
            home = here(r["home"]) if isinstance(r.get("home"), dict) else ""
            if not home or home.startswith("peer~"):
                # made from a source only that device has: listed under the first of its songs' sources here
                home = next((t["source"] for t in fresh if not t["source"].startswith("peer~")), "") or                     (p["instance_id"] if p else "") or home
            if p is None:
                p = {"id": r["id"], "created": r.get("created") or updated, "source": None, "offline": False}
                self.playlists.append(p)
            p.update({"name": r["name"][:200] or "Playlist", "tracks": fresh, "updated": updated, "instance_id": home})
            p.update(self._follows(r.get("follows"), home))
            changed = True
        if changed:
            self._save()
        return changed
