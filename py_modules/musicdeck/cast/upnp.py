"""
UPnP/DLNA media renderers: Sonos, AV receivers, smart TVs and the like.

Found with an SSDP search; controlled with SOAP calls to the AVTransport (load, play, pause, seek)
and RenderingControl (volume) services named in the device's description. Renderers don't push
their state reliably, so it's polled once a second while connected.
"""
import asyncio
import re
import socket
import time
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import aiohttp

SSDP = ("239.255.255.250", 1900)
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING = "urn:schemas-upnp-org:service:RenderingControl:1"
MAX_XML = 256 * 1024  # a device description or SOAP reply is a few KiB
POLL_EVERY = 1.0
# DLNA flags: byte-range seeking, streaming transfer; renderers like Samsung's refuse media without them
DLNA_FEATURES = "DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000"
LOCATION = re.compile(rb"^location:\s*(\S+)", re.I | re.M)


class UpnpError(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml(text: bytes) -> ElementTree.Element:
    if len(text) > MAX_XML:
        raise UpnpError("The device's reply was too large")
    if b"<!DOCTYPE" in text or b"<!ENTITY" in text:
        raise UpnpError("The device's reply wasn't plain XML")  # no entity tricks from the network
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise UpnpError("The device's reply wasn't valid XML")


class _Searcher(asyncio.DatagramProtocol):
    def __init__(self):
        self.locations: dict[str, str] = {}

    def datagram_received(self, data, addr):
        match = LOCATION.search(data)
        if match:
            self.locations.setdefault(match.group(1).decode(errors="replace"), addr[0])


async def search(seconds: float = 2.0) -> dict[str, str]:
    """Description URLs of renderers that answered, with the address each answer came from."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.bind(("0.0.0.0", 0))
    transport, searcher = await loop.create_datagram_endpoint(_Searcher, sock=sock)
    message = (f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
               f"MX: {max(int(seconds) - 1, 1)}\r\nST: {RENDERER}\r\n\r\n").encode()
    try:
        for _ in range(2):
            transport.sendto(message, SSDP)
            await asyncio.sleep(seconds / 2)
    finally:
        transport.close()
    return searcher.locations


async def describe(session: aiohttp.ClientSession, location: str, answered_from: str) -> Optional[dict]:
    """A renderer's name and control URLs from its description, or None if it isn't usable."""
    # the description must live on the device that answered, so a stray reply can't point us elsewhere
    if urlparse(location).hostname != answered_from or urlparse(location).scheme != "http":
        return None
    try:
        async with session.get(location, timeout=aiohttp.ClientTimeout(total=4)) as response:
            root = _xml(await response.content.read(MAX_XML + 1))
    except (aiohttp.ClientError, asyncio.TimeoutError, UpnpError):
        return None
    device = next((e for e in root.iter() if _local(e.tag) == "device"), None)
    if device is None:
        return None
    info = {_local(e.tag): (e.text or "").strip() for e in device if len(e) == 0}
    controls = {}
    for service in root.iter():
        if _local(service.tag) != "service":
            continue
        fields = {_local(e.tag): (e.text or "").strip() for e in service}
        if fields.get("serviceType") in (AV_TRANSPORT, RENDERING) and fields.get("controlURL"):
            url = urljoin(location, fields["controlURL"])
            if urlparse(url).hostname == answered_from:
                controls[fields["serviceType"]] = url
    if AV_TRANSPORT not in controls:
        return None
    return {
        "id": "upnp:" + (info.get("UDN") or location),
        "name": info.get("friendlyName") or answered_from,
        "model": " ".join(x for x in (info.get("manufacturer"), info.get("modelName")) if x),
        "host": answered_from,
        "transport": controls[AV_TRANSPORT],
        "rendering": controls.get(RENDERING),
    }


def _seconds(clock: str) -> float:
    """"H:MM:SS(.fff)" (UPnP's time format) in seconds; 0 for "NOT_IMPLEMENTED" and the like."""
    try:
        parts = [float(x) for x in clock.split(":")]
    except ValueError:
        return 0.0
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def _clock(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def didl(url: str, content_type: str, meta: dict) -> str:
    """DIDL-Lite metadata for SetAVTransportURI: what the renderer shows and how to fetch the audio."""
    art = f"<upnp:albumArtURI>{escape(meta['art'])}</upnp:albumArtURI>" if meta.get("art") else ""
    duration = f' duration="{_clock(meta["duration"])}.000"' if meta.get("duration") else ""
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
        f"<dc:title>{escape(meta.get('title') or 'MusicDeck')}</dc:title>"
        f"<upnp:artist>{escape(meta.get('artist') or '')}</upnp:artist>"
        f"<upnp:album>{escape(meta.get('album') or '')}</upnp:album>{art}"
        "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
        f'<res protocolInfo="http-get:*:{escape(content_type)}:{DLNA_FEATURES}"{duration}>{escape(url)}</res>'
        "</item></DIDL-Lite>"
    )


class Renderer:
    """One UPnP renderer, controlled the same way as castv2.Chromecast (see cast.CastManager)."""

    kind = "upnp"

    def __init__(self, device: dict, session: aiohttp.ClientSession, on_status: Callable[[dict], None],
                 on_gone: Callable[[str], None]):
        self.device, self.session = device, session
        self.on_status, self.on_gone = on_status, on_gone
        self.volume = 1.0
        self.poller: Optional[asyncio.Task] = None
        self.loaded = False
        # what we last asked for, so a renderer that stops by itself can be told apart from our stop
        self.want_playing = False
        self.started_at = 0.0
        self.duration = 0.0
        self.failures = 0

    async def connect(self) -> None:
        await self._transport_info()  # answers at all?
        if self.device.get("rendering"):
            try:
                reply = await self._call(RENDERING, "GetVolume", {"Channel": "Master"})
                self.volume = int(reply.get("CurrentVolume") or 100) / 100
            except (UpnpError, ValueError):
                pass
        self.poller = asyncio.create_task(self._poll())

    async def close(self, stop_player: bool = True) -> None:
        if self.poller:
            self.poller.cancel()
        if stop_player and self.loaded:
            try:
                await self._call(AV_TRANSPORT, "Stop", {})
            except UpnpError:
                pass

    async def load(self, url: str, content_type: str, meta: dict, start: float, autoplay: bool, live: bool) -> None:
        self.want_playing = False
        try:
            await self._call(AV_TRANSPORT, "Stop", {})
        except UpnpError:
            pass  # some renderers refuse Stop when nothing's loaded
        await self._call(AV_TRANSPORT, "SetAVTransportURI",
                         {"CurrentURI": url, "CurrentURIMetaData": didl(url, content_type, meta)})
        self.loaded = True
        self.duration = float(meta.get("duration") or 0)
        if autoplay:
            await self.play()
        if start > 1 and not live:
            await self._wait_until_playing()
            await self.seek(start)

    async def play(self) -> None:
        await self._call(AV_TRANSPORT, "Play", {"Speed": "1"})
        self.want_playing, self.started_at = True, time.time()

    async def pause(self) -> None:
        self.want_playing = False
        await self._call(AV_TRANSPORT, "Pause", {})

    async def seek(self, seconds: float) -> None:
        await self._call(AV_TRANSPORT, "Seek", {"Unit": "REL_TIME", "Target": _clock(seconds)})

    async def stop(self) -> None:
        self.want_playing = False
        await self._call(AV_TRANSPORT, "Stop", {})

    async def set_volume(self, level: float) -> None:
        self.volume = min(max(level, 0.0), 1.0)
        if self.device.get("rendering"):
            await self._call(RENDERING, "SetVolume", {"Channel": "Master", "DesiredVolume": str(round(self.volume * 100))})

    async def _wait_until_playing(self) -> None:
        for _ in range(20):  # seeking before the renderer has opened the stream is ignored by most
            if (await self._transport_info()) == "PLAYING":
                return
            await asyncio.sleep(0.5)

    async def _transport_info(self) -> str:
        reply = await self._call(AV_TRANSPORT, "GetTransportInfo", {})
        return reply.get("CurrentTransportState") or ""

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(POLL_EVERY)
            try:
                state = await self._transport_info()
                position = await self._call(AV_TRANSPORT, "GetPositionInfo", {})
                self.failures = 0
            except UpnpError:
                self.failures += 1
                if self.failures >= 5:
                    self.on_gone("The device stopped answering")
                    return
                continue
            if not self.loaded:
                continue
            report: dict[str, Any] = {
                "state": {"PLAYING": "playing", "PAUSED_PLAYBACK": "paused", "TRANSITIONING": "buffering"}.get(
                    state, "idle"),
                "position": _seconds(position.get("RelTime") or ""),
            }
            duration = _seconds(position.get("TrackDuration") or "")
            if duration > 0:
                report["duration"] = duration
            if report["state"] == "idle" and self.want_playing and time.time() - self.started_at > 3:
                # stopped by itself while we wanted it playing: the track finished (or failed near the start)
                self.want_playing = False
                self.loaded = False
                report["ended"] = True
            self.on_status(report)

    async def _call(self, service: str, action: str, arguments: dict[str, str]) -> dict[str, str]:
        url = self.device["transport"] if service == AV_TRANSPORT else self.device.get("rendering")
        if not url:
            raise UpnpError("Not supported by this device")
        body = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in {"InstanceID": "0", **arguments}.items())
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}">{body}</u:{action}></s:Body></s:Envelope>'
        )
        headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{service}#{action}"'}
        try:
            async with self.session.post(url, data=envelope.encode(), headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=5)) as response:
                text = await response.content.read(MAX_XML + 1)
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise UpnpError(f"The device didn't answer ({e.__class__.__name__})")
        root = _xml(text)
        if status >= 400:
            code = next((e.text for e in root.iter() if _local(e.tag) == "errorCode"), None)
            raise UpnpError(f"The device refused {action}" + (f" (error {code})" if code else ""))
        reply = next((e for e in root.iter() if _local(e.tag) == f"{action}Response"), None)
        return {_local(e.tag): (e.text or "") for e in reply} if reply is not None else {}
