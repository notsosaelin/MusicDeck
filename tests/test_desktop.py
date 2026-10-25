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
    print("all desktop checks passed")


if __name__ == "__main__":
    main()
