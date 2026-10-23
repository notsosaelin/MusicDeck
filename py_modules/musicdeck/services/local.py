"""
Music files on the device. The folders chosen in the dialog are scanned recursively; tags (composer,
genres, year, rating, label, compilation), lengths, quality (format, bit depth, sample rate, bitrate),
ReplayGain and embedded art are read with mutagen and cached in an index (local-<id>.json), so a
rescan only re-reads files whose size or modification time changed. Files are served straight from
disk by the stream server. Favourites (local-<id>-favourites.json) and play counts
(local-<id>-plays.json) are kept on the Deck; nothing leaves it except LRCLIB lyrics lookups.
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
    from mutagen.easyid3 import EasyID3

    def _popm(id3: Any, key: str) -> list[str]:
        frames = id3.getall("POPM")
        return [f"{frames[0].rating}:popm"] if frames else []

    # EasyID3 has no rating: POPM (where players keep star ratings) as "rating", marked as 0-255
    EasyID3.RegisterKey("rating", _popm)
except ImportError:  # vendored into py_modules by scripts/vendor.py; degrade to filename-only metadata
    mutagen = None

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac", ".wav", ".webm"}
PLAYLIST_EXTENSIONS = {".m3u", ".m3u8"}
COVER_NAMES = ["cover", "folder", "front", "album", "albumart"]
COVER_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"]
INDEX_VERSION = 3  # bump when the stored tag fields change; forces a full rescan


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", "surrogateescape")).hexdigest()[:16]


# easy=True doesn't wrap ID3 tags embedded in WAV/AIFF/DSF, so those are read by frame id
ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "albumartist": "TPE2", "album": "TALB",
              "tracknumber": "TRCK", "discnumber": "TPOS", "date": "TDRC", "genre": "TCON",
              "composer": "TCOM", "organization": "TPUB", "originaldate": "TDOR", "compilation": "TCMP"}
# several genres in one tag: "Rock; Pop", "Rock/Pop", or one value each
GENRE_SPLIT = re.compile(r"\s*[;/|\x00]\s*")
# folders of one album's discs ("CD1", "Disc 2"): the album is the folder above
DISC_FOLDER = re.compile(r"^(cd|dis[ck])\s*\d+$", re.IGNORECASE)
# what each kind of file is called, by extension (an .m4a says itself whether it's ALAC or AAC)
FORMATS = {".mp3": "MP3", ".flac": "FLAC", ".ogg": "Ogg Vorbis", ".oga": "Ogg Vorbis", ".opus": "Opus",
           ".m4a": "AAC", ".mp4": "AAC", ".aac": "AAC", ".wav": "WAV", ".webm": "WebM"}
LOSSLESS = {"FLAC", "ALAC", "WAV"}
VARIOUS = "Various artists"
SHELF = 20  # tracks or albums in a Home row
LIST_MAX = 100  # tracks in a played or rated list


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


def _all(tags: Any, key: str) -> list[str]:
    """Every value of a tag (genres come several to a file)."""
    try:
        if hasattr(tags, "getall"):
            frame = tags.get(ID3_FRAMES[key])
            values = list(frame.text) if frame is not None else []
        else:
            values = tags.get(key) or []
    except Exception:
        return []
    if not isinstance(values, list):
        values = [values]
    return [str(v).strip() for v in values if str(v).strip()]


def _genres(tags: Any) -> list[str]:
    seen: dict[str, str] = {}
    for value in _all(tags, "genre"):
        for genre in GENRE_SPLIT.split(value):
            # "rock" and "Rock" are one genre, named as it first came
            if genre and genre.lower() not in seen:
                seen[genre.lower()] = genre
    return list(seen.values())


def _stars(value: Any, scale: Optional[float] = None) -> int:
    """A rating as 0-5 stars: ID3's POPM 0-255 (scale 255), FMPS 0-1, a RATING of 0-100 or 1-5."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    if scale == 255:
        # how players write POPM: 1, 64, 128, 196, 255 for one to five stars
        return 1 if number < 64 else 2 if number < 128 else 3 if number < 196 else 4 if number < 255 else 5
    if number <= 1:
        return max(1, round(number * 5))
    if number <= 5:
        return round(number)
    return max(1, min(5, round(number / 20)))


def _rating(tags: Any) -> int:
    try:
        if hasattr(tags, "getall"):  # raw ID3 (WAV/AIFF)
            frames = tags.getall("POPM")
            return _stars(frames[0].rating, 255) if frames else 0
        value = _first(tags, "rating")
        if value:
            # EasyID3 hands POPM over through the "rating" key registered above, marked ":popm"
            return _stars(value.split(":")[0], 255 if value.endswith(":popm") else None)
        return _stars(_first(tags, "fmps_rating"))
    except Exception:
        return 0


def _quality(audio: Any, path: str) -> dict[str, Any]:
    """The file's format and how it's encoded: {"format", "bits", "rate", "bitrate", "channels"}."""
    info = getattr(audio, "info", None)
    ext = os.path.splitext(path)[1].lower()
    name = FORMATS.get(ext, ext[1:].upper())
    if str(getattr(info, "codec", "") or "").lower() == "alac":
        name = "ALAC"
    return {"format": name, "bits": int(getattr(info, "bits_per_sample", 0) or 0),
            "rate": int(getattr(info, "sample_rate", 0) or 0),
            "bitrate": int((getattr(info, "bitrate", 0) or 0) / 1000),
            "channels": int(getattr(info, "channels", 0) or 0)}


def _quality_line(t: dict[str, Any], short: bool = False) -> str:
    """"FLAC · 24-bit · 96 kHz", "MP3 · 320 kbps"; short: just "FLAC 24/96" style, for an album's subtitle."""
    name = t.get("format", "")
    rate = t.get("rate", 0)
    if name in LOSSLESS:
        if short:
            hi_res = t.get("bits", 0) > 16 or rate > 48000
            return f"{name} {t['bits']}/{rate / 1000:g}" if hi_res and t.get("bits") and rate else name
        parts = [name, f"{t['bits']}-bit" if t.get("bits") else "", f"{rate / 1000:g} kHz" if rate else ""]
    else:
        if short:
            return name
        parts = [name, f"{t['bitrate']} kbps" if t.get("bitrate") else "", f"{rate / 1000:g} kHz" if rate else ""]
    return " · ".join(x for x in parts if x)


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
                            "genres": [], "composer": "", "label": "", "compilation": False, "rating": 0,
                            "duration": 0.0, "rg": None, **_quality(None, path)}
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
    # the year it first came out, over a reissue's
    info["year"] = (_first(tags, "originaldate") or _first(tags, "date"))[:4]
    info["genres"] = _genres(tags)
    info["genre"] = info["genres"][0] if info["genres"] else ""
    info["composer"] = _first(tags, "composer")
    info["label"] = _first(tags, "organization") or _first(tags, "label")
    info["compilation"] = _first(tags, "compilation").lower() in ("1", "true", "yes")
    info["rating"] = _rating(tags)
    info["duration"] = float(getattr(audio.info, "length", 0) or 0)
    info.update(_quality(audio, path))
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
    # scrobble: plays counted on the Deck (Most played, Recently played), when "Report plays" is on
    features = frozenset({"star", "lyrics", "radio", "scrobble"})

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.index_path = os.path.join(runtime_dir, f"local-{instance_id}.json")
        self.art_cache = os.path.join(runtime_dir, "art-cache")
        self.tracks: dict[str, dict[str, Any]] = {}
        self.playlists: dict[str, dict[str, Any]] = {}
        # favourites live outside the index so rescans and index format bumps never lose them
        self.favourites_path = os.path.join(runtime_dir, f"local-{instance_id}-favourites.json")
        self.favourites: set[str] = set()
        # {track id: {"count", "last"}}: kept apart from the index, as favourites are
        self.plays_path = os.path.join(runtime_dir, f"local-{instance_id}-plays.json")
        self.plays: dict[str, dict[str, Any]] = {}
        # the tracks grouped into albums, made again when the tracks change
        self._albums_cache: Optional[dict[str, list[tuple[str, dict[str, Any]]]]] = None
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
        try:
            with open(self.plays_path, "r", encoding="utf-8") as f:
                plays = json.load(f)
            self.plays = {k: v for k, v in plays.items() if isinstance(v, dict)} if isinstance(plays, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            self.plays = {}
        self.rescan()

    async def stop(self) -> None:
        if self.scan_task:
            self.scan_task.cancel()

    def remove(self) -> None:
        for path in (self.index_path, self.favourites_path, self.plays_path):
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
                        # "added": when it came into the library; its tags edited since don't make it new
                        added = old.get("added", old["mtime"]) if old else st.st_mtime
                        record = {"path": path, "mtime": st.st_mtime, "size": st.st_size, "added": added,
                                  **read_tags(path)}
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
        self._albums_cache = None
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
        self._albums_cache = None
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

    @staticmethod
    def _album_folder(t: dict[str, Any]) -> str:
        """The folder an album's files are in: the one above "CD1", "Disc 2"."""
        directory = os.path.dirname(t["path"])
        return os.path.dirname(directory) if DISC_FOLDER.match(os.path.basename(directory)) else directory

    def _album_key(self, t: dict[str, Any]) -> str:
        if not t["album"]:
            # untagged files: treat each folder as an album
            return _hash("dir:" + self._album_folder(t))
        if not t["albumartist"]:
            # no album artist (a compilation, often): its folder keeps it one album, not one per artist
            return _hash(f"{t['album'].lower()}\0{self._album_folder(t)}")
        return _hash(f"{t['albumartist'].lower()}\0{t['album'].lower()}")

    @classmethod
    def _album_owner(cls, items: list[tuple[str, dict[str, Any]]]) -> str:
        """Whose album it is: its album artist, its one artist, or "Various artists"."""
        first = items[0][1]
        if first["albumartist"]:
            return first["albumartist"]
        artists = {t["artist"].lower() for _, t in items if t["artist"]}
        if first.get("compilation") or len(artists) > 1:
            return VARIOUS
        return cls._album_artist(first)

    def _albums(self) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        if self._albums_cache is not None:
            return self._albums_cache
        albums: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for tid, t in self.tracks.items():
            albums.setdefault(self._album_key(t), []).append((tid, t))
        for items in albums.values():
            items.sort(key=lambda it: (it[1]["disc"], it[1]["track"], it[1]["title"].lower()))
        self._albums_cache = albums
        return albums

    def _track(self, tid: str, t: dict[str, Any]) -> dict[str, Any]:
        return models.track(tid, t["title"], t["artist"] or t["albumartist"], t["album"], t["duration"],
                            art=f"t:{tid}", album_id=self._album_key(t),
                            artist_id=_hash((t["albumartist"] or t["artist"] or "Unknown artist").lower()),
                            starred=tid in self.favourites, gain=t.get("rg"))

    def _album_collection(self, key: str, items: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        tid, t = items[0]
        title = t["album"] or os.path.basename(self._album_folder(t))
        subtitle = self._album_owner(items) + (f" · {t['year']}" if t["year"] else "")
        return models.collection(key, "album", title, subtitle, art=f"t:{tid}")

    def _album_collections(self, albums: list[tuple[str, list[tuple[str, dict[str, Any]]]]]) -> list[dict[str, Any]]:
        return [self._album_collection(k, v) for k, v in albums]

    def _played(self, tid: str) -> dict[str, Any]:
        return self.plays.get(tid) or {}

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        if not self.tracks:
            return []
        albums = self._albums()
        recent = sorted(albums.items(), key=lambda kv: max(t.get("added", t["mtime"]) for _, t in kv[1]),
                        reverse=True)[:20]
        sections = []
        if self.playlists:
            sections.append(models.section("Playlists", [
                models.collection(pid, "playlist", p["title"], models.count(len(p["tracks"]), "track"))
                for pid, p in sorted(self.playlists.items(), key=lambda kv: kv[1]["title"].lower())]))
        sections.append(models.section("Recently added", self._album_collections(recent)))
        # what the Deck remembers playing, and what the files say is best
        for list_id in ("recentplayed", "mostplayed", "toprated"):
            page = self._open_list(list_id)
            if page["tracks"]:
                sections.append(models.section(page["title"], [], page["tracks"][:SHELF]))
        if self.plays:
            unplayed = [(k, v) for k, v in albums.items() if not any(tid in self.plays for tid, _ in v)]
            if unplayed:
                unplayed.sort(key=lambda kv: max(t.get("added", t["mtime"]) for _, t in kv[1]), reverse=True)
                sections.append(models.section("Not played yet", self._album_collections(unplayed[:SHELF])))
        sections.append(models.section("Library", [
            models.collection("favourites", "list", "Favourite tracks", models.count(len(self.favourites), "track")),
            models.collection("albums", "list", "Albums", models.count(len(albums), "album")),
            models.collection("artists", "list", "Artists"),
            models.collection("genres", "list", "Genres"),
            models.collection("decades", "list", "Years"),
            models.collection("", "folder", "Folders"),
            models.collection("tracks", "list", "All tracks", models.count(len(self.tracks), "track")),
        ]))
        return sections

    # --- media center ---

    TABS = (("home", "Home"), ("albums", "Albums"), ("artists", "Artists"), ("genres", "Genres"),
            ("years", "Years"), ("composers", "Composers"), ("folders", "Folders"))
    NOW_PLAYING = "library"

    def center_tabs(self) -> list[dict[str, str]]:
        # a tab only for what the files are tagged with: no Composers for a library without any
        return [t for t in super().center_tabs()
                if t["id"] != "composers" or any(t2["composer"] for t2 in self.tracks.values())]

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            sections = [s for s in await self.home() if not models.is_categories(s)]
            favourites = self._open_list("favourites")["tracks"]
            return sections + ([models.section("Favourite tracks", [], favourites, display="grid")] if favourites else [])
        if tab_id in ("albums", "artists", "composers"):
            page = self._open_list(tab_id)
            return [models.section(page["title"], page["collections"], display="grid")]
        if tab_id == "genres":
            page = self._open_list("genres")
            return [models.section("Genres", page["collections"], display="tiles")] if page["collections"] \
                else [models.notice("None of the files say what genre they are")]
        if tab_id == "years":
            decades = self._open_list("decades")["collections"]
            if not decades:
                return [models.notice("None of the files say what year they're from")]
            return [models.section("Decades", decades, display="tiles")]
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
            first = items[0][1]
            length = sum(t["duration"] for _, t in items)
            about = [self._album_owner(items), first["year"], models.count(len(items), "track"),
                     f"{round(length / 60)} min" if length >= 60 else "", _quality_line(first, short=True)]
            return models.page(c["title"], " · ".join(a for a in about if a), c["art"],
                               tracks=[self._track(tid, t) for tid, t in items])
        if kind == "artist":
            albums = [(k, v) for k, v in self._albums().items() if _hash(self._album_owner(v).lower()) == id]
            # songs of theirs on other people's albums (compilations, guest spots)
            elsewhere = [(tid, t) for k, v in self._albums().items() if _hash(self._album_owner(v).lower()) != id
                         for tid, t in v if _hash((t["artist"] or "").lower()) == id]
            if not albums and not elsewhere:
                raise ServiceError("Artist not found")
            albums.sort(key=lambda kv: (kv[1][0][1]["year"], kv[1][0][1]["album"].lower()))
            name = self._album_owner(albums[0][1]) if albums else elsewhere[0][1]["artist"]
            return models.page(name, models.count(len(albums), "album"),
                               collections=self._album_collections(albums),
                               tracks=[self._track(tid, t) for _, v in albums for tid, t in v],
                               extra=[{"title": "Appears on", "tracks": [self._track(tid, t) for tid, t in elsewhere]}])
        if kind == "playlist":
            playlist = self.playlists.get(id)
            if not playlist:
                raise ServiceError("Playlist not found")
            return models.page(playlist["title"], "Playlist", tracks=[
                self._track(tid, self.tracks[tid]) for tid in playlist["tracks"] if tid in self.tracks])
        if kind == "folder":
            return self._open_folder(id)
        raise ServiceError(f"Unknown collection kind: {kind}")

    def _tracks_page(self, title: str, picked: list[tuple[str, dict[str, Any]]], subtitle: str = "") -> dict[str, Any]:
        """A page of tracks, with the albums they're from first when there are a few."""
        keys = list(dict.fromkeys(self._album_key(t) for _, t in picked))
        albums = self._albums()
        collections = self._album_collections([(k, albums[k]) for k in keys if k in albums]) if len(keys) > 1 else []
        return models.page(title, subtitle or models.count(len(picked), "track"), collections=collections,
                           tracks=[self._track(tid, t) for tid, t in picked])

    def _open_list(self, id: str) -> dict[str, Any]:
        if id == "albums":
            albums = sorted(self._albums().items(), key=lambda kv: (kv[1][0][1]["album"] or "~").lower())
            return models.page("Albums", collections=self._album_collections(albums))
        if id == "artists":
            artists: dict[str, tuple[str, int]] = {}
            for items in self._albums().values():
                name = self._album_owner(items)
                key = _hash(name.lower())
                artists[key] = (name, artists.get(key, (name, 0))[1] + 1)
            return models.page("Artists", collections=[
                models.collection(k, "artist", name, models.count(n, "album"))
                for k, (name, n) in sorted(artists.items(), key=lambda kv: kv[1][0].lower())])
        if id == "genres":
            counts: dict[str, list[Any]] = {}
            for t in self.tracks.values():
                for genre in t.get("genres") or ([t["genre"]] if t["genre"] else []):
                    entry = counts.setdefault(genre.lower(), [genre, 0])
                    entry[1] += 1
            return models.page("Genres", collections=[
                models.collection(f"genre:{name}", "list", name, models.count(n, "track"))
                for name, n in sorted(counts.values(), key=lambda e: e[0].lower())])
        if id == "decades":
            decades: dict[int, int] = {}
            for items in self._albums().values():
                year = _number(items[0][1]["year"])
                if year:
                    decades[year // 10 * 10] = decades.get(year // 10 * 10, 0) + 1
            return models.page("Years", collections=[
                models.collection(f"decade:{d}", "list", f"{d}s", models.count(n, "album"))
                for d, n in sorted(decades.items(), reverse=True)])
        if id == "composers":
            composers: dict[str, list[Any]] = {}
            for t in self.tracks.values():
                if t["composer"]:
                    entry = composers.setdefault(t["composer"].lower(), [t["composer"], 0])
                    entry[1] += 1
            return models.page("Composers", collections=[
                models.collection(f"composer:{name}", "list", name, models.count(n, "track"))
                for name, n in sorted(composers.values(), key=lambda e: e[0].lower())])
        if id.startswith("genre:"):
            genre = id[6:].lower()
            picked = [(tid, t) for tid, t in self._album_ordered()
                      if genre in [g.lower() for g in t.get("genres") or [t["genre"]]]]
            return self._tracks_page(id[6:], picked)
        if id.startswith("decade:"):
            start = _number(id[7:])
            albums = [(k, v) for k, v in self._albums().items() if start <= _number(v[0][1]["year"]) < start + 10]
            albums.sort(key=lambda kv: (kv[1][0][1]["year"], kv[1][0][1]["album"].lower()))
            return models.page(f"{start}s", models.count(len(albums), "album"),
                               collections=self._album_collections(albums))
        if id.startswith("composer:"):
            name = id[9:].lower()
            picked = [(tid, t) for tid, t in self._album_ordered() if t["composer"].lower() == name]
            return self._tracks_page(id[9:], picked)
        if id == "recentplayed":
            played = sorted((tid for tid in self.plays if tid in self.tracks),
                            key=lambda tid: self.plays[tid].get("last", 0), reverse=True)[:LIST_MAX]
            return models.page("Recently played", tracks=[self._track(tid, self.tracks[tid]) for tid in played])
        if id == "mostplayed":
            # played more than once: one play says little about what's liked
            played = sorted((tid for tid, p in self.plays.items() if tid in self.tracks and p.get("count", 0) > 1),
                            key=lambda tid: (-self.plays[tid]["count"], -self.plays[tid].get("last", 0)))[:LIST_MAX]
            return models.page("Most played", tracks=[self._track(tid, self.tracks[tid]) for tid in played])
        if id == "toprated":
            rated = [(tid, t) for tid, t in self.tracks.items() if t.get("rating", 0) >= 4]
            rated.sort(key=lambda kv: (-kv[1]["rating"], kv[1]["artist"].lower(), kv[1]["title"].lower()))
            return models.page("Top rated", tracks=[self._track(tid, t) for tid, t in rated[:LIST_MAX]])
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

    def _album_ordered(self) -> list[tuple[str, dict[str, Any]]]:
        """Every track, album by album (A to Z), each in its own order."""
        albums = sorted(self._albums().values(), key=lambda v: (self._album_owner(v).lower(), v[0][1]["year"],
                                                               v[0][1]["album"].lower()))
        return [it for v in albums for it in v]

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

        def matches(t: dict[str, Any]) -> bool:
            return (q in t["title"].lower() or q in t["artist"].lower() or q in t["album"].lower()
                    or q in t["composer"].lower() or any(q in g.lower() for g in t.get("genres") or []))

        tracks = [(tid, t) for tid, t in self.tracks.items() if matches(t)]
        albums = [(k, v) for k, v in self._albums().items()
                  if q in (v[0][1]["album"] or "").lower() or q in self._album_owner(v).lower()]
        return models.page(f"Results for “{query}”", collections=self._album_collections(albums[:20]),
                           tracks=[self._track(tid, t) for tid, t in tracks[:100]])

    # --- now playing ---

    async def details(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        """The file behind the track: how it's encoded, what its tags say, how often it's played."""
        t = self.tracks.get(track.get("id", ""))
        if not t:
            return None
        played = self._played(track["id"])
        facts = [
            ["Format", _quality_line(t)],
            ["Year", t["year"]],
            ["Genre", ", ".join(t.get("genres") or ([t["genre"]] if t["genre"] else []))],
            ["Composer", t["composer"]],
            ["Label", t["label"]],
            ["Track", f"{t['track']}" + (f" · disc {t['disc']}" if t["disc"] > 1 else "") if t["track"] else ""],
            ["Rating", "★" * t["rating"] + "☆" * (5 - t["rating"]) if t.get("rating") else ""],
            ["Plays", (models.count(played["count"], "play")
                       + f", last {time.strftime('%d %b %Y', time.localtime(played['last']))}")
             if played.get("count") else "Not played yet"],
            ["File", os.path.basename(t["path"])],
            ["Folder", self._shown_folder(os.path.dirname(t["path"]))],
        ]
        return {"facts": [f for f in facts if f[1]]}

    def _shown_folder(self, directory: str) -> str:
        """A folder as it is inside the library ("Artist/Album"), not its whole path."""
        for root in self.roots:
            if directory == root or directory.startswith(root + os.sep):
                return os.path.relpath(directory, os.path.dirname(root))
        return directory

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

    async def scrobble(self, track_id: str, submission: bool) -> None:
        """A listen (submission) counts a play: Most played, Recently played and Not played yet come
        from these."""
        if not submission or track_id not in self.tracks:
            return
        played = self.plays.get(track_id) or {}
        self.plays[track_id] = {"count": played.get("count", 0) + 1, "last": time.time()}
        tmp = self.plays_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.plays, f)
        os.replace(tmp, self.plays_path)

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
