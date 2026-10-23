"""A Soulseek client: signing in, searching, and downloading files from other users, in pure Python
(Decky's Python can't load the compiled pieces existing clients depend on). The protocol is the one
Nicotine+ documents (https://nicotine-plus.org/doc/SLSKPROTOCOL.html).

- One connection to the server: login, the port peers can reach us on, searches, peers' addresses,
  and the server's go-betweens for peers that can't reach each other directly.
- Connections between peers: "P" for messages (search results, upload queues), "F" for a file. Either
  side opens one directly; when that can't work (a router in the way), the other is asked through the
  server to connect instead and announces itself with PierceFireWall and the token the asker chose.
- A download: QueueUpload; when its turn comes the uploader sends TransferRequest, we accept with
  TransferResponse, and it opens an F connection to send the file.

What peers send is not trusted: message sizes are capped, search results are parsed defensively, and a
file's name only decides where it's saved after cleaning (dest() in the caller). Nothing is shared: we
tell the server so (0 folders, 0 files), and asks for our files go unanswered.
"""
import asyncio
import hashlib
import logging
import os
import secrets
import socket
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

SERVER = ("server.slsknet.org", 2242)
VERSION, MINOR = 160, 1
MAX_MESSAGE = 16 * 1024 * 1024  # a search response with thousands of files is well under this
CONNECT_TIMEOUT = 8.0
INDIRECT_TIMEOUT = 20.0

_log = logging.getLogger("musicdeck")


class SoulseekError(Exception):
    """Something the user is told: a refused sign-in, a peer that can't be reached, a denied upload."""


# --- encoding ---

def u8(v: int) -> bytes:
    return struct.pack("<B", v)


def u32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def u64(v: int) -> bytes:
    return struct.pack("<Q", v)


def string(v: str) -> bytes:
    data = v.encode("utf-8")
    return u32(len(data)) + data


def message(code: int, body: bytes = b"", code_size: int = 4) -> bytes:
    head = u32(code) if code_size == 4 else u8(code)
    return u32(len(head) + len(body)) + head + body


class Reader:
    """Reads a message's fields in order; running past its end raises SoulseekError."""

    def __init__(self, data: bytes):
        self.data = data
        self.at = 0

    def _take(self, n: int) -> bytes:
        if self.at + n > len(self.data):
            raise SoulseekError("A message ended too soon")
        part = self.data[self.at:self.at + n]
        self.at += n
        return part

    def u8(self) -> int:
        return self._take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self._take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self._take(8))[0]

    def bool(self) -> bool:
        return self.u8() != 0

    def string(self) -> str:
        return self._take(self.u32()).decode("utf-8", "replace")

    def ip(self) -> str:
        # a uint32 whose bytes are the address back to front
        return socket.inet_ntoa(self._take(4)[::-1])


async def read_message(reader: asyncio.StreamReader, code_size: int = 4) -> tuple[int, bytes]:
    length = struct.unpack("<I", await reader.readexactly(4))[0]
    if length < code_size or length > MAX_MESSAGE:
        raise SoulseekError("A message of an impossible size")
    data = await reader.readexactly(length)
    code = struct.unpack("<I", data[:4])[0] if code_size == 4 else data[0]
    return code, data[code_size:]


# --- search results ---

@dataclass
class SharedFile:
    user: str
    path: str  # as the user's client names it: "@@share\\Artist\\Album\\01 Song.flac"
    size: int
    bitrate: int = 0
    duration: int = 0
    sample_rate: int = 0
    bit_depth: int = 0
    free_slot: bool = False
    speed: int = 0  # bytes a second, as the user's uploads have averaged
    queue: int = 0

    @property
    def folder(self) -> str:
        return self.path.replace("/", "\\").rpartition("\\")[0]

    @property
    def name(self) -> str:
        return self.path.replace("/", "\\").rpartition("\\")[2]


def parse_search_response(payload: bytes) -> tuple[str, int, list[SharedFile]]:
    """A FileSearchResponse (zlib-compressed): who answered, the search's token, and their files."""
    r = Reader(zlib.decompressobj().decompress(payload, MAX_MESSAGE))
    user, token, count = r.string(), r.u32(), r.u32()
    files = []
    for _ in range(min(count, 5000)):
        r.u8()
        path, size = r.string(), r.u64()
        r.string()  # the extension: the path has it
        attrs = {}
        for _ in range(min(r.u32(), 16)):
            code, value = r.u32(), r.u32()
            attrs[code] = value
        files.append(SharedFile(user, path, size, bitrate=attrs.get(0, 0), duration=attrs.get(1, 0),
                                sample_rate=attrs.get(4, 0), bit_depth=attrs.get(5, 0)))
    free, speed, queue = r.bool(), r.u32(), r.u32()
    for f in files:
        f.free_slot, f.speed, f.queue = free, speed, queue
    return user, token, files


# --- the client ---

@dataclass
class Download:
    user: str
    path: str
    size: int
    dest: str
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    token: Optional[int] = None
    received: int = 0
    place: Optional[int] = None  # its place in the user's upload queue, when they say


class Client:
    def __init__(self, username: str, password: str, port: int = 2234,
                 server: Optional[tuple[str, int]] = None, on_change: Optional[Callable[[], None]] = None):
        self.username = username
        self.password = password
        self.port = port
        self.server_address = server or SERVER
        self.on_change = on_change or (lambda: None)
        self.server_writer: Optional[asyncio.StreamWriter] = None
        self.listener: Optional[asyncio.base_events.Server] = None
        self.tasks: set[asyncio.Task] = set()
        self.connected = False
        self.searches: dict[int, list[SharedFile]] = {}
        self.addresses: dict[str, asyncio.Future] = {}
        self.indirect: dict[int, asyncio.Future] = {}
        self.peers: dict[str, asyncio.StreamWriter] = {}
        self.downloads: dict[tuple[str, str], Download] = {}
        self.transfers: dict[int, Download] = {}

    def _spawn(self, work: Any) -> None:
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # --- the server ---

    async def connect(self) -> None:
        """Signs in (a new name is registered on first sign-in) and starts listening for peers."""
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(*self.server_address), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            raise SoulseekError(f"Couldn't reach the Soulseek server: {e}") from e
        digest = hashlib.md5((self.username + self.password).encode("utf-8")).hexdigest()
        writer.write(message(1, string(self.username) + string(self.password) + u32(VERSION) + string(digest)
                             + u32(MINOR)))
        await writer.drain()
        try:
            code, payload = await asyncio.wait_for(read_message(reader), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as e:
            writer.close()
            raise SoulseekError("The Soulseek server didn't answer the sign-in") from e
        r = Reader(payload)
        if code != 1 or not r.bool():
            reason = r.string() if code == 1 else "no answer"
            writer.close()
            raise SoulseekError({"INVALIDPASS": "Wrong password for that Soulseek name",
                                 "INVALIDUSERNAME": "Soulseek doesn't accept that name"}.get(reason, f"Soulseek refused: {reason}"))
        self.server_writer = writer
        self.connected = True
        await self._listen()
        # where peers reach us; nothing shared; not part of the network that passes searches along
        self._send(message(2, u32(self.port)) + message(35, u32(0) + u32(0)) + message(71, u8(1)))
        self._spawn(self._read_server(reader))
        self.on_change()

    async def _listen(self) -> None:
        for port in range(self.port, self.port + 20):
            try:
                self.listener = await asyncio.start_server(self._incoming, "0.0.0.0", port)
                self.port = port
                return
            except OSError:
                continue
        _log.info("Soulseek: no port to listen on; peers will be asked to be reached instead")

    def _send(self, data: bytes) -> None:
        if self.server_writer and not self.server_writer.is_closing():
            self.server_writer.write(data)

    async def _read_server(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                code, payload = await read_message(reader)
                r = Reader(payload)
                if code == 3:  # GetPeerAddress
                    user, ip, port = r.string(), r.ip(), r.u32()
                    found = self.addresses.pop(user, None)
                    if found and not found.done():
                        found.set_result((ip, port))
                elif code == 18:  # ConnectToPeer: they can't reach us; we reach them
                    user, kind, ip, port, token = r.string(), r.string(), r.ip(), r.u32(), r.u32()
                    self._spawn(self._pierce(user, kind, ip, port, token))
                elif code == 1001:  # CantConnectToPeer
                    found = self.indirect.pop(r.u32(), None)
                    if found and not found.done():
                        found.set_exception(SoulseekError("That user can't be reached right now"))
                elif code == 41:  # Relogged: signed in elsewhere
                    raise SoulseekError("Signed in to Soulseek somewhere else")
        except (OSError, asyncio.IncompleteReadError, SoulseekError) as e:
            _log.info(f"Soulseek: disconnected from the server ({e})")
        finally:
            self.connected = False
            self.on_change()

    async def close(self) -> None:
        self.connected = False
        for task in list(self.tasks):
            task.cancel()
        if self.listener:
            self.listener.close()
        if self.server_writer:
            self.server_writer.close()
        for writer in self.peers.values():
            writer.close()
        for d in self.downloads.values():
            if not d.done.done():
                d.done.set_exception(SoulseekError("Disconnected from Soulseek"))

    # --- searching ---

    async def search(self, query: str, wait: float = 6.0) -> list[SharedFile]:
        """Everyone's answers to a search, as they come in over `wait` seconds."""
        if not self.connected:
            raise SoulseekError("Not connected to Soulseek")
        token = secrets.randbelow(2**31)
        self.searches[token] = []
        self._send(message(26, u32(token) + string(query)))
        await asyncio.sleep(wait)
        return self.searches.pop(token, [])

    # --- peers ---

    async def _incoming(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """A peer connecting to us: its PeerInit (who, which kind), or PierceFireWall answering our ask."""
        try:
            code, payload = await asyncio.wait_for(read_message(reader, code_size=1), CONNECT_TIMEOUT)
            r = Reader(payload)
            if code == 1:
                user, kind = r.string(), r.string()
                await self._serve(user, kind, reader, writer)
            elif code == 0:
                found = self.indirect.pop(r.u32(), None)
                if found and not found.done():
                    found.set_result((reader, writer))
                    return  # the asker reads it
                writer.close()
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, SoulseekError):
            writer.close()

    async def _pierce(self, user: str, kind: str, ip: str, port: int, token: int) -> None:
        """Connects to a peer the server says wants us to, announcing the token it chose."""
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            self._send(message(1001, u32(token) + string(user)))
            return
        writer.write(message(0, u32(token), code_size=1))
        await self._serve(user, kind, reader, writer)

    async def _serve(self, user: str, kind: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if kind == "P":
                self.peers.setdefault(user, writer)
                await self._read_peer(user, reader, writer)
            elif kind == "F":
                await self._receive_file(reader, writer)
        finally:
            if self.peers.get(user) is writer:
                del self.peers[user]
            writer.close()

    async def _peer(self, user: str) -> asyncio.StreamWriter:
        """A message connection to a user: directly, or (a router in the way) asked through the server."""
        writer = self.peers.get(user)
        if writer and not writer.is_closing():
            return writer
        loop = asyncio.get_running_loop()
        self.addresses[user] = loop.create_future()
        self._send(message(3, string(user)))
        try:
            ip, port = await asyncio.wait_for(self.addresses[user], CONNECT_TIMEOUT)
            if port:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
                writer.write(message(1, string(self.username) + string("P") + u32(0), code_size=1))
                self.peers[user] = writer
                self._spawn(self._serve(user, "P", reader, writer))
                return writer
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            self.addresses.pop(user, None)
        token = secrets.randbelow(2**31)
        self.indirect[token] = loop.create_future()
        self._send(message(18, u32(token) + string(user) + string("P")))
        try:
            reader, writer = await asyncio.wait_for(self.indirect[token], INDIRECT_TIMEOUT)
        except asyncio.TimeoutError:
            raise SoulseekError(f"{user} can't be reached right now")
        finally:
            self.indirect.pop(token, None)
        self.peers[user] = writer
        self._spawn(self._serve(user, "P", reader, writer))
        return writer

    async def _read_peer(self, user: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            code, payload = await read_message(reader)
            if code == 9:  # FileSearchResponse
                try:
                    _, token, files = parse_search_response(payload)
                except (SoulseekError, zlib.error):
                    continue
                if token in self.searches:
                    for f in files:
                        f.user = user  # who sent it, not who it says it is
                    self.searches[token].extend(files)
                continue
            r = Reader(payload)
            if code == 40:  # TransferRequest: an upload to us is ready
                direction, token, path = r.u32(), r.u32(), r.string()
                size = r.u64() if direction == 1 else 0
                d = self.downloads.get((user, path))
                if direction == 1 and d and not d.done.done():
                    d.token, d.size = token, size or d.size
                    self.transfers[token] = d
                    writer.write(message(41, u32(token) + u8(1)))
                else:
                    writer.write(message(41, u32(token) + u8(0) + string("Cancelled")))
            elif code == 44:  # PlaceInQueueResponse
                d = self.downloads.get((user, r.string()))
                if d:
                    d.place = r.u32()
                    self.on_change()
            elif code in (46, 50):  # UploadFailed, UploadDenied
                path = r.string()
                reason = r.string() if code == 50 else "the upload failed"
                d = self.downloads.get((user, path))
                if d and not d.done.done():
                    d.done.set_exception(SoulseekError(f"{user}: {reason}"))

    async def _receive_file(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        token = struct.unpack("<I", await asyncio.wait_for(reader.readexactly(4), CONNECT_TIMEOUT))[0]
        d = self.transfers.pop(token, None)
        if not d:
            return
        writer.write(u64(0))
        partial = d.dest + ".part"
        os.makedirs(os.path.dirname(d.dest), exist_ok=True)
        try:
            with open(partial, "wb") as f:
                while d.received < d.size:
                    chunk = await asyncio.wait_for(reader.read(min(256 * 1024, d.size - d.received)), 60)
                    if not chunk:
                        raise SoulseekError(f"{d.user} stopped sending")
                    f.write(chunk)
                    d.received += len(chunk)
            os.replace(partial, d.dest)
            if not d.done.done():
                d.done.set_result(d.dest)
        except (OSError, asyncio.TimeoutError, SoulseekError) as e:
            if os.path.exists(partial):
                os.remove(partial)
            if not d.done.done():
                d.done.set_exception(e if isinstance(e, SoulseekError) else SoulseekError(f"The download stopped: {e}"))

    # --- downloading ---

    async def download(self, user: str, path: str, size: int, dest: str, wait: Optional[float] = None) -> str:
        """Asks `user` for the file, and waits (`wait` seconds at most, else for its turn) until it's
        saved at `dest`. A download already under way is waited for, not asked for again."""
        key = (user, path)
        d = self.downloads.get(key)
        if not d or d.done.done():
            d = self.downloads[key] = Download(user, path, size, dest)
            writer = await self._peer(user)
            writer.write(message(43, string(path)))
            self.on_change()
        try:
            return await asyncio.wait_for(asyncio.shield(d.done), wait)
        except asyncio.TimeoutError:
            # still in their queue: ask where, so it can be said
            writer = self.peers.get(user)
            if writer:
                writer.write(message(51, string(path)))
            raise SoulseekError(f"{user} hasn't started sending yet" +
                                (f" (number {d.place} in their queue)" if d.place else "")) from None
        finally:
            if d.done.done():
                self.downloads.pop(key, None)
                self.on_change()
