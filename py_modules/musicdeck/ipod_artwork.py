"""
Album covers on an iPod's screen: iPod_Control/Artwork/ArtworkDB and its .ithmb files, as iTunes and
libgpod write them (a port of libgpod 0.8.3's db-artwork-writer.c and ithumb-writer.c, LGPL-2.1+).

* Each iPod shows covers at a few sizes of its own ("formats": 56x56, 128x128, 320x320 on a classic),
  listed in SysInfoExtended's AlbumArt, else libgpod's table by model. Each is RGB565 pixels.
* A cover becomes one picture per format, appended to F<format>_<n>.ithmb; the ArtworkDB says where
  each is (an mhii per cover, an mhni per size), and a track points at its mhii (its header's 0x160;
  older iPods match the mhii's song id, the track's dbid, instead). iPods that share covers ("sparse
  artwork", the nano 3G on and the classic) get one per album; older ones one per track, as libgpod does.
* What's there already (iTunes' covers) is kept as it is: new pictures go at the ends of the files, and
  an mhii goes only when no track points at it any more (its pictures stay in the files, unused).
* Scaling is ffmpeg's (no image library in Decky's Python): fitted inside the format, centred, the rest
  the format's background colour, as libgpod does. Without ffmpeg, or for an iPod without covers (a
  shuffle, the 1st-4th generation, the mini), nothing changes.
"""
import hashlib
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Optional

from . import native
from .itunesdb import ALBUM, mac_time

MAX_ITHMB = 256 * 1000 * 1000  # libgpod starts a new .ithmb file past this
FIRST_ID = 0x64


@dataclass(frozen=True)
class Format:
    id: int
    width: int
    height: int
    big_endian: bool = False
    row_align: int = 0  # bytes a row is padded to a multiple of (SysInfoExtended's RowBytesAlignment)
    back: tuple[int, int, int] = (0, 0, 0)  # what's around a cover that doesn't fill it
    crop: bool = False  # filled and cut to size instead of fitted


def _formats(*sizes: tuple[int, int, int]) -> list[Format]:
    return [Format(i, w, h) for i, w, h in sizes]


# libgpod's ipod_artwork_capabilities (itdb_device.c), for an iPod without SysInfoExtended
CLASSIC = _formats((1061, 56, 56), (1055, 128, 128), (1068, 128, 128), (1060, 320, 320))
FALLBACK = {
    "photo": _formats((1017, 56, 56), (1016, 140, 140)),
    "video_1": _formats((1028, 100, 100), (1029, 200, 200)),
    "video_2": _formats((1028, 100, 100), (1029, 200, 200)),
    "nano_1": _formats((1031, 42, 42), (1027, 100, 100)),
    "nano_2": _formats((1031, 42, 42), (1027, 100, 100)),
    "nano_3": CLASSIC,
    "nano_4": _formats((1055, 128, 128), (1068, 128, 128), (1071, 240, 240), (1074, 50, 50), (1078, 80, 80),
                       (1084, 240, 240)),
    "nano_5": _formats((1056, 128, 128), (1078, 80, 80), (1073, 240, 240), (1074, 50, 50)),
    "classic_1": CLASSIC, "classic_2": CLASSIC, "classic_3": CLASSIC,
}
SPARSE = {"nano_3", "nano_4", "nano_5", "nano_6", "nano_7", "classic_1", "classic_2", "classic_3"}
PIXEL_FORMATS = {"4C353635": False, "42353635": True}  # L565, B565 (the others aren't used for covers)


def _plist_dict(text: str) -> dict[str, Any]:
    """One plist <dict> of plain values (what an AlbumArt entry is), read by pattern."""
    found: dict[str, Any] = {}
    for key, kind, value, flag in re.findall(
            r"<key>([^<]+)</key>\s*(?:<(integer|string|real)>([^<]*)</\2>|<(true|false)\s*/>)", text):
        if flag:
            found[key] = flag == "true"
        elif kind == "integer":
            found[key] = int(value) if re.fullmatch(r"-?\d+", value.strip()) else 0
        elif kind == "real":
            found[key] = float(value or 0)
        else:
            found[key] = value.strip()
    return found


def _from_sysinfo(xml: str) -> Optional[list[Format]]:
    """SysInfoExtended's AlbumArt (or AlbumArt2) formats, in the order libgpod lists them; None without."""
    for key in ("AlbumArt", "AlbumArt2"):
        m = re.search(rf"<key>{key}</key>\s*<array>(.*?)</array>", xml, re.S)
        if not m:
            continue
        formats = []
        for body in re.findall(r"<dict>(.*?)</dict>", m.group(1), re.S):
            d = _plist_dict(body)
            pixel = str(d.get("PixelFormat", "")).upper()
            if pixel not in PIXEL_FORMATS or not d.get("FormatId") or not d.get("RenderWidth"):
                continue
            align = int(d.get("RowBytesAlignment") or 0) or (4 if d.get("AlignRowBytes") else 0)
            back = int(str(d.get("BackColor") or "0"), 16) if re.fullmatch(r"[0-9A-Fa-f]{1,8}",
                                                                          str(d.get("BackColor") or "0")) else 0
            formats.append(Format(int(d["FormatId"]), int(d["RenderWidth"]), int(d.get("RenderHeight") or 0),
                                  PIXEL_FORMATS[pixel], align, ((back >> 24) & 0xFF, (back >> 16) & 0xFF,
                                                                (back >> 8) & 0xFF), bool(d.get("Crop"))))
        if formats:
            formats.reverse()  # libgpod prepends as it reads
            return formats
    return None


def cover_formats(generation: str, sysinfo_extended: str = "") -> list[Format]:
    """The sizes this iPod shows covers at ([] for one that shows none)."""
    if sysinfo_extended:
        found = _from_sysinfo(sysinfo_extended)
        if found is not None:
            return [f for f in found if f.height > 0]
    return list(reversed(FALLBACK.get(generation, [])))  # libgpod's order (it prepends these too)


def sparse(generation: str, sysinfo_extended: str = "") -> bool:
    """Whether tracks share covers (one per album) rather than each having its own."""
    if re.search(r"<key>SupportsSparseArtwork</key>\s*<true\s*/>", sysinfo_extended or ""):
        return True
    return generation in SPARSE


# --- pictures ---

def image_size(data: bytes) -> Optional[tuple[int, int]]:
    """(width, height) of a PNG or JPEG, from its header."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] != b"\xff\xd8":
        return None
    at = 2
    while at + 9 <= len(data):
        if data[at] != 0xFF:
            at += 1
            continue
        marker = data[at + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            at += 1 if marker == 0xFF else 2
            continue
        length = struct.unpack(">H", data[at + 2:at + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[at + 5:at + 9])
            return width, height
        at += 2 + length
    return None


def fitted(width: int, height: int, fmt: Format) -> tuple[int, int]:
    """The size a width x height picture is scaled to for a format (libgpod's scale_and_crop)."""
    if fmt.crop or not width or not height:
        return fmt.width, fmt.height
    ws, hs = fmt.width / width, fmt.height / height
    if ws < hs:
        return fmt.width, min(math.ceil(height * ws), fmt.height)
    if ws > hs:
        return min(math.ceil(width * hs), fmt.width), fmt.height
    return fmt.width, fmt.height


def _pixel(rgb: tuple[int, int, int], big_endian: bool) -> bytes:
    r, g, b = rgb
    return struct.pack(">H" if big_endian else "<H", ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))


def packed(pixels: bytes, width: int, height: int, fmt: Format) -> tuple[bytes, int, int]:
    """A scaled picture's RGB565 pixels (width x height) placed in a format's frame: centred, the rest its
    background, rows padded to its alignment. Returns (data, horizontal padding, vertical padding)."""
    h_pad, v_pad = (fmt.width - width) // 2, (fmt.height - height) // 2
    row_pixels = fmt.width
    if fmt.row_align and (fmt.row_align // 2) and fmt.width % (fmt.row_align // 2):
        row_pixels += fmt.row_align // 2 - fmt.width % (fmt.row_align // 2)
    back = _pixel(fmt.back, fmt.big_endian)
    blank = back * row_pixels
    rows = [blank] * v_pad
    for y in range(height):
        line = pixels[y * width * 2:(y + 1) * width * 2]
        rows.append(back * h_pad + line + back * (row_pixels - h_pad - width))
    rows += [blank] * (fmt.height - v_pad - height)
    return b"".join(rows), h_pad, v_pad


class Scaler:
    """Pictures scaled with ffmpeg, kept per (cover, format) for the covers an album shares."""

    def __init__(self, ffmpeg: str, scratch: str):
        self.ffmpeg = ffmpeg
        self.scratch = scratch

    def _run(self, args: list[str]) -> bytes:
        result = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", *args], capture_output=True,
                                timeout=60, **native.NO_WINDOW)
        return result.stdout if result.returncode == 0 else b""

    def prepare(self, data: bytes) -> Optional[tuple[str, int, int]]:
        """The cover as a file ffmpeg reads, with its size (other formats made PNG first)."""
        path = os.path.join(self.scratch, hashlib.sha1(data).hexdigest())
        with open(path, "wb") as f:
            f.write(data)
        size = image_size(data)
        if not size:
            png = self._run(["-i", path, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"])
            size = image_size(png)
            if not size:
                return None
            with open(path, "wb") as f:
                f.write(png)
        return path, size[0], size[1]

    def scale(self, path: str, width: int, height: int, fmt: Format) -> Optional[bytes]:
        if fmt.crop:
            vf = f"scale={fmt.width}:{fmt.height}:force_original_aspect_ratio=increase:flags=bilinear," \
                 f"crop={fmt.width}:{fmt.height}"
        else:
            vf = f"scale={width}:{height}:flags=bilinear"
        out = self._run(["-i", path, "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt",
                         "rgb565be" if fmt.big_endian else "rgb565le", "-"])
        return out if len(out) == width * height * 2 else None


# --- the ArtworkDB ---

def _u16(b: bytes, at: int) -> int:
    return struct.unpack_from("<H", b, at)[0]


def _u32(b: bytes, at: int) -> int:
    return struct.unpack_from("<I", b, at)[0]


def _header(tag: bytes, size: int) -> bytearray:
    h = bytearray(size)
    h[0:4] = tag
    struct.pack_into("<I", h, 4, size)
    return h


@dataclass
class Picture:
    """An mhii: one cover, its pictures (as written), for the track(s) pointing at its id."""
    raw: bytes

    @property
    def id(self) -> int:
        return _u32(self.raw, 0x10)

    @property
    def song_id(self) -> int:
        return struct.unpack_from("<Q", self.raw, 0x14)[0]

    def with_song(self, dbid: int) -> "Picture":
        raw = bytearray(self.raw)
        struct.pack_into("<Q", raw, 0x14, dbid)
        return Picture(bytes(raw))


def _filename_mhod(name: str) -> bytes:
    text = name.encode("utf-16-le")
    pad = (4 - (36 + len(text)) % 4) % 4
    h = bytearray(36)
    h[0:4] = b"mhod"
    struct.pack_into("<II", h, 4, 24, 36 + len(text) + pad)  # header_len: the mhod's own 24, as iTunes
    struct.pack_into("<H", h, 0x0C, 3)
    h[0x0F] = pad
    struct.pack_into("<I", h, 0x18, len(text))
    h[0x1C] = 2  # UTF-16
    return bytes(h) + text + bytes(pad)


def _thumb_mhod(fmt: Format, offset: int, size: int, h_pad: int, v_pad: int, width: int, height: int,
                filename: str) -> bytes:
    name = _filename_mhod(filename)
    mhni = _header(b"mhni", 0x4C)
    struct.pack_into("<IIIII", mhni, 8, 0x4C + len(name), 1, fmt.id, offset, size)
    struct.pack_into("<hhhh", mhni, 0x1C, v_pad, h_pad, height, width)
    body = bytes(mhni) + name
    mhod = bytearray(24)
    mhod[0:4] = b"mhod"
    struct.pack_into("<II", mhod, 4, 24, 24 + len(body))
    struct.pack_into("<H", mhod, 0x0C, 2)  # a picture (MHOD_TYPE_LOCATION)
    return bytes(mhod) + body


def new_picture(image_id: int, song_id: int, source_size: int, thumbs: list[bytes]) -> Picture:
    h = _header(b"mhii", 0x98)
    struct.pack_into("<IIIQ", h, 8, 0x98 + sum(len(t) for t in thumbs), len(thumbs), image_id, song_id)
    struct.pack_into("<I", h, 0x28, mac_time(time.time()))  # when it was made (libgpod: when it was added)
    struct.pack_into("<I", h, 0x30, source_size)
    return Picture(bytes(h) + b"".join(thumbs))


@dataclass
class ArtworkDB:
    header: bytearray  # the mhfd's, kept (iTunes' unknown fields)
    pictures: list[Picture]
    albums: bytes  # section 2 (mhla), as it is: empty in a music ArtworkDB
    files: list[bytes]  # section 3's mhif entries: the formats

    @classmethod
    def parse(cls, data: bytes) -> "ArtworkDB":
        if data[:4] != b"mhfd":
            raise ValueError("Not an ArtworkDB")
        header = bytearray(data[:_u32(data, 4)])
        pictures: list[Picture] = []
        albums = bytes(_header(b"mhla", 0x5C))
        files: list[bytes] = []
        at = len(header)
        for _ in range(_u32(data, 0x14)):
            if data[at:at + 4] != b"mhsd":
                break
            total, index = _u32(data, at + 8), _u16(data, at + 12)
            body = data[at + _u32(data, at + 4):at + total]
            children, inner = _u32(body, 8), _u32(body, 4)
            entries, p = [], inner
            for _ in range(children):
                if body[p:p + 2] != b"mh":
                    break
                size = _u32(body, p + 8)  # mhii, mhba and mhif: the total at +8
                entries.append(body[p:p + size])
                p += size
            if index == 1:
                pictures = [Picture(e) for e in entries]
            elif index == 2:
                albums = body
            elif index == 3:
                files = entries
            at += total
        return cls(header, pictures, albums, files)

    @classmethod
    def new(cls) -> "ArtworkDB":
        h = _header(b"mhfd", 0x84)
        struct.pack_into("<I", h, 0x10, 2)  # iTunes 4.9 on: 2 (iTunes 7 throws away one with 1)
        h[0x30] = 2
        return cls(h, [], bytes(_header(b"mhla", 0x5C)), [])

    def add_formats(self, formats: list[Format]) -> None:
        have = {_u32(f, 0x10) for f in self.files}
        for fmt in formats:
            if fmt.id not in have:
                mhif = _header(b"mhif", 0x7C)
                struct.pack_into("<III", mhif, 8, 0x7C, 0, fmt.id)
                struct.pack_into("<I", mhif, 0x14, fmt.width * fmt.height * 2)
                self.files.append(bytes(mhif))

    def next_id(self) -> int:
        return max([p.id for p in self.pictures] + [FIRST_ID - 1]) + 1

    def to_bytes(self) -> bytes:
        def section(index: int, body: bytes) -> bytes:
            h = _header(b"mhsd", 0x60)
            struct.pack_into("<I", h, 8, 0x60 + len(body))
            struct.pack_into("<H", h, 12, index)
            return bytes(h) + body

        def listing(tag: bytes, entries: list[bytes]) -> bytes:
            h = _header(tag, 0x5C)
            struct.pack_into("<I", h, 8, len(entries))
            return bytes(h) + b"".join(entries)

        body = (section(1, listing(b"mhli", [p.raw for p in self.pictures])) + section(2, self.albums)
                + section(3, listing(b"mhlf", self.files)))
        header = bytearray(self.header)
        struct.pack_into("<I", header, 8, len(header) + len(body))
        struct.pack_into("<I", header, 0x14, 3)
        struct.pack_into("<I", header, 0x1C, max([p.id for p in self.pictures] + [0]))
        return bytes(header) + body


def _find(directory: str, name: str) -> Optional[str]:
    try:
        return next((os.path.join(directory, n) for n in os.listdir(directory) if n.lower() == name.lower()), None)
    except OSError:
        return None


def _load(control: str) -> ArtworkDB:
    artwork = _find(control, "Artwork")
    path = _find(artwork, "ArtworkDB") if artwork else None
    if path:
        try:
            with open(path, "rb") as f:
                return ArtworkDB.parse(f.read())
        except (OSError, ValueError, struct.error):
            pass
    return ArtworkDB.new()


def _stage(control: str, art: ArtworkDB) -> list[tuple[str, str]]:
    """The ArtworkDB written beside the old one, to be moved over it with the database."""
    folder = _find(control, "Artwork") or os.path.join(control, "Artwork")
    os.makedirs(folder, exist_ok=True)
    final = _find(folder, "ArtworkDB") or os.path.join(folder, "ArtworkDB")
    with open(final + ".musicdeck", "wb") as f:
        f.write(art.to_bytes())
        f.flush()
        os.fsync(f.fileno())
    return [(final + ".musicdeck", final)]


class _Ithmb:
    """Appends to F<format>_<n>.ithmb (a new n once one is past MAX_ITHMB)."""

    def __init__(self, folder: str, fmt: Format):
        self.folder, self.fmt, self.index = folder, fmt, 1
        while True:
            path = self._path()
            if not os.path.isfile(path) or os.path.getsize(path) < MAX_ITHMB:
                break
            self.index += 1

    def _path(self) -> str:
        name = f"F{self.fmt.id}_{self.index}.ithmb"
        return _find(self.folder, name) or os.path.join(self.folder, name)

    def write(self, data: bytes) -> tuple[str, int]:
        """Appends one picture; returns the file's name as the ArtworkDB gives it, and where it starts."""
        index = self.index
        with open(self._path(), "ab") as f:
            f.seek(0, os.SEEK_END)
            offset = f.tell()
            f.write(data)
        if offset + len(data) >= MAX_ITHMB:
            self.index += 1
        return f":F{self.fmt.id}_{index}.ithmb", offset


def _links(tracks: list[Any]) -> tuple[set[int], set[int]]:
    return {_u32(t.header, 0x160) for t in tracks if len(t.header) >= 0x164}, {t.dbid for t in tracks}


def add_covers(control: str, generation: str, sysinfo_extended: str, tracks: list[Any],
               covers: list[tuple[Any, bytes]], scratch: str, scaler: Optional[Scaler] = None) -> list[tuple[str, str]]:
    """Gives the new tracks `covers` ([(track, image bytes)]) their pictures, and drops the ArtworkDB's
    entries no track points at. Returns the ArtworkDB staged to be moved into place ([] when nothing's
    done: no covers on this iPod, or no ffmpeg)."""
    if not covers:
        return prune(control, tracks)
    formats = cover_formats(generation, sysinfo_extended)
    ffmpeg = shutil.which("ffmpeg")
    if not formats or not (ffmpeg or scaler):
        return []
    art = _load(control)
    art.add_formats(formats)
    folder = _find(control, "Artwork") or os.path.join(control, "Artwork")
    os.makedirs(folder, exist_ok=True)
    shared = sparse(generation, sysinfo_extended)
    writers = {f.id: _Ithmb(folder, f) for f in formats}
    scaler = scaler or Scaler(ffmpeg or "ffmpeg", tempfile.mkdtemp(prefix="covers-", dir=scratch))
    done: dict[str, int] = {}  # one picture per album's cover (on iPods that share them)
    for track, data in covers:
        album = track.strings().get(ALBUM, "")
        key = hashlib.sha1(album.encode() + data).hexdigest() if shared else f"{track.dbid}"
        if key in done:
            _point(track, done[key], len(data))
            continue
        prepared = scaler.prepare(data)
        if not prepared:
            continue
        path, width, height = prepared
        thumbs = []
        for fmt in formats:
            w, h = fitted(width, height, fmt)
            pixels = scaler.scale(path, w, h, fmt)
            if pixels is None:
                continue
            frame, h_pad, v_pad = packed(pixels, w, h, fmt)
            filename, offset = writers[fmt.id].write(frame)
            thumbs.append(_thumb_mhod(fmt, offset, len(frame), h_pad, v_pad, h_pad + w, v_pad + h, filename))
        if not thumbs:
            continue
        image_id = art.next_id()
        art.pictures.append(new_picture(image_id, track.dbid, len(data) + 1, thumbs))
        done[key] = image_id
        _point(track, image_id, len(data))
    _prune(art, tracks)
    return _stage(control, art)


def _point(track: Any, image_id: int, source_size: int) -> None:
    """A track's header pointing at its cover: artwork count and size, "has artwork", the mhii's id."""
    h = track.header
    struct.pack_into("<H", h, 0x7C, 1)
    struct.pack_into("<I", h, 0x80, source_size)
    h[0xA4] = 1
    struct.pack_into("<I", h, 0x160, image_id)


def _prune(art: ArtworkDB, tracks: list[Any]) -> None:
    """Drops the covers no track points at (by id, or on older iPods by its song id); a shared one whose
    first track went is given to another of its tracks."""
    links, dbids = _links(tracks)
    by_link: dict[int, int] = {}
    for t in tracks:
        if len(t.header) >= 0x164:
            by_link.setdefault(_u32(t.header, 0x160), t.dbid)
    kept = []
    for p in art.pictures:
        if p.song_id in dbids:
            kept.append(p)
        elif p.id in links:
            kept.append(p.with_song(by_link[p.id]))
    art.pictures = kept


def prune(control: str, tracks: list[Any]) -> list[tuple[str, str]]:
    """After tracks are taken off: the ArtworkDB without their covers, staged ([] without an ArtworkDB)."""
    artwork = _find(control, "Artwork")
    if not artwork or not _find(artwork, "ArtworkDB"):
        return []
    art = _load(control)
    before = len(art.pictures)
    _prune(art, tracks)
    return _stage(control, art) if len(art.pictures) != before else []
