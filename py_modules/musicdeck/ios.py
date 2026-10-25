"""
iPhones and iPod touches on Windows: the music Apple's sync put on them (iTunes_Control/Music), read
the way libimobiledevice reads it, in Python. On Linux GVfs does this (devices.py's afc: mounts).

* usbmux: Apple Mobile Device Service (installed with Apple Devices or iTunes: Windows has no iPhone
  driver without it) listens on 127.0.0.1:27015. It lists the iPhones plugged in, keeps the pair record
  made when one was told to trust this PC, and connects a socket through to a port on the phone. Its
  messages: a 16-byte header (length, version 1, type 8 = plist, tag) and an XML plist.
* lockdownd (port 62078): 4-byte big-endian length, then a plist. A session is started with the pair
  record's host id, and then goes on over TLS with the pair record's host certificate; through it, the
  phone's name, and the port of its AFC service (com.apple.afc: its media share).
* AFC: packets of a 40-byte header ("CFA6LPAA", the whole length, the header's length, a number, the
  operation), arguments, then data: a folder read, a file's facts (st_size, st_mtime in ns, st_ifmt),
  a file opened, read in pieces and closed, the device's free space.

Without a pair record (never trusted, or Apple's service not there), `devices` still lists the phone and
says so: the phone asks "Trust this computer?" once Apple Devices or iTunes has seen it.
"""
import contextlib
import os
import plistlib
import socket
import ssl
import struct
import tempfile
from typing import Any, Iterator, Optional

USBMUX = ("127.0.0.1", 27015)
LOCKDOWN_PORT = 62078
LABEL = "MusicDeck"
#: where Apple's sync puts music, under the media share's root
MUSIC = "/iTunes_Control/Music"

AFC_MAGIC = b"CFA6LPAA"
AFC_STATUS, AFC_DATA, AFC_READ_DIR = 0x01, 0x02, 0x03
AFC_GET_FILE_INFO, AFC_GET_DEVINFO = 0x0A, 0x0B
AFC_FILE_OPEN, AFC_FILE_OPEN_RES, AFC_FILE_READ, AFC_FILE_CLOSE = 0x0D, 0x0E, 0x0F, 0x14
AFC_READ_CHUNK = 1 << 20


class IosError(OSError):
    pass


def _recv(sock: Any, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise IosError("the iPhone stopped answering")
        out += chunk
    return out


# --- usbmux ---------------------------------------------------------------------------------------

def _mux_message(payload: dict[str, Any], tag: int = 1) -> bytes:
    body = plistlib.dumps({"ClientVersionString": LABEL, "ProgName": LABEL, "kLibUSBMuxVersion": 3, **payload})
    return struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body


def _mux_reply(sock: Any) -> dict[str, Any]:
    length, _, _, _ = struct.unpack("<IIII", _recv(sock, 16))
    return plistlib.loads(_recv(sock, length - 16))


def _mux(address: Any = None) -> socket.socket:
    try:
        return socket.create_connection(address or USBMUX, timeout=5)
    except OSError as e:
        raise IosError("Apple Mobile Device Service isn't running (it comes with Apple Devices or iTunes)") from e


def devices(address: Any = None) -> list[dict[str, Any]]:
    """[{"udid", "device_id"}] of the iPhones plugged in by USB; [] when Apple's service isn't there."""
    try:
        with _mux(address) as sock:
            sock.sendall(_mux_message({"MessageType": "ListDevices"}))
            reply = _mux_reply(sock)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return []
    found = []
    for d in reply.get("DeviceList", []):
        props = d.get("Properties", {})
        if props.get("ConnectionType", "USB") == "USB" and props.get("SerialNumber"):
            found.append({"udid": props["SerialNumber"], "device_id": d.get("DeviceID", props.get("DeviceID"))})
    return found


def pair_record(udid: str, address: Any = None) -> Optional[dict[str, Any]]:
    """The pair record for this PC that Apple's service keeps; None when there's none (not trusted)."""
    try:
        with _mux(address) as sock:
            sock.sendall(_mux_message({"MessageType": "ReadPairRecord", "PairRecordID": udid}))
            reply = _mux_reply(sock)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    data = reply.get("PairRecordData")
    return plistlib.loads(data) if isinstance(data, bytes) else None


def connect(device_id: int, port: int, address: Any = None) -> socket.socket:
    """A socket through to a port on the phone."""
    sock = _mux(address)
    # the port goes in network byte order, read by usbmuxd as a little-endian number
    sock.sendall(_mux_message({"MessageType": "Connect", "DeviceID": device_id,
                               "PortNumber": struct.unpack("<H", struct.pack(">H", port))[0]}))
    reply = _mux_reply(sock)
    if reply.get("Number", 1) != 0:
        sock.close()
        raise IosError(f"the iPhone didn't open port {port} ({reply.get('Number')})")
    sock.settimeout(30)
    return sock


# --- lockdownd ------------------------------------------------------------------------------------

def _tls(sock: socket.socket, record: dict[str, Any]) -> ssl.SSLSocket:
    """TLS with the pair record's host certificate (and no checking of the phone's: it's the phone)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1
    with contextlib.suppress(ssl.SSLError):
        context.set_ciphers("ALL:@SECLEVEL=0")  # older iPhones' lockdownd offers what OpenSSL 3 calls weak
    with tempfile.TemporaryDirectory() as tmp:
        cert, key = os.path.join(tmp, "host.pem"), os.path.join(tmp, "host.key")
        with open(cert, "wb") as f:
            f.write(record["HostCertificate"])
        with open(key, "wb") as f:
            f.write(record["HostPrivateKey"])
        context.load_cert_chain(cert, key)
    return context.wrap_socket(sock, server_side=False)


class Lockdown:
    def __init__(self, sock: Any):
        self.sock = sock

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = plistlib.dumps({"Label": LABEL, **payload})
        self.sock.sendall(struct.pack(">I", len(body)) + body)
        length, = struct.unpack(">I", _recv(self.sock, 4))
        reply = plistlib.loads(_recv(self.sock, length))
        if "Error" in reply:
            raise IosError(f"the iPhone said {reply['Error']}")
        return reply

    @classmethod
    @contextlib.contextmanager
    def session(cls, device_id: int, record: dict[str, Any], address: Any = None) -> Iterator["Lockdown"]:
        ld = cls(connect(device_id, LOCKDOWN_PORT, address))
        try:
            started = ld.request({"Request": "StartSession", "HostID": record["HostID"],
                                  "SystemBUID": record.get("SystemBUID", "")})
            if started.get("EnableSessionSSL"):
                ld.sock = _tls(ld.sock, record)
            yield ld
        finally:
            with contextlib.suppress(OSError):
                ld.sock.close()

    def value(self, key: str) -> Any:
        return self.request({"Request": "GetValue", "Key": key}).get("Value")

    def service(self, name: str) -> tuple[int, bool]:
        reply = self.request({"Request": "StartService", "Service": name})
        return int(reply["Port"]), bool(reply.get("EnableServiceSSL"))


# --- AFC ------------------------------------------------------------------------------------------

def afc_packet(number: int, operation: int, args: bytes = b"", data: bytes = b"") -> bytes:
    head = 40 + len(args)
    return AFC_MAGIC + struct.pack("<QQQQ", head + len(data), head, number, operation) + args + data


def afc_pairs(data: bytes) -> dict[str, str]:
    """AFC's key NUL value NUL... answers (a file's facts, the device's)."""
    parts = data.split(b"\0")
    return {parts[i].decode("utf-8", "replace"): parts[i + 1].decode("utf-8", "replace")
            for i in range(0, len(parts) - 1, 2)}


class Afc:
    """The phone's media share: its folders, files' facts, files read, free space."""

    def __init__(self, sock: Any):
        self.sock = sock
        self.number = 0

    @classmethod
    def open(cls, device_id: int, record: dict[str, Any], address: Any = None) -> tuple["Afc", str]:
        """The AFC service, and the phone's name."""
        with Lockdown.session(device_id, record, address) as ld:
            name = ld.value("DeviceName") or "iPhone"
            port, secure = ld.service("com.apple.afc")
        sock = connect(device_id, port, address)
        return cls(_tls(sock, record) if secure else sock), name

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()

    def _call(self, operation: int, args: bytes = b"", data: bytes = b"") -> tuple[int, bytes]:
        self.number += 1
        self.sock.sendall(afc_packet(self.number, operation, args, data))
        head = _recv(self.sock, 40)
        if head[:8] != AFC_MAGIC:
            raise IosError("the iPhone's file service answered nonsense")
        total, this, _, op = struct.unpack("<QQQQ", head[8:])
        body = _recv(self.sock, total - 40)
        if op == AFC_STATUS:
            code = struct.unpack("<Q", body[:8])[0] if len(body) >= 8 else 0
            if code:
                raise IosError(f"the iPhone's file service said no ({code})")
        return op, body

    def listdir(self, path: str) -> list[str]:
        _, body = self._call(AFC_READ_DIR, path.encode() + b"\0")
        return [n.decode("utf-8", "replace") for n in body.split(b"\0") if n and n not in (b".", b"..")]

    def info(self, path: str) -> dict[str, str]:
        _, body = self._call(AFC_GET_FILE_INFO, path.encode() + b"\0")
        return afc_pairs(body)

    def space(self) -> dict[str, int]:
        facts = afc_pairs(self._call(AFC_GET_DEVINFO)[1])
        return {"total": int(facts.get("FSTotalBytes", 0)), "free": int(facts.get("FSFreeBytes", 0))}

    def read(self, path: str, dest: str) -> None:
        _, body = self._call(AFC_FILE_OPEN, struct.pack("<Q", 1) + path.encode() + b"\0")  # 1: read only
        handle = struct.unpack("<Q", body[:8])[0]
        try:
            with open(dest, "wb") as f:
                while True:
                    _, chunk = self._call(AFC_FILE_READ, struct.pack("<QQ", handle, AFC_READ_CHUNK))
                    if not chunk:
                        break
                    f.write(chunk)
        finally:
            with contextlib.suppress(IosError, OSError):
                self._call(AFC_FILE_CLOSE, struct.pack("<Q", handle))

    def walk(self, top: str) -> Iterator[dict[str, Any]]:
        """{"path" (under `top`, with /), "size", "mtime"} of every file under a folder."""
        stack = [""]
        while stack:
            rel = stack.pop()
            try:
                names = self.listdir(f"{top}/{rel}".rstrip("/"))
            except IosError:
                continue
            for name in sorted(names):
                sub = f"{rel}/{name}" if rel else name
                try:
                    facts = self.info(f"{top}/{sub}")
                except IosError:
                    continue
                if facts.get("st_ifmt") == "S_IFDIR":
                    stack.append(sub)
                elif facts.get("st_ifmt") == "S_IFREG":
                    yield {"path": sub, "size": int(facts.get("st_size", 0)),
                           "mtime": int(facts.get("st_mtime", 0)) / 1e9}
