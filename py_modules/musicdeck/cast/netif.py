"""
Sending a discovery query (SSDP, mDNS) out of every network the computer is on.

A socket bound to 0.0.0.0 sends multicast through one interface only, the system's choice: on a PC with
WSL, Hyper-V, a VPN or Tailscale that's often a virtual adapter that reaches nothing, and no speaker or TV
is found. So a query also goes out from each of the computer's own IPv4 addresses (loopback and
link-local left out), and answers to any of them are counted.
"""
import asyncio
import ipaddress
import socket
from typing import Callable


def local_addresses() -> list[str]:
    """The computer's IPv4 addresses that may face a home network."""
    found: set[str] = set()
    try:
        found.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    # the address of the default route (no packet is sent: a UDP "connect" only picks the interface)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    usable = []
    for address in found:
        ip = ipaddress.ip_address(address)
        if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
            usable.append(address)
    return sorted(usable)


async def send_everywhere(protocol: Callable[[], asyncio.DatagramProtocol], payload: bytes, target: tuple[str, int],
                          times: int, seconds: float, ttl: int) -> asyncio.DatagramProtocol:
    """Sends `payload` to `target` `times` times over `seconds`, from the default socket and from every
    local address; every answer goes to the one protocol instance returned."""
    loop = asyncio.get_running_loop()
    shared = protocol()
    transports = []
    for local in ["0.0.0.0", *local_addresses()]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
            if local != "0.0.0.0":
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
            sock.bind((local, 0))
            transport, _ = await loop.create_datagram_endpoint(lambda: shared, sock=sock)
            transports.append(transport)
        except OSError:
            sock.close()  # an address that went away, or one that can't multicast: the others still go
    try:
        for _ in range(times):
            for transport in transports:
                try:
                    transport.sendto(payload, target)
                except OSError:
                    pass
            await asyncio.sleep(seconds / times)
    finally:
        for transport in transports:
            transport.close()
    return shared
