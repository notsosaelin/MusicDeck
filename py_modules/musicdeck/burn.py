"""
Burning audio CDs: songs made into CD audio (ffmpeg) and written by cdrskin (libburn's burner, GPL-2.0+,
bundled as bin/<platform>/cdrskin, or the system's), as Brasero or K3b would.

* The disc: `cdrskin -atip` says whether there's one, whether it's blank (a CD-R, or a CD-RW, which can
  be erased first: blank=fast), and how much it holds (ATIP's start of lead-out, in 1/75 s frames).
* The songs: 44.1 kHz 16-bit stereo WAV, each padded with silence to a whole number of CD frames (2352
  bytes), so the track sizes are known before writing, as Session At Once needs.
* Burned Session At Once (-sao): tracks one after another with no gaps added between them (a live
  album stays seamless), and CD-Text (the album's and each song's title and artist) from a Sony input
  sheet (input_sheet_v07t=), which CD players and cars show. Latin-1 only (CD-Text's 8859 code).
* While burning, the Deck doesn't sleep (systemd-inhibit). Progress is cdrskin's own "Track 03: 12 of 45
  MB written" lines.

The drive is /dev/sr*, the user's through uaccess, as for reading CDs (cd.py). Linux only.
"""
import os
import re
import shutil
import struct
import subprocess
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import native

FRAME = 2352  # bytes of a CD frame (1/75 s)
FRAMES_PER_SECOND = 75
LEAD_IN_GAP = 2 * FRAMES_PER_SECOND  # the 2 s before the first track every CD has
CD_80 = 359_849  # an 80-minute CD-R's frames, when the disc doesn't say
CDTEXT_FIELD = 80  # characters kept of a CD-Text title (players show a line's worth)


@dataclass
class Disc:
    status: str  # "blank", "full", "appendable", "empty" (no disc), "unsuitable" (a DVD...), "unknown"
    media: str = ""  # "CD-R", "CD-RW"...
    erasable: bool = False
    frames: int = 0  # what it holds, in frames (0 when it doesn't say)

    @property
    def usable(self) -> bool:
        """Whether music can be burned on it as it is, or once erased."""
        return self.media.startswith("CD") and (self.status == "blank" or (self.erasable and self.status != "empty"))

    @property
    def minutes(self) -> int:
        return (self.frames or CD_80) // FRAMES_PER_SECOND // 60


def burner(runtime_dir: Optional[str] = None) -> Optional[str]:
    """cdrskin: MusicDeck's own (bin/<platform>), else the system's; None off Linux or without one."""
    if not native.platform_id().startswith("linux"):
        return None
    return native.helper("cdrskin", runtime_dir) or shutil.which("cdrskin")


STATUSES = {"burn_disc_blank": "blank", "burn_disc_full": "full", "burn_disc_appendable": "appendable",
            "burn_disc_empty": "empty", "burn_disc_unsuitable": "unsuitable"}


def parse_atip(text: str) -> Disc:
    """What `cdrskin dev=... -atip` printed."""
    m = re.search(r"cdrskin: status \d+ (\w+)", text)
    status = STATUSES.get(m.group(1).lower(), "unknown") if m else "unknown"
    media = ""
    m = re.search(r"^Current:\s*(.+)$", text, re.M) or re.search(r"Mounted media type:\s*(.+)$", text, re.M)
    if m and m.group(1).strip().lower() != "none":
        media = m.group(1).strip()
    erasable = bool(re.search(r"^\s*Is erasable", text, re.M)) or "Disk Is erasable" in text
    m = re.search(r"ATIP start of lead out:\s*(\d+)", text)
    frames = int(m.group(1)) if m else 0
    if status == "empty":
        media = ""
    return Disc(status, media, erasable, frames)


def probe(cdrskin: str, drive: str) -> Disc:
    """The disc in a drive, as cdrskin sees it."""
    try:
        result = subprocess.run([cdrskin, f"dev={drive}", "-atip"], capture_output=True, text=True, timeout=60,
                                errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return Disc("unknown")
    return parse_atip(result.stdout + "\n" + result.stderr)


def frames_needed(durations: list[float]) -> int:
    """The frames a list of songs takes on a CD (each rounded up to a whole frame), with the lead-in gap."""
    return LEAD_IN_GAP + sum(-(-round(d * 44100 * 4) // FRAME) for d in durations)


# letters and marks Unicode doesn't take apart into a Latin-1 one and an accent
PLAIN = {"Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Œ": "OE", "œ": "oe", "ı": "i", "‘": "'", "’": "'", "‚": "'",
         "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "…": "...", "•": "-", "€": "EUR"}


def latin1(text: str, limit: int = CDTEXT_FIELD) -> str:
    """Text as CD-Text's Latin-1 holds it: accents kept where Latin-1 has them, else dropped ("ź" → "z",
    "Ł" → "L"), anything else left out; one line, cut to `limit`."""
    out = []
    for ch in " ".join(str(text or "").split()):
        if ord(ch) < 256:
            out.append(ch)
            continue
        if ch in PLAIN:
            out.append(PLAIN[ch])
            continue
        base = unicodedata.normalize("NFKD", ch)
        out.append("".join(c for c in base if ord(c) < 256 and not unicodedata.combining(c)))
    return "".join(out).strip()[:limit]


def cdtext_sheet(album: str, artist: str, tracks: list[dict[str, Any]]) -> str:
    """A Sony input sheet (0.7T) of the disc's CD-Text: the album, its artist, each song's title and artist."""
    lines = ["Input Sheet Version = 0.7T", "Text Code = 8859", "Language Code = English",
             f"Album Title = {latin1(album)}", f"Artist Name = {latin1(artist)}",
             "First Track Number = 1", f"Last Track Number = {len(tracks)}"]
    for n, t in enumerate(tracks, 1):
        lines.append(f"Track {n:02d} Title = {latin1(t.get('title') or f'Track {n}')}")
        lines.append(f"Track {n:02d} Artist = {latin1(t.get('artist') or artist)}")
    return "\n".join(lines) + "\n"


def to_cd_audio(ffmpeg: str, source: str, dest: str) -> int:
    """A song as CD audio: a plain 44-byte-header WAV of 44.1 kHz 16-bit stereo, padded with silence to a
    whole number of frames. Returns its frames."""
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-map", "0:a:0",
                             "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact", "-ar", "44100",
                             "-ac", "2", "-c:a", "pcm_s16le", "-f", "wav", dest], capture_output=True, text=True,
                            errors="replace", **native.NO_WINDOW)
    if result.returncode != 0 or not os.path.isfile(dest):
        raise BurnError(f"Couldn't make {os.path.basename(source)} into CD audio: {result.stderr.strip()[-200:]}")
    with open(dest, "r+b") as f:
        head = f.read(44)
        if head[:4] != b"RIFF" or head[8:16] != b"WAVEfmt " or head[36:40] != b"data":
            raise BurnError(f"Couldn't make {os.path.basename(source)} into CD audio")
        size = os.path.getsize(dest) - 44
        padded = -(-size // FRAME) * FRAME
        f.seek(0, os.SEEK_END)
        f.write(bytes(padded - size))
        f.seek(4)
        f.write(struct.pack("<I", 36 + padded))
        f.seek(40)
        f.write(struct.pack("<I", padded))
    return padded // FRAME


class BurnError(Exception):
    pass


PROGRESS = re.compile(r"Track (\d+):\s+(\d+)(?:\s+of\s+(\d+))? MB written")


def parse_progress(line: str) -> Optional[tuple[int, int, int]]:
    """(track, MB written, MB in all, or 0) from a cdrskin progress line."""
    m = PROGRESS.search(line)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def _inhibited(command: list[str], why: str) -> list[str]:
    """The command run so the Deck doesn't sleep meanwhile (systemd-inhibit, where there is one)."""
    inhibit = shutil.which("systemd-inhibit")
    if not inhibit:
        return command
    return [inhibit, "--what=sleep:idle:handle-lid-switch", "--who=MusicDeck", f"--why={why}", "--mode=block",
            *command]


def _run(command: list[str], on_line: Callable[[str], None]) -> tuple[int, str]:
    """Runs cdrskin, each output line (its progress lines end in \\r) to on_line; (exit code, last lines)."""
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **native.NO_WINDOW)
    assert proc.stdout
    tail: list[str] = []
    buf = b""
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                on_line(line)
                tail = (tail + [line])[-12:]
    if buf.strip():
        tail.append(buf.decode("utf-8", "replace").strip())
    return proc.wait(), "\n".join(tail)


def _failure(output: str) -> str:
    """cdrskin's own reason (its FATAL or SORRY line), cut to a sentence."""
    for line in reversed(output.splitlines()):
        m = re.search(r"(?:FATAL|SORRY)\s*:\s*(.+)", line)
        if m:
            return m.group(1).strip()[:200]
    return (output.strip().splitlines() or ["cdrskin stopped"])[-1][:200]


def erase(cdrskin: str, drive: str) -> None:
    """Erases a CD-RW (blank=fast: its table of contents, a minute or so)."""
    code, out = _run(_inhibited([cdrskin, "-v", f"dev={drive}", "gracetime=0", "blank=fast"], "Erasing a CD"),
                     lambda line: None)
    if code != 0:
        raise BurnError(f"Couldn't erase the CD: {_failure(out)}")


def burn(cdrskin: str, drive: str, wavs: list[str], sheet: Optional[str],
         progress: Callable[[int, int, int], None]) -> None:
    """Writes the WAVs as an audio CD (Session At Once, CD-Text from `sheet`), then ejects it."""
    command = [cdrskin, "-v", f"dev={drive}", "gracetime=0", "-sao", "-eject"]
    if sheet:
        command.append(f"input_sheet_v07t={sheet}")
    command += ["-audio", *wavs]

    def on_line(line: str) -> None:
        found = parse_progress(line)
        if found:
            progress(*found)
    code, out = _run(_inhibited(command, "Burning a CD"), on_line)
    if code != 0:
        raise BurnError(f"Burning stopped: {_failure(out)}")
