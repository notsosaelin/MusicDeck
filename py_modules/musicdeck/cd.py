"""
Audio CDs, read straight from the drive with Linux's CD-ROM ioctls (linux/cdrom.h), as cdparanoia and
the kernel's own CD audio support do: no program needed, so it works on SteamOS as it comes.

* Whether a drive has an audio disc: CDROM_DRIVE_STATUS (a disc, ready) and CDROM_DISC_STATUS (audio,
  or audio with a data track: an "enhanced" CD).
* The table of contents: CDROMREADTOCHDR (first and last track), CDROMREADTOCENTRY (each track's start,
  and the lead-out's, as LBA; whether it's audio).
* Audio: CDROMREADAUDIO, raw 2352-byte frames (1/75 s of 16-bit stereo 44.1 kHz PCM), into a ctypes
  buffer whose address goes in the request. Played as a WAV made on the fly (`TrackWav`): a header, then
  the track's frames, any byte range of it, so seeking works.
* MusicBrainz knows a disc by its disc ID (`disc_id`): a hash of the track offsets.

Drives are /dev/sr*. Steam's systemd gives the user at the screen access to them (uaccess), so reading
needs no root. Linux only.
"""
import base64
import ctypes
import hashlib
import os
import struct
import threading
from typing import Callable, Optional

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROMREADAUDIO = 0x530E
CDROM_DRIVE_STATUS = 0x5326
CDROM_DISC_STATUS = 0x5327
CDROMEJECT = 0x5309
CDROM_LOCKDOOR = 0x5329
CDS_DISC_OK = 4
CDS_AUDIO = 100
CDS_MIXED = 105
CDROM_LBA = 0x01
CDROM_LEADOUT = 0xAA
CDROM_DATA_TRACK = 0x04
FRAME = 2352
FRAMES_PER_SECOND = 75
#: frames read at once (a second's worth): kernels take up to this many per request
CHUNK = 75
WAV_HEADER = 44


def drives() -> list[str]:
    """The CD/DVD drives there are: /dev/sr0, /dev/sr1..."""
    return sorted(f"/dev/{n}" for n in os.listdir("/dev") if n.startswith("sr") and n[2:].isdigit()) \
        if os.path.isdir("/dev") else []


def _open(device: str) -> int:
    # O_NONBLOCK: opens without a disc, and doesn't wait for one
    return os.open(device, os.O_RDONLY | os.O_NONBLOCK)


def eject(device: str) -> bool:
    """Opens the drive's tray (CDROM_LOCKDOOR off, then CDROMEJECT), as `eject` does."""
    if fcntl is None or not device:
        return False
    try:
        fd = _open(device)
    except OSError:
        return False
    try:
        try:
            fcntl.ioctl(fd, CDROM_LOCKDOOR, 0)
        except OSError:
            pass
        fcntl.ioctl(fd, CDROMEJECT, 0)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def has_audio_disc(device: str) -> bool:
    if fcntl is None:
        return False
    try:
        fd = _open(device)
    except OSError:
        return False
    try:
        if fcntl.ioctl(fd, CDROM_DRIVE_STATUS, 0) != CDS_DISC_OK:
            return False
        return fcntl.ioctl(fd, CDROM_DISC_STATUS, 0) in (CDS_AUDIO, CDS_MIXED)
    except OSError:
        return False
    finally:
        os.close(fd)


def read_toc(device: str) -> list[tuple[int, int, bool]]:
    """[(track number, start LBA, audio?)] in order, then (CDROM_LEADOUT, lead-out LBA, False)."""
    assert fcntl is not None
    fd = _open(device)
    try:
        first, last = struct.unpack("BB", fcntl.ioctl(fd, CDROMREADTOCHDR, bytes(2)))
        toc = []
        for number in list(range(first, last + 1)) + [CDROM_LEADOUT]:
            # struct cdrom_tocentry: track, adr:4|ctrl:4, format, (pad), union{lba}, datamode, (pad)
            entry = fcntl.ioctl(fd, CDROMREADTOCENTRY, struct.pack("BBBxiB3x", number, 0, CDROM_LBA, 0, 0))
            _, adr_ctrl, _, lba, _ = struct.unpack("BBBxiB3x", entry)
            toc.append((number, lba, number != CDROM_LEADOUT and not (adr_ctrl >> 4) & CDROM_DATA_TRACK))
        return toc
    finally:
        os.close(fd)


def tracks(toc: list[tuple[int, int, bool]]) -> list[dict[str, int]]:
    """The audio tracks: {"number", "start", "frames"}. On an enhanced CD the data session follows the
    last audio track after a gap of 11,400 frames (152 s), which isn't the track's."""
    found = []
    for (number, start, audio), (_, end, next_audio) in zip(toc, toc[1:]):
        if not audio:
            continue
        frames = end - start - (11400 if not next_audio and end != toc[-1][1] else 0)
        found.append({"number": number, "start": start, "frames": max(0, frames)})
    return found


def disc_id(toc: list[tuple[int, int, bool]]) -> str:
    """MusicBrainz's disc ID: SHA-1 of the first and last audio track's numbers and the offsets (LBA +
    150) of the lead-out and every track, in hex, as base64 with "._-" for "+/="."""
    audio = [t for t in toc if t[2]]
    if not audio:
        return ""
    last = audio[-1][0]
    # an enhanced CD's lead-out is where its audio ends: before the data session's gap
    after = next((t for t in toc if t[0] > last or t[0] == CDROM_LEADOUT), toc[-1])
    leadout = after[1] - (11400 if after[0] != CDROM_LEADOUT else 0)
    offsets = {n: lba + 150 for n, lba, ok in audio}
    text = f"{audio[0][0]:02X}{last:02X}{leadout + 150:08X}" + "".join(f"{offsets.get(i, 0):08X}" for i in range(1, 100))
    digest = base64.b64encode(hashlib.sha1(text.encode()).digest()).decode()
    return digest.translate(str.maketrans("+/=", "._-"))


def wav_header(frames: int) -> bytes:
    """A WAV header for `frames` CD frames of 16-bit stereo 44.1 kHz PCM."""
    size = frames * FRAME
    return (b"RIFF" + struct.pack("<I", 36 + size) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100,
                                                                               44100 * 4, 4, 16)
            + b"data" + struct.pack("<I", size))


class AudioReader:
    """Reads raw frames from a drive (one at a time: a drive reads one place at once)."""

    def __init__(self, device: str):
        self.device = device
        self.lock = threading.Lock()

    def read(self, lba: int, count: int) -> bytes:
        assert fcntl is not None
        out = bytearray()
        with self.lock:
            fd = _open(self.device)
            try:
                while count > 0:
                    n = min(count, CHUNK)
                    buf = ctypes.create_string_buffer(n * FRAME)
                    # struct cdrom_read_audio: union{lba}, addr_format, (pad), nframes, (pad), buf pointer
                    request = bytearray(struct.pack("iB3xi4xQ", lba, CDROM_LBA, n, ctypes.addressof(buf)))
                    fcntl.ioctl(fd, CDROMREADAUDIO, request)
                    out += buf.raw
                    lba += n
                    count -= n
            finally:
                os.close(fd)
        return bytes(out)


class TrackWav:
    """A track as a WAV file, read on demand: `size`, and `read(offset, length)` for any byte range
    (the stream server serves it like a file, so the player can seek)."""

    def __init__(self, start: int, frames: int, read_frames: Callable[[int, int], bytes]):
        self.start, self.frames, self.read_frames = start, frames, read_frames
        self.header = wav_header(frames)
        self.size = WAV_HEADER + frames * FRAME
        self.content_type = "audio/wav"

    def read(self, offset: int, length: int) -> bytes:
        end = min(self.size, offset + length)
        out = b""
        if offset < WAV_HEADER:
            out = self.header[offset:min(end, WAV_HEADER)]
            offset = WAV_HEADER
        if offset >= end:
            return out
        first, last = (offset - WAV_HEADER) // FRAME, (end - 1 - WAV_HEADER) // FRAME
        data = self.read_frames(self.start + first, last - first + 1)
        skip = (offset - WAV_HEADER) - first * FRAME
        return out + data[skip:skip + (end - offset)]


def cd_reader(device: str) -> Optional[AudioReader]:
    return AudioReader(device) if fcntl is not None else None
