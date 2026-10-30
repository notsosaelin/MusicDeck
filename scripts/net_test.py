"""Two musicdeck-net helpers on this machine, paired with each other, measured: what a session fetch, a page
of plays and a playlist sync cost in bytes, what staying in touch costs while idle, and how fast a device that
goes away shows as offline. --relay-only sends everything through n0's relays (no direct path).

    python -u scripts/net_test.py [--relay-only] [--idle 120] [--exe bin/windows-x86_64/musicdeck-net.exe]
"""
import argparse
import json
import os
import queue
import random
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SESSION = {"name": "Test Deck", "services": [{"type": "qobuz", "name": "Qobuz"}, {"type": "youtube"}],
           "session": {"source": "qobuz", "instance": "qobuz", "track": {"id": "123456789", "title": "Smells Like Teen Spirit",
                       "artist": "Nirvana", "album": "Nevermind", "duration": 301, "art": "https://static.qobuz.com/images/covers/x.jpg"},
                       "position": 87.4, "playing": True, "queue": ["123456790", "123456791"], "at": time.time()}}


def plays_page(n=200):
    return {"plays": [{"t": time.time() - i * 200, "id": str(random.randrange(10**9)), "src": "qobuz",
                       "title": f"Song {i}", "artist": random.choice(["Nirvana", "Pixies", "Hole", "Mudhoney"]),
                       "album": "Some Album", "dur": 200 + i % 90} for i in range(n)], "more": False}


def playlists(n_tracks=400):
    tracks = [{"id": str(random.randrange(10**9)), "src": random.choice(["qobuz", "youtube", "spotify"]),
               "title": f"Track {i}", "artist": random.choice(["Nirvana", "Pixies", "Hole", "Mudhoney", "Sonic Youth"]),
               "album": f"Album {i % 30}", "duration": 180 + i % 120, "art": f"https://example.com/art/{i % 30}.jpg"}
              for i in range(n_tracks)]
    return {"playlists": [{"id": "pl1", "name": "Road trip", "tracks": tracks, "changed": time.time()}], "deleted": {}}


ANSWERS = {"/p/hello": lambda: {"id": "x", "name": "Test Deck", "version": "0.43.0"},
           "/p/session": lambda: SESSION, "/p/plays": plays_page, "/p/playlists": playlists,
           "/p/playlists/changed": lambda: {"ok": True}}


class Helper:
    def __init__(self, exe, name, extra):
        self.name = name
        folder = tempfile.mkdtemp(prefix=f"mdnet-{name}-")
        self.proc = subprocess.Popen([exe, "--key", os.path.join(folder, "net.key"), *extra], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.events = queue.Queue()
        self.results = {}
        self.node = None
        self.next = 1
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            msg = json.loads(line)
            op = msg.get("op")
            if op == "request":
                path = msg["req"]["path"].split("?")[0]
                self.send({"op": "reply", "rid": msg["rid"], "res": ANSWERS[path]()})
            elif op == "result":
                self.results[msg["id"]] = msg
            else:
                self.events.put(msg)

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def wait(self, pred, timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self.events.get(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                break
            if msg.get("op") == "log":
                print(f"  [{self.name}] {msg['msg']}")
            elif msg.get("op") == "peer":
                print(f"  [{self.name}] peer {'online via ' + msg['via'] if msg['online'] else 'offline'}")
            if pred(msg):
                return msg
        return None

    def call(self, node, path, method="GET", timeout=40):
        cid = self.next
        self.next += 1
        self.send({"op": "call", "id": cid, "node": node, "req": {"method": method, "path": path}})
        end = time.time() + timeout
        while time.time() < end and cid not in self.results:
            time.sleep(0.02)
        return self.results.pop(cid, {"error": "timed out here"})

    def stats(self):
        self.send({"op": "stats"})
        return self.wait(lambda m: m.get("op") == "stats", 10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=os.path.join(ROOT, "bin", "windows-x86_64", "musicdeck-net.exe"))
    ap.add_argument("--relay-only", action="store_true")
    ap.add_argument("--idle", type=int, default=120, help="seconds of idle presence to measure")
    args = ap.parse_args()

    a = Helper(args.exe, "A", ["--relay-only"] if args.relay_only else [])
    b = Helper(args.exe, "B", [])
    for h in (a, b):
        h.node = h.wait(lambda m: m.get("op") == "ready", 30)["node"]
        print(f"{h.name}: {h.node}")
    a.send({"op": "allow", "nodes": [b.node]})
    b.send({"op": "allow", "nodes": [a.node]})
    t0 = time.time()
    up = a.wait(lambda m: m.get("op") == "peer" and m.get("online"), 90)
    print(f"A sees B online after {time.time() - t0:.1f}s via {up and up.get('via')}")
    if not up:
        sys.exit("never connected")

    def spent():
        s = a.stats()
        return s["tx"] + s["rx"]

    time.sleep(3)
    rows = []
    for label, path, method in [("session fetch", "/p/session", "GET"), ("hello", "/p/hello", "GET"),
                                ("plays page (200 listens)", "/p/plays?since=0", "GET"),
                                ("playlist sync (400 songs)", "/p/playlists", "GET"),
                                ("playlists changed", "/p/playlists/changed", "POST")]:
        before = spent()
        t = time.time()
        got = a.call(b.node, path, method)
        took = time.time() - t
        time.sleep(1)
        cost = spent() - before
        raw = len(json.dumps(got.get("res")))
        rows.append((label, cost, raw, took, "error" in got))
        print(f"{label:28s} {cost:7d} B on the wire (JSON {raw:6d} B), {took * 1000:.0f} ms"
              + (f"  ERROR {got['error']}" if "error" in got else ""))

    bad = a.call(b.node, "/p/stream/local/1")
    print(f"a path that isn't allowed: {bad.get('error')!r}")

    before, t = spent(), time.time()
    print(f"idle for {args.idle}s...")
    a.wait(lambda m: m.get("op") == "peer" and not m.get("online"), args.idle)
    idle = spent() - before
    per_hour = idle / (time.time() - t) * 3600
    print(f"idle presence: {idle} B in {time.time() - t:.0f}s = {per_hour / 1024:.1f} KB/hour (both directions, A's side)")

    print("B killed (as if it lost power)...")
    t = time.time()
    b.proc.kill()
    gone = a.wait(lambda m: m.get("op") == "peer" and not m.get("online"), 90)
    print(f"A shows B offline after {time.time() - t:.1f}s" if gone else "A never showed B offline")
    a.send({"op": "quit"})
    a.proc.wait(10)


if __name__ == "__main__":
    main()
