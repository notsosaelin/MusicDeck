"""
The SQLite libraries the iPod nano 5th-7th generation play from (iPod_Control/iTunes/iTunes Library.itlp):
Library.itdb (songs, albums, artists, playlists), Locations.itdb (their files), Dynamic.itdb (plays,
ratings), Extras.itdb, Genius.itdb, and Locations.itdb.cbk, Locations.itdb's checksums signed for the iPod
(hash72 on a nano 5G, hashAB on a 6G/7G). The nano 6G/7G read nothing else; the 5G reads these too.

Made from the same database written to iTunesCDB (itunesdb.py), as libgpod 0.8.3's itdb_sqlite.c makes
them (ported from it, LGPL-2.1-or-later, Copyright (C) 2009 Nikias Bassen, Christophe Fergeau, Hector
Martin), checked against what libgpod writes (scripts/libgpod_check, tests/test_ipod.py). The iPod's own
SQL to finish them (SysInfoExtended's SQLMusicLibraryPostProcessCommands) is run when it's known.
"""
import hashlib
import os
import re
import sqlite3
import struct
from typing import Any, Callable, Optional

from . import ipod_schema
from .itunesdb import (ALBUM, ALBUMARTIST, ARTIST, COMMENT, COMPOSER, FILETYPE, GENRE, PATH, SORT_ARTIST, TITLE,
                       ITunesDB, Track, _u16, _u32, _u64, unix_time)

FILES = ("Dynamic.itdb", "Extras.itdb", "Genius.itdb", "Library.itdb", "Locations.itdb")
SORT_TITLE_MHOD, SORT_ALBUM_MHOD, SORT_ALBUMARTIST_MHOD, SORT_COMPOSER_MHOD = 27, 28, 29, 30
GROUPING, DESCRIPTION = 13, 14
APPLE_EPOCH = 978307200  # 2001-01-01, what the iPod's SQLite dates count from
CBK_BLOCK = 1024
MOVIE, MUSIC_VIDEO, TV_SHOW = 0x02, 0x20, 0x40
# a song's chapter data when it has no chapters ("sean" with only its "hedr"), as libgpod writes it
NO_CHAPTERS = bytes.fromhex("000000000000000000000000000000307365616e0000000100000001000000000000001c6865647200000001"
                            "00000000000000000000000000000001")


def _signed(n: int) -> int:
    """A 64-bit id as SQLite stores integers: signed."""
    return n - (1 << 64) if n >= 1 << 63 else n


class _Song:
    """A track's fields as the libraries want them (libgpod's Itdb_Track, as parsed from the database)."""

    def __init__(self, track: Track):
        s, h = track.strings(), track.header
        self.dbid = track.dbid
        self.title, self.artist, self.album = s.get(TITLE), s.get(ARTIST), s.get(ALBUM)
        self.albumartist, self.composer, self.genre = s.get(ALBUMARTIST), s.get(COMPOSER), s.get(GENRE)
        self.sort_title, self.sort_artist = s.get(SORT_TITLE_MHOD), s.get(SORT_ARTIST)
        self.sort_album, self.sort_albumartist = s.get(SORT_ALBUM_MHOD), s.get(SORT_ALBUMARTIST_MHOD)
        self.sort_composer = s.get(SORT_COMPOSER_MHOD)
        self.comment, self.grouping, self.description = s.get(COMMENT), s.get(GROUPING), s.get(DESCRIPTION)
        self.filetype, self.ipod_path = s.get(FILETYPE), s.get(PATH)
        self.mediatype = _u32(h, 0xD0)
        self.time_modified, self.time_played = unix_time(_u32(h, 0x20)), unix_time(_u32(h, 0x58))
        self.last_skipped = unix_time(_u32(h, 0xA0))
        self.year, self.compilation = _u32(h, 0x34), h[0x1E] if len(h) > 0x1E else 0
        self.rating, self.app_rating = (h[0x1F] if len(h) > 0x1F else 0), (h[0x79] if len(h) > 0x79 else 0)
        self.size, self.tracklen = _u32(h, 0x24), _u32(h, 0x28)
        self.track_nr, self.tracks, self.cd_nr, self.cds = _u32(h, 0x2C), _u32(h, 0x30), _u32(h, 0x5C), _u32(h, 0x60)
        self.bitrate, self.samplerate = _u32(h, 0x38), _u32(h, 0x3C) >> 16
        self.volume = struct.unpack_from("<i", h, 0x40)[0] if len(h) >= 0x44 else 0
        self.starttime, self.stoptime, self.soundcheck = _u32(h, 0x44), _u32(h, 0x48), _u32(h, 0x4C)
        self.playcount, self.bookmark, self.skipcount = _u32(h, 0x50), _u32(h, 0x6C), _u32(h, 0x9C)
        self.bpm = _u16(h, 0x7A)
        self.has_artwork = h[0xA4] if len(h) > 0xA4 else 0
        self.skip_when_shuffling = h[0xA5] if len(h) > 0xA5 else 0
        self.remember_position = h[0xA6] if len(h) > 0xA6 else 0
        self.marker = _u32(h, 0x18)
        self.pregap, self.postgap, self.gapless_data = _u32(h, 0xB8), _u32(h, 0xC8), _u32(h, 0xF8)
        self.gapless_track_flag = _u16(h, 0x100)
        self.album_id, self.artist_id, self.composer_id = _u32(h, 0x120), _u32(h, 0x1E0), _u32(h, 0x1F4)
        self.artwork_id = _u32(h, 0x160)
        self.season_nr, self.episode_nr = _u32(h, 0xD4), _u32(h, 0xD8)

    def sort_field(self, order: int) -> Optional[str]:
        """libgpod's sort_field: the first non-empty of a sort order's fields."""
        fields = {0: (self.sort_title, self.title), 1: (self.sort_artist, self.artist),
                  2: (self.sort_album, self.album), 3: (self.genre,), 4: (self.sort_composer, self.composer),
                  5: (self.sort_artist, self.artist),
                  6: (self.sort_albumartist, self.albumartist, self.sort_artist, self.artist), 7: ()}[order]
        return next((f for f in fields if f), None)


ORDER_TITLE, ORDER_ARTIST, ORDER_ALBUM, ORDER_GENRE, ORDER_COMPOSER, ORDER_ALBUM_ARTIST, ORDER_ALBUM_BY_ARTIST, \
    ORDER_SERIES_NAME = range(8)


def _orders(songs: list[_Song]) -> list[dict[str, int]]:
    """Each sort order's position of every name: 100, 200... in sorted order (libgpod's compute_key_orders;
    it sorts with the locale's collation, here case-insensitively, as an English locale does)."""
    out = []
    for order in range(8):
        names = sorted({n for n in (s.sort_field(order) for s in songs) if n}, key=lambda n: (n.casefold(), n))
        out.append({n: (i + 1) * 100 for i, n in enumerate(names)})
    return out


def _order(orders: list[dict[str, int]], order: int, song: _Song) -> int:
    key = song.sort_field(order)
    return orders[order].get(key, 100) if key else 100


def _first(*values: Optional[str]) -> Optional[str]:
    return next((v for v in values if v), None)


def _create(path: str, statements: tuple[str, ...]) -> sqlite3.Connection:
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA synchronous = OFF;")
    db.executescript("".join(statements))
    return db


def _playlist_rows(itdb: ITunesDB) -> list[dict[str, Any]]:
    """The playlists (master first), each with its id, name, time, flags and members' dbids in order."""
    by_id = {t.id: t for t in itdb.tracks}
    rows = []
    for p in itdb.playlists():
        h = p.header
        rows.append({"id": _u64(h, 0x1C), "name": p.name, "master": p.master, "podcast": p.podcasts,
                     "timestamp": unix_time(_u32(h, 0x18)), "sortorder": _u32(h, 0x2C),
                     "members": [by_id[i.track_id] for i in p.items if i.track_id in by_id]})
    return rows


def write(itdb: ITunesDB, outdir: str, sign: Callable[[bytes], bytes], cbk_header: int,
          post_process: Optional[list[str]] = None) -> None:
    """The five libraries and their signed checksums, into `outdir` (a folder of their own: the caller
    moves them onto the iPod). `sign(sha1)` signs the checksums (hash72: 46 bytes, hashAB: 57);
    `post_process`: the iPod's own SQL commands to run on them, in order."""
    header = itdb.header
    tz = struct.unpack_from("<i", header, 0x6C)[0] if len(header) >= 0x70 else 0

    def timeconv(t: float) -> int:
        # libgpod's timeconv: seconds since 2001, less the database's time zone offset, as 32 bits
        return (int(t) - APPLE_EPOCH - tz + (1 << 31)) % (1 << 32) - (1 << 31)

    songs = [_Song(t) for t in itdb.tracks]
    playlists = _playlist_rows(itdb)
    os.makedirs(outdir, exist_ok=True)

    # Dynamic.itdb: plays, ratings, bookmarks; each playlist's view
    db = _create(os.path.join(outdir, "Dynamic.itdb"), ipod_schema.DYNAMIC_CREATE)
    db.execute("BEGIN;")
    for s in songs:
        if not s.ipod_path:
            continue
        db.execute("INSERT INTO item_stats (item_pid,has_been_played,date_played,play_count_user,play_count_recent,"
                   "date_skipped,skip_count_user,skip_count_recent,bookmark_time_ms,bookmark_time_ms_common,"
                   "user_rating,user_rating_common) VALUES(?,?,?,?,?,?,?,?,?,?,?,?);",
                   (_signed(s.dbid), 1 if s.playcount else 0, timeconv(s.time_played), s.playcount, 0,
                    timeconv(s.last_skipped), s.skipcount, 0, float(s.bookmark), float(s.bookmark), s.rating,
                    s.app_rating))
    for p in playlists:
        play_order = {5: 7, 24: 39}.get(p["sortorder"], 7)
        db.execute("INSERT INTO container_ui VALUES(?,?,?,?,?,?,?);", (_signed(p["id"]), play_order, 0, 1, 0, 0, 0))
    db.execute("COMMIT;")
    db.close()

    # Extras.itdb: a song's chapters, as libgpod writes them: the empty list for each song (lyrics aren't
    # written); Genius.itdb: its tables
    db = _create(os.path.join(outdir, "Extras.itdb"), ipod_schema.EXTRAS_CREATE)
    db.execute("BEGIN;")
    for s in songs:
        db.execute("INSERT INTO chapter VALUES(?,?);", (_signed(s.dbid), NO_CHAPTERS))
    db.execute("COMMIT;")
    db.close()
    _create(os.path.join(outdir, "Genius.itdb"), ipod_schema.GENIUS_CREATE).close()

    # Library.itdb: the songs, their albums, artists, composers and genres, the playlists
    db = _create(os.path.join(outdir, "Library.itdb"), ipod_schema.LIBRARY_CREATE)
    db.execute("BEGIN;")
    version = _u32(header, 0x10)
    db.execute("INSERT INTO version_info VALUES(?,?,?,?,?,?,?);",
               (1, 2 if version >= 0x28 else 1, version, 0, 0, 0, _u16(header, 0x20)))
    genres: dict[str, int] = {}
    for s in songs:
        if s.genre is not None and s.genre not in genres:
            genres[s.genre] = len(genres) + 1
            db.execute("INSERT INTO genre_map (id,genre,genre_order) VALUES(?,?,0);", (genres[s.genre], s.genre))
    master = None
    for pos, p in enumerate(playlists):
        if p["master"]:
            master = p
        kinds = 0
        for n, t in enumerate(p["members"]):
            db.execute("INSERT INTO item_to_container VALUES(?,?,?,?);", (_signed(t.dbid), _signed(p["id"]), n, None))
            kinds |= _u32(t.header, 0xD0)
        db.execute("INSERT INTO container VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,"
                   "NULL,NULL);",
                   (_signed(p["id"]), 11 if p["podcast"] else 0, timeconv(p["timestamp"]), timeconv(p["timestamp"]),
                    p["name"], pos, 0, kinds, 0, 1 if (p["podcast"] or p["master"]) else 0, 0))
    if master is None:
        raise ValueError("The database has no master playlist")
    db.execute("INSERT INTO db_info VALUES(?,?,?,?,?,?,?,?);",
               (_signed(_u64(header, 0x48)), _signed(master["id"]), None, _u16(header, 0xA0), _u16(header, 0xA2),
                None, None, None))
    # the ids libgpod gives albums and artists in SQLite: the album and artist lists' own (mhia/mhii +20)
    album_sql = {e.id: _u64(e.header, 20) for e in (itdb.section(4).entries if itdb.section(4) else [])}
    artist_sql = {e.id: _u64(e.header, 20) for e in (itdb.section(8).entries if itdb.section(8) else [])}
    orders = _orders(songs)
    for s in songs:
        album_pid = album_sql.get(s.album_id, 0) if s.album is not None else 0
        artist_pid = artist_sql.get(s.artist_id, 0) if s.artist is not None else 0
        composer_pid = (int.from_bytes(hashlib.sha1(f"composer:{s.composer}".encode()).digest()[:8], "little")
                        if s.composer is not None else 0)
        genre_id = genres.get(s.genre, 0) if s.genre else 0
        db.execute(
            "INSERT INTO item (pid,media_kind,date_modified,year,is_compilation,remember_bookmark,exclude_from_shuffle,"
            "artwork_status,artwork_cache_id,start_time_ms,stop_time_ms,total_time_ms,total_burn_time_ms,track_number,"
            "track_count,disc_number,disc_count,bpm,relative_volume,eq_preset,radio_stream_status,genre_id,album_pid,"
            "artist_pid,composer_pid,title,artist,album,album_artist,composer,sort_title,sort_artist,sort_album,"
            "sort_album_artist,sort_composer,title_order,artist_order,album_order,genre_order,composer_order,"
            "album_artist_order,album_by_artist_order,series_name_order,comment,grouping,description,description_long) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);",
            (_signed(s.dbid), s.mediatype, timeconv(s.time_modified), s.year, s.compilation, s.remember_position,
             s.skip_when_shuffling, s.has_artwork, s.artwork_id, float(s.starttime), float(s.stoptime),
             float(s.tracklen), None, s.track_nr, s.tracks, s.cd_nr, s.cds, s.bpm, s.volume, None, None, genre_id,
             _signed(album_pid), _signed(artist_pid), _signed(composer_pid),
             _first(s.title), _first(s.artist), _first(s.album), _first(s.albumartist), _first(s.composer),
             _first(s.sort_title, s.title), _first(s.sort_artist, s.artist), _first(s.sort_album, s.album),
             _first(s.sort_albumartist, s.albumartist), _first(s.sort_composer, s.composer),
             _order(orders, ORDER_TITLE, s), _order(orders, ORDER_ARTIST, s), _order(orders, ORDER_ALBUM, s),
             _order(orders, ORDER_GENRE, s), _order(orders, ORDER_COMPOSER, s), _order(orders, ORDER_ALBUM_ARTIST, s),
             _order(orders, ORDER_ALBUM_BY_ARTIST, s), _order(orders, ORDER_SERIES_NAME, s), s.comment,
             _first(s.grouping), _first(s.description), None))
        db.execute("INSERT OR IGNORE INTO location_kind_map VALUES(?,?);", (s.mediatype, s.filetype))
        marker = struct.pack(">I", s.marker)
        audio_format = 301 if marker == b"MP3 " else 502 if marker in (b"M4A ", b"M4R ", b"MP4 ") else 0
        db.execute("INSERT INTO avformat_info VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?);",
                   (_signed(s.dbid), 0, audio_format, s.bitrate, float(s.samplerate), 0, s.gapless_track_flag,
                    s.pregap, s.postgap, s.gapless_data, 0, 0, s.soundcheck))
        if s.album is not None and album_pid:
            db.execute("INSERT OR IGNORE INTO album VALUES(?,?,?,?,?,?,?,?,?,?,?);",
                       (_signed(album_pid), 2, 0, 0, _signed(artist_pid), 0, s.album,
                        _order(orders, ORDER_ALBUM_ARTIST, s), 0, None, 0))
        if s.artist is not None and artist_pid:
            db.execute("INSERT OR IGNORE INTO artist VALUES(?,?,?,?,?,?,?);",
                       (_signed(artist_pid), 2, 0, 0, s.artist, _order(orders, ORDER_ARTIST, s), s.artist))
        if s.composer is not None:
            db.execute("INSERT OR IGNORE INTO composer (pid,name,name_order,sort_name) VALUES(?,?,?,?);",
                       (_signed(composer_pid), s.composer, _order(orders, ORDER_COMPOSER, s), s.composer))
        if s.mediatype & (MOVIE | MUSIC_VIDEO | TV_SHOW):
            strings = next(t for t in itdb.tracks if t.dbid == s.dbid).strings()
            db.execute("INSERT INTO video_info VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);",
                       (_signed(s.dbid), 0, 0, 0, 0, 0, 0, 0, s.season_nr, 0, 0, 0, 0, 0, 0, strings.get(19), None,
                        strings.get(20), (s.season_nr << 16) | s.episode_nr, strings.get(21), None, None))
    db.execute("UPDATE album SET artwork_item_pid = (SELECT item.pid FROM item WHERE item.artwork_cache_id != 0 "
               "AND item.album_pid = album.pid LIMIT 1);")
    db.execute("COMMIT;")
    db.close()

    # Locations.itdb: where each song's file is, under iPod_Control/Music
    db = _create(os.path.join(outdir, "Locations.itdb"), ipod_schema.LOCATIONS_CREATE)
    db.execute("BEGIN;")
    db.execute("INSERT INTO base_location (id, path) VALUES (?,?);", (1, "iPod_Control/Music"))
    for s in songs:
        if not s.ipod_path:
            continue
        # ":iPod_Control:Music:F01:ABCD.mp3" -> "F01/ABCD.mp3": the path after its third ":"
        parts = s.ipod_path.split(":")
        location = "/".join(parts[3:]) if len(parts) > 3 else s.ipod_path.replace(":", "/")
        db.execute("INSERT INTO location (item_pid, sub_id, base_location_id, location_type, location, extension, "
                   "kind_id, date_created, file_size) VALUES(?,?,?,?,?,?,?,?,?);",
                   (_signed(s.dbid), 0, 1, 0x46494C45, location, s.marker, s.mediatype, timeconv(s.time_modified),
                    s.size))
    db.execute("COMMIT;")
    db.close()

    if post_process:
        run_post_process(outdir, post_process)
    write_cbk(outdir, sign, cbk_header)


def write_cbk(outdir: str, sign: Callable[[bytes], bytes], header_size: int) -> None:
    """Locations.itdb.cbk: the signature, then the SHA-1 of the SHA-1s, then the SHA-1 of every whole
    1024-byte block of Locations.itdb (libgpod's mk_Locations_cbk)."""
    with open(os.path.join(outdir, "Locations.itdb"), "rb") as f:
        data = f.read()
    sha1s = b"".join(hashlib.sha1(data[o:o + CBK_BLOCK]).digest()
                     for o in range(0, len(data) - len(data) % CBK_BLOCK, CBK_BLOCK))
    final = hashlib.sha1(sha1s).digest()
    signature = sign(final)
    if len(signature) != header_size:
        raise ValueError("The checksums' signature is the wrong size for this iPod")
    with open(os.path.join(outdir, "Locations.itdb.cbk"), "wb") as f:
        f.write(signature + final + sha1s)


def _sort_key(value: Any) -> Optional[bytes]:
    """The iPod's iPhoneSortKey SQL function, as libgpod imitates it."""
    if value is None:
        return b"\x31\x01\x01\x00"
    if not isinstance(value, str):
        return None
    upper = value.encode("utf-8", "replace").upper()
    if not upper:
        return b"\x31\x01\x01\x00"
    words, length = 0, 0
    for ch in upper:
        if 48 <= ch <= 57 or 65 <= ch <= 90:
            length += 1
        elif ch == 32:
            words += 1
            length += 1
        else:
            length += 2
    words += 1
    offset = 1 + length + 3
    buf = bytearray(offset + words * 2 + 1)
    i, idx, word_count, word_len = 0, 0, 0, 0
    buf[idx] = 0x30
    idx += 1
    for ch in upper:
        word_len += 1
        if 48 <= ch <= 57 or 65 <= ch <= 90:
            buf[idx] = (ch - (0x55 - ch)) & 0xFF
            idx += 1
        elif ch == 32:
            buf[idx] = 0x06
            idx += 1
            word_len -= 1
            buf[offset + word_count * 2] = 0x8F
            buf[offset + word_count * 2 + 1] = (0x86 - word_len) & 0xFF
            word_count += 1
            word_len = 0
        else:
            second = {58: 0xD8, 45: 0x90, 44: 0xB2, 46: 0x51, 39: 0x31}.get(ch, 0x90)
            buf[idx] = 0x08 if ch == 46 else 0x07
            buf[idx + 1] = second
            idx += 2
        i += 1
    buf[offset + word_count * 2] = 0x8F
    buf[offset + word_count * 2 + 1] = (3 + word_len) & 0xFF
    buf[offset - 3], buf[offset - 2], buf[offset - 1] = 0x01, (i + 4) & 0xFF, 0x01
    return bytes(buf)


def _sort_section(value: Any) -> int:
    """The iPod's iPhoneSortSection SQL function: which letter's section a sort key is in (26: the rest)."""
    raw = value if isinstance(value, bytes) else value.encode() if isinstance(value, str) else b""
    if len(raw) > 1 and raw[0] == 0x30 and 0x2D <= raw[1] <= 0x5F:
        return (raw[1] - 0x2D) // 2
    return 26


def run_post_process(outdir: str, commands: list[str]) -> int:
    """The iPod's own SQL, run on the libraries with the others attached (libgpod's
    run_post_process_commands); returns how many ran. One that fails is skipped, as libgpod does."""
    db = sqlite3.connect(os.path.join(outdir, "Library.itdb"), isolation_level=None)
    for name in ("Dynamic.itdb", "Extras.itdb", "Genius.itdb", "Locations.itdb"):
        try:
            db.execute(f"ATTACH DATABASE ? AS '{name}';", (os.path.join(outdir, name),))
        except sqlite3.Error:
            pass
    db.create_function("iPhoneSortKey", 1, _sort_key)
    db.create_function("iPhoneSortSection", 1, _sort_section)
    ran = 0
    db.execute("BEGIN;")
    for sql in commands:
        try:
            db.executescript(sql) if ";" in sql.strip().rstrip(";") else db.execute(sql)
            ran += 1
        except sqlite3.Error:
            pass
    try:
        db.execute("COMMIT;")
    except sqlite3.Error:
        pass
    db.close()
    return ran


def post_process_commands(sysinfo_extended: str) -> list[str]:
    """The SQL a nano's SysInfoExtended asks to run on its libraries: SQLMusicLibraryPostProcessCommands'
    highest-numbered UserVersionCommandSets' Commands, each looked up in its SQLCommands. Read by pattern
    (Decky's Python has no XML tree parser)."""
    m = re.search(r"<key>com\.apple\.mobile\.iTunes\.SQLMusicLibraryPostProcessCommands</key>\s*<dict>(.*)",
                  sysinfo_extended, re.S)
    if not m:
        return []
    body = m.group(1)
    sql = dict(re.findall(r"<key>([^<]+)</key>\s*<string>([^<]*)</string>",
                          _dict_after(body, "SQLCommands")))
    sets = _dict_after(body, "UserVersionCommandSets")
    best, commands = -1, []
    for number, inner in re.findall(r"<key>(\d+)</key>\s*<dict>(.*?)</dict>", sets, re.S):
        names = re.findall(r"<string>([^<]*)</string>", _array_after(inner, "Commands"))
        if int(number) > best and names:
            best, commands = int(number), names
    import html
    return [html.unescape(sql[n]) for n in commands if n in sql]


def _dict_after(text: str, key: str) -> str:
    """The <dict>...</dict> following <key>key</key>, nested dicts included."""
    m = re.search(rf"<key>{re.escape(key)}</key>\s*<dict>", text)
    if not m:
        return ""
    depth, i = 1, m.end()
    for tag in re.finditer(r"<(/?)dict>", text[i:]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return text[i:i + tag.start()]
    return text[i:]


def _array_after(text: str, key: str) -> str:
    m = re.search(rf"<key>{re.escape(key)}</key>\s*<array>(.*?)</array>", text, re.S)
    return m.group(1) if m else ""
