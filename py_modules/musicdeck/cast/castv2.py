"""
A Google Cast sender: just enough of the CASTV2 protocol to play audio on Chromecasts, Google/Nest
speakers and TVs and soundbars with Cast built in, through Google's Default Media Receiver.

The protocol is length-prefixed protobuf `CastMessage`s over TLS on port 8009, carrying JSON on
namespaces (connection, heartbeat, receiver, media). The message has six flat fields, so it's
encoded by hand here rather than pulling in protobuf. Cast devices present self-signed
certificates; the connection is encrypted but, as with every Cast sender, not authenticated.
"""
import asyncio
import json
import ssl
import struct
from typing import Any, Callable, Optional

DEFAULT_RECEIVER = "CC1AD845"
NS_CONNECTION = "urn:x-cast:com.google.cast.tp.connection"
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"
SENDER = "sender-musicdeck"
PING_EVERY = 5
REPLY_TIMEOUT = 15
MAX_MESSAGE = 1 << 20  # the protocol's own limit is 64 KiB; anything near this is garbage
# how often to ask for the position while playing, so the Deck's clock doesn't drift from the device's
STATUS_EVERY = 5


class CastError(Exception):
    pass


# --- CastMessage protobuf ---

def _varint(n: int) -> bytes:
    out = b""
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out += bytes([byte | 0x80])
        else:
            return out + bytes([byte])


def _field(number: int, value) -> bytes:
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    raw = value.encode()
    return _varint(number << 3 | 2) + _varint(len(raw)) + raw


def encode(source: str, destination: str, namespace: str, payload: dict) -> bytes:
    body = (_field(1, 0) + _field(2, source) + _field(3, destination) + _field(4, namespace)
            + _field(5, 0) + _field(6, json.dumps(payload, separators=(",", ":"))))
    return struct.pack(">I", len(body)) + body


def decode(body: bytes) -> dict[int, Any]:
    """CastMessage fields by number: ints for varints, str for strings (binary payloads skipped)."""
    fields: dict[int, Any] = {}
    at = 0

    def varint() -> int:
        nonlocal at
        value = shift = 0
        while True:
            byte = body[at]
            at += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return value
            if shift > 63:
                raise ValueError("varint too long")

    while at < len(body):
        key = varint()
        number, wire = key >> 3, key & 7
        if wire == 0:
            fields[number] = varint()
        elif wire == 2:
            length = varint()
            raw = body[at:at + length]
            at += length
            if number != 7:  # payload_binary: never used for media
                fields[number] = raw.decode("utf-8", "replace")
        else:
            raise ValueError(f"unexpected wire type {wire}")
    return fields


# --- the sender ---

class Chromecast:
    """One connection to one Cast device. `on_status` gets this module's status dicts (see
    _media_status); `on_gone` is called once if the session ends without us ending it."""

    kind = "chromecast"
    tls = True  # tests talk to a fake device in plain TCP

    def __init__(self, host: str, port: int, on_status: Callable[[dict], None], on_gone: Callable[[str], None]):
        self.host, self.port = host, port
        self.on_status, self.on_gone = on_status, on_gone
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.tasks: list[asyncio.Task] = []
        self.pending: dict[int, asyncio.Future] = {}
        self.request_id = 0
        self.transport_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.media_session: Optional[int] = None
        self.volume = 1.0
        self.closing = False
        self.playing = False

    async def connect(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Cast devices' certificates are self-signed (see above)
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=context if self.tls else None), 10)
        except (OSError, asyncio.TimeoutError) as e:
            raise CastError(f"Couldn't reach the device ({e})")
        self._send("receiver-0", NS_CONNECTION, {"type": "CONNECT"})
        self.tasks = [asyncio.create_task(self._read_loop()), asyncio.create_task(self._ping_loop())]
        status = await self._request("receiver-0", NS_RECEIVER, {"type": "GET_STATUS"})
        self._receiver_status(status)
        await self._launch()

    async def _launch(self) -> None:
        reply = await self._request("receiver-0", NS_RECEIVER, {"type": "LAUNCH", "appId": DEFAULT_RECEIVER})
        if reply.get("type") == "LAUNCH_ERROR":
            raise CastError(f"The device wouldn't start its player ({reply.get('reason', 'unknown')})")
        app = self._receiver_status(reply)
        if not app:
            raise CastError("The device didn't start its player")
        self.transport_id, self.session_id = app["transportId"], app["sessionId"]
        self._send(self.transport_id, NS_CONNECTION, {"type": "CONNECT"})

    async def close(self, stop_player: bool = True) -> None:
        self.closing = True
        if stop_player and self.session_id and self.writer:
            try:
                self._send("receiver-0", NS_RECEIVER, {"type": "STOP", "sessionId": self.session_id,
                                                       "requestId": self._next_id()})
                await self.writer.drain()
            except (OSError, RuntimeError):
                pass
        for task in self.tasks:
            task.cancel()
        if self.writer:
            self.writer.close()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(CastError("Disconnected"))

    # --- controls ---

    async def load(self, url: str, content_type: str, meta: dict, start: float, autoplay: bool, live: bool) -> None:
        media: dict[str, Any] = {
            "contentId": url,
            "contentUrl": url,
            "contentType": content_type,
            "streamType": "LIVE" if live else "BUFFERED",
            "metadata": {
                "metadataType": 3,  # MusicTrackMediaMetadata
                "title": meta.get("title") or "",
                "artist": meta.get("artist") or "",
                "albumName": meta.get("album") or "",
                "images": [{"url": meta["art"]}] if meta.get("art") else [],
            },
        }
        if meta.get("duration"):
            media["duration"] = meta["duration"]
        if content_type == "application/x-mpegurl" and meta.get("segments"):
            media["hlsSegmentFormat"] = meta["segments"]
        payload = {"type": "LOAD", "media": media, "autoplay": autoplay, "currentTime": max(start, 0)}
        reply = await self._media(payload)
        if reply.get("type") in ("LOAD_FAILED", "LOAD_CANCELLED", "INVALID_REQUEST"):
            raise CastError("The device couldn't play this track")

    async def play(self) -> None:
        await self._media_command({"type": "PLAY"})

    async def pause(self) -> None:
        await self._media_command({"type": "PAUSE"})

    async def seek(self, seconds: float) -> None:
        await self._media_command({"type": "SEEK", "currentTime": max(seconds, 0)})

    async def stop(self) -> None:
        await self._media_command({"type": "STOP"})

    async def set_volume(self, level: float) -> None:
        self.volume = min(max(level, 0.0), 1.0)
        await self._request("receiver-0", NS_RECEIVER, {"type": "SET_VOLUME", "volume": {"level": self.volume}})

    async def _media_command(self, payload: dict) -> None:
        if self.media_session is None:
            return  # nothing loaded (yet): nothing to pause, seek or stop
        await self._media({**payload, "mediaSessionId": self.media_session})

    async def _media(self, payload: dict) -> dict:
        if not self.transport_id:
            raise CastError("Not connected")
        reply = await self._request(self.transport_id, NS_MEDIA, payload)
        if reply.get("type") == "MEDIA_STATUS":
            self._media_status(reply)
        return reply

    # --- plumbing ---

    def _next_id(self) -> int:
        self.request_id += 1
        return self.request_id

    def _send(self, destination: str, namespace: str, payload: dict) -> None:
        if not self.writer or self.writer.is_closing():
            raise CastError("Not connected")
        self.writer.write(encode(SENDER, destination, namespace, payload))

    async def _request(self, destination: str, namespace: str, payload: dict) -> dict:
        request_id = self._next_id()
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self._send(destination, namespace, {**payload, "requestId": request_id})
            return await asyncio.wait_for(future, REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            raise CastError("The device stopped answering")
        finally:
            self.pending.pop(request_id, None)

    async def _ping_loop(self) -> None:
        tick = 0
        try:
            while True:
                await asyncio.sleep(PING_EVERY)
                self._send("receiver-0", NS_HEARTBEAT, {"type": "PING"})
                tick += PING_EVERY
                if self.playing and self.transport_id and tick % STATUS_EVERY == 0:
                    self._send(self.transport_id, NS_MEDIA, {"type": "GET_STATUS", "requestId": self._next_id()})
        except (CastError, OSError, RuntimeError):
            pass

    async def _read_loop(self) -> None:
        assert self.reader
        reason = "The device disconnected"
        try:
            while True:
                (length,) = struct.unpack(">I", await self.reader.readexactly(4))
                if length > MAX_MESSAGE:
                    reason = "The device sent something unexpected"
                    break
                message = decode(await self.reader.readexactly(length))
                self._dispatch(message)
        except (asyncio.IncompleteReadError, OSError, ValueError, IndexError):
            pass
        except asyncio.CancelledError:
            return
        if not self.closing:
            self.closing = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(CastError(reason))
            self.on_gone(reason)

    def _dispatch(self, message: dict[int, Any]) -> None:
        namespace = message.get(4)
        try:
            payload = json.loads(message.get(6) or "{}")
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        kind = payload.get("type")
        if namespace == NS_HEARTBEAT:
            if kind == "PING":
                self._send(message.get(2) or "receiver-0", NS_HEARTBEAT, {"type": "PONG"})
            return
        if namespace == NS_CONNECTION and kind == "CLOSE" and message.get(2) == self.transport_id:
            # the player was closed on the device, or someone else cast to it
            if not self.closing:
                self.closing = True
                self.on_gone("Something else is playing on the device")
            return
        future = self.pending.get(payload.get("requestId") or -1)
        if future and not future.done():
            future.set_result(payload)
        if kind == "RECEIVER_STATUS":
            app = self._receiver_status(payload)
            if self.session_id and (not app or app.get("sessionId") != self.session_id) and not self.closing:
                self.closing = True
                self.on_gone("Something else is playing on the device")
        elif kind == "MEDIA_STATUS" and not future:
            self._media_status(payload)

    def _receiver_status(self, payload: dict) -> Optional[dict]:
        status = payload.get("status") or {}
        volume = status.get("volume") or {}
        if isinstance(volume.get("level"), (int, float)):
            self.volume = float(volume["level"])
        for app in status.get("applications") or []:
            if app.get("appId") == DEFAULT_RECEIVER and app.get("transportId"):
                return app
        return None

    def _media_status(self, payload: dict) -> None:
        for status in payload.get("status") or []:
            if not isinstance(status, dict):
                continue
            if isinstance(status.get("mediaSessionId"), int):
                self.media_session = status["mediaSessionId"]
            state = {"PLAYING": "playing", "PAUSED": "paused", "BUFFERING": "buffering"}.get(
                status.get("playerState"), "idle")
            self.playing = state in ("playing", "buffering")
            media = status.get("media") or {}
            report: dict[str, Any] = {"state": state, "position": float(status.get("currentTime") or 0)}
            if isinstance(media.get("duration"), (int, float)) and media["duration"] > 0:
                report["duration"] = float(media["duration"])
            if state == "idle":
                reason = status.get("idleReason")
                report["ended"] = reason == "FINISHED"
                if reason == "ERROR":
                    report["error"] = "The device couldn't play this track"
                if reason in ("FINISHED", "ERROR"):
                    self.media_session = None
            self.on_status(report)
