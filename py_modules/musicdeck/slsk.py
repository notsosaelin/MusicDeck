"""A Soulseek client: signing in, searching, downloading, browsing other users, and (when the user picks
folders to share) sharing: answering searches and browses, and uploading. Pure Python (Decky's Python
can't load the compiled pieces existing clients depend on); the protocol is the one Nicotine+ documents
(https://nicotine-plus.org/doc/SLSKPROTOCOL.html).

- One connection to the server: login, the port peers can reach us on, our share counts, searches (ours,
  and others' sent to us), peers' addresses, and the server's go-betweens for peers that can't reach each
  other directly.
- Connections between peers: "P" for messages (search results, browsing, folders, upload queues), "F"
  for a file, "D" for the distributed network that passes searches along (joined only when sharing, and
  only as a leaf: we take searches from a parent, and accept no children). Either side opens one
  directly; when that can't work (a router in the way), the other is asked through the server to connect
  instead and announces itself with PierceFireWall and the token the asker chose.
- A download: QueueUpload; when its turn comes the uploader sends TransferRequest, we accept with
  TransferResponse, and it opens an F connection; we say how much we have already (FileOffset), so an
  interrupted download carries on rather than starting over.
- An upload, the other way round: a QueueUpload for a shared file waits for one of UPLOAD_SLOTS; we send
  TransferRequest, and on the peer's yes open an F connection and send from where it says.

What peers send is not trusted: message sizes are capped, everything is parsed with bounds checks, a
file's name only decides where it's saved after the caller cleans it, and only files in the shared
folders are ever read for anyone. Without shared folders we tell the server we share nothing (true), and
asks for our files go unanswered.
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
MAX_BROWSE = 64 * 1024 * 1024  # a whole library, decompressed
CONNECT_TIMEOUT = 8.0
INDIRECT_TIMEOUT = 20.0
UPLOAD_SLOTS = 2
SEARCH_RESULTS = 200  # the most of our files we answer one search with
AUDIO = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".ape", ".wv", ".alac", ".aiff"}

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

    def i32(self) -> int:
        return struct.unpack("<i", self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self._take(8))[0]

    def bool(self) -> bool:
        return self.u8() != 0

    def string(self) -> str:
        return self._take(self.u32()).decode("utf-8", "replace")

    def ip(self) -> str:
        # a uint32 whose bytes are the address back to front
        return socket.inet_ntoa(self._take(4)[::-1])

    def rest(self) -> bytes:
        part = self.data[self.at:]
        self.at = len(self.data)
        return part


async def read_message(reader: asyncio.StreamReader, code_size: int = 4) -> tuple[int, bytes]:
    length = struct.unpack("<I", await reader.readexactly(4))[0]
    if length < code_size or length > MAX_MESSAGE:
        raise SoulseekError("A message of an impossible size")
    data = await reader.readexactly(length)
    code = struct.unpack("<I", data[:4])[0] if code_size == 4 else data[0]
    return code, data[code_size:]


def _inflate(payload: bytes, limit: int = MAX_MESSAGE) -> bytes:
    inflater = zlib.decompressobj()
    data = inflater.decompress(payload, limit)
    if inflater.unconsumed_tail:
        raise SoulseekError("A compressed message too large to read")
    return data


# --- files ---

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


def _read_files(r: Reader, user: str, folder: str = "", limit: int = 5000) -> list[SharedFile]:
    """A list of files as the protocol writes them (code, name, size, extension, attributes)."""
    files = []
    count = r.u32()
    for i in range(count):
        r.u8()
        name, size = r.string(), r.u64()
        r.string()  # the extension: the name has it
        attrs = {}
        for _ in range(min(r.u32(), 16)):
            code, value = r.u32(), r.u32()
            attrs[code] = value
        if i < limit:
            files.append(SharedFile(user, f"{folder}\\{name}" if folder else name, size, bitrate=attrs.get(0, 0),
                                    duration=attrs.get(1, 0), sample_rate=attrs.get(4, 0), bit_depth=attrs.get(5, 0)))
    return files


def parse_search_response(payload: bytes) -> tuple[str, int, list[SharedFile]]:
    """A FileSearchResponse (zlib-compressed): who answered, the search's token, and their files."""
    r = Reader(_inflate(payload))
    user, token = r.string(), r.u32()
    files = _read_files(r, user)
    free, speed, queue = r.bool(), r.u32(), r.u32()
    for f in files:
        f.free_slot, f.speed, f.queue = free, speed, queue
    return user, token, files


def _read_folders(r: Reader, user: str, limit: int = 20000) -> dict[str, list[SharedFile]]:
    folders: dict[str, list[SharedFile]] = {}
    total = 0
    for _ in range(r.u32()):
        folder = r.string()
        files = _read_files(r, user, folder, max(0, limit - total))
        total += len(files)
        if files:
            folders[folder] = files
    return folders


# --- what we share ---

@dataclass
class Shared:
    real: str  # where it is on the Deck
    size: int
    attrs: dict[int, int]


class Shares:
    """The files in the folders the user shares, by the names other users see ("MusicDeck\\Music\\...")."""

    def __init__(self, folders: list[str]):
        self.folders = [os.path.abspath(os.path.expanduser(f)) for f in folders if f]
        self.files: dict[str, Shared] = {}
        self.dirs: dict[str, list[str]] = {}

    @staticmethod
    def _attrs(path: str) -> dict[int, int]:
        try:
            import mutagen
            audio = mutagen.File(path)
            info = getattr(audio, "info", None)
        except Exception:
            return {}
        attrs = {}
        if getattr(info, "bitrate", 0):
            attrs[0] = int(info.bitrate / 1000)
        if getattr(info, "length", 0):
            attrs[1] = int(info.length)
        if getattr(info, "sample_rate", 0):
            attrs[4] = int(info.sample_rate)
        if getattr(info, "bits_per_sample", 0):
            attrs[5] = int(info.bits_per_sample)
        return attrs

    def scan(self, known: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Indexes the shared folders (blocking); `known` (what the last scan saved) spares re-reading
        files that haven't changed. Returns what to save for next time."""
        known = known or {}
        index: dict[str, Any] = {}
        files: dict[str, Shared] = {}
        dirs: dict[str, list[str]] = {}
        for root in self.folders:
            top = "MusicDeck\\" + (os.path.basename(root) or "Music")
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for name in sorted(filenames):
                    if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO:
                        continue
                    real = os.path.join(directory, name)
                    try:
                        st = os.stat(real)
                    except OSError:
                        continue
                    old = known.get(real)
                    attrs = ({int(k): v for k, v in old["attrs"].items()}
                             if old and old.get("mtime") == st.st_mtime and old.get("size") == st.st_size
                             else self._attrs(real))
                    index[real] = {"mtime": st.st_mtime, "size": st.st_size, "attrs": attrs}
                    rel = os.path.relpath(directory, root)
                    folder = top if rel == "." else top + "\\" + rel.replace(os.sep, "\\")
                    virtual = folder + "\\" + name
                    files[virtual] = Shared(real, st.st_size, attrs)
                    dirs.setdefault(folder, []).append(virtual)
        self.files, self.dirs = files, dirs
        return index

    def counts(self) -> tuple[int, int]:
        return len(self.dirs), len(self.files)

    def search(self, query: str) -> list[str]:
        """Our files a search asks for: every word in its path, none of the -excluded ones."""
        words = query.lower().split()
        wanted = [w for w in words if not w.startswith("-")]
        unwanted = [w[1:] for w in words if w.startswith("-") and len(w) > 1]
        if not wanted:
            return []
        found = []
        for virtual in self.files:
            low = virtual.lower()
            if all(w in low for w in wanted) and not any(w in low for w in unwanted):
                found.append(virtual)
                if len(found) >= SEARCH_RESULTS:
                    break
        return found

    def entry(self, virtual: str, full: bool = True) -> bytes:
        shared = self.files[virtual]
        name = virtual if full else virtual.rpartition("\\")[2]
        ext = os.path.splitext(virtual)[1][1:].lower()
        attrs = b"".join(u32(k) + u32(v) for k, v in sorted(shared.attrs.items()))
        return u8(1) + string(name) + u64(shared.size) + string(ext) + u32(len(shared.attrs)) + attrs

    def folder_listing(self, folders: list[str]) -> bytes:
        body = u32(len(folders))
        for folder in folders:
            names = self.dirs.get(folder, [])
            body += string(folder) + u32(len(names)) + b"".join(self.entry(v, full=False) for v in names)
        return body


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
    writer: Optional[asyncio.StreamWriter] = None  # the file's connection, to cancel it


@dataclass
class Upload:
    user: str
    path: str  # the name the user asked for
    token: int = field(default_factory=lambda: secrets.randbelow(2**31))
    answer: Optional[asyncio.Future] = None


class Client:
    def __init__(self, username: str, password: str, port: int = 2234,
                 server: Optional[tuple[str, int]] = None, on_change: Optional[Callable[[], None]] = None,
                 shares: Optional[Shares] = None):
        self.username = username
        self.password = password
        self.port = port
        self.server_address = server or SERVER
        self.on_change = on_change or (lambda: None)
        self.shares = shares
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
        self.folder_requests: dict[int, asyncio.Future] = {}
        self.browses: dict[str, asyncio.Future] = {}
        # sharing: uploads waiting for a slot, those under way, answers to our TransferRequests
        self.upload_queue: list[Upload] = []
        self.uploading: dict[int, Upload] = {}
        self.uploads_done = 0
        self.parent: Optional[asyncio.StreamWriter] = None
        self.branch: dict[str, Any] = {}

    def _spawn(self, work: Any) -> None:
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    @property
    def sharing(self) -> bool:
        return bool(self.shares and self.shares.files)

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
        dirs, files = self.shares.counts() if self.shares else (0, 0)
        # where peers reach us; what we share (truly); no children of our own on the search network
        self._send(message(2, u32(self.port)) + message(35, u32(dirs) + u32(files)) + message(100, u8(0))
                   + message(71, u8(1)))
        self._spawn(self._read_server(reader))
        self.on_change()

    def shares_changed(self) -> None:
        """The shared folders were scanned again: the server hears the new counts."""
        dirs, files = self.shares.counts() if self.shares else (0, 0)
        self._send(message(35, u32(dirs) + u32(files)))

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
                elif code == 26 and self.sharing:  # someone's search, passed on by the server
                    user, token, query = r.string(), r.u32(), r.string()
                    self._spawn(self._answer_search(user, token, query))
                elif code == 93 and self.sharing:  # EmbeddedMessage: a search, as a branch root gets them
                    if r.u8() == 3:
                        self._distributed_search(Reader(r.rest()))
                elif code == 102 and self.sharing and self.parent is None:  # PossibleParents
                    candidates = []
                    for _ in range(min(r.u32(), 10)):
                        candidates.append((r.string(), r.ip(), r.u32()))
                    self._spawn(self._find_parent(candidates))
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
        if self.parent:
            self.parent.close()
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
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, SoulseekError):
            pass  # the peer hung up, or sent what can't be read: that connection is done
        finally:
            if self.peers.get(user) is writer:
                del self.peers[user]
            writer.close()

    async def _connection(self, user: str, kind: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """A new connection to a user, of a kind: directly, or (a router in the way) asked through the
        server, the user then connecting to us."""
        loop = asyncio.get_running_loop()
        self.addresses[user] = loop.create_future()
        self._send(message(3, string(user)))
        try:
            ip, port = await asyncio.wait_for(self.addresses[user], CONNECT_TIMEOUT)
            if port:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
                writer.write(message(1, string(self.username) + string(kind) + u32(0), code_size=1))
                return reader, writer
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            self.addresses.pop(user, None)
        token = secrets.randbelow(2**31)
        self.indirect[token] = loop.create_future()
        self._send(message(18, u32(token) + string(user) + string(kind)))
        try:
            return await asyncio.wait_for(self.indirect[token], INDIRECT_TIMEOUT)
        except asyncio.TimeoutError:
            raise SoulseekError(f"{user} can't be reached right now")
        finally:
            self.indirect.pop(token, None)

    async def _peer(self, user: str) -> asyncio.StreamWriter:
        """A message connection to a user, the one there is if there's one open."""
        writer = self.peers.get(user)
        if writer and not writer.is_closing():
            return writer
        reader, writer = await self._connection(user, "P")
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
            if code == 5:  # SharedFileListResponse: a browse we asked for
                found = self.browses.pop(user, None)
                if found and not found.done():
                    try:
                        found.set_result(_read_folders(Reader(_inflate(payload, MAX_BROWSE)), user))
                    except (SoulseekError, zlib.error) as e:
                        found.set_exception(SoulseekError(f"{user}'s list couldn't be read: {e}"))
                continue
            if code == 37:  # FolderContentsResponse
                try:
                    r = Reader(_inflate(payload))
                    token = r.u32()
                    r.string()
                    folders = _read_folders(r, user)
                except (SoulseekError, zlib.error):
                    continue
                found = self.folder_requests.pop(token, None)
                if found and not found.done():
                    found.set_result(folders)
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
            elif code == 41:  # TransferResponse: the answer to our TransferRequest (an upload)
                token, allowed = r.u32(), r.bool()
                up = self.uploading.get(token)
                if up and up.answer and not up.answer.done():
                    up.answer.set_result(allowed)
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
            elif code == 4:  # GetShareFileList: what we share
                writer.write(message(5, zlib.compress(self._share_list())))
            elif code == 36:  # FolderContentsRequest
                token, folder = r.u32(), r.string()
                folders = [folder] if self.shares and folder in self.shares.dirs else []
                body = u32(token) + string(folder) + (self.shares.folder_listing(folders) if self.shares else u32(0))
                writer.write(message(37, zlib.compress(body)))
            elif code == 43:  # QueueUpload: someone wants one of our files
                path = r.string()
                if self.shares and path in self.shares.files:
                    if not any(u.user == user and u.path == path for u in self.upload_queue):
                        self.upload_queue.append(Upload(user, path))
                    self._spawn(self._next_upload())
                else:
                    writer.write(message(50, string(path) + string("File not shared.")))
            elif code == 51:  # PlaceInQueueRequest
                path = r.string()
                place = next((i + 1 for i, u in enumerate(self.upload_queue) if u.user == user and u.path == path), 0)
                if place:
                    writer.write(message(44, string(path) + u32(place)))

    # --- downloading ---

    async def _receive_file(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        token = struct.unpack("<I", await asyncio.wait_for(reader.readexactly(4), CONNECT_TIMEOUT))[0]
        d = self.transfers.pop(token, None)
        if not d:
            return
        d.writer = writer
        partial = d.dest + ".part"
        os.makedirs(os.path.dirname(d.dest), exist_ok=True)
        # what an interrupted download got already: it carries on from there
        have = os.path.getsize(partial) if os.path.exists(partial) else 0
        if have > d.size:
            have = 0
        writer.write(u64(have))
        d.received = have
        try:
            with open(partial, "ab" if have else "wb") as f:
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
            # the part stays, to carry on from next time
            if not d.done.done():
                d.done.set_exception(e if isinstance(e, SoulseekError) else SoulseekError(f"The download stopped: {e}"))

    async def download(self, user: str, path: str, size: int, dest: str, wait: Optional[float] = None) -> str:
        """Asks `user` for the file, and waits (`wait` seconds at most, else for its turn) until it's
        saved at `dest`. A download already under way is waited for, not asked for again."""
        key = (user, path)
        d = self.downloads.get(key)
        if not d or d.done.done():
            d = self.downloads[key] = Download(user, path, size, dest)
            # a download failed with nobody waiting (left to run on) is seen, not logged as forgotten
            d.done.add_done_callback(lambda f: f.cancelled() or f.exception())
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

    def cancel(self, user: str, path: str) -> None:
        """Stops a download (what it got stays, to carry on from if it's asked for again)."""
        d = self.downloads.pop((user, path), None)
        if d:
            if d.writer:
                d.writer.close()
            if not d.done.done():
                d.done.set_exception(SoulseekError("Cancelled"))
            self.on_change()

    async def folder(self, user: str, folder: str) -> list[SharedFile]:
        """Every file in one of a user's folders (a search only finds those that match)."""
        token = secrets.randbelow(2**31)
        self.folder_requests[token] = asyncio.get_running_loop().create_future()
        writer = await self._peer(user)
        writer.write(message(36, u32(token) + string(folder)))
        try:
            folders = await asyncio.wait_for(self.folder_requests[token], 20)
        except asyncio.TimeoutError:
            raise SoulseekError(f"{user} didn't send the folder")
        finally:
            self.folder_requests.pop(token, None)
        return [f for files in folders.values() for f in files]

    async def browse(self, user: str) -> dict[str, list[SharedFile]]:
        """Everything a user shares, by folder."""
        if user not in self.browses:
            self.browses[user] = asyncio.get_running_loop().create_future()
            writer = await self._peer(user)
            writer.write(message(4))
        try:
            return await asyncio.wait_for(asyncio.shield(self.browses[user]), 60)
        except asyncio.TimeoutError:
            self.browses.pop(user, None)
            raise SoulseekError(f"{user} didn't send their list")

    # --- sharing ---

    def _share_list(self) -> bytes:
        folders = sorted(self.shares.dirs) if self.shares else []
        listing = self.shares.folder_listing(folders) if self.shares else u32(0)
        return listing + u32(0) + u32(0)  # the unknown field, no private folders

    async def _answer_search(self, user: str, token: int, query: str) -> None:
        """Someone's search our shares have files for: they hear about them, over a P connection."""
        if not self.shares or user == self.username:
            return
        found = self.shares.search(query)
        if not found:
            return
        body = string(self.username) + u32(token) + u32(len(found)) + b"".join(self.shares.entry(v) for v in found)
        free = len(self.uploading) < UPLOAD_SLOTS
        body += u8(1 if free else 0) + u32(0) + u32(len(self.upload_queue)) + u32(0) + u32(0)
        try:
            writer = await self._peer(user)
        except SoulseekError:
            return
        writer.write(message(9, zlib.compress(body)))

    def _distributed_search(self, r: Reader) -> None:
        try:
            if r.u32() != 49:  # DistribSearch's marker, always "1"
                return
            user, token, query = r.string(), r.u32(), r.string()
        except SoulseekError:
            return
        self._spawn(self._answer_search(user, token, query))

    async def _find_parent(self, candidates: list[tuple[str, str, int]]) -> None:
        """Joins the search network under one of the users the server suggests: it passes searches on to us."""
        for user, ip, port in candidates:
            if self.parent is not None:
                return
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                continue
            writer.write(message(1, string(self.username) + string("D") + u32(0), code_size=1))
            self.parent = writer
            try:
                await self._read_parent(reader)
            except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, SoulseekError):
                pass
            finally:
                writer.close()
                self.parent = None
                self.branch = {}
                self._send(message(71, u8(1)))  # no parent again: the server suggests others

    async def _read_parent(self, reader: asyncio.StreamReader) -> None:
        adopted = False
        while True:
            code, payload = await read_message(reader, code_size=1)
            r = Reader(payload)
            if code == 4:
                self.branch["level"] = r.i32()
            elif code == 5:
                self.branch["root"] = r.string()
            elif code == 3:
                if not adopted and "level" in self.branch and "root" in self.branch:
                    adopted = True
                    self._send(message(71, u8(0)) + message(126, u32(self.branch["level"] + 1))
                               + message(127, string(self.branch["root"])))
                self._distributed_search(r)
            elif code == 93 and r.u8() == 3:
                self._distributed_search(Reader(r.rest()))

    async def _next_upload(self) -> None:
        """Starts the next waiting upload while a slot is free."""
        while self.upload_queue and len(self.uploading) < UPLOAD_SLOTS:
            up = self.upload_queue.pop(0)
            self.uploading[up.token] = up
            self._spawn(self._upload(up))

    async def _upload(self, up: Upload) -> None:
        shared = self.shares.files.get(up.path) if self.shares else None
        try:
            if not shared:
                return
            up.answer = asyncio.get_running_loop().create_future()
            writer = await self._peer(up.user)
            writer.write(message(40, u32(1) + u32(up.token) + string(up.path) + u64(shared.size)))
            if not await asyncio.wait_for(up.answer, 60):
                return
            reader, fwriter = await self._connection(up.user, "F")
            try:
                fwriter.write(u32(up.token))
                offset = struct.unpack("<Q", await asyncio.wait_for(reader.readexactly(8), 30))[0]
                with open(shared.real, "rb") as f:
                    f.seek(min(offset, shared.size))
                    while True:
                        chunk = f.read(256 * 1024)
                        if not chunk:
                            break
                        fwriter.write(chunk)
                        await asyncio.wait_for(fwriter.drain(), 60)
                # the downloader closes the connection once it has it all
                await asyncio.wait_for(reader.read(1), 30)
                self.uploads_done += 1
            finally:
                fwriter.close()
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, SoulseekError):
            writer = self.peers.get(up.user)
            if writer:
                writer.write(message(46, string(up.path)))
        finally:
            self.uploading.pop(up.token, None)
            self.on_change()
            self._spawn(self._next_upload())
