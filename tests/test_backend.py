"""
End-to-end check of the backend without a Steam Deck: stubs the `decky` module, builds a small
tagged library in a temp dir, then drives main.Plugin and the stream server over real HTTP.

    python tests/test_backend.py      (needs aiohttp + certifi installed; mutagen comes from py_modules)
"""
import asyncio
import json
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
emitted = []


async def _emit(event, *args):
    emitted.append(event)
decky.emit = _emit
os.environ["DECKY_USER_HOME"] = tmp  # Decky also passes it to the plugin as an environment variable
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

        print("changes are announced")
        emitted.clear()
        await plugin.set_active_service(plugin.settings.get("active_service"))
        await plugin.set_setting("volume", 0.5)
        check(emitted == ["state_changed", "state_changed"],
              "every loaded frontend hears about a source switch or a setting change")

        print("soundcloud streams")
        from musicdeck.services.soundcloud import SoundCloudService

        def tc(protocol, mime="audio/mp4", snipped=False):
            return {"format": {"protocol": protocol, "mime_type": mime}, "quality": "sq", "snipped": snipped}
        drm_only = {"policy": "MONETIZE", "media": {"transcodings": [tc("cbc-encrypted-hls"), tc("ctr-encrypted-hls")]}}
        mixed = {"policy": "MONETIZE", "media": {"transcodings": [tc("ctr-encrypted-hls"), tc("hls", "audio/ogg")]}}
        preview = {"policy": "SNIP", "media": {"transcodings": [tc("hls", snipped=True)]}}
        check(not SoundCloudService._playable(drm_only) and not SoundCloudService._playable(preview)
              and SoundCloudService._playable(mixed), "DRM-only tracks and Go+ previews are left out")
        check([x["format"]["protocol"] for x in SoundCloudService._streams(mixed)] == ["hls"],
              "an encrypted stream is never picked")

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

        print("emby")
        from aiohttp import web as emweb
        emby_calls = []

        async def fake_emby(request):
            body = await request.json() if request.can_read_body else None
            emby_calls.append((request.method, request.path, dict(request.query), body))
            if request.path == "/Users/AuthenticateByName":
                if body.get("Pw") != "right":
                    return emweb.Response(status=401)
                return emweb.json_response({"AccessToken": "etok", "User": {"Id": "eu"}})
            if request.headers.get("Authorization", "").find('Token="etok"') < 0:
                return emweb.Response(status=401)
            if request.path == "/Users/eu/Items/al1":
                return emweb.json_response({"Id": "al1", "Name": "Album", "AlbumArtist": "A", "ImageTags": {}})
            if request.path == "/Items/al1":  # the Jellyfin path: Emby doesn't have it
                return emweb.Response(status=404)
            if request.path == "/Items":
                if request.query.get("ParentId") == "al1":
                    return emweb.json_response({"Items": [{"Id": "t1", "Name": "Song", "Type": "Audio", "AlbumId": "al1",
                                                           "RunTimeTicks": 600_000_000, "ImageTags": {}}]})
                if request.query.get("IncludeItemTypes") == "Playlist":
                    return emweb.json_response({"Items": [{"Id": "p1", "Name": "Mix", "Type": "Playlist", "ImageTags": {}}]})
                return emweb.json_response({"Items": []})
            if request.path in ("/Sessions/Playing", "/Users/eu/FavoriteItems/t1", "/Users/eu/PlayedItems/t1"):
                return emweb.Response(status=204 if body is None or body.get("PlaySessionId") or request.path != "/Sessions/Playing" else 400)
            return emweb.Response(status=500)
        em_app = emweb.Application()
        em_app.router.add_route("*", "/{path:.*}", fake_emby)
        em_runner = emweb.AppRunner(em_app)
        await em_runner.setup()
        em_site = emweb.TCPSite(em_runner, "127.0.0.1", 0)
        await em_site.start()
        em_url = f"http://127.0.0.1:{em_site._server.sockets[0].getsockname()[1]}"
        try:
            wrong = await plugin.save_service({"type": "emby", "name": "", "config": {"url": em_url, "username": "me", "password": "nope"}})
            check(not wrong["ok"] and "Emby didn't accept" in wrong["error"], "a wrong password names Emby")
            empty = await plugin.save_service({"type": "emby", "name": "", "config": {"url": em_url, "username": "me"}})
            check(not empty["ok"] and "username and password" in empty["error"] and "Quick Connect" not in empty["error"],
                  "no password: asks for one, and doesn't offer Quick Connect")
            check(not (await plugin.sign_in_start("emby", {"url": em_url}))["ok"], "Emby has no Quick Connect")
            em = (await plugin.save_service({"type": "emby", "name": "", "config": {"url": em_url, "username": "me", "password": "right"}}))["data"]
            eid = em["id"]
            stored = next(x for x in plugin.settings.services() if x["id"] == eid)["config"]
            check(em["name"] == "Emby" and stored["token"] == "etok" and "password" not in stored
                  and "lyrics" not in em["features"], "the password becomes a token; lyrics come from LRCLIB")
            em_page = (await plugin.open_collection(eid, "album", "al1"))["data"]
            check(em_page["title"] == "Album" and [t["id"] for t in em_page["tracks"]] == ["t1"], "an album, from the user's item path")
            lists = await plugin.instances[eid].user_playlists()
            check([p["id"] for p in lists] == ["p1"] and not any("MediaTypes" in c[2] for c in emby_calls),
                  "playlists aren't filtered by media type (Emby would return everything)")
            await plugin.instances[eid].star("t1", True)
            await plugin.instances[eid].star("t1", False)
            check([c[:2] for c in emby_calls[-2:]] == [("POST", "/Users/eu/FavoriteItems/t1"), ("DELETE", "/Users/eu/FavoriteItems/t1")],
                  "favourites under the user")
            await plugin.instances[eid].scrobble("t1", False)
            await plugin.instances[eid].scrobble("t1", True)
            check(emby_calls[-2][1] == "/Sessions/Playing" and emby_calls[-2][3]["MediaSourceId"] == "t1"
                  and emby_calls[-1][:2] == ("POST", "/Users/eu/PlayedItems/t1"), "now playing with a session id; played under the user")
            edited = (await plugin.save_service({"id": eid, "type": "emby", "name": "Home", "config": {"url": em_url, "username": "me"}}))["data"]
            check(edited["name"] == "Home" and next(x for x in plugin.settings.services() if x["id"] == eid)["config"]["token"] == "etok", "editing keeps the token")
            await plugin.remove_service(eid)
        finally:
            await em_runner.cleanup()

        print("plex")
        from aiohttp import web as pxweb
        from musicdeck.services import plex as pxmod
        px_calls = []
        px_pin_polls = []
        px_open = [False]  # a server that lets this network in without a sign-in

        def px_track(key, codec="mp3", rating=None, gain=None):
            stream = {"streamType": 2, "codec": codec}
            if gain is not None:
                stream.update(gain=gain, albumGain=gain - 1, peak=0.9)
            return {"ratingKey": key, "type": "track", "title": f"Song {key}", "grandparentTitle": "Band",
                    "parentTitle": "Record", "parentRatingKey": "20", "grandparentRatingKey": "30", "duration": 61000,
                    "thumb": "/library/metadata/20/thumb/1", "userRating": rating,
                    "Media": [{"audioCodec": codec, "Part": [{"key": f"/library/parts/{key}/1/file.{codec}",
                                                              "Stream": [stream, {"streamType": 4, "key": "/library/streams/9", "codec": "lrc"}]}]}]}

        async def fake_plex(request):
            path, q = request.path, request.query
            px_calls.append((request.method, path, dict(q)))
            token = request.headers.get("X-Plex-Token", "")
            if path == "/api/v2/pins" and request.method == "POST":
                return pxweb.json_response({"id": 7, "code": "AB12" if q.get("strong") == "false" else "long-code-xyz"})
            if path == "/api/v2/pins/7":
                px_pin_polls.append(1)
                return pxweb.json_response({"id": 7, "authToken": "acct" if len(px_pin_polls) > 1 else None})
            if path == "/api/v2/user":
                return pxweb.json_response({"uuid": "u-1", "username": "plexfan"}) if token == "acct" else pxweb.Response(status=401)
            if path == "/api/v2/resources":
                return pxweb.json_response([
                    {"name": "Friend's", "provides": "server", "owned": False, "clientIdentifier": "friend",
                     "accessToken": "ft", "connections": [{"uri": px_base, "local": False}]},
                    {"name": "Home", "provides": "server", "owned": True, "clientIdentifier": "home", "accessToken": "srv",
                     "connections": [{"uri": "http://127.0.0.1:9", "local": True, "address": "127.0.0.1", "port": 9},
                                     {"uri": px_base, "local": False}]},
                    {"name": "TV", "provides": "player,client", "clientIdentifier": "tv"}])
            if path == "/identity":
                return pxweb.json_response({"MediaContainer": {"machineIdentifier": "m1"}})
            if token not in ("srv", "ft") and not px_open[0]:
                return pxweb.Response(status=401)
            meta = lambda items: pxweb.json_response({"MediaContainer": {"Metadata": items}})
            if path == "/library/sections":
                return pxweb.json_response({"MediaContainer": {"Directory": [{"key": "1", "type": "artist"}, {"key": "2", "type": "movie"}]}})
            if path == "/library/sections/2/all":
                raise AssertionError("asked a film library for music")
            if path == "/library/sections/1/all":
                return meta([px_track("11", rating=10.0), px_track("12")] if q.get("type") == "10" else
                            [{"ratingKey": "20", "type": "album", "title": "Record", "parentTitle": "Band", "year": 1999}])
            items = {"11": px_track("11", rating=10.0, gain=-7.5), "12": px_track("12", codec="alac"),
                     "20": {"ratingKey": "20", "type": "album", "title": "Record", "parentTitle": "Band"},
                     "40": {"ratingKey": "40", "type": "playlist", "title": "Mix", "leafCount": 1}}
            if path.startswith("/library/metadata/") and path.count("/") == 3:
                key = path.rsplit("/", 1)[1]
                return meta([items[key]]) if key in items else pxweb.Response(status=404)
            if path == "/library/metadata/20/children":
                return meta([px_track("11", rating=10.0), px_track("12")])
            if path == "/library/metadata/11/nearest":
                return pxweb.Response(status=403)  # sonic analysis needs Plex Pass
            if path == "/playlists":
                return meta([items["40"], {"ratingKey": "41", "type": "playlist", "title": "Smart", "smart": True}])
            if path == "/playlists/40/items":
                return meta([px_track("11")]) if request.method == "GET" else meta([])
            if path == "/library/streams/9":
                return pxweb.json_response({"MediaContainer": {"Lyrics": [{"timed": True, "Line": [
                    {"startOffset": 1500, "Span": [{"text": "Hello "}, {"text": "there"}]}, {"startOffset": 4000, "Span": [{"text": "Bye"}]}]}]}})
            if path in ("/:/rate", "/:/scrobble", "/:/timeline"):
                return pxweb.Response(status=200)
            return pxweb.Response(status=404)
        px_app = pxweb.Application()
        px_app.router.add_route("*", "/{path:.*}", fake_plex)
        px_runner = pxweb.AppRunner(px_app)
        await px_runner.setup()
        px_site = pxweb.TCPSite(px_runner, "127.0.0.1", 0)
        await px_site.start()
        px_base = f"http://127.0.0.1:{px_site._server.sockets[0].getsockname()[1]}"
        real_plex = (pxmod.PLEX_TV, pxmod.RESOURCES)
        pxmod.PLEX_TV, pxmod.RESOURCES = px_base + "/api/v2", px_base + "/api/v2/resources"
        try:
            locked = await plugin.save_service({"type": "plex", "name": "", "config": {"url": px_base}})
            check(not locked["ok"] and "needs you to sign in with Plex" in locked["error"],
                  "an address alone: refused when the server wants a sign-in")
            px_open[0] = True
            open_px = (await plugin.save_service({"type": "plex", "name": "", "config": {"url": px_base}}))["data"]
            check(not open_px["has_credentials"] and open_px["config"]["server_url"] == px_base
                  and (await plugin.home(open_px["id"]))["ok"], "an address alone: fine for a server that doesn't ask")
            await plugin.remove_service(open_px["id"])
            px_open[0] = False
            started = await plugin.sign_in_start("plex", {"client_id": ""})
            on_deck = await plugin.sign_in_start("plex", {"client_id": "", "browser": True})
            check(on_deck["ok"] and not on_deck["data"]["code"] and "code=long-code-xyz" in on_deck["data"]["url"]
                  and on_deck["data"]["url"].startswith("https://app.plex.tv/auth/#!?clientID="),
                  "on the Deck: Plex's sign-in page with a long code")
            started = await plugin.sign_in_start("plex", {"client_id": ""})
            check(started["ok"] and started["data"]["code"] == "AB12" and not started["data"]["url"],
                  "elsewhere: a short code for plex.tv/link (the newer attempt replaces the other)")
            draft = {"type": "plex", "name": "", "config": {}}
            poll = {"status": "waiting"}
            for _ in range(20):
                poll = await plugin.sign_in_poll(started["data"]["login_id"], draft)
                if poll["status"] != "waiting":
                    break
                await asyncio.sleep(0.5)
            check(poll["status"] == "done", f"the code is approved and the source saved ({poll.get('error')})")
            px = poll["service"]
            pid = px["id"]
            stored = next(x for x in plugin.settings.services() if x["id"] == pid)["config"]
            check(px["config"]["server_name"] == "Home" and stored["server_url"] == px_base and stored["server_token"] == "srv"
                  and stored["token"] == "acct" and len(stored["client_id"]) == 24,
                  "the account's own server, at the first address that answers, with its token and the sign-in's client id")
            check([x["id"] for x in px["config"]["servers"]] == ["home", "friend"] and px["config"]["username"] == "plexfan",
                  "the account's servers (own first, players left out) for the dialog's picker")
            check("token" not in px["config"] and "server_token" not in px["config"] and px["has_credentials"],
                  "neither token reaches the UI")
            again = await plugin.save_service({"type": "plex", "name": "", "config": {"token": "acct"}})
            check(not again["ok"] and "already added" in again["error"], "the same account and server can't be added twice")
            friend = (await plugin.save_service({"id": pid, "type": "plex", "name": "", "config": {**px["config"], "server": "friend"}}))["data"]
            fstored = next(x for x in plugin.settings.services() if x["id"] == pid)["config"]
            check(friend["config"]["server_name"] == "Friend's" and fstored["server_token"] == "ft" and fstored["token"] == "acct",
                  "picking another server in the dialog, keeping the sign-in")
            await plugin.save_service({"id": pid, "type": "plex", "name": "", "config": {**px["config"], "server": "home"}})
            inst = plugin.instances[pid]
            home = (await plugin.home(pid))["data"]
            check([x["title"] for x in home] == ["Playlists", "Recently played", "Recently added", "Most played", "Favourite albums", "Library"],
                  "home rows, from music libraries only")
            check([c["id"] for c in home[0]["items"]] == ["40", "41"] and [p["id"] for p in await inst.user_playlists()] == ["40"],
                  "smart playlists are shown but can't be added to")
            page = (await plugin.open_collection(pid, "album", "20"))["data"]
            t = page["tracks"][0]
            check(t["starred"] and not page["tracks"][1]["starred"] and t["duration"] == 61 and t["art"] == "/library/metadata/20/thumb/1",
                  "tracks: four or five stars is a favourite; lengths; album art")
            src = await inst.stream("11")
            check(src.url == px_base + "/library/parts/11/1/file.mp3" and src.headers["X-Plex-Token"] == "srv"
                  and src.gain == {"track": -7.5, "album": -8.5, "track_peak": 0.9, "album_peak": 0.9},
                  "playable files direct, token in a header, Plex's loudness analysis as gain")
            alac = await inst.stream("12")
            check("/music/:/transcode/universal/start?" in alac.url and "path=%2Flibrary%2Fmetadata%2F12" in alac.url,
                  "codecs Steam can't play are transcoded by the server")
            check((await inst.stream("12")).url != alac.url, "each transcode is its own session")
            found = await inst.lyrics(t)
            check(found == {"synced": True, "lines": [{"t": 1.5, "text": "Hello there"}, {"t": 4.0, "text": "Bye"}], "source": "Plex"},
                  "Plex's timed lyrics")
            await inst.star("11", True)
            await inst.star("11", False)
            check([(c[1], c[2].get("rating")) for c in px_calls[-2:]] == [("/:/rate", "10"), ("/:/rate", "-1")],
                  "favourite = five stars; taking it back clears the rating")
            await inst.scrobble("11", False)
            await inst.scrobble("11", True)
            check([c[1] for c in px_calls[-2:]] == ["/:/timeline", "/:/scrobble"], "now playing, then a play")
            radio = (await plugin.radio(pid, t))["data"]
            check(radio is not None and all(x["id"] != "11" for x in radio), "radio without Plex Pass: the library, not the seed")
            await inst.add_to_playlist("40", ["11", "12"])
            check(px_calls[-1][2].get("uri") == "server://m1/com.plexapp.plugins.library/library/metadata/11,12",
                  "adding to a playlist names this server")
            before = len(px_calls)
            found = (await plugin.search(pid, "mix"))["data"]
            check(all(c[1] != "/hubs/search" for c in px_calls[before:]) and any(c[2].get("title") == "mix" for c in px_calls[before:])
                  and [c["id"] for c in found["collections"] if c["kind"] == "playlist"] == ["40"],
                  "search by title (not Plex's fuzzy hub search), playlists by name")
            for bad in ["../20", "20/children", "", "x"]:
                check(not (await plugin.open_collection(pid, "album", bad))["ok"], f"refused album id {bad!r}")
            check(not (await plugin.open_collection(pid, "album", "11"))["ok"], "a track opened as an album isn't found")
            check(not (await plugin.stream_info(pid, "20"))["ok"], "an album isn't a track")
            for bad in ["/library/../../etc/passwd", "http://elsewhere/x.jpg", "library/metadata/1/thumb"]:
                try:
                    await inst.art(bad)
                    refused = False
                except Exception:
                    refused = True
                check(refused, f"refused art {bad!r}")
            await plugin.remove_service(pid)
        finally:
            pxmod.PLEX_TV, pxmod.RESOURCES = real_plex
            await px_runner.cleanup()

        print("internet radio")
        from aiohttp import web as aioweb
        from musicdeck.services.radio import RadioService
        cfg = RadioService.prepare_config({"country": "de", "favourites": [{"id": "x"}]}, {"favourites": [{"id": "kept"}]})
        check(cfg == {"country": "DE", "favourites": [{"id": "kept"}]}, "country normalised; stored favourites survive edits")
        check(RadioService.prepare_config({"country": "Germany"}, None)["country"] == "", "only ISO codes accepted")
        rs = RadioService("rd", {"favourites": [{"id": "s1"}]}, tmp)
        station = rs._station({"stationuuid": "s1", "name": " Jazz FM ", "codec": "MP3", "bitrate": 128,
                               "country": "Germany", "tags": "jazz,smooth,lounge,extra", "favicon": ""})
        check(station["live"] and station["title"] == "Jazz FM" and station["artist"] == "Germany"
              and station["album"] == "jazz, smooth, lounge" and station["starred"] and station["art"] is None,
              "stations are live tracks with their place and genres")
        from musicdeck import models as md_models
        check("live" not in md_models.track("t", "Song"), "ordinary tracks carry no live flag")

        async def icy(request):
            # 16 bytes of audio, then a metadata block announcing the song, as Icecast sends it
            meta = (b"StreamTitle='Artist - Song';StreamUrl='';" if request.query.get("enc") != "latin1"
                    else "StreamTitle='Brüllová - Song';".encode("latin-1"))
            meta += b"\0" * (-len(meta) % 16)
            body = b"A" * 16 + bytes([len(meta) // 16]) + meta + b"B" * 16
            return aioweb.Response(body=body, headers={"icy-metaint": "16", "Content-Type": "audio/mpeg"})
        icy_app = aioweb.Application()
        icy_app.router.add_get("/stream", icy)
        icy_runner = aioweb.AppRunner(icy_app)
        await icy_runner.setup()
        icy_site = aioweb.TCPSite(icy_runner, "127.0.0.1", 0)
        await icy_site.start()
        icy_port = icy_site._server.sockets[0].getsockname()[1]
        await rs.start()
        rs.stations["s1"] = (time.time(), {"stationuuid": "s1", "url_resolved": f"http://127.0.0.1:{icy_port}/stream"})
        check(await rs.on_air("s1") == {"text": "Artist – Song", "title": "Song", "artist": "Artist", "art": None},
              "song on air read from ICY metadata")
        from musicdeck.services.radio import parse_on_air
        iheart = ('Zara Larsson - text="Midnight Sun" song_spot="M" MediaBaseId="3134734" '
                  'amgArtworkURL="https://i.iheart.com/v3/catalog/track/332448851?ops=fit(200,200)" length="00:03:05"')
        check(parse_on_air(iheart) == {"text": "Zara Larsson – Midnight Sun", "title": "Midnight Sun",
                                       "artist": "Zara Larsson",
                                       "art": "https://i.iheart.com/v3/catalog/track/332448851?ops=fit(200,200)"},
              "tagged titles: the song, its artist and its cover, without the tags")
        check(parse_on_air('text="The One That Got Away"artist="Katy Perry" amgArtworkURL=""')["text"]
              == "Katy Perry – The One That Got Away", "artist given as a tag")
        check(parse_on_air("Station News", "https://station.example/") == {"text": "Station News", "title": "Station News",
                                                                           "artist": "", "art": None},
              "no artist; a website in StreamUrl isn't a cover")
        check(parse_on_air("A - B", "https://cdn.example/cover.jpg")["art"] == "https://cdn.example/cover.jpg",
              "an image in StreamUrl is the cover")
        check(parse_on_air("") is None and parse_on_air(" - ") is None, "nothing on air")
        check(parse_on_air("NY's #1 Hit Music Station - text=\"Z100\" song_spot=\"T\"") is None,
              "station idents and ads aren't songs")
        check(parse_on_air('title="Super Bass",artist="NICKI MINAJ",url="song_spot="M" MediaBaseId="0"')["text"]
              == "NICKI MINAJ – Super Bass", "title= and artist= tags")
        check(parse_on_air('title="Z100 Preset 4",artist="",url="song_spot="F" MediaBaseId="0"') is None,
              "a jingle behind an unclosed url= tag isn't a song")
        rs.stations["s3"] = (time.time(), {"stationuuid": "s3", "url_resolved": f"http://127.0.0.1:{icy_port}/stream?enc=latin1"})
        check((await rs.on_air("s3"))["artist"] == "Brüllová", "Latin-1 titles read as Latin-1")
        rs.stations["s2"] = (time.time(), {"stationuuid": "s2", "url_resolved": "https://x/live.m3u8", "hls": 1})
        check(await rs.on_air("s2") is None, "HLS stations have no ICY title")
        await rs.stop()
        await icy_runner.cleanup()

        print("listenbrainz")
        from aiohttp import web as lbweb
        from musicdeck import listenbrainz as lbmod
        received = []
        lb_state = {"up": True}

        async def lb_validate(request):
            ok = request.headers.get("Authorization") == "Token good"
            return lbweb.json_response({"valid": ok, "user_name": "listener" if ok else None})

        async def lb_submit(request):
            if not lb_state["up"]:
                return lbweb.Response(status=503)
            if request.headers.get("Authorization") != "Token good":
                return lbweb.Response(status=401)
            received.append(await request.json())
            return lbweb.json_response({"status": "ok"})
        lb_app = lbweb.Application()
        lb_app.router.add_get("/1/validate-token", lb_validate)
        lb_app.router.add_post("/1/submit-listens", lb_submit)
        lb_runner = lbweb.AppRunner(lb_app)
        await lb_runner.setup()
        lb_site = lbweb.TCPSite(lb_runner, "127.0.0.1", 0)
        await lb_site.start()
        lbmod.API = f"http://127.0.0.1:{lb_site._server.sockets[0].getsockname()[1]}/1"

        bad = await plugin.set_listenbrainz("nope")
        check(not bad["ok"] and "accept" in bad["error"], "an invalid token is refused")
        good = await plugin.set_listenbrainz(" good ")
        state = await plugin.get_state()
        check(good["ok"] and state["listenbrainz"] == {"user": "listener", "pending": 0}
              and "token" not in json.dumps(state["listenbrainz"]), "linked; the token stays in the backend")
        song = {"id": "lb1", "title": "Song", "artist": "Band", "album": "Record", "duration": 200}
        lb = plugin.listenbrainz
        await lb.now_playing("good", song, "Local files")
        check(received[-1]["listen_type"] == "playing_now"
              and received[-1]["payload"][0]["track_metadata"]["additional_info"]["duration_ms"] == 200000,
              "now playing in ListenBrainz's format")
        await lb.listen("good", {**song, "live": True, "title": "Station"}, "Internet radio")
        check(len(received) == 1, "radio stations aren't listens")
        lb_state["up"] = False
        await lb.listen("good", song, "Local files", listened_at=1000)
        check(lb.pending_count() == 1 and len(received) == 1, "a listen made offline is kept")
        lb_state["up"] = True
        await lb.listen("good", {**song, "title": "Next"}, "Local files", listened_at=2000)
        check(lb.pending_count() == 0 and received[-1]["listen_type"] == "import"
              and [x["listened_at"] for x in received[-1]["payload"]] == [1000, 2000], "kept listens sent with the next")
        before = len(received)
        # the play-report test gave local files a fake play history; take it away again
        plugin.instances[sid].features = frozenset(f for f in plugin.instances[sid].features if f != "scrobble")
        local_track = page["tracks"][1]  # [0] was reported moments ago, inside the duplicate window
        await plugin.scrobble(sid, local_track["id"], True, local_track)
        await asyncio.sleep(0.3)  # sent in the background
        check(len(received) == before + 1 and received[-1]["payload"][0]["track_metadata"]["track_name"]
              == local_track["title"], "plays from a source without its own history reach ListenBrainz")
        await plugin.set_listenbrainz("")
        check((await plugin.get_state())["listenbrainz"]["user"] == "", "unlinked")
        await lb_runner.cleanup()

        print("bandcamp")
        from musicdeck.services import bandcamp as bcmod
        check(bcmod._unescape("&quot;A&amp;B&quot; &#39;x&#x27; &lt;3") == '"A&B" \'x\' <3', "page entities unescaped")
        bc = bcmod.BandcampService("bc", {}, tmp)
        tralbum = {"url": "https://band.bandcamp.com/album/rec", "item_type": "album", "artist": "Band", "art_id": 724507428,
                   "current": {"title": "Rec"},
                   "trackinfo": [{"title": "Open", "title_link": "/track/open", "duration": 61.5,
                                  "file": {"mp3-128": "https://t4.bcbits.com/stream/x"}},
                                 {"title": "Buyers only", "title_link": "/track/paid", "duration": 99, "file": None}]}
        tracks = bc._tracks(tralbum)
        check([t["id"] for t in tracks] == ["https://band.bandcamp.com/track/open"], "buyers-only tracks left out")
        check(tracks[0]["album_id"] == "https://band.bandcamp.com/album/rec" and tracks[0]["artist_id"]
              == "https://band.bandcamp.com" and tracks[0]["art"] == "https://f4.bcbits.com/img/a0724507428_2.jpg",
              "album, artist and art ids from the page")
        page_html = '<div id="x" data-tralbum="' + json.dumps(tralbum).replace("&", "&amp;").replace('"', "&quot;") + '"></div>'
        check(json.loads(bcmod._unescape(bcmod.TRALBUM.search(page_html).group(1))) == tralbum,
              "album data read back out of a page attribute")

        print("sign-in flow")
        from musicdeck.services import SERVICE_TYPES, SignIn
        from musicdeck.services.radio import RadioService

        class FakeSignIn(SignIn):
            closed = []

            async def start(self):
                self.url, self.status = "https://example.com/approve", "waiting"

            async def close(self):
                FakeSignIn.closed.append(self.id)

        class FakeSource(RadioService):
            type = "fakesignin"
            display_name = "Fake"

            @classmethod
            def sign_in(cls, options, runtime_dir):
                return FakeSignIn()
        SERVICE_TYPES["fakesignin"] = FakeSource
        try:
            first = (await plugin.sign_in_start("fakesignin", {}))["data"]
            second = (await plugin.sign_in_start("fakesignin", {}))["data"]
            check(FakeSignIn.closed == [first["login_id"]] and second["url"] == "https://example.com/approve",
                  "a new attempt replaces the previous one")
            draft = {"type": "fakesignin", "name": "Fake one", "config": {"country": "DE"}}
            poll = await plugin.sign_in_poll(second["login_id"], draft)
            check(poll == {"status": "waiting", "url": "https://example.com/approve"}, "waiting, with the page to show")
            attempt = plugin.sign_ins[second["login_id"]]
            attempt.status, attempt.result = "done", {"favourites": [{"id": "from-sign-in"}]}
            done = await plugin.sign_in_poll(second["login_id"], draft)
            check(done["status"] == "done" and done["service"]["name"] == "Fake one"
                  and done["service"]["config"]["country"] == "DE", "done: the source is saved")
            await plugin.remove_service(done["service"]["id"])
            third = (await plugin.sign_in_start("fakesignin", {}))["data"]
            await plugin.sign_in_cancel(third["login_id"])
            gone = await plugin.sign_in_poll(third["login_id"], draft)
            check(third["login_id"] in FakeSignIn.closed and gone["status"] == "error", "cancel closes the attempt")
            refused = await plugin.sign_in_start("local", {})
            check(not refused["ok"], "sources without a separate sign-in refuse one")
            # unloading mid sign-in: close() and the cancelled task both stop the redirect listener
            from musicdeck.services.qobuz import QobuzLogin
            login = QobuzLogin(tmp)
            login._runner = aioweb.AppRunner(aioweb.Application())
            await login._runner.setup()
            await aioweb.TCPSite(login._runner, "127.0.0.1", 0).start()
            login._task = asyncio.get_event_loop().create_task(login._finish())
            await asyncio.sleep(0)
            await login.close()
            await asyncio.wait([login._task])
            check(login._task.cancelled() or login._task.exception() is None,
                  "closing a waiting sign-in stops its listener once")
        finally:
            del SERVICE_TYPES["fakesignin"]

        print("steam soundtracks")
        from musicdeck.services import steam as stmod
        check(stmod.parse_vdf('"a" { "B" "x \\"y\\"" // note\n "c" { } }') == {"a": {"b": 'x "y"', "c": {}}},
              "vdf: nesting, escapes, comments, lower-cased keys")
        steam_root = os.path.join(tmp, ".local", "share", "Steam")
        sd = os.path.join(tmp, "SD Card")

        def write(path, text=""):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

        def manifest(library, appid, name, installdir, flags, extra=""):
            write(os.path.join(library, "steamapps", f"appmanifest_{appid}.acf"),
                  f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n\t"installdir"\t\t"{installdir}"\n'
                  f'\t"StateFlags"\t\t"{flags}"\n\t"LastUpdated"\t\t"{appid}"\n{extra}}}\n')

        def vdf_path(path):  # backslashes are escapes in VDF (Windows paths when testing there)
            return path.replace("\\", "\\\\")
        write(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"),
              f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{vdf_path(steam_root)}"\n\t}}\n'
              f'\t"1"\n\t{{\n\t\t"path"\t\t"{vdf_path(sd)}"\n\t}}\n\t"2"\n\t{{\n\t\t"path"\t\t"/nowhere"\n\t}}\n}}\n')
        ost = os.path.join(steam_root, "steamapps", "music", "Game OST")
        manifest(steam_root, 100, "Game Soundtrack", "Game OST", 4)
        for n, title in ((1, "Intro"), (2, "Battle")):  # the same album as MP3 and FLAC: one copy is kept
            for folder, ext in (("MP3", ".mp3"), ("FLAC", ".flac")):
                wav = os.path.join(ost, folder, f"0{n} {title}.wav")
                make_wav(wav, title, "Composer", "Tagged Album", n)
                os.rename(wav, wav[:-4] + ext)
        make_wav(os.path.join(ost, "Bonus", "03 Bonus.wav"), "Bonus", "Composer", "Tagged Album", 3)
        with open(os.path.join(ost, "cover.jpg"), "wb") as f:
            f.write(JPEG)
        # an older soundtrack: DLC installed inside its game's folder, next to the game's sound effects
        manifest(sd, 200, "Old Game", "OldGame", 4,
                 '\t"InstalledDepots"\n\t{\n\t\t"201"\n\t\t{\n\t\t\t"manifest"\t\t"1"\n\t\t\t"dlcappid"\t\t"202"\n\t\t}\n\t}\n')
        make_wav(os.path.join(sd, "steamapps", "common", "OldGame", "Data", "Soundtrack", "theme.wav"),
                 "Old Theme", "Old Composer", "", 1)
        make_wav(os.path.join(sd, "steamapps", "common", "OldGame", "sfx", "boom.wav"), "Boom", "", "", 1)
        manifest(steam_root, 300, "Downloading OST", "Downloading OST", 1026)
        os.makedirs(os.path.join(steam_root, "steamapps", "music", "Downloading OST"))

        saved = await plugin.save_service({"type": "steam", "name": "", "config": {}})
        check(saved["ok"] and saved["data"]["name"] == "Steam soundtracks", "add the Steam soundtracks source")
        st_id = saved["data"]["id"]
        owned = [{"appid": 100, "name": "Game Soundtrack", "parent": 10, "parent_name": "Game", "cover": None},
                 {"appid": 202, "name": "Old Game OST", "parent": 200, "parent_name": "Old Game", "cover": None},
                 {"appid": 300, "name": "Downloading OST", "parent": None, "parent_name": "", "cover": None},
                 {"appid": 400, "name": "Not Yet", "parent": 40, "parent_name": "Other Game",
                  "cover": "https://shared.steamstatic.com/community_assets/images/apps/400/c.jpg"},
                 {"appid": 500, "name": "Odd", "parent": None, "parent_name": "", "cover": "https://evil.example/c.jpg"}]
        await plugin.steam_library(st_id, owned, 10)
        st = plugin.instances[st_id]
        while st.scan_task and not st.scan_task.done():
            await asyncio.sleep(0.05)
        home = (await plugin.home(st_id))["data"]
        check([s["title"] for s in home] == ["From Game", "Installed", "Library", "Not installed"],
              f"home: the running game's soundtrack first {[s['title'] for s in home]}")
        check([c["id"] for c in home[0]["items"]] == ["100"], "running game's soundtrack")
        check([c["title"] for c in home[1]["items"]] == ["Game Soundtrack", "Old Game OST"],
              "installed from every library, DLC soundtracks included, named as Steam names them")
        check([(c["id"], c["subtitle"]) for c in home[3]["items"]]
              == [("300", "Installing…"), ("400", "Not installed · Other Game"), ("500", "Not installed")],
              "not installed: downloading, with its game, plain")
        page = (await plugin.open_collection(st_id, "album", "100"))["data"]
        check([t["title"] for t in page["tracks"]] == ["Intro", "Battle", "Bonus"]
              and page["subtitle"] == "Composer · 3 tracks", "one copy of each track, bonus extras kept")
        check(st._track_index()[page["tracks"][0]["id"]][1]["path"].endswith(".flac"), "FLAC preferred over MP3")
        check(page["tracks"][0]["album"] == "Game Soundtrack" and page["tracks"][0]["album_id"] == "100",
              "a soundtrack is one album, whatever the tags say")
        old = (await plugin.open_collection(st_id, "album", "202"))["data"]
        check([t["title"] for t in old["tracks"]] == ["Old Theme"], "only the game's soundtrack folder, no sound effects")
        later = (await plugin.open_collection(st_id, "album", "400"))["data"]
        check(later["action"] == {"label": "Install", "url": "steam://install/400"} and later["notice"]
              and not later["tracks"], "not installed: an Install action")
        downloading = (await plugin.open_collection(st_id, "album", "300"))["data"]
        check(downloading["action"] is None and downloading["subtitle"] == "Installing…", "downloading: no action")
        base = plugin.server.base_url
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{base}/art/{st_id}/c:100?k={plugin.server.key}") as r:
                check(r.status == 200 and (await r.read()) == JPEG, "cover image from the soundtrack's folder")
            async with http.get(f"{base}/stream/{st_id}/{page['tracks'][1]['id']}?k={plugin.server.key}") as r:
                check(r.status == 200 and (await r.read())[:4] == b"RIFF", "track streamed from disk")
        check((await st.art("c:400")).url.startswith("https://shared.steamstatic.com/"), "Steam's album art when not installed")
        check(st.owned["500"]["cover"] is None, "cover URLs only from Steam's image hosts")
        found = (await plugin.search(st_id, "old game"))["data"]
        check([c["id"] for c in found["collections"]] == ["202"], "search finds a soundtrack by its game")
        radio = (await plugin.radio(st_id, page["tracks"][0]))["data"]
        check(len(radio) == 3 and page["tracks"][0]["id"] not in [t["id"] for t in radio], "radio from every soundtrack")
        await plugin.steam_library(st_id, owned, None)
        check([s["title"] for s in (await plugin.home(st_id))["data"]][0] == "Installed", "no game running: no game row")
        # Steam finishing the download changes its folders; the next visit picks it up
        manifest(steam_root, 300, "Downloading OST", "Downloading OST", 4)
        make_wav(os.path.join(steam_root, "steamapps", "music", "Downloading OST", "a.wav"), "Done", "", "", 1)
        os.utime(os.path.join(steam_root, "steamapps"), (time.time() + 5, time.time() + 5))
        await plugin.home(st_id)
        while st.scan_task and not st.scan_task.done():
            await asyncio.sleep(0.05)
        check([t["title"] for t in (await plugin.open_collection(st_id, "album", "300"))["data"]["tracks"]] == ["Done"],
              "a finished install is found without a manual rescan")
        await plugin.remove_service(st_id)
        check(not [n for n in os.listdir(decky.DECKY_PLUGIN_RUNTIME_DIR) if n.startswith(f"steam-{st_id}")],
              "removing the source deletes its files")

        print("several accounts")
        from musicdeck.services import spotify as spmod2
        legacy = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "spotify", "credentials")
        write(os.path.join(legacy, "credentials.json"), json.dumps({"username": "alice"}))
        alice = await plugin.save_service({"type": "spotify", "name": "",
                                           "config": {"refresh_token": "a", "username": "alice"}})
        bob = await plugin.save_service({"type": "spotify", "name": "", "config": {"refresh_token": "b", "username": "bob"}})
        check(alice["ok"] and bob["ok"] and [alice["data"]["name"], bob["data"]["name"]] == ["Spotify", "Spotify 2"],
              "two accounts of one service, named apart")
        again = await plugin.save_service({"type": "spotify", "name": "",
                                           "config": {"refresh_token": "c", "username": "alice"}})
        check(not again["ok"] and "already added as “Spotify”" in again["error"], "the same account twice is refused")
        edit = await plugin.save_service({"id": alice["data"]["id"], "type": "spotify", "name": "Mine", "config": {}})
        check(edit["ok"], "editing a source isn't a duplicate of itself")
        alice_dir = spmod2.account_dir(decky.DECKY_PLUGIN_RUNTIME_DIR, "alice")
        check(os.path.isfile(os.path.join(alice_dir, "credentials", "credentials.json")) and not os.path.exists(legacy),
              "the old shared sign-in moves to its own account")
        bob_dir = spmod2.account_dir(decky.DECKY_PLUGIN_RUNTIME_DIR, "bob")
        check(alice_dir != bob_dir and plugin.instances[bob["data"]["id"]].helper.cache_dir == bob_dir,
              "each account's playback sign-in kept apart")
        write(os.path.join(bob_dir, "credentials", "credentials.json"), "{}")
        await plugin.remove_service(bob["data"]["id"])
        check(not os.path.exists(bob_dir) and os.path.exists(alice_dir), "removing an account deletes only its sign-in")
        await plugin.remove_service(alice["data"]["id"])

        print("frontend events")
        # used to raise (and lose the batch) when an event's fields reused a keyword of debuglog.debug
        await plugin.log_events([{"t": 1, "event": "audio pause", "data": {"at": 12.5, "event": "x", "source": "y"}}])
        check(True, "events with their own at/event/source fields are accepted")

        print("lyrics lookups")
        from aiohttp import web as lrweb
        from musicdeck import lyrics as lyrmod
        # LRCLIB: "status" for every request, or a list of statuses taken one per request
        answers = {"status": 429, "plain": False}
        lrclib_asked = []
        netease = {"mode": "none", "asked": []}

        async def lrclib(request):
            lrclib_asked.append(request.path)
            status = answers["status"].pop(0) if isinstance(answers["status"], list) else answers["status"]
            if status == 404 and request.path == "/api/search":
                return lrweb.json_response([])  # how LRCLIB says it found nothing
            if status != 200:
                return lrweb.Response(status=status)
            if request.path == "/api/get":
                if answers["plain"]:
                    return lrweb.json_response({"plainLyrics": "Hello\nWorld", "instrumental": False})
                return lrweb.json_response({"syncedLyrics": "[00:01.00]Hello\n[00:02.50]World", "instrumental": False})
            return lrweb.json_response([])

        async def netease_api(request):
            netease["asked"].append((request.path, dict(request.query), request.headers.get("Cookie")))
            mode = netease["mode"]
            if mode == "down":
                return lrweb.Response(status=503)
            if request.path == "/ne/search/get":
                if mode == "none":
                    return lrweb.json_response({"result": {"songs": []}})
                song = lambda id, name, artist, ms: {"id": id, "name": name, "artists": [{"name": artist}], "duration": ms}  # noqa: E731
                return lrweb.json_response({"result": {"songs": [
                    song(1, "Song", "Someone Else", 100_000),          # a cover
                    song(2, "Song (Instrumental)", "Artist", 100_000),  # no singing
                    song(3, "Song (Live)", "Artist", 160_000),          # too long
                    song(4, "Song", "Artist", 101_000),                 # the song, but this upload has no lyrics
                    song(5, "Song", "Artist", 99_000),                  # the song
                ]}})
            if mode == "instrumental":
                return lrweb.json_response({"pureMusic": True, "lrc": {"lyric": ""}})
            if request.query.get("id") != "5":
                return lrweb.json_response({"lrc": {"lyric": ""}})
            return lrweb.json_response({"lrc": {"lyric": "[00:00.00] 作词 : Writer\n[00:00.10]Artist: Band\n[00:00.20]\n"
                                                         "[00:03.00]Sung: with a colon\n[00:05.00]Last line"}})
        lr_app = lrweb.Application()
        lr_app.router.add_get("/api/get", lrclib)
        lr_app.router.add_get("/api/search", lrclib)
        lr_app.router.add_get("/ne/search/get", netease_api)
        lr_app.router.add_get("/ne/song/lyric", netease_api)
        lr_runner = lrweb.AppRunner(lr_app)
        await lr_runner.setup()
        lr_site = lrweb.TCPSite(lr_runner, "127.0.0.1", 0)
        await lr_site.start()
        lr_base = f"http://127.0.0.1:{lr_site._server.sockets[0].getsockname()[1]}"
        real_sites = (lyrmod.LRCLIB, lyrmod.NETEASE, lyrmod.RETRY_DELAY)
        lyrmod.LRCLIB, lyrmod.NETEASE, lyrmod.RETRY_DELAY = lr_base + "/api", lr_base + "/ne", 0
        song = {"id": "nope", "title": "Song", "artist": "Artist", "duration": 100}
        texts = lambda r: [line["text"] for line in r["data"]["lines"]]  # noqa: E731
        try:
            r = await plugin.get_lyrics(sid, song)
            check(not r["ok"] and "429" in r["error"] and lrclib_asked.count("/api/get") == 1,
                  "LRCLIB rate limiting: an error, not \"no lyrics\" (and not asked again straight away)")
            answers["status"] = 404
            r = await plugin.get_lyrics(sid, song)
            check(r == {"ok": True, "data": None}, "no site knows the song: no lyrics")
            answers["status"] = 200
            r = await plugin.get_lyrics(sid, song)
            check(r["ok"] and r["data"]["synced"] and r["data"]["source"] == "LRCLIB" and texts(r) == ["Hello", "World"],
                  "LRCLIB's synced lyrics are parsed")
            answers["status"], netease["mode"] = [503, 200, 200], "none"
            r = await plugin.get_lyrics(sid, song)
            check(r["ok"] and r["data"]["source"] == "LRCLIB", "a busy LRCLIB (503) is asked once more")
            answers["status"], netease["mode"] = 503, "lyrics"
            netease["asked"].clear()
            r = await plugin.get_lyrics(sid, song)
            check(r["ok"] and r["data"]["source"] == "NetEase" and r["data"]["synced"]
                  and texts(r) == ["Sung: with a colon", "Last line"],
                  "LRCLIB down: NetEase's lyrics, credits taken off the start (a sung colon kept)")
            lyric_ids = [q.get("id") for path, q, _ in netease["asked"] if path == "/ne/song/lyric"]
            check(lyric_ids == ["4", "5"],
                  "NetEase: the cover, instrumental and live take passed over; an upload without lyrics, then the next")
            check(all(cookie is None for _, _, cookie in netease["asked"]), "NetEase is asked without cookies")
            answers["status"], answers["plain"] = 200, True
            r = await plugin.get_lyrics(sid, song)
            check(r["ok"] and r["data"]["source"] == "NetEase", "LRCLIB only has unsynced lyrics: NetEase's synced ones win")
            netease["mode"] = "none"
            r = await plugin.get_lyrics(sid, song)
            check(r["ok"] and r["data"]["source"] == "LRCLIB" and not r["data"]["synced"],
                  "...and LRCLIB's unsynced ones when NetEase has none")
            answers["status"], answers["plain"], netease["mode"] = 404, False, "instrumental"
            r = await plugin.get_lyrics(sid, song)
            check(r == {"ok": True, "data": None}, "NetEase knowing it as instrumental: no lyrics")
            netease["mode"] = "down"
            r = await plugin.get_lyrics(sid, song)
            check(not r["ok"], "LRCLIB has none but NetEase can't be asked: an error, so it's tried again later")
            await lr_runner.cleanup()
            r = await plugin.get_lyrics(sid, song)
            check(not r["ok"] and "reach" in r["error"], "neither site reachable: an error")
        finally:
            lyrmod.LRCLIB, lyrmod.NETEASE, lyrmod.RETRY_DELAY = real_sites

        print("jump back in")
        titles_of = lambda home: [s["title"] for s in home]  # noqa: E731
        first_home = (await plugin.home(sid))["data"]
        check("Jump back in" not in titles_of(first_home), "nothing played yet: no row")
        mine = await plugin.create_playlist(sid, "Road trip", [])
        await plugin.played_collection(sid, album_one)
        await plugin.played_collection(sid, mine)
        await plugin.played_collection(sid, album_one)  # again: moves to the front, once
        home_now = (await plugin.home(sid))["data"]
        check(titles_of(home_now)[:2] == ["Jump back in", "Your playlists"]
              and [c["title"] for c in home_now[0]["items"]] == ["Album One", "Road trip"],
              "played albums and playlists lead the home, newest first, once each")
        await plugin.playlist_rename(mine["id"], "Road trip 2")
        check([c["title"] for c in (await plugin.home(sid))["data"][0]["items"]] == ["Album One", "Road trip 2"],
              "a renamed playlist shows its new name")
        await plugin.playlist_delete(mine["id"])
        check([c["title"] for c in (await plugin.home(sid))["data"][0]["items"]] == ["Album One"],
              "a deleted playlist leaves the row")
        for i in range(15):
            await plugin.played_collection(sid, {"id": f"a{i}", "kind": "album", "title": f"A{i}"})
        check(len(plugin.recents.for_instance(sid)) == 10, "the row keeps the last 10")
        for q in ["lonely", "  Second   Song ", "LONELY", "", "   "]:
            await plugin.search(sid, q)
        check(await plugin.search_history(sid) == ["LONELY", "Second Song"],
              "recent searches: newest first, tidied, once each whatever the case, blanks ignored")
        for i in range(10):
            await plugin.search(sid, f"query {i}")
        check(len(await plugin.search_history(sid)) == 8, "the last 8 searches are kept")
        await plugin.forget_search(sid, "query 9")
        check("query 9" not in await plugin.search_history(sid), "one search can be removed")
        await plugin.forget_search(sid, "")
        check(await plugin.search_history(sid) == [], "or all of them")
        await plugin.search(sid, "kept")
        for junk in [None, "x", {}, {"id": "only-id"}, {"kind": "album"}]:
            await plugin.played_collection(sid, junk)
        await plugin.played_collection("no-such-source", album_one)
        check(len(plugin.recents.for_instance(sid)) == 10 and "no-such-source" not in plugin.recents.collections,
              "garbage and unknown sources are ignored")
        temp = await plugin.save_service({"type": "radio", "name": "Temp", "config": {}})
        await plugin.played_collection(temp["data"]["id"], {"id": "tag:jazz", "kind": "list", "title": "Jazz"})
        await plugin.search(temp["data"]["id"], "jazz")
        await plugin.remove_service(temp["data"]["id"])
        check(temp["data"]["id"] not in plugin.recents.collections and temp["data"]["id"] not in plugin.recents.searches_by_instance,
              "removing a source forgets what it played and searched")
        from musicdeck.recents import Recents
        reread = Recents(decky.DECKY_PLUGIN_RUNTIME_DIR)
        check(len(reread.for_instance(sid)) == 10 and reread.searches(sid) == ["kept"], "kept across restarts")
        first_version = os.path.join(tmp, "recents-v1")
        write(os.path.join(first_version, "recents.json"), json.dumps({sid: [{"id": "x", "kind": "album", "title": "X"}]}))
        check([c["title"] for c in Recents(first_version).for_instance(sid)] == ["X"], "the first version's file still reads")
        plugin.recents.forget(instance_id=sid)

        print("podcasts")
        from musicdeck import feeds
        from musicdeck.services import podcasts as podmod
        FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
  <title>Tom &amp; Jerry&#39;s <![CDATA[<Show>]]></title>
  <itunes:author>Tom</itunes:author>
  <itunes:image href="https://example.com/show.jpg"/>
  <description><![CDATA[<p>A <b>great</b> show</p>]]></description>
  <!-- a comment with <tags> -->
  <item><title>Older</title><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
    <enclosure url="https://example.com/1.mp3" type="audio/mpeg" length="1"/><itunes:duration>1:02:03</itunes:duration></item>
  <item><title>Newer &lt;3</title><pubDate>Tue, 02 Jan 2024 10:00:00 +0100</pubDate>
    <enclosure url="https://example.com/2.mp3" type="audio/mpeg"/><itunes:duration>125</itunes:duration>
    <itunes:image href="https://example.com/2.jpg"/><p>an unclosed tag, and 3 < 4</item>
  <item><title>Just text, no audio</title></item>
  <item><title>Video</title><enclosure url="https://example.com/v.mov" type="video/quicktime"/></item>
</channel></rss>"""
        show = feeds.parse_feed(FEED)
        check(show["title"] == "Tom & Jerry's <Show>" and show["author"] == "Tom" and show["description"] == "A great show"
              and show["art"] == "https://example.com/show.jpg", "feed: entities, CDATA and HTML in titles and notes")
        check([e["title"] for e in show["episodes"]] == ["Newer <3", "Older"] and show["episodes"][1]["duration"] == 3723
              and show["episodes"][0]["duration"] == 125 and show["episodes"][0]["art"] == "https://example.com/2.jpg"
              and show["episodes"][1]["art"] == "https://example.com/show.jpg",
              "feed: episodes newest first, durations, own art or the show's; no audio, no episode")
        check(show["episodes"][0]["published"] == 1704186000, "feed: dates with a time zone")
        check(feeds.duration_seconds("62:03") == 3723 and feeds.duration_seconds("12.5") == 12.5
              and feeds.duration_seconds("soon") == 0, "durations in every format, or 0")
        for junk in [b"", b"<html><body>Not a feed</body></html>", b"\xff\xfe garbage"]:
            try:
                feeds.parse_feed(junk)
                check(False, f"not a feed: {junk[:20]!r}")
            except ValueError:
                pass

        async def fake_directory(request):
            if request.path == "/feed.xml":
                return lrweb.Response(body=FEED, content_type="application/rss+xml")
            if request.path == "/charts.json":
                return lrweb.json_response({"feed": {"results": [
                    {"id": "123", "name": "Top Show", "artistName": "Someone",
                     "artworkUrl100": "https://x/100x100bb.jpg"}]}})
            return lrweb.Response(status=404)
        pod_app = lrweb.Application()
        pod_app.router.add_get("/{path:.*}", fake_directory)
        pod_runner = lrweb.AppRunner(pod_app)
        await pod_runner.setup()
        pod_site = lrweb.TCPSite(pod_runner, "127.0.0.1", 0)
        await pod_site.start()
        base_url = f"http://127.0.0.1:{pod_site._server.sockets[0].getsockname()[1]}"
        real_charts = podmod.CHARTS
        podmod.CHARTS = base_url + "/charts.json?country={country}&count={count}"
        try:
            pod = (await plugin.save_service({"type": "podcasts", "name": "", "config": {"country": "gb"}}))["data"]
            pid = pod["id"]
            check(pod["name"] == "Podcasts" and pod["config"]["country"] == "GB" and pod["features"] == ["progress"],
                  "add the Podcasts source")
            home = (await plugin.home(pid))["data"]
            check([s["title"] for s in home] == ["Top podcasts"] and home[0]["items"][0]["id"] == "a:123"
                  and home[0]["items"][0]["art"] == "https://x/600x600bb.jpg", "home: the top charts, big artwork")
            found = (await plugin.search(pid, base_url + "/feed.xml"))["data"]
            show_id = found["collections"][0]["id"]
            check(found["collections"][0]["title"] == "Tom & Jerry's <Show>", "a pasted feed address finds its podcast")
            page = (await plugin.open_collection(pid, "album", show_id))["data"]
            check(page["subtitle"] == "Tom · 2 episodes" and page["action"] == {"label": "Subscribe", "call": "subscribe"}
                  and [t["title"] for t in page["tracks"]] == ["Newer <3", "Older"]
                  and page["tracks"][1]["artist"] == "1 Jan 2024", "a podcast's page: episodes and Subscribe")
            r = await plugin.collection_action(pid, "album", show_id, "subscribe")
            check(r == {"ok": True, "data": "Subscribed to Tom & Jerry's <Show>"}
                  and plugin.settings.find_service(pid)["config"]["subscriptions"][0]["feed"] == base_url + "/feed.xml",
                  "subscribing is kept in the source's config")
            page = (await plugin.open_collection(pid, "album", show_id))["data"]
            check(page["action"] == {"label": "Subscribed", "call": "unsubscribe"}, "then the button unsubscribes")
            home = (await plugin.home(pid))["data"]
            check([s["title"] for s in home] == ["New episodes", "Your podcasts", "Top podcasts"],
                  f"home with a subscription {[s['title'] for s in home]}")
            episode = page["tracks"][1]
            info = (await plugin.stream_info(pid, episode["id"]))["data"]
            check(info["start_at"] == 0, "a new episode starts at the beginning")
            await plugin.save_progress(pid, episode, 600.0, 3723.0)
            info = (await plugin.stream_info(pid, episode["id"]))["data"]
            check(info["start_at"] == 595, "an episode resumes where it was left, a few seconds back")
            home = (await plugin.home(pid))["data"]
            check(home[0]["title"] == "Continue listening" and home[0]["tracks"][0]["artist"] == "52 min left",
                  "home: continue listening, with the time left")
            await plugin.save_progress(pid, episode, 3700.0, 3723.0)
            check((await plugin.stream_info(pid, episode["id"]))["data"]["start_at"] == 0
                  and "Continue listening" not in [s["title"] for s in (await plugin.home(pid))["data"]],
                  "listened to the end: played, starts over, leaves continue listening")
            page = (await plugin.open_collection(pid, "album", show_id))["data"]
            check(page["tracks"][1]["artist"] == "1 Jan 2024 · Played", "played episodes say so")
            await plugin.set_played(pid, episode, False)
            await plugin.set_played(pid, page["tracks"][0], True)
            page = (await plugin.open_collection(pid, "album", show_id))["data"]
            check([t["artist"] for t in page["tracks"]] == ["2 Jan 2024 · Played", "1 Jan 2024"],
                  "marking played and unplayed")
            for bad in ["nope", "e:", "e:!!!", "e:" + podmod._encode("file:///etc/passwd")]:
                r = await plugin.stream_info(pid, bad)
                check(not r["ok"], f"not an episode: {bad[:20]}")
            for bad in [("p:!!", "subscribe"), (show_id, "explode"), ("a:notanumber", "subscribe")]:
                check(not (await plugin.collection_action(pid, "album", *bad))["ok"], f"refused: {bad}")
            check((await plugin.get_lyrics(pid, episode)) == {"ok": True, "data": None}, "episodes have no lyrics to look up")
            await plugin.save_progress(pid, episode, "far", 10)
            await plugin.save_progress(pid, {"id": "not-an-episode"}, 1.0, 2.0)
            check(all(k.startswith("e:") for k in plugin.instances[pid].progress), "garbage progress is ignored")
            check(not (await plugin.open_collection(pid, "album", "p:" + podmod._encode(base_url + "/missing.xml")))["ok"],
                  "a feed that's gone: an error, not a crash")
            progress_file = plugin.instances[pid].progress_path
            await plugin.remove_service(pid)
            check(not os.path.exists(progress_file), "removing the source deletes its progress")
        finally:
            podmod.CHARTS = real_charts
            await pod_runner.cleanup()

        print("live music archive")
        from musicdeck.services import livemusic as lmmod
        from datetime import date as _date
        today = _date.today()
        searches = []

        async def fake_archive(request):
            if request.path == "/advancedsearch.php":
                q = request.query.get("q", "")
                searches.append(q)
                if "mediatype:collection" in q:
                    docs = [{"identifier": "TheBand", "title": "The Band", "item_count": 1234}]
                elif "collection:TheBand" in q:
                    docs = [{"identifier": "tb2000", "title": "The Band Live on 2000-01-01", "date": "2000-01-01T00:00:00Z"},
                            {"identifier": "tb1999", "title": "The Band Live on 1999-05-05", "date": "1999-05-05T00:00:00Z"}]
                    return lrweb.json_response({"response": {"numFound": 5000, "docs": docs}})
                elif "title:" in q:  # on this day: one match from today, one only sharing the numbers
                    docs = [{"identifier": "today", "title": "x", "date": f"1977-{today:%m-%d}T00:00:00Z"},
                            {"identifier": "other", "title": "y", "date": f"{today:%m-%d}".replace("-", "") + "-01-01"}]
                else:
                    docs = [{"identifier": "tb1999", "title": "The Band Live on 1999-05-05", "date": "1999-05-05T00:00:00Z",
                             "venue": "The Hall", "creator": "The Band"}]
                return lrweb.json_response({"response": {"numFound": len(docs), "docs": docs}})
            if request.path == "/metadata/TheBand":
                return lrweb.json_response({"metadata": {"title": "The Band"}})
            if request.path in ("/metadata/tb1999", "/metadata/tb2000"):
                return lrweb.json_response({"metadata": {"title": "The Band Live", "creator": "The Band", "date": "1999-05-05",
                                                         "venue": "The Hall", "collection": ["TheBand", "etree"]},
                                            "files": [{"name": "tb d1t02.mp3", "format": "VBR MP3", "title": "Second", "length": "1:02:03"},
                                                      {"name": "tb d1t01.mp3", "format": "VBR MP3", "title": "First", "length": "512.5"},
                                                      {"name": "tb d1t01.flac", "format": "Flac", "title": "First"},
                                                      {"name": "tb.jpg", "format": "JPEG"}]})
            if request.path == "/metadata/nothing":
                return lrweb.json_response({})
            return lrweb.Response(status=404)
        lm_app = lrweb.Application()
        lm_app.router.add_get("/{path:.*}", fake_archive)
        lm_runner = lrweb.AppRunner(lm_app)
        await lm_runner.setup()
        lm_site = lrweb.TCPSite(lm_runner, "127.0.0.1", 0)
        await lm_site.start()
        lm_base = f"http://127.0.0.1:{lm_site._server.sockets[0].getsockname()[1]}"
        real_urls = (lmmod.SEARCH, lmmod.METADATA)
        lmmod.SEARCH, lmmod.METADATA = lm_base + "/advancedsearch.php", lm_base + "/metadata/{id}"
        try:
            lm = (await plugin.save_service({"type": "livemusic", "name": "", "config": {}}))["data"]
            lid = lm["id"]
            check(lm["name"] == "Live Music Archive" and lm["features"] == ["radio"], "add the Live Music Archive")
            home = (await plugin.home(lid))["data"]
            check([s["title"] for s in home] == ["On this day", "Popular this week", "Bands", "Just added"]
                  and [c["id"] for c in home[0]["items"]] == ["s:today"],
                  "home rows; on this day keeps only shows from this date")
            check(home[2]["items"][0]["subtitle"] == "1,234 shows", "bands with their show counts")
            page = (await plugin.open_collection(lid, "album", "s:tb1999"))["data"]
            check([t["title"] for t in page["tracks"]] == ["First", "Second"] and page["tracks"][1]["duration"] == 3723
                  and page["tracks"][0]["artist_id"] == "a:TheBand" and page["subtitle"] == "1999-05-05 · The Hall"
                  and page["art"] == "TheBand" and page["tracks"][0]["art"] == "TheBand",
                  "a show: MP3s in order (FLAC and images left out), lengths, its band and the band's picture")
            band = (await plugin.open_collection(lid, "artist", "a:TheBand"))["data"]
            check(band["title"] == "The Band" and band["subtitle"] == "5,000 shows · the latest 2"
                  and [c["id"] for c in band["collections"]] == ["s:tb2000", "s:tb1999"], "a band: newest shows first, the true count")
            src = await plugin.instances[lid].stream(page["tracks"][0]["id"])
            check(src.url == "https://archive.org/download/tb1999/tb%20d1t01.mp3", "track files are downloaded by name, quoted")
            radio = (await plugin.radio(lid, page["tracks"][0]))["data"]
            check(radio and all(t["album_id"] == "s:tb2000" for t in radio), "radio plays the band's other shows")
            before = len(searches)
            found = (await plugin.search(lid, 'dead "77 (cornell)'))["data"]
            check(found["collections"] and all('"' not in q and "(cornell)" not in q for q in searches[before:]),
                  "search words only: quotes and brackets can't break the query")
            for bad in ["s:../../etc", "s:", "x:1", "a:a b", "s:nothing"]:
                check(not (await plugin.open_collection(lid, "album", bad))["ok"], f"refused: {bad}")
            for bad in ["t:tb1999", "t:../x/y.mp3", "t:tb1999/../../secret", "nope"]:
                check(not (await plugin.stream_info(lid, bad))["ok"], f"not a track: {bad}")
            await plugin.remove_service(lid)
        finally:
            lmmod.SEARCH, lmmod.METADATA = real_urls
            await lm_runner.cleanup()

        print("game music")
        mix = await plugin.create_playlist(sid, "Boss fights", [])
        album = {"id": album_one["id"], "kind": "album", "title": "Album One", "art": None, "extra": "dropped"}
        emitted.clear()
        r = await plugin.set_game_music(sid, 1245620, "ELDEN RING", album)
        check(r["ok"] and r["data"] == [{"instance_id": sid, "appid": 1245620, "game": "ELDEN RING",
                                         "collection": {"id": album_one["id"], "kind": "album", "title": "Album One",
                                                        "subtitle": None, "art": None}}]
              and emitted == ["state_changed"], "an album set to play with a game, announced")
        await plugin.set_game_music(sid, 1245620, "ELDEN RING", mix)
        await plugin.set_game_music(sid, 570, "Dota 2", album)
        music = (await plugin.get_state())["game_music"]
        check(sorted((m["appid"], m["collection"]["title"]) for m in music) == [(570, "Album One"), (1245620, "Boss fights")],
              "one choice per game: a new one replaces the old")
        for bad in [(gone, 570, "x", album), (sid, 0, "x", album), (sid, "570", "x", album), (sid, True, "x", album),
                    (sid, 570, "x", {"id": "a", "kind": "artist"}), (sid, 570, "x", {"kind": "album"}),
                    (sid, 570, "x", "album")]:
            check(not (await plugin.set_game_music(*bad))["ok"], f"refused: {str(bad[1:])[:50]}")
        await plugin.playlist_delete(mix["id"])
        check([m["appid"] for m in (await plugin.get_state())["game_music"]] == [570],
              "deleting a playlist drops the game music it was")
        r = await plugin.set_game_music(sid, 570, "Dota 2", None)
        check(r["ok"] and r["data"] == [], "None removes a game's music")
        other = await plugin.save_service({"type": "radio", "name": "Other", "config": {}})
        await plugin.set_game_music(other["data"]["id"], 570, "Dota 2", {"id": "tag:pop", "kind": "playlist", "title": "Pop"})
        await plugin.set_game_music(sid, 570, "Dota 2", album)
        check(len((await plugin.get_state())["game_music"]) == 2, "each source keeps its own choice for a game")
        await plugin.remove_service(other["data"]["id"])
        check([m["instance_id"] for m in (await plugin.get_state())["game_music"]] == [sid],
              "removing a source drops its game music")
        await plugin.set_game_music(sid, 570, "Dota 2", None)

        print("abuse: nothing a confused user (or a stale UI) does may crash an endpoint")
        failures = []

        async def survives(label, coro):
            """Awaits a call that should fail politely (an error result or nothing), never raise."""
            try:
                return await coro
            except Exception as e:  # noqa: BLE001 - recording any crash is the point
                failures.append(f"{label}: {type(e).__name__}: {e}")
                return None

        gone = "000000000000"
        track = {"id": "x", "title": "T", "artist": "A", "album": "B", "duration": 1}
        for name, call in [
            ("home", plugin.home(gone)), ("open", plugin.open_collection(gone, "album", "1")),
            ("search", plugin.search(gone, "q")), ("stream_info", plugin.stream_info(gone, "1")),
            ("forget_stream", plugin.forget_stream(gone, "1")), ("star", plugin.star(gone, "1", True)),
            ("radio", plugin.radio(gone, track)), ("user_playlists", plugin.user_playlists(gone)),
            ("add_to_playlist", plugin.add_to_playlist(gone, "p", ["1"])), ("lyrics", plugin.get_lyrics(gone, track)),
            ("on_air", plugin.on_air(gone, "1")), ("test", plugin.test_service(gone)), ("rescan", plugin.rescan(gone)),
            ("steam_library", plugin.steam_library(gone, [], None)), ("youtube_update", plugin.youtube_update(gone)),
            ("set_active", plugin.set_active_service(gone)), ("remove", plugin.remove_service(gone)),
            ("scrobble", plugin.scrobble(gone, "1", True, track)), ("scrobble no track", plugin.scrobble(sid, "1", False)),
            ("save_as_playlist", plugin.save_as_playlist(gone, "album", "1")),
            ("sign_in_poll", plugin.sign_in_poll("nope", {})), ("sign_in_cancel", plugin.sign_in_cancel("nope")),
            ("sign_in_start", plugin.sign_in_start("nope", {})),
        ]:
            await survives(f"unknown source: {name}", call)
        check(plugin.settings.get("active_service") == sid, "switching to a source that doesn't exist changes nothing")

        for q in ["", " ", "a" * 5000, "🎵💀", "%00'\";--", "../../etc/passwd"]:
            r = await survives(f"search {q[:10]!r}", plugin.search(sid, q))
            if r is not None and not isinstance(r, dict):
                failures.append(f"search {q[:10]!r} returned {r!r}")
        for kind, cid in [("nope", "1"), ("album", ""), ("album", "../../x"), ("folder", "/"), ("folder", "../.."),
                          ("artist", "\x00"), ("playlist", "mdpl:missing")]:
            await survives(f"open {kind} {cid!r}", plugin.open_collection(sid, kind, cid))
        await survives("stream of a track that isn't there", plugin.stream_info(sid, "nope"))

        for draft in [{"type": "nope"}, {"type": "local"}, {"type": "local", "config": None},
                      {"type": "local", "name": "x" * 1000, "config": {"paths": []}},
                      {"type": "local", "config": {"paths": ["/definitely/not/here", "", "/"]}},
                      {"type": "subsonic", "config": {"url": "::::", "username": "", "password": ""}},
                      {"type": "local", "id": "doesnotexist", "config": {"paths": [lib]}},
                      {"type": "radio", "config": {"country": 42}}]:
            r = await survives(f"save_service {str(draft)[:60]}", plugin.save_service(draft))
            if r and r.get("ok"):
                await plugin.remove_service(r["data"]["id"])
        names = [s["name"] for s in plugin.settings.services()]
        check(len(names) == len(set(names)), f"no two sources end up with the same name {names}")

        # double- and triple-taps: the same save, switch or removal several times at once
        doubled = await asyncio.gather(*[plugin.save_service({"type": "radio", "name": "", "config": {}}) for _ in range(3)])
        radios = [s for s in plugin.settings.services() if s["type"] == "radio"]
        check(all(r["ok"] for r in doubled) and len({s["name"] for s in radios}) == len(radios),
              "a triple-tapped Save adds sources with distinct names, and nothing breaks")
        await asyncio.gather(*[survives("double remove", plugin.remove_service(radios[0]["id"])) for _ in range(3)])
        check(radios[0]["id"] not in plugin.instances and not plugin.settings.find_service(radios[0]["id"]),
              "removing a source three times at once removes it once")
        await asyncio.gather(*[plugin.set_active_service(s["id"]) for s in radios[1:]] + [plugin.set_active_service(sid)])
        check(plugin.settings.get("active_service") in plugin.instances, "racing switches leave a real source live")
        for s in radios[1:]:
            await plugin.remove_service(s["id"])
        await plugin.set_active_service(sid)
        await asyncio.gather(*[survives("rescan spam", plugin.rescan(sid)) for _ in range(5)],
                             *[survives("search during rescan", plugin.search(sid, "song")) for _ in range(5)])
        await plugin.instances[sid].scan_task
        check((await plugin.get_state())["services"][0]["status"]["tracks"] >= 1, "rescans tapped five times settle")

        # playlists: gone, doubled, garbage
        album_id = album_one["id"]
        earlier = plugin.playlists.find_copy(sid, {"kind": "album", "id": album_id})
        if earlier:  # saved by the playlists section above
            await plugin.playlist_delete("md:" + earlier["id"])
        saves = await asyncio.gather(*[plugin.save_as_playlist(sid, "album", album_id) for _ in range(3)])
        check(sum(r["ok"] for r in saves) == 1 and "Already in your playlists" in next(r["error"] for r in saves if not r["ok"]),
              "saving the same album three times at once keeps one copy")
        await plugin.playlist_delete(next(r["data"]["id"] for r in saves if r["ok"]))
        made = await plugin.create_playlist(sid, "   ", [])
        check(made["title"] == "New playlist", "a blank playlist name gets a default")
        long = await plugin.create_playlist(sid, "🎵" * 300, [track, track, {"id": "y"}])
        check(len((await plugin.open_collection(sid, "playlist", long["id"]))["data"]["tracks"]) == 2,
              "the same track twice in a new playlist is kept once")
        await plugin.playlist_delete(long["id"])
        for name, call in [("add", plugin.playlist_add(long["id"], [track])), ("remove", plugin.playlist_remove(long["id"], "x")),
                           ("rename", plugin.playlist_rename(long["id"], "n")), ("clear art", plugin.playlist_clear_art(long["id"])),
                           ("set art", plugin.playlist_set_art(long["id"], "/nope.jpg")), ("delete again", plugin.playlist_delete(long["id"])),
                           ("add garbage", plugin.playlist_add(made["id"], [{"title": "no id"}])),
                           ("open deleted", plugin.open_collection(sid, "playlist", long["id"]))]:
            await survives(f"deleted playlist: {name}", call)
        for path in ["/nope.jpg", lib, os.path.join(lib, "mix.m3u")]:
            r = await survives(f"art from {path}", plugin.playlist_set_art(made["id"], path))
            if r is not None and r.get("ok"):
                failures.append(f"art from {path} was accepted")
        art = os.path.join(tmp, "cover.png")
        write(art)
        await plugin.playlist_set_art(made["id"], art)
        art_dir = plugin.playlists.art_dir
        throwaway = await plugin.save_service({"type": "radio", "name": "Throwaway", "config": {}})
        doomed = await plugin.create_playlist(throwaway["data"]["id"], "Doomed", [])
        await plugin.playlist_set_art(doomed["id"], art)
        await plugin.remove_service(throwaway["data"]["id"])
        pictures = os.listdir(art_dir)
        check(made["id"][len("md:"):] + ".png" in pictures and doomed["id"][len("md:"):] + ".png" not in pictures,
              "removing a source deletes its playlists' pictures too")
        await plugin.playlist_delete(made["id"])

        # the saved queue: garbage in, nothing out
        for session in [{}, {"queue": None}, {"queue": [{"track": track}]}, {"queue": "abc", "index": "x"},
                        {"queue": [{"instanceId": sid, "track": track}], "index": 99, "position": -5}]:
            await survives(f"save_session {str(session)[:40]}", plugin.save_session(session))
            await survives("load_session after it", plugin.load_session())
        with open(plugin._session_path(), "w") as f:
            f.write("{not json")
        check(await survives("corrupt session", plugin.load_session()) is None, "a corrupt saved queue is ignored")
        await survives("garbage events", plugin.log_events([None, 1, "x", {"event": None, "data": "nope"}]))
        await survives("no events", plugin.log_events(None))
        await survives("garbage setting", plugin.set_setting("volume", "loud"))
        check(isinstance(plugin.settings.get("volume"), (int, float)), "a volume that isn't a number is refused")

        check(not failures, "no endpoint crashed:\n      " + "\n      ".join(failures))

        base, key = plugin.server.base_url, plugin.server.key
        some = next(iter(plugin.instances[sid].tracks))
        statuses = {}
        async with aiohttp.ClientSession() as http:
            for label, url, headers in [
                ("wrong key", f"{base}/stream/{sid}/{some}?k=nope", {}),
                ("art, no key", f"{base}/art/{sid}/t:{some}", {}),
                ("fetch, no key", f"{base}/fetch?u=aHR0cHM6Ly9leGFtcGxlLmNvbQ", {}),
                ("path tricks", f"{base}/stream/{sid}/..%2F..%2Fetc%2Fpasswd?k={key}", {}),
                ("art path tricks", f"{base}/art/{sid}/t:../../etc/passwd?k={key}", {}),
                ("unknown source", f"{base}/stream/{gone}/{some}?k={key}", {}),
                ("fetch a file", f"{base}/fetch?k={key}&u=ZmlsZTovLy9ldGMvcGFzc3dk", {}),
                ("fetch garbage", f"{base}/fetch?k={key}&u=%%%", {}),
                ("nonsense range", f"{base}/stream/{sid}/{some}?k={key}", {"Range": "bytes=abc-def"}),
                ("range past the end", f"{base}/stream/{sid}/{some}?k={key}", {"Range": "bytes=999999999-"}),
            ]:
                async with http.get(url, headers=headers) as r:
                    statuses[label] = r.status
        check(all(400 <= v < 500 for v in statuses.values()),
              f"the stream server refuses bad requests, never with a server error {statuses}")

        from musicdeck.settings import Settings as SettingsStore
        from musicdeck.playlists import PlaylistStore as Playlists
        broken = os.path.join(tmp, "broken")
        write(os.path.join(broken, "settings.json"), '{"services": [{"id": "a", "type": "loc')
        write(os.path.join(broken, "playlists.json"), "\x00\x00")
        fresh, lists = SettingsStore(broken), Playlists(broken)
        check(fresh.services() == [] and lists.playlists == []
              and os.path.isfile(os.path.join(broken, "settings.json.corrupt"))
              and os.path.isfile(os.path.join(broken, "playlists.json.corrupt")),
              "a cut-short settings or playlists file is kept aside, not overwritten by the next save")

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
