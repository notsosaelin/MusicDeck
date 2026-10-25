"""
Converting songs for a phone, a USB stick or another player (Local files' Convert): MP3, AAC (M4A), Opus or FLAC,
made by ffmpeg with the tags (and, but for Opus, the cover) kept, into Documents > MusicDeck converted > <format> >
<album artist> > <album> > "NN Title.ext". A song of a CUE sheet is converted from its slice of the file.
"""
import os
import shutil
import subprocess
from typing import Any, Optional

from .organize import safe

FORMATS = {
    "mp3": {"ext": ".mp3", "label": "MP3 320 kbps", "args": ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3"], "cover": True},
    "aac": {"ext": ".m4a", "label": "AAC 256 kbps", "args": ["-c:a", "aac", "-b:a", "256k"], "cover": True},
    "opus": {"ext": ".opus", "label": "Opus 160 kbps", "args": ["-c:a", "libopus", "-b:a", "160k"], "cover": False},
    "flac": {"ext": ".flac", "label": "FLAC", "args": ["-c:a", "flac"], "cover": True},
}


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def target(root: str, fmt: str, t: dict[str, Any]) -> str:
    album_artist = t.get("albumartist") or t.get("artist") or "Unknown artist"
    name = (f"{t['track']:02d} " if t.get("track") else "") + (t.get("title") or os.path.splitext(os.path.basename(t["path"]))[0])
    return os.path.join(root, FORMATS[fmt]["label"].split(" ")[0], safe(album_artist), safe(t.get("album") or "Unknown album"),
                        safe(name) + FORMATS[fmt]["ext"])


def convert(t: dict[str, Any], fmt: str, out: str, timeout: float = 900) -> Optional[str]:
    """One song into `out`; None when done, else why not."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "Converting needs ffmpeg, which isn't installed"
    spec = FORMATS[fmt]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    span = t.get("cue")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if span:
        cmd += ["-ss", f"{span[0]:.3f}", "-to", f"{span[1]:.3f}"]
    cmd += ["-i", t["path"], "-map", "0:a:0"]
    if spec["cover"] and not span:
        cmd += ["-map", "0:v:0?", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    cmd += spec["args"] + ["-map_metadata", "0"]
    # the song's own names (a CUE song's come from its sheet, not the file's tags)
    for key, value in (("title", t.get("title")), ("artist", t.get("artist")), ("album_artist", t.get("albumartist")),
                       ("album", t.get("album")), ("track", str(t.get("track") or "")), ("date", t.get("year"))):
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    tmp = out + ".part" + spec["ext"]
    try:
        result = subprocess.run(cmd + [tmp], capture_output=True, text=True, timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if result.returncode != 0 or not os.path.exists(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        return (result.stderr.strip().splitlines() or ["ffmpeg failed"])[-1][:200]
    os.replace(tmp, out)
    return None
