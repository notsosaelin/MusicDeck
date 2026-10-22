"""
Casting without a speaker: mDNS parsing, the Cast protocol against a fake Chromecast, UPnP against
a fake renderer, and the stream server's home-network site, all on this machine.

    python tests/test_cast.py      (needs aiohttp + certifi installed)
"""
import asyncio
import json
import os
import socket
import struct
import sys
import tempfile
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "py_modules")]

import logging  # noqa: E402

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

from musicdeck.cast import CastManager, castv2, mdns, upnp  # noqa: E402
from musicdeck.server import StreamServer, _b64  # noqa: E402
from musicdeck.services import MusicService, ServiceError, StreamSource  # noqa: E402

logger = logging.getLogger("cast-test")


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("  ok -", message)


# --- mDNS ---

def name(text):
    return mdns._encode_name(text)


def record(owner: bytes, rtype: int, body: bytes) -> bytes:
    return owner + struct.pack("!HHIH", rtype, 0x8001, 120, len(body)) + body


def mdns_tests():
    print("mdns")
    service = name("_googlecast._tcp.local")
    header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 3)
    # the PTR's owner is the service name at offset 12; later names point back into it
    instance = b"\x0eChromecast-abc" + b"\xc0\x0c"
    ptr = record(service, mdns.PTR, instance)
    instance_at = 12 + len(service) + 10
    srv_body = struct.pack("!HHH", 0, 0, 8009) + name("abc.local")
    srv = record(b"\xc0" + bytes([instance_at]), mdns.SRV, srv_body)
    txt_body = b"".join(bytes([len(x)]) + x for x in (b"id=abc123", b"fn=Living Room", b"md=Nest Audio"))
    txt = record(b"\xc0" + bytes([instance_at]), mdns.TXT, txt_body)
    a = record(name("abc.local"), mdns.A, socket.inet_aton("192.168.1.20"))
    packet = header + ptr + srv + txt + a
    found = mdns._instances(mdns.parse(packet), "_googlecast._tcp.local")
    check(found == [{"name": "Chromecast-abc._googlecast._tcp.local", "host": "192.168.1.20", "port": 8009,
                     "txt": {"id": "abc123", "fn": "Living Room", "md": "Nest Audio"}}],
          f"a compressed mDNS answer gives the device's name, address and port: {found}")
    loop = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c"
    try:
        mdns.parse(loop)
        check(False, "a name that points at itself is refused")
    except ValueError:
        check(True, "a name that points at itself is refused")
    check(mdns.parse(struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + service + b"\0\x0c\0\x01") == [],
          "queries (others browsing) are ignored")


# --- a fake Chromecast ---

class FakeCast:
    def __init__(self):
        self.received: list[tuple[str, str, dict]] = []
        self.writer = None
        self.media_session = 7
        self.player_state = "IDLE"

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    def send(self, source, namespace, payload):
        self.writer.write(castv2.encode(source, castv2.SENDER, namespace, payload))

    def receiver_status(self, request_id, running=True):
        apps = [{"appId": castv2.DEFAULT_RECEIVER, "transportId": "t-1", "sessionId": "s-1"}] if running else []
        return {"type": "RECEIVER_STATUS", "requestId": request_id,
                "status": {"applications": apps, "volume": {"level": 0.4}}}

    def media_status(self, request_id, **extra):
        status = {"mediaSessionId": self.media_session, "playerState": self.player_state, "currentTime": 12.5,
                  "media": {"duration": 200}, **extra}
        return {"type": "MEDIA_STATUS", "requestId": request_id, "status": [status]}

    async def handle(self, reader, writer):
        self.writer = writer
        try:
            while True:
                (length,) = struct.unpack(">I", await reader.readexactly(4))
                message = castv2.decode(await reader.readexactly(length))
                payload = json.loads(message[6])
                self.received.append((message[3], message[4], payload))
                kind, rid = payload.get("type"), payload.get("requestId", 0)
                if kind == "GET_STATUS" and message[4] == castv2.NS_RECEIVER:
                    self.send("receiver-0", castv2.NS_RECEIVER, self.receiver_status(rid, running=False))
                elif kind == "LAUNCH":
                    self.send("receiver-0", castv2.NS_RECEIVER, self.receiver_status(rid))
                elif kind == "SET_VOLUME":
                    self.send("receiver-0", castv2.NS_RECEIVER, self.receiver_status(rid))
                elif message[4] == castv2.NS_MEDIA:
                    if kind == "LOAD":
                        self.player_state = "PLAYING" if payload.get("autoplay") else "PAUSED"
                    elif kind in ("PLAY", "PAUSE"):
                        self.player_state = "PLAYING" if kind == "PLAY" else "PAUSED"
                    self.send("t-1", castv2.NS_MEDIA, self.media_status(rid))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    def of(self, kind):
        return [p for _, _, p in self.received if p.get("type") == kind]


async def until(condition, seconds=3.0):
    for _ in range(int(seconds * 50)):
        if condition():
            return True
        await asyncio.sleep(0.02)
    return False


async def castv2_tests():
    print("castv2")
    message = castv2.decode(castv2.encode("a", "b", "urn:x", {"type": "PING", "n": "é"})[4:])
    check(message[2] == "a" and message[3] == "b" and message[4] == "urn:x" and json.loads(message[6])["n"] == "é",
          "a CastMessage survives encoding and decoding")
    fake = FakeCast()
    await fake.start()
    statuses, gone = [], []
    cast = castv2.Chromecast("127.0.0.1", fake.port, statuses.append, gone.append)
    cast.tls = False
    await cast.connect()
    check(cast.transport_id == "t-1" and cast.volume == 0.4, "connecting launches the media player and reads the volume")
    check([p["appId"] for p in fake.of("LAUNCH")] == [castv2.DEFAULT_RECEIVER], "Google's default media receiver is launched")
    await cast.load("http://deck/c/x", "audio/mpeg", {"title": "Song", "artist": "Band", "album": "LP", "art": "http://deck/c/x/art",
                                                      "duration": 200}, 30, True, False)
    load = fake.of("LOAD")[0]
    check(load["media"]["contentId"] == "http://deck/c/x" and load["currentTime"] == 30 and load["autoplay"] is True
          and load["media"]["metadata"]["title"] == "Song" and load["media"]["metadata"]["images"][0]["url"].endswith("/art"),
          "LOAD carries the link, start time, title and cover")
    check(statuses[-1]["state"] == "playing" and statuses[-1]["position"] == 12.5 and statuses[-1]["duration"] == 200,
          "its reply reports the position and length")
    await cast.pause()
    check(fake.of("PAUSE")[0]["mediaSessionId"] == 7 and statuses[-1]["state"] == "paused", "pause goes to the media session")
    await cast.set_volume(1.7)
    check(fake.of("SET_VOLUME")[-1]["volume"]["level"] == 1.0, "volume is kept within 0..1")
    fake.player_state = "IDLE"
    fake.send("t-1", castv2.NS_MEDIA, fake.media_status(0, idleReason="FINISHED"))
    check(await until(lambda: statuses[-1].get("ended")), "a track playing to its end is reported as ended")
    fake.send("receiver-0", castv2.NS_HEARTBEAT, {"type": "PING"})
    check(await until(lambda: fake.of("PONG")), "the device's pings are answered")
    fake.send("receiver-0", castv2.NS_RECEIVER, fake.receiver_status(0, running=False))
    check(await until(lambda: gone), "another app taking over the device ends the session")
    await cast.close()

    # the device dropping the connection
    gone2 = []
    cast = castv2.Chromecast("127.0.0.1", fake.port, lambda s: None, gone2.append)
    cast.tls = False
    await cast.connect()
    fake.writer.close()
    check(await until(lambda: gone2), "a dropped connection ends the session")
    await cast.close(stop_player=False)
    # a device that isn't there
    nowhere = castv2.Chromecast("127.0.0.1", 9, lambda s: None, lambda r: None)
    nowhere.tls = False
    try:
        await nowhere.connect()
        check(False, "a device that isn't there is an error")
    except castv2.CastError:
        check(True, "a device that isn't there is an error")
    fake.server.close()


# --- a fake UPnP renderer ---

DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<friendlyName>Kitchen &amp; Co</friendlyName><manufacturer>Acme</manufacturer><modelName>Box</modelName>
<UDN>uuid:1234</UDN><serviceList>
<service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType><controlURL>/av</controlURL></service>
<service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType><controlURL>/rc</controlURL></service>
</serviceList></device></root>"""


class FakeRenderer:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.state = "NO_MEDIA_PRESENT"

    async def start(self):
        app = web.Application()
        app.router.add_get("/desc.xml", lambda r: web.Response(text=DESCRIPTION, content_type="text/xml"))
        app.router.add_get("/evil.xml", lambda r: web.Response(text='<!DOCTYPE x [<!ENTITY a "b">]><root/>'))
        app.router.add_post("/av", self.control)
        app.router.add_post("/rc", self.control)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def control(self, request):
        action = request.headers["SOAPAction"].strip('"').split("#")[1]
        body = await request.text()
        self.calls.append((action, body))
        out = ""
        if action == "Play":
            self.state = "PLAYING"
        elif action == "Pause":
            self.state = "PAUSED_PLAYBACK"
        elif action == "Stop":
            self.state = "STOPPED"
        elif action == "GetTransportInfo":
            out = f"<CurrentTransportState>{self.state}</CurrentTransportState>"
        elif action == "GetPositionInfo":
            out = "<RelTime>0:01:05</RelTime><TrackDuration>0:03:20</TrackDuration>"
        elif action == "GetVolume":
            out = "<CurrentVolume>35</CurrentVolume>"
        return web.Response(text=f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                                 f'<u:{action}Response xmlns:u="x">{out}</u:{action}Response></s:Body></s:Envelope>',
                            content_type="text/xml")

    def of(self, action):
        return [b for a, b in self.calls if a == action]


async def upnp_tests(http):
    print("upnp")
    fake = FakeRenderer()
    await fake.start()
    base = f"http://127.0.0.1:{fake.port}"
    device = await upnp.describe(http, f"{base}/desc.xml", "127.0.0.1")
    check(device and device["name"] == "Kitchen & Co" and device["model"] == "Acme Box"
          and device["transport"] == f"{base}/av" and device["rendering"] == f"{base}/rc",
          "a renderer's description gives its name and control addresses")
    check(await upnp.describe(http, f"{base}/desc.xml", "10.9.9.9") is None,
          "a description that isn't on the device that answered is ignored")
    check(await upnp.describe(http, f"{base}/evil.xml", "127.0.0.1") is None, "XML with entity declarations is refused")
    check(upnp._seconds("1:02:03.500") == 3723.5 and upnp._seconds("NOT_IMPLEMENTED") == 0 and upnp._clock(3723.9) == "1:02:03",
          "UPnP clock times convert both ways")
    meta = upnp.didl("http://deck/c/x?a=1&b=2", "audio/mp4", {"title": "Rock & <Roll>", "artist": "A", "duration": 200})
    check("Rock &amp; &lt;Roll&gt;" in meta and "a=1&amp;b=2" in meta and "http-get:*:audio/mp4:DLNA.ORG_OP=01" in meta
          and 'duration="0:03:20.000"' in meta, "DIDL metadata is escaped and says how to fetch and seek")
    statuses, gone = [], []
    renderer = upnp.Renderer(device, http, statuses.append, gone.append)
    await renderer.connect()
    check(renderer.volume == 0.35, "connecting reads the volume")
    await renderer.load("http://deck/c/x", "audio/mpeg", {"title": "T"}, 0, True, False)
    set_uri = fake.of("SetAVTransportURI")[0]
    check("<CurrentURI>http://deck/c/x</CurrentURI>" in set_uri and "&lt;DIDL-Lite" in set_uri,
          "the link and its metadata (escaped once more inside SOAP) are sent")
    check(fake.state == "PLAYING", "autoplay plays")
    check(await until(lambda: statuses and statuses[-1]["state"] == "playing" and statuses[-1]["position"] == 65), "the position is polled")
    await renderer.set_volume(0.5)
    check("<DesiredVolume>50</DesiredVolume>" in fake.of("SetVolume")[0], "volume goes to RenderingControl in percent")
    renderer.started_at -= 10
    fake.state = "STOPPED"  # the track finished on its own
    check(await until(lambda: statuses[-1].get("ended")), "stopping by itself while playing counts as the track ending")
    await renderer.close()
    await fake.runner.cleanup()


# --- the stream server's home-network site, and the manager ---

class FakeSource(MusicService):
    type = "fake"
    display_name = "Fake"

    def __init__(self, path, castable=True):
        super().__init__("fake", {}, "")
        self.path = path
        self.castable = castable
        self.playlist = "#EXTM3U\n#EXTINF:5,\nhttps://cdn.example/seg1.aac\n"

    async def stream(self, track_id):
        if track_id == "hls":
            return StreamSource(playlist=self.playlist)
        return StreamSource(path=self.path)

    async def art(self, art_id):
        return StreamSource(path=self.path, content_type="image/jpeg")


async def lan_tests(http, path):
    print("home-network site")
    source = FakeSource(path)
    server = StreamServer(lambda i: source if i == "fake" else None, logger)
    await server.start()
    try:
        try:
            await server.cast_link("fake", "t", None, False)
            check(False, "no links are made when not casting")
        except ServiceError:
            check(True, "no links are made when not casting")
        await server.open_lan("127.0.0.1")
        link = await server.cast_link("fake", "t", "a", False)
        check(link["type"] == "audio/x-wav" or link["type"].startswith("audio/"), f"a local file's type is named: {link['type']}")
        async with http.get(link["url"]) as r:
            check(r.status == 200 and (await r.read())[:4] == b"RIFF", "the device fetches the track at its link")
        async with http.get(link["url"], headers={"Range": "bytes=0-3"}) as r:
            check(r.status == 206, "ranges work, so the device can seek")
        async with http.get(link["art"]) as r:
            check(r.status == 200, "and its cover")
        async with http.get(server.lan_base + "/c/guessed") as r:
            check(r.status == 404, "a made-up link gets nothing")
        async with http.get(server.lan_base + f"/stream/fake/t?k={server.key}") as r:
            check(r.status == 404, "the Deck-only routes aren't on the network site, even with the key")
        hls = await server.cast_link("fake", "hls", None, False)
        check(hls["hls"] and hls["type"] == "application/x-mpegurl", "an HLS track is announced as HLS")
        async with http.get(hls["url"]) as r:
            text = await r.text()
        token = hls["url"].rsplit("/", 1)[1]
        check(f"/c/{token}/f?u=" in text and "cdn.example" not in text, "its pieces are rewritten to come through the Deck")
        async with http.get(f"{server.lan_base}/c/{token}/f?u={_b64('https://elsewhere.example/x')}") as r:
            check(r.status == 403, "the relay only fetches this track's own pieces, nothing else")
        server.lan_peer = "10.1.2.3"
        async with http.get(link["url"]) as r:
            check(r.status == 403, "only the device being cast to is answered")
        server.lan_peer = "127.0.0.1"
        for _ in range(5):
            await server.cast_link("fake", "t", None, False)
        async with http.get(link["url"]) as r:
            check(r.status == 404, "old links stop working as new tracks play")
        base = server.lan_base
        await server.close_lan()
        try:
            async with http.get(base + "/c/x"):
                pass
            check(False, "the network site closes when casting stops")
        except aiohttp.ClientConnectionError:
            check(True, "the network site closes when casting stops")
    finally:
        await server.stop()


async def manager_tests(http, path):
    print("manager")
    sources = {"fake": FakeSource(path), "drm": FakeSource(path, castable=False)}
    server = StreamServer(sources.get, logger)
    await server.start()
    events = []

    async def emit(name, data):
        events.append((name, data))
    manager = CastManager(server, sources.get, emit, http, logger)
    fake = FakeCast()
    await fake.start()
    original = castv2.Chromecast.tls
    castv2.Chromecast.tls = False
    try:
        try:
            await manager.connect("cast:nope")
            check(False, "a device from no search is refused")
        except ServiceError:
            check(True, "a device from no search is refused")
        manager.found = {"cast:x": {"id": "cast:x", "name": "Den", "model": "", "kind": "chromecast",
                                    "host": "127.0.0.1", "port": fake.port}}
        session = await manager.connect("cast:x")
        check(session == {"id": "cast:x", "name": "Den", "kind": "chromecast", "volume": 0.4} and server.lan_base,
              "connecting opens the network site and reports the device's volume")
        await manager.load("fake", {"id": "t", "title": "Song", "art": "a", "duration": 100}, 5, True)
        media = fake.of("LOAD")[-1]["media"]
        check(media["contentId"].startswith(server.lan_base + "/c/") and media["metadata"]["title"] == "Song",
              "a track is loaded from a fresh link on the network site")
        check(await until(lambda: any(n == "cast_status" and d.get("state") == "playing" for n, d in events)),
              "the device's state reaches the frontend")
        for bad, why in ((("drm", {"id": "t"}), "a source that can't be cast is refused"),
                         (("fake", {"id": 5}), "a malformed track is refused"),
                         (("gone", {"id": "t"}), "an unknown source is refused")):
            try:
                await manager.load(*bad, 0, True)
                check(False, why)
            except ServiceError:
                check(True, why)
        try:
            await manager.control("rm -rf", None)
            check(False, "unknown controls are refused")
        except ServiceError:
            check(True, "unknown controls are refused")
        await manager.control("volume", 0.25)
        check(fake.of("SET_VOLUME")[-1]["volume"]["level"] == 0.25, "volume reaches the device")
        manager.info = {**manager.info, "kind": "upnp"}
        manager.device.kind = "upnp"
        try:
            await manager.load("fake", {"id": "hls"}, 0, True)
            check(False, "HLS isn't sent to UPnP renderers")
        except ServiceError:
            check(True, "HLS isn't sent to UPnP renderers")
        manager.device.kind = "chromecast"
        fake.send("receiver-0", castv2.NS_RECEIVER, fake.receiver_status(0, running=False))
        check(await until(lambda: any(d.get("gone") for _, d in events) and manager.device is None and not server.lan_base),
              "a session ended on the device is reported, and the network site closes")
        session = await manager.connect("cast:x")
        await manager.disconnect()
        check(manager.device is None and not server.lan_base and await until(lambda: fake.of("STOP")), "stopping casting stops the device's player")
    finally:
        castv2.Chromecast.tls = original
        await manager.disconnect()
        await server.stop()
        fake.server.close()


# standard modules Decky's frozen Python leaves out; importing one stops the whole backend loading
MISSING_IN_DECKY = ("xml.etree", "xml.dom", "sqlite3", "tkinter")


def decky_python_tests():
    print("decky's python")
    offenders = []
    for folder in (os.path.join(ROOT, "py_modules", "musicdeck"), ROOT):
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isdir(path) and name != "__pycache__" and folder != ROOT:
                files = [os.path.join(path, f) for f in os.listdir(path) if f.endswith(".py")]
            elif name.endswith(".py"):
                files = [path]
            else:
                continue
            for file in files:
                with open(file, encoding="utf-8") as f:
                    for line in f:
                        if line.lstrip().startswith(("import ", "from ")) and any(m in line for m in MISSING_IN_DECKY):
                            offenders.append(f"{os.path.relpath(file, ROOT)}: {line.strip()}")
    check(not offenders, f"nothing imports a module Decky's Python doesn't have: {offenders}")


async def run():
    decky_python_tests()
    mdns_tests()
    await castv2_tests()
    tmp = tempfile.mkdtemp(prefix="musicdeck-cast-")
    path = os.path.join(tmp, "a.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    async with aiohttp.ClientSession() as http:
        await upnp_tests(http)
        await lan_tests(http, path)
        await manager_tests(http, path)
    print("all cast checks passed")


asyncio.run(run())
