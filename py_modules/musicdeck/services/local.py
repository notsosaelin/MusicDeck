"""
Music files on the device. The folders chosen in the dialog are scanned recursively; tags (composer,
genres, year, rating, label, compilation), lengths, quality (format, bit depth, sample rate, bitrate),
ReplayGain and embedded art are read with mutagen and cached in an index (local-<id>.json), so a
rescan only re-reads files whose size or modification time changed. Files are served straight from
disk by the stream server. Favourites (local-<id>-favourites.json) and play counts
(local-<id>-plays.json) are kept on the Deck.

Managing the library: tags are edited in the files themselves (tagging.py: written to a copy that then
replaces the file; each file's last edit can be undone, from local-<id>-tag-history.json), and filled in
from a MusicBrainz release the user picks. Album artwork set by the user, or found on the Cover Art
Archive, is kept in local-<id>-art/ (the music folders aren't touched) and shown before the files' own.
Nothing leaves the Deck except LRCLIB lyrics lookups and the MusicBrainz look-ups the user asks for.
"""
import asyncio
import hashlib
import json
import os
import random
import re
import time
from typing import Any, Optional

import shutil

import aiohttp

from .. import lyrics, models, pictures, tagging
from ..background import spawn
from ..catalogues import Catalogues
from ..musicbrainz import MusicBrainz
from ..pictures import plain
from ..watch import FolderWatcher
from ..net import tls
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
ART_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_ART_BYTES = 15 * 1024 * 1024
ARTIST = "artist-"  # an artist's picture's key among the albums' (album keys are hex: no clash)
UNKNOWN = "Unknown artist"
LYRICS_PACE = 0.5  # seconds between lyrics look-ups for many tracks, to be gentle with LRCLIB
RECHECK_SECONDS = 3600  # a scan for new music this often, for what the folder watcher can't see
#: What happens when new albums arrive (the source's dialog): covers and artists' pictures; MusicBrainz
#: tags ("off", "sure": only an unmistakable match, only filling empty fields, "ask": a list to review);
#: lyrics; and where lyrics are kept ("musicdeck": in MusicDeck's own folder, "beside": an .lrc next
#: to each song, the way Navidrome, Jellyfin, Plex and foobar2000 look for them).
DEFAULTS = {"auto_art": True, "auto_tags": "ask", "auto_lyrics": False, "lyrics_place": "musicdeck"}
TAG_MODES = ("off", "sure", "ask")
LYRICS_PLACES = ("musicdeck", "beside")


def _lrc(found: dict[str, Any], t: dict[str, Any]) -> str:
    """Lyrics as an .lrc file: timed lines when they're synced, with the song's names at the top."""
    # [ar:] [ti:] [al:] [length:]: players such as Poweramp check them against the song
    minutes, seconds = divmod(int(t.get("duration") or 0), 60)
    head = [f"[ar:{t['artist'] or t['albumartist']}]", f"[ti:{t['title']}]", f"[al:{t['album']}]",
            f"[length:{minutes:02d}:{seconds:02d}]" if t.get("duration") else "[length:]"]
    lines = []
    for line in found["lines"]:
        if found.get("synced") and line.get("t") is not None:
            minutes, seconds = divmod(float(line["t"]), 60)
            lines.append(f"[{int(minutes):02d}:{seconds:05.2f}]{line['text']}")
        else:
            lines.append(line["text"])
    return "\n".join([h for h in head if not h.endswith(":]")] + lines) + "\n"


def _write_text(path: str, text: str) -> None:
    """Writes beside the music through a hidden copy, so a crash never leaves half a file."""
    directory, name = os.path.split(path)
    os.makedirs(directory, exist_ok=True)
    temp = os.path.join(directory, f".{name}{tagging.TEMP_SUFFIX}")
    try:
        with open(temp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temp, path)
    except PermissionError:
        raise ServiceError("MusicDeck isn't allowed to write in this folder")
    finally:
        if os.path.exists(temp):
            os.remove(temp)


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


def _size(n: int) -> str:
    """1536 -> "1.5 KB"; what a library takes up on the Deck"""
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


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
            try:
                value = tags.get(key) if hasattr(tags, "get") else None
            except (ValueError, KeyError):  # a key the format can't have (MP4's "©lyr" in a Vorbis comment)
                continue
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

    config: {"paths": ["/home/deck/Music", ...], plus what happens when new music arrives: DEFAULTS}
    """

    type = "local"
    display_name = "Local files"
    # scrobble: plays counted on the Deck (Most played, Recently played), when "Report plays" is on
    # tags: edited in the files, and looked up on MusicBrainz; artwork: albums' own pictures, set or found
    features = frozenset({"star", "lyrics", "radio", "scrobble", "tags", "artwork"})

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
        # albums' own pictures, set by the user or found on the Cover Art Archive: {album key: file name}
        self.art_dir = os.path.join(runtime_dir, f"local-{instance_id}-art")
        self.album_art: dict[str, str] = {}
        # each file's last tag edit, to undo it: {track id: {"keys": {key: values before}, "at"}}
        self.history_path = os.path.join(runtime_dir, f"local-{instance_id}-tag-history.json")
        self.history: dict[str, dict[str, Any]] = {}
        # one edit at a time, and none while a scan runs (it would put back what it read before)
        self.edit_lock = asyncio.Lock()
        self.mb: Optional[MusicBrainz] = None
        self.catalogues: Optional[Catalogues] = None
        self.http: Optional[aiohttp.ClientSession] = None
        self.art_task: Optional[asyncio.Task[None]] = None
        self.art_progress = {"running": False, "done": 0, "total": 0, "found": 0}
        self.lyrics_task: Optional[asyncio.Task[None]] = None
        self.lyrics_progress = {"running": False, "done": 0, "total": 0, "found": 0}
        # Browse's tiles (counts, covers), made again when the library or its pictures change
        self._browse_cache: Optional[list[dict[str, Any]]] = None
        # lyrics kept in MusicDeck rather than beside the songs: <track id>.lrc
        self.lyrics_dir = os.path.join(runtime_dir, f"local-{instance_id}-lyrics")
        # new albums MusicBrainz may know, for the user to look at ("Ask me"): {album key: {title, artist, at}}
        self.review_path = os.path.join(runtime_dir, f"local-{instance_id}-review.json")
        self.review: dict[str, dict[str, Any]] = {}
        # new music: the folders watched, a scan now and then, and the new albums' queue
        self.watcher: Optional[FolderWatcher] = None
        self.recheck: Optional[asyncio.Task[None]] = None
        self.new_tracks: set[str] = set()
        # albums renamed by tag edits: {old id: new id}, so what remembered the old one still finds it
        self.renamed: dict[str, str] = {}
        # the tracks of the albums being got ready (lyrics are only looked up for these)
        self.new_since: set[str] = set()
        self.import_queue: list[str] = []
        self.import_task: Optional[asyncio.Task[None]] = None
        self.import_progress = {"running": False, "done": 0, "total": 0}
        # set by the plugin: a short note to the user (a toast), e.g. what a new album got
        self.announce: Optional[Any] = None
        self.scan_task: Optional[asyncio.Task[None]] = None
        self.scan_progress = {"scanning": False, "found": 0, "last_scan": 0.0, "error": ""}

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        """What happens to new music, as one of the choices there are (DEFAULTS for what's missing)."""
        config = dict(config)
        for key, default in DEFAULTS.items():
            value = config.get(key, default)
            allowed = TAG_MODES if key == "auto_tags" else LYRICS_PLACES if key == "lyrics_place" else None
            config[key] = value if (value in allowed if allowed else isinstance(value, bool)) else default
        return config

    def option(self, key: str) -> Any:
        return self.config.get(key, DEFAULTS[key])

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
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
            self.history = history if isinstance(history, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            self.history = {}
        try:
            self.album_art = {os.path.splitext(n)[0]: n for n in os.listdir(self.art_dir)
                              if os.path.splitext(n)[1].lower() in ART_TYPES}
        except OSError:
            self.album_art = {}
        try:
            with open(self.review_path, "r", encoding="utf-8") as f:
                review = json.load(f)
            self.review = review if isinstance(review, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            self.review = {}
        self.rescan()
        self.watcher = FolderWatcher(self.roots, self.rescan)
        try:
            self.watcher.start()
        except OSError:
            self.watcher.stop()
        self.recheck = spawn(self._recheck(), "checking for new music")

    async def _recheck(self) -> None:
        while True:
            await asyncio.sleep(RECHECK_SECONDS)
            self.rescan()

    async def stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
        for task in (self.scan_task, self.art_task, self.lyrics_task, self.import_task, self.recheck):
            if task:
                task.cancel()
        if self.http:
            await self.http.close()

    def remove(self) -> None:
        for path in (self.index_path, self.favourites_path, self.plays_path, self.history_path, self.review_path):
            try:
                os.remove(path)
            except OSError:
                pass
        shutil.rmtree(self.art_dir, ignore_errors=True)
        shutil.rmtree(self.lyrics_dir, ignore_errors=True)

    async def test(self) -> str:
        if not self.roots:
            raise ServiceError("No music folder selected")
        missing = [r for r in self.roots if not os.path.isdir(r)]
        if missing:
            raise ServiceError(f"Folder not found: {missing[0]}")
        return f"{models.count(len(self.tracks), 'track')} indexed"

    def status(self) -> dict[str, Any]:
        return {**self.scan_progress, "tracks": len(self.tracks), "art": dict(self.art_progress),
                "lyrics": dict(self.lyrics_progress), "importing": dict(self.import_progress),
                "review": len(self.review), "watching": bool(self.watcher and self.watcher.available)}

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
        self._queue_new_albums()

    def _scan_blocking(self) -> None:
        previous = {t["path"]: t for t in self.tracks.values()}
        # a library's very first scan is all "new": only what arrives after it is new music
        first = not previous
        tracks: dict[str, dict[str, Any]] = {}
        playlist_files: list[str] = []
        for root in self.roots:
            # followlinks=False avoids symlink loops; os.walk skips unreadable dirs silently
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for name in sorted(filenames):
                    # hidden files: macOS's "._" shadows, and a copy being tag-edited (tagging.py)
                    if name.startswith(".") or tagging.is_temp(name):
                        continue
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
        if not first:
            self.new_tracks |= {tid for tid, t in tracks.items() if t["path"] not in previous}
        self.tracks = tracks
        self.playlists = playlists
        self._changed()
        self.folder_covers = {}
        self._save_index()

    # --- new music ---

    def _queue_new_albums(self) -> None:
        """The albums of the tracks that just arrived, queued to get ready (covers, tags, lyrics)."""
        new = {tid for tid in self.new_tracks if tid in self.tracks}
        self.new_tracks = set()
        if not new or not (self.option("auto_art") or self.option("auto_tags") != "off" or self.option("auto_lyrics")):
            return
        albums = self._albums()
        keys = [k for k, items in albums.items() if any(tid in new for tid, _ in items)]
        self.import_queue += [k for k in keys if k not in self.import_queue]
        self.new_since |= new
        if not (self.import_task and not self.import_task.done()):
            self.import_task = spawn(self._import(), "getting new music ready")

    async def _import(self) -> None:
        done = {"albums": 0, "covers": 0, "pictures": 0, "tagged": 0, "review": 0, "lyrics": 0}
        self.import_progress.update(running=True, done=0, total=len(self.import_queue))
        try:
            while self.import_queue:
                key = self.import_queue.pop(0)
                self.import_progress["total"] = self.import_progress["done"] + len(self.import_queue) + 1
                if key in self._albums():
                    try:
                        await self._import_album(key, done)
                    except ServiceError:
                        pass  # offline, or a site that can't answer: Find missing artwork and Look up still can
                    done["albums"] += 1
                self.import_progress["done"] += 1
        finally:
            self.import_progress["running"] = False
            self.new_since = set()
        if done["albums"] and self.announce:
            self.announce(self._import_note(done))

    async def _import_album(self, key: str, done: dict[str, int]) -> None:
        items = self._albums()[key]
        new = self.new_since
        whole = all(tid in new for tid, _ in items)  # a new album, not a track added to one
        owner = self._album_owner(items)
        if self.option("auto_art"):
            if items[0][1]["album"] and not await self._has_art(key) and await self.find_album_art(key):
                done["covers"] += 1
            artist = _hash(owner.lower())
            if owner not in (VARIOUS, UNKNOWN) and ARTIST + artist not in self.album_art \
                    and await self.find_artist_art(artist):
                done["pictures"] += 1
        mode = self.option("auto_tags")
        if mode != "off" and whole and items[0][1]["album"]:
            key = await self._import_tags(key, mode, done)
        if self.option("auto_lyrics") and key in self._albums():
            for tid, _ in self._albums()[key]:
                if tid in new:
                    try:
                        if await self._save_lyrics(tid):
                            done["lyrics"] += 1
                    except ServiceError:
                        pass
                    await asyncio.sleep(LYRICS_PACE)

    async def _import_tags(self, key: str, mode: str, done: dict[str, int]) -> str:
        """"sure": a match there's no doubt about fills in what the files leave empty; "ask": an album
        MusicBrainz knows goes on the list to review. Returns the album's key (filling in can change it)."""
        items = self._albums()[key]
        album = items[0][1]["album"]
        owner = self._album_owner(items)
        releases = [r for r in await self.lookup(key) if plain(r["title"]) == plain(album)]
        if not releases:
            return key
        if mode == "ask":
            self.review[key] = {"title": album, "artist": owner, "at": time.time()}
            self._save_review()
            done["review"] += 1
            return key
        # MusicBrainz's only: it tells one release from another, where the catalogues name albums
        sure = [r for r in releases if r["source"] == "MusicBrainz" and r["tracks"] == len(items) and r["score"] >= 95
                and (owner == VARIOUS or plain(r["artist"]) == plain(owner))]
        # every likely release the same record (pressings of one release group), or no doubt at all
        if not sure or len({r["group"] or r["id"] for r in sure}) != 1:
            return key
        plan = await self._lookup_plan(key, sure[0]["id"])
        # only what the files leave empty: nothing the user (or their tagger) wrote is changed
        album_changes = {f: new for f, (old, new) in plan["album"].items() if not old}
        edits = [(tid, {**album_changes, **{f: new for f, (old, new) in plan["tracks"].get(tid, {}).items() if not old}})
                 for tid, _ in items]
        edits = [(tid, c) for tid, c in edits if c]
        if not edits:
            return key
        result = await self._write_tags(edits)
        done["tagged"] += 1
        return result.get("album_id") or key

    @staticmethod
    def _import_note(done: dict[str, int]) -> str:
        """"2 new albums: 2 covers, lyrics for 18 tracks, 1 to review"."""
        parts = [models.count(done["covers"], "cover") if done["covers"] else "",
                 models.count(done["pictures"], "artist picture") if done["pictures"] else "",
                 f"{models.count(done['tagged'], 'album')} tagged" if done["tagged"] else "",
                 f"lyrics for {models.count(done['lyrics'], 'track')}" if done["lyrics"] else "",
                 f"{done['review']} to review" if done["review"] else ""]
        found = ", ".join(x for x in parts if x)
        head = f"{models.count(done['albums'], 'new album')} ready"
        return f"{head}: {found}" if found else head

    def _save_review(self) -> None:
        self._browse_cache = None
        self.forget_browse_info()
        tmp = self.review_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.review, f)
        os.replace(tmp, self.review_path)

    async def dismiss_review(self, album_id: str) -> None:
        if self.review.pop(album_id, None):
            self._save_review()

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
        self.renamed = data.get("renamed", {}) if isinstance(data.get("renamed"), dict) else {}
        self._changed()
        self.scan_progress["last_scan"] = data.get("scanned_at", 0.0)

    def _save_index(self) -> None:
        os.makedirs(self.runtime_dir, exist_ok=True)
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": INDEX_VERSION, "roots": self.roots, "scanned_at": time.time(),
                       "tracks": self.tracks, "playlists": self.playlists, "renamed": self.renamed}, f)
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

    def _changed(self) -> None:
        """The tracks changed: albums and Browse's tiles are made again when next needed."""
        self._albums_cache = None
        self._browse_cache = None
        self.forget_browse_info()

    def _stamp(self, key: str) -> str:
        """".<time>" of an own picture: a new one is a new art id, so the browser doesn't show the old one
        from its cache."""
        own = self.album_art.get(key)
        try:
            return f".{int(os.path.getmtime(os.path.join(self.art_dir, own)))}" if own else ""
        except OSError:
            return ""

    def _art_id(self, tid: str, t: dict[str, Any]) -> str:
        """t:<track id>, with its album's own picture's version when it has one."""
        return f"t:{tid}{self._stamp(self._album_key(t))}"

    def _artist_art_id(self, artist_id: str) -> Optional[str]:
        """a:<artist id> when the artist has a picture (set or found), else None."""
        return f"a:{artist_id}{self._stamp(ARTIST + artist_id)}" if ARTIST + artist_id in self.album_art else None

    def _artist_index(self) -> dict[str, tuple[str, int]]:
        """{artist id: (name, albums)}, by album artist."""
        artists: dict[str, tuple[str, int]] = {}
        for items in self._albums().values():
            name = self._album_owner(items)
            key = _hash(name.lower())
            artists[key] = (name, artists.get(key, (name, 0))[1] + 1)
        return artists

    def _track(self, tid: str, t: dict[str, Any]) -> dict[str, Any]:
        return models.track(tid, t["title"], t["artist"] or t["albumartist"], t["album"], t["duration"],
                            art=self._art_id(tid, t), album_id=self._album_key(t),
                            artist_id=_hash((t["albumartist"] or t["artist"] or "Unknown artist").lower()),
                            starred=tid in self.favourites, gain=t.get("rg"))

    def _album_collection(self, key: str, items: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        tid, t = items[0]
        title = t["album"] or os.path.basename(self._album_folder(t))
        subtitle = self._album_owner(items) + (f" · {t['year']}" if t["year"] else "")
        return models.collection(key, "album", title, subtitle, art=self._art_id(tid, t))

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

    NOW_PLAYING = "library"

    def center_tabs(self) -> list[dict[str, Any]]:
        """With what's in each on Browse: how many, and a few covers (albums) or pictures (artists). A
        section only for what the files are tagged with: no Composers for a library without any."""
        if self._browse_cache is None:
            self._browse_cache = self._browse_tiles()
        return self._browse_cache

    def _browse_tiles(self) -> list[dict[str, Any]]:
        """Browse's tiles: the library (albums, artists, songs, playlists, favourites); what's been
        listened to; the ways in (genres, years); the biggest genres, a press away; the folders."""
        albums = self._albums()
        artists = self._artist_index()
        composers = {t["composer"].lower() for t in self.tracks.values() if t["composer"]}
        genres: dict[str, list[Any]] = {}
        for tid, t in self.tracks.items():
            for genre in t.get("genres") or ([t["genre"]] if t["genre"] else []):
                genres.setdefault(genre.lower(), [genre, []])[1].append((tid, t))
        decades = {_number(v[0][1]["year"]) // 10 for v in albums.values() if _number(v[0][1]["year"])}

        def covers(items: list[tuple[str, dict[str, Any]]], n: int = 4) -> list[str]:
            """Art ids of the albums these tracks are from, one each, those with a picture of their own first."""
            seen: dict[str, str] = {}
            for tid, t in items:
                seen.setdefault(self._album_key(t), self._art_id(tid, t))
            return [a for _, a in sorted(seen.items(), key=lambda kv: kv[0] not in self.album_art)][:n]

        # albums with a picture of their own first, then the latest added
        recent = sorted(albums.items(), key=lambda kv: (kv[0] not in self.album_art,
                                                        -max(t.get("added", t["mtime"]) for _, t in kv[1])))
        tabs: list[dict[str, Any]] = []

        def add(id: str, title: str, group: str, subtitle: str = "", art: Optional[list[str]] = None) -> None:
            tabs.append({"id": id, "title": title, "group": group, **({"subtitle": subtitle} if subtitle else {}),
                         **({"art": art} if art else {})})

        add("home", "Home", "explore")  # its own tab, not on Browse
        if self.review:
            reviewed = [k for k in self.review if k in albums]
            add("review", "To review", "yours", models.count(len(reviewed), "album"),
                [self._art_id(*albums[k][0]) for k in reviewed[:4]])
        add("albums", "Albums", "yours", models.count(len(albums), "album"),
            [self._art_id(*v[0]) for _, v in recent[:4]])
        add("artists", "Artists", "yours", models.count(len(artists), "artist"),
            [a for a in (self._artist_art_id(k) for k in artists) if a][:4])
        # other covers than Albums' where there are enough
        add("songs", "Songs", "yours", models.count(len(self.tracks), "song"),
            [self._art_id(*v[0]) for _, v in (recent[4:8] if len(recent) >= 8 else recent[1:2] or recent[:1])])
        if self.playlists:
            listed = [(tid, self.tracks[tid]) for pl in self.playlists.values() for tid in pl["tracks"]
                      if tid in self.tracks]
            add("playlists", "Playlists", "yours", models.count(len(self.playlists), "playlist"), covers(listed))
        favourites = [(tid, self.tracks[tid]) for tid in self.favourites if tid in self.tracks]
        if favourites:
            add("favourites", "Favourites", "yours", models.count(len(favourites), "song"), covers(favourites))
        if composers:
            add("composers", "Composers", "yours", models.count(len(composers), "composer"))

        # what's been listened to: no counts, which the next play would put out of date
        played = sorted((tid for tid in self.plays if tid in self.tracks),
                        key=lambda tid: self.plays[tid].get("last", 0), reverse=True)
        if played:
            add("recentplayed", "Recently played", "listening", "What you've played lately",
                covers([(tid, self.tracks[tid]) for tid in played], 1))
            often = sorted((tid for tid in played if self.plays[tid].get("count", 0) > 1),
                           key=lambda tid: -self.plays[tid]["count"])
            if often:
                add("mostplayed", "Most played", "listening", "Played more than once",
                    covers([(tid, self.tracks[tid]) for tid in often], 1))
            unplayed = [v for v in albums.values() if not any(tid in self.plays for tid, _ in v)]
            if unplayed:
                unplayed.sort(key=lambda v: max(t.get("added", t["mtime"]) for _, t in v), reverse=True)
                add("unplayed", "Not played yet", "listening", "Albums you haven't played",
                    [self._art_id(*unplayed[0][0])])
        rated = [(tid, t) for tid, t in self.tracks.items() if t.get("rating", 0) >= 4]
        if rated:
            add("toprated", "Top rated", "listening", "Four stars and up", covers(rated, 1))

        if genres:
            add("genres", "Genres", "explore", models.count(len(genres), "genre"))
        if decades:
            add("years", "Years", "explore", models.count(len(decades), "decade"))
        size = sum(t.get("size", 0) for t in self.tracks.values())
        folders = models.count(len(self._open_folder("")["collections"]), "folder")
        add("folders", "Folders", "files", f"{folders} · {_size(size)}" if size else folders)
        return tabs

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            sections = [s for s in await self.home() if not models.is_categories(s)]
            favourites = self._open_list("favourites")["tracks"]
            return sections + ([models.section("Favourite tracks", [], favourites, display="grid")] if favourites else [])
        if tab_id == "review":
            albums = self._albums()
            keys = [k for k in self.review if k in albums]
            if not keys:
                return [models.notice("Nothing to review")]
            return [models.notice("MusicBrainz may know these new albums. Y on one, then Look up on MusicBrainz, "
                                  "shows what it would change before anything is written."),
                    models.section("To review", self._album_collections([(k, albums[k]) for k in keys]), display="grid")]
        if tab_id == "songs":
            return [models.section("Songs", [], self._open_list("tracks")["tracks"], display="grid")]
        if tab_id == "playlists":
            return [models.section("Playlists", [
                models.collection(pid, "playlist", pl["title"], models.count(len(pl["tracks"]), "track"))
                for pid, pl in sorted(self.playlists.items(), key=lambda kv: kv[1]["title"].lower())],
                display="grid", mine=True)]
        if tab_id in ("favourites", "recentplayed", "mostplayed", "toprated"):
            page = self._open_list(tab_id)
            return [models.section(page["title"], [], page["tracks"], display="grid")] if page["tracks"] \
                else [models.notice("Nothing here yet")]
        if tab_id == "unplayed":
            unplayed = [(k, v) for k, v in self._albums().items() if not any(tid in self.plays for tid, _ in v)]
            unplayed.sort(key=lambda kv: max(t.get("added", t["mtime"]) for _, t in kv[1]), reverse=True)
            return [models.section("Not played yet", self._album_collections(unplayed), display="grid")] if unplayed \
                else [models.notice("Every album has been played")]
        if tab_id.startswith("genre:"):
            # a genre's albums, then its songs
            page = self._open_list(tab_id)
            return ([models.section("Albums", page["collections"], display="grid")] if page["collections"] else []) \
                + [models.section("Songs", [], page["tracks"], display="grid")]
        if tab_id in ("albums", "artists", "composers"):
            page = self._open_list(tab_id)
            return [models.section(page["title"], page["collections"], display="grid")]
        if tab_id == "genres":
            page = self._open_list("genres")
            return [models.section("Genres", page["collections"], display="rows")] if page["collections"] \
                else [models.notice("None of the files say what genre they are")]
        if tab_id == "years":
            decades = self._open_list("decades")["collections"]
            if not decades:
                return [models.notice("None of the files say what year they're from")]
            return [models.section("Decades", decades, display="rows")]
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
            return models.page(name, models.count(len(albums), "album"), self._artist_art_id(id),
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

    def current(self, collection: dict[str, Any]) -> Optional[dict[str, Any]]:
        """An album or artist as it is now: its picture, its name; an album renamed by a tag edit under
        its new id; None when it's left the library."""
        kind, id = collection.get("kind"), collection.get("id", "")
        if kind == "album":
            albums = self._albums()
            id = self.renamed.get(id, id)
            return self._album_collection(id, albums[id]) if id in albums else None
        if kind == "artist":
            found = self._artist_index().get(id)
            return models.collection(id, "artist", found[0], models.count(found[1], "album"),
                                     art=self._artist_art_id(id)) if found else None
        return collection

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
            return models.page("Artists", collections=[
                models.collection(k, "artist", name, models.count(n, "album"), art=self._artist_art_id(k))
                for k, (name, n) in sorted(self._artist_index().items(), key=lambda kv: kv[1][0].lower())])
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

    # --- managing the library: tags ---

    def _target(self, kind: str, id: str) -> list[tuple[str, dict[str, Any]]]:
        """The files a tag edit is for: one track, or an album's tracks."""
        if kind == "track" and id in self.tracks:
            return [(id, self.tracks[id])]
        if kind == "album" and id in self._albums():
            return list(self._albums()[id])
        raise ServiceError("That's no longer in the library")

    @staticmethod
    def _field_value(t: dict[str, Any], field: str) -> str:
        if field == "track":
            return str(t["track"] or "")
        if field == "disc":
            return str(t["disc"] or "")
        if field == "genre":
            return "; ".join(t.get("genres") or ([t["genre"]] if t["genre"] else []))
        return str(t.get(field) or "")

    async def tag_fields(self, kind: str, id: str) -> dict[str, Any]:
        """What the tag editor shows: {"fields": [{"field", "label", "value", "mixed"}], "files", "undo",
        "own_art"}. An album shows the fields its tracks share; one its tracks differ on is "mixed"."""
        items = self._target(kind, id)
        names = tagging.ALBUM_FIELDS if kind == "album" else tuple(f for f, _ in tagging.FIELDS)
        fields = []
        for field, label in tagging.FIELDS:
            if field not in names:
                continue
            values = {self._field_value(t, field) for _, t in items}
            fields.append({"field": field, "label": label, "value": values.pop() if len(values) == 1 else "",
                           "mixed": len(values) > 0})
        album = self._album_key(items[0][1])
        return {"fields": fields, "files": len(items), "undo": any(tid in self.history for tid, _ in items),
                "own_art": album in self.album_art, "album_id": album, "review": album in self.review}

    async def edit_tags(self, kind: str, id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Writes the changes to the files; returns {"changed", "album_id"} (the album's id changes with
        its name or artist)."""
        try:
            changes = tagging.clean(changes, tagging.ALBUM_FIELDS if kind == "album"
                                    else tuple(f for f, _ in tagging.FIELDS))
        except tagging.TagError as e:
            raise ServiceError(str(e))
        if not changes:
            raise ServiceError("Nothing to change")
        if kind == "album" and self.review.pop(id, None):
            self._save_review()
        return await self._write_tags([(tid, changes) for tid, _ in self._target(kind, id)])

    async def undo_tags(self, kind: str, id: str) -> dict[str, Any]:
        """Puts back what the last edit of each file changed."""
        undoable = [tid for tid, _ in self._target(kind, id) if tid in self.history]
        if not undoable:
            raise ServiceError("Nothing to undo")
        return await self._write_tags([(tid, None) for tid in undoable])

    async def _write_tags(self, edits: list[tuple[str, Optional[dict[str, Any]]]]) -> dict[str, Any]:
        """Each (track id, changes) written, or (track id, None) undone; the index, history and the
        album's picture follow."""
        if self.scan_progress["scanning"]:
            raise ServiceError("The library is being scanned; try again when it's done")
        async with self.edit_lock:
            old_albums = {self._album_key(self.tracks[tid]) for tid, _ in edits if tid in self.tracks}
            changed, errors = 0, []
            for tid, changes in edits:
                t = self.tracks.get(tid)
                if not t:
                    continue
                try:
                    if changes is None:
                        await asyncio.to_thread(tagging.write_raw, t["path"], self.history[tid]["keys"])
                        self.history.pop(tid, None)
                    else:
                        before = await asyncio.to_thread(tagging.write, t["path"], changes)
                        self.history[tid] = {"keys": before, "at": time.time()}
                except tagging.TagError as e:
                    errors.append(str(e))
                    continue
                await asyncio.to_thread(self._reread, tid)
                changed += 1
            self._changed()
            new_albums = {self._album_key(self.tracks[tid]) for tid, _ in edits if tid in self.tracks}
            # an album renamed as a whole keeps its picture, and is found under its new id (Jump back in)
            if len(old_albums) == 1 and len(new_albums) == 1 and old_albums != new_albums:
                old, new = next(iter(old_albums)), next(iter(new_albums))
                self._move_art(old, new)
                self.renamed = {k: (new if v == old else v) for k, v in self.renamed.items()}
                self.renamed[old] = new
                self.renamed = dict(list(self.renamed.items())[-200:])
            await asyncio.to_thread(self._save_after_edit)
        if not changed:
            raise ServiceError(errors[0] if errors else "Nothing was changed")
        return {"changed": changed, "failed": len(errors), "error": errors[0] if errors else "",
                "album_id": next(iter(new_albums)) if len(new_albums) == 1 else ""}

    def _reread(self, tid: str) -> None:
        t = self.tracks[tid]
        try:
            st = os.stat(t["path"])
        except OSError:
            return
        self.tracks[tid] = {"path": t["path"], "mtime": st.st_mtime, "size": st.st_size,
                            "added": t.get("added", t["mtime"]), **read_tags(t["path"])}

    def _save_after_edit(self) -> None:
        self._save_index()
        tmp = self.history_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.history, f)
        os.replace(tmp, self.history_path)

    # --- managing the library: MusicBrainz ---

    def _http(self) -> aiohttp.ClientSession:
        """One connection pool for what the library asks the internet (MusicBrainz, pictures, lyrics),
        made the first time it's needed."""
        if self.http is None:
            self.http = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls()),
                                              timeout=aiohttp.ClientTimeout(total=20))
        return self.http

    def _catalogues(self) -> Catalogues:
        if self.catalogues is None:
            self.catalogues = Catalogues(self._http())
        return self.catalogues

    def _musicbrainz(self) -> MusicBrainz:
        if self.mb is None:
            self.mb = MusicBrainz(self._http())
        return self.mb

    async def lookup(self, album_id: str) -> list[dict[str, Any]]:
        """MusicBrainz releases that may be this album, best first."""
        items = self._target("album", album_id)
        first = items[0][1]
        album = first["album"] or os.path.basename(self._album_folder(first))
        owner = self._album_owner(items)
        artist = "" if owner == VARIOUS else owner
        # MusicBrainz, and the catalogues that know what it may not; one that can't be asked is left out
        found = await asyncio.gather(self._musicbrainz().search(artist, album, len(items)),
                                     self._catalogues().search(artist, album), return_exceptions=True)
        if isinstance(found[0], BaseException) and isinstance(found[1], BaseException):
            raise found[0] if isinstance(found[0], ServiceError) else ServiceError("Couldn't look the album up")
        releases = [{**r, "source": "MusicBrainz"} for r in found[0]] if isinstance(found[0], list) else []
        releases += found[1] if isinstance(found[1], list) else []
        # the album's track count first, then the best matches; MusicBrainz's before the catalogues' on a tie
        return sorted(releases, key=lambda r: (r["tracks"] != len(items), -r["score"], r["source"] != "MusicBrainz"))[:12]

    async def lookup_changes(self, album_id: str, release_id: str) -> dict[str, Any]:
        """What taking a release's details would change: {"release", "album": [[label, old, new]],
        "tracks": [[file, [[label, old, new]]]], "matched"}. Track by track only when the release has as
        many tracks as the album (matched in disc and track order); else the album's details only."""
        plan = await self._lookup_plan(album_id, release_id)
        labels = dict(tagging.FIELDS)
        album = [[labels[f], old, new] for f, (old, new) in plan["album"].items()]
        tracks = [[os.path.basename(self.tracks[tid]["path"]), [[labels[f], old, new] for f, (old, new) in c.items()]]
                  for tid, c in plan["tracks"].items() if c]
        r = plan["release"]
        return {"release": {k: r[k] for k in ("id", "title", "artist", "year", "label")} | {"genre": "; ".join(r["genres"])},
                "album": album, "tracks": tracks, "matched": plan["matched"]}

    async def apply_lookup(self, album_id: str, release_id: str) -> dict[str, Any]:
        """Writes a release's details to the album's files; finds its cover too when the album has none."""
        plan = await self._lookup_plan(album_id, release_id)
        if self.review.pop(album_id, None):
            self._save_review()
        album_changes = {f: new for f, (_, new) in plan["album"].items()}
        edits = [(tid, {**album_changes, **{f: new for f, (_, new) in plan["tracks"].get(tid, {}).items()}})
                 for tid, _ in self._target("album", album_id)]
        edits = [(tid, c) for tid, c in edits if c]
        result = await self._write_tags(edits) if edits else {"changed": 0, "album_id": album_id}
        new_album = result.get("album_id") or album_id
        if new_album in self._albums() and not await self._has_art(new_album):
            release = plan["release"]
            cover = await (self._catalogues().cover(release) if release.get("cover")
                           else self._musicbrainz().cover(release_id, release["group"]))
            if cover:
                self._save_art(new_album, *cover)
                result["art"] = True
        return result

    async def _lookup_plan(self, album_id: str, release_id: str) -> dict[str, Any]:
        items = self._target("album", album_id)
        # a catalogue's album ("itunes:...", "deezer:...") or a MusicBrainz release
        r = await (self._catalogues().release(release_id) if ":" in release_id
                   else self._musicbrainz().release(release_id))
        first = items[0][1]
        wanted = {"album": r["title"], "albumartist": r["artist"], "year": r["year"],
                  "genre": "; ".join(r["genres"]), "label": r["label"]}
        album: dict[str, tuple[str, str]] = {}
        for field, new in wanted.items():
            # only what MusicBrainz knows, and only what differs from every track
            if new and any(self._field_value(t, field) != new for _, t in items):
                album[field] = (self._field_value(first, field), new)
        matched = len(r["tracks"]) == len(items)
        tracks: dict[str, dict[str, tuple[str, str]]] = {}
        if matched:
            for (tid, t), rt in zip(items, r["tracks"]):
                change = {}
                for field, new in (("title", rt["title"]), ("artist", rt["artist"]), ("track", str(rt["track"])),
                                   ("disc", str(rt["disc"]) if r["discs"] > 1 else "")):
                    if new and self._field_value(t, field) != new:
                        change[field] = (self._field_value(t, field), new)
                tracks[tid] = change
        return {"release": r, "album": album, "tracks": tracks, "matched": matched}

    # --- managing the library: artwork ---

    async def _has_art(self, album_id: str) -> bool:
        """The album has a picture: its own, a cover file in its folder, or one in its first file."""
        if album_id in self.album_art:
            return True
        first = self._albums()[album_id][0][1]
        directory = os.path.dirname(first["path"])
        if directory not in self.folder_covers:
            self.folder_covers[directory] = await asyncio.to_thread(find_folder_cover, directory)
        if self.folder_covers[directory]:
            return True
        return await asyncio.to_thread(extract_embedded_art, first["path"]) is not None

    def _save_art(self, album_id: str, data: bytes, ext: str) -> None:
        os.makedirs(self.art_dir, exist_ok=True)
        self._remove_art(album_id)
        name = album_id + (".jpg" if ext == ".jpeg" else ext)
        tmp = os.path.join(self.art_dir, name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(self.art_dir, name))
        self.album_art[album_id] = name
        self._browse_cache = None
        self.forget_browse_info()

    def _remove_art(self, album_id: str) -> None:
        name = self.album_art.pop(album_id, None)
        self._browse_cache = None
        self.forget_browse_info()
        if name:
            try:
                os.remove(os.path.join(self.art_dir, name))
            except OSError:
                pass

    def _move_art(self, old: str, new: str) -> None:
        name = self.album_art.get(old)
        if not name or new in self.album_art:
            return
        target = new + os.path.splitext(name)[1]
        try:
            os.replace(os.path.join(self.art_dir, name), os.path.join(self.art_dir, target))
        except OSError:
            return
        del self.album_art[old]
        self.album_art[new] = target

    @staticmethod
    async def _read_image(image_path: str) -> tuple[bytes, str]:
        """A picture the user picked: (bytes, extension). A copy is kept, so the original can move."""
        ext = os.path.splitext(image_path)[1].lower()
        if ext not in ART_TYPES:
            raise ServiceError("Pick a JPG, PNG, WebP or GIF image")
        try:
            if os.path.getsize(image_path) > MAX_ART_BYTES:
                raise ServiceError("That image is too large (15 MB max)")
            return await asyncio.to_thread(lambda: open(image_path, "rb").read()), ext
        except OSError as e:
            raise ServiceError(f"Couldn't read the image: {e.strerror or e}")

    async def set_album_art(self, album_id: str, image_path: str) -> None:
        """A picture the user picked becomes the album's own."""
        self._target("album", album_id)
        self._save_art(album_id, *await self._read_image(image_path))

    async def clear_album_art(self, album_id: str) -> None:
        """Back to the files' own picture (or none)."""
        self._remove_art(album_id)

    async def find_album_art(self, album_id: str) -> bool:
        """The album's cover from the Cover Art Archive, by its name and artist."""
        items = self._target("album", album_id)
        first = items[0][1]
        if not first["album"]:
            raise ServiceError("Name the album first (Edit tags) to look for its cover")
        owner = self._album_owner(items)
        artist = "" if owner == VARIOUS else owner
        # the Cover Art Archive first (its covers are the exact release's), then the catalogues
        cover = await self._musicbrainz().find_cover(artist, first["album"])             or await self._catalogues().find_cover(artist, first["album"])
        if not cover:
            return False
        self._save_art(album_id, *cover)
        return True

    # --- managing the library: artists' pictures ---

    def _artist_name(self, artist_id: str) -> str:
        found = self._artist_index().get(artist_id)
        if found:
            return found[0]
        # an artist with songs on other people's albums only
        name = next((t["artist"] for t in self.tracks.values() if _hash((t["artist"] or "").lower()) == artist_id), None)
        if not name:
            raise ServiceError("That artist is no longer in the library")
        return name

    async def set_artist_art(self, artist_id: str, image_path: str) -> None:
        self._artist_name(artist_id)
        self._save_art(ARTIST + artist_id, *await self._read_image(image_path))

    async def clear_artist_art(self, artist_id: str) -> None:
        self._remove_art(ARTIST + artist_id)

    async def find_artist_art(self, artist_id: str) -> bool:
        """The artist's picture from Deezer's catalogue, for the artist of exactly this name."""
        name = self._artist_name(artist_id)
        if name in (VARIOUS, UNKNOWN):
            raise ServiceError("Name the album artist first (Edit tags) to look for their picture")
        picture = await pictures.artist_picture(self._http(), name)
        if not picture:
            return False
        self._save_art(ARTIST + artist_id, *picture)
        return True

    def find_missing_art(self) -> None:
        """Looks for a cover for every named album, and a picture for every named artist, without one, in
        the background (status "art")."""
        if self.art_task and not self.art_task.done():
            return
        self.art_progress.update(running=True, done=0, total=0, found=0)
        self.art_task = spawn(self._find_missing_art(), "finding missing artwork")

    async def _find_missing_art(self) -> None:
        try:
            missing = [k for k, v in list(self._albums().items()) if v[0][1]["album"] and not await self._has_art(k)]
            artists = [k for k, (name, _) in self._artist_index().items()
                       if name not in (VARIOUS, UNKNOWN) and ARTIST + k not in self.album_art]
            self.art_progress["total"] = len(missing) + len(artists)
            for key in missing:
                if key in self._albums():
                    try:
                        if await self.find_album_art(key):
                            self.art_progress["found"] += 1
                    except ServiceError:
                        pass  # one album MusicBrainz can't answer for doesn't stop the rest
                self.art_progress["done"] += 1
            for key in artists:
                try:
                    if await self.find_artist_art(key):
                        self.art_progress["found"] += 1
                except ServiceError:
                    pass
                self.art_progress["done"] += 1
        finally:
            self.art_progress["running"] = False

    # --- managing the library: lyrics ---

    @staticmethod
    def _sidecar(t: dict[str, Any]) -> str:
        return os.path.splitext(t["path"])[0] + ".lrc"

    def _kept_lyrics(self, tid: str) -> str:
        """Where MusicDeck keeps a track's lyrics when they aren't beside the songs."""
        return os.path.join(self.lyrics_dir, tid + ".lrc")

    def _read_kept(self, tid: str) -> Optional[dict[str, Any]]:
        try:
            with open(self._kept_lyrics(tid), "r", encoding="utf-8") as f:
                return lyrics.parse_lrc(f.read(), "Saved lyrics")
        except OSError:
            return None

    async def _save_lyrics(self, tid: str) -> Optional[bool]:
        """Looks a track's lyrics up and keeps them (in MusicDeck, or as an .lrc beside the song, as the
        source is set): True when saved, False when the sites have none, None when it has lyrics already
        (a file beside it, its tags, or kept by MusicDeck)."""
        t = self.tracks[tid]
        if await asyncio.to_thread(read_lyrics, t["path"]) or os.path.exists(self._kept_lyrics(tid)):
            return None
        found = await lyrics.lookup(self._http(), self._track(tid, t))
        if not found:
            return False
        target = self._sidecar(t) if self.option("lyrics_place") == "beside" else self._kept_lyrics(tid)
        await asyncio.to_thread(_write_text, target, _lrc(found, t))
        return True

    async def save_lyrics(self, track_id: str) -> bool:
        """The lyrics the lyrics sites have for a track, kept beside it as an .lrc (synced when they
        are): they then work offline, and in other players."""
        if track_id not in self.tracks:
            raise ServiceError("That's no longer in the library")
        saved = await self._save_lyrics(track_id)
        if saved is None:
            raise ServiceError("This track has lyrics already")
        return saved

    async def find_lyrics(self, album_id: str) -> dict[str, int]:
        """Lyrics for every track of an album that has none: {"found", "total"} (the tracks without)."""
        found = total = 0
        for tid, _ in self._target("album", album_id):
            try:
                saved = await self._save_lyrics(tid)
            except ServiceError:
                saved = False
            if saved is None:
                continue
            total += 1
            found += saved
            await asyncio.sleep(LYRICS_PACE)
        return {"found": found, "total": total}

    def find_missing_lyrics(self) -> None:
        """Lyrics for every track without, in the background (status "lyrics")."""
        if self.lyrics_task and not self.lyrics_task.done():
            return
        self.lyrics_progress.update(running=True, done=0, total=len(self.tracks), found=0)
        self.lyrics_task = spawn(self._find_missing_lyrics(), "finding missing lyrics")

    async def _find_missing_lyrics(self) -> None:
        try:
            for tid in list(self.tracks):
                if tid in self.tracks:
                    try:
                        saved = await self._save_lyrics(tid)
                    except ServiceError:
                        saved = False  # one the sites can't answer for doesn't stop the rest
                    if saved:
                        self.lyrics_progress["found"] += 1
                    if saved is not None:
                        await asyncio.sleep(LYRICS_PACE)
                self.lyrics_progress["done"] += 1
        finally:
            self.lyrics_progress["running"] = False

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        t = self.tracks.get(track_id)
        if not t or not os.path.isfile(t["path"]):
            raise ServiceError("Track not found")
        return StreamSource(path=t["path"])

    async def art(self, art_id: str) -> StreamSource:
        kind, _, tid = art_id.partition(":")
        if kind == "a":  # an artist's picture
            own = self.album_art.get(ARTIST + tid.partition(".")[0])
            if not own or not os.path.isfile(os.path.join(self.art_dir, own)):
                raise ServiceError("Art not found")
            return StreamSource(path=os.path.join(self.art_dir, own))
        t = self.tracks.get(tid.partition(".")[0]) if kind == "t" else None
        if not t:
            raise ServiceError("Art not found")
        tid = tid.partition(".")[0]
        own = self.album_art.get(self._album_key(t))
        if own and os.path.isfile(os.path.join(self.art_dir, own)):
            return StreamSource(path=os.path.join(self.art_dir, own))
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
        self._browse_cache = None
        self.forget_browse_info()
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
        self._browse_cache = None
        self.forget_browse_info()
        tmp = self.plays_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.plays, f)
        os.replace(tmp, self.plays_path)

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        """The song's own (an .lrc beside it, then its tags), then those MusicDeck kept for it."""
        t = self.tracks.get(track["id"])
        if not t:
            return None
        return await asyncio.to_thread(read_lyrics, t["path"]) or await asyncio.to_thread(self._read_kept, track["id"])

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
