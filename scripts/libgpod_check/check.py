"""
MusicDeck's iPod code against libgpod itself (after setup.sh, on Linux or WSL): run as
    python3 scripts/libgpod_check/check.py [--fixtures]

* libgpod writes databases for an iPod classic (signed with hash58) and two shuffles (with iTunesSD);
  musicdeck/itunesdb.py must read them, write them back byte for byte, sign them the same, and make the
  same sorted lists and iTunesSD files.
* musicdeck/itunesdb.py writes a database from nothing and changes libgpod's; libgpod must read both.
* --fixtures: copies libgpod's databases to tests/fixtures/ipod, which tests/test_ipod.py checks against
  where libgpod isn't (Windows, CI).
"""
import importlib.util
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("itunesdb", os.path.join(REPO, "py_modules", "musicdeck", "itunesdb.py"))
i = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i)
HOME = os.path.expanduser("~/gpod")
ENV = {**os.environ, "LD_LIBRARY_PATH": f"{HOME}/root/usr/lib/x86_64-linux-gnu", "LC_ALL": "en_US.UTF-8",
       "LOCPATH": f"{HOME}/locale"}
GUID_TEXT = "0x000A2700138F1F2E"
GUID = i.guid_bytes(GUID_TEXT)
SONGS = ["Zebra", "The Beatles", "Abbey Road", ":iPod_Control:Music:F01:A.mp3",
         "apple", "Artist B", "B Side", ":iPod_Control:Music:F02:B.m4a",
         "9 Lives", "Artist B", "B Side", ":iPod_Control:Music:F03:C.mp3"]
failed = []


def check(ok, message):
    print(("  ok - " if ok else "  FAILED - ") + message)
    if not ok:
        failed.append(message)


def gpod(*args):
    return subprocess.run([f"{HOME}/tool/gpodtool", *args], capture_output=True, text=True, env=ENV)


def pretend(name, model):
    d = f"{HOME}/check-{name}"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(f"{d}/iPod_Control/iTunes")
    os.makedirs(f"{d}/iPod_Control/Device")
    with open(f"{d}/iPod_Control/Device/SysInfo", "w") as f:
        f.write(f"ModelNumStr: {model}\nFirewireGuid: {GUID_TEXT}\n")
    return d


def read(path):
    with open(path, "rb") as f:
        return f.read()


written = {}
for name, model in (("classic", "xB147"), ("shuffle2", "xA546"), ("shuffle4", "xC584")):
    print(name)
    d = pretend(name, model)
    r = gpod("make", d, *SONGS)
    if r.returncode != 0:
        sys.exit(f"libgpod couldn't write: {r.stderr}")
    data = read(f"{d}/iPod_Control/iTunes/iTunesDB")
    written[name] = d
    db = i.ITunesDB.parse(data)
    check(db.to_bytes(GUID, db.hashing_scheme) == data, "read and written back byte for byte, signed the same")
    master = next(p for p in db.section(2).playlists if p.master)
    check([m for m in master.mhods if i._u32(m, 12) in (52, 53)] == i._index_mhods(db.tracks),
          "the same sorted lists")
    if name.startswith("shuffle"):
        theirs = bytearray(read(f"{d}/iPod_Control/iTunes/iTunesSD"))
        if name == "shuffle2":
            for n in range(len(db.tracks)):
                theirs[18 + 558 * n + 0x22B] = 1  # iTunes' "play when shuffling", which libgpod leaves 0
        check(i.shuffle_db(db, old_format=name == "shuffle2") == bytes(theirs), "the same iTunesSD")

print("ours, read by libgpod")
fresh = pretend("ours", "xB147")
db = i.new_database("Sam's iPod")
for n, (title, artist, album) in enumerate([("Zebra", "The Beatles", "Abbey Road"), ("Énigme", "Artist B", "B Side")], 1):
    db.add_track({"title": title, "artist": artist, "album": album, "path": f"/iPod_Control/Music/F0{n}/T{n}.mp3",
                  "filetype": "MPEG audio file", "size": 1000, "duration": 61.5, "track": n})
db.rebuild_master()
db.add_playlist("Road trip", [db.tracks[1].id, db.tracks[0].id])
with open(f"{fresh}/iPod_Control/iTunes/iTunesDB", "wb") as f:
    f.write(db.to_bytes(GUID, i.HASH58))
out = gpod("dump", fresh).stdout
check("track|Énigme|Artist B|B Side|:iPod_Control:Music:F02:T2.mp3|61500" in out
      and "playlist|Road trip|0|2|Énigme|Zebra" in out and "playlist|Sam's iPod|1|2" in out,
      "a database made from nothing")
edited = written["classic"]
db = i.ITunesDB.parse(read(f"{edited}/iPod_Control/iTunes/iTunesDB"))
db.add_track({"title": "Fourth", "artist": "Artist A", "album": "Album X", "path": "/iPod_Control/Music/F00/D.m4a",
              "filetype": "AAC audio file", "size": 5, "duration": 3})
db.remove_tracks({db.tracks[1].id})
db.rebuild_master()
with open(f"{edited}/iPod_Control/iTunes/iTunesDB", "wb") as f:
    f.write(db.to_bytes(GUID, i.HASH58))
out = gpod("dump", edited).stdout
check("tracks 3" in out and "track|Fourth|" in out and "|apple|" not in out and "playlist|Test iPod|1|3" in out,
      "libgpod's database, changed")

print("nano 5G: libgpod's SQLite libraries and signatures")
sys.path.insert(0, os.path.join(REPO, "py_modules"))
import sqlite3  # noqa: E402
from musicdeck import ipod_sqlite  # noqa: E402
from musicdeck import itunesdb as pkg_itunesdb  # noqa: E402
nano = pretend("nano5", "xC027")
IV, RANDOM = bytes(range(16, 32)), bytes(range(1, 13))
with open(f"{nano}/iPod_Control/Device/HashInfo", "wb") as f:
    f.write(b"HASHv0" + GUID.ljust(20, b"\0") + RANDOM + IV)
r = gpod("make", nano, *SONGS)
if r.returncode != 0:
    sys.exit(f"libgpod couldn't write for a nano 5G: {r.stderr}")
cdb = read(f"{nano}/iPod_Control/iTunes/iTunesCDB")
key = pkg_itunesdb.hash72_key_from(cdb[0x72:0xA0], pkg_itunesdb.signed_sha1(cdb))
again = bytearray(cdb)
pkg_itunesdb.write_hash72(again, IV, RANDOM)
check(key == (IV, RANDOM) and bytes(again) == cdb, "hash72: the key recovered, and the same signature")
theirs_dir = f"{nano}/iPod_Control/iTunes/iTunes Library.itlp"
ours_dir = f"{HOME}/check-nano5-ours"
shutil.rmtree(ours_dir, ignore_errors=True)
ipod_sqlite.write(pkg_itunesdb.ITunesDB.parse(cdb), ours_dir, lambda s: pkg_itunesdb.hash72_signature(s, IV, RANDOM), 46)
for name in ipod_sqlite.FILES:
    a, b = sqlite3.connect(f"{theirs_dir}/{name}"), sqlite3.connect(f"{ours_dir}/{name}")
    for (table,) in a.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        rows_a = sorted(a.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
        rows_b = sorted(b.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
        check(rows_a == rows_b, f"{name} {table}: {len(rows_a)} rows the same")

print("covers: libgpod's ArtworkDB and ours")
import struct  # noqa: E402
import zlib  # noqa: E402
from musicdeck import ipod_artwork  # noqa: E402


def png(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class Flat:  # ffmpeg's part (pixels): the ArtworkDB doesn't depend on them
    def prepare(self, data):
        return "cover", 300, 200

    def scale(self, path, width, height, fmt):
        return b"\0\xf8" * (width * height)


cover = png(300, 200)
with open(f"{HOME}/cover.png", "wb") as f:
    f.write(cover)
covered = pretend("covers", "xB147")
r = gpod("makeart", covered, f"{HOME}/cover.png", *SONGS)
if r.returncode != 0:
    sys.exit(f"libgpod couldn't write covers: {r.stderr}")
theirs_art = read(f"{covered}/iPod_Control/Artwork/ArtworkDB")
check(ipod_artwork.ArtworkDB.parse(theirs_art).to_bytes() == theirs_art, "libgpod's ArtworkDB read and written back")
plain = pretend("covers-ours", "xB147")
gpod("make", plain, *SONGS)
db = pkg_itunesdb.ITunesDB.parse(read(f"{plain}/iPod_Control/iTunes/iTunesDB"))
for written_path, final in ipod_artwork.add_covers(f"{plain}/iPod_Control", "classic_1", "", db.tracks,
                                                   [(t, cover) for t in db.tracks], plain, Flat()):
    os.replace(written_path, final)
with open(f"{plain}/iPod_Control/iTunes/iTunesDB", "wb") as f:
    f.write(db.to_bytes(GUID, db.hashing_scheme))


def masked(data):
    data = bytearray(data)
    at = data.find(b"mhii")
    while at >= 0:  # its song id (gpodtool numbers makeart's tracks differently) and its date
        data[at + 0x14:at + 0x1C], data[at + 0x28:at + 0x2C] = bytes(8), bytes(4)
        at = data.find(b"mhii", at + 4)
    return bytes(data)


check(masked(read(f"{plain}/iPod_Control/Artwork/ArtworkDB")) == masked(theirs_art),
      "ours the same as libgpod's for the same covers")
out = gpod("art", plain).stdout
check(out.count("|320x214:") == 3 and out.count("|56x38:") == 3 and "|1|101|101|" in out,
      "libgpod reads ours: each track's cover, at each size")

if "--fixtures" in sys.argv:
    dest = os.path.join(REPO, "tests", "fixtures", "ipod")
    os.makedirs(dest, exist_ok=True)
    for name in ("classic", "shuffle2", "shuffle4"):
        d = pretend(name, {"classic": "xB147", "shuffle2": "xA546", "shuffle4": "xC584"}[name])
        gpod("make", d, *SONGS)
        shutil.copyfile(f"{d}/iPod_Control/iTunes/iTunesDB", os.path.join(dest, f"{name}-iTunesDB"))
        if name.startswith("shuffle"):
            shutil.copyfile(f"{d}/iPod_Control/iTunes/iTunesSD", os.path.join(dest, f"{name}-iTunesSD"))
    shutil.copyfile(f"{covered}/iPod_Control/Artwork/ArtworkDB", os.path.join(dest, "classic-ArtworkDB"))
    print("fixtures written to", dest)

sys.exit(1 if failed else 0)
