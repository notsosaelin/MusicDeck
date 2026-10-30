"""
Playing on speakers and TVs on the home network ("casting").

The frontend's player stays in charge of the queue, play reports and so on; while casting, its
audio element is swapped for one that forwards to this module (src/cast.ts). The device fetches
each track itself from the stream server's home-network site (StreamServer.open_lan), so the
Deck only relays bytes, and its EQ, levelling and crossfade don't apply.

Three kinds of device: Google Cast (castv2.py; Chromecast, Google/Nest speakers, TVs and soundbars
with Cast), UPnP/DLNA renderers (upnp.py; Sonos, AV receivers, smart TVs, an Xbox's Media Player) and
Rokus (roku.py; Roku players and Roku TVs, through Play on Roku); and the user's other devices running MusicDeck
(peer.py, through peers.py), listed first, which are given the song itself rather than a link. All report
{"state": "playing"|"paused"|"buffering"|"idle", "position", "duration"?, "ended"?, "error"?},
passed on to the frontend as "cast_status" events; {"gone": reason} means the session ended
without the user ending it (the device went away, or something else started playing on it).
"""
import asyncio
import time
from typing import Any, Awaitable, Callable, Optional, Union

import aiohttp

from .. import debuglog
from ..services import MusicService, ServiceError
from . import mdns, roku, upnp
from .castv2 import CastError, Chromecast
from .peer import PeerError, PeerRenderer
from .roku import Roku, RokuError
from .upnp import Renderer, UpnpError

GOOGLECAST = "_googlecast._tcp.local"
SEARCH_SECONDS = 2.5
# a speaker or TV found is offered this long after it last answered (Play on opens with them at once)
KEPT_SECONDS = 600
# a renderer's description (its name, control addresses) is fetched again after this long
DESCRIBED_SECONDS = 1800
CONTROLS = {"play", "pause", "seek", "stop", "volume"}

Device = Union[Chromecast, Renderer, Roku, PeerRenderer]
ERRORS = (CastError, UpnpError, RokuError, PeerError)
# kinds that fetch a plain file (no HLS) in a format they list: given the compatible stream
PLAIN = {"upnp", "roku"}


class CastManager:
    def __init__(self, server, lookup: Callable[[str], Optional[MusicService]],
                 emit: Callable[[str, dict], Awaitable[None]], http: aiohttp.ClientSession, logger):
        self.server = server
        self.lookup = lookup
        self.emit = emit
        self.http = http
        self.logger = logger
        # what the last search found, by id: {"id", "name", "model", "kind", "host", ...}
        self.found: dict[str, dict] = {}
        # each device found: (when it last answered, monotonic; it); and renderers' descriptions by address
        self.seen: dict[str, tuple[float, dict]] = {}
        self.described: dict[str, tuple[float, Optional[dict]]] = {}
        self.device: Optional[Device] = None
        self.info: Optional[dict] = None
        self.lock = asyncio.Lock()
        self._sending: set[asyncio.Task] = set()
        # the user's other devices (peers.Peers), and this one's sources as they're told to them; set by the plugin
        self.peers = None
        self.services: Callable[[], list[dict]] = lambda: []

    # --- finding devices ---

    async def search(self) -> list[dict]:
        """Devices on the network, by name: [{"id", "name", "model", "kind"}]."""
        casts, renderers, rokus = await asyncio.gather(mdns.browse(GOOGLECAST, SEARCH_SECONDS), upnp.search(SEARCH_SECONDS),
                                                       roku.search(SEARCH_SECONDS), return_exceptions=True)
        found: dict[str, dict] = {}
        for entry in casts if isinstance(casts, list) else []:
            txt = entry.get("txt") or {}
            device_id = "cast:" + (txt.get("id") or entry["name"])
            found[device_id] = {"id": device_id, "name": txt.get("fn") or entry["name"].split(".")[0],
                                "model": txt.get("md") or "", "kind": "chromecast",
                                "host": entry["host"], "port": entry["port"]}
        if isinstance(renderers, dict):
            described = await asyncio.gather(*(self._describe(location, host) for location, host in renderers.items()))
            cast_hosts = {d["host"] for d in found.values()}
            for device in described:
                # a device that does both is better driven over Cast
                if device and device["host"] not in cast_hosts:
                    found[device["id"]] = {**device, "kind": "upnp"}
        if isinstance(rokus, dict):
            # a Roku TV with Cast or DLNA too is better driven over those (volume, seeking)
            taken = {d["host"] for d in found.values()}
            described_rokus = await asyncio.gather(*(roku.describe(self.http, host, base) for host, base in rokus.items()))
            for device in described_rokus:
                if device and device["host"] not in taken:
                    found[device["id"]] = {**device, "kind": "roku"}
        for error in (casts, renderers, rokus):
            if isinstance(error, BaseException):
                debuglog.debug("cast", "search failed", error=repr(error))
        now = time.monotonic()
        for device_id, device in found.items():
            self.seen[device_id] = (now, device)
        return self.known()

    def known(self) -> list[dict]:
        """The devices found lately (no searching, so at once): speakers and TVs that answered in the last
        KEPT_SECONDS, your other MusicDeck devices that are on now, and the one playing. The user's own devices
        first, then by name."""
        now = time.monotonic()
        self.seen = {k: v for k, v in self.seen.items() if now - v[0] < KEPT_SECONDS}
        found = {k: d for k, (_, d) in self.seen.items()}
        for target in self.peers.targets() if self.peers else []:
            found[target["id"]] = {**target, "peer": target["id"].split(":", 1)[1]}
        if self.info:
            found.setdefault(self.info["id"], self.info)
        self.found = found
        return sorted(({k: d[k] for k in ("id", "name", "model", "kind")} for d in found.values()),
                      key=lambda d: (d["kind"] != "musicdeck", d["name"].lower()))

    async def _describe(self, location: str, host: str) -> Optional[dict]:
        """A renderer's description, fetched once in a while rather than every search (a TV that's asleep takes
        seconds to answer)."""
        hit = self.described.get(location)
        if hit and time.monotonic() - hit[0] < DESCRIBED_SECONDS and hit[1]:
            return hit[1]
        device = await upnp.describe(self.http, location, host)
        self.described[location] = (time.monotonic(), device)
        return device

    # --- the session ---

    async def connect(self, device_id: str) -> dict[str, Any]:
        """Starts casting to a device from the last search: {"id", "name", "kind", "volume"}."""
        info = self.found.get(device_id)
        if info is None:
            raise ServiceError("That device isn't on the network any more")
        async with self.lock:
            await self._disconnect(True)
            # a device's reports count only while it's the one being cast to
            mine: list[Device] = []
            on_status = lambda status: mine[0] is self.device and self._status(status)  # noqa: E731
            on_gone = lambda reason: mine[0] is self.device and self._gone(reason)  # noqa: E731
            if info["kind"] == "musicdeck":
                if not self.peers:
                    raise ServiceError("That device isn't paired")
                device: Device = PeerRenderer(self.peers, info["peer"], self.services, on_status, on_gone)
            elif info["kind"] == "chromecast":
                device = Chromecast(info["host"], info["port"], on_status, on_gone)
            elif info["kind"] == "roku":
                device = Roku(info, self.http, on_status, on_gone)
            else:
                device = Renderer(info, self.http, on_status, on_gone)
            mine.append(device)
            try:
                await device.connect()
                if device.kind != "musicdeck":  # it's given songs, not links to this machine's casting site
                    await self.server.open_lan(info["host"])
            except (*ERRORS, OSError) as e:
                await device.close(stop_player=False)
                await self.server.close_lan()
                raise ServiceError(str(e) or "Couldn't connect to the device")
            self.device, self.info = device, info
            debuglog.debug("cast", "connected", kind=info["kind"], model=info.get("model"))
            return {"id": info["id"], "name": info["name"], "kind": info["kind"], "volume": device.volume}

    async def disconnect(self) -> None:
        async with self.lock:
            await self._disconnect(True)

    async def _disconnect(self, stop_player: bool) -> None:
        device, self.device, self.info = self.device, None, None
        if device:
            await device.close(stop_player)
        await self.server.close_lan()

    async def load(self, instance_id: str, track: dict, start: float, autoplay: bool) -> None:
        """Plays a track on the device, from `start` seconds."""
        service = self.lookup(instance_id)
        if service is None:
            raise ServiceError("Unknown service")
        if isinstance(self.device, PeerRenderer):
            # another MusicDeck plays it from its own copy of the service, or streamed from here
            track_id = track.get("id")
            if not isinstance(track_id, str):
                raise ServiceError("Bad track")
            async with self.lock:
                try:
                    await self._connected().load_track(instance_id, track, float(start or 0), bool(autoplay))  # type: ignore[union-attr]
                except PeerError as e:
                    raise ServiceError(str(e))
            return
        if not service.castable:
            raise ServiceError(f"{service.display_name} can't play on other devices from here")
        track_id, art = track.get("id"), track.get("art")
        if not isinstance(track_id, str) or (art is not None and not isinstance(art, str)):
            raise ServiceError("Bad track")
        async with self.lock:
            device = self._connected()
            link = await self.server.cast_link(instance_id, track_id, art, compatible=device.kind in PLAIN)
            if link["hls"] and device.kind in PLAIN:
                raise ServiceError("This device can't play this kind of stream")
            meta = {"title": _text(track.get("title")), "artist": _text(track.get("artist")),
                    "album": _text(track.get("album")), "art": link["art"], "segments": link["segments"],
                    "duration": track["duration"] if isinstance(track.get("duration"), (int, float)) else None}
            debuglog.debug("cast", "load", type=link["type"], hls=link["hls"], start=start)
            try:
                await device.load(link["url"], link["type"], meta, float(start or 0), bool(autoplay),
                                  bool(track.get("live")))
            except ERRORS as e:
                raise ServiceError(str(e))

    async def control(self, action: str, value: Optional[float] = None) -> None:
        """play, pause, stop, seek (seconds) or volume (0..1)."""
        if action not in CONTROLS:
            raise ServiceError("Unknown control")
        if action in ("seek", "volume") and not isinstance(value, (int, float)):
            raise ServiceError("Bad value")
        async with self.lock:
            device = self._connected()
            try:
                if action == "seek":
                    await device.seek(float(value))  # type: ignore[arg-type]
                elif action == "volume":
                    await device.set_volume(float(value))  # type: ignore[arg-type]
                else:
                    await getattr(device, action)()
            except ERRORS as e:
                raise ServiceError(str(e))

    def _connected(self) -> Device:
        if not self.device:
            raise ServiceError("Not casting")
        return self.device

    # --- from the device ---

    def _status(self, status: dict) -> None:
        self._send({**status, "volume": self.device.volume if self.device else None})

    def _gone(self, reason: str) -> None:
        self.logger.info(f"Casting ended: {reason}")
        self._send({"gone": reason})

        async def tidy():
            async with self.lock:
                await self._disconnect(False)
        self._spawn(tidy())

    def _send(self, status: dict) -> None:
        self._spawn(self.emit("cast_status", status))

    def _spawn(self, work: Awaitable) -> None:
        task = asyncio.ensure_future(work)
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)


def _text(value: Any) -> str:
    return value[:500] if isinstance(value, str) else ""
