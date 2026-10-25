"""
What desktop Steam's Music section asks of the backend that Game Mode doesn't: smart playlists (Local files'
songs picked by rules: LocalService.smart) and music files dropped onto it (the stream server's /import,
LocalService.import_target).

    python tests/test_desktop.py
"""
import asyncio
import logging
import os
import sys
import tempfile
import time
import types
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "py_modules")]
sys.modules.setdefault("decky", types.SimpleNamespace(logger=logging.getLogger("decky")))

import aiohttp  # noqa: E402
from musicdeck.server import StreamServer  # noqa: E402
from musicdeck.services.base import ServiceError  # noqa: E402
from musicdeck.services.shelf import LocalLibrary  # noqa: E402


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"  ok - {message}")


def song(tid: str, title: str, artist: str, album: str, genre: str, year: str, added_days: float, duration: int) -> tuple:
    return tid, {"path": f"/music/{tid}.flac", "mtime": 0, "size": 1, "title": title, "artist": artist,
                 "albumartist": artist, "album": album, "genre": genre, "genres": [g for g in genre.split(";") if g],
                 "year": year, "track": 1, "disc": 1, "duration": duration, "added": time.time() - added_days * 86400}


def smart_rules(lib: LocalLibrary) -> None:
    print("smart playlists")
    lib.tracks = dict([
        song("a", "Teen Spirit", "Nirvana", "Nevermind", "Grunge;Rock", "1991", 400, 301),
        song("b", "So What", "Miles Davis", "Kind of Blue", "Jazz", "1959", 2, 562),
        song("c", "Blue in Green", "Miles Davis", "Kind of Blue", "Jazz", "1959", 2, 337),
        song("d", "Lithium", "Nirvana", "Nevermind", "Grunge", "1991", 10, 257),
    ])
    lib.plays = {"a": {"count": 12, "last": time.time() - 3600}, "d": {"count": 3, "last": time.time() - 86400 * 40}}
    lib.favourites = {"c"}
    titles = lambda rules: [t["title"] for t in lib.smart(rules)]  # noqa: E731
    check(titles({"rules": [{"field": "artist", "op": "is", "value": "miles davis"}], "sort": "title"})
          == ["Blue in Green", "So What"], "artist is (any case), by title")
    check(titles({"rules": [{"field": "genre", "op": "is", "value": "rock"}]}) == ["Teen Spirit"], "genre: any of a song's")
    check(titles({"rules": [{"field": "added_days", "op": "lt", "value": 30}], "sort": "title"})
          == ["Blue in Green", "Lithium", "So What"], "added in the last 30 days")
    check(titles({"rules": [{"field": "plays", "op": "gt", "value": 0}], "sort": "plays"}) == ["Teen Spirit", "Lithium"],
          "played, most played first")
    check(titles({"rules": [{"field": "played_days", "op": "gt", "value": 30}], "sort": "title"})
          == ["Blue in Green", "Lithium", "So What"], "not played in a month (never played counts)")
    check(titles({"rules": [{"field": "starred", "op": "is", "value": "true"}]}) == ["Blue in Green"], "favourites")
    check(titles({"rules": [{"field": "year", "op": "lt", "value": 1980}, {"field": "duration", "op": "gt", "value": 400}]})
          == ["So What"], "all of: before 1980 and longer than 400 s")
    check(len(lib.smart({"rules": [{"field": "artist", "op": "is", "value": "Nirvana"},
                                   {"field": "starred", "op": "is", "value": "true"}], "match": "any"})) == 3, "any of")
    check(len(lib.smart({"rules": [], "limit": 2})) == 2, "no rules: everything, up to the limit")
    check(len(lib.smart({"rules": [{"field": "path", "op": "contains", "value": "/"}]})) == 4, "unknown fields are left out")
    track = lib.smart({"rules": [{"field": "title", "op": "starts", "value": "so"}]})[0]
    check(track["title"] == "So What" and track["album"] == "Kind of Blue" and track["album_id"], "songs as the player plays them")


async def importing(lib: LocalLibrary, music: str) -> None:
    print("files dropped onto Music")
    scans = []
    lib.rescan = lambda: scans.append(1)
    server = StreamServer(lambda i: lib if i == "lib" else None, logging.getLogger("test"))
    await server.start()
    wav = os.path.join(tempfile.mkdtemp(), "Dropped Song.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0\0\0\0" * 4410)
    body = open(wav, "rb").read()
    try:
        async with aiohttp.ClientSession() as http:
            url = f"{server.base_url}/import/lib"
            async with http.post(url, params={"k": "wrong", "name": "x.wav"}, data=body) as r:
                check(r.status == 403, "without the key: refused")
            headers = {"Content-Type": "text/plain"}  # as the page sends it: no CORS preflight
            async with http.post(url, params={"k": server.key, "name": "Dropped Song.wav"}, data=body, headers=headers) as r:
                answer = await r.json()
                check(r.headers.get("Access-Control-Allow-Origin") == "*" and answer["ok"], f"taken ({answer})")
            kept = os.path.join(music, "Imported", "Dropped Song.wav")
            check(os.path.isfile(kept) and open(kept, "rb").read() == body, "into Local files' Imported folder, whole")
            check(scans, "and Local files looks again")
            async with http.post(url, params={"k": server.key, "name": "Dropped Song.wav"}, data=body, headers=headers) as r:
                check((await r.json())["name"] == "Dropped Song (2).wav", "a second one of that name gets a number")
            async with http.post(url, params={"k": server.key, "name": "notes.txt"}, data=b"hi", headers=headers) as r:
                answer = await r.json()
                check(not answer["ok"] and "music" in answer["error"], f"not music: refused ({answer['error']})")
            leftovers = [n for n in os.listdir(os.path.join(music, "Imported")) if n.startswith(".")]
            check(not leftovers, "no temporary files left")
    finally:
        await server.stop()


def soundtrack_news() -> None:
    print("Steam soundtracks finishing installing")
    from musicdeck.services import steam as steammod
    root = tempfile.mkdtemp()
    service = steammod.SteamService("st", {}, os.path.join(root, "data"))
    said = []
    service.announce = said.append
    service.owned = {"452070": {"appid": "452070", "name": "Oblivion Soundtrack", "parent": None, "parent_name": "", "cover": None}}
    music = os.path.join(root, "Oblivion Soundtrack")
    os.makedirs(music)
    with wave.open(os.path.join(music, "1- Reign of the Septims.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(bytes(4) * 441)
    state = {"installed": False}
    steammod.find_soundtracks = lambda home, dlc: {"452070": {"appid": "452070", "name": "Oblivion Soundtrack", "dirs": [music],
                                                             "installed": state["installed"], "updated": 0}}
    asyncio.run(service._scan())
    check(not said, "the first scan says nothing (it's what was already there)")
    asyncio.run(service._scan())
    check(not said, "still downloading: nothing to say")
    state["installed"] = True
    asyncio.run(service._scan())
    check(said == ["Oblivion Soundtrack installed: ready to play"], f"finished: said, so pages showing it refresh ({said})")
    asyncio.run(service._scan())
    check(len(said) == 1, "said once")


def formats() -> None:
    print("formats Steam can't play, converted")
    import shutil
    import subprocess
    from musicdeck.services import local as localmod
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("  (no ffmpeg here: not tried)")
        return
    root = tempfile.mkdtemp()
    made = {}
    # an AIFF's tags as iTunes and taggers write them: an ID3 chunk (ffmpeg's own default is AIFF's old text chunks)
    for ext, codec in ((".aiff", ["-c:a", "pcm_s16be", "-write_id3v2", "1"]), (".wv", ["-c:a", "wavpack"]), (".m4a", ["-c:a", "alac"]),
                       (".ape", None), (".wma", ["-c:a", "wmav2"]), (".m4a.aac", ["-c:a", "aac"])):
        if codec is None:
            continue  # ffmpeg reads Monkey's Audio but doesn't write it
        out = os.path.join(root, "song" + ext.replace(".m4a.aac", "-aac.m4a"))
        r = subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                            "-ac", "2", *codec, "-metadata", "title=Converted Song", "-metadata", "artist=The Band", out],
                           capture_output=True, text=True)
        if r.returncode == 0:
            made[os.path.basename(out)] = out
    check(len(made) >= 4, f"test files made by ffmpeg ({sorted(made)})")
    cache = os.path.join(root, "converted")
    for name, path in sorted(made.items()):
        t = localmod.read_tags(path)
        convert = localmod.needs_converting(path, t)
        if name == "song-aac.m4a":
            check(not convert and t["format"] == "AAC", "AAC in .m4a plays as it is")
            continue
        check(t.get("title") == "Converted Song" and t.get("artist") == "The Band", f"{name}: indexed with its tags ({t.get('format')})")
        check(convert, f"{name}: Steam can't play it as it is")
        out = localmod.convert_for_playback(path, t, cache)
        head = open(out, "rb").read(4)
        check(head in (b"fLaC", b"OggS") and (head == b"OggS") == name.endswith(".wma"),
              f"{name}: played as {'Opus' if head == b'OggS' else 'FLAC'}")
        again = localmod.convert_for_playback(path, t, cache)
        check(again == out, f"{name}: made once, then kept")


def identifying() -> None:
    print("which release an album is (identify.py)")
    from musicdeck import identify
    names = ["So What", "Freddie Freeloader", "Blue in Green", "All Blues", "Flamenco Sketches"]
    original = [562, 586, 337, 693, 566]
    remaster = [545, 589, 327, 691, 559]  # the same titles, other running times (as a remaster's are)
    files = [{"title": n, "track": i + 1, "disc": 1, "duration": d} for i, (n, d) in enumerate(zip(names, original))]

    def release(source, lengths, rid, **extra):
        return {"id": rid, "source": source, "title": "Kind of Blue", "artist": "Miles Davis", "year": "1959", "genres": [],
                "label": "", "discs": 1, "tracks": [{"disc": 1, "track": i + 1, "title": n, "artist": "Miles Davis", "length": d}
                                                     for i, (n, d) in enumerate(zip(names, lengths))], **extra}
    sure = identify.confidence(files, "Kind of Blue", "Miles Davis", "1959", release("MusicBrainz", original, "a"))
    other = identify.confidence(files, "Kind of Blue", "Miles Davis", "1959", release("MusicBrainz", remaster, "b"))
    check(sure >= 95 and other < sure - 5, f"the edition whose running times match, over one of the same name ({sure} vs {other})")
    shuffled = [files[3], files[0], files[4], files[1], files[2]]
    mapping = identify.match_tracks(shuffled, release("Deezer", original, "c")["tracks"])
    check(mapping == [3, 0, 4, 1, 2], f"files in any order find their tracks ({mapping})")
    some = [{"title": "blue in green", "track": 0, "disc": 0, "duration": 338}, {"title": "All Blues (Live)", "track": 0, "disc": 0, "duration": 690}]
    check(identify.match_tracks(some, release("Deezer", original, "c")["tracks"]) == [2, 3],
          "a few songs of it, unnumbered, by title and length")
    stranger = [{"title": "Teen Spirit", "track": 0, "disc": 0, "duration": 301}]
    check(identify.match_tracks(stranger, release("Deezer", original, "c")["tracks"]) == [None], "a song that isn't on it: not forced")
    mb = {**release("MusicBrainz", [0] * 5, "mbid", group="g"), "confidence": 90}
    deezer = {**release("Deezer", original, "deezer:1", cover="https://deezer/c.jpg", label="Columbia", genres=["Jazz"]), "confidence": 97}
    apple = {**release("Apple Music", original, "itunes:2", cover="https://apple/c.jpg"), "confidence": 96}
    elsewhere = {**release("Deezer", remaster, "deezer:9"), "confidence": 60}
    elsewhere["tracks"] = elsewhere["tracks"][:4]
    found = identify.records([mb, deezer, apple, elsewhere])
    best = found[0]
    check(len(found) == 2 and best["id"] == "mbid" and best["sources"] == ["Apple Music", "Deezer", "MusicBrainz"]
          and best["cover"] == "https://apple/c.jpg" and best["label"] == "Columbia" and best["genres"] == ["Jazz"],
          f"one record from three catalogues: MusicBrainz's ids, the best cover, the label and genre of whoever has them ({best['sources']})")
    check([t["length"] for t in best["tracks"]] == original and best["confidence"] == 97,
          "with the lengths MusicBrainz didn't have, and the surest catalogue's confidence")


def main() -> None:
    root = tempfile.mkdtemp()
    music = os.path.join(root, "Music")
    os.makedirs(music)
    lib = LocalLibrary("lib", {"paths": [music]}, os.path.join(root, "data"))
    smart_rules(lib)
    try:
        lib.import_target("../../etc/passwd.flac")
        traversal = os.path.dirname(lib.import_target("../../etc/passwd.flac"))
        check(traversal == os.path.join(music, "Imported"), "a name with folders in it stays in Imported")
    except ServiceError as e:
        check(False, f"import_target: {e}")
    asyncio.run(importing(lib, music))
    soundtrack_news()
    formats()
    identifying()
    print("all desktop checks passed")


if __name__ == "__main__":
    main()
