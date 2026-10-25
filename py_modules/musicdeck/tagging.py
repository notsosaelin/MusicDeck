"""Writing tags to music files, for the local source's tag editing and MusicBrainz look-ups.

Edits name fields as the editor shows them (`FIELDS`: title, artist, album, albumartist, track, disc,
year, genre, composer, label); they become each format's own keys (`raw_changes`), the names mutagen's
"easy" interfaces use (ID3 frames for the raw ID3 inside WAV/AIFF). What a key held before an edit is
returned by `write`, so the edit can be undone exactly (`write_raw` puts it back).

A file is never written in place: the change is made to a copy beside it, which then replaces the
original in one step, so a crash or a full card leaves the old file, not half a new one. The copy is a
dot-file the library scan skips.
"""
import os
import shutil
import time
from typing import Any, Optional

try:
    import mutagen
    from mutagen import id3
    from mutagen.easymp4 import EasyMP4Tags

    # EasyMP4 knows neither: the composer atom, and the label as iTunes writes it
    EasyMP4Tags.RegisterTextKey("composer", "\xa9wrt")
    EasyMP4Tags.RegisterFreeformKey("organization", "LABEL")
    # and a rating where write_rating puts it (0-100), so reading finds it
    EasyMP4Tags.RegisterFreeformKey("rating", "RATING")
except ImportError:  # vendored into py_modules by scripts/vendor.py
    mutagen = None

#: What the editor offers, in its order: (field, label).
FIELDS = (("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("albumartist", "Album artist"),
          ("track", "Track"), ("disc", "Disc"), ("year", "Year"), ("genre", "Genre"),
          ("composer", "Composer"), ("label", "Label"))
#: The fields an album's edit offers: what every track of an album shares.
ALBUM_FIELDS = ("album", "albumartist", "year", "genre", "label")
#: Field -> the key it's written under ("year" also updates an "originaldate" a file has).
KEYS = {"title": "title", "artist": "artist", "album": "album", "albumartist": "albumartist",
        "track": "tracknumber", "disc": "discnumber", "year": "date", "genre": "genre",
        "composer": "composer", "label": "organization"}
# the raw ID3 frames for each key, for ID3 that easy=True doesn't wrap (WAV, AIFF)
ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "tracknumber": "TRCK",
              "discnumber": "TPOS", "date": "TDRC", "originaldate": "TDOR", "genre": "TCON",
              "composer": "TCOM", "organization": "TPUB"}
MAX_TEXT = 300
TEMP_SUFFIX = ".musicdeck-tmp"


class TagError(Exception):
    """A tag edit that couldn't be made, said so the user can act on it."""


def clean(changes: Any, allowed: tuple[str, ...] = tuple(f for f, _ in FIELDS)) -> dict[str, Any]:
    """The edits that can be written: known fields, sensible values. Numbers are 0-999 (0 clears);
    the genre may be several, separated by ";"."""
    if not isinstance(changes, dict):
        raise TagError("Nothing to change")
    out: dict[str, Any] = {}
    for field, value in changes.items():
        if field not in allowed:
            continue
        if field in ("track", "disc"):
            try:
                number = int(str(value).strip() or 0)
            except ValueError:
                raise TagError(f"The {field} number has to be a number")
            if not 0 <= number <= 999:
                raise TagError(f"The {field} number has to be between 0 and 999")
            out[field] = number
        elif field == "year":
            year = str(value).strip()
            if year and not (year.isdigit() and len(year) == 4):
                raise TagError("The year has to be four digits")
            out[field] = year
        elif isinstance(value, str):
            out[field] = value.strip()[:MAX_TEXT]
    return out


def _values(field: str, value: Any, old: Optional[list[str]]) -> Optional[list[str]]:
    """A field's new value as the strings to store, or None to remove the key."""
    if field in ("track", "disc"):
        if not value:
            return None
        # keep "of 12" when the file says how many there are
        total = (old or [""])[0].partition("/")[2]
        return [f"{value}/{total}" if total else str(value)]
    if field == "genre":
        genres = [g.strip() for g in str(value).split(";") if g.strip()]
        return genres or None
    return [value] if value else None


def _open(path: str, easy: bool = True) -> Any:
    if mutagen is None:
        raise TagError("Tag editing isn't available (mutagen is missing)")
    try:
        audio = mutagen.File(path, easy=easy)
    except Exception as e:
        raise TagError(f"Couldn't read the file's tags: {e}")
    if audio is None:
        raise TagError("MusicDeck can't write tags to this kind of file")
    if audio.tags is None:
        audio.add_tags()
    return audio


def _read_key(tags: Any, key: str) -> Optional[list[str]]:
    try:
        if hasattr(tags, "getall"):  # raw ID3
            frame = tags.get(ID3_FRAMES[key])
            return [str(t) for t in frame.text] if frame is not None else None
        value = tags.get(key)
    except Exception:  # a key the format's easy interface doesn't know
        return None
    if value is None:
        return None
    return [str(v) for v in (value if isinstance(value, list) else [value])]


def _write_key(tags: Any, key: str, values: Optional[list[str]]) -> None:
    if hasattr(tags, "getall"):
        frame_id = ID3_FRAMES[key]
        tags.delall(frame_id)
        if values:
            tags.add(getattr(id3, frame_id)(encoding=3, text=values))
        return
    if values:
        tags[key] = values
    elif key in tags:
        del tags[key]


def _replace(temp: str, path: str) -> None:
    """The edited copy in the file's place. Windows refuses while another program has the file open (the
    stream server sending it to the player): a few tries, then a reason."""
    for attempt in range(6):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if os.name != "nt":
                raise TagError("MusicDeck isn't allowed to write in this folder")
            if attempt == 5:
                raise TagError("The file is in use; try again when it isn't playing")
            time.sleep(0.25)


def _save_atomically(path: str, change: Any, easy: bool = True) -> None:
    """Runs change(audio) on a copy of the file, saves it, then puts the copy in the file's place."""
    directory, name = os.path.split(path)
    temp = os.path.join(directory, f".{name}{TEMP_SUFFIX}{os.path.splitext(name)[1]}")
    try:
        shutil.copy2(path, temp)  # the mode comes along
    except PermissionError:
        raise TagError("MusicDeck isn't allowed to write in this folder")
    except OSError as e:
        raise TagError(f"Couldn't copy the file to edit it: {e.strerror or e}")
    try:
        audio = _open(temp, easy)
        change(audio)
        try:
            audio.save()
        except Exception as e:
            raise TagError(f"Couldn't save the tags: {e}")
        _replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def write(path: str, changes: dict[str, Any]) -> dict[str, Optional[list[str]]]:
    """Writes `changes` (from clean()); returns what each key changed held before, for undoing."""
    before: dict[str, Optional[list[str]]] = {}

    def change(audio: Any) -> None:
        tags = audio.tags
        for field, value in changes.items():
            key = KEYS[field]
            old = _read_key(tags, key)
            before[key] = old
            _write_key(tags, key, _values(field, value, old))
            if field == "year":
                # the year shown is the original one when a file has it: that's the one to change too
                original = _read_key(tags, "originaldate")
                if original:
                    before["originaldate"] = original
                    _write_key(tags, "originaldate", [value] if value else None)

    _save_atomically(path, change)
    return before


def write_raw(path: str, keys: dict[str, Optional[list[str]]]) -> None:
    """Puts keys back as they were (what write() returned)."""
    def change(audio: Any) -> None:
        for key, values in keys.items():
            if key in ID3_FRAMES:
                _write_key(audio.tags, key, values)

    _save_atomically(path, change)


#: one to five stars as POPM 0-255, as Windows, MusicBee and foobar2000 write them
POPM_STARS = {1: 1, 2: 64, 3: 128, 4: 196, 5: 255}
POPM_EMAIL = "Windows Media Player 9 Series"


def write_rating(path: str, stars: int) -> None:
    """A 0-5 star rating (0 removes it) where other players look for it: ID3's POPM (MP3, and the ID3 inside
    WAV/AIFF), a RATING of 0-100 in Vorbis comments (FLAC, Ogg, Opus), a RATING freeform atom in MP4. Other
    formats raise TagError: their rating is kept by MusicDeck instead."""
    if mutagen is None:
        raise TagError("Tag editing isn't available (mutagen is missing)")
    stars = max(0, min(5, int(stars)))

    def change(audio: Any) -> None:
        tags = audio.tags
        if hasattr(tags, "getall"):  # ID3
            count = next((f.count for f in tags.getall("POPM")), 0)
            tags.delall("POPM")
            if stars:
                tags.add(id3.POPM(email=POPM_EMAIL, rating=POPM_STARS[stars], count=count))
            return
        kind = type(audio).__name__
        if kind in ("FLAC", "OggVorbis", "OggOpus", "OggFLAC", "OggSpeex"):
            for key in [k for k in tags.keys() if k.lower() in ("rating", "fmps_rating")]:
                del tags[key]
            if stars:
                tags["RATING"] = [str(stars * 20)]
            return
        if kind == "MP4":
            atom = "----:com.apple.iTunes:RATING"
            if stars:
                tags[atom] = [str(stars * 20).encode()]
            elif atom in tags:
                del tags[atom]
            return
        raise TagError("This kind of file can't hold a rating")

    _save_atomically(path, change, easy=False)


def write_replaygain(path: str, track_gain: float, track_peak: float, album_gain: float, album_peak: float) -> None:
    """ReplayGain tags as players look for them: TXXX frames in ID3, REPLAYGAIN_* Vorbis comments, freeform
    atoms in MP4. Other formats raise TagError."""
    if mutagen is None:
        raise TagError("Tag editing isn't available (mutagen is missing)")
    values = {"REPLAYGAIN_TRACK_GAIN": f"{track_gain:+.2f} dB", "REPLAYGAIN_TRACK_PEAK": f"{track_peak:.6f}",
              "REPLAYGAIN_ALBUM_GAIN": f"{album_gain:+.2f} dB", "REPLAYGAIN_ALBUM_PEAK": f"{album_peak:.6f}"}

    def change(audio: Any) -> None:
        tags = audio.tags
        if hasattr(tags, "getall"):  # ID3
            for key, value in values.items():
                tags.delall(f"TXXX:{key}")
                tags.delall(f"TXXX:{key.lower()}")
                tags.add(id3.TXXX(encoding=3, desc=key, text=[value]))
            return
        kind = type(audio).__name__
        if kind in ("FLAC", "OggVorbis", "OggOpus", "OggFLAC", "OggSpeex"):
            for key, value in values.items():
                tags[key] = [value]
            return
        if kind == "MP4":
            for key, value in values.items():
                tags[f"----:com.apple.iTunes:{key.lower()}"] = [value.encode()]
            return
        raise TagError("This kind of file can't hold loudness tags")

    _save_atomically(path, change, easy=False)


def is_temp(name: str) -> bool:
    """A copy being edited (or one a crash left): never part of the library."""
    return TEMP_SUFFIX in name
