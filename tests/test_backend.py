"""
End-to-end check of the backend without a Steam Deck: stubs the `decky` module, builds a small
tagged library in a temp dir, then drives main.Plugin and the stream server over real HTTP.

    python tests/test_backend.py      (needs aiohttp + certifi installed; mutagen comes from py_modules)
"""
import asyncio
import logging
import os
import struct
import sys
import tempfile
import time
import types
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "py_modules")]

tmp = tempfile.mkdtemp(prefix="musicdeck-test-")
decky = types.ModuleType("decky")
decky.logger = logging.getLogger("musicdeck-test")
decky.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(tmp, "settings")
decky.DECKY_PLUGIN_RUNTIME_DIR = os.path.join(tmp, "runtime")
decky.DECKY_PLUGIN_LOG_DIR = os.path.join(tmp, "logs")
decky.DECKY_USER_HOME = tmp
sys.modules["decky"] = decky
logging.basicConfig(level=logging.INFO)

import aiohttp  # noqa: E402
from mutagen.id3 import APIC, TALB, TIT2, TPE1, TPE2, TRCK, TXXX, USLT  # noqa: E402
from mutagen.wave import WAVE  # noqa: E402

import main  # noqa: E402
from musicdeck.services.subsonic import SubsonicService  # noqa: E402

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def make_wav(path, title, artist, album, track, art=False, gain=None, lyrics=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * 8000)  # 1 second of silence
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=title))
    audio.tags.add(TPE1(encoding=3, text=artist))
    audio.tags.add(TPE2(encoding=3, text=artist))
    audio.tags.add(TALB(encoding=3, text=album))
    audio.tags.add(TRCK(encoding=3, text=str(track)))
    if art:
        audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, data=JPEG))
    if gain is not None:
        audio.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=f"{gain} dB"))
        audio.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK", text="0.95"))
    if lyrics:
        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    audio.save()


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("  ok -", message)


async def run():
    lib = os.path.join(tmp, "Music")
    make_wav(os.path.join(lib, "Artist A", "Album One", "02 second.wav"), "Second", "Artist A", "Album One", 2, art=True)
    make_wav(os.path.join(lib, "Artist A", "Album One", "01 first.wav"), "First", "Artist A", "Album One", 1, art=True)
    make_wav(os.path.join(lib, "Artist B", "Other", "song.wav"), "Lonely Song", "Artist B", "Other", 1,
             gain=-6.5, lyrics="first line\nsecond line")
    with open(os.path.join(lib, "Artist A", "Album One", "01 first.lrc"), "w") as f:
        f.write("[ar:Artist A]\n[00:01.00]Hello\n[00:03.50]World\n")
    with open(os.path.join(lib, "Artist B", "Other", "cover.jpg"), "wb") as f:
        f.write(JPEG)
    with open(os.path.join(lib, "mix.m3u"), "w") as f:
        f.write("#EXTM3U\nArtist B/Other/song.wav\nArtist A/Album One/01 first.wav\nmissing.wav\n")

    plugin = main.Plugin()
    await plugin._main()
    try:
        print("local service")
        saved = await plugin.save_service({"type": "local", "name": "", "config": {"paths": [lib]}})
        check(saved["ok"], "save local service")
        sid = saved["data"]["id"]
        await plugin.instances[sid].scan_task
        state = await plugin.get_state()
        check(state["active_service"] == sid, "first service becomes active")
        check(state["services"][0]["status"]["tracks"] == 3, "scanned 3 tracks")

        home = (await plugin.home(sid))["data"]
        titles = [s["title"] for s in home]
        check(titles == ["Playlists", "Recently added", "Library"], f"home sections {titles}")
        playlist = home[0]["items"][0]
        check(playlist["title"] == "mix" and playlist["subtitle"] == "2 tracks", "m3u playlist resolves 2 of 3 entries")

        albums = (await plugin.open_collection(sid, "list", "albums"))["data"]["collections"]
        album_one = next(a for a in albums if a["title"] == "Album One")
        page = (await plugin.open_collection(sid, "album", album_one["id"]))["data"]
        check([t["title"] for t in page["tracks"]] == ["First", "Second"], "album sorted by track number")

        artists = (await plugin.open_collection(sid, "list", "artists"))["data"]["collections"]
        check(sorted(a["title"] for a in artists) == ["Artist A", "Artist B"], "artists grouped")
        artist_page = (await plugin.open_collection(sid, "artist", artists[0]["id"]))["data"]
        check(len(artist_page["collections"]) == 1, "artist page lists albums")

        folder = (await plugin.open_collection(sid, "folder", ""))["data"]
        check(sorted(c["title"] for c in folder["collections"]) == ["Artist A", "Artist B"], "folder browsing")
        outside = await plugin.open_collection(sid, "folder", tmp)
        check(not outside["ok"], "folders outside the library are refused")

        results = (await plugin.search(sid, "lonely"))["data"]
        check([t["title"] for t in results["tracks"]] == ["Lonely Song"], "search by title")

        print("stream server")
        base, key = state["server"]["base"], state["server"]["key"]
        track = page["tracks"][0]
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{base}/stream/{sid}/{track['id']}") as r:
                check(r.status == 403, "requests without the key are rejected")
            async with http.get(f"{base}/stream/{sid}/{track['id']}?k={key}", headers={"Range": "bytes=0-99"}) as r:
                body = await r.read()
                check(r.status == 206 and len(body) == 100, "range request returns partial content")
                check(r.headers.get("Access-Control-Allow-Origin") == "*", "CORS header present")
            async with http.get(f"{base}/art/{sid}/{track['art']}?k={key}") as r:
                check(r.status == 200 and (await r.read()) == JPEG, "embedded art extracted")
                check(r.headers["Content-Type"] == "image/jpeg", "embedded art mime type")
            lonely = results["tracks"][0]
            async with http.get(f"{base}/art/{sid}/{lonely['art']}?k={key}") as r:
                check(r.status == 200 and (await r.read()) == JPEG, "folder cover.jpg used")
            async with http.get(f"{base}/stream/{sid}/nope?k={key}") as r:
                check(r.status == 404, "unknown track is 404")

        print("features")
        first = page["tracks"][0]
        check(first["album_id"] == album_one["id"], "tracks link back to their album")
        lonely = results["tracks"][0]
        check(lonely["gain"] and lonely["gain"]["track"] == -6.5 and lonely["gain"]["track_peak"] == 0.95,
              "ReplayGain read from tags")
        ly = (await plugin.get_lyrics(sid, first))["data"]
        check(ly and ly["synced"] and ly["lines"][1] == {"t": 3.5, "text": "World"}, "synced lyrics from .lrc sidecar")
        ly = (await plugin.get_lyrics(sid, lonely))["data"]
        check(ly and not ly["synced"] and ly["lines"][0]["text"] == "first line", "embedded lyrics")
        check((await plugin.star(sid, first["id"], True))["ok"], "star a local track")
        favs = (await plugin.open_collection(sid, "list", "favourites"))["data"]["tracks"]
        check([t["id"] for t in favs] == [first["id"]] and favs[0]["starred"], "favourites list")
        radio = (await plugin.radio(sid, first))["data"]
        check(radio and radio[0]["artist"] == "Artist A" and all(t["id"] != first["id"] for t in radio),
              "radio starts with the same artist, excludes the seed")
        await plugin.save_session({"queue": [{"instanceId": sid, "track": first}], "index": 0, "position": 42})
        session = await plugin.load_session()
        check(session["position"] == 42 and session["queue"][0]["track"]["id"] == first["id"], "session persists")

        print("musicdeck playlists")
        pl = await plugin.create_playlist(sid, "Road trip", [first])
        check(pl["id"].startswith("md:") and pl["subtitle"] == "1 track", "create playlist with a track")
        check(await plugin.playlist_add(pl["id"], [first, lonely]) == 1, "adding skips tracks already present")
        home = (await plugin.home(sid))["data"]
        check(home[0]["title"] == "Your playlists" and home[0]["items"][0]["title"] == "Road trip",
              "your playlists lead the home view")
        opened = (await plugin.open_collection(sid, "playlist", pl["id"]))["data"]
        check([t["title"] for t in opened["tracks"]] == ["First", "Lonely Song"], "playlist keeps order")
        await plugin.playlist_remove(pl["id"], first["id"])
        await plugin.playlist_rename(pl["id"], "Drive")
        opened = (await plugin.open_collection(sid, "playlist", pl["id"]))["data"]
        check(opened["title"] == "Drive" and len(opened["tracks"]) == 1, "remove and rename")
        saved = (await plugin.save_as_playlist(sid, "album", album_one["id"]))["data"]
        check(saved["title"] == "Album One" and saved["subtitle"] == "2 tracks", "save an album as a playlist")
        check(len(await plugin.local_playlists(sid)) == 2, "playlists listed per source")
        cover = os.path.join(tmp, "cover-pick.jpg")
        with open(cover, "wb") as f:
            f.write(JPEG)
        check((await plugin.playlist_set_art(saved["id"], cover))["ok"], "set custom playlist artwork")
        art_id = next(c for c in await plugin.local_playlists(sid) if c["id"] == saved["id"])["art"]
        check(art_id.startswith("mdart:"), "playlist uses its custom art id")
        os.remove(cover)  # the original can go away; MusicDeck keeps a copy
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{base}/art/{sid}/{art_id}?k={key}") as r:
                check(r.status == 200 and (await r.read()) == JPEG, "custom artwork served after original deleted")
        not_image = os.path.join(tmp, "notes.txt")
        with open(not_image, "w") as f:
            f.write("x")
        check(not (await plugin.playlist_set_art(saved["id"], not_image))["ok"], "non-images rejected")
        await plugin.playlist_clear_art(saved["id"])
        art_id = next(c for c in await plugin.local_playlists(sid) if c["id"] == saved["id"])["art"]
        check(not (art_id or "").startswith("mdart:"), "reset to default artwork")
        await plugin.playlist_delete(pl["id"])
        check(len(await plugin.local_playlists(sid)) == 1, "delete playlist")

        print("play reports")
        svc = plugin.instances[sid]
        sent = []

        async def fake_scrobble(track_id, submission):
            sent.append((track_id, submission))
        svc.scrobble = fake_scrobble
        svc.features = frozenset(svc.features | {"scrobble"})
        for _ in range(3):  # e.g. a retry and a plugin reload re-reporting the same listen
            await plugin.scrobble(sid, first["id"], False)
        await plugin.scrobble(sid, first["id"], True)
        await plugin.scrobble(sid, lonely["id"], False)
        check(sent == [(first["id"], False), (first["id"], True), (lonely["id"], False)],
              "duplicate play reports within 90s are dropped")

        print("session follows the live source")
        await plugin.save_session({"queue": [{"instanceId": "some-other-source", "track": first}],
                                   "index": 0, "position": 0})
        check(await plugin.load_session() is None, "a queue from another source isn't restored")

        print("spotify mapping")
        from musicdeck.services.spotify import SpotifyService
        spot = SpotifyService("sp", {}, tmp)
        album = {"id": "alb1", "name": "Iron", "release_date": "2021-03-04", "artists": [{"id": "ar1", "name": "Post Animal"}],
                 "images": [{"url": "https://i/640", "width": 640}, {"url": "https://i/300", "width": 300}]}
        raw = {"id": "t1", "type": "track", "name": "Ralphie", "duration_ms": 291000, "album": album,
               "artists": [{"id": "ar1", "name": "Post Animal"}, {"id": "ar2", "name": "Guest"}]}
        mapped = spot._tracks([raw, {"track": raw}, {"item": {**raw, "id": "t2"}},
                               {"track": {**raw, "id": "t3", "is_playable": False}}, {"track": None},
                               {"track": {**raw, "id": "t4", "is_local": True}}])
        check([t["id"] for t in mapped] == ["t1", "t1", "t2"], "plain, saved and playlist-item shapes; unplayable/local dropped")
        t = mapped[0]
        check(t["artist"] == "Post Animal, Guest" and t["duration"] == 291 and t["art"] == "https://i/300"
              and t["album_id"] == "alb1" and t["artist_id"] == "ar1", "track fields mapped")
        check(spot._album(album)["subtitle"] == "Post Animal · 2021", "album subtitle")

        print("spotify app and rate limits")
        from musicdeck.services import spotify as spmod
        from musicdeck.services.base import ServiceError as SpErr
        ok_cfg = await SpotifyService.authenticate({"bitrate": 160}, {"refresh_token": "r"}, tmp)
        check(ok_cfg["signed_with"] == spmod.WEB_CLIENT_ID and ok_cfg["client_id"] == "", "shared app by default")
        try:
            await SpotifyService.authenticate({"client_id": " abc "}, {"refresh_token": "r"}, tmp)
            check(False, "changing the client ID needs a new sign-in")
        except SpErr as e:
            check("Sign in again" in str(e), "changing the client ID needs a new sign-in")
        mine = await SpotifyService.authenticate({"client_id": "abc", "refresh_token": "r2", "signed_with": "abc"},
                                                 {"refresh_token": "r"}, tmp)
        check(mine["client_id"] == "abc" and SpotifyService("sp2", mine, tmp).client_id == "abc",
              "personal app used after signing in with it")
        gated = SpotifyService("sp3", {"client_id": "gate-test"}, tmp)
        await gated.start()
        gated.gate.until = time.time() + 30
        try:
            await gated._get("/me")
            check(False, "requests fail fast during a rate-limit cooldown")
        except SpErr as e:
            check("rate-limiting" in str(e), "requests fail fast during a rate-limit cooldown")
        await gated.stop()
        gated.liked = {"t1": True}
        marked = [{"id": "t1"}, {"id": "t2"}]
        await gated._starred(marked, limit=0)
        check(marked[0].get("starred") is True and "starred" not in marked[1], "liked state remembered, no extra requests")

        print("tidal")
        from musicdeck.services import tidal as tdmod
        from musicdeck.services.base import StreamSource as SS
        mpd = """<?xml version='1.0' encoding='UTF-8'?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-main:2011" type="static">
 <Period id="0"><AdaptationSet id="0" contentType="audio" mimeType="audio/mp4" segmentAlignment="true">
  <Representation id="FLAC,44100,16" codecs="flac" bandwidth="914332" audioSamplingRate="44100">
   <SegmentTemplate timescale="44100" initialization="https://sp-ad-fa.audio.tidal.com/x/0.mp4?token=t"
     media="https://sp-ad-fa.audio.tidal.com/x/$Number$.mp4?token=t" startNumber="1">
    <SegmentTimeline><S d="176128" r="2"/><S d="44100"/></SegmentTimeline>
   </SegmentTemplate>
  </Representation></AdaptationSet></Period></MPD>"""
        hls = tdmod.dash_to_hls(mpd)
        segs = [l for l in hls.splitlines() if l.startswith("https://")]
        check(segs == [f"https://sp-ad-fa.audio.tidal.com/x/{n}.mp4?token=t" for n in (1, 2, 3, 4)],
              "DASH timeline repeats expand to numbered segments")
        check('#EXT-X-MAP:URI="https://sp-ad-fa.audio.tidal.com/x/0.mp4?token=t"' in hls and "#EXTINF:3.994," in hls
              and "#EXTINF:1.000," in hls and hls.rstrip().endswith("#EXT-X-ENDLIST"), "init segment and durations")
        td = tdmod.TidalService("td", {"user_id": "1"}, tmp)
        td.favourites = {"77"}
        raw = {"id": 77, "title": "Ralphie", "version": "Live", "duration": 291, "replayGain": -7.5, "peak": 0.98,
               "artists": [{"id": 5, "name": "Post Animal"}, {"id": 6, "name": "Guest"}],
               "album": {"id": 9, "title": "Iron", "cover": "aae523c8-6e75-4fe5-ad58-e3116ee6110f"}}
        mapped = td._tracks([raw, {"item": {**raw, "id": 78}, "type": "track"},
                             {"item": {**raw, "id": 79, "type": "video"}}, {**raw, "id": 80, "allowStreaming": False}])
        check([t["id"] for t in mapped] == ["77", "78"], "plain and wrapped tracks; videos and unstreamable dropped")
        t = mapped[0]
        check(t["title"] == "Ralphie (Live)" and t["artist"] == "Post Animal, Guest" and t["album_id"] == "9"
              and t["artist_id"] == "5" and t["starred"] and not mapped[1]["starred"]
              and t["art"] == "https://resources.tidal.com/images/aae523c8/6e75/4fe5/ad58/e3116ee6110f/320x320.jpg"
              and t["gain"]["track"] == -7.5, "track fields, favourite state, artwork and gain mapped")
        mix = td._mix({"id": "001abc", "title": "My Mix 1", "subTitle": "Post Animal and more",
                       "images": {"SMALL": {"url": "https://s"}, "MEDIUM": {"url": "https://m"}}})
        check(mix["id"] == "mix:001abc" and mix["kind"] == "playlist" and mix["art"] == "https://m", "mixes open as playlists")
        built = SS(playlist=hls, hls=True)

        class FakeTidal:
            features = frozenset()
            async def stream(self, track_id):
                return built
        plugin.instances["tdfake"] = FakeTidal()
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{base}/stream/tdfake/1?k={key}") as r:
                body = await r.text()
                check(r.status == 200 and "mpegurl" in r.headers["Content-Type"], "generated playlist served as HLS")
                check(body.count("/fetch?k=") == 5 and "sp-ad-fa" not in body, "every segment routed through the server")
        del plugin.instances["tdfake"]
        cfg = await tdmod.TidalService.authenticate({"quality": "MAX"}, {"refresh_token": "r"}, tmp)
        check(cfg["quality"] == "LOSSLESS" and cfg["refresh_token"] == "r", "unknown quality falls back to lossless")

        print("jellyfin")
        from musicdeck.services.jellyfin import JellyfinService as JF
        prev = {"url": "https://jf.example", "device_id": "dev1", "token": "tok", "user_id": "u1", "username": "me"}
        kept = await JF.authenticate({"url": "jf.example/", "username": "me"}, prev, tmp)
        check(kept["token"] == "tok" and kept["device_id"] == "dev1" and kept["url"] == "https://jf.example"
              and "password" not in kept, "editing keeps the token; address normalised")
        qc = await JF.authenticate({"url": "https://jf.example", "token": "new", "user_id": "u2", "device_id": "dev2",
                                    "username": "other"}, prev, tmp)
        check(qc["token"] == "new" and qc["device_id"] == "dev2" and qc["user_id"] == "u2",
              "a Quick Connect token keeps the device id it was issued to")
        from musicdeck.services.base import ServiceError as JFError
        try:
            await JF.authenticate({"url": "https://other.example", "username": "me"}, prev, tmp)
            refused = False
        except JFError:
            refused = True
        check(refused, "a new server needs a new sign-in")
        jf = JF("jf", prev, tmp)
        item = {"Id": "t1", "Name": "Song", "Artists": ["A", "B"], "ArtistItems": [{"Id": "a1", "Name": "A"}],
                "Album": "Al", "AlbumId": "al1", "AlbumPrimaryImageTag": "x", "RunTimeTicks": 1_800_000_000,
                "UserData": {"IsFavorite": True}, "NormalizationGain": -6.2, "ImageTags": {}}
        t = jf._track(item)
        check(t["artist"] == "A, B" and t["duration"] == 180 and t["art"] == "al1" and t["starred"]
              and t["artist_id"] == "a1" and t["gain"]["track"] == -6.2, "track mapping (ticks, album art fallback)")

        print("decky's python")
        import re as _re
        # Decky's frozen interpreter ships without these; a top-level import stops the whole plugin loading
        # (guarded imports inside functions, like youtube.py's webbrowser stub, are fine)
        missing = _re.compile(r"^(?:import|from)\s+(xml|html|webbrowser|getpass)\b", _re.M)
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        offenders = []
        for folder in ["py_modules/musicdeck", "py_modules/musicdeck/services"]:
            for name in os.listdir(os.path.join(root, folder)):
                if name.endswith(".py") and name != "ytdlp_worker.py":  # the worker runs on system Python
                    with open(os.path.join(root, folder, name), encoding="utf-8") as f:
                        if missing.search(f.read()):
                            offenders.append(name)
        check(not offenders and missing.search("import xml.etree.ElementTree as ET"),
              f"no imports Decky's Python lacks {offenders}")

        print("rescan")
        os.remove(os.path.join(lib, "Artist A", "Album One", "02 second.wav"))
        await plugin.rescan(sid)
        await plugin.instances[sid].scan_task
        check((await plugin.get_state())["services"][0]["status"]["tracks"] == 2, "rescan drops deleted file")

        print("subsonic config")
        cfg = SubsonicService.prepare_config({"url": "music.example.com/", "username": " me ", "password": "pw"}, None)
        check(cfg["url"] == "https://music.example.com" and cfg["username"] == "me", "url and username normalised")
        check("password" not in cfg and len(cfg["token"]) == 32, "password replaced by token")
        kept = SubsonicService.prepare_config({"url": cfg["url"], "username": "me"}, cfg)
        check(kept["token"] == cfg["token"], "editing without password keeps credentials")
        saved = await plugin.save_service({"type": "subsonic", "name": "Home",
                                           "config": {"url": "http://127.0.0.1:9", "username": "me", "password": "pw"}})
        public = saved["data"]["config"]
        check("token" not in public and "salt" not in public and saved["data"]["has_credentials"],
              "credentials never sent to the frontend")
        bad = await plugin.test_service(saved["data"]["id"])
        check(not bad["ok"], f"unreachable server reports an error: {bad.get('error')}")
    finally:
        await plugin._unload()
    print("all backend checks passed")


asyncio.run(run())
