"""
Library tools that change many files at once, for Local files (a mixin of LocalService):

  retag      tag edits for many songs (retag.py's plans), shown first, then written through the tag editor, so
             each file's edit can be undone
  organize   files moved to where their tags say (organize.py's plan), with what goes with them (an .lrc of the
             same name; a folder's cover, .cue and the like when all its songs leave together), folders left empty
             removed, and every id that changes with a path carried across: plays, favourites, ratings, tag undo,
             kept lyrics (MusicDeck's playlists: by the caller, main.py). The last organize can be undone.

Track ids are their paths' hashes, so a moved file is a new id: `_carry` moves what's kept under the old one.
"""
import asyncio
import json
import os
import shutil
import time
from typing import Any, Optional

from datetime import datetime

from .. import convert, loudness, migrate, organize, retag, tagging
from .base import ServiceError

LIST = 300  # rows of a preview shown (the count is whole)
SIDECARS = (".lrc",)
# what a folder's songs bring along when they all move to one new folder
FOLDER_EXTRAS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".cue", ".log", ".txt", ".nfo", ".m3u", ".m3u8", ".pdf")


# kinds of file whose loudness can be written (tagging.write_replaygain)
GAIN_FORMATS = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac", ".wav", ".aif", ".aiff"}
MEASURING = 2  # songs measured at once


def _when(text: Any) -> Optional[float]:
    """A plist date ("2024-03-01T20:15:00Z") as seconds since 1970."""
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").timestamp()
    except ValueError:
        return None


COLUMN_SONGS = 3000  # songs a column browser's choice lists at most


class LibraryTools:
    # --- converting songs to another format (convert.py) ---

    def convert_songs(self, ids: list[str], fmt: str, root: str) -> int:
        """Starts converting these songs into `root` (in the background, two at a time; the status's "convert" says how
        far, and a toast when done). Returns how many."""
        if fmt not in convert.FORMATS:
            raise ServiceError("Unknown format")
        if not convert.available():
            raise ServiceError("Converting needs ffmpeg, which isn't installed")
        songs = [(tid, self.tracks[tid]) for tid in dict.fromkeys(ids) if isinstance(tid, str) and tid in self.tracks]
        if not songs:
            raise ServiceError("No songs to convert")
        if self.convert_task and not self.convert_task.done():
            raise ServiceError("Songs are being converted already: wait for those first")
        self.convert_task = asyncio.create_task(self._convert(songs, fmt, root))
        return len(songs)

    async def _convert(self, songs: list[tuple[str, dict[str, Any]]], fmt: str, root: str) -> None:
        progress = self.convert_progress = {"running": True, "done": 0, "total": len(songs), "failed": 0, "folder": root,
                                            "error": ""}
        gate = asyncio.Semaphore(MEASURING)

        async def one(t: dict[str, Any]) -> None:
            async with gate:
                why = await asyncio.to_thread(convert.convert, t, fmt, convert.target(root, fmt, t))
            progress["done"] += 1
            if why:
                progress["failed"] += 1
                progress["error"] = why

        try:
            await asyncio.gather(*(one(t) for _, t in songs))
            if self.announce:
                made = progress["total"] - progress["failed"]
                self.announce(f"Converted {made} {'song' if made == 1 else 'songs'} to {convert.FORMATS[fmt]['label']}"
                              + (f" ({progress['failed']} couldn't be: {progress['error']})" if progress["failed"] else ""))
        finally:
            progress["running"] = False

    # --- the column browser: genre, then artist, then album, each narrowing the next (iTunes's) ---

    async def columns(self, genre: str = "", artist: str = "", album: str = "") -> dict[str, Any]:
        """Each column's names with how many songs, given what's chosen to its left, and the songs of the choice:
        {"genres": [[name, n]], "artists": [[name, n]], "albums": [[album id, title, owner, n]], "tracks", "count"}.
        An empty choice is "All"."""
        from ..query import sort_name
        def artist_of(t: dict[str, Any]) -> str:
            return t.get("albumartist") or t.get("artist") or "Unknown artist"
        every = list(self.tracks.items())
        genres: dict[str, int] = {}
        for _, t in every:
            for g in t.get("genres") or ([t["genre"]] if t.get("genre") else []):
                genres[g] = genres.get(g, 0) + 1
        in_genre = [(tid, t) for tid, t in every if not genre or genre in (t.get("genres") or [t.get("genre", "")])]
        artists: dict[str, int] = {}
        for _, t in in_genre:
            artists[artist_of(t)] = artists.get(artist_of(t), 0) + 1
        by_artist = [(tid, t) for tid, t in in_genre if not artist or artist_of(t) == artist]
        albums: dict[str, list] = {}
        for tid, t in by_artist:
            key = self._album_key(t)
            if key not in albums:
                albums[key] = [key, t["album"] or os.path.basename(self._album_folder(t)), artist_of(t), 0, t.get("year") or ""]
            albums[key][3] += 1
        chosen = [(tid, t) for tid, t in by_artist if not album or self._album_key(t) == album]
        chosen.sort(key=lambda kv: (sort_name(artist_of(kv[1])), kv[1].get("year") or "", kv[1]["album"].lower(),
                                    kv[1].get("disc") or 0, kv[1].get("track") or 0))
        return {
            "genres": sorted(([g, n] for g, n in genres.items()), key=lambda x: x[0].lower()),
            "artists": sorted(([a, n] for a, n in artists.items()), key=lambda x: sort_name(x[0])),
            "albums": [a[:4] for a in sorted(albums.values(), key=lambda a: (sort_name(a[2]), a[4], a[1].lower()))],
            "tracks": [self._track(tid, t) for tid, t in chosen[:COLUMN_SONGS]],
            "count": len(chosen),
        }

    # --- moving in from iTunes or MusicBee (migrate.py) ---

    async def import_library(self, path: str) -> dict[str, Any]:
        """Play counts, last plays, skips, ratings and loved songs from an iTunes/MusicBee library file, for the songs
        that are here; and its plain playlists, as [(name, [tracks])], for the caller to make. Counts only go up; a
        rating already set here stays; ratings are kept by MusicDeck (the files aren't rewritten)."""
        try:
            library = await asyncio.to_thread(migrate.read_plist, path)
        except migrate.MigrateError as e:
            raise ServiceError(str(e))
        found, total = migrate.match(library, self.tracks)
        songs = {str(s.get("Track ID", k)): s for k, s in (library.get("Tracks") or {}).items() if isinstance(s, dict)}
        plays = rated = loved = 0
        for key, tid in found.items():
            song = songs.get(key) or {}
            had = self.plays.get(tid) or {}
            count = song.get("Play Count") if isinstance(song.get("Play Count"), int) else 0
            skips = song.get("Skip Count") if isinstance(song.get("Skip Count"), int) else 0
            if count > had.get("count", 0) or skips > had.get("skips", 0):
                self.plays[tid] = {**had, "count": max(count, had.get("count", 0)), "skips": max(skips, had.get("skips", 0)),
                                   "last": max(_when(song.get("Play Date UTC")) or 0, had.get("last", 0)) or had.get("last", 0)}
                plays += 1
            stars = 0 if song.get("Rating Computed") else migrate.stars(song.get("Rating"))
            if stars and not self._stars_of(tid, self.tracks[tid]):
                self.ratings[tid] = stars
                rated += 1
            if (song.get("Loved") or song.get("Favorited")) and tid not in self.favourites:
                self.favourites.add(tid)
                loved += 1
        self._carry({tid: tid for tid in found.values()})  # (saves favourites, plays, ratings)
        self._changed()
        lists = [(name, [self._track(tid, self.tracks[tid]) for tid in ids]) for name, ids in migrate.playlists(library, found)]
        return {"songs": total, "matched": len(found), "plays": plays, "ratings": rated, "loved": loved, "playlists": lists}

    def playlist_entries(self, track_ids: list[str]) -> list[tuple[str, str, float]]:
        """(path, "Artist - Title", seconds) for each of these songs still here, for an .m3u8."""
        out = []
        for tid in track_ids:
            t = self.tracks.get(tid)
            if t:
                out.append((t["path"], f"{t.get('artist') or t.get('albumartist') or ''} - {t.get('title') or ''}".strip(" -"),
                            t.get("duration") or 0))
        return out

    # --- loudness for songs without ReplayGain (loudness.py) ---

    def _without_gain(self) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        """Albums with a song lacking loudness tags that could hold them: the whole album is measured (its gain is
        its songs' together)."""
        return {key: items for key, items in self._albums().items()
                if any(not t.get("rg") and not t.get("cue") and os.path.splitext(t["path"])[1].lower() in GAIN_FORMATS
                       for _, t in items)}

    def gain_missing(self) -> int:
        return sum(1 for t in self.tracks.values()
                   if not t.get("rg") and not t.get("cue") and os.path.splitext(t["path"])[1].lower() in GAIN_FORMATS)

    def scan_loudness(self) -> None:
        """Measures, in the background, every album with songs lacking loudness tags (status()'s "loudness")."""
        if not loudness.available():
            raise ServiceError("Measuring loudness needs ffmpeg, which isn't installed")
        if self.loudness_task and not self.loudness_task.done():
            return
        self.loudness_task = asyncio.create_task(self._scan_loudness())

    async def _scan_loudness(self) -> None:
        albums = self._without_gain()
        progress = self.loudness_progress = {"running": True, "done": 0, "total": sum(len(v) for v in albums.values()), "written": 0}
        gate = asyncio.Semaphore(MEASURING)

        async def measure(path: str):
            async with gate:
                return await asyncio.to_thread(loudness.measure, path)

        try:
            for items in albums.values():
                items = [(tid, t) for tid, t in items if not t.get("cue")]
                results = await asyncio.gather(*(measure(t["path"]) for _, t in items))
                measured = [(tid, t, m) for (tid, t), m in zip(items, results) if m]
                progress["done"] += len(items)
                if not measured:
                    continue
                album_lufs, album_peak = loudness.album([(m[0], m[1], t.get("duration") or 0) for _, t, m in measured])
                async with self.edit_lock:
                    for tid, t, (lufs, peak) in measured:
                        if os.path.splitext(t["path"])[1].lower() not in GAIN_FORMATS:
                            continue
                        try:
                            await asyncio.to_thread(tagging.write_replaygain, t["path"], loudness.gain(lufs), peak,
                                                    loudness.gain(album_lufs), album_peak)
                        except tagging.TagError:
                            continue
                        await asyncio.to_thread(self._reread, tid)
                        progress["written"] += 1
            self._changed()
            await asyncio.to_thread(self._save_index)
            if self.announce and progress["written"]:
                self.announce(f"Volume levelling: loudness worked out for {progress['written']} songs")
        finally:
            progress["running"] = False

    # --- tag edits for many songs ---

    def _retag_plan(self, ids: list[str], op: str, args: dict[str, Any]) -> dict[str, dict[str, Any]]:
        # ["*"]: every song in the library
        ids = list(self.tracks) if ids == ["*"] else [tid for tid in dict.fromkeys(ids) if isinstance(tid, str) and tid in self.tracks]
        if not ids:
            raise ServiceError("No songs to change")
        try:
            if op == "from_names":
                return retag.from_names(self.tracks, ids, str(args.get("pattern") or ""))
            if op == "case":
                return retag.case(self.tracks, ids, "title" if args.get("style") != "sentence" else "sentence",
                                  [f for f in args.get("fields") or retag.CASE_FIELDS if isinstance(f, str)])
            if op == "renumber":
                return retag.renumber(self.tracks, ids)
            if op == "replace":
                return retag.replace(self.tracks, ids, str(args.get("field") or ""), str(args.get("find") or ""),
                                     str(args.get("to") or ""))
        except retag.PatternError as e:
            raise ServiceError(str(e))
        raise ServiceError("Unknown tag tool")

    async def retag_preview(self, ids: list[str], op: str, args: dict[str, Any]) -> dict[str, Any]:
        """What a tag tool would change: [{"id", "file", "fields": {field: [before, after]}}], and how many."""
        plan = self._retag_plan(ids, op, args)
        rows = [{"id": tid, "file": os.path.basename(self.tracks[tid]["path"]), "title": self.tracks[tid]["title"],
                 "fields": {f: [self.tracks[tid].get(f) or "", v] for f, v in changes.items()}}
                for tid, changes in list(plan.items())[:LIST]]
        return {"changes": rows, "count": len(plan)}

    async def retag_apply(self, ids: list[str], op: str, args: dict[str, Any]) -> dict[str, Any]:
        plan = self._retag_plan(ids, op, args)
        if not plan:
            raise ServiceError("Nothing to change")
        try:
            edits = [(tid, tagging.clean(changes)) for tid, changes in plan.items()]
        except tagging.TagError as e:
            raise ServiceError(str(e))
        return await self._write_tags(edits)

    # --- organizing files by their tags ---

    def _discs_of(self, t: dict[str, Any]) -> int:
        items = self._albums().get(self._album_key(t), [])
        return len({x.get("disc") or 1 for _, x in items})

    def _root_of(self, path: str) -> Optional[str]:
        for root in sorted(self.roots, key=len, reverse=True):
            if os.path.normcase(path).startswith(os.path.normcase(root.rstrip(os.sep) + os.sep)):
                return root
        return None

    def _organize_plan(self, pattern: str, ids: Optional[list[str]] = None) -> dict[str, Any]:
        """organize.plan for each library folder's own songs (a song stays under the folder it's in); only `ids`'
        when given (an album, a selection)."""
        by_root: dict[str, dict[str, dict[str, Any]]] = {}
        wanted = set(ids) if ids else None
        for tid, t in self.tracks.items():
            if (wanted is not None and tid not in wanted) or t.get("cue"):  # a CUE sheet's file is one album's: not moved
                continue
            root = self._root_of(t["path"])
            if root:
                by_root.setdefault(root, {})[tid] = t
        total: dict[str, Any] = {"moves": [], "same": 0, "clashes": [], "untagged": 0}
        try:
            for root, tracks in by_root.items():
                part = organize.plan(tracks, root, pattern, self._discs_of)
                total["moves"] += part["moves"]
                total["same"] += part["same"]
                total["clashes"] += part["clashes"]
                total["untagged"] += part["untagged"]
        except organize.PatternError as e:
            raise ServiceError(str(e))
        return total

    async def organize_preview(self, pattern: str, ids: Optional[list[str]] = None) -> dict[str, Any]:
        """Where songs would go: [{"id", "title", "from", "to"}] (inside their library folder), how many move, how
        many are already in place, and the songs that would clash (left where they are)."""
        plan = await asyncio.to_thread(self._organize_plan, pattern, ids)
        shown = lambda path: os.path.relpath(path, self._root_of(path) or os.path.dirname(path))  # noqa: E731
        return {
            "moves": [{"id": tid, "title": self.tracks[tid]["title"], "from": shown(src), "to": shown(to)}
                      for tid, src, to in plan["moves"][:LIST]],
            "count": len(plan["moves"]),
            "same": plan["same"],
            "clashes": [{"to": shown(to), "songs": [self.tracks[tid]["title"] for tid in ids]} for to, ids in plan["clashes"][:50]],
            "clash_count": len(plan["clashes"]),
            "untagged": plan["untagged"],
            "undo": os.path.exists(self._organize_undo_path),
        }

    @property
    def _organize_undo_path(self) -> str:
        return os.path.join(self.runtime_dir, f"local-{self.instance_id}-organize.json")

    async def organize_apply(self, pattern: str, ids: Optional[list[str]] = None) -> dict[str, Any]:
        """Moves the files; returns {"moved", "failed", "error", "ids": {old track id: new}}."""
        if self.scan_progress["scanning"]:
            raise ServiceError("The library is being scanned; try again when it's done")
        async with self.edit_lock:
            plan = await asyncio.to_thread(self._organize_plan, pattern, ids)
            if not plan["moves"]:
                raise ServiceError("Everything is where it belongs already")
            pairs, errors = await asyncio.to_thread(self._move_all, [(src, to) for _, src, to in plan["moves"]])
            ids = self._after_moves(pairs)
            with open(self._organize_undo_path, "w", encoding="utf-8") as f:
                json.dump({"at": time.time(), "moves": [[to, src] for src, to in pairs]}, f)
        return {"moved": len(ids), "failed": len(errors), "error": errors[0] if errors else "", "ids": ids}

    async def organize_undo(self) -> dict[str, Any]:
        """Puts the last organize's files back where they were."""
        try:
            with open(self._organize_undo_path, encoding="utf-8") as f:
                moves = json.load(f)["moves"]
        except (OSError, ValueError, KeyError):
            raise ServiceError("Nothing to undo")
        async with self.edit_lock:
            pairs, errors = await asyncio.to_thread(self._move_all, [(a, b) for a, b in moves if os.path.exists(a)], False)
            ids = self._after_moves(pairs)
            os.remove(self._organize_undo_path)
        return {"moved": len(pairs), "failed": len(errors), "error": errors[0] if errors else "", "ids": ids}

    def _move_all(self, moves: list[tuple[str, str]], extras: bool = True) -> tuple[list[tuple[str, str]], list[str]]:
        """Each file to its new place (and what goes with it); returns the moves made, and errors."""
        done: list[tuple[str, str]] = []
        errors: list[str] = []
        leaving: dict[str, set[str]] = {}
        for src, to in moves:
            try:
                self._move(src, to)
            except OSError as e:
                errors.append(f"{os.path.basename(src)}: {e.strerror or e}")
                continue
            done.append((src, to))
            leaving.setdefault(os.path.dirname(src), set()).add(os.path.dirname(to))
            for ext in SIDECARS:
                side = os.path.splitext(src)[0] + ext
                if os.path.exists(side):
                    side_to = os.path.splitext(to)[0] + ext
                    try:
                        self._move(side, side_to)
                        done.append((side, side_to))
                    except OSError:
                        pass
        for folder, destinations in leaving.items():
            if extras and len(destinations) == 1 and not self._has_music(folder):
                new = next(iter(destinations))
                for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
                    path = os.path.join(folder, name)
                    if os.path.isfile(path) and os.path.splitext(name)[1].lower() in FOLDER_EXTRAS \
                            and not os.path.exists(os.path.join(new, name)):
                        try:
                            self._move(path, os.path.join(new, name))
                            done.append((path, os.path.join(new, name)))
                        except OSError:
                            pass
            self._prune(folder)
        return done, errors

    @staticmethod
    def _move(src: str, to: str) -> None:
        if os.path.exists(to) and os.path.normcase(src) != os.path.normcase(to):
            raise OSError(17, "a file of that name is there already")
        os.makedirs(os.path.dirname(to), exist_ok=True)
        try:
            os.replace(src, to)
        except OSError:
            shutil.move(src, to)  # another drive: copied, then removed

    def _has_music(self, folder: str) -> bool:
        from .local import AUDIO_EXTENSIONS  # (local.py imports this module)
        try:
            return any(os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS for n in os.listdir(folder))
        except OSError:
            return False

    def _prune(self, folder: str) -> None:
        """The folder and those above it, while empty, up to (not including) a library folder."""
        roots = {os.path.normcase(r) for r in self.roots}
        while folder and os.path.normcase(folder) not in roots and self._root_of(folder):
            try:
                os.rmdir(folder)
            except OSError:
                return
            folder = os.path.dirname(folder)

    def _after_moves(self, pairs: list[tuple[str, str]]) -> dict[str, str]:
        """The index follows the files; what was kept under their old ids moves to the new ones."""
        from .local import _hash
        ids: dict[str, str] = {}
        for src, to in pairs:
            old = _hash(src)
            if old in self.tracks:
                new = _hash(to)
                self.tracks[new] = {**self.tracks.pop(old), "path": to}
                ids[old] = new
        self._carry(ids)
        self._changed()
        self._save_index()
        return ids

    def _carry(self, ids: dict[str, str]) -> None:
        """What's kept under old track ids moved to new ones, and the stores saved."""
        if not ids:
            return
        self.favourites = {ids.get(t, t) for t in self.favourites}
        self.plays = {ids.get(t, t): v for t, v in self.plays.items()}
        self.ratings = {ids.get(t, t): v for t, v in self.ratings.items()}
        self.history = {ids.get(t, t): v for t, v in self.history.items()}
        for old, new in ids.items():
            kept = os.path.join(self.lyrics_dir, old + ".lrc")
            if os.path.exists(kept):
                try:
                    os.replace(kept, os.path.join(self.lyrics_dir, new + ".lrc"))
                except OSError:
                    pass
        for path, value in ((self.favourites_path, sorted(self.favourites)), (self.plays_path, self.plays),
                            (self.ratings_path, self.ratings), (self.history_path, self.history)):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(value, f)
            os.replace(tmp, path)
