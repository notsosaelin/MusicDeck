"""
An iPod's own database, iPod_Control/iTunes/iTunesDB: what Apple's firmware plays (it ignores files that
aren't listed in it). Read, changed and written back the way libgpod (gtkpod, Rhythmbox, Strawberry) does
it; libgpod 0.8.3's itdb_itunesdb.c is the reference for the format, and tests/test_ipod.py checks what's
written here against libgpod itself.

* The file is a tree of chunks, each "mh??" + its header's length + its own (or its children's) length:
  mhbd (the database) > mhsd sections: 1 the tracks (mhlt > mhit > mhod strings), 2 the playlists and
  3 the same for podcasts (mhlp > mhyp > mhod, mhip), 4 the albums (mhla > mhia), 8 the artists (mhli >
  mhii), and others kept as they are.
* Changing it keeps everything it doesn't touch byte for byte (an existing track's own fields, artwork
  links, playlists): new tracks are added at the end, the master playlist (every track; the iPod's
  sorted Songs/Artists/Albums lists, mhod 52/53) is made again, removed tracks leave every playlist.
* The checksum the iPod checks before showing anything (from the iPod Classic and the 3rd-generation
  nano on): hash58, an HMAC-SHA1 of the file keyed from the iPod's FireWire GUID (libgpod's
  itdb_hash58.c, from wtbw's work). The nano 5G's hash72 and the nano 6G/7G's hashAB aren't written:
  those also need SQLite databases, and hashAB a closed library (see ipod.py).
* What the iPod adds between syncs is folded in, as libgpod does: "Play Counts" (plays, ratings and
  bookmarks made on the iPod, by track index) and "OTGPlaylistInfo" (On-The-Go playlists), so neither is
  lost or applied twice.
* A Shuffle reads iTunesSD instead (written beside it): the 1st/2nd generation's format or the newer one.

Times are Mac times (seconds since 1904). Little-endian throughout, but for the old Shuffle's iTunesSD.
"""
import hashlib
import math
import os
import random
import re
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Optional

MAC_EPOCH = 2082844800  # 1970 - 1904, in seconds
FIRST_TRACK_ID = 52  # libgpod's FIRST_IPOD_ID

# mhod types
TITLE, PATH, ALBUM, ARTIST, GENRE, FILETYPE, COMMENT, COMPOSER = 1, 2, 3, 4, 5, 6, 8, 12
ALBUMARTIST, SORT_ARTIST = 22, 23
PODCAST_URL, PODCAST_RSS = 15, 16
STRING_TYPES = set(range(1, 15)) | set(range(18, 32)) | {200, 201, 202, 300}
LIB_INDEX, JUMP_TABLE, PLAYLIST_POS = 52, 53, 100
ALBUM_ALBUM, ALBUM_ARTIST, ARTIST_NAME = 200, 201, 300
# mhod 52's sort types
SORT_TITLE, SORT_ALBUM, SORT_ARTIST_ORDER, SORT_GENRE, SORT_COMPOSER = 3, 4, 5, 7, 18

# hashing schemes (mhbd +0x30)
HASH_NONE, HASH58, HASH72, HASHAB = 0, 1, 2, 3

TABLE1 = bytes.fromhex(
    "637C777BF26B6FC53001672BFED7AB76CA82C97DFA5947F0ADD4A2AF9CA472C0B7FD9326363FF7CC34A5E5F171D8311504C723C31896059A071280E2EB27B275"
    "09832C1A1B6E5AA0523BD6B329E32F8453D100ED20FCB15B6ACBBE394A4C58CFD0EFAAFB434D338545F9027F503C9FA851A3408F929D38F5BCB6DA2110FFF3D2"
    "CD0C13EC5F974417C4A77E3D645D197360814FDC222A908846EEB814DE5E0BDBE0323A0A4906245CC2D3AC629195E479E7C8376D8DD54EA96C56F4EA657AAE08"
    "BA78252E1CA6B4C6E8DD741F4BBD8B8A703EB5664803F60E613557B986C11D9EE1F8981169D98E949B1E87E9CE5528DF8CA1890DBFE6426841992D0FB054BB16")
TABLE2 = bytes.fromhex(
    "52096AD53036A538BF40A39E81F3D7FB7CE339829B2FFF87348E4344C4DEE9CB547B9432A6C2233DEE4C950B42FAC34E082EA16628D924B2765BA2496D8BD125"
    "72F8F66486689816D4A45CCC5D65B6926C704850FDEDB9DA5E154657A78D9D8490D8AB008CBCD30AF7E45805B8B34506D02C1E8FCA3F0F02C1AFBD0301138A6B"
    "3A9111414F67DCEA97F2CFCEF0B4E67396AC7422E7AD3585E2F937E81C75DF6E47F11A711D29C5896FB7620EAA18BE1BFC563E4BC6D279209ADBC0FE78CD5AF4"
    "1FDDA8338807C731B11210592780EC5F60517FA919B54A0D2DE57A9F93C99CEFA0E03B4DAE2AF5B0C8EBBB3C83539961172B047EBA77D626E169146355210C7D")
FIXED = bytes.fromhex("6723FE304533F890992107C1D012B2A10781")


class ITunesDBError(Exception):
    pass


def _utc_offset(unix: float) -> int:
    """This computer's offset from UTC now, in seconds: an iPod keeps local times, all of them in one time
    zone (libgpod converts every date with the current offset, not each date's own summer time)."""
    try:
        return int(time.localtime().tm_gmtoff or 0)
    except (OverflowError, OSError, ValueError):
        return 0


def mac_time(unix: float) -> int:
    """A time as an iPod keeps it: local time, in seconds since 1904 (libgpod's device_time_time_t_to_mac,
    with this computer's time zone)."""
    return int(unix + MAC_EPOCH + _utc_offset(unix)) & 0xFFFFFFFF if unix else 0


def unix_time(mac: int) -> float:
    if not mac:
        return 0.0
    local = mac - MAC_EPOCH
    return float(local - _utc_offset(local))


def _u32(b: bytes, at: int) -> int:
    return struct.unpack_from("<I", b, at)[0] if at + 4 <= len(b) else 0


def _u16(b: bytes, at: int) -> int:
    return struct.unpack_from("<H", b, at)[0] if at + 2 <= len(b) else 0


def _u64(b: bytes, at: int) -> int:
    return struct.unpack_from("<Q", b, at)[0] if at + 8 <= len(b) else 0


def _put32(b: bytearray, at: int, value: int) -> None:
    struct.pack_into("<I", b, at, value & 0xFFFFFFFF)


# --- strings (mhod) ---

def string_mhod(kind: int, text: str) -> bytes:
    """A string mhod as iTunes writes it: UTF-16LE."""
    data = text.encode("utf-16-le")
    return (b"mhod" + struct.pack("<IIIII", 24, 40 + len(data), kind, 0, 0)
            + struct.pack("<IIII", 1, len(data), 1, 0) + data)


def position_mhod(position: int) -> bytes:
    """A playlist item's position (mhod 100)."""
    return b"mhod" + struct.pack("<IIIII", 24, 44, PLAYLIST_POS, 0, 0) + struct.pack("<IIIII", position, 0, 0, 0, 0)


def read_mhod(raw: bytes) -> tuple[int, Optional[str]]:
    """(type, its text) for a string mhod; (type, None) for any other."""
    kind = _u32(raw, 12)
    if kind in (PODCAST_URL, PODCAST_RSS):
        return kind, raw[24:].split(b"\0", 1)[0].decode("utf-8", "replace")
    if kind not in STRING_TYPES or len(raw) < 40:
        return kind, None
    encoding, length = _u32(raw, 24), _u32(raw, 28)
    data = raw[40:40 + length]
    return kind, data.decode("utf-8" if encoding == 2 else "utf-16-le", "replace")


def _children(buf: bytes, at: int, count: int) -> tuple[list[bytes], int]:
    """`count` whole chunks from `at` (each its own total length at +8)."""
    out = []
    for _ in range(count):
        if buf[at:at + 4][:2] != b"mh":
            break
        total = _u32(buf, at + 8)
        if total < 12 or at + total > len(buf):
            raise ITunesDBError("A chunk runs past the end of the database")
        out.append(buf[at:at + total])
        at += total
    return out, at


# --- the parts ---

@dataclass
class Track:
    header: bytearray  # the mhit header, as it is (fields kept that this doesn't know)
    mhods: list[bytes]

    @property
    def id(self) -> int:
        return _u32(self.header, 16)

    @id.setter
    def id(self, value: int) -> None:
        _put32(self.header, 16, value)

    @property
    def dbid(self) -> int:
        return _u64(self.header, 0x70)

    def strings(self) -> dict[int, str]:
        found: dict[int, str] = {}
        for raw in self.mhods:
            kind, text = read_mhod(raw)
            if text is not None:
                found.setdefault(kind, text)
        return found

    def fields(self) -> dict[str, Any]:
        """What the track says about itself, as names: title, artist, album, path (on the iPod, "/"
        separated), length (s), track/disc numbers, year, rating (0-5), plays..."""
        s, h = self.strings(), self.header
        return {"id": self.id, "dbid": self.dbid, "title": s.get(TITLE, ""), "artist": s.get(ARTIST, ""),
                "album": s.get(ALBUM, ""), "albumartist": s.get(ALBUMARTIST, ""), "genre": s.get(GENRE, ""),
                "composer": s.get(COMPOSER, ""), "filetype": s.get(FILETYPE, ""),
                "path": s.get(PATH, "").replace(":", "/"), "rating": h[0x1F] // 20 if len(h) > 0x1F else 0,
                "compilation": bool(h[0x1E]) if len(h) > 0x1E else False, "size": _u32(h, 0x24),
                "duration": _u32(h, 0x28) / 1000, "track": _u32(h, 0x2C), "year": _u32(h, 0x34),
                "bitrate": _u32(h, 0x38), "rate": _u32(h, 0x3C) >> 16, "plays": _u32(h, 0x50),
                "played": unix_time(_u32(h, 0x58)), "disc": _u32(h, 0x5C), "added": unix_time(_u32(h, 0x68)),
                "album_id": _u32(h, 0x120), "artist_id": _u32(h, 0x1E0), "gain": sound_check_gain(_u32(h, 0x4C))}

    def to_bytes(self) -> bytes:
        header = bytearray(self.header)
        body = b"".join(self.mhods)
        _put32(header, 8, len(header) + len(body))
        _put32(header, 12, len(self.mhods))
        return bytes(header) + body


@dataclass
class Item:
    """A playlist's entry (mhip) and its own mhods (its position)."""
    header: bytearray
    mhods: list[bytes]

    @property
    def track_id(self) -> int:
        return _u32(self.header, 24)

    def to_bytes(self) -> bytes:
        header = bytearray(self.header)
        body = b"".join(self.mhods)
        _put32(header, 8, len(header) + len(body))
        _put32(header, 12, len(self.mhods))
        return bytes(header) + body


@dataclass
class Playlist:
    header: bytearray
    mhods: list[bytes]
    items: list[Item]

    @property
    def master(self) -> bool:
        return len(self.header) > 0x14 and self.header[0x14] == 1

    @property
    def podcasts(self) -> bool:
        return _u16(self.header, 0x2A) == 1

    @property
    def name(self) -> str:
        for raw in self.mhods:
            kind, text = read_mhod(raw)
            if kind == TITLE and text is not None:
                return text
        return ""

    def to_bytes(self) -> bytes:
        header = bytearray(self.header)
        body = b"".join(self.mhods) + b"".join(i.to_bytes() for i in self.items)
        _put32(header, 8, len(header) + len(body))
        _put32(header, 12, len(self.mhods))
        _put32(header, 16, len(self.items))
        return bytes(header) + body


@dataclass
class Entry:
    """An album (mhia) or artist (mhii) of the lists newer firmware browses by."""
    header: bytearray
    mhods: list[bytes]

    @property
    def id(self) -> int:
        return _u32(self.header, 16)

    def strings(self) -> dict[int, str]:
        return {k: t for k, t in (read_mhod(m) for m in self.mhods) if t is not None}

    def to_bytes(self) -> bytes:
        header = bytearray(self.header)
        body = b"".join(self.mhods)
        _put32(header, 8, len(header) + len(body))
        _put32(header, 12, len(self.mhods))
        return bytes(header) + body


@dataclass
class Section:
    """An mhsd: its type, its header and its list's header, and what's in it (or its body as it is)."""
    header: bytearray
    list_header: bytearray = field(default_factory=bytearray)
    tracks: list[Track] = field(default_factory=list)
    playlists: list[Playlist] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    raw: bytes = b""

    @property
    def type(self) -> int:
        return _u32(self.header, 12)

    def to_bytes(self) -> bytes:
        header = bytearray(self.header)
        if self.list_header:
            lh = bytearray(self.list_header)
            if self.type == 1:
                _put32(lh, 8, len(self.tracks))
                body = bytes(lh) + b"".join(t.to_bytes() for t in self.tracks)
            elif self.type in (2, 3, 5):
                _put32(lh, 8, len(self.playlists))
                body = bytes(lh) + b"".join(p.to_bytes() for p in self.playlists)
            else:
                _put32(lh, 8, len(self.entries))
                body = bytes(lh) + b"".join(e.to_bytes() for e in self.entries)
        else:
            body = self.raw
        _put32(header, 8, len(header) + len(body))
        return bytes(header) + body


def _parse_track(raw: bytes) -> Track:
    header_len, mhod_count = _u32(raw, 4), _u32(raw, 12)
    mhods, _ = _children(raw, header_len, mhod_count)
    return Track(bytearray(raw[:header_len]), mhods)


def _parse_playlist(raw: bytes) -> Playlist:
    header_len, mhod_count, item_count = _u32(raw, 4), _u32(raw, 12), _u32(raw, 16)
    mhods, at = _children(raw, header_len, mhod_count)
    items = []
    for _ in range(item_count):
        if raw[at:at + 4] != b"mhip":
            break
        total, ih = _u32(raw, at + 8), _u32(raw, at + 4)
        chunk = raw[at:at + total]
        children, _ = _children(chunk, ih, _u32(chunk, 12))
        items.append(Item(bytearray(chunk[:ih]), children))
        at += total
    return Playlist(bytearray(raw[:header_len]), mhods, items)


def _parse_entry(raw: bytes) -> Entry:
    header_len = _u32(raw, 4)
    mhods, _ = _children(raw, header_len, _u32(raw, 12))
    return Entry(bytearray(raw[:header_len]), mhods)


class ITunesDB:
    def __init__(self, header: bytearray, sections: list[Section]):
        self.header = header
        self.sections = sections

    # --- reading ---

    @classmethod
    def parse(cls, data: bytes) -> "ITunesDB":
        """An iTunesDB, or an iTunesCDB (the nano 5G's and later: the same, its body zlib-compressed)."""
        if data[:4] != b"mhbd":
            raise ITunesDBError("Not an iPod database")
        header_len = _u32(data, 4)
        if header_len >= 0xA9 and _u32(data, 8) == len(data) and data[header_len:header_len + 4] != b"mhsd":
            try:
                data = data[:header_len] + zlib.decompress(data[header_len:])
            except zlib.error as e:
                raise ITunesDBError(f"The iPod's database is damaged: {e}") from e
        sections = []
        at = header_len
        for _ in range(_u32(data, 0x14)):
            if data[at:at + 4] != b"mhsd":
                break
            sh, total = _u32(data, at + 4), _u32(data, at + 8)
            chunk = data[at:at + total]
            section = Section(bytearray(chunk[:sh]))
            body = chunk[sh:]
            kind = section.type
            if kind in (1, 2, 3, 4, 5, 8) and body[:2] == b"mh":
                lh, count = _u32(body, 4), _u32(body, 8)
                section.list_header = bytearray(body[:lh])
                children, _ = _children(body, lh, count)
                if kind == 1:
                    section.tracks = [_parse_track(c) for c in children]
                elif kind in (2, 3, 5):
                    section.playlists = [_parse_playlist(c) for c in children]
                else:
                    section.entries = [_parse_entry(c) for c in children]
            else:
                section.raw = body
            sections.append(section)
            at += total
        return cls(bytearray(data[:header_len]), sections)

    def section(self, kind: int) -> Optional[Section]:
        return next((s for s in self.sections if s.type == kind), None)

    @property
    def tracks(self) -> list[Track]:
        s = self.section(1)
        return s.tracks if s else []

    def playlists(self) -> list[Playlist]:
        """The playlists (section 2: the one with each playlist's tracks listed as they are)."""
        s = self.section(2) or self.section(3)
        return s.playlists if s else []

    @property
    def name(self) -> str:
        """The iPod's name: its master playlist's."""
        return next((p.name for p in self.playlists() if p.master), "")

    @property
    def hashing_scheme(self) -> int:
        return _u16(self.header, 0x30)

    # --- changing ---

    def _next_track_id(self) -> int:
        return max([t.id for t in self.tracks] + [FIRST_TRACK_ID - 1]) + 1

    def _list_id(self, section_type: int, strings: dict[int, str], key: tuple[int, ...]) -> Optional[int]:
        """The id of an album (4) or artist (8) with these names, added when it's new; None without that list."""
        s = self.section(section_type)
        if s is None or not s.list_header:
            return None
        wanted = tuple((strings.get(k) or "").lower() for k in key)
        for e in s.entries:
            if tuple((e.strings().get(k) or "").lower() for k in key) == wanted:
                return e.id
        new_id = max([e.id for e in s.entries] + [0]) + 1
        sql_id = random.getrandbits(64)
        if section_type == 4:
            header = bytearray(b"mhia" + struct.pack("<IIIIQI", 88, 0, 0, new_id, sql_id, 2) + bytes(56))
        else:
            header = bytearray(b"mhii" + struct.pack("<IIIIQI", 80, 0, 0, new_id, sql_id, 2) + bytes(48))
        mhods = [string_mhod(k, strings[k]) for k in key if strings.get(k)]
        s.entries.append(Entry(header, mhods))
        return new_id

    def add_track(self, info: dict[str, Any]) -> Track:
        """A new track, at the end. info: title, artist, album, albumartist, genre, composer, path (from
        the iPod's root, "/"-separated), filetype ("MPEG audio file"...), size, duration (s), track,
        tracks, disc, discs, year, bitrate (kbps), rate (Hz), rating (0-5), compilation, gain (ReplayGain's,
        dB: the iPod's Sound Check)."""
        now = time.time()
        dbid = random.getrandbits(64) or 1
        ext = os.path.splitext(info["path"])[1][1:5].upper().ljust(4)
        marker = int.from_bytes(ext.encode("ascii", "replace"), "big")
        mp3 = info["path"].lower().endswith(".mp3")
        h = bytearray(0x248)
        h[0:4] = b"mhit"
        _put32(h, 4, 0x248)
        _put32(h, 16, self._next_track_id())
        _put32(h, 0x14, 1)  # visible
        _put32(h, 0x18, marker)
        h[0x1C], h[0x1D] = (0, 1) if mp3 else (0, 0)  # type1/type2: VBR MP3 / AAC (libgpod leaves to the app)
        h[0x1E] = 1 if info.get("compilation") else 0
        h[0x1F] = max(0, min(5, int(info.get("rating") or 0))) * 20
        _put32(h, 0x20, mac_time(now))
        _put32(h, 0x24, int(info.get("size") or 0))
        _put32(h, 0x28, int(round(float(info.get("duration") or 0) * 1000)))
        _put32(h, 0x2C, int(info.get("track") or 0))
        _put32(h, 0x30, int(info.get("tracks") or 0))
        _put32(h, 0x34, int(info.get("year") or 0) if str(info.get("year") or "0").isdigit() else 0)
        _put32(h, 0x38, int(info.get("bitrate") or 0))
        _put32(h, 0x3C, (int(info.get("rate") or 44100) & 0xFFFF) << 16)
        if info.get("gain") is not None:
            _put32(h, 0x4C, sound_check(float(info["gain"])))
        _put32(h, 0x5C, int(info.get("disc") or 0))
        _put32(h, 0x60, int(info.get("discs") or 0))
        _put32(h, 0x68, mac_time(now))
        struct.pack_into("<Q", h, 0x70, dbid)
        h[0x78] = 0  # checked
        struct.pack_into("<f", h, 0x88, float(info.get("rate") or 44100))
        struct.pack_into("<H", h, 0x90, 0x0C if mp3 else 0x33)  # unk144: libgpod's MP3 / AAC values
        struct.pack_into("<Q", h, 0xA8, dbid)  # dbid2
        _put32(h, 0xD0, 1)  # media type: audio
        strings = {TITLE: info.get("title") or os.path.basename(info["path"]), ARTIST: info.get("artist") or "",
                   ALBUM: info.get("album") or "", GENRE: info.get("genre") or "",
                   COMPOSER: info.get("composer") or "", ALBUMARTIST: info.get("albumartist") or "",
                   FILETYPE: info.get("filetype") or "", PATH: info["path"].replace("/", ":")}
        artist = strings[ALBUMARTIST] or strings[ARTIST]
        album_id = self._list_id(4, {ALBUM_ALBUM: strings[ALBUM], ALBUM_ARTIST: artist}, (ALBUM_ALBUM, ALBUM_ARTIST)) \
            if strings[ALBUM] else None
        artist_id = self._list_id(8, {ARTIST_NAME: strings[ARTIST]}, (ARTIST_NAME,)) if strings[ARTIST] else None
        _put32(h, 0x120, album_id or 0)
        h[0x124:0x12C] = self.header[0x24:0x2C]  # as the database's own (libgpod: "purpose unknown")
        _put32(h, 0x12C, int(info.get("size") or 0))
        struct.pack_into("<Q", h, 0x134, 0x808080808080)
        _put32(h, 0x168, 1)
        _put32(h, 0x1E0, artist_id or 0)
        order = (TITLE, ARTIST, ALBUM, FILETYPE, PATH, GENRE, COMPOSER, ALBUMARTIST)
        track = Track(h, [string_mhod(k, strings[k]) for k in order if strings[k]])
        tracks = self.section(1)
        if tracks is None:
            raise ITunesDBError("The iPod's database has no list of tracks")
        tracks.tracks.append(track)
        return track

    def rename(self, name: str) -> None:
        """The iPod's name (its master playlist's), as iTunes shows it and the iPod's About says."""
        master = next((p for p in self.playlists() if p.master), None)
        if master is None:
            raise ITunesDBError("The iPod's database has no main playlist")
        for kind in (2, 3):
            s = self.section(kind)
            for p in s.playlists if s else []:
                if p.master:
                    kept = [m for m in p.mhods if read_mhod(m)[0] != TITLE]
                    p.mhods = [string_mhod(TITLE, name)] + kept  # first, where iTunes and libgpod put it

    def remove_tracks(self, ids: set[int]) -> list[Track]:
        """Takes tracks out of the database and every playlist; returns them (their files are the caller's)."""
        tracks = self.section(1)
        gone = [t for t in self.tracks if t.id in ids]
        if tracks:
            tracks.tracks = [t for t in tracks.tracks if t.id not in ids]
        for kind in (2, 3):
            s = self.section(kind)
            for p in s.playlists if s else []:
                p.items = [i for i in p.items if i.track_id not in ids]
                # a podcasts list's items are grouped by show, their mhods ids rather than positions
                if not p.podcasts:
                    _renumber(p)
        return gone

    def add_playlist(self, name: str, track_ids: list[int]) -> None:
        """A playlist (to both playlist sections, as iTunes keeps them)."""
        pid = random.getrandbits(64)
        for kind in (2, 3):
            s = self.section(kind)
            if s is None:
                continue
            header = bytearray(108)
            header[0:4] = b"mhyp"
            _put32(header, 4, 108)
            _put32(header, 0x18, mac_time(time.time()))
            struct.pack_into("<Q", header, 0x1C, pid)
            struct.pack_into("<H", header, 0x28, 1)
            items = [_item(tid, i) for i, tid in enumerate(track_ids)]
            s.playlists.append(Playlist(header, [string_mhod(TITLE, name), _preferences_mhod()], items))

    def rebuild_master(self) -> None:
        """The master playlist lists every track, in order, with the sorted lists the iPod browses by."""
        tracks = self.tracks
        for kind in (2, 3):
            s = self.section(kind)
            master = next((p for p in s.playlists if p.master), None) if s else None
            if master is None:
                continue
            kept = [m for m in master.mhods if _u32(m, 12) not in (LIB_INDEX, JUMP_TABLE)]
            master.mhods = kept + (_index_mhods(tracks) if tracks else [])
            master.items = [_item(t.id, i) for i, t in enumerate(tracks)]

    def fold_play_counts(self, play_counts: bytes) -> None:
        """What the iPod counted since the last sync ("Play Counts": an entry per track, in order)."""
        if play_counts[:4] != b"mhdp":
            return
        header_len, entry_len, count = _u32(play_counts, 4), _u32(play_counts, 8), _u32(play_counts, 12)
        if entry_len < 12:
            return
        for i, track in enumerate(self.tracks[:count]):
            at = header_len + i * entry_len
            plays, last, bookmark = struct.unpack_from("<III", play_counts, at)
            h = track.header
            if plays:
                _put32(h, 0x50, _u32(h, 0x50) + plays)
                _put32(h, 0x54, _u32(h, 0x54) + plays)
            if last:
                _put32(h, 0x58, last)
            if bookmark:
                _put32(h, 0x6C, bookmark)
            if entry_len >= 16:
                # the rating as it is on the iPod now (libgpod takes it whenever it's there)
                rating = _u32(play_counts, at + 12)
                if rating <= 100 and len(h) > 0x1F:
                    h[0x1F] = rating
            if entry_len >= 0x1C:
                skips = _u32(play_counts, at + 20)
                if skips:
                    _put32(h, 0x9C, _u32(h, 0x9C) + skips)
                if _u32(play_counts, at + 24):
                    _put32(h, 0xA0, _u32(play_counts, at + 24))

    def add_on_the_go(self, otg: bytes, name: str) -> None:
        """An On-The-Go playlist made on the iPod (track indexes), kept as a playlist of its own."""
        if otg[:4] != b"mhpo":
            return
        header_len, entry_len, count = _u32(otg, 4), _u32(otg, 8), _u32(otg, 12)
        tracks = self.tracks
        ids = [tracks[n].id for n in (_u32(otg, header_len + i * entry_len) for i in range(count)) if n < len(tracks)]
        if ids:
            self.add_playlist(name, ids)

    # --- writing ---

    def to_bytes(self, firewire_guid: Optional[bytes] = None, scheme: int = HASH_NONE, compressed: bool = False,
                 hash72_key: Optional[tuple[bytes, bytes]] = None,
                 hashab: Optional[Any] = None) -> bytes:
        """The whole file, sizes made right, and signed for an iPod that checks: hash58 with its GUID;
        hash72 (nano 5G) with the key recovered from a database iTunes signed (`hash72_key`: iv, random
        bytes); hashAB (nano 6G/7G) through `hashab(sha1) -> 57 bytes`. `compressed`: an iTunesCDB (the
        nano 5G on), its body zlib-compressed after the header, as libgpod writes it."""
        header = bytearray(self.header)
        if len(header) >= 0x14:
            _put32(header, 0x14, len(self.sections))
        if len(header) > 0xA8:
            # 0x0C: 2 where the database is compressed (libgpod); 0xA8: the compressed flag
            _put32(header, 0x0C, 2 if compressed else (_u32(header, 0x0C) if _u32(header, 0x0C) == 1 else 1))
            header[0xA8] = 1 if compressed else 0
        body = b"".join(s.to_bytes() for s in self.sections)
        if compressed:
            body = zlib.compress(body, 1)
        data = bytearray(bytes(header) + body)
        _put32(data, 8, len(data))
        if scheme == HASH58:
            if not firewire_guid:
                raise ITunesDBError("This iPod checks its database against its FireWire ID, which wasn't found")
            write_hash58(data, firewire_guid)
        elif scheme == HASH72:
            if not hash72_key:
                raise ITunesDBError("This iPod's signing key isn't known: it needs one sync with iTunes first")
            write_hash72(data, *hash72_key)
        elif scheme == HASHAB:
            if not hashab:
                raise ITunesDBError("Signing for this iPod needs MusicDeck's hashab helper, which isn't here")
            write_hashab(data, hashab, self.hashing_scheme)
        elif scheme != HASH_NONE:
            raise ITunesDBError("This iPod's database is signed in a way MusicDeck can't write")
        else:
            struct.pack_into("<H", data, 0x30, 0)
        return bytes(data)


def _renumber(p: Playlist) -> None:
    for i, item in enumerate(p.items):
        item.mhods = [position_mhod(i) if _u32(m, 12) == PLAYLIST_POS else m for m in item.mhods]


def _item(track_id: int, position: int) -> Item:
    header = bytearray(76)
    header[0:4] = b"mhip"
    _put32(header, 4, 76)
    _put32(header, 24, track_id)
    return Item(header, [position_mhod(position)])


def _preferences_mhod() -> bytes:
    """The playlist view preferences iTunes expects in each playlist (libgpod's
    mk_long_mhod_id_playlist, as it is)."""
    values = [0, 0, 0, 0, 0, 0, 0x010084, 0x05, 0x09, 0x03, 0x120001, 0, 0, 0, 0xC80002, 0, 0, 0, 0x3C000D, 0, 0, 0,
              0x7D0004, 0, 0, 0, 0x7D0003, 0, 0, 0, 0x640008, 0, 0, 0, 0x640017, 0x01, 0, 0, 0x500014, 0x01, 0, 0,
              0x7D0015, 0x01] + [0] * 114
    return b"mhod" + struct.pack("<III", 0x18, 0x288, PLAYLIST_POS) + struct.pack(f"<{len(values)}I", *values)


def _letter(text: str) -> int:
    """The jump table's letter for a name: its first letter or digit, upper case; "0" for digits and none."""
    for ch in text:
        if ch.isalnum():
            if ch.isalpha():
                up = ch.upper()
                return ord(up) if ord(up) < 0x10000 else ord("0")
            return ord("0")
    return ord("0")


def _sort_artist(name: str) -> str:
    return f"{name[4:]}, The" if name.lower().startswith("the ") else name


def _index_mhods(tracks: list[Track]) -> list[bytes]:
    """The master playlist's sorted lists (mhod 52) and their jump tables (mhod 53), by title, artist,
    album, genre and composer, as libgpod writes them."""
    rows = []
    for index, t in enumerate(tracks):
        s, h = t.strings(), t.header
        rows.append({"index": index, "title": s.get(TITLE, ""), "artist": _sort_artist(s.get(ARTIST, "")),
                     "album": s.get(ALBUM, ""), "genre": s.get(GENRE, ""), "composer": s.get(COMPOSER, ""),
                     "disc": _u32(h, 0x5C), "track": _u32(h, 0x2C)})

    def key(*names: str):
        return lambda r: tuple(r[n].casefold() if isinstance(r[n], str) else r[n] for n in names)

    orders = [(SORT_TITLE, "title", key("title")),
              (SORT_ARTIST_ORDER, "artist", key("artist", "album", "disc", "track", "title")),
              (SORT_ALBUM, "album", key("album", "disc", "track", "title")),
              (SORT_GENRE, "genre", key("genre", "artist", "album", "disc", "track", "title")),
              (SORT_COMPOSER, "composer", key("composer", "album", "disc", "track", "title"))]
    out = []
    for sort_type, field_name, k in orders:
        ordered = sorted(rows, key=k)
        out.append(b"mhod" + struct.pack("<IIIII", 24, 4 * len(ordered) + 72, LIB_INDEX, 0, 0)
                   + struct.pack("<II", sort_type, len(ordered)) + bytes(40)
                   + struct.pack(f"<{len(ordered)}I", *(r["index"] for r in ordered)))
        jumps: list[list[int]] = []
        for position, r in enumerate(ordered):
            letter = _letter(r[field_name])
            if not jumps or jumps[-1][0] != letter:
                jumps.append([letter, position, 0])
            jumps[-1][2] += 1
        out.append(b"mhod" + struct.pack("<IIIII", 24, 12 * len(jumps) + 40, JUMP_TABLE, 0, 0)
                   + struct.pack("<II", sort_type, len(jumps)) + bytes(8)
                   + b"".join(struct.pack("<HHII", letter, 0, start, n) for letter, start, n in jumps))
    return out


# --- a database for an iPod that has none ---

def new_database(name: str) -> ITunesDB:
    """An empty database, laid out as libgpod writes one (its master playlist named after the iPod)."""
    header = bytearray(244)
    header[0:4] = b"mhbd"
    _put32(header, 4, 244)
    _put32(header, 0x0C, 1)
    _put32(header, 0x10, 0x30)  # the iTunes 9.2 database version; newer iPods refuse older ones
    struct.pack_into("<Q", header, 0x18, random.getrandbits(64))
    struct.pack_into("<H", header, 0x20, 2)  # written on Windows
    struct.pack_into("<Q", header, 0x24, random.getrandbits(64))
    header[0x46:0x48] = b"en"
    struct.pack_into("<Q", header, 0x48, random.getrandbits(64))

    def section(kind: int, list_tag: bytes = b"") -> Section:
        sh = bytearray(96)
        sh[0:4] = b"mhsd"
        _put32(sh, 4, 96)
        _put32(sh, 12, kind)
        lh = bytearray(92)
        if list_tag:
            lh[0:4] = list_tag
            _put32(lh, 4, 92)
        return Section(sh, lh if list_tag else bytearray())

    master = bytearray(108)
    master[0:4] = b"mhyp"
    _put32(master, 4, 108)
    master[0x14] = 1
    _put32(master, 0x18, mac_time(time.time()))
    struct.pack_into("<Q", master, 0x1C, random.getrandbits(64))
    struct.pack_into("<H", master, 0x28, 1)
    playlists_3, playlists_2 = section(3, b"mhlp"), section(2, b"mhlp")
    for s in (playlists_3, playlists_2):
        s.playlists.append(Playlist(bytearray(master), [string_mhod(TITLE, name), _preferences_mhod()], []))
    empty6, empty10 = section(6, b"mhlt"), section(10, b"mhlt")
    sections = [section(1, b"mhlt"), playlists_3, playlists_2, section(4, b"mhla"), section(8, b"mhli"), empty6,
                empty10, section(5, b"mhlp")]
    return ITunesDB(header, sections)


# --- hash58 ---

def _gcd(a: int, b: int) -> int:
    while True:
        a = a % b
        if a == 0:
            return b
        b = b % a
        if b == 0:
            return a


def _lcm(a: int, b: int) -> int:
    return 1 if a == 0 or b == 0 else (a * b) // _gcd(a, b)


def hash58_key(firewire_guid: bytes) -> bytes:
    """The 64-byte HMAC key for an iPod: from its FireWire GUID's first 8 bytes (libgpod's generate_key)."""
    y = bytearray(16)
    for i in range(4):
        l = _lcm(firewire_guid[i * 2], firewire_guid[i * 2 + 1])
        hi, lo = (l >> 8) & 0xFF, l & 0xFF
        y[i * 4:i * 4 + 4] = bytes((TABLE1[hi], TABLE2[hi], TABLE1[lo], TABLE2[lo]))
    return hashlib.sha1(FIXED + bytes(y)).digest().ljust(64, b"\0")


def write_hash58(data: bytearray, firewire_guid: bytes) -> None:
    """Signs a database in place: HMAC-SHA1 of the file with its id (0x18), 0x32-0x45 and the hash
    itself (0x58) zeroed, and the hashing scheme (0x30) set, into 0x58."""
    if len(data) < 0x6C or data[:4] != b"mhbd":
        raise ITunesDBError("Too small to be an iPod database")
    db_id, unk32 = bytes(data[0x18:0x20]), bytes(data[0x32:0x46])
    data[0x18:0x20] = bytes(8)
    data[0x32:0x46] = bytes(20)
    data[0x58:0x6C] = bytes(20)
    struct.pack_into("<H", data, 0x30, HASH58)
    key = hash58_key(firewire_guid)
    inner = hashlib.sha1(bytes(k ^ 0x36 for k in key) + bytes(data)).digest()
    digest = hashlib.sha1(bytes(k ^ 0x5C for k in key) + inner).digest()
    data[0x58:0x6C] = digest
    data[0x18:0x20] = db_id
    data[0x32:0x46] = unk32


# --- hash72 (nano 5G) and hashAB (nano 6G/7G) ---

# the key every iPod's hash72 is made with (libgpod's itdb_hash72.c, from Chris Lee's work)
HASH72_KEY = bytes.fromhex("618ca10dc7f57fd3b4723e08157463d7")


def signed_sha1(data: bytes, hashab: bool = False) -> bytes:
    """What an iPod's hash72 or hashAB signs: the file's SHA-1 with its id (0x18), hash58 (0x58) and
    hash72 (0x72) zeroed, and hashAB (0xAB) for hashAB (libgpod's itdb_hash72/hashAB_compute_itunesdb_sha1)."""
    copy = bytearray(data)
    copy[0x18:0x20] = bytes(8)
    copy[0x58:0x6C] = bytes(20)
    copy[0x72:0xA0] = bytes(46)
    if hashab:
        copy[0xAB:0xE4] = bytes(57)
    return hashlib.sha1(bytes(copy)).digest()


def hash72_signature(sha1: bytes, iv: bytes, random_bytes: bytes) -> bytes:
    """46 bytes: 01 00, the 12 random bytes, then AES-CBC(sha1 + random bytes) with the iPod's iv."""
    from .aes import cbc_encrypt
    return b"\x01\x00" + random_bytes + cbc_encrypt(HASH72_KEY, iv, sha1 + random_bytes)


def hash72_key_from(signature: bytes, sha1: bytes) -> Optional[tuple[bytes, bytes]]:
    """An iPod's (iv, random bytes), from a hash72 iTunes made for it and what it signed: what libgpod
    keeps as its HashInfo file. CBC's first block gives the iv away: iv = D(C1) xor P1."""
    from .aes import decrypt_block, expand_key
    if len(signature) < 46 or signature[:2] != b"\x01\x00":
        return None
    random_bytes = signature[2:14]
    first = decrypt_block(expand_key(HASH72_KEY), signature[14:30])
    iv = bytes(a ^ b for a, b in zip(first, sha1[:16]))
    if hash72_signature(sha1, iv, random_bytes) != signature[:46]:
        return None
    return iv, random_bytes


def write_hash72(data: bytearray, iv: bytes, random_bytes: bytes) -> None:
    """Signs a database for a nano 5G in place (scheme 2 at 0x30 and 0x70, the signature at 0x72)."""
    data[0x32:0x46] = bytes(20)  # libgpod writes these as zeros: so do we, before signing
    struct.pack_into("<H", data, 0x30, HASH72)
    struct.pack_into("<H", data, 0x70, 2)
    data[0x72:0xA0] = hash72_signature(signed_sha1(data), iv, random_bytes)


def write_hashab(data: bytearray, sign: Any, current: int = 0) -> None:
    """Signs a database for a nano 6G/7G in place: the 57-byte hashAB at 0xAB. The scheme at 0x30 is
    left as iTunes wrote it (libgpod writes 3, other tools 4), 4 for a new database; 4 at 0x70."""
    data[0x32:0x46] = bytes(20)
    struct.pack_into("<H", data, 0x30, current if current in (3, 4) else 4)
    struct.pack_into("<H", data, 0x70, 4)
    signature = sign(signed_sha1(data, hashab=True))
    if len(signature) != 57:
        raise ITunesDBError("The hashab helper gave a signature of the wrong size")
    data[0xAB:0xE4] = signature


def sound_check(gain_db: float) -> int:
    """Sound Check's value for a volume change in dB (libgpod: 1000 * 10^(-dB/10)); ReplayGain's is one."""
    return max(1, min(0xFFFFFFFF, round(1000 * 10 ** (-gain_db / 10))))


def sound_check_gain(value: int) -> Optional[float]:
    """The volume change a Sound Check value is, in dB (None for 0: none)."""
    return round(-10 * math.log10(value / 1000), 2) if value else None


def guid_bytes(text: str) -> Optional[bytes]:
    """"0x000A27001234ABCD" (SysInfo), "000A27001234ABCD" (the USB serial): its bytes (up to 20)."""
    hex_text = (text or "").strip()
    if hex_text[:2].lower() == "0x":
        hex_text = hex_text[2:]
    if not re.fullmatch(r"[0-9A-Fa-f]{16,40}", hex_text) or len(hex_text) % 2:
        return None
    return bytes.fromhex(hex_text)


# --- the Shuffle's iTunesSD ---

def shuffle_db(db: ITunesDB, old_format: bool) -> bytes:
    """iTunesSD for a Shuffle: the 1st/2nd generation's (big-endian, 558-byte entries) or the 3rd/4th's."""
    tracks = db.tracks

    def kind(t: Track) -> int:
        """1 MP3, 2 AAC, 4 WAV: from the track's file type text (libgpod's convert_filetype), its file's
        name when it has none."""
        ft = t.strings().get(FILETYPE, "") or t.strings().get(PATH, "")
        return 2 if re.search(r"AAC|MP4|M4A|aac|mp4|m4a", ft) else 4 if re.search(r"WAV|wav", ft) else 1

    def be24(n: int) -> bytes:
        return (n & 0xFFFFFF).to_bytes(3, "big")

    if old_format:
        out = bytearray(be24(len(tracks)) + be24(0x010600) + be24(0x12) + be24(0) * 3)
        for t in tracks:
            path = t.strings().get(PATH, "").replace(":", "/")[:261].encode("utf-16-le")
            out += (be24(0x22E) + be24(0x5AA501) + be24(0) * 6 + be24(100) + be24(kind(t)) + be24(0x200)
                    # played when shuffling: 1, as iTunes writes it (0 is "skip"; libgpod 0.8.3 writes its
                    # own skip flag, 0, here as it is), then not bookmarkable, and an unknown 0
                    + path.ljust(522, b"\0") + bytes((1, 0, 0)))
        return bytes(out)
    # the newer format: bdhs, then hths (the tracks, rths each), then hphs (the playlists, lphs each)
    rths = []
    for t in tracks:
        h = t.header
        path = t.strings().get(PATH, "").replace(":", "/").encode("utf-8")[:256]
        # flags: played when shuffling (the opposite of iTunesDB's "skip"), position not remembered
        body = (struct.pack("<IIII", 0, 0, 0, kind(t)) + path.ljust(256, b"\0") + struct.pack("<I", 0)
                + bytes((1, 0, 0, 0)) + struct.pack("<IIIIIII", 0, 0, 0, 0, 0, 0, _u32(h, 0x120))
                + struct.pack("<HH", _u32(h, 0x2C) & 0xFFFF, _u32(h, 0x5C) & 0xFFFF) + bytes(8)
                + struct.pack("<QI", t.dbid, _u32(h, 0x1E0)) + bytes(32))
        rths.append(b"rths" + struct.pack("<I", 8 + len(body)) + body)
    playlists = [p for p in db.playlists() if p.items]
    ids = {t.id: n for n, t in enumerate(tracks)}
    lphs = []
    for p in playlists:
        members = [ids[i.track_id] for i in p.items if i.track_id in ids]
        stype = 1 if p.master else 3 if p.podcasts else 2
        head = struct.pack("<II", len(members), len(members)) + (bytes(8) if p.master else bytes(p.header[0x1C:0x24]))
        body = head + struct.pack("<I", stype) + bytes(16) + struct.pack(f"<{len(members)}I", *members)
        lphs.append(b"lphs" + struct.pack("<I", 8 + len(body)) + body)
    bdhs_len = 64
    hths = 20 + 4 * len(rths)
    track_at = bdhs_len
    offsets, at = [], track_at + hths
    for r in rths:
        offsets.append(at)
        at += len(r)
    hphs_at = at
    hphs = 20 + 4 * len(lphs)
    pl_offsets, at = [], hphs_at + hphs
    for chunk in lphs:
        pl_offsets.append(at)
        at += len(chunk)
    masters = sum(1 for p in playlists if p.master)
    podcasts = sum(1 for p in playlists if p.podcasts)
    bdhs = (b"bdhs" + struct.pack("<III", 0x02000003, bdhs_len, len(tracks)) + struct.pack("<I", len(playlists))
            + bytes(8) + bytes((0, 1)) + bytes(2) + struct.pack("<III", len(tracks), track_at, hphs_at) + bytes(20))
    out = bytearray(bdhs)
    out += b"hths" + struct.pack("<II", hths, len(rths)) + bytes(8) + struct.pack(f"<{len(offsets)}I", *offsets)
    out += b"".join(rths)
    out += (b"hphs" + struct.pack("<I", hphs) + struct.pack("<HHHHHH", len(lphs), 0, len(lphs) - podcasts, masters,
                                                           len(lphs), 0)
            + struct.pack(f"<{len(pl_offsets)}I", *pl_offsets))
    out += b"".join(lphs)
    return bytes(out)
