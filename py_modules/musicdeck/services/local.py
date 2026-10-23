"""
Music files on the device. The folders chosen in the dialog are scanned recursively; tags, lengths,
ReplayGain and embedded art are read with mutagen and cached in an index (local-<id>.json), so a
rescan only re-reads files whose size or modification time changed. Files are served straight from
disk by the stream server. Favourites are kept in local-<id>-favourites.json; nothing leaves the
Deck except LRCLIB lyrics lookups.
"""
import asyncio
import hashlib
import json
import os
import random
import re
import time
from typing import Any, Optional

from .. import lyrics, models
from .base import MusicService, ServiceError, StreamSource

try:
    import mutagen
except ImportError:  # vendored into py_modules by scripts/vendor.py; degrade to filename-only metadata
    mutagen = None

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac", ".wav", ".webm"}
PLAYLIST_EXTENSIONS = {".m3u", ".m3u8"}
COVER_NAMES = ["cover", "folder", "front", "album", "albumart"]
COVER_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"]
INDEX_VERSION = 2  # bump when the stored tag fields change; forces a full rescan


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", "surrogateescape")).hexdigest()[:16]


# easy=True doesn't wrap ID3 tags embedded in WAV/AIFF/DSF, so those are read by frame id
ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "albumartist": "TPE2", "album": "TALB",
              "tracknumber": "TRCK", "discnumber": "TPOS", "date": "TDRC", "genre": "TCON"}


def _first(tags: Any, key: str) -> str:
    try:
        if hasattr(tags, "getall"):
            frame = tags.get(ID3_FRAMES[key])
            value = frame.text if frame is not None else None
        else:
            value = tags.get(key)
    except Exception:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value).strip() if value else ""


def _replaygain(tags: Any, key: str) -> Optional[float]:
    """Reads e.g. replaygain_track_gain ("-6.54 dB") / _peak ("0.98") from any tag format."""
    try:
        if hasattr(tags, "getall"):  # raw ID3 (WAV/AIFF): stored as TXXX frames
            frames = tags.getall(f"TXXX:{key.upper()}") or tags.getall(f"TXXX:{key}")
            value = frames[0].text[0] if frames else None
        else:
            value = next((tags.get(k) for k in (key, key.upper()) if tags.get(k)), None)
            if value is None and hasattr(tags, "keys"):  # MP4 freeform atoms
                atom = next((k for k in tags.keys() if k.lower().endswith(key)), None)
                value = tags.get(atom) if atom else None
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        return float(str(value).lower().replace("db", "").strip()) if value is not None else None
    except (ValueError, TypeError, IndexError):
        return None


def _number(value: str) -> int:
    # "3/12" -> 3
    try:
        return int(value.split("/")[0])
    except (ValueError, AttributeError):
        return 0


# "01 Intro.flac", "03 - Battle.wav": the track number of a file without tags
NUMBERED_NAME = re.compile(r"^(\d{1,3})(?:\s*[-._]\s*|\s+)(.+)$")


def read_tags(path: str) -> dict[str, Any]:
    stem = os.path.splitext(os.path.basename(path))[0]
    numbered = NUMBERED_NAME.match(stem)
    name = numbered.group(2) if numbered else stem
    info: dict[str, Any] = {"title": name, "artist": "", "albumartist": "", "album": "",
                            "track": int(numbered.group(1)) if numbered else 0, "disc": 0, "year": "", "genre": "",
                            "duration": 0.0, "rg": None}
    if mutagen is None:
        return info
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return info
    if audio is None:
        return info
    tags = audio.tags or {}
    info["title"] = _first(tags, "title") or name
    info["artist"] = _first(tags, "artist")
    info["albumartist"] = _first(tags, "albumartist")
    info["album"] = _first(tags, "album")
    info["track"] = _number(_first(tags, "tracknumber")) or info["track"]
    info["disc"] = _number(_first(tags, "discnumber"))
    info["year"] = _first(tags, "date")[:4]
    info["genre"] = _first(tags, "genre")
    info["duration"] = float(getattr(audio.info, "length", 0) or 0)
    info["rg"] = models.gain(_replaygain(tags, "replaygain_track_gain"), _replaygain(tags, "replaygain_album_gain"),
                             _replaygain(tags, "replaygain_track_peak"), _replaygain(tags, "replaygain_album_peak"))
    return info


LYRIC_TAGS = ("lyrics", "LYRICS", "unsyncedlyrics", "UNSYNCEDLYRICS", "\xa9lyr")


def read_lyrics(path: str) -> Optional[dict[str, Any]]:
    """A sidecar .lrc next to the file wins; otherwise embedded USLT / LYRICS / (c)lyr tags."""
    sidecar = os.path.splitext(path)[0] + ".lrc"
    if os.path.isfile(sidecar):
        with open(sidecar, "r", encoding="utf-8", errors="replace") as f:
            parsed = lyrics.parse_lrc(f.read(), "Lyrics file")
        if parsed:
            return parsed
    if mutagen is None:
        return None
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    text = None
    if hasattr(tags, "getall"):
        frames = tags.getall("USLT")
        text = frames[0].text if frames else None
    else:
        for key in LYRIC_TAGS:
            value = tags.get(key) if hasattr(tags, "get") else None
            if value:
                text = value[0] if isinstance(value, list) else value
                break
    return lyrics.parse_lrc(str(text), "Embedded") if text else None


def extract_embedded_art(path: str) -> Optional[tuple[bytes, str]]:
    """Returns (image bytes, mime type) of the first embedded picture, if any."""
    if mutagen is None:
        return None
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None
    # FLAC
    pictures = getattr(audio, "pictures", None)
    if pictures:
        return pictures[0].data, pictures[0].mime or "image/jpeg"
    tags = audio.tags
    if tags is None:
        return None
    # ID3 (mp3, some wav)
    if hasattr(tags, "getall"):
        frames = tags.getall("APIC")
        if frames:
            return frames[0].data, frames[0].mime or "image/jpeg"
    # MP4
    covr = tags.get("covr") if hasattr(tags, "get") else None
    if covr:
        from mutagen.mp4 import MP4Cover
        cover = covr[0]
        mime = "image/png" if getattr(cover, "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
        return bytes(cover), mime
    # Ogg Vorbis / Opus
    blocks = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
    if blocks:
        import base64
        from mutagen.flac import Picture
        picture = Picture(base64.b64decode(blocks[0]))
        return picture.data, picture.mime or "image/jpeg"
    return None


def find_folder_cover(directory: str) -> Optional[str]:
    try:
        entries = {e.lower(): e for e in os.listdir(directory)}
    except OSError:
        return None
    for name in COVER_NAMES:
        for ext in COVER_EXTENSIONS:
            if name + ext in entries:
                return os.path.join(directory, entries[name + ext])
    return None


class LocalService(MusicService):
    """Recursively scans one or more folders and indexes their tags.

    config: {"paths": ["/home/deck/Music", ...]}
    """

    type = "local"
    display_name = "Local files"
    features = frozenset({"star", "lyrics", "radio"})

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.index_path = os.path.join(runtime_dir, f"local-{instance_id}.json")
        self.art_cache = os.path.join(runtime_dir, "art-cache")
        self.tracks: dict[str, dict[str, Any]] = {}
        self.playlists: dict[str, dict[str, Any]] = {}
        # favourites live outside the index so rescans and index format bumps never lose them
        self.favourites_path = os.path.join(runtime_dir, f"local-{instance_id}-favourites.json")
        self.favourites: set[str] = set()
        self.folder_covers: dict[str, Optional[str]] = {}
        self.scan_task: Optional[asyncio.Task[None]] = None
        self.scan_progress = {"scanning": False, "found": 0, "last_scan": 0.0, "error": ""}

    @property
    def roots(self) -> list[str]:
        return [os.path.abspath(os.path.expanduser(p)) for p in self.config.get("paths", []) if p]

    async def start(self) -> None:
        await asyncio.to_thread(self._load_index)
        try:
            with open(self.favourites_path, "r", encoding="utf-8") as f:
                self.favourites = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self.favourites = set()
        self.rescan()

    async def stop(self) -> None:
        if self.scan_task:
            self.scan_task.cancel()

    def remove(self) -> None:
        for path in (self.index_path, self.favourites_path):
            try:
                os.remove(path)
            except OSError:
                pass

    async def test(self) -> str:
        if not self.roots:
            raise ServiceError("No music folder selected")
        missing = [r for r in self.roots if not os.path.isdir(r)]
        if missing:
            raise ServiceError(f"Folder not found: {missing[0]}")
        return f"{models.count(len(self.tracks), 'track')} indexed"

    def status(self) -> dict[str, Any]:
        return {**self.scan_progress, "tracks": len(self.tracks)}

    # --- scanning ---

    def rescan(self) -> None:
        if self.scan_task and not self.scan_task.done():
            return
        # set before the task runs so the UI sees "scanning" as soon as rescan() returns
        self.scan_progress.update(scanning=True, found=0, error="")
        self.scan_task = asyncio.get_event_loop().create_task(self._scan())

    async def _scan(self) -> None:
        try:
            await asyncio.to_thread(self._scan_blocking)
            self.scan_progress["last_scan"] = time.time()
        except Exception as e:
            self.scan_progress["error"] = str(e)
            raise
        finally:
            self.scan_progress["scanning"] = False

    def _scan_blocking(self) -> None:
        previous = {t["path"]: t for t in self.tracks.values()}
        tracks: dict[str, dict[str, Any]] = {}
        playlist_files: list[str] = []
        for root in self.roots:
            # followlinks=False avoids symlink loops; os.walk skips unreadable dirs silently
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for name in sorted(filenames):
                    ext = os.path.splitext(name)[1].lower()
                    path = os.path.join(directory, name)
                    if ext in PLAYLIST_EXTENSIONS:
                        playlist_files.append(path)
                        continue
                    if ext not in AUDIO_EXTENSIONS:
                        continue
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    old = previous.get(path)
                    if old and old["mtime"] == st.st_mtime and old["size"] == st.st_size:
                        record = old
                    else:
                        record = {"path": path, "mtime": st.st_mtime, "size": st.st_size, **read_tags(path)}
                    tracks[_hash(path)] = record
                    self.scan_progress["found"] = len(tracks)
        by_path = {t["path"]: tid for tid, t in tracks.items()}
        playlists = {}
        for path in playlist_files:
            entries = self._parse_playlist(path, by_path)
            if entries:
                playlists[_hash(path)] = {"path": path,
                                          "title": os.path.splitext(os.path.basename(path))[0],
                                          "tracks": entries}
        self.tracks = tracks
        self.playlists = playlists
        self.folder_covers = {}
        self._save_index()

    @staticmethod
    def _parse_playlist(path: str, by_path: dict[str, str]) -> list[str]:
        base = os.path.dirname(path)
        result = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("file://"):
                        from urllib.parse import unquote
                        line = unquote(line[7:])
                    full = os.path.normpath(line if os.path.isabs(line) else os.path.join(base, line))
                    if full in by_path:
                        result.append(by_path[full])
        except OSError:
            return []
        return result

    def _load_index(self) -> None:
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if data.get("version") != INDEX_VERSION or data.get("roots") != self.roots:
            return
        self.tracks = data.get("tracks", {})
        self.playlists = data.get("playlists", {})
        self.scan_progress["last_scan"] = data.get("scanned_at", 0.0)

    def _save_index(self) -> None:
        os.makedirs(self.runtime_dir, exist_ok=True)
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": INDEX_VERSION, "roots": self.roots, "scanned_at": time.time(),
                       "tracks": self.tracks, "playlists": self.playlists}, f)
        os.replace(tmp, self.index_path)

    # --- grouping ---

    @staticmethod
    def _album_artist(t: dict[str, Any]) -> str:
        return t["albumartist"] or t["artist"] or "Unknown artist"

    def _album_key(self, t: dict[str, Any]) -> str:
        if not t["album"]:
            # untagged files: treat each folder as an album
            return _hash("dir:" + os.path.dirname(t["path"]))
        return _hash(f"{self._album_artist(t).lower()}\0{t['album'].lower()}")

    def _albums(self) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        albums: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for tid, t in self.tracks.items():
            albums.setdefault(self._album_key(t), []).append((tid, t))
        for items in albums.values():
            items.sort(key=lambda it: (it[1]["disc"], it[1]["track"], it[1]["title"].lower()))
        return albums

    def _track(self, tid: str, t: dict[str, Any]) -> dict[str, Any]:
        return models.track(tid, t["title"], t["artist"] or t["albumartist"], t["album"], t["duration"],
                            art=f"t:{tid}", album_id=self._album_key(t),
                            artist_id=_hash(self._album_artist(t).lower()),
                            starred=tid in self.favourites, gain=t.get("rg"))

    def _album_collection(self, key: str, items: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        tid, t = items[0]
        title = t["album"] or os.path.basename(os.path.dirname(t["path"]))
        subtitle = self._album_artist(t) + (f" · {t['year']}" if t["year"] else "")
        return models.collection(key, "album", title, subtitle, art=f"t:{tid}")

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        if not self.tracks:
            return []
        albums = self._albums()
        recent = sorted(albums.items(), key=lambda kv: max(t["mtime"] for _, t in kv[1]), reverse=True)[:20]
        sections = []
        if self.playlists:
            sections.append(models.section("Playlists", [
                models.collection(pid, "playlist", p["title"], models.count(len(p["tracks"]), "track"))
                for pid, p in sorted(self.playlists.items(), key=lambda kv: kv[1]["title"].lower())]))
        sections.append(models.section("Recently added", [self._album_collection(k, v) for k, v in recent]))
        sections.append(models.section("Library", [
            models.collection("favourites", "list", "Favourite tracks", models.count(len(self.favourites), "track")),
            models.collection("albums", "list", "Albums", models.count(len(albums), "album")),
            models.collection("artists", "list", "Artists"),
            models.collection("", "folder", "Folders"),
            models.collection("tracks", "list", "All tracks", models.count(len(self.tracks), "track")),
        ]))
        return sections

    # --- media center ---

    TABS = (("home", "Home"), ("albums", "Albums"), ("artists", "Artists"), ("folders", "Folders"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            sections = [s for s in await self.home() if not models.is_categories(s)]
            favourites = self._open_list("favourites")["tracks"]
            return sections + ([models.section("Favourite tracks", [], favourites, display="grid")] if favourites else [])
        if tab_id in ("albums", "artists"):
            page = self._open_list(tab_id)
            return [models.section(page["title"], page["collections"], display="grid")]
        if tab_id == "folders":
            page = self._open_folder("")
            return [models.section(page["title"] if page["collections"] else "Folders", page["collections"],
                                   page["tracks"], display="grid")]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list":
            return self._open_list(id)
        if kind == "album":
            items = self._albums().get(id)
            if not items:
                raise ServiceError("Album not found")
            c = self._album_collection(id, items)
            return models.page(c["title"], c["subtitle"], c["art"],
                               tracks=[self._track(tid, t) for tid, t in items])
        if kind == "artist":
            albums = [(k, v) for k, v in self._albums().items() if _hash(self._album_artist(v[0][1]).lower()) == id]
            if not albums:
                raise ServiceError("Artist not found")
            albums.sort(key=lambda kv: (kv[1][0][1]["year"], kv[1][0][1]["album"].lower()))
            name = self._album_artist(albums[0][1][0][1])
            return models.page(name, models.count(len(albums), "album"),
                               collections=[self._album_collection(k, v) for k, v in albums],
                               tracks=[self._track(tid, t) for _, v in albums for tid, t in v])
        if kind == "playlist":
            playlist = self.playlists.get(id)
            if not playlist:
                raise ServiceError("Playlist not found")
            return models.page(playlist["title"], "Playlist", tracks=[
                self._track(tid, self.tracks[tid]) for tid in playlist["tracks"] if tid in self.tracks])
        if kind == "folder":
            return self._open_folder(id)
        raise ServiceError(f"Unknown collection kind: {kind}")

    def _open_list(self, id: str) -> dict[str, Any]:
        if id == "albums":
            albums = sorted(self._albums().items(), key=lambda kv: (kv[1][0][1]["album"] or "~").lower())
            return models.page("Albums", collections=[self._album_collection(k, v) for k, v in albums])
        if id == "artists":
            artists: dict[str, tuple[str, int]] = {}
            for items in self._albums().values():
                name = self._album_artist(items[0][1])
                key = _hash(name.lower())
                artists[key] = (name, artists.get(key, (name, 0))[1] + 1)
            return models.page("Artists", collections=[
                models.collection(k, "artist", name, models.count(n, "album"))
                for k, (name, n) in sorted(artists.items(), key=lambda kv: kv[1][0].lower())])
        if id == "favourites":
            favs = [(tid, self.tracks[tid]) for tid in self.favourites if tid in self.tracks]
            favs.sort(key=lambda kv: (kv[1]["artist"].lower(), kv[1]["title"].lower()))
            return models.page("Favourite tracks", models.count(len(favs), "track"),
                               tracks=[self._track(tid, t) for tid, t in favs])
        if id == "tracks":
            ordered = sorted(self.tracks.items(), key=lambda kv: (kv[1]["artist"].lower(), kv[1]["album"].lower(),
                                                                  kv[1]["disc"], kv[1]["track"]))
            return models.page("All tracks", tracks=[self._track(tid, t) for tid, t in ordered])
        raise ServiceError(f"Unknown list: {id}")

    def _open_folder(self, id: str) -> dict[str, Any]:
        # Folder ids are absolute paths; "" is the virtual top level listing every root.
        if id == "":
            if len(self.roots) == 1:
                return self._open_folder(self.roots[0])
            return models.page("Folders", collections=[
                models.collection(r, "folder", os.path.basename(r) or r, r) for r in self.roots])
        directory = os.path.abspath(id)
        if not any(directory == r or directory.startswith(r + os.sep) for r in self.roots):
            raise ServiceError("Folder is outside the library")
        prefix = directory + os.sep
        subdirs: set[str] = set()
        tracks = []
        for tid, t in self.tracks.items():
            if not t["path"].startswith(prefix):
                continue
            rest = t["path"][len(prefix):]
            if os.sep in rest:
                subdirs.add(rest.split(os.sep, 1)[0])
            else:
                tracks.append((tid, t))
        tracks.sort(key=lambda it: (it[1]["disc"], it[1]["track"], os.path.basename(it[1]["path"]).lower()))
        return models.page(os.path.basename(directory) or directory, directory,
                           collections=[models.collection(os.path.join(directory, d), "folder", d)
                                        for d in sorted(subdirs, key=str.lower)],
                           tracks=[self._track(tid, t) for tid, t in tracks])

    async def search(self, query: str) -> dict[str, Any]:
        q = query.strip().lower()
        if not q:
            return models.page("Search")
        tracks = [(tid, t) for tid, t in self.tracks.items()
                  if q in t["title"].lower() or q in t["artist"].lower() or q in t["album"].lower()]
        albums = [(k, v) for k, v in self._albums().items()
                  if q in (v[0][1]["album"] or "").lower() or q in self._album_artist(v[0][1]).lower()]
        return models.page(f"Results for “{query}”",
                           collections=[self._album_collection(k, v) for k, v in albums[:20]],
                           tracks=[self._track(tid, t) for tid, t in tracks[:100]])

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        t = self.tracks.get(track_id)
        if not t or not os.path.isfile(t["path"]):
            raise ServiceError("Track not found")
        return StreamSource(path=t["path"])

    async def art(self, art_id: str) -> StreamSource:
        kind, _, tid = art_id.partition(":")
        t = self.tracks.get(tid) if kind == "t" else None
        if not t:
            raise ServiceError("Art not found")
        cached = os.path.join(self.art_cache, tid)
        if os.path.isfile(cached + ".mime"):
            with open(cached + ".mime", "r") as f:
                return StreamSource(path=cached, content_type=f.read())
        directory = os.path.dirname(t["path"])
        if directory not in self.folder_covers:
            self.folder_covers[directory] = await asyncio.to_thread(find_folder_cover, directory)
        cover = self.folder_covers[directory]
        if cover:
            return StreamSource(path=cover)
        embedded = await asyncio.to_thread(extract_embedded_art, t["path"])
        if not embedded:
            raise ServiceError("No art")
        data, mime = embedded
        os.makedirs(self.art_cache, exist_ok=True)
        with open(cached, "wb") as f:
            f.write(data)
        with open(cached + ".mime", "w") as f:
            f.write(mime)
        return StreamSource(path=cached, content_type=mime)

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        if starred:
            self.favourites.add(track_id)
        else:
            self.favourites.discard(track_id)
        tmp = self.favourites_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(self.favourites), f)
        os.replace(tmp, self.favourites_path)

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        t = self.tracks.get(track["id"])
        return await asyncio.to_thread(read_lyrics, t["path"]) if t else None

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        """Same artist and same genre first, then the rest of the library, all shuffled."""
        seed = self.tracks.get(track["id"])
        if not seed:
            raise ServiceError("Track not found")
        artist, genre = self._album_artist(seed).lower(), (seed["genre"] or "").lower()
        close = [tid for tid, t in self.tracks.items() if tid != track["id"]
                 and (self._album_artist(t).lower() == artist or (genre and (t["genre"] or "").lower() == genre))]
        close_set = set(close)
        rest = [tid for tid in self.tracks if tid != track["id"] and tid not in close_set]
        random.shuffle(close)
        random.shuffle(rest)
        picked = (close[:35] + rest)[:50]
        return [self._track(tid, self.tracks[tid]) for tid in picked]
