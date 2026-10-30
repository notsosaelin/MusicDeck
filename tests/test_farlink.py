"""
Your devices over the internet (farlink.py, the musicdeck-net helper): two devices in this process, each with its
own helper, paired over loopback as at home, then made to reach each other only over the internet link (far_only):
presence, what's playing (and how long ago, for where the song has got to), "Continue here", listens and playlists;
what isn't allowed over it refused, by the helper and again by peers.py; a device that isn't paired refused; unpairing
and a device going away shown at once. Needs the helper built for this platform (scripts/build_net_helper.sh) and an
internet connection (devices find each other through n0's discovery).

    python -u tests/test_farlink.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import aiohttp  # noqa: E402

from musicdeck import native, peers as peermod, stats as statsmod  # noqa: E402
from musicdeck.farlink import Farlink, FarError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
failures = []


def check(ok, what):
    print(("  ok - " if ok else "  FAIL - ") + what)
    if not ok:
        failures.append(what)


async def until(cond, seconds, step=0.25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return cond()


async def main():
    exe = native.helper("musicdeck-net", None)
    if not exe:
        sys.exit("no musicdeck-net built for this platform: scripts/build_net_helper.sh")
    tmp = tempfile.mkdtemp(prefix="farlink-test-")
    http = aiohttp.ClientSession()
    events = {"a": [], "b": []}
    sessions = {"a": {"saved_at": time.time() - 30}, "b": {"saved_at": time.time() - 30}}
    devs, logs, playlists = {}, {}, {"a": [], "b": []}

    async def served(request, kind, instance, item_id):
        raise AssertionError("nothing streams over the internet")

    for who in ("a", "b"):
        folder = os.path.join(tmp, who)
        os.makedirs(folder)
        logs[who] = statsmod.PlayLog(os.path.join(folder, "plays.jsonl"))

        async def emit(name, payload, who=who):
            events[who].append((name, payload))
            if name == "peer_took_over":  # the frontend pauses, and saves where it stopped
                sessions[who] = {"saved_at": time.time(), "playing": False}

        def session(who=who):
            return {"queue": [{"instanceId": "qz", "track": {"id": "t1", "title": "Song", "duration": 200}}],
                    "index": 0, "position": 42, "playing": True, **sessions[who]}
        devs[who] = peermod.Peers(folder, f"Far {who.upper()}", logging.getLogger("far-" + who), emit, session,
                                  lambda: [{"id": "qz", "type": "qobuz", "name": "Qobuz"}], logs[who], served, http,
                                  far_exe=exe)

        async def take(peer_id, theirs, who=who):
            playlists[who].append(theirs)
            return True
        devs[who].playlists_shared = lambda who=who: {"playlists": [{"id": f"pl-{who}", "name": f"From {who}",
                                                                     "tracks": [{"id": "x"}] * 300}], "deleted": {}}
        devs[who].playlists_take = take
        await devs[who].start()
    A, B = devs["a"], devs["b"]
    stranger = None
    try:
        print("pairing at home")
        A._heard(json.dumps({"musicdeck": 1, "id": B.me["id"], "name": "Far B", "port": B.port}).encode(), "127.0.0.1")
        B._heard(json.dumps({"musicdeck": 1, "id": A.me["id"], "name": "Far A", "port": A.port}).encode(), "127.0.0.1")
        B.pairable_until = time.time() + 60
        await A.pair(B.me["id"])
        prompt = next(p for n, p in events["b"] if n == "peer_pair_request")
        await B.answer(prompt["rid"], True)
        check(await until(lambda: B.me["id"] in A.paired, 15), "paired")
        check(await until(lambda: A.paired[B.me["id"]].get("node") and B.paired[A.me["id"]].get("node"), 10),
              "each learned the other's internet id (signed, at home)")
        check(A.far.node and len(A.far.node) == 64 and A.paired[B.me["id"]]["node"] == B.far.node,
              "the id is the other's helper's")

        print("away")
        A.far_only = B.far_only = True
        t = time.monotonic()
        online = await until(lambda: A._far(B.me["id"]) and B._far(A.me["id"]), 60)
        check(online, f"each sees the other online over the internet ({time.monotonic() - t:.1f}s)")
        if not online:
            return
        listed = A.listing()["paired"][0]
        check(listed["online"] and listed["via"] == "internet", f"listed as online over the internet: {listed}")
        check(not A.near(B.me["id"]), "and not as nearby (its Local files don't stream from there)")

        t = time.monotonic()
        got = await A.call(B.me["id"], "GET", "/p/session")
        check(got["session"]["position"] == 42 and 29 < got.get("age", 0) < 600,
              f"what's playing there, and how long ago by its own clock ({got.get('age')}s, "
              f"{(time.monotonic() - t) * 1000:.0f} ms)")
        found = await A.sessions()
        check(len(found) == 1 and found[0]["age"] > 29, "Jump back in has it too, with its age")

        sessions["b"] = {"saved_at": time.time() - 3, "playing": True}
        told = await A.call(B.me["id"], "POST", "/p/took-over", {})
        check(told.get("saved") and ("peer_took_over", {"id": A.me["id"], "name": "Far A"}) in events["b"],
              "Continue here: it pauses there, and answers once where it stopped is saved")
        after = await A.call(B.me["id"], "GET", "/p/session")
        check(after["session"]["playing"] is False and after["age"] < 3, "so the session read next is where it stopped")

        logs["b"].add("qz", {"id": "s1", "title": "One", "artist": "X"}, when=time.time() - 100)
        logs["b"].add("qz", {"id": "s2", "title": "Two", "artist": "X"}, when=time.time() - 50)
        added = await A.sync_plays(B.me["id"])
        check(added == 2, f"its listens come over (since=, a page at a time): {added}")
        check(await A.sync_plays(B.me["id"]) == 0, "and only new ones next time")

        check(await A.sync_playlists(B.me["id"]) and playlists["a"][-1]["playlists"][0]["id"] == "pl-b",
              "its playlists come over (300 songs, compressed on the way)")
        before = len(playlists["b"])
        A.playlists_changed()
        check(await until(lambda: len(playlists["b"]) > before, 15), "told this one's changed, it fetches them")

        print("played on, and told")
        cast_asked = []

        async def cast_here(peer_id, action, payload):
            cast_asked.append(action)
            return {"state": "playing", "position": 3} if action == "status" else {"ok": True}
        B.on_cast = cast_here
        await A.call(B.me["id"], "POST", "/p/cast/start", {})
        status = await A.call(B.me["id"], "GET", "/p/cast/status")
        await A.call(B.me["id"], "POST", "/p/cast/end", {"stop": True})
        check(cast_asked == ["start", "status", "end"] and status.get("state") == "playing",
              f"played on as a speaker over the internet: {cast_asked}")
        check(any(t["id"] == f"musicdeck:{B.me['id']}" for t in A.targets()), "and offered as a place to play")
        events["a"].clear()
        B.session_changed()
        check(await until(lambda: ("peer_session", {"id": B.me["id"]}) in events["a"], 10),
              "it played or paused: the other is told at once")

        print("what isn't allowed")
        b_node = A.paired[B.me["id"]]["node"]
        for method, path in (("GET", "/p/art/loc/1"), ("GET", "/p/stream/loc/1"), ("POST", "/p/pair"),
                             ("GET", "/p/session/../stream"), ("GET", "/p/plays?since=1;drop")):
            try:
                await A.far.call(b_node, method, path)
                check(False, f"{method} {path} refused")
            except FarError as e:
                check("not allowed" in str(e), f"{method} {path} refused by the helper ({e})")
        try:
            await B._far_asked(A.far.node, {"method": "GET", "path": "/p/art/loc/1"})
            check(False, "peers.py refuses it too")
        except PermissionError:
            check(True, "peers.py refuses it too")
        try:
            await B._far_asked("f" * 64, {"method": "GET", "path": "/p/session"})
            check(False, "and asks from a device it doesn't know")
        except PermissionError:
            check(True, "and asks from a device it doesn't know")

        stranger_events = []

        async def nothing(node, req):
            return None

        async def seen(node, online, via):
            stranger_events.append(online)
        stranger = Farlink(exe, os.path.join(tmp, "stranger.key"), logging.getLogger("stranger"), nothing, seen)
        await stranger.start()
        await asyncio.wait_for(stranger.ready.wait(), 20)
        stranger.allow([b_node])
        try:
            await stranger.call(b_node, "GET", "/p/session", timeout=30)
            check(False, "a device that isn't paired is refused")
        except FarError as e:
            check(True, f"a device that isn't paired is refused ({e})")
        await asyncio.sleep(3)
        check(not stranger.online(b_node), "and never shows it online")

        print("going away")
        a_node = B.paired[A.me["id"]]["node"]
        await A.unpair(B.me["id"])
        check(not B.paired and a_node not in B.far.allowed, "unpairing over the internet: forgotten there, and refused")
        check(await until(lambda: not B.far.online(a_node), 10), "and shown gone at once")
    finally:
        if stranger:
            await stranger.stop()
        for d in devs.values():
            await d.stop()
        await http.close()
    print("FAILED: " + "; ".join(failures) if failures else "all farlink checks passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
