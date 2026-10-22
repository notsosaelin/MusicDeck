"""
A one-shot multicast DNS browser (RFC 6762 §5.1), just enough to find Google Cast devices.

The query goes out from an ordinary port and asks for unicast replies, so it works alongside the
system's own mDNS responder (Avahi) without binding port 5353 or needing a library.
"""
import asyncio
import socket
import struct
from typing import Optional

MDNS = ("224.0.0.251", 5353)
PTR, TXT, A, SRV = 12, 16, 1, 33
QU = 0x8000  # "unicast response, please"
MAX_POINTERS = 20  # a name compressed through more pointers than this is malformed (or a loop)


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode()
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def query(service: str) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    return header + _encode_name(service) + struct.pack("!HH", PTR, 1 | QU)


def _read_name(data: bytes, at: int) -> tuple[str, int]:
    """A (possibly compressed) name at `at`: the name and the offset just past it."""
    labels, end, jumps = [], None, 0
    while True:
        length = data[at]
        if length & 0xC0 == 0xC0:
            jumps += 1
            if jumps > MAX_POINTERS:
                raise ValueError("pointer loop")
            if end is None:
                end = at + 2
            at = ((length & 0x3F) << 8) | data[at + 1]
            continue
        at += 1
        if length == 0:
            break
        labels.append(data[at:at + length].decode("utf-8", "replace"))
        at += length
    return ".".join(labels), end if end is not None else at


def parse(data: bytes) -> list[tuple[str, int, object]]:
    """Every record in a response as (name, type, value); value is a name (PTR), (target, port)
    (SRV), {key: value} (TXT) or an address (A). Raises ValueError (or IndexError) if malformed."""
    _, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", data)
    if not flags & 0x8000:
        return []  # a query, not a response
    at = 12
    for _ in range(qd):
        _, at = _read_name(data, at)
        at += 4
    records = []
    for _ in range(an + ns + ar):
        name, at = _read_name(data, at)
        rtype, _, _, length = struct.unpack_from("!HHIH", data, at)
        at += 10
        body = data[at:at + length]
        if rtype == PTR:
            records.append((name, rtype, _read_name(data, at)[0]))
        elif rtype == SRV:
            port = struct.unpack_from("!H", body, 4)[0]
            records.append((name, rtype, (_read_name(data, at + 6)[0], port)))
        elif rtype == TXT:
            values, i = {}, 0
            while i < len(body):
                n = body[i]
                entry = body[i + 1:i + 1 + n].decode("utf-8", "replace")
                key, _, value = entry.partition("=")
                values[key] = value
                i += 1 + n
            records.append((name, rtype, values))
        elif rtype == A and length == 4:
            records.append((name, rtype, socket.inet_ntoa(body)))
        at += length
    return records


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.records: list[tuple[str, int, object]] = []

    def datagram_received(self, data, addr):
        try:
            self.records.extend(parse(data))
        except (ValueError, IndexError, struct.error):
            pass  # someone else's malformed packet


async def browse(service: str, seconds: float = 2.0) -> list[dict]:
    """Instances of `service` (e.g. "_googlecast._tcp.local") as
    {"name", "host", "port", "txt"}; asks three times over `seconds`, since replies get lost."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.bind(("0.0.0.0", 0))
    transport, collector = await loop.create_datagram_endpoint(_Collector, sock=sock)
    try:
        for _ in range(3):
            transport.sendto(query(service), MDNS)
            await asyncio.sleep(seconds / 3)
    finally:
        transport.close()
    return _instances(collector.records, service)


def _instances(records, service: str) -> list[dict]:
    service = service.lower()
    found: dict[str, dict] = {}
    srv: dict[str, tuple[str, int]] = {}
    txt: dict[str, dict] = {}
    addresses: dict[str, str] = {}
    for name, rtype, value in records:
        key = name.lower()
        if rtype == PTR and key == service:
            found.setdefault(str(value).lower(), {"name": str(value)})
        elif rtype == SRV:
            srv[key] = value  # type: ignore[assignment]
        elif rtype == TXT:
            txt[key] = value  # type: ignore[assignment]
        elif rtype == A:
            addresses[key] = str(value)
    instances = []
    for key, entry in found.items():
        target: Optional[tuple[str, int]] = srv.get(key)
        host = addresses.get(target[0].lower()) if target else None
        if target and host:
            instances.append({**entry, "host": host, "port": target[1], "txt": txt.get(key, {})})
    return instances
