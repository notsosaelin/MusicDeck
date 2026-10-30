"""
Audio CD images (a .cue sheet and its files) in a drive of their own: for trying the audio CD source without a drive,
everything past the drive itself as it is with a disc (the device appearing, MusicBrainz naming it, playing, seeking,
ripping). A developer inserts one (Plugin.virtual_cd) into a virtual drive, which then stays as a drive plugged in
would (empty after ejecting) until it's removed; nothing in MusicDeck offers it.

cd.py asks this module for a drive whose name is an image's path: its table of contents in the drive's terms (LBA, the
first track at 0, the lead-out last), and its raw frames (2352 bytes of 16-bit stereo 44.1 kHz little-endian PCM).

The sheet: FILE lines of type BINARY (raw PCM, as a drive gives it), MOTOROLA (the same, big-endian) or WAVE (a WAV of
CD audio); TRACK nn AUDIO, or a data mode (MODE1/2352...), a data track; INDEX 01 mm:ss:ff where a track starts in its
file (INDEX 00, its pregap, and PREGAP, silence not in the file, count before it as a real disc's do).
"""
import os
import re
import struct
import threading
from typing import Optional

FRAME = 2352
LEADOUT = 0xAA
#: the virtual drive's name, as cd.py's functions are given it ("/dev/sr0" for a real one)
DRIVE = "virtual:cd0"
#: whether the virtual drive is there, and the image in it (None: empty, its tray open)
_present = False
_image: Optional["CueImage"] = None
_lock = threading.Lock()


def _msf(text: str) -> int:
    m, s, f = (int(x) for x in text.split(":"))
    return (m * 60 + s) * 75 + f


class _File:
    """One FILE of the sheet: where its audio starts (past a WAV's header), how many frames, its byte order."""

    def __init__(self, path: str, kind: str):
        self.path, self.swap, self.offset = path, kind == "MOTOROLA", 0
        size = os.path.getsize(path)
        if kind == "WAVE":
            self.offset, size = _wav_data(path)
        self.frames = size // FRAME


def _wav_data(path: str) -> tuple[int, int]:
    """Where a WAV's audio is and how long, if it's CD audio (16-bit stereo 44.1 kHz PCM)."""
    with open(path, "rb") as f:
        if f.read(12)[8:] != b"WAVE":
            raise ValueError(f"{os.path.basename(path)} isn't a WAV")
        fmt = None
        while True:
            head = f.read(8)
            if len(head) < 8:
                raise ValueError(f"{os.path.basename(path)} has no audio")
            name, size = head[:4], struct.unpack("<I", head[4:])[0]
            if name == b"fmt ":
                fmt = struct.unpack("<HHIIHH", f.read(16))
                f.seek(size - 16 + (size & 1), 1)
            elif name == b"data":
                if not fmt or (fmt[0], fmt[1], fmt[2], fmt[5]) != (1, 2, 44100, 16):
                    raise ValueError(f"{os.path.basename(path)} isn't CD audio (16-bit stereo 44.1 kHz)")
                return f.tell(), size
            else:
                f.seek(size + (size & 1), 1)


class CueImage:
    """A cue sheet read: `toc` as cd.read_toc gives a drive's, and read(lba, count) its frames."""

    def __init__(self, cue: str):
        self.cue = cue
        folder = os.path.dirname(os.path.abspath(cue))
        with open(cue, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = [ln.strip() for ln in f]
        # the disc as stretches of frames, in order: [where on the disc, its file (None: silence), frames, where in
        # the file]
        self.parts: list[list] = []
        tracks: list[tuple[int, int, bool]] = []
        file: Optional[_File] = None
        number, audio, gap = 0, True, 0
        for line in lines:
            word = line.split(" ", 1)[0].upper()
            if word == "FILE":
                m = re.match(r'FILE\s+"?(.+?)"?\s+(\w+)$', line, re.I)
                if not m:
                    raise ValueError(f"Can't read: {line}")
                path = m.group(1) if os.path.isabs(m.group(1)) else os.path.join(folder, m.group(1))
                file = _File(path, m.group(2).upper())
                self.parts.append([self._end(), file, file.frames, 0])
            elif word == "TRACK":
                bits = line.split()
                number, audio, gap = int(bits[1]), bits[2].upper() == "AUDIO", 0
            elif word == "PREGAP":
                gap = _msf(line.split()[1])
            elif word == "INDEX" and len(line.split()) > 2 and line.split()[1] == "01":
                if file is None:
                    raise ValueError("A track before any FILE")
                at = _msf(line.split()[2])  # in the file
                last = self.parts[-1]
                if gap:
                    # silence not in the file, just before the track: the rest of the file moves on past it
                    head = at - last[3]
                    self.parts[-1:] = [[last[0], file, head, last[3]], [last[0] + head, None, gap, 0],
                                       [last[0] + head + gap, file, last[2] - head, at]]
                    gap = 0
                    last = self.parts[-1]
                tracks.append((number, last[0] + at - last[3], audio))
        if not tracks:
            raise ValueError("No tracks in the sheet")
        self.parts = [p for p in self.parts if p[2] > 0]
        self.toc = tracks + [(LEADOUT, self._end(), False)]
        self.lock = threading.Lock()

    def _end(self) -> int:
        return self.parts[-1][0] + self.parts[-1][2] if self.parts else 0

    def read(self, lba: int, count: int) -> bytes:
        out = bytearray(count * FRAME)  # what no file has is silence
        with self.lock:
            for at, file, frames, file_at in self.parts:
                lo, hi = max(lba, at), min(lba + count, at + frames)
                if lo >= hi or file is None:
                    continue
                with open(file.path, "rb") as f:
                    f.seek(file.offset + (file_at + lo - at) * FRAME)
                    data = f.read((hi - lo) * FRAME)
                if file.swap:
                    swapped = bytearray(len(data) - len(data) % 2)
                    swapped[0::2], swapped[1::2] = data[1::2], data[0:len(swapped):2]
                    data = bytes(swapped)
                out[(lo - lba) * FRAME:(lo - lba) * FRAME + len(data)] = data
        return bytes(out)


def insert(cue: str) -> CueImage:
    """Puts an image in the virtual drive (there from then on, as a drive plugged in); raises ValueError for one that
    can't be read."""
    global _present, _image
    try:
        image = CueImage(os.path.abspath(cue))
    except OSError as e:
        raise ValueError(f"Can't read the image: {e}")
    with _lock:
        _present, _image = True, image
    return image


def eject(drive: str) -> bool:
    """The disc out of the virtual drive, which stays (empty, as a real drive does)."""
    global _image
    with _lock:
        if drive != DRIVE or _image is None:
            return False
        _image = None
        return True


def remove() -> None:
    """The virtual drive gone, as if unplugged."""
    global _present, _image
    with _lock:
        _present, _image = False, None


def is_virtual(drive: str) -> bool:
    return drive == DRIVE


def drives() -> list[str]:
    with _lock:
        return [DRIVE] if _present else []


def get(drive: str) -> Optional[CueImage]:
    """The image in the virtual drive (None for any other drive, or while it's empty)."""
    with _lock:
        return _image if drive == DRIVE else None
