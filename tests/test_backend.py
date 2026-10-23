"""
End-to-end check of the backend without a Steam Deck: stubs the `decky` module, builds a small
tagged library in a temp dir, then drives main.Plugin and the stream server over real HTTP.

    python tests/test_backend.py      (needs aiohttp + certifi installed; mutagen comes from py_modules)
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import sys
import tempfile
import time
import types
import wave

# what the checks print has stars and dots in it; a Windows console would choke on them otherwise
sys.stdout.reconfigure(encoding="utf-8")

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
from mutagen.id3 import APIC, POPM, TALB, TCMP, TCOM, TCON, TDOR, TDRC, TIT2, TPE1, TPE2, TPUB, TPOS, TRCK, TXXX, USLT  # noqa: E402,E501
from mutagen.wave import WAVE  # noqa: E402

import main  # noqa: E402
from musicdeck.services.base import ServiceError as ServiceErrorForTest  # noqa: E402
from musicdeck.services.subsonic import SubsonicService  # noqa: E402

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def make_wav(path, title, artist, album, track, art=False, gain=None, lyrics=None, albumartist=None, frames=()):
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
    if albumartist != "":
        audio.tags.add(TPE2(encoding=3, text=albumartist or artist))
    audio.tags.add(TALB(encoding=3, text=album))
    audio.tags.add(TRCK(encoding=3, text=str(track)))
    if art:
        audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, data=JPEG))
    if gain is not None:
        audio.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=f"{gain} dB"))
        audio.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK", text="0.95"))
    if lyrics:
        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    for frame in frames:
        audio.tags.add(frame)
    audio.save()


async def _raise(error):
    raise error


async def media_center_tabs(plugin, instance_id):
    """Every media center tab of a source: {tab id: [(section title, display, items, tracks)]}, or the error."""
    service = next(x for x in (await plugin.get_state())["services"] if x["id"] == instance_id)
    found = {}
    for tab in service["tabs"]:
        result = await plugin.center_tab(instance_id, tab["id"])
        found[tab["id"]] = ([(x["title"], x.get("display", "shelf"), len(x["items"]), len(x["tracks"]))
                             for x in result["data"]] if result["ok"] else result["error"])
    return found


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

        tabs = await media_center_tabs(plugin, sid)
        check(list(tabs) == ["home", "albums", "artists", "genres", "years", "folders"],
              f"local files' media center tabs (no Composers: none tagged): {list(tabs)}")
        check(tabs["genres"] == [("", "shelf", 0, 0)] and tabs["years"] == [("", "shelf", 0, 0)],
              f"no genres or years tagged: a note, not an empty page: {tabs['genres']} {tabs['years']}")
        check(tabs["home"] == [("Playlists", "shelf", 1, 0), ("Recently added", "shelf", 2, 0)],
              f"Home: the folder's playlists and what's new, its ways in left to the other tabs: {tabs['home']}")
        check(tabs["albums"] == [("Albums", "grid", 2, 0)] and tabs["artists"] == [("Artists", "grid", 2, 0)],
              f"every album and artist in a grid: {tabs['albums']} {tabs['artists']}")
        check(tabs["folders"][0][1] == "grid" and tabs["folders"][0][2] == 2, f"the folders: {tabs['folders']}")
        check(not (await plugin.center_tab(sid, "nowhere"))["ok"], "a tab the source doesn't have is an error")

        folder = (await plugin.open_collection(sid, "folder", ""))["data"]
        check(sorted(c["title"] for c in folder["collections"]) == ["Artist A", "Artist B"], "folder browsing")
        outside = await plugin.open_collection(sid, "folder", tmp)
        check(not outside["ok"], "folders outside the library are refused")

        results = (await plugin.search(sid, "lonely"))["data"]
        check([t["title"] for t in results["tracks"]] == ["Lonely Song"], "search by title")

        print("local library")
        lib2 = os.path.join(tmp, "Library")
        comp = os.path.join(lib2, "Hits 1999")
        hit = lambda i, artist, genre: make_wav(  # noqa: E731
            os.path.join(comp, f"CD{1 if i < 3 else 2}", f"0{i} song.wav"), f"Hit {i}", artist, "Hits 1999", i,
            albumartist="", frames=[TCON(encoding=3, text=genre), TDRC(encoding=3, text="2004"),
                                    TDOR(encoding=3, text="1999"), TPOS(encoding=3, text="1" if i < 3 else "2")])
        hit(1, "Singer One", "Pop; Dance")
        hit(2, "Singer Two", "pop")
        hit(3, "Singer One", "Rock")
        make_wav(os.path.join(lib2, "Composer", "piece.wav"), "Piece", "Orchestra", "Works", 1,
                 frames=[TCOM(encoding=3, text="Some Composer"), TCON(encoding=3, text="Classical"),
                         TDRC(encoding=3, text="1985-05-01"), TPUB(encoding=3, text="A Label"),
                         POPM(email="x", rating=196), TCMP(encoding=3, text="0")])
        second = await plugin.save_service({"type": "local", "name": "Library", "config": {"paths": [lib2]}})
        lid = second["data"]["id"]
        library = plugin.instances[lid]
        await library.scan_task
        albums = (await plugin.open_collection(lid, "list", "albums"))["data"]["collections"]
        hits = [a for a in albums if a["title"] == "Hits 1999"]
        check(len(hits) == 1 and hits[0]["subtitle"] == "Various artists · 1999",
              f"a compilation without an album artist is one album, over its CD folders, by its first year: {hits}")
        hits_page = (await plugin.open_collection(lid, "album", hits[0]["id"]))["data"]
        check([t["title"] for t in hits_page["tracks"]] == ["Hit 1", "Hit 2", "Hit 3"]
              and hits_page["subtitle"] == "Various artists · 1999 · 3 tracks · WAV",
              f"in disc and track order, with what it is: {hits_page['subtitle']}")
        genres = (await plugin.open_collection(lid, "list", "genres"))["data"]["collections"]
        check([(g["title"], g["subtitle"]) for g in genres] == [("Classical", "1 track"), ("Dance", "1 track"),
                                                                 ("Pop", "2 tracks"), ("Rock", "1 track")],
              f"several genres to a tag, one genre whatever its case: {[(g['title'], g['subtitle']) for g in genres]}")
        pop = (await plugin.open_collection(lid, "list", "genre:Pop"))["data"]
        check([t["title"] for t in pop["tracks"]] == ["Hit 1", "Hit 2"], "a genre's tracks")
        decades = (await plugin.open_collection(lid, "list", "decades"))["data"]["collections"]
        check([d["title"] for d in decades] == ["1990s", "1980s"], f"years by decade, latest first: {decades}")
        tabs2 = await media_center_tabs(plugin, lid)
        check("composers" in tabs2 and tabs2["genres"][0][1] == "tiles", f"a Composers tab once there's one: {list(tabs2)}")
        composer = (await plugin.open_collection(lid, "list", "composer:Some Composer"))["data"]
        check([t["title"] for t in composer["tracks"]] == ["Piece"], "a composer's works")
        singer = next(t for t in hits_page["tracks"] if t["artist"] == "Singer One")
        singer_page = (await plugin.open_collection(lid, "artist", singer["artist_id"]))["data"]
        check(singer_page["collections"] == [] and [t["title"] for t in singer_page["extra"][0]["tracks"]]
              == ["Hit 1", "Hit 3"], "an artist's songs on a compilation: Appears on")
        check([t["title"] for t in (await plugin.search(lid, "classical"))["data"]["tracks"]] == ["Piece"]
              and [t["title"] for t in (await plugin.search(lid, "some composer"))["data"]["tracks"]] == ["Piece"],
              "search finds genres and composers")
        piece = (await plugin.search(lid, "piece"))["data"]["tracks"][0]
        facts = dict((await plugin.track_details(lid, piece))["data"]["facts"])
        check(facts["Format"] == "WAV · 16-bit · 8 kHz" and facts["Year"] == "1985" and facts["Label"] == "A Label"
              and facts["Composer"] == "Some Composer" and facts["Rating"] == "★★★★☆"
              and facts["Plays"] == "Not played yet" and facts["Folder"] == os.path.join("Library", "Composer"),
              f"the file's own facts for Now Playing: {facts}")
        state2 = await plugin.get_state()
        check(next(s for s in state2["services"] if s["id"] == lid)["now_playing"] == "library",
              "Now Playing shows it as a library (with Info)")
        home2 = (await plugin.home(lid))["data"]
        check([s["title"] for s in home2] == ["Recently added", "Top rated", "Library"],
              f"rated files make a Top rated row; nothing played, no played rows: {[s['title'] for s in home2]}")
        for _ in range(2):
            plugin.recent_scrobbles.clear()
            await plugin.scrobble(lid, piece["id"], True, piece)
        plugin.recent_scrobbles.clear()
        await plugin.scrobble(lid, hits_page["tracks"][0]["id"], True, hits_page["tracks"][0])
        home2 = (await plugin.home(lid))["data"]
        rows = {s["title"]: s for s in home2}
        check([s["title"] for s in home2] == ["Recently added", "Recently played", "Most played", "Top rated",
                                              "Library"],
              f"played: Recently played, Most played (played twice or more): {[s['title'] for s in home2]}")
        check([t["title"] for t in rows["Recently played"]["tracks"]] == ["Hit 1", "Piece"]
              and [t["title"] for t in rows["Most played"]["tracks"]] == ["Piece"], "in the order they were played")
        facts = dict((await plugin.track_details(lid, piece))["data"]["facts"])
        check(facts["Plays"].startswith("2 plays, last "), f"plays counted: {facts['Plays']}")
        restarted = type(library)(lid, library.config, library.runtime_dir)
        await restarted.start()
        await restarted.scan_task
        check(restarted.plays[piece["id"]]["count"] == 2, "play counts are kept on the Deck")
        await restarted.stop()

        print("local library: lyrics in FLAC")
        from musicdeck.services.local import read_lyrics
        from mutagen.flac import FLAC
        flac_path = os.path.join(tmp, "plain.flac")
        info = (4096).to_bytes(2, "big") * 2 + bytes(6) + ((44100 << 44) | (1 << 41) | (15 << 36)).to_bytes(8, "big") + bytes(16)
        with open(flac_path, "wb") as f:
            f.write(b"fLaC" + bytes([0x80]) + len(info).to_bytes(3, "big") + info)
        flac = FLAC(flac_path)
        flac["TITLE"] = "Tagged"
        flac.save()
        check(read_lyrics(flac_path) is None, "a tagged FLAC file without lyrics has none (MP4's lyrics key isn't asked of it)")
        flac = FLAC(flac_path)
        flac["LYRICS"] = "la la"
        flac.save()
        check(read_lyrics(flac_path)["lines"][0]["text"] == "la la", "and its own lyrics are read")

        print("local library: tags and artwork")
        from aiohttp import web as mbweb
        from musicdeck import musicbrainz as mbmod
        # an MP3 (MPEG-1 layer III frames of silence), to write through EasyID3 as well as raw ID3 in WAV
        mp3_dir = os.path.join(lib2, "Loose")
        os.makedirs(mp3_dir, exist_ok=True)
        frame = bytes.fromhex("fffb9064") + bytes(413)
        with open(os.path.join(mp3_dir, "untitled.mp3"), "wb") as f:
            f.write(frame * 20)
        library.rescan()
        await library.scan_task
        loose = next(tid for tid, t in library.tracks.items() if t["path"].endswith("untitled.mp3"))
        fields = (await plugin.get_tags(lid, "track", loose))["data"]
        check([f["field"] for f in fields["fields"]][:4] == ["title", "artist", "album", "albumartist"]
              and fields["fields"][0]["value"] == "untitled" and not fields["undo"], f"a track's fields: {fields}")
        edited = await plugin.set_tags(lid, "track", loose, {"title": "Found", "artist": "Someone", "album": "Loose Ends",
                                                             "track": "3", "year": "2001", "genre": "Pop; Rock"})
        t = library.tracks[loose]
        check(edited["ok"] and t["title"] == "Found" and t["artist"] == "Someone" and t["track"] == 3
              and t["year"] == "2001" and t["genres"] == ["Pop", "Rock"], f"MP3 tags written and read back: {edited} {t}")
        check(not any(n.startswith(".") for n in os.listdir(mp3_dir)), "no copy left beside the file")
        check(not (await plugin.set_tags(lid, "track", loose, {"year": "nineteen"}))["ok"]
              and not (await plugin.set_tags(lid, "track", loose, {"track": "-1"}))["ok"]
              and not (await plugin.set_tags(lid, "track", loose, {}))["ok"], "bad years, numbers and empty edits refused")
        check((await plugin.get_tags(lid, "track", loose))["data"]["undo"], "the edit can be undone")
        await plugin.undo_tags(lid, "track", loose)
        t = library.tracks[loose]
        check(t["title"] == "untitled" and t["artist"] == "" and t["year"] == "" and t["genres"] == []
              and not (await plugin.get_tags(lid, "track", loose))["data"]["undo"], f"undone, as it was: {t}")
        check(not (await plugin.undo_tags(lid, "track", loose))["ok"], "nothing more to undo")

        albums = (await plugin.open_collection(lid, "list", "albums"))["data"]["collections"]
        works = next(a for a in albums if a["title"] == "Works")
        album_fields = (await plugin.get_tags(lid, "album", works["id"]))["data"]
        check([f["field"] for f in album_fields["fields"]] == ["album", "albumartist", "year", "genre", "label"]
              and album_fields["files"] == 1, f"an album's shared fields: {album_fields}")
        hits_fields = {f["field"]: f for f in (await plugin.get_tags(lid, "album", hits[0]["id"]))["data"]["fields"]}
        check(hits_fields["genre"]["mixed"] and not hits_fields["album"]["mixed"] and hits_fields["album"]["value"] == "Hits 1999",
              "tracks that differ show as mixed")
        png = os.path.join(tmp, "cover.png")
        with open(png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + bytes(32))
        check((await plugin.album_art(lid, works["id"], "set", png))["ok"], "a picture picked for an album")
        piece = (await plugin.search(lid, "piece"))["data"]["tracks"][0]
        check("." in piece["art"].split(":", 1)[1], f"its art id changes, so it isn't shown from the cache: {piece['art']}")
        src = await library.art(piece["art"])
        check(src.path and src.path.endswith(".png") and library.art_dir in src.path, "the album's own picture comes first")
        check(not (await plugin.album_art(lid, works["id"], "set", os.path.join(tmp, "x.txt")))["ok"], "only pictures")
        plugin.recents.add(lid, works)  # played as it was
        renamed = (await plugin.set_tags(lid, "album", works["id"], {"album": "Collected Works", "year": "1986"}))["data"]
        jump = next(sec for sec in (await plugin.home(lid))["data"] if sec["title"] == "Jump back in")["items"][0]
        check(jump["id"] == renamed["album_id"] and jump["title"] == "Collected Works" and jump["art"] != works["art"],
              f"Jump back in shows an album as it is now: renamed, with its picture: {jump}")
        check(renamed["album_id"] != works["id"] and renamed["album_id"] in library.album_art
              and works["id"] not in library.album_art, "a renamed album keeps its picture")
        piece_t = library.tracks[piece["id"]]
        check(piece_t["album"] == "Collected Works" and piece_t["year"] == "1986" and piece_t["title"] == "Piece",
              f"an album edit writes its files (WAV, raw ID3; the original year too): {piece_t}")
        await plugin.album_art(lid, renamed["album_id"], "clear")
        check(renamed["album_id"] not in library.album_art and not os.listdir(library.art_dir), "and lets it go")

        mb_calls = []
        big_cover = JPEG[:-2] + bytes(300_000) + JPEG[-2:]
        mb_release = {"id": "rel1", "title": "Hits 1999", "date": "2004", "artist-credit": [{"name": "Various Artists"}],
                      "release-group": {"id": "rg1", "first-release-date": "1999-03-01", "genres": [{"name": "pop", "count": 3}]},
                      "label-info": [{"label": {"name": "Hit Label"}}],
                      "media": [{"position": 1, "format": "CD", "tracks": [
                          {"position": 1, "title": "Hit One", "artist-credit": [{"name": "Singer One"}]},
                          {"position": 2, "title": "Hit 2", "artist-credit": [{"name": "Singer Two"}]}]},
                                {"position": 2, "format": "CD", "tracks": [
                          {"position": 1, "title": "Hit 3", "artist-credit": [{"name": "Singer One"}]}]}]}

        async def fake_mb(request):
            mb_calls.append((request.path, dict(request.query), request.headers.get("User-Agent", "")))
            if request.path == "/ws/2/release/" and ('"New Arrival"' in request.query.get("query", "")
                                                      or '"Sure Thing"' in request.query.get("query", "")):
                name = "New Arrival" if "New Arrival" in request.query["query"] else "Sure Thing"
                rid = "rel3" if name == "New Arrival" else "rel4"
                return mbweb.json_response({"releases": [
                    {"id": rid, "title": name, "score": 100, "artist-credit": [{"name": "Singer One"}],
                     "media": [{"track-count": 2}], "release-group": {"id": "rg-" + rid}}]})
            if request.path in ("/ws/2/release/rel3", "/ws/2/release/rel4"):
                rid = request.path.rsplit("/", 1)[1]
                return mbweb.json_response({
                    "id": rid, "title": "New Arrival" if rid == "rel3" else "Sure Thing", "date": "2021",
                    "artist-credit": [{"name": "Singer One"}],
                    "release-group": {"id": "rg-" + rid, "first-release-date": "2021", "genres": [{"name": "pop", "count": 1}]},
                    "media": [{"position": 1, "tracks": [
                        {"position": 1, "title": "Arrival Song", "artist-credit": [{"name": "Singer One"}]},
                        {"position": 2, "title": "Second Song", "artist-credit": [{"name": "Singer One"}]}]}]})
            if request.path in ("/release/rel3/front-500", "/release/rel4/front-500"):
                return mbweb.Response(body=JPEG, content_type="image/jpeg")
            if request.path == "/ws/2/release/":
                return mbweb.json_response({"releases": [
                    {"id": "rel2", "title": "Hits 1999", "score": 100, "date": "1999", "media": [{"track-count": 12, "format": "CD"}]},
                    {"id": "rel1", "title": "Hits 1999", "score": 95, "date": "2004", "release-group": {"id": "rg1"},
                     "artist-credit": [{"name": "Various Artists"}], "media": [{"track-count": 2}, {"track-count": 1}]}]})
            if request.path == "/ws/2/release/rel1":
                return mbweb.json_response(mb_release)
            if request.path == "/itunes/search":
                found = "Catalogue Only" in request.query.get("term", "")
                return mbweb.json_response({"results": [
                    {"collectionId": 77, "collectionName": "Catalogue Only", "artistName": "Indie Band", "trackCount": 2,
                     "releaseDate": "2023-01-01T08:00:00Z", "country": "USA"}] if found else []})
            if request.path == "/itunes/lookup" and request.query.get("id") == "77":
                return mbweb.json_response({"results": [
                    {"wrapperType": "collection", "collectionName": "Catalogue Only", "artistName": "Indie Band",
                     "releaseDate": "2023-01-01T08:00:00Z", "primaryGenreName": "Indie",
                     "artworkUrl100": mb_base + "/pic/100x100bb.jpg"},
                    {"wrapperType": "track", "trackName": "First Light", "trackNumber": 1, "discNumber": 1, "artistName": "Indie Band"},
                    {"wrapperType": "track", "trackName": "Last Light", "trackNumber": 2, "discNumber": 1, "artistName": "Indie Band"}]})
            if request.path == "/pic/1200x1200bb.jpg":
                return mbweb.Response(body=JPEG, content_type="image/jpeg")
            if request.path == "/deezer/search/album":
                found = "Catalogue Only" in request.query.get("q", "")
                return mbweb.json_response({"data": [
                    {"id": 88, "title": "Catalogue Only", "artist": {"name": "Indie Band"}, "nb_tracks": 2}] if found else []})
            if request.path == "/deezer/album/88":
                return mbweb.json_response({"title": "Catalogue Only", "artist": {"name": "Indie Band"}, "label": "Small Label",
                                            "release_date": "2023-01-01", "genres": {"data": [{"name": "Indie"}]},
                                            "cover_xl": mb_base + "/pic/real.jpg"})
            if request.path == "/deezer/album/88/tracks":
                return mbweb.json_response({"data": [
                    {"title": "First Light", "track_position": 1, "disk_number": 1, "artist": {"name": "Indie Band"}},
                    {"title": "Last Light", "track_position": 2, "disk_number": 1, "artist": {"name": "Indie Band"}}]})
            if request.path == "/deezer/search/artist":
                q = request.query.get("q")
                return mbweb.json_response({"data": [
                    {"name": q + " Tribute Band", "picture_xl": mb_base + "/pic/tribute.jpg", "nb_fan": 999},
                    {"name": q.upper(), "picture_xl": mb_base + "/pic/real.jpg", "nb_fan": 50},
                    {"name": q, "picture_xl": "https://e-cdns-images.dzcdn.net/images/artist//1000x1000.jpg", "nb_fan": 5},
                ] if q == "Singer One" else [{"name": q, "picture_xl": "https://e-cdns-images.dzcdn.net/images/artist//x.jpg"}]})
            if request.path == "/pic/real.jpg":
                return mbweb.Response(body=JPEG, content_type="image/jpeg")
            if request.path == "/lrclib/get":
                if request.query.get("track_name") == "arrival song":
                    return mbweb.json_response({"plainLyrics": "here I am"})
                if request.query.get("track_name") == "Hit 2":
                    return mbweb.json_response({"syncedLyrics": "[00:01.50]Hello road\n[01:02.25]Goodbye"})
                return mbweb.Response(status=404)
            if request.path == "/lrclib/search":
                return mbweb.json_response([])
            if request.path == "/netease/search/get":
                return mbweb.json_response({"result": {"songs": []}})
            if request.path == "/release/rel1/front-500":
                # big, and in pieces with pauses between, as a real download arrives
                resp = mbweb.StreamResponse(headers={"Content-Type": "image/jpeg", "Content-Length": str(len(big_cover))})
                await resp.prepare(request)
                for at in range(0, len(big_cover), 50_000):
                    await resp.write(big_cover[at:at + 50_000])
                    await asyncio.sleep(0.01)
                await resp.write_eof()
                return resp
            return mbweb.Response(status=404)
        mb_app = mbweb.Application()
        mb_app.router.add_route("*", "/{path:.*}", fake_mb)
        mb_runner = mbweb.AppRunner(mb_app)
        await mb_runner.setup()
        mb_site = mbweb.TCPSite(mb_runner, "127.0.0.1", 0)
        await mb_site.start()
        mb_base = f"http://127.0.0.1:{mb_site._server.sockets[0].getsockname()[1]}"
        from musicdeck import catalogues as catmod, lyrics as lymod, pictures as picmod
        from musicdeck.services import local as localmod
        real_mb = (mbmod.API, mbmod.CAA, mbmod.SPACING, picmod.API, lymod.LRCLIB, lymod.NETEASE, localmod.LYRICS_PACE,
                   catmod.ITUNES, catmod.DEEZER)
        mbmod.API, mbmod.CAA, mbmod.SPACING = mb_base + "/ws/2", mb_base, 0
        picmod.API, lymod.LRCLIB, lymod.NETEASE, localmod.LYRICS_PACE = (mb_base + "/deezer", mb_base + "/lrclib",
                                                                        mb_base + "/netease", 0)
        catmod.ITUNES, catmod.DEEZER = mb_base + "/itunes", mb_base + "/deezer"
        try:
            found = (await plugin.lookup_album(lid, hits[0]["id"]))["data"]
            check([r["id"] for r in found] == ["rel1", "rel2"] and found[0]["tracks"] == 3 and found[0]["group"] == "rg1",
                  f"releases with the album's track count first: {found}")
            check("artist:" not in mb_calls[0][1]["query"] and mb_calls[0][2].startswith("MusicDeck/"),
                  f"a compilation isn't searched by 'Various artists'; the app names itself: {mb_calls[0]}")
            preview = (await plugin.lookup_changes(lid, hits[0]["id"], "rel1"))["data"]
            album_changes = {c[0]: (c[1], c[2]) for c in preview["album"]}
            check(preview["matched"] and album_changes.get("Album artist") == ("", "Various Artists")
                  and album_changes.get("Label") == ("", "Hit Label") and album_changes.get("Genre") == ("Pop; Dance", "Pop")
                  and "Year" not in album_changes and "Album" not in album_changes,
                  f"what would change for the album (its first year already right): {preview['album']}")
            check(preview["tracks"] == [["01 song.wav", [["Title", "Hit 1", "Hit One"]]], ["03 song.wav", [["Track", "3", "1"]]]],
                  f"and track by track, in disc order (numbered on each disc): {preview['tracks']}")
            applied = (await plugin.apply_lookup(lid, hits[0]["id"], "rel1"))["data"]
            new_hits = applied["album_id"]
            check(applied["changed"] == 3 and applied.get("art") and new_hits in library.album_art,
                  f"applied to every file, and the missing cover found: {applied}")
            with open(os.path.join(library.art_dir, library.album_art[new_hits]), "rb") as f:
                check(f.read() == big_cover, "the whole cover is saved, however it arrives")
            hit_tracks = (await plugin.open_collection(lid, "album", new_hits))["data"]["tracks"]
            check([t["title"] for t in hit_tracks] == ["Hit One", "Hit 2", "Hit 3"]
                  and all(library.tracks[t["id"]]["albumartist"] == "Various Artists" for t in hit_tracks),
                  "the album now as MusicBrainz has it")
            await plugin.album_art(lid, new_hits, "clear")
            mb_calls.clear()
            await plugin.find_missing_art(lid)
            await library.art_task
            st = next(x for x in (await plugin.get_state())["services"] if x["id"] == lid)["status"]["art"]
            check(not st["running"] and st["total"] >= 1 and st["found"] == 1 and new_hits in library.album_art,
                  f"missing artwork found in the background: {st}")
            print("local library: artists' pictures, lyrics, Browse")
            artists_now = (await plugin.open_collection(lid, "list", "artists"))["data"]["collections"]
            singer = next(t for t in library.tracks.values() if t["title"] == "Hit One")
            singer_key = hashlib.sha1(singer["artist"].lower().encode()).hexdigest()[:16]
            found_pic = await plugin.artist_art(lid, singer_key, "find")
            check(found_pic["ok"] and found_pic["data"] is True and "artist-" + singer_key in library.album_art,
                  f"an artist's picture, for that exact name, not a tribute band's or a blank one: {found_pic}")
            singer_page = (await plugin.open_collection(lid, "artist", singer_key))["data"]
            check(singer_page["art"] and singer_page["art"].startswith("a:" + singer_key + "."),
                  f"shown on their page: {singer_page['art']}")
            src = await library.art(singer_page["art"])
            check(open(src.path, "rb").read() == JPEG, "and served")
            two = next(t for t in library.tracks.values() if t["title"] == "Hit 2")
            two_key = hashlib.sha1(two["artist"].lower().encode()).hexdigest()[:16]
            check((await plugin.artist_art(lid, two_key, "find"))["data"] is False, "a blank picture is no picture")
            check(not (await plugin.artist_art(lid, "nobody", "find"))["ok"], "an artist who isn't there")
            await plugin.artist_art(lid, singer_key, "clear")
            check("artist-" + singer_key not in library.album_art, "and let go")
            del artists_now

            hit2 = next(tid for tid, t in library.tracks.items() if t["title"] == "Hit 2")
            saved_lyrics = await plugin.save_lyrics(lid, hit2)
            beside = os.path.splitext(library.tracks[hit2]["path"])[0] + ".lrc"
            kept = os.path.join(library.lyrics_dir, hit2 + ".lrc")
            check(saved_lyrics["data"] is True and not os.path.exists(beside) and open(kept, encoding="utf-8").read()
                  == "[ar:Singer Two]\n[ti:Hit 2]\n[al:Hits 1999]\n[length:00:01]\n[00:01.50]Hello road\n[01:02.25]Goodbye\n",
                  f"lyrics kept in MusicDeck, the music folder left as it was: {saved_lyrics}")
            got = (await plugin.get_lyrics(lid, {"id": hit2}))["data"]
            check(got["synced"] and got["lines"][1] == {"t": 62.25, "text": "Goodbye"} and got["source"] == "Saved lyrics",
                  "and read back from there")
            os.remove(kept)
            library.config["lyrics_place"] = "beside"
            await plugin.save_lyrics(lid, hit2)
            lrc = beside
            got = (await plugin.get_lyrics(lid, {"id": hit2}))["data"]
            check(os.path.exists(beside) and got["source"] == "Lyrics file",
                  "or, set so, beside the song as the same-named .lrc other players read")
            check(not (await plugin.save_lyrics(lid, hit2))["ok"], "a track with lyrics isn't looked up again")
            batch = (await plugin.find_lyrics(lid, new_hits))["data"]
            check(batch == {"found": 0, "total": 2}, f"an album's tracks without lyrics looked up: {batch}")
            os.remove(lrc)
            await plugin.find_missing_lyrics(lid)
            await library.lyrics_task
            lst = next(x for x in (await plugin.get_state())["services"] if x["id"] == lid)["status"]["lyrics"]
            check(not lst["running"] and lst["found"] == 1 and os.path.exists(lrc), f"every missing lyric, in the background: {lst}")

            library.config["lyrics_place"] = "musicdeck"
            tiles = {t["id"]: t for t in next(x for x in (await plugin.get_state())["services"] if x["id"] == lid)["tabs"]}
            check(tiles["albums"]["group"] == "yours" and tiles["genres"]["group"] == "explore"
                  and tiles["folders"]["group"] == "files" and tiles["albums"]["subtitle"].endswith("albums")
                  and len(tiles["albums"]["art"]) >= 1, f"Browse's tiles: what's in each, grouped: {tiles}")

            print("local library: new music")
            check(library.config["auto_tags"] == "ask" and library.config["auto_art"] is True
                  and library.config["auto_lyrics"] is False and library.config["lyrics_place"] == "musicdeck",
                  f"new music: covers, Ask me for tags, no lyrics, kept in MusicDeck, unless set otherwise: {library.config}")
            check(type(library).prepare_config({"auto_tags": "always", "lyrics_place": "/etc", "auto_art": "yes"}, None)
                  == {"auto_tags": "ask", "lyrics_place": "musicdeck", "auto_art": True, "auto_lyrics": False},
                  "odd choices fall back to the defaults")
            check(library.import_progress["total"] == 0 or all(not t["album"] for t in library.tracks.values() if t["path"].endswith(".mp3")),
                  "a library's first scan isn't new music")
            emitted.clear()
            arrive = os.path.join(lib2, "New Arrival")
            for n, title in ((1, "arrival song"), (2, "second song")):
                make_wav(os.path.join(arrive, f"0{n} {title}.wav"), title, "Singer One", "New Arrival", n)
            library.rescan()
            await library.scan_task
            await library.import_task
            arrival = next(k for k, v in library._albums().items() if v[0][1]["album"] == "New Arrival")
            check(arrival in library.album_art and arrival in library.review and "source_note" in emitted,
                  f"a new album gets its cover, goes on the list to review, and the user is told: {library.review}")
            check(all(library.tracks[tid]["title"] in ("arrival song", "second song") for tid, _ in library._albums()[arrival]),
                  "Ask me writes nothing")
            review_tab = await plugin.center_tab(lid, "review")
            tiles = {t["id"]: t for t in next(x for x in (await plugin.get_state())["services"] if x["id"] == lid)["tabs"]}
            check(tiles["review"]["subtitle"] == "1 album" and review_tab["data"][1]["items"][0]["id"] == arrival
                  and (await plugin.get_tags(lid, "album", arrival))["data"]["review"],
                  "on Browse as To review, and its menu knows")
            await plugin.dismiss_review(lid, arrival)
            check(arrival not in library.review
                  and "review" not in {t["id"] for t in next(x for x in (await plugin.get_state())["services"] if x["id"] == lid)["tabs"]},
                  "dismissed, and the tile goes")

            library.config.update(auto_tags="sure", auto_lyrics=True)
            sure_dir = os.path.join(lib2, "Sure Thing")
            for n, title in ((1, "arrival song"), (2, "second song")):
                make_wav(os.path.join(sure_dir, f"0{n} {title}.wav"), title, "Singer One", "Sure Thing", n)
            library.rescan()
            await library.scan_task
            await library.import_task
            sure = next(k for k, v in library._albums().items() if v[0][1]["album"] == "Sure Thing")
            sure_tracks = [library.tracks[tid] for tid, _ in library._albums()[sure]]
            check(all(t["year"] == "2021" and t["genres"] == ["Pop"] for t in sure_tracks)
                  and [t["title"] for t in sure_tracks] == ["arrival song", "second song"],
                  f"Only when sure: what was empty is filled in, nothing written over: {[(t['title'], t['year']) for t in sure_tracks]}")
            check(os.path.exists(os.path.join(library.lyrics_dir, library._albums()[sure][0][0] + ".lrc"))
                  and not any(n.endswith(".lrc") for n in os.listdir(sure_dir)),
                  "and its lyrics found, kept in MusicDeck")
            library.config.update(auto_tags="ask", auto_lyrics=False)

            print("local library: Apple Music's and Deezer's catalogues")
            library.config.update(auto_tags="off", auto_art=False)
            cat_dir = os.path.join(lib2, "Catalogue Only")
            for n, title in ((1, "first light"), (2, "last light")):
                make_wav(os.path.join(cat_dir, f"0{n} {title}.wav"), title, "Indie Band", "Catalogue Only", n)
            library.rescan()
            await library.scan_task
            cat = next(k for k, v in library._albums().items() if v[0][1]["album"] == "Catalogue Only")
            found = (await plugin.lookup_album(lid, cat))["data"]
            check([(r["id"], r["source"]) for r in found[:2]] == [("itunes:77", "Apple Music"), ("deezer:88", "Deezer")]
                  and found[0]["year"] == "2023", f"an album MusicBrainz doesn't know, found in the catalogues: {found[:3]}")
            preview = (await plugin.lookup_changes(lid, cat, "deezer:88"))["data"]
            check({c[0]: c[2] for c in preview["album"]} == {"Year": "2023", "Genre": "Indie",
                                                              "Label": "Small Label"}
                  and [len(t[1]) for t in preview["tracks"]] == [1, 1], f"Deezer's album, track by track: {preview}")
            applied = (await plugin.apply_lookup(lid, cat, "deezer:88"))["data"]
            cat = applied["album_id"]
            check(applied["changed"] == 2 and applied.get("art") and cat in library.album_art,
                  f"applied, with its cover: {applied}")
            await plugin.album_art(lid, cat, "clear")
            check((await plugin.album_art(lid, cat, "find"))["data"] is True and cat in library.album_art,
                  "no cover at the Cover Art Archive: Apple's instead")
            check(not (await plugin.lookup_changes(lid, cat, "itunes:../x"))["ok"], "a catalogue id that isn't one")
            library.config.update(auto_tags="ask", auto_art=True)
        finally:
            (mbmod.API, mbmod.CAA, mbmod.SPACING, picmod.API, lymod.LRCLIB, lymod.NETEASE, localmod.LYRICS_PACE,
             catmod.ITUNES, catmod.DEEZER) = real_mb
            await mb_runner.cleanup()
        check(not (await plugin.get_tags(sid, "track", "nope"))["ok"], "a track that isn't there")

        await plugin.remove_service(lid)
        check(not os.path.exists(library.plays_path) and not os.path.exists(library.art_dir), "and go with the source")

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

        print("media center settings")
        center = (await plugin.get_state())["center"]
        check(center["open_on"] == "last" and center["idle_off"] == 0, "the media center starts with its defaults")
        await plugin.set_setting("center", {**center, "cards": "large", "idle_off": 5, "show_browse": False})
        center = (await plugin.get_state())["center"]
        check(center["cards"] == "large" and center["idle_off"] == 5 and center["show_browse"] is False,
              "its choices are kept")
        plugin.settings.set("center", {**center, "show_sound": True})  # a choice an older version had
        check("show_sound" not in (await plugin.get_state())["center"], "choices that no longer exist aren't handed out")
        plugin.settings.set("center", center)
        for bad in ({"cards": "huge"}, {"idle_off": 3}, {"idle_off": True}, {"open_on": ["home"]}, {"theme": "x"},
                    {"show_library": "yes"}):
            await plugin.set_setting("center", {**center, **bad})
            check((await plugin.get_state())["center"] == center, f"an unknown choice is refused: {bad}")

        print("the EQ for each output")
        from musicdeck.audio_output import describe
        jack = {"name": "alsa_output.pci-0000_64_00.6.analog-stereo", "description": "Ryzen HD Audio Controller Analog Stereo",
                "properties": {"device.api": "alsa", "device.bus": "pci"},
                "ports": [{"name": "analog-output-speaker", "description": "Speaker"},
                          {"name": "analog-output-headphones", "description": "Headphones"}]}
        buds = {"name": "bluez_output.AA_BB_CC_DD_EE_FF.1", "description": "Buds",
                "properties": {"device.api": "bluez5", "device.description": "Pixel Buds"}, "active_port": "headset-output",
                "ports": [{"name": "headset-output", "description": "Headset"}]}
        tv = {"name": "alsa_output.pci-0000_64_00.1.hdmi-stereo", "description": "Rembrandt HDMI",
              "properties": {"device.api": "alsa"}, "active_port": "hdmi-output-0",
              "ports": [{"name": "hdmi-output-0", "description": "HDMI / DisplayPort"}]}
        speakers = describe([{**jack, "active_port": "analog-output-speaker"}, buds], jack["name"])
        headphones = describe([{**jack, "active_port": "analog-output-headphones"}, buds], jack["name"])
        check(speakers["name"] == "Speakers" and headphones["name"] == "Headphones" and speakers["id"] != headphones["id"],
              "the speakers and the headphone jack are told apart, though they're one device")
        check(describe([jack, buds], buds["name"]) == {"id": "bluez_output.AA_BB_CC_DD_EE_FF.1#headset-output", "name": "Pixel Buds"},
              "a Bluetooth device goes by its own name, and its id by its address")
        check(describe([tv], tv["name"])["name"] == "HDMI / DisplayPort" and describe([jack], "gone") is None,
              "other outputs go by their port; a default sink that isn't listed is no output")

        general = {"enabled": True, "preset": "Rock", "gains": [4.0, 3, 2, 0, -1, -1, 1, 2, 3, 4], "preamp": 0.0}
        flat = {"enabled": True, "preset": "Flat", "gains": [0.0] * 10, "preamp": -2.0}
        await plugin.set_eq(general, None)
        plugin.output = speakers
        check((await plugin.get_state())["eq"] == general, "with one EQ, every output has it")
        await plugin.set_setting("eq_per_output", True)
        check((await plugin.get_state())["eq"] == general, "an output without its own EQ yet starts from the one EQ")
        await plugin.set_eq(flat, speakers["id"])
        check((await plugin.get_state())["eq"] == flat, "an EQ set on an output is that output's")
        plugin.output = headphones
        check((await plugin.get_state())["eq"] == general, "another output keeps its own (here still the one EQ)")
        await plugin.set_eq(flat, speakers["id"])
        check(plugin.settings.get("output_eqs")[speakers["id"]]["name"] == "Speakers",
              "an EQ saved after the output changed still goes to the output it was set on")
        await plugin.set_eq({"enabled": True, "gains": "loud"}, headphones["id"])
        check(headphones["id"] not in plugin.settings.get("output_eqs"), "an invalid EQ isn't saved")
        await plugin.set_setting("eq_per_output", False)
        check((await plugin.get_state())["eq"] == general and plugin.settings.get("eq") == general,
              "turned off, the one EQ applies again, and the outputs' own are kept for next time")
        plugin.output = None

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
            if request.path == "/Playlists" and request.method == "POST":
                return emweb.json_response({"Id": "p2"})
            if request.path == "/Users/eu/Items/p2":
                return emweb.json_response({"Id": "p2", "Name": request.query.get("Name", "Road trip"), "Type": "Playlist",
                                            "ChildCount": 1, "ImageTags": {}})
            if request.path == "/Items":
                if request.query.get("ParentId") == "al1":
                    return emweb.json_response({"Items": [{"Id": "t1", "Name": "Song", "Type": "Audio", "AlbumId": "al1",
                                                           "RunTimeTicks": 600_000_000, "ImageTags": {}}]})
                if request.query.get("IncludeItemTypes") == "Playlist":
                    return emweb.json_response({"Items": [{"Id": "p1", "Name": "Mix", "Type": "Playlist", "ImageTags": {}}]})
                return emweb.json_response({"Items": []})
            if request.path == "/Artists/AlbumArtists":
                return emweb.json_response({"Items": [{"Id": "ar1", "Name": "A", "ImageTags": {}}]})
            if request.path == "/MusicGenres":
                return emweb.json_response({"Items": [{"Id": "g1", "Name": "Rock"}, {"Name": "no id"}]})
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
            made = await plugin.create_service_playlist(eid, "  Road trip ", ["t1"])
            create = next(c for c in emby_calls if c[:2] == ("POST", "/Playlists"))
            check(made["ok"] and made["data"]["id"] == "p2" and made["data"]["kind"] == "playlist"
                  and create[2].get("Name") == "Road trip" and create[2].get("Ids") == "t1" and create[2].get("UserId") == "eu"
                  and create[2].get("MediaType") == "Audio", f"a new playlist on the server, with the track: {create}")
            check(not (await plugin.create_service_playlist(eid, "   ", ["t1"]))["ok"], "a playlist needs a name")
            await plugin.instances[eid].star("t1", True)
            await plugin.instances[eid].star("t1", False)
            check([c[:2] for c in emby_calls[-2:]] == [("POST", "/Users/eu/FavoriteItems/t1"), ("DELETE", "/Users/eu/FavoriteItems/t1")],
                  "favourites under the user")
            await plugin.instances[eid].scrobble("t1", False)
            await plugin.instances[eid].scrobble("t1", True)
            check(emby_calls[-2][1] == "/Sessions/Playing" and emby_calls[-2][3]["MediaSourceId"] == "t1"
                  and emby_calls[-1][:2] == ("POST", "/Users/eu/PlayedItems/t1"), "now playing with a session id; played under the user")
            tabs = await media_center_tabs(plugin, eid)
            check(list(tabs) == ["home", "albums", "artists", "genres", "library"]
                  and all(isinstance(v, list) for v in tabs.values()),
                  f"Emby's (Jellyfin's) media center tabs all open: {tabs}")
            check(tabs["artists"] == [("Artists", "grid", 1, 0)] and tabs["genres"] == [("Genres", "rows", 1, 0)]
                  and ("Playlists", "grid", 1, 0) in tabs["library"], f"its artists, genres and playlists: {tabs}")
            genre = (await plugin.open_collection(eid, "list", "genre:g1#Rock"))["data"]
            check(genre["title"] == "Rock" and any(c[1] == "/Items" and c[2].get("GenreIds") == "g1" for c in emby_calls),
                  "a genre opens to its albums")
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
            if path == "/playlists" and request.method == "POST":
                return meta([{"ratingKey": "42", "type": "playlist", "title": request.query.get("title"), "leafCount": 2}])
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
            tabs = await media_center_tabs(plugin, pid)
            check(list(tabs) == ["home", "albums", "artists", "genres", "library"]
                  and all(isinstance(v, list) for v in tabs.values())
                  and ("Playlists", "grid", 2, 0) in tabs["library"],
                  f"Plex's media center tabs all open, its playlists in Library: {tabs}")
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
            made = (await plugin.create_service_playlist(pid, "Road trip", ["11", "12"]))["data"]
            check(made["id"] == "42" and made["title"] == "Road trip" and px_calls[-1][2].get("type") == "audio"
                  and px_calls[-1][2].get("smart") == "0"
                  and px_calls[-1][2].get("uri") == "server://m1/com.plexapp.plugins.library/library/metadata/11,12",
                  f"a new Plex playlist made with its tracks: {px_calls[-1]}")
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

        print("audiobookshelf")
        from aiohttp import web as abweb
        ab_calls = []
        ab_state = {"access": "acc1", "refresh": "ref1", "progress": {"B1": {"libraryItemId": "B1", "episodeId": None,
                                                                            "currentTime": 100, "isFinished": False}}}
        ab_files = {"11": b"A" * 1000, "12": b"B" * 1000}

        def ab_book(full=True):
            book = {"id": "B1", "mediaType": "book", "media": {
                "coverPath": "/metadata/items/B1/cover.jpg", "duration": 180,
                "metadata": {"title": "Long Book", "authorName": "Ann Author", "narratorName": "Ned",
                             "authors": [{"id": "AU1", "name": "Ann Author"}]}}}
            if full:
                book["media"]["tracks"] = [
                    {"index": 1, "ino": "11", "startOffset": 0, "duration": 90, "codec": "mp3", "mimeType": "audio/mpeg",
                     "metaTags": {"tagTitle": "Part one"}},
                    {"index": 2, "ino": "12", "startOffset": 90, "duration": 90, "codec": "mp3", "mimeType": "audio/mpeg",
                     "metaTags": {}}]
                book["media"]["chapters"] = [{"start": 0, "end": 90, "title": "Prologue"},
                                             {"start": 90, "end": 120, "title": "Second"},
                                             {"start": 120, "end": 180, "title": "Third"}]
            return book

        ab_pod = {"id": "P1", "mediaType": "podcast", "media": {"coverPath": None, "metadata": {"title": "Cast", "author": "Host"},
                  "episodes": [{"id": "E1", "title": "Older", "publishedAt": 1767225600000, "duration": 60,
                                "audioFile": {"ino": "21", "codec": "wmav2", "duration": 60}},
                               {"id": "E2", "title": "Newer", "publishedAt": 1769904000000, "duration": 60,
                                "audioFile": {"ino": "22", "codec": "mp3", "mimeType": "audio/mpeg", "duration": 60}}]}}

        async def fake_abs(request):
            body = await request.json() if request.can_read_body else None
            ab_calls.append((request.method, request.path, dict(request.query), body))
            if request.path == "/login":
                if body != {"username": "me", "password": "right"} or request.headers.get("x-return-tokens") != "true":
                    return abweb.Response(status=401, text="Unauthorized")
                return abweb.json_response({"user": {"id": "U1", "username": "me", "token": "legacy",
                                                     "accessToken": ab_state["access"], "refreshToken": ab_state["refresh"]}})
            if request.path == "/auth/refresh":
                if request.headers.get("x-refresh-token") != ab_state["refresh"]:
                    return abweb.Response(status=401)
                ab_state["access"], ab_state["refresh"] = "acc2", "ref2"
                return abweb.json_response({"user": {"id": "U1", "accessToken": "acc2", "refreshToken": "ref2"}})
            if request.headers.get("Authorization") not in (f"Bearer {ab_state['access']}", "Bearer apikey"):
                return abweb.Response(status=401, text="Unauthorized")
            if request.path == "/api/me":
                return abweb.json_response({"id": "U1", "username": "me", "mediaProgress": list(ab_state["progress"].values())})
            if request.path == "/api/libraries":
                return abweb.json_response({"libraries": [{"id": "L1", "name": "Books", "mediaType": "book"},
                                                          {"id": "L2", "name": "Shows", "mediaType": "podcast"},
                                                          {"id": "L3", "name": "Comics", "mediaType": "ebook"}]})
            if request.path == "/api/libraries/L1/personalized":
                return abweb.json_response([
                    {"id": "continue-listening", "type": "book", "entities": [ab_book(False)]},
                    {"id": "recently-added", "type": "book", "entities": [ab_book(False)]},
                    {"id": "newest-authors", "type": "authors", "entities": [{"id": "AU1", "name": "Ann Author", "numBooks": 1}]},
                    {"id": "something-new", "type": "book", "entities": [ab_book(False)]}])
            if request.path == "/api/libraries/L2/personalized":
                return abweb.json_response([{"id": "newest-episodes", "type": "episode",
                                             "entities": [{**ab_pod, "recentEpisode": ab_pod["media"]["episodes"][1]}]}])
            if request.path == "/api/items/B1":
                return abweb.json_response(ab_book())
            if request.path == "/api/items/P1":
                return abweb.json_response(ab_pod)
            if request.method == "PATCH" and request.path.startswith("/api/me/progress/"):
                key = request.path[len("/api/me/progress/"):]
                item, _, episode = key.partition("/")
                ab_state["progress"][key] = {"libraryItemId": item, "episodeId": episode or None, **body}
                return abweb.Response(text="OK")
            if request.path in ("/api/items/B1/file/11", "/api/items/B1/file/12"):
                data = ab_files[request.path[-2:]]
                if request.headers.get("Range") == "bytes=0-99":
                    return abweb.Response(status=206, body=data[:100], headers={"Content-Range": f"bytes 0-99/{len(data)}"},
                                          content_type="audio/mpeg")
                return abweb.Response(body=data, content_type="audio/mpeg")
            if request.path == "/api/items/B1/cover":
                return abweb.Response(body=JPEG, content_type="image/jpeg")
            if request.path == "/api/libraries/L1/search":
                return abweb.json_response({"book": [{"libraryItem": ab_book(False)}], "authors": [{"id": "AU1", "name": "Ann Author"}],
                                            "series": [{"series": {"id": "S1", "name": "Saga"}, "books": [ab_book(False)]}]})
            if request.path == "/api/libraries/L2/search":
                return abweb.json_response({"podcast": [], "episodes": [{"libraryItem": {**ab_pod, "recentEpisode": ab_pod["media"]["episodes"][0]}}]})
            if request.path == "/api/libraries/L1/items" and request.query.get("filter") == "series." + base64.b64encode(b"S1").decode():
                in_series = ab_book(False)
                in_series["media"]["metadata"]["series"] = {"id": "S1", "name": "Saga", "sequence": "1"}
                return abweb.json_response({"results": [in_series]})
            return abweb.Response(status=404)
        ab_app = abweb.Application()
        ab_app.router.add_route("*", "/{path:.*}", fake_abs)
        ab_runner = abweb.AppRunner(ab_app)
        await ab_runner.setup()
        ab_site = abweb.TCPSite(ab_runner, "127.0.0.1", 0)
        await ab_site.start()
        ab_url = f"http://127.0.0.1:{ab_site._server.sockets[0].getsockname()[1]}"
        try:
            wrong = await plugin.save_service({"type": "audiobookshelf", "name": "", "config": {"url": ab_url, "username": "me", "password": "nope"}})
            check(not wrong["ok"] and "didn't accept that username and password" in wrong["error"], "a wrong password is refused")
            badkey = await plugin.save_service({"type": "audiobookshelf", "name": "", "config": {"url": ab_url, "password": "nokey"}})
            check(not badkey["ok"] and "API key" in badkey["error"], "a password without a username is an API key, checked")
            ab = (await plugin.save_service({"type": "audiobookshelf", "name": "", "config": {"url": ab_url, "username": "me", "password": "right"}}))["data"]
            aid = ab["id"]
            stored = next(x for x in plugin.settings.services() if x["id"] == aid)["config"]
            check(stored["token"] == "acc1" and stored["refresh_token"] == "ref1" and "password" not in stored
                  and "token" not in ab["config"] and "refresh_token" not in ab["config"],
                  "the password becomes an access and a refresh token, neither shown to the UI")
            again = await plugin.save_service({"type": "audiobookshelf", "name": "", "config": {"url": ab_url, "username": "me", "password": "right"}})
            check(not again["ok"] and "already added" in again["error"], "the same user on the same server can't be added twice")
            home = (await plugin.home(aid))["data"]
            check([s["title"] for s in home] == ["Continue listening", "New episodes · Shows", "Recently added · Books", "Authors · Books", "Libraries"],
                  "home: continue listening first, the server's shelves by name (unknown ones left out), then the libraries")
            check([t["id"] for t in home[0]["tracks"]] == ["f:B1:12"] and home[0]["tracks"][0]["artist"] == "Ann Author · 1 min left",
                  "a book in Continue listening is the file it was left in, with what's left of it")
            check([c["id"] for c in home[-1]["items"]] == ["l:L1", "l:L2"], "ebook-only libraries are left out")
            ab_page = (await plugin.open_collection(aid, "album", "b:B1"))["data"]
            check([(t["id"], t["title"]) for t in ab_page["tracks"]] == [("f:B1:11", "Prologue"), ("f:B1:12", "Second")]
                  and ab_page["subtitle"] == "Ann Author, read by Ned · 3 min" and ab_page["art"] == "i:B1",
                  "a book's files, named after the chapter each starts")
            check(ab_page["tracks"][0]["artist"] == "Ann Author · Played" and ab_page["tracks"][0]["artist_id"] == "a:AU1",
                  "files before where the user is count as played")
            info = (await plugin.stream_info(aid, "f:B1:12"))["data"]
            check(info["start_at"] == 5, "a file resumes where the book was left, a few seconds back")
            check((await plugin.stream_info(aid, "f:B1:11"))["data"]["start_at"] == 0, "a file before that starts at its start")
            await plugin.save_progress(aid, ab_page["tracks"][1], 20.0, 90.0)
            check(ab_state["progress"]["B1"]["currentTime"] == 110 and ab_state["progress"]["B1"]["duration"] == 180
                  and not ab_state["progress"]["B1"]["isFinished"], "progress is saved as the place in the whole book")
            await plugin.save_progress(aid, ab_page["tracks"][1], 85.0, 90.0)
            check(ab_state["progress"]["B1"]["isFinished"], "the end of the last file finishes the book")
            check((await plugin.set_played(aid, ab_page["tracks"][0], False))["ok"] and ab_state["progress"]["B1"]["currentTime"] == 0
                  and not ab_state["progress"]["B1"]["isFinished"], "unplayed: back to that file's start")
            await plugin.set_played(aid, ab_page["tracks"][0], True)
            check(ab_state["progress"]["B1"]["currentTime"] == 90 and not ab_state["progress"]["B1"]["isFinished"],
                  "played: on to the next file, the book not finished")
            chapters = (await plugin.get_lyrics(aid, ab_page["tracks"][1]))["data"]
            check(chapters == {"synced": True, "source": "", "title": "Chapters", "lines": [{"t": 0, "text": "Second"}, {"t": 30, "text": "Third"}]},
                  "a file's chapters, timed from its start, as synced lines")
            check((await plugin.get_lyrics(aid, ab_page["tracks"][0]))["data"] is None, "one chapter in a file: nothing to show, nothing looked up")
            pod = (await plugin.open_collection(aid, "album", "p:P1"))["data"]
            check([t["id"] for t in pod["tracks"]] == ["e:P1:E2", "e:P1:E1"] and pod["subtitle"] == "Host · 2 episodes"
                  and pod["tracks"][0]["album_id"] == "p:P1", "a podcast's episodes, newest first")
            await plugin.save_progress(aid, pod["tracks"][0], 20.0, 60.0)
            check(ab_state["progress"]["P1/E2"]["currentTime"] == 20, "episode progress under the item and episode")
            check(not (await plugin.stream_info(aid, "e:P1:E1"))["ok"], "a format Steam can't play is an error, not silence")
            found = (await plugin.search(aid, "saga"))["data"]
            check([c["id"] for c in found["collections"]] == ["a:AU1", "b:B1", "s:L1:S1"] and [t["id"] for t in found["tracks"]] == ["e:P1:E1"],
                  "search: authors, books and series from every library, and episodes")
            series = (await plugin.open_collection(aid, "album", "s:L1:S1"))["data"]
            check(series["title"] == "Saga" and [c["id"] for c in series["collections"]] == ["b:B1"], "a series, in the server's order")
            base, key = plugin.server.base_url, plugin.server.key
            inst = plugin.instances[aid]
            async with aiohttp.ClientSession() as http:
                # the access token stops working: the next request renews it and saves the new pair
                ab_state["access"] = "acc1"
                inst.items.clear()
                inst.config["token"] = "acc-old"
                await plugin.open_collection(aid, "album", "b:B1")
                stored = next(x for x in plugin.settings.services() if x["id"] == aid)["config"]
                check(stored["token"] == "acc2" and stored["refresh_token"] == "ref2", "a refused token is renewed and the new pair saved")
                plugin.server.forget(aid, "f:B1:11")
                async with http.get(f"{base}/stream/{aid}/f:B1:11?k={key}", headers={"Range": "bytes=0-99"}) as r:
                    check(r.status == 206 and await r.read() == b"A" * 100, "audio through the stream server, with ranges")
                async with http.get(f"{base}/art/{aid}/i:B1?k={key}") as r:
                    check(r.status == 200 and await r.read() == JPEG, "covers through the stream server")
            check(next(c for c in reversed(ab_calls) if c[1] == "/api/items/B1/cover")[2] == {"width": "600"},
                  "covers are asked for at the size shown")
            for bad in ["b:../../api/me", "b:", "x:B1", "s:L1", "l:nope"]:
                check(not (await plugin.open_collection(aid, "album", bad))["ok"], f"refused id {bad!r}")
            for bad in ["f:B1:99", "f:B1/../x:11", "e:P1", "b:B1"]:
                check(not (await plugin.stream_info(aid, bad))["ok"], f"refused track {bad!r}")
            check(not (await plugin.set_played(aid, {"id": "f:B1:99"}, True))["ok"], "marking a file that's gone is an error")
            tabs = await media_center_tabs(plugin, aid)
            check(list(tabs) == ["home", "library", "authors", "series"] and all(isinstance(v, list) for v in tabs.values()),
                  f"Audiobookshelf's media center tabs all open: {tabs}")
            keyed = await plugin.save_service({"type": "audiobookshelf", "name": "Key", "config": {"url": ab_url, "password": "apikey"}})
            check(not keyed["ok"] and "already added" in keyed["error"], "an API key for the same user is the same account")
            await plugin.remove_service(aid)
            keyed = (await plugin.save_service({"type": "audiobookshelf", "name": "Key", "config": {"url": ab_url, "password": "apikey"}}))["data"]
            kstored = next(x for x in plugin.settings.services() if x["id"] == keyed["id"])["config"]
            check(kstored["token"] == "apikey" and not kstored["refresh_token"] and keyed["config"]["username"] == "me",
                  "an API key is kept as the token, with nothing to renew")
            await plugin.remove_service(keyed["id"])
        finally:
            await ab_runner.cleanup()

        print("downloads")
        from aiohttp import web as dlweb
        from musicdeck import offline as offmod
        from musicdeck.services.base import MusicService as BaseService, StreamSource as Source
        dl_calls = []
        dl_audio = bytes(range(256)) * 40  # 10,240 bytes
        dl_init, dl_seg = b"INIT", [b"SEG0", b"SEG1"]

        async def fake_media(request):
            dl_calls.append(request.path)
            if request.path == "/song.mp3":
                spec = request.headers.get("Range", "")[len("bytes="):]
                start, _, end = spec.partition("-")
                start, end = int(start), min(int(end), len(dl_audio) - 1)
                return dlweb.Response(status=206, body=dl_audio[start:end + 1], content_type="audio/mpeg",
                                      headers={"Content-Range": f"bytes {start}-{end}/{len(dl_audio)}"})
            if request.path == "/whole.ogg":  # a server that ignores ranges
                return dlweb.Response(body=b"OGG" * 100, content_type="audio/ogg")
            if request.path == "/fmp4.m3u8":
                return dlweb.Response(text='#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:10,\nseg0.m4s\n#EXTINF:10,\nseg1.m4s\n')
            if request.path == "/master.m3u8":
                return dlweb.Response(text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000\nmedia/mp3.m3u8\n")
            if request.path == "/media/mp3.m3u8":
                return dlweb.Response(text="#EXTM3U\n#EXTINF:10,\na.mp3\n#EXTINF:10,\nb.mp3\n")
            if request.path == "/locked.m3u8":
                return dlweb.Response(text='#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:10,\nx.ts\n')
            if request.path in ("/init.mp4", "/media/a.mp3"):
                return dlweb.Response(body=dl_init if request.path == "/init.mp4" else b"MP3A")
            if request.path in ("/seg0.m4s", "/seg1.m4s"):
                return dlweb.Response(body=dl_seg[int(request.path[4])])
            if request.path == "/media/b.mp3":
                return dlweb.Response(body=b"MP3B")
            if request.path == "/cover.jpg":
                return dlweb.Response(body=JPEG, content_type="image/jpeg")
            return dlweb.Response(status=404)
        dl_app = dlweb.Application()
        dl_app.router.add_route("GET", "/{path:.*}", fake_media)
        dl_runner = dlweb.AppRunner(dl_app)
        await dl_runner.setup()
        dl_site = dlweb.TCPSite(dl_runner, "127.0.0.1", 0)
        await dl_site.start()
        dl_url = f"http://127.0.0.1:{dl_site._server.sockets[0].getsockname()[1]}"

        class FakeSource(BaseService):
            """Tracks of every kind a downloadable source can hand over."""
            type, display_name, downloadable = "fakedl", "Fake", True
            streams = {"file": Source(url=dl_url + "/song.mp3", headers={"X-Key": "secret"}),
                       "whole": Source(url=dl_url + "/whole.ogg"),
                       "fmp4": Source(url=dl_url + "/fmp4.m3u8", hls=True),
                       "mp3hls": Source(url=dl_url + "/master.m3u8", hls=True),
                       "locked": Source(url=dl_url + "/locked.m3u8", hls=True),
                       "dash": Source(playlist="#EXTM3U\n", hls=True)}

            async def stream(self, track_id):
                return self.streams[track_id]

            async def art(self, art_id):
                return Source(url=dl_url + "/cover.jpg")

        class FakeStreamingOnly(FakeSource):
            type, display_name, downloadable = "fakesub", "Fake subscription", False

        real_chunk = offmod.CHUNK
        offmod.CHUNK = 4096  # the file arrives in three ranges
        main.SERVICE_TYPES.update({FakeSource.type: FakeSource, FakeStreamingOnly.type: FakeStreamingOnly})
        try:
            fake = (await plugin.save_service({"type": "fakedl", "name": "", "config": {}}))["data"]
            fid = fake["id"]
            check(fake["downloadable"], "sources say whether they can download")
            tracks = [{"id": i, "title": i.title(), "artist": "", "album": "", "duration": 10, "art": "c1"}
                      for i in ("file", "whole", "fmp4", "mp3hls", "locked", "dash")]
            mix = await plugin.create_playlist(fid, "Mix", tracks)
            dl_page = (await plugin.open_collection(fid, "playlist", mix["id"]))["data"]
            check(dl_page["offline"] == {"on": False, "saved": 0, "total": 6}, "a playlist of a downloadable source offers it")
            check((await plugin.set_playlist_offline(mix["id"], True))["ok"], "marking a playlist for download")
            await plugin.offline.worker
            st = (await plugin.get_state())["offline"]
            check(st["tracks"] == 4 and st["failed"] == 2 and st["pending"] == 0 and st["current"] is None,
                  "plain files, fragmented MP4 and MP3 streams are saved; encrypted and generated streams aren't")
            check(sum(1 for c in dl_calls if c == "/song.mp3") == 3, "large files come in ranges")
            check(len(plugin.offline.art) == 1 and dl_calls.count("/cover.jpg") == 1, "artwork shared by tracks is saved once")
            base, key = plugin.server.base_url, plugin.server.key
            await dl_runner.cleanup()  # the network is gone
            async with aiohttp.ClientSession() as http:
                async def fetch(path):
                    async with http.get(f"{base}{path}?k={key}") as r:
                        return r.status, await r.read(), r.headers.get("Content-Type", "")
                check(await fetch(f"/stream/{fid}/file") == (200, dl_audio, "audio/mpeg"), "a saved file plays without the source")
                check((await fetch(f"/stream/{fid}/fmp4"))[1:] == (dl_init + b"".join(dl_seg), "audio/mp4"),
                      "an HLS stream is saved as one MP4 file, init section first")
                check((await fetch(f"/stream/{fid}/mp3hls"))[1:] == (b"MP3AMP3B", "audio/mpeg"), "a master playlist's media playlist is followed")
                check((await fetch(f"/stream/{fid}/whole"))[1] == b"OGG" * 100, "a server without ranges sends it whole")
                check((await fetch(f"/art/{fid}/c1"))[:2] == (200, JPEG), "artwork shows without the source")
                async with http.get(f"{base}/stream/{fid}/file?k={key}", headers={"Range": "bytes=100-199"}) as r:
                    check(r.status == 206 and await r.read() == dl_audio[100:200], "saved files seek")
            check((await plugin.stream_info(fid, "fmp4"))["data"]["hls"] is False, "a saved HLS track plays as a file")
            check(not any(f.endswith(".part") for f in os.listdir(plugin.offline.dir)), "failed downloads leave nothing behind")
            saved = (await plugin.playlist_offline(fid, mix["id"]))
            check(saved == {"on": True, "saved": 4, "total": 6}, "the dl_page counts what's saved")
            index = json.load(open(os.path.join(plugin.offline.dir, "index.json")))
            check(all("secret" not in json.dumps(v) and "127.0.0.1" not in json.dumps(v) for v in index.values()),
                  "the index keeps file names only, not addresses or headers")

            second = await plugin.create_playlist(fid, "Again", [tracks[0]])
            await plugin.set_playlist_offline(second["id"], True)
            await plugin.offline.worker
            check((await plugin.get_state())["offline"]["tracks"] == 4, "a track in two downloaded playlists is kept once")
            fmp4_file = plugin.offline.tracks[f"{fid} fmp4"]["file"]
            await plugin.playlist_remove(mix["id"], "fmp4")
            check(not os.path.exists(os.path.join(plugin.offline.dir, fmp4_file)) and not plugin.offline.has(fid, "fmp4"),
                  "a track taken out of the playlist is deleted")
            await plugin.set_playlist_offline(mix["id"], False)
            check(plugin.offline.has(fid, "file") and len(plugin.offline.tracks) == 1,
                  "turning a download off keeps what another downloaded playlist holds")
            await plugin.playlist_delete(second["id"])
            check(plugin.offline.tracks == {} and plugin.offline.art == {} and
                  [f for f in os.listdir(plugin.offline.dir) if f != "index.json"] == [],
                  "deleting the last playlist holding them deletes the files and artwork")

            await plugin.set_playlist_offline(mix["id"], True)  # the source is down now
            await plugin.offline.worker
            st = (await plugin.get_state())["offline"]
            check(st["failed"] == offmod.GIVE_UP_AFTER and st["stopped"] == "Couldn't reach the source",
                  "with the source unreachable, downloading stops after a few failures")
            await plugin.remove_downloads()
            check(not plugin.playlists.offline() and (await plugin.get_state())["offline"]["failed"] == 0,
                  "remove all downloads: no playlist is downloaded any more")

            real_free = offmod.MIN_FREE
            offmod.MIN_FREE = 1 << 62
            await plugin.set_playlist_offline(mix["id"], True)
            await plugin.offline.worker
            check((await plugin.get_state())["offline"]["stopped"] == "Not enough free space", "downloads stop before the disk fills")
            offmod.MIN_FREE = real_free
            await plugin.remove_downloads()

            sub = (await plugin.save_service({"type": "fakesub", "name": "", "config": {}}))["data"]
            locked = await plugin.create_playlist(sub["id"], "Subscription mix", tracks[:1])
            check((await plugin.open_collection(sub["id"], "playlist", locked["id"]))["data"]["offline"] is None,
                  "a source that can't download doesn't offer it")
            await plugin.set_playlist_offline(locked["id"], True)
            check(plugin.offline.wanted == {}, "and marking its playlist anyway downloads nothing")
            check(not (await plugin.set_playlist_offline("md:gone", True))["ok"], "a deleted playlist can't be downloaded")
            await plugin.remove_service(sub["id"])
            await plugin.remove_service(fid)
        finally:
            offmod.CHUNK = real_chunk
            main.SERVICE_TYPES.pop("fakedl", None)
            main.SERVICE_TYPES.pop("fakesub", None)
            await dl_runner.cleanup()

        print("download races")
        races = offmod.OfflineStore(os.path.join(tmp, "races"), lambda: asyncio.sleep(0))
        gate = asyncio.Event()

        async def gated_fetch(source, key):
            await gate.wait()
            name = key.replace(" ", "_")
            open(os.path.join(races.dir, name), "wb").write(b"x")
            return {"file": name, "bytes": 1}

        async def resolve_any(instance_id, kind, item):
            return Source(url="http://x/" + item)

        races._fetch = gated_fetch
        races.sync({("s", "a"): {"id": "a", "title": "A"}}, resolve_any)
        await asyncio.sleep(0.05)
        races.sync({}, resolve_any)  # unmarked while "a" downloads
        gate.set()
        await races.worker
        check(races.tracks == {} and os.listdir(races.dir) == [],
              "a track unmarked while it downloads isn't kept")
        # marked in the moment the worker is saying it's finished: still downloaded
        wanted_c = {("s", "c"): {"id": "c", "title": "C"}}

        async def mark_more_as_it_finishes():
            if races.current is None and not races.has("s", "d"):
                races.sync({**wanted_c, ("s", "d"): {"id": "d", "title": "D"}}, resolve_any)
            await asyncio.sleep(0)

        races.on_change = mark_more_as_it_finishes
        races.sync(wanted_c, resolve_any)
        await races.worker
        check(races.has("s", "c") and races.has("s", "d"),
              "a track marked as the downloader finishes is downloaded too")
        await races.stop()

        print("shared tabs")
        from musicdeck.services import base as basemod
        fetched = []

        class Charts(BaseService):
            type = "charts"
            display_name = "Charts"
            TABS = (("home", "Home"), ("charts", "Charts"), ("mine", "Mine"))
            SHARED_TABS = frozenset({"charts"})
            answer = [{"title": "Top", "items": [{"id": "a", "kind": "album", "title": "A"}], "tracks": []}]

            async def tab(self, tab_id):
                fetched.append(tab_id)
                return self.answer

        charts = Charts("chartschartsc", {}, tmp)
        await charts.cached_tab("charts")
        await charts.cached_tab("charts")
        await charts.cached_tab("mine")
        await charts.cached_tab("mine")
        check(fetched == ["charts", "mine", "mine"], "a tab that's the same for everyone is fetched once; the user's each time")
        real_ttl = basemod.TAB_CACHE_SECONDS
        basemod.TAB_CACHE_SECONDS = 0
        await charts.cached_tab("charts")
        basemod.TAB_CACHE_SECONDS = real_ttl
        check(fetched.count("charts") == 2, "and fetched again once it's old")
        fetched.clear()
        empty = Charts("chartsempty0", {}, tmp)
        empty.answer = [{"title": "Top", "items": [], "tracks": []}]
        await empty.cached_tab("charts")
        await empty.cached_tab("charts")
        check(fetched == ["charts", "charts"], "an empty answer isn't kept")

        print("radio's jump back in")

        class Stations(BaseService):
            type = "fakestations"
            display_name = "Stations"
            NOW_PLAYING = "radio"

            async def home(self):
                return []

        main.SERVICE_TYPES["fakestations"] = Stations
        try:
            fs = (await plugin.save_service({"type": "fakestations", "name": "", "config": {}}))["data"]["id"]
            await plugin.played_collection(fs, {"id": "tag:pop", "kind": "list", "title": "pop"})
            for sid_ in ("one", "two", "one"):
                await plugin.played_station(fs, {"id": sid_, "title": f"Station {sid_}", "artist": "Somewhere", "live": True})
            jump = (await plugin.home(fs))["data"][0]
            check(jump["title"] == "Jump back in" and jump["items"] == [] and jump.get("display") == "covers"
                  and [t["id"] for t in jump["tracks"]] == ["one", "two"] and jump["tracks"][0]["live"],
                  f"a radio source's Jump back in: the stations listened to, latest first, not the genre: {jump}")
            await plugin.remove_service(fs)
        finally:
            main.SERVICE_TYPES.pop("fakestations", None)

        print("soulseek")
        import zlib as slzlib
        from musicdeck import slsk
        from musicdeck.slsk import message as slmsg, string as slstr, u32 as slu32, u64 as slu64, u8 as slu8, read_message
        song = bytes(range(256)) * 300  # 76,800 bytes
        shared = [("@@music\\Band - Record\\01 One.flac", len(song)), ("@@music\\Band - Record\\02 Two.flac", len(song)),
                  ("@@music\\Band - Record\\cover.jpg", 10)]
        listen = {}  # our client's listening port, as it tells the server
        tokens = {"next": 76}  # each upload its own transfer token

        def search_response(user, token, files):
            body = slstr(user) + slu32(token) + slu32(len(files))
            for path, size in files:
                body += slu8(1) + slstr(path) + slu64(size) + slstr("flac") + slu32(2) + slu32(4) + slu32(44100) + slu32(5) + slu32(16)
            body += slu8(1) + slu32(2_000_000) + slu32(0) + slu32(0) + slu32(0)
            return slmsg(9, slzlib.compress(body))

        async def peer_session(reader, writer, user):
            """A user sharing `shared`, answering messages as they come: QueueUpload gets a TransferRequest
            with a token of its own; an accepted one, the file over an F connection to us."""
            pending = {}

            async def send_file(token, path):
                fr, fw = await asyncio.open_connection("127.0.0.1", listen["port"])
                fw.write(slmsg(1, slstr(user) + slstr("F") + slu32(0), code_size=1) + slu32(token))
                assert await fr.readexactly(8) == slu64(0)
                fw.write(song)
                await fw.drain()
                await asyncio.sleep(0.05)
                fw.close()
            while True:
                try:
                    code, payload = await read_message(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                r = slsk.Reader(payload)
                if code == 43:
                    path = r.string()
                    tokens["next"] += 1
                    pending[tokens["next"]] = path
                    writer.write(slmsg(40, slu32(1) + slu32(tokens["next"]) + slstr(path) + slu64(dict(shared)[path])))
                elif code == 41:
                    token, allowed = r.u32(), r.bool()
                    if allowed and token in pending:
                        asyncio.ensure_future(send_file(token, pending.pop(token)))

        async def direct_peer(reader, writer):
            code, payload = await read_message(reader, code_size=1)  # our PeerInit
            await peer_session(reader, writer, "alice")
        alice = await asyncio.start_server(direct_peer, "127.0.0.1", 0)
        alice_port = alice.sockets[0].getsockname()[1]
        # bob can't be reached: he only reaches us, asked through the server
        logins = []

        async def slsk_server(reader, writer):
            code, payload = await read_message(reader)
            r = slsk.Reader(payload)
            user, password = r.string(), r.string()
            logins.append(user)
            if password != "right":
                writer.write(slmsg(1, slu8(0) + slstr("INVALIDPASS")))
                return
            writer.write(slmsg(1, slu8(1) + slstr("hi") + slu32(0) + slstr("x") + slu8(0)))
            while True:
                try:
                    code, payload = await read_message(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                r = slsk.Reader(payload)
                if code == 2:
                    listen["port"] = r.u32()
                elif code == 26:
                    token = r.u32()
                    # alice connects to us and answers; bob asks the server to have us connect to him
                    ar, aw = await asyncio.open_connection("127.0.0.1", listen["port"])
                    aw.write(slmsg(1, slstr("alice") + slstr("P") + slu32(0), code_size=1) + search_response("alice", token, shared))
                    await aw.drain()
                    # as a real client does, alice goes on serving the connection she opened
                    asyncio.ensure_future(peer_session(ar, aw, "alice"))

                    async def bob(br, bw):
                        await read_message(br, code_size=1)  # our PierceFireWall
                        bw.write(search_response("bob", token, [("@@b\\Loose\\single.mp3", 500)]))
                        await bw.drain()
                    bob_server = await asyncio.start_server(bob, "127.0.0.1", 0)
                    bob_port = bob_server.sockets[0].getsockname()[1]
                    writer.write(slmsg(18, slstr("bob") + slstr("P") + bytes([1, 0, 0, 127]) + slu32(bob_port) + slu32(555) + slu8(0)))
                elif code == 3:
                    user = r.string()
                    writer.write(slmsg(3, slstr(user) + bytes([1, 0, 0, 127]) + slu32(alice_port if user == "alice" else 0)
                                       + slu32(0) + bytes(2)))
        server = await asyncio.start_server(slsk_server, "127.0.0.1", 0)
        real_server = slsk.SERVER
        slsk.SERVER = ("127.0.0.1", server.sockets[0].getsockname()[1])
        music_dir = os.path.join(tmp, "SlskMusic")
        try:
            wrong = await plugin.save_service({"type": "soulseek", "name": "", "config": {"username": "me", "password": "nope"}})
            check(not wrong["ok"] and "Wrong password" in wrong["error"], f"a wrong password is said at once: {wrong}")
            check(not (await plugin.save_service({"type": "soulseek", "name": "", "config": {"username": "me"}}))["ok"],
                  "a name and a password are needed")
            saved = await plugin.save_service({"type": "soulseek", "name": "", "config": {
                "username": "me", "password": "right", "download_dir": music_dir}})
            check(saved["ok"] and not saved["data"]["has_credentials"] is False, f"signed in: {saved}")
            ss = saved["data"]["id"]
            inst = plugin.instances[ss]
            await inst.connecting
            check(inst.status()["connected"], "connected to the server")
            import musicdeck.services.soulseek as ssmod
            real_wait, ssmod.SEARCH_WAIT = ssmod.SEARCH_WAIT, 0.6
            found = (await plugin.search(ss, "band record"))["data"]
            ssmod.SEARCH_WAIT = real_wait
            check([c["title"] for c in found["collections"]] == ["Band - Record"]
                  and "alice" in found["collections"][0]["subtitle"] and "FLAC 16/44.1" in found["collections"][0]["subtitle"]
                  and sorted(t["title"] for t in found["tracks"]) == ["01 One", "02 Two", "single"],
                  f"results from a user who reached us and one we reached through the server; pictures left out: {found}")
            album = (await plugin.open_collection(ss, "album", found["collections"][0]["id"]))["data"]
            check(album["action"]["label"] == "Download album" and len(album["tracks"]) == 2, "an album, with Download album")
            one = next(t for t in found["tracks"] if t["title"] == "01 One")
            src = await inst.stream(one["id"])
            check(open(src.path, "rb").read() == song, "playing a track downloads it first")
            msg = await plugin.collection_action(ss, "album", album_id := found["collections"][0]["id"], "download")
            check(msg["ok"], f"downloading the album: {msg}")
            for _ in range(100):
                if inst.album_jobs[album_id].get("done"):
                    break
                await asyncio.sleep(0.05)
            target = os.path.join(music_dir, "Band - Record")
            check(sorted(os.listdir(target)) == ["01 One.flac", "02 Two.flac"]
                  and open(os.path.join(target, "02 Two.flac"), "rb").read() == song,
                  "into a folder of its own in the chosen music folder")
            home = (await plugin.home(ss))["data"]
            downloaded = next(sec for sec in home if sec["title"] == "Downloaded")["items"][0]
            folder = (await plugin.open_collection(ss, "folder", downloaded["id"]))["data"]
            check(len(folder["tracks"]) == 2 and (await inst.stream(folder["tracks"][0]["id"])).path.startswith(target),
                  "Home lists it, and plays it from the folder")
            from musicdeck.services.soulseek import _safe
            check(_safe("..") == "untitled" and "/" not in _safe("a/../b") and "\\" not in _safe("a\\b"),
                  "names from other people's shares can't climb out of the folder")
            bad = slsk.Reader(b"\x05\x00\x00\x00ab")
            try:
                bad.string()
                check(False, "a short message is refused")
            except slsk.SoulseekError:
                check(True, "a message cut short is refused, not read past")
            await plugin.remove_service(ss)
        finally:
            slsk.SERVER = real_server
            server.close()
            alice.close()

        print("overlapping restarts")
        started, stopped = [], []

        class Slow(BaseService):
            type = "slowstart"
            display_name = "Slow"

            async def start(self):
                started.append(self)
                await asyncio.sleep(0.05)

            async def stop(self):
                stopped.append(self)

        main.SERVICE_TYPES["slowstart"] = Slow
        try:
            slow = {"id": "slowslowslow", "type": "slowstart", "name": "Slow", "config": {}}
            await asyncio.gather(plugin._start_instance(slow), plugin._start_instance(slow))
            check(len(started) == 2 and stopped == [started[0]] and plugin.instances[slow["id"]] is started[1],
                  "two restarts at once leave one instance running, the other stopped")
            plugin.instances.pop(slow["id"])
        finally:
            main.SERVICE_TYPES.pop("slowstart", None)

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
        async def song_search(request):
            # Apple's catalogue: the song on air, and one only like it
            return aioweb.json_response({"results": [
                {"trackName": "Song (Live)", "artistName": "Artist", "artworkUrl100": "https://x/live/100x100bb.jpg"},
                {"trackName": "Song", "artistName": "Artist", "artworkUrl100": "https://x/song/100x100bb.jpg"},
            ] if "Artist Song" == request.query.get("term") else []})
        icy_app = aioweb.Application()
        icy_app.router.add_get("/stream", icy)
        icy_app.router.add_get("/search", song_search)
        icy_runner = aioweb.AppRunner(icy_app)
        await icy_runner.setup()
        icy_site = aioweb.TCPSite(icy_runner, "127.0.0.1", 0)
        await icy_site.start()
        icy_port = icy_site._server.sockets[0].getsockname()[1]
        await rs.start()
        from musicdeck.services import radio as radiomod
        real_itunes, radiomod.ITUNES = radiomod.ITUNES, f"http://127.0.0.1:{icy_port}"
        rs.stations.put("s1", {"stationuuid": "s1", "url_resolved": f"http://127.0.0.1:{icy_port}/stream"})
        check(await rs.on_air("s1") == {"text": "Artist – Song", "title": "Song", "artist": "Artist",
                                        "art": "https://x/song/600x600bb.jpg"},
              "song on air read from ICY metadata, its cover found for exactly that song")
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
        from musicdeck.services.radio import believable
        station = "50s 60s RETRO HITS"
        check(not believable({"title": "PLAY_8_NEW", "artist": station}, station)
              and not believable({"title": "Advertisement", "artist": ""}, station)
              and not believable({"title": "Listen", "artist": "www.station.example"}, station)
              and not believable({"title": "ID1234", "artist": "Promo"}, station),
              "a station's own name, playout codes, adverts and web addresses aren't songs")
        check(believable({"title": "Hey Jude", "artist": "The Beatles"}, station)
              and believable({"title": "1999", "artist": "Prince"}, station),
              "real songs are, number titles included")
        from musicdeck.services.radio import tidy_on_air
        padded = tidy_on_air(parse_on_air("Theme from Joy House by Jimmy Smith - Adroit Jazz Underground on walmradio.com"),
                             "Adroit Jazz Underground")
        check(padded and padded["title"] == "Theme from Joy House" and padded["artist"] == "Jimmy Smith",
              f"the station's half of a title goes, and '<song> by <artist>' is read as such: {padded}")
        check(tidy_on_air(parse_on_air("Radio Bob - Rockt!"), "Radio Bob") is None
              and tidy_on_air(parse_on_air("50s 60s RETRO HITS - PLAY_8_NEW"), station) is None
              and tidy_on_air(parse_on_air("Will.i.am - Scream"), "Hits FM")["artist"] == "Will.i.am",
              "a station's slogan or code isn't a song; a dotted name is")
        check(parse_on_air("NY's #1 Hit Music Station - text=\"Z100\" song_spot=\"T\"") is None,
              "station idents and ads aren't songs")
        check(parse_on_air('title="Super Bass",artist="NICKI MINAJ",url="song_spot="M" MediaBaseId="0"')["text"]
              == "NICKI MINAJ – Super Bass", "title= and artist= tags")
        check(parse_on_air('title="Z100 Preset 4",artist="",url="song_spot="F" MediaBaseId="0"') is None,
              "a jingle behind an unclosed url= tag isn't a song")
        rs.stations.put("s3", {"stationuuid": "s3", "url_resolved": f"http://127.0.0.1:{icy_port}/stream?enc=latin1"})
        on_s3 = await rs.on_air("s3")
        check(on_s3["artist"] == "Brüllová" and on_s3["art"] is None, "Latin-1 titles read as Latin-1; no cover found, none")
        radiomod.ITUNES = real_itunes
        rs.stations.put("s2", {"stationuuid": "s2", "url_resolved": "https://x/live.m3u8", "hls": 1})
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

        print("last.fm")
        from musicdeck import lastfm as fmmod
        fm = {"approved": False, "up": True, "session_ok": True, "scrobbles": [], "now": []}

        async def fm_api(request):
            params = dict(request.query) if request.method == "GET" else dict(await request.post())
            signed = {k: v for k, v in params.items() if k not in ("api_sig", "format")}
            if params.get("api_key") != "KEY":
                return lbweb.json_response({"error": 10, "message": "Invalid API key"})
            if params.get("api_sig") != fmmod.sign(signed, "SECRET"):
                return lbweb.json_response({"error": 13, "message": "Invalid method signature"})
            method = params["method"]
            if method == "auth.getToken":
                return lbweb.json_response({"token": "TOKEN"})
            if method == "auth.getSession":
                if not fm["approved"]:
                    return lbweb.json_response({"error": 14, "message": "Unauthorized Token"})
                return lbweb.json_response({"session": {"name": "fmuser", "key": "SK", "subscriber": 0}})
            if not fm["up"]:
                return lbweb.json_response({"error": 16, "message": "Temporarily unavailable"})
            if params.get("sk") != "SK" or not fm["session_ok"]:
                return lbweb.json_response({"error": 9, "message": "Invalid session key"})
            (fm["scrobbles"] if method == "track.scrobble" else fm["now"]).append(params)
            return lbweb.json_response({"ok": True})
        fm_app = lbweb.Application()
        fm_app.router.add_route("*", "/2.0/", fm_api)
        fm_runner = lbweb.AppRunner(fm_app)
        await fm_runner.setup()
        fm_site = lbweb.TCPSite(fm_runner, "127.0.0.1", 0)
        await fm_site.start()
        fmmod.API = f"http://127.0.0.1:{fm_site._server.sockets[0].getsockname()[1]}/2.0/"

        check(fmmod.sign({"b": "2", "a": "1", "format": "json"}, "s") == hashlib.md5(b"a1b2s").hexdigest(),
              "signatures: name+value in name order, then the secret, leaving out format")
        check(not (await plugin.lastfm_start("", ""))["ok"], "linking needs an API account")
        bad_key = await plugin.lastfm_start("NOPE", "SECRET")
        bad_secret = await plugin.lastfm_start("KEY", "WRONG")
        check(not bad_key["ok"] and "API key" in bad_key["error"] and not bad_secret["ok"] and "secret" in bad_secret["error"],
              "a wrong key or secret is said plainly")
        started = await plugin.lastfm_start(" KEY ", " SECRET ")
        check(started["ok"] and started["data"]["url"] == "https://www.last.fm/api/auth/?api_key=KEY&token=TOKEN",
              "linking opens Last.fm's approval page for the token")
        check((await plugin.lastfm_poll())["status"] == "waiting", "waits while the user hasn't approved")
        fm["approved"] = True
        done = await plugin.lastfm_poll()
        state = await plugin.get_state()
        check(done == {"status": "done", "user": "fmuser"} and state["lastfm"] == {"user": "fmuser", "has_key": True, "pending": 0}
              and "SK" not in json.dumps(state) and "SECRET" not in json.dumps(state),
              "linked once approved; the secret and session key stay in the backend")
        account = plugin.settings.get("lastfm")
        await plugin.lastfm.now_playing(account, song)
        check(fm["now"][-1]["track"] == "Song" and fm["now"][-1]["duration"] == "200", "now playing sent")
        await plugin.lastfm.scrobble(account, {**song, "title": "Jingle", "duration": 12})
        await plugin.lastfm.scrobble(account, {**song, "live": True})
        check(not fm["scrobbles"], "stations and tracks under 30 seconds aren't scrobbled")
        fm["up"] = False
        await plugin.lastfm.scrobble(account, song, timestamp=1000)
        check(plugin.lastfm.pending_count() == 1, "a play made while Last.fm is down is kept")
        fm["up"] = True
        await plugin.lastfm.scrobble(account, {**song, "title": "Next"}, timestamp=2000)
        sent = fm["scrobbles"][-1]
        check(plugin.lastfm.pending_count() == 0 and sent["track[0]"] == "Song" and sent["timestamp[0]"] == "1000"
              and sent["track[1]"] == "Next" and sent["album[1]"] == "Record", "kept plays go with the next, numbered")
        await plugin.scrobble(sid, "fm1", True, {**song, "id": "fm1", "title": "Other"})
        await asyncio.sleep(0.3)
        check(fm["scrobbles"][-1]["track[0]"] == "Other", "plays from any source reach Last.fm")
        fm["session_ok"] = False
        await plugin.scrobble(sid, "fm2", True, {**song, "id": "fm2", "title": "Revoked"})
        await asyncio.sleep(0.3)
        state = await plugin.get_state()
        check(state["lastfm"]["user"] == "" and state["lastfm"]["has_key"] and plugin.lastfm.pending_count() == 1,
              "a revoked session unlinks, keeping the API account and the play for the next link")
        await fm_runner.cleanup()

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
        check(later["action"] == {"label": "Install", "url": "steam://install/400"} and later["subtitle"] == "Not installed"
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
            if request.path == "/category.json":
                # the older iTunes chart feed, the only one with categories
                return lrweb.json_response({"feed": {"entry": [
                    {"id": {"attributes": {"im:id": "999"}}, "im:name": {"label": "Crime Show"},
                     "im:artist": {"label": "Network"}, "im:image": [{"label": "https://x/55x55bb.png"},
                                                                     {"label": "https://x/170x170bb.png"}]},
                    {"id": {}, "im:name": {"label": "No id, left out"}}]}})
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
        real_category = podmod.CATEGORY_CHART
        podmod.CATEGORY_CHART = base_url + "/category.json?country={country}&genre={genre}"
        try:
            pod = (await plugin.save_service({"type": "podcasts", "name": "", "config": {"country": "gb"}}))["data"]
            pid = pod["id"]
            check(pod["name"] == "Podcasts" and pod["config"]["country"] == "GB" and pod["features"] == ["browse", "progress"],
                  "add the Podcasts source")
            browse = (await plugin.browse(pid))["data"]
            check(browse[0]["title"] == "Categories" and {"id": "genre:1488", "kind": "list", "title": "True Crime",
                                                          "subtitle": "", "art": None} in browse[0]["items"],
                  "browse: Apple's podcast categories")
            crime = (await plugin.open_collection(pid, "list", "genre:1488"))["data"]
            check(crime["title"] == "True Crime" and [c["id"] for c in crime["collections"]] == ["a:999"]
                  and crime["collections"][0]["art"] == "https://x/600x600bb.png", "a category: its top shows, big artwork")
            check(not (await plugin.open_collection(pid, "list", "genre:../x"))["ok"], "a made-up category is refused")
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
            podmod.CATEGORY_CHART = real_category
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
            ("add_to_playlist", plugin.add_to_playlist(gone, "p", ["1"])),
            ("create_service_playlist", plugin.create_service_playlist(gone, "x", ["1"])), ("lyrics", plugin.get_lyrics(gone, track)),
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
        # (imports guarded inside functions are fine)
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

        print("source types")
        from musicdeck.services import CATALOGUE, SERVICE_TYPES as TYPES
        check(all(TYPES.get(t).type == t and TYPES.get(t).display_name == name for t, _, _, name, _ in CATALOGUE),
              "every catalogued type loads, under its own type and name")
        check(TYPES.get("nonsense") is None and "nonsense" not in TYPES, "unknown types are None")

        print("youtube worker")
        from musicdeck.services import youtube as ytmod
        fake_worker = os.path.join(tmp, "fake_worker.py")
        with open(fake_worker, "w", encoding="utf-8") as f:
            f.write(
                "import json, os, sys\n"
                "print(json.dumps({'id': None, 'ready': True}), flush=True)\n"
                "for line in sys.stdin:\n"
                "    req = json.loads(line)\n"
                "    if req['op'] == 'music':\n"
                "        out = {'id': req['id'], 'result': {'method': req['method'], 'args': req['args'], 'pid': os.getpid()}}\n"
                "    else:\n"
                "        out = {'id': req['id'], 'error': 'ValueError: no such op'}\n"
                "    print(json.dumps(out), flush=True)\n")
        real = (ytmod.WORKER_SCRIPT, ytmod.IDLE_STOP, ytmod.PY_MODULES, os.environ.get("MUSICDECK_PYTHON"))
        ytmod.WORKER_SCRIPT, ytmod.IDLE_STOP = fake_worker, 0.3
        os.environ["MUSICDECK_PYTHON"] = sys.executable
        bundled = os.path.join(tmp, "bundled")
        os.makedirs(os.path.join(bundled, "yt_dlp"), exist_ok=True)
        with open(os.path.join(bundled, "yt_dlp", "version.py"), "w") as f:
            f.write("# Autogenerated\n__version__ = '2099.01.02'\nRELEASE_GIT_HEAD = 'x'\n")
        ytmod.PY_MODULES = bundled
        worker = ytmod.YouTubeWorker(os.path.join(tmp, "runtime"))
        try:
            check(worker.version == "2099.01.02" and worker.proc is None, "the yt-dlp version is read without starting the worker")
            first = await worker.call("music", method="search", args=["x"], kwargs={})
            check(first["method"] == "search" and first["args"] == ["x"], "YouTube Music calls go to the worker")
            together = await asyncio.gather(*[worker.call("music", method=m, args=[], kwargs={}) for m in ("get_home", "get_album")])
            check([r["method"] for r in together] == ["get_home", "get_album"] and together[0]["pid"] == first["pid"],
                  "calls at once share one worker, each answer to its own call")
            try:
                await worker.call("nonsense")
                failed = False
            except ytmod.ServiceError as e:
                failed = "no such op" in str(e)
            check(failed, "a worker's error comes back as a message")
            await asyncio.sleep(0.6)
            check(worker.proc is None or worker.proc.returncode is not None, "an idle worker stops, freeing its memory")
            again = await worker.call("music", method="search", args=[], kwargs={})
            check(again["pid"] != first["pid"], "and starts again when next needed")
        finally:
            await worker.stop()
            ytmod.WORKER_SCRIPT, ytmod.IDLE_STOP, ytmod.PY_MODULES = real[:3]
            if real[3] is None:
                os.environ.pop("MUSICDECK_PYTHON", None)
            else:
                os.environ["MUSICDECK_PYTHON"] = real[3]

        print("redaction")
        from musicdeck.debuglog import redact
        check(redact("http://nd:4533/rest/stream?u=me&t=abc&s=salt") == "http://nd:4533/rest/stream?…",
              "addresses lose their query (Subsonic tokens, signed links, the stream key)")
        check("tok123" not in redact('Token="tok123"') and "eyJhbGciOiJIUzI1NiJ9" not in redact("Bearer eyJhbGciOiJIUzI1NiJ9.x.y"),
              "header-style credentials are masked")
        check(redact("Played the Token Ring song") == "Played the Token Ring song" and redact("Bearer of bad news") == "Bearer of bad news",
              "ordinary words that look like credential names are left alone")
        check(redact({"token": "x", "n": ["https://a/b?k=1"]}) == {"token": "…", "n": ["https://a/b?…"]},
              "inside dicts and lists too")
        plugin.settings.set("debug_log", True)
        from musicdeck import debuglog as dl
        dl.configure(decky.DECKY_PLUGIN_LOG_DIR, True)
        dl.debug("proxy", "upstream error", url="http://h/rest/stream?t=SECRETTOKEN", headers={"X-Plex-Token": "PLEXSECRET"})
        await plugin.log_events([{"t": 1, "event": "audio src", "data": {"src": "http://127.0.0.1:1/stream/a/b?k=STREAMKEY"}}])
        dl.configure(decky.DECKY_PLUGIN_LOG_DIR, False)
        plugin.settings.set("debug_log", False)
        written = open(dl.path, encoding="utf-8").read()
        check(not any(x in written for x in ("SECRETTOKEN", "PLEXSECRET", "STREAMKEY")), "nothing secret reaches the debug log")
        failing = await main._result(_raise(ServiceErrorForTest("Could not reach http://h/x?t=SECRET")))
        check("SECRET" not in failing["error"], "nor an error message the UI shows")

        print("download limits")
        import io
        from musicdeck import offline as offlim
        real_limits = (offlim.MAX_FILE, offlim.MIN_FREE, offlim.CHUNK)
        try:
            offlim.MAX_FILE, offlim.CHUNK = 10, 4
            sink = offlim._Sink(io.BytesIO(), tmp)
            sink.write(b"12345")
            try:
                sink.write(b"678901")
                stopped = ""
            except ServiceErrorForTest as e:
                stopped = str(e)
            check("more than a song" in stopped, "a source sending endless data is cut off")
            offlim.MAX_FILE, offlim.MIN_FREE = 1 << 40, 1 << 62
            sink = offlim._Sink(io.BytesIO(), tmp)
            try:
                sink.write(b"x" * 8)
                stopped = ""
            except ServiceErrorForTest as e:
                stopped = str(e)
            check(stopped == "Not enough free space", "a download stops when the disk is filling up, not just between songs")
        finally:
            offlim.MAX_FILE, offlim.MIN_FREE, offlim.CHUNK = real_limits

        print("private folders")
        if os.name == "posix":
            check(all(os.stat(d).st_mode & 0o077 == 0 for d in (decky.DECKY_PLUGIN_SETTINGS_DIR, decky.DECKY_PLUGIN_RUNTIME_DIR,
                                                               decky.DECKY_PLUGIN_LOG_DIR)),
                  "settings, data and logs are readable by this user only")
        check(any(isinstance(f, main.debuglog.Redacting) for f in decky.logger.filters), "the main log is redacted")

        print("cache")
        from musicdeck.cache import Cache
        c = Cache(ttl=60, size=3)
        for k in "abc":
            c.put(k, k.upper())
        c.get("a")  # now the most recently used
        c.put("d", "D")
        check("b" not in c and c.get("a") == "A" and len(c) == 3, "past its size, the least recently used goes")
        c.put("none", None)
        check("none" in c and c.get("none", "missing") is None, "None is a value, told apart from missing")
        c.put("short", 1, ttl=-1)
        check("short" not in c and c.get("short") is None, "an entry past its own lifetime is gone")
        c.drop(lambda k: k in ("a", "d"))
        check("a" not in c and "d" not in c, "dropping what a change made stale")

        print("backups")
        sub_id = saved["data"]["id"]
        mix = await plugin.create_playlist(sub_id, "Road trip", [{"id": "s1", "title": "One", "artist": "A"}])
        picture = os.path.join(tmp, "cover.png")
        with open(picture, "wb") as f:
            f.write(b"\x89PNG fake")
        await plugin.playlist_set_art(mix["id"], picture)
        await plugin.set_listenbrainz("")
        exported = await plugin.export_backup()
        check(exported["ok"] and exported["data"].startswith(tmp) and exported["data"].endswith(".json"),
              "a backup is written to the user's folder")
        text = open(exported["data"], encoding="utf-8").read()
        stored = next(s for s in plugin.settings.services() if s["id"] == sub_id)["config"]
        check(stored["token"] not in text and stored["salt"] not in text and '"token"' not in text,
              "no sign-in reaches the backup")
        if os.name == "posix":
            check(os.stat(exported["data"]).st_mode & 0o077 == 0, "only its owner can read it")
        data = json.loads(text)
        check(data["playlist_art"][mix["id"][3:]]["ext"] == ".png" and any(s["id"] == sub_id and s["signed_in"] for s in data["sources"]),
              "playlist pictures go in it, and which sources were signed in")
        # this Deck loses the source (and with it the playlist); the backup brings both back
        volume_before = plugin.settings.get("volume")
        await plugin.remove_service(sub_id)
        restored = await plugin.import_backup(exported["data"])
        check(restored["ok"] and "1 playlist" in restored["data"] and "sign in again to Home" in restored["data"],
              f"restoring says what came back and what needs signing in: {restored.get('data')}")
        back = next(s for s in plugin.settings.services() if s["id"] == sub_id)
        check(back["config"]["url"] == "http://127.0.0.1:9" and "token" not in back["config"] and back["config"]["sign_in_again"],
              "a source with an account comes back signed out, marked as such")
        state = await plugin.get_state()
        check(next(s for s in state["services"] if s["id"] == sub_id)["config"]["sign_in_again"], "the UI can say so")
        page = (await plugin.open_collection(sub_id, "playlist", mix["id"]))["data"]
        check(page["title"] == "Road trip" and [t["id"] for t in page["tracks"]] == ["s1"]
              and plugin.playlists.art_path(page["art"]) and open(plugin.playlists.art_path(page["art"]), "rb").read() == b"\x89PNG fake",
              "the playlist and its picture are back")
        check(plugin.settings.get("volume") == volume_before, "preferences on a Deck already set up stay as they are")
        again = await plugin.import_backup(exported["data"])
        check(again["ok"] and again["data"].startswith("0 sources and 0 playlists")
              and sum(1 for s in plugin.settings.services() if s["id"] == sub_id) == 1, "restoring twice adds nothing")
        await plugin.save_service({"id": sub_id, "type": "subsonic", "name": "Home",
                                              "config": {"url": "http://127.0.0.1:9", "username": "me", "password": "pw"}})
        check("sign_in_again" not in next(s for s in plugin.settings.services() if s["id"] == sub_id)["config"],
              "signing in again clears the mark")

        def write_backup(name, content):
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content if isinstance(content, str) else json.dumps(content))
            return path

        check(not (await plugin.import_backup(write_backup("x.json", "not json")))["ok"], "a file that isn't JSON is refused")
        check(not (await plugin.import_backup(write_backup("y.json", {"hello": 1})))["ok"], "JSON that isn't a backup is refused")
        check(not (await plugin.import_backup(write_backup("z.json", {"musicdeck_backup": 1, "sources": {}})))["ok"],
              "a damaged backup is refused whole")
        check(not (await plugin.import_backup(os.path.join(tmp, "missing.json")))["ok"], "a missing file is an error")
        hostile = {"musicdeck_backup": 1, "settings": {"eq": {"gains": "loud"}, "volume": 7},
                   "sources": [{"id": "../../etc", "type": "local", "name": "x", "config": {}},
                               {"id": "aaaaaaaaaaaa", "type": "nonsense", "name": "x", "config": {}},
                               {"id": "bbbbbbbbbbbb", "type": "radio", "name": "Radio again", "config": {"token": "leak", "country": "NL"}}],
                   "playlists": [{"id": "cccccccccccc", "instance_id": "nowhere", "name": "Orphan", "tracks": []},
                                 {"id": "../escape", "instance_id": "bbbbbbbbbbbb", "name": "Bad id", "tracks": []},
                                 {"id": "dddddddddddd", "instance_id": "bbbbbbbbbbbb", "name": "Kept", "tracks": [{"id": "t"}, "junk"]},
                                 {"id": "eeeeeeeeeeee", "instance_id": "bbbbbbbbbbbb", "name": "Script art", "tracks": [{"id": "t"}]}],
                   "playlist_art": {"eeeeeeeeeeee": {"ext": ".sh", "data": base64.b64encode(b"#!/bin/sh").decode()}},
                   "files": {"../../evil.json": {"x": 1}, "radio-bbbbbbbbbbbb-favourites.json": {"x": 1},
                             "recents.json": {"collections": {}}}}
        result = await plugin.import_backup(write_backup("hostile.json", hostile))
        check(result["ok"] and result["data"].startswith("1 source and 2 playlists"),
              f"from a hostile backup only the valid source and playlists are taken: {result.get('data')}")
        radio = next(s for s in plugin.settings.services() if s["id"] == "bbbbbbbbbbbb")
        check("token" not in radio["config"] and "sign_in_again" not in radio["config"], "secrets in a backup are dropped, never stored")
        check(not os.path.exists(os.path.join(tmp, "evil.json")) and not os.path.exists(os.path.join(tmp, "runtime", "radio-bbbbbbbbbbbb-favourites.json")),
              "only MusicDeck's own data files are restored, inside its folder")
        ids = {p["id"] for p in plugin.playlists.playlists}
        check("cccccccccccc" not in ids and not any("escape" in i for i in ids)
              and [t["id"] for t in plugin.playlists.find("dddddddddddd")["tracks"]] == ["t"],
              "playlists of sources that aren't here, odd ids and broken tracks are left out")
        check(not any(f.endswith(".sh") for f in os.listdir(plugin.playlists.art_dir)), "a picture that isn't an image isn't written")
        check(plugin.settings.get("volume") == volume_before and isinstance(plugin.settings.get("eq")["gains"], list),
              "invalid preferences are ignored")
        await plugin.remove_service("bbbbbbbbbbbb")
    finally:
        await plugin._unload()
    print("all backend checks passed")


asyncio.run(run())
