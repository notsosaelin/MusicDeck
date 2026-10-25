"""Asking the home router to forward a port to the Deck (UPnP Internet Gateway Device), so Soulseek users
can reach it: more search results, downloads from users behind their own routers, and uploads. Routers
with UPnP turned off just don't answer; nothing else changes then.

SSDP finds the router; its description names the WANIPConnection (or WANPPPConnection) control URL;
AddPortMapping asks for the port for an hour at a time (renewed while it's wanted); DeletePortMapping
gives it back. The XML is read with cast.upnp's parser (expat, no entities).
"""
import asyncio
import socket
from typing import Optional
from urllib.parse import urljoin, urlparse

import aiohttp

from .cast.upnp import SSDP, UpnpError, _local, _Searcher, _xml, read_all

IGD = "urn:schemas-upnp-org:device:InternetGatewayDevice:1"
SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:1", "urn:schemas-upnp-org:service:WANIPConnection:2",
            "urn:schemas-upnp-org:service:WANPPPConnection:1")
LEASE = 3600
MAX_DESCRIPTION = 256 * 1024


async def _search(seconds: float = 2.0) -> dict[str, str]:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.bind(("0.0.0.0", 0))
    transport, searcher = await loop.create_datagram_endpoint(_Searcher, sock=sock)
    message = (f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
               f"MX: 1\r\nST: {IGD}\r\n\r\n").encode()
    try:
        for _ in range(2):
            transport.sendto(message, SSDP)
            await asyncio.sleep(seconds / 2)
    finally:
        transport.close()
    return searcher.locations


def _own_address(towards: str) -> str:
    """The Deck's address on the network the router is on."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((towards, 1900))
        return s.getsockname()[0]


class PortMapping:
    def __init__(self, session: aiohttp.ClientSession, port: int, description: str = "MusicDeck"):
        self.session = session
        self.port = port
        self.description = description
        self.control: Optional[tuple[str, str]] = None  # (control URL, service type)
        self.renewing: Optional[asyncio.Task] = None

    async def _find(self) -> Optional[tuple[str, str]]:
        for location, answered_from in (await _search()).items():
            # the description and control must be on the router that answered
            if urlparse(location).hostname != answered_from or urlparse(location).scheme != "http":
                continue
            try:
                async with self.session.get(location, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    root = _xml(await read_all(resp, MAX_DESCRIPTION))
            except (aiohttp.ClientError, asyncio.TimeoutError, UpnpError):
                continue
            for service in root.iter():
                if _local(service.tag) != "service":
                    continue
                fields = {_local(e.tag): (e.text or "").strip() for e in service}
                if fields.get("serviceType") in SERVICES and fields.get("controlURL"):
                    url = urljoin(location, fields["controlURL"])
                    if urlparse(url).hostname == answered_from:
                        return url, fields["serviceType"]
        return None

    async def _soap(self, action: str, args: dict[str, str]) -> bool:
        assert self.control
        url, service = self.control
        body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
        envelope = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    f'<u:{action} xmlns:u="{service}">{body}</u:{action}></s:Body></s:Envelope>')
        try:
            async with self.session.post(url, data=envelope.encode(), timeout=aiohttp.ClientTimeout(total=5),
                                         headers={"Content-Type": 'text/xml; charset="utf-8"',
                                                  "SOAPAction": f'"{service}#{action}"'}) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def open(self) -> bool:
        """Asks for the port, and keeps renewing it; False when no router would."""
        self.control = self.control or await self._find()
        if not self.control:
            return False
        try:
            me = _own_address(urlparse(self.control[0]).hostname or "")
        except OSError:
            return False
        args = {"NewRemoteHost": "", "NewExternalPort": str(self.port), "NewProtocol": "TCP",
                "NewInternalPort": str(self.port), "NewInternalClient": me, "NewEnabled": "1",
                "NewPortMappingDescription": self.description, "NewLeaseDuration": str(LEASE)}
        if not await self._soap("AddPortMapping", args):
            # some routers refuse leases: ask for one that lasts until it's taken away
            args["NewLeaseDuration"] = "0"
            if not await self._soap("AddPortMapping", args):
                return False
        if self.renewing is None:
            self.renewing = asyncio.ensure_future(self._renew())
        return True

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(LEASE - 300)
            await self.open()

    async def close(self) -> None:
        if self.renewing:
            self.renewing.cancel()
        if self.control:
            await self._soap("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": str(self.port),
                                                   "NewProtocol": "TCP"})
