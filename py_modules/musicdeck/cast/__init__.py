"""
Playing on speakers and TVs on the home network ("casting").

The frontend's player stays in charge of the queue, play reports and so on; while casting, its
audio element is swapped for one that forwards to this module (src/cast.ts). The device fetches
each track itself from the stream server's home-network site (StreamServer.open_lan), so the
Deck only relays bytes, and its EQ, levelling and crossfade don't apply.

Two kinds of device: Google Cast (castv2.py; Chromecast, Google/Nest speakers, TVs and soundbars
with Cast) and UPnP/DLNA renderers (upnp.py; Sonos, AV receivers, smart TVs). Both report
{"state": "playing"|"paused"|"buffering"|"idle", "position", "duration"?, "ended"?, "error"?},
passed on to the frontend as "cast_status" events; {"gone": reason} means the session ended
without the user ending it (the device went away, or something else started playing on it).
"""
import asyncio
from typing import Any, Awaitable, Callable, Optional, Union

import aiohttp

from .. import debuglog
from ..services import MusicService, ServiceError
from . import mdns, upnp
from .castv2 import CastError, Chromecast
from .upnp import Renderer, UpnpError

GOOGLECAST = "_googlecast._tcp.local"
SEARCH_SECONDS = 2.5
CONTROLS = {"play", "pause", "seek", "stop", "volume"}

Device = Union[Chromecast, Renderer]


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
        self.device: Optional[Device] = None
        self.info: Optional[dict] = None
        self.lock = asyncio.Lock()
        self._sending: set[asyncio.Task] = set()

    # --- finding devices ---

    async def search(self) -> list[dict]:
        """Devices on the network, by name: [{"id", "name", "model", "kind"}]."""
        casts, renderers = await asyncio.gather(mdns.browse(GOOGLECAST, SEARCH_SECONDS),
                                                upnp.search(SEARCH_SECONDS), return_exceptions=True)
        found: dict[str, dict] = {}
        for entry in casts if isinstance(casts, list) else []:
            txt = entry.get("txt") or {}
            device_id = "cast:" + (txt.get("id") or entry["name"])
            found[device_id] = {"id": device_id, "name": txt.get("fn") or entry["name"].split(".")[0],
                                "model": txt.get("md") or "", "kind": "chromecast",
                                "host": entry["host"], "port": entry["port"]}
        if isinstance(renderers, dict):
            described = await asyncio.gather(*(upnp.describe(self.http, location, host)
                                               for location, host in renderers.items()))
            cast_hosts = {d["host"] for d in found.values()}
            for device in described:
                # a device that does both is better driven over Cast
                if device and device["host"] not in cast_hosts:
                    found[device["id"]] = {**device, "kind": "upnp"}
        for error in (casts, renderers):
            if isinstance(error, BaseException):
                debuglog.debug("cast", "search failed", error=repr(error))
        if self.info:
            found.setdefault(self.info["id"], self.info)
        self.found = found
        return sorted(({k: d[k] for k in ("id", "name", "model", "kind")} for d in found.values()),
                      key=lambda d: d["name"].lower())

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
            if info["kind"] == "chromecast":
                device: Device = Chromecast(info["host"], info["port"], on_status, on_gone)
            else:
                device = Renderer(info, self.http, on_status, on_gone)
            mine.append(device)
            try:
                await device.connect()
                await self.server.open_lan(info["host"])
            except (CastError, UpnpError, OSError) as e:
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
        if not service.castable:
            raise ServiceError(f"{service.display_name} can't play on other devices from here")
        track_id, art = track.get("id"), track.get("art")
        if not isinstance(track_id, str) or (art is not None and not isinstance(art, str)):
            raise ServiceError("Bad track")
        async with self.lock:
            device = self._connected()
            link = await self.server.cast_link(instance_id, track_id, art, compatible=device.kind == "upnp")
            if link["hls"] and device.kind == "upnp":
                raise ServiceError("This device can't play this kind of stream")
            meta = {"title": _text(track.get("title")), "artist": _text(track.get("artist")),
                    "album": _text(track.get("album")), "art": link["art"], "segments": link["segments"],
                    "duration": track["duration"] if isinstance(track.get("duration"), (int, float)) else None}
            debuglog.debug("cast", "load", type=link["type"], hls=link["hls"], start=start)
            try:
                await device.load(link["url"], link["type"], meta, float(start or 0), bool(autoplay),
                                  bool(track.get("live")))
            except (CastError, UpnpError) as e:
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
            except (CastError, UpnpError) as e:
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
