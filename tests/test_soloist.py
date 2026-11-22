"""
Spotify through Spotify Soloist, without Soloist: its WebSocket faked from what the real one sends (recorded
2026-11-20 with Soloist 1.4.0.3), its downloads from a local server, and the Spotify source's switch-over.

    python tests/test_soloist.py      (needs aiohttp + certifi installed)

The real thing: out/winbuild/soloist_live.py, against a Soloist that's been paired.
"""
import asyncio
import datetime
import io
import json
import os
import sys
import tarfile
import tempfile
import time
import types
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "py_modules")]

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

from musicdeck import native, soloist  # noqa: E402
from musicdeck.services import ServiceError  # noqa: E402
from musicdeck.services.spotify import SOLOIST_KEY, SpotifyService  # noqa: E402

A, B, AUTOPLAY = "spotify:track:4uLU6hMCjMI75M1A2tKUQC", "spotify:track:7GhIk7Il098yCjg4BQjzvb", "spotify:track:05wIrZSwuaVWhcv5FfqeH0"


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("  ok -", message)


async def until(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


# --- builds: dates, staleness ---

def build_tests():
    print("builds")
    v = "soloist 1.4.0.3 build 1795197722 (20261120) (gcedbef4d51) (linux/x86_64)"
    check(soloist.build_date(v) == datetime.date(2026, 11, 20), "the build's date is read from --version")
    check(soloist.build_date("soloist 2.0") is None, "no date: none")
    check(not soloist._stale({"version": v}, datetime.date(2026, 12, 1)), "a fresh build isn't fetched again")
    check(soloist._stale({"version": v}, datetime.date(2027, 2, 3)), "75 days on, a new one is fetched (90 is the end)")
    check(soloist._stale({"version": "soloist ?", "fetched": time.time() - 80 * 86400}), "no date: by when it was fetched")
    check(soloist.archive_name("linux-x86_64") == "soloist_release_x86_64.tar.gz", "the Deck's archive")
    check(soloist.archive_name("linux-arm64") == "soloist_release_arm64.tar.gz", "arm64 Linux's")
    check(soloist.archive_name("windows-x86_64").endswith(".zip"), "Windows' is a zip")
    check(soloist.archive_name("macos-arm64") is None, "nothing for systems MusicDeck doesn't run on")


# --- install, from a local "Spotify" ---

def _archives(exe_name: str) -> dict[str, bytes]:
    program = b"#!/bin/sh\necho fake\n"
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w:gz") as t:
        for name, data in (("CHANGELOG.md", b"notes"), ("../evil", b"x"), (exe_name, program)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("CHANGELOG.md", "notes")
        zf.writestr(exe_name, program)
    return {"tar": tar.getvalue(), "zip": z.getvalue()}


async def install_tests(http):
    print("install")
    exe_name = os.path.basename(soloist.executable("x"))
    archives = _archives(exe_name)
    name = soloist.archive_name()
    served = {"count": 0, "broken": False, "status": 200}

    async def build(request):
        served["count"] += 1
        if served["status"] != 200:
            return web.Response(status=served["status"])
        if request.match_info["name"] != name:
            return web.Response(status=404)
        body = archives["zip" if name.endswith(".zip") else "tar"]
        return web.Response(body=body[:40] if served["broken"] else body)

    app = web.Application()
    app.router.add_get("/{name}", build)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    builds = f"http://127.0.0.1:{port}/"
    real_version = soloist._version
    version = {"text": f"soloist 1.4.0.3 build 1 ({datetime.date.today():%Y%m%d}) (g) (test)"}

    async def fake_version(exe):
        return version["text"]
    soloist._version = fake_version
    try:
        runtime = tempfile.mkdtemp()
        exe = await soloist.install(runtime, http, builds=builds)
        check(os.path.isfile(exe) and open(exe, "rb").read().startswith(b"#!"), "the program is unpacked from Spotify's archive")
        check(sorted(os.listdir(os.path.dirname(exe))) == sorted([exe_name, "build.json"]),
              "only the program (nothing else in the archive, no leftovers)")
        check(not os.path.exists(os.path.join(runtime, "soloist", "evil")), "paths in the archive go nowhere")
        if not native.WINDOWS:
            check(os.access(exe, os.X_OK), "it's made executable")
        count = served["count"]
        await soloist.install(runtime, http, builds=builds)
        check(served["count"] == count, "a fresh copy isn't fetched again")

        # getting old: fetched again; and if that fails, the old one is kept
        version["text"] = "soloist 1.4.0.3 build 1 (20200101) (g) (test)"
        with open(os.path.join(runtime, "soloist", "bin", "build.json"), "w") as f:
            json.dump({"version": version["text"], "fetched": 0}, f)
        served["status"] = 503
        exe2 = await soloist.install(runtime, http, builds=builds)
        check(exe2 == exe and os.path.isfile(exe), "an old copy is kept when the new one can't be fetched")
        try:
            await soloist.install(runtime, http, force=True, builds=builds)
            check(False, "a copy that's run out isn't kept")
        except ServiceError as e:
            check("503" in str(e), f"a copy that's run out needs the new one: {e}")
        served["status"] = 200
        served["broken"] = True
        try:
            await soloist.install(tempfile.mkdtemp(), http, builds=builds)
            check(False, "a damaged download is refused")
        except ServiceError as e:
            check("damaged" in str(e), f"a damaged download is refused: {e}")
        served["broken"] = False
        version["text"] = "something else entirely"
        try:
            await soloist.install(tempfile.mkdtemp(), http, builds=builds)
            check(False, "a program that isn't Soloist is refused")
        except ServiceError as e:
            check("isn't Soloist" in str(e), "a program that isn't Soloist is refused")
    finally:
        soloist._version = real_version
        await runner.cleanup()


# --- the WebSocket: a fake Soloist ---

class FakeSoloist:
    """Answers as Soloist 1.4.0.3 did: commands -> command_result, then its events."""

    def __init__(self):
        self.commands: list[dict] = []
        self.ws = None
        self.current = None
        self.status = "idle"
        self.position = 0
        self.logged_in = True
        self.volume = 40

    def pos(self, ms, speed):
        return {"position_ms": ms, "timestamp_ms": time.time() * 1000, "speed": speed}

    async def send(self, **m):
        await self.ws.send_json(m)

    def state(self):
        return {"type": "playback_state", "status": self.status, "item": {"uri": self.current or "", "entity_type": "track",
                "decorations": {}}, "position": self.pos(self.position, 1 if self.status == "playing" else 0),
                "volume": self.volume, "is_active": True}

    async def handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws = ws
        await self.send(type="auth_state", logged_in=self.logged_in, is_active=True, device_name="MusicDeck (test)")
        async for msg in ws:
            m = json.loads(msg.data)
            self.commands.append(m)
            c = m.get("command")
            if c == "get_auth_state":
                await self.send(type="auth_state", logged_in=self.logged_in, is_active=True, device_name="MusicDeck (test)")
            elif c == "play" and m.get("uri"):
                await self.send(type="context_changed", context={"uri": m["uri"]})
                await self.send(type="command_result", command="play")
                if m["uri"].endswith("0000000000000000000000"):
                    continue  # a track that doesn't exist: nothing more, as the real one
                self.current, self.status, self.position = m["uri"], "buffering", 0
                await self.send(type="track_changed", item={"uri": self.current, "entity_type": "track", "decorations": {}})
                await self.send(type="playback_changed", status="buffering")
                await self.send(**self.state())
                self.status = "playing"
                await self.send(type="playback_changed", status="playing")
                await self.send(**self.state())
            elif c == "play":
                self.status = "playing"
                await self.send(type="playback_changed", status="playing")
                await self.send(**self.state())
            elif c == "pause":
                self.status = "paused"
                await self.send(type="playback_changed", status="paused")
                await self.send(**self.state())
            elif c == "seek":
                self.position = m["position_ms"]
                await self.send(type="playback_changed", status="buffering")
                await self.send(type="position_sync", position=self.pos(self.position, 0))
                await self.send(type="playback_changed", status=self.status)
                await self.send(type="position_sync", position=self.pos(self.position, 1))
            elif c == "set_volume":
                self.volume = m["volume"]
                await self.send(type="volume_changed", volume=self.volume)
            if c:
                await self.send(type="command_result", command=c)
        return ws

    async def autoplay(self, uri=AUTOPLAY):
        """The track it was given ends, and Spotify's autoplay starts the next."""
        self.current, self.status, self.position = uri, "buffering", 0
        await self.send(type="track_changed", item={"uri": uri, "entity_type": "track", "decorations": {}})
        await self.send(type="queue_changed", previous=[], upcoming=[{"source": "autoplay"}])
        await self.send(**self.state())

    def sent(self, command):
        return [c for c in self.commands if c.get("command") == command]


async def websocket_tests(http):
    print("websocket")
    fake = FakeSoloist()
    app = web.Application()
    app.router.add_get("/", fake.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    reports: list[dict] = []
    s = soloist.Soloist(tempfile.mkdtemp(), tempfile.mkdtemp(), lambda: http, reports.append, name="MusicDeck (test)")
    s.set_key("test_key")
    # as if started: a process that's running, and its WebSocket
    s.proc = types.SimpleNamespace(returncode=None, pid=0)
    await s._connect(port)
    check(await until(lambda: s.logged_in is True), "it says it's signed in")
    check(s.setup_status()["phase"] == "ready", "ready")

    # Spotify keeps a volume for the device (a phone left it at 100): becoming active, it takes that one
    await s.control("volume", 0.3)
    fake.volume = 100
    await s.load(A[len("spotify:track:"):], 0, True, 213.573)
    check(fake.sent("play")[-1].get("uri") == A, "the track is played by its URI")
    check(await until(lambda: fake.sent("set_volume") and fake.sent("set_volume")[-1]["volume"] == 30
                      and reports and reports[-1].get("volume") == 0.3),
          "the player's volume is put back once it plays, over the one Spotify kept")
    check(not any(r.get("volume") == 1.0 for r in reports), "and Spotify's isn't reported as the volume turned up")
    check(await until(lambda: any(r.get("state") == "playing" for r in reports)), "it reports playing")
    first = next(r for r in reports if "state" in r)
    check(first["state"] == "buffering" and first["position"] < 1,
          "the first report is the new track's (not the one before's state and position)")
    n = len(reports)
    await asyncio.sleep(0.3)
    check(len(reports) == n, f"no repeats: its several messages per change make one report")

    await s.control("seek", 100)
    check(await until(lambda: fake.sent("seek") and fake.sent("seek")[-1]["position_ms"] == 100000), "seek, in milliseconds")
    check(await until(lambda: any(abs(r.get("position", 0) - 100) < 1 for r in reports[n:])), "the position follows")
    await s.control("pause")
    check(await until(lambda: reports[-1].get("state") == "paused"), "pause")
    await s.control("play")
    check(await until(lambda: fake.sent("play")[-1].get("uri") is None and reports[-1].get("state") == "playing"),
          "play resumes (no URI: not from the start)")
    await s.control("volume", 0.5)
    check(await until(lambda: fake.sent("set_volume") and fake.sent("set_volume")[-1]["volume"] == 50
                      and reports[-1].get("volume") == 0.5),
          "volume, 0-100 there")

    # the end: autoplay comes on near the end of ours
    fake.position = 211000
    await fake.send(type="position_sync", position=fake.pos(211000, 1))
    await until(lambda: s.last_ours > 210)
    pauses = len(fake.sent("pause"))
    await fake.autoplay()
    check(await until(lambda: any(r.get("ended") for r in reports)), "autoplay's track coming on is the end of ours")
    check(await until(lambda: len(fake.sent("pause")) == pauses + 1), "and it's paused at once (not heard)")
    n = len(reports)
    await fake.send(**fake.state())
    await asyncio.sleep(0.2)
    check(not any("state" in r for r in reports[n:]), "what plays after isn't reported as the player's")

    # the phone takes over in the middle: paused here, and its choice is left alone
    await s.load(B[len("spotify:track:"):], 0, True, 200)
    await until(lambda: s.ours)
    fake.position = 50000
    await fake.send(type="position_sync", position=fake.pos(50000, 1))
    await until(lambda: s.last_ours > 49)
    pauses = len(fake.sent("pause"))
    mark = len(reports)
    await fake.autoplay("spotify:track:somethingTheyPicked")
    await asyncio.sleep(0.3)
    check(any(r.get("state") == "paused" for r in reports[mark:]) and not any(r.get("ended") for r in reports[mark:]),
          "another track mid-way (from the phone) is reported as paused, not the end")
    await asyncio.sleep(0.2)
    check(len(fake.sent("pause")) == pauses, "and what the phone chose keeps playing")
    plays = len(fake.sent("play"))
    await s.control("play")
    check(await until(lambda: len(fake.sent("play")) > plays) and fake.sent("play")[plays].get("uri") == B,
          "play here starts ours again")
    check(await until(lambda: fake.sent("seek") and fake.sent("seek")[-1]["position_ms"] == 50000),
          "from where it was")

    # cued (a restored session): nothing plays until play, then from where it was
    plays = len(fake.sent("play"))
    await s.load(A[len("spotify:track:"):], 30, False, 213.573)
    check(reports[-1] == {"state": "paused", "position": 30}, "a cued track is reported paused where it starts")
    check(len(fake.sent("play")) == plays, "and nothing is played yet")
    await s.control("seek", 42)
    await s.control("play")
    check(await until(lambda: fake.sent("play")[-1].get("uri") == A and fake.sent("seek")[-1]["position_ms"] == 42000),
          "play starts it, from where it was sought to")

    # a track Spotify can't play: Soloist says nothing; we give up in a while
    old = soloist.PLAY_SECONDS
    soloist.PLAY_SECONDS = 0.5
    try:
        await s.load("0000000000000000000000", 0, True, 100)
        check(False, "a track that doesn't play is an error")
    except ServiceError as e:
        check("couldn't play" in str(e), "a track that doesn't play is an error, in a moment")
        check(isinstance(e, soloist.TrackFailed), "about the track: the player moves on to the next")
    finally:
        soloist.PLAY_SECONDS = old
    try:
        await s.load("not a track!", 0, True, 100)
    except ServiceError:
        pass

    # paired with someone else's account
    s.expected_account, s.account = "me", "someone_else"
    check(s.setup_status()["phase"] == "other_account", "another account's pairing is told apart")
    try:
        await s.load(A[len("spotify:track:"):], 0, True, 213.573)
        check(False, "another account's Soloist isn't played through")
    except ServiceError as e:
        check("different Spotify account" in str(e), "another account's Soloist isn't played through")
    s.account = "me"

    # stop: ours paused, nothing more reported
    await s.load(A[len("spotify:track:"):], 0, True, 213.573)
    pauses = len(fake.sent("pause"))
    await s.control("stop")
    check(await until(lambda: len(fake.sent("pause")) == pauses + 1), "stop pauses it")
    check(s.want is None, "and forgets the track")

    # not paired: says how
    fake.logged_in = False
    s.logged_in = False
    s.output.append("2026-11-20 20:32:27.789: waiting for login — connect to \"MusicDeck (test)\" from your Spotify app")
    try:
        await s.load(A[len("spotify:track:"):], 0, True, 213.573)
        check(False, "unpaired, it says to pair it")
    except ServiceError as e:
        check("choose “MusicDeck (test)” under Devices" in str(e), "unpaired, it says how to pair it")
        check(not isinstance(e, soloist.TrackFailed), "about Soloist, not the track: the player stops, not skips")
    check(not any(r.get("paired") for r in reports), "no word of pairing yet")
    # just started with a stored session: its WebSocket says "not logged in" before it has brought it back
    # (seen with the real one: the first track was refused as unpaired)
    s.output.clear()
    s.output.append("2026-11-20 20:32:27.789: restoring session")
    check(not s._awaiting_pairing(), "\"restoring session\" isn't waiting to be paired")
    s._set_phase("pairing")  # what its WebSocket's "not logged in" sets
    check(s.setup_status()["phase"] == "starting", "and the settings say starting, not to pair it")

    async def comes_back():
        await asyncio.sleep(0.6)
        fake.logged_in = True
        s.output.append("2026-11-20 20:32:28.596: logged in as me")
    asyncio.ensure_future(comes_back())
    t = time.monotonic()
    await s.load(A[len("spotify:track:"):], 0, True, 213.573)
    check(s.logged_in and time.monotonic() - t >= 0.5, "a track asked for meanwhile waits for the session, and plays")
    check(sum(1 for r in reports if r.get("paired")) == 1,
          "paired after refusing a track: the player is told once, and plays it on")
    s.output.append("2026-11-20 20:32:28.000: No session to restore")
    check(s._awaiting_pairing(), "\"No session to restore\" is")
    s.logged_in = False
    s._set_phase("pairing")
    check(s.setup_status()["phase"] == "pairing", "and then the settings say to pair it")

    await s.ws.close()
    await runner.cleanup()

    # a Soloist left running on its data folder (the backend killed before it stopped it) is ended; a pid file
    # naming some other program by now leaves that alone
    other = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
    try:
        with open(os.path.join(s.data_dir, "soloist.pid"), "w") as f:
            f.write(str(other.pid))
        check(not soloist._is_our_soloist(other.pid, s.data_dir), "another program isn't taken for Soloist")
        await s._end_leftover()
        check(other.returncode is None, "and isn't ended")
    finally:
        other.kill()
        await other.wait()
    with open(os.path.join(s.data_dir, "soloist.pid"), "w") as f:
        f.write("not a pid")
    await s._end_leftover()
    check(True, "a pid file that doesn't name a process is passed over")


# --- the Spotify source switching over ---

async def spotify_tests():
    print("spotify source")
    runtime = tempfile.mkdtemp()
    base = {"refresh_token": "r", "username": "newer_account", "signed_with": "d420a117a32841c2b3474932e49fb54b"}
    config = await SpotifyService.authenticate({}, base, runtime)
    check(config["soloist_set"] is False and "audio" not in config, "no key: librespot, as before")
    service = SpotifyService("sp", dict(config), runtime)
    saved = []
    service.save_config = lambda c: saved.append(dict(c))
    service._librespot_refused()
    check(service.config["needs_soloist"] is True and not service.plays_itself(),
          "refused without a key: it's remembered that it needs Soloist")
    check(saved and saved[-1]["needs_soloist"], "and saved")

    config = await SpotifyService.authenticate({SOLOIST_KEY: "  abcd_0123456789abcdef0123456789  "}, service.config, runtime)
    check(config[SOLOIST_KEY] == "abcd_0123456789abcdef0123456789" and config["soloist_set"], "a key is kept (trimmed)")
    check(config["audio"] == "soloist", "an account known to need it switches as soon as the key's entered")
    if soloist.supported():
        check(SpotifyService("sp", dict(config), runtime).plays_itself(), "and its tracks play through Soloist")

    kept = await SpotifyService.authenticate({}, config, runtime)
    check(kept[SOLOIST_KEY] == config[SOLOIST_KEY], "saving without the key (the frontend never has it) keeps it")
    off = await SpotifyService.authenticate({"audio": "auto"}, config, runtime)
    check("audio" not in off and off[SOLOIST_KEY], "turned off by hand: librespot again, key kept")
    gone = await SpotifyService.authenticate({SOLOIST_KEY: ""}, config, runtime)
    check(SOLOIST_KEY not in gone and "audio" not in gone and gone["soloist_set"] is False, "an empty key takes it away")

    fresh = await SpotifyService.authenticate({SOLOIST_KEY: "abcd_0123456789abcdef0123456789"}, base, runtime)
    check("audio" not in fresh, "a key for an account that hasn't been refused: librespot until it is")
    service = SpotifyService("sp", dict(fresh), runtime)
    service.save_config = lambda c: saved.append(dict(c))
    service._librespot_refused()
    check(service.config.get("audio") == "soloist" or not soloist.supported(), "and the first refusal switches it")

    import ast
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    secrets = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == "SECRET_KEYS" for t in n.targets))
    check(SOLOIST_KEY in secrets, "the key is a secret: never sent to the frontend, nor in backups")


async def run():
    build_tests()
    async with aiohttp.ClientSession() as http:
        await install_tests(http)
        await websocket_tests(http)
    await spotify_tests()
    print("all soloist tests passed")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(run(), 120))
