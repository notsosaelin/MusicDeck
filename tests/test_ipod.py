"""
iPods: the iTunesDB format (musicdeck/itunesdb.py) against databases libgpod 0.8.3 wrote (tests/fixtures/
ipod: an iPod classic's, signed with hash58, and two shuffles' with their iTunesSD files; made by
scripts/libgpod_check), then syncing a pretend iPod classic end to end (musicdeck/ipod.py,
services/ipod.py). Run: python tests/test_ipod.py
"""
import asyncio
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "py_modules")]
sys.modules.setdefault("decky", types.SimpleNamespace(logger=logging.getLogger("decky")))

from musicdeck import ipod, itunesdb  # noqa: E402
from musicdeck.itunesdb import HASH58, HASH72, HASHAB, ITunesDB  # noqa: E402
from musicdeck.services.ipod import IpodService  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "ipod")
GUID = itunesdb.guid_bytes("0x000A2700138F1F2E")  # the fixtures' iPods' FireWire GUID


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("  ok -", message)


def fixture(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def make_wav(path: str, seconds: float = 1.0) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0\0\0\0" * int(44100 * seconds))


def tag(path: str, title: str, artist: str, album: str, track: int) -> None:
    from mutagen import File
    audio = File(path, easy=True)
    if audio.tags is None:
        audio.add_tags()
    audio["title"], audio["artist"], audio["album"], audio["tracknumber"] = title, artist, album, str(track)
    audio.save()


def pretend_ipod(model: str = "xB147", db: bytes = b"") -> str:
    """A mounted iPod as Linux shows one: iPod_Control with its Device/SysInfo, iTunes and Music."""
    root = tempfile.mkdtemp(prefix="ipod-")
    control = os.path.join(root, "iPod_Control")
    for sub in ("Device", "iTunes", *[os.path.join("Music", f"F{n:02d}") for n in range(3)]):
        os.makedirs(os.path.join(control, sub))
    with open(os.path.join(control, "Device", "SysInfo"), "w") as f:
        f.write(f"ModelNumStr: {model}\nFirewireGuid: 0x000A2700138F1F2E\n")
    if db:
        with open(os.path.join(control, "iTunes", "iTunesDB"), "wb") as f:
            f.write(db)
    return root


def signed_right(data: bytes) -> bool:
    again = bytearray(data)
    again[0x58:0x6C] = bytes(20)
    itunesdb.write_hash58(again, GUID)
    return bytes(again) == data


def format_against_libgpod() -> None:
    print("the iTunesDB format, against libgpod")
    classic = fixture("classic-iTunesDB")
    check(signed_right(classic), "hash58 signs a database as libgpod does")
    db = ITunesDB.parse(classic)
    check([t.fields()["title"] for t in db.tracks] == ["Zebra", "apple", "9 Lives"] and db.name == "Test iPod",
          "its tracks and the iPod's name read")
    check(db.to_bytes(GUID, db.hashing_scheme) == classic, "written back byte for byte, signature and all")
    master = next(p for p in db.section(2).playlists if p.master)
    check([m for m in master.mhods if itunesdb._u32(m, 12) in (52, 53)] == itunesdb._index_mhods(db.tracks),
          "the iPod's sorted lists (songs, artists, albums, genres, composers) made as libgpod makes them")
    shuffle4 = ITunesDB.parse(fixture("shuffle4-iTunesDB"))
    check(itunesdb.shuffle_db(shuffle4, old_format=False) == fixture("shuffle4-iTunesSD"),
          "a 3rd/4th generation shuffle's iTunesSD as libgpod writes it")
    old = itunesdb.shuffle_db(ITunesDB.parse(fixture("shuffle2-iTunesDB")), old_format=True)
    theirs = bytearray(fixture("shuffle2-iTunesSD"))
    for n in range(len(ITunesDB.parse(fixture("shuffle2-iTunesDB")).tracks)):
        theirs[18 + 558 * n + 0x22B] = 1  # "play when shuffling": iTunes' 1, where libgpod writes 0
    check(old == bytes(theirs), "a 1st/2nd generation shuffle's iTunesSD (but its play-when-shuffling flag)")
    fresh = itunesdb.new_database("Sam's iPod")
    fresh.add_track({"title": "Énigme", "artist": "The Beatles", "album": "Abbey Road",
                     "path": "/iPod_Control/Music/F00/ABCD.mp3", "filetype": "MPEG audio file", "duration": 61.5})
    fresh.rebuild_master()
    data = fresh.to_bytes(GUID, HASH58)
    back = ITunesDB.parse(data)
    f = back.tracks[0].fields()
    check(signed_right(data) and back.name == "Sam's iPod" and f["title"] == "Énigme" and f["duration"] == 61.5
          and f["path"] == "/iPod_Control/Music/F00/ABCD.mp3" and len(back.section(4).entries) == 1
          and len(back.section(8).entries) == 1, f"a database made from nothing reads back: {f}")


def identify() -> None:
    print("which iPod")
    root = pretend_ipod("xB147", fixture("classic-iTunesDB"))
    info = ipod.identify(root)
    check(info.generation == "classic_1" and info.scheme == HASH58 and info.guid == GUID and info.why_not() == "",
          f"an 80 GB iPod classic: hash58, its GUID from SysInfo, can be synced: {info}")
    for model, words in (("xC027", "only iTunes can sign"), ("xC525", "key MusicDeck doesn't have")):
        nano = ipod.identify(pretend_ipod(model))
        check(words in nano.why_not(), f"{nano.model}: played, not synced ({nano.why_not()})")
    blind = pretend_ipod("", b"")
    os.remove(os.path.join(blind, "iPod_Control", "Device", "SysInfo"))
    check("couldn't be identified" in ipod.identify(blind).why_not(), "an iPod that can't be told isn't changed")
    signed = pretend_ipod("", fixture("classic-iTunesDB"))
    os.remove(os.path.join(signed, "iPod_Control", "Device", "SysInfo"))
    check(ipod.identify(signed).scheme == HASH58, "without SysInfo, its database's signature says how to sign")
    check(not ipod.identify(tempfile.mkdtemp()), "a stick isn't an iPod")


def sync() -> None:
    print("syncing an iPod classic")
    root = pretend_ipod("xB147", fixture("classic-iTunesDB"))
    itunes = os.path.join(root, "iPod_Control", "iTunes")
    # what the iPod counted since the last sync: 3 plays and 4 stars for its first track, and an On-The-Go list
    with open(os.path.join(itunes, "Play Counts"), "wb") as f:
        f.write(b"mhdp" + struct.pack("<III", 0x60, 0x10, 3) + bytes(0x60 - 16)
                + struct.pack("<IIII", 3, 3800000000, 0, 80) + bytes(32))
    with open(os.path.join(itunes, "OTGPlaylistInfo"), "wb") as f:
        f.write(b"mhpo" + struct.pack("<III", 0x14, 4, 2) + bytes(4) + struct.pack("<II", 2, 0))
    music = tempfile.mkdtemp()
    song = os.path.join(music, "song.wav")
    make_wav(song)
    info = ipod.identify(root)
    backups = tempfile.mkdtemp()
    done = []
    added = ipod.add(info, [{"path": song, "title": "New Song", "artist": "Band C", "album": "Live", "track": 1}],
                     "Road trip", backups, done.append)
    data = open(os.path.join(itunes, "iTunesDB"), "rb").read()
    db = ITunesDB.parse(data)
    new = db.tracks[-1].fields()
    check(added == 1 and done == [1] and new["title"] == "New Song" and new["filetype"] == "WAV audio file"
          and os.path.isfile(ipod.on_disk(root, new["path"])) and new["path"].startswith("/iPod_Control/Music/F0"),
          f"a song copied into iPod_Control/Music/Fnn and its library: {new}")
    check(signed_right(data), "the library signed for this iPod")
    check(db.tracks[0].fields()["plays"] == 3 and db.tracks[0].fields()["rating"] == 4
          and os.path.isfile(os.path.join(itunes, "Play Counts.bak"))
          and not os.path.exists(os.path.join(itunes, "Play Counts")),
          "plays and ratings made on the iPod kept, and not counted twice")
    names = {p.name: [i.track_id for i in p.items] for p in db.playlists()}
    check(names.get("On-The-Go") == [db.tracks[2].id, db.tracks[0].id] and "Road trip" in names
          and not os.path.exists(os.path.join(itunes, "OTGPlaylistInfo")),
          f"its On-The-Go playlist kept as a playlist, and the copied one made: {list(names)}")
    check(os.path.isfile(os.path.join(backups, "iTunesDB.previous")), "the library as it was kept on the Deck")
    again = ipod.add(info, [{"path": song, "title": "New Song", "artist": "Band C", "album": "Live"}], "", backups,
                     done.append)
    check(again == 0 and len(ITunesDB.parse(open(os.path.join(itunes, "iTunesDB"), "rb").read()).tracks) == 4,
          "a song already on it isn't added twice")
    if shutil.which("ffmpeg"):
        flac = os.path.join(music, "lossless.flac")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", song, flac], check=True)
        tag(flac, "Lossless", "Band C", "Live", 2)
        ipod.add(info, [{"path": flac, "title": "Lossless", "artist": "Band C", "album": "Live"}], "", backups,
                 done.append)
        alac = ITunesDB.parse(open(os.path.join(itunes, "iTunesDB"), "rb").read()).tracks[-1].fields()
        check(alac["path"].endswith(".m4a") and alac["filetype"] == "Apple Lossless audio file"
              and abs(alac["duration"] - 1.0) < 0.05,
              f"FLAC, which iPods don't play, put on as Apple Lossless: {alac}")
    else:
        print("  (no ffmpeg here: converting not tried)")
    gone_file = ipod.on_disk(root, new["path"])
    removed = ipod.remove(info, {new["path"]}, backups)
    db = ITunesDB.parse(open(os.path.join(itunes, "iTunesDB"), "rb").read())
    check(removed == 1 and not os.path.exists(gone_file) and all(t.fields()["title"] != "New Song" for t in db.tracks)
          and all(new["title"] not in [x.fields()["title"] for x in db.tracks] for _ in db.playlists()),
          "a song taken off: its file, its place in the library and in its playlists")
    nano5 = ipod.identify(pretend_ipod("xC027"))
    try:
        ipod.add(nano5, [{"path": song}], "", backups, done.append)
        check(False, "a nano 5G isn't synced")
    except itunesdb.ITunesDBError as e:
        check("only iTunes" in str(e), "a nano 5G isn't synced: it says why")
    return root


async def reading(root: str) -> None:
    print("an iPod's library, read from its database")
    runtime = tempfile.mkdtemp()
    service = IpodService("ipodtest", {"paths": [root], "kind": "ipod", "source": ""}, runtime)
    await service.start()
    await service.scan_task
    titles = sorted(t["title"] for t in service.tracks.values())
    check(service.name() == "Test iPod" and service.describe() == "iPod classic" and "Zebra" in titles,
          f"its songs, its name and model: {service.name()}, {service.describe()}, {titles}")
    albums = service._albums()
    check(len([k for k, v in albums.items() if v[0][1]["album"] == "B Side"]) <= 1,
          "an album isn't split by the folders its files were spread over")
    check([p["title"] for p in service.playlists.values()] == ["On-The-Go"],
          "its playlists (an emptied one left out)")
    await service.stop()


if __name__ == "__main__":
    format_against_libgpod()
    identify()
    ipod_root = sync()
    asyncio.run(reading(ipod_root))
    print("all iPod checks passed")
