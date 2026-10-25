"""
CUE sheets: a whole album in one file (a rip's .flac, .ape or .wav) with a .cue beside it saying where each song
starts. The library shows the songs, each a slice of the file (LocalService: "cue" [start, end] in seconds), played
by cutting that slice out (ffmpeg, once, kept like converted files).

  TITLE / PERFORMER  the album's (before the first TRACK) or a song's
  FILE "name" WAVE   the audio file the songs that follow are in
  TRACK 01 AUDIO     a song; its INDEX 01 mm:ss:ff is where it starts (75 frames a second)

Read as UTF-8 (with or without a BOM), else Windows-1252, which most rippers wrote.
"""
import os
import re
import shlex
from typing import Any, Optional

MAX_BYTES = 256 * 1024


def read(path: str) -> Optional[dict[str, Any]]:
    """{"title", "performer", "date", "genre", "files": [{"file": absolute path, "tracks": [{"n", "title",
    "performer", "start"}]}]}, or None when it isn't a cue sheet with songs."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    sheet: dict[str, Any] = {"title": "", "performer": "", "date": "", "genre": "", "files": []}
    current_file: Optional[dict[str, Any]] = None
    song: Optional[dict[str, Any]] = None
    for line in text.splitlines():
        try:
            words = shlex.split(line.strip(), posix=True)
        except ValueError:
            words = line.strip().split()
        if not words:
            continue
        key, rest = words[0].upper(), words[1:]
        if key == "FILE" and rest:
            current_file = {"file": os.path.join(os.path.dirname(path), rest[0]), "tracks": []}
            sheet["files"].append(current_file)
            song = None
        elif key == "TRACK" and current_file is not None and rest:
            song = {"n": int(re.sub(r"\D", "", rest[0]) or 0), "title": "", "performer": "", "start": None}
            current_file["tracks"].append(song)
        elif key in ("TITLE", "PERFORMER") and rest:
            (song if song is not None else sheet)[key.lower()] = rest[0]
        elif key == "INDEX" and song is not None and len(rest) >= 2 and rest[0] == "01":
            song["start"] = timestamp(rest[1])
        elif key == "REM" and len(rest) >= 2 and song is None and rest[0].upper() in ("DATE", "GENRE"):
            sheet[rest[0].lower()] = rest[1]
    for f in sheet["files"]:
        f["tracks"] = [t for t in f["tracks"] if t["start"] is not None]
    sheet["files"] = [f for f in sheet["files"] if f["tracks"]]
    return sheet if sheet["files"] else None


def timestamp(text: str) -> Optional[float]:
    """"mm:ss:ff" (75 frames a second) in seconds."""
    parts = text.split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    minutes, seconds, frames = (int(p) for p in parts)
    return minutes * 60 + seconds + frames / 75


def songs(sheet: dict[str, Any], audio: str, length: float) -> list[dict[str, Any]]:
    """The songs of `audio` (one of the sheet's files, by path) as {"n", "title", "performer", "start", "end"}: each
    ends where the next starts; the last at the file's end."""
    entry = next((f for f in sheet["files"] if os.path.normcase(os.path.normpath(f["file"])) == os.path.normcase(os.path.normpath(audio))), None)
    if not entry:
        return []
    tracks = sorted(entry["tracks"], key=lambda t: t["start"])
    out = []
    for i, t in enumerate(tracks):
        end = tracks[i + 1]["start"] if i + 1 < len(tracks) else length
        if end and end > t["start"]:
            out.append({**t, "end": end})
    return out
