"""
Roku players and Roku TVs, through Roku's External Control Protocol (port 8060).

Found with an SSDP search for "roku:ecp" and named by /query/device-info. A track plays through Roku's built-in
"Play on Roku" app (15985), given the stream's address, its format and the song's title, artist and cover, which
the Roku shows; /query/media-player says what it's doing, polled once a second. Play and pause are the remote's
Play key (a toggle, so it's pressed only when the state says it's needed).

What ECP can't do: jump within a song (a track starts at its beginning) or set a volume (only a Roku TV has one,
moved a step at a time). And a Roku whose "Control by mobile apps" is Limited (the default on recent Roku OS)
refuses everything but key presses: connecting says where to change it.
"""
import asyncio
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode, urlparse

import aiohttp

from . import netif
from .upnp import SSDP, UpnpError, _xml, read_all

PLAY_ON_ROKU = "15985"
POLL_EVERY = 1.0
LOCATION = re.compile(rb"^location:\s*(\S+)", re.I | re.M)
# the formats Play on Roku takes for audio, by MIME type
FORMATS = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a",
           "audio/flac": "flac", "audio/x-flac": "flac", "audio/wav": "wav", "audio/x-wav": "wav", "audio/x-ms-wma": "wma"}
LIMITED = ("This Roku only takes remote-control key presses from apps. To play on it, on the Roku go to Settings > "
           "System > Advanced system settings > Control by mobile apps > Network access, and choose Default or Permissive.")


class RokuError(Exception):
    """A Roku that refused or didn't answer; the message is for the user."""


class _Searcher(asyncio.DatagramProtocol):
    def __init__(self):
        self.hosts: dict[str, str] = {}

    def datagram_received(self, data, addr):
        match = LOCATION.search(data)
        if match:
            self.hosts.setdefault(addr[0], match.group(1).decode(errors="replace"))


async def search(seconds: float = 2.0) -> dict[str, str]:
    """Rokus that answered: {address: their ECP base URL}."""
    message = (b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
               b"MX: 1\r\nST: roku:ecp\r\n\r\n")
    searcher = await netif.send_everywhere(_Searcher, message, SSDP, times=2, seconds=seconds, ttl=2)
    return searcher.hosts


async def describe(session: aiohttp.ClientSession, host: str, base: str) -> Optional[dict]:
    """A Roku's name and model from /query/device-info, or None when it doesn't answer like one. Only an address
    that answered the search itself is asked (an answer can't point MusicDeck at some other machine)."""
    parsed = urlparse(base)
    if parsed.scheme != "http" or parsed.hostname != host:
        return None
    base = f"http://{host}:{parsed.port or 8060}/"
    try:
        async with session.get(base + "query/device-info", timeout=aiohttp.ClientTimeout(total=3)) as response:
            if response.status != 200:
                return None
            root = _xml(await read_all(response, 64 * 1024))
    except (aiohttp.ClientError, asyncio.TimeoutError, UpnpError):
        return None
    fields = {e.tag: e.text.strip() for e in root.iter()}
    serial = fields.get("serial-number") or fields.get("device-id") or host
    return {"id": f"roku:{serial}", "name": fields.get("user-device-name") or fields.get("friendly-device-name") or "Roku",
            "model": fields.get("model-name") or "Roku", "host": host, "base": base,
            "tv": fields.get("is-tv") == "true"}


class Roku:
    """One Roku, controlled the same way as castv2.Chromecast and upnp.Renderer (see cast.CastManager)."""

    kind = "roku"

    def __init__(self, device: dict, session: aiohttp.ClientSession, on_status: Callable[[dict], None],
                 on_gone: Callable[[str], None]):
        self.device, self.session = device, session
        self.on_status, self.on_gone = on_status, on_gone
        self.volume = 1.0
        self.poller: Optional[asyncio.Task] = None
        self.loaded = False
        self.want_playing = False
        self.started_at = 0.0
        self.state = ""
        self.failures = 0

    async def connect(self) -> None:
        await self._player()  # answers, and isn't Limited?
        self.poller = asyncio.create_task(self._poll())

    async def close(self, stop_player: bool = True) -> None:
        if self.poller:
            self.poller.cancel()
        if stop_player and self.loaded and self.state in ("play", "pause", "buffer"):
            try:
                await self._post("keypress/Back")  # out of Play on Roku's player
            except RokuError:
                pass

    async def load(self, url: str, content_type: str, meta: dict, start: float, autoplay: bool, live: bool) -> None:
        song_format = FORMATS.get(content_type.split(";")[0].strip().lower())
        if not song_format:
            raise RokuError("This Roku can't play this kind of file")
        params = {"t": "a", "u": url, "songFormat": song_format, "songName": meta.get("title") or "MusicDeck",
                  "artistName": meta.get("artist") or ""}
        if meta.get("album"):
            params["albumName"] = meta["album"]
        if meta.get("art"):
            params["albumArtUrl"] = meta["art"]
        self.want_playing = False
        await self._post(f"launch/{PLAY_ON_ROKU}?{urlencode(params)}")
        self.loaded, self.want_playing, self.started_at = True, True, time.time()
        if not autoplay:
            # it starts by itself: paused once it's going
            for _ in range(20):
                await asyncio.sleep(0.5)
                if (await self._player()).attrs.get("state") == "play":
                    await self.pause()
                    break

    async def play(self) -> None:
        if (await self._player()).attrs.get("state") != "play":
            await self._post("keypress/Play")
        self.want_playing, self.started_at = True, time.time()

    async def pause(self) -> None:
        self.want_playing = False
        if (await self._player()).attrs.get("state") == "play":
            await self._post("keypress/Play")

    async def seek(self, seconds: float) -> None:
        raise RokuError("A Roku can't jump within a song from another device")

    async def stop(self) -> None:
        self.want_playing = False
        await self.pause()

    async def set_volume(self, level: float) -> None:
        raise RokuError("Set a Roku's volume with its remote (or the TV's)")

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(POLL_EVERY)
            try:
                player = await self._player()
                self.failures = 0
            except RokuError:
                self.failures += 1
                if self.failures >= 5:
                    self.on_gone("The Roku stopped answering")
                    return
                continue
            if not self.loaded:
                continue
            state = player.attrs.get("state", "")
            plugin = next((e for e in player.iter() if e.tag == "plugin"), None)
            fields = {e.tag: e.text.strip() for e in player.iter()}
            if plugin is not None and plugin.attrs.get("id") not in (None, PLAY_ON_ROKU) and state in ("play", "pause"):
                self.loaded = False
                self.on_gone("Something else started playing on the Roku")
                return
            report: dict[str, Any] = {
                "state": {"play": "playing", "pause": "paused", "buffer": "buffering", "startup": "buffering"}.get(state, "idle"),
                "position": _ms(fields.get("position", "")),
            }
            duration = _ms(fields.get("duration", ""))
            if duration > 0:
                report["duration"] = duration
            if report["state"] == "idle" and self.want_playing and time.time() - self.started_at > 4:
                # stopped by itself while it was meant to play: the song finished
                self.want_playing = False
                self.loaded = False
                report["ended"] = True
            self.on_status(report)

    async def _player(self):
        """/query/media-player, the Roku's player and what it's doing."""
        try:
            async with self.session.get(self.device["base"] + "query/media-player",
                                        timeout=aiohttp.ClientTimeout(total=3)) as response:
                body = await read_all(response, 64 * 1024)
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise RokuError(f"The Roku didn't answer ({e.__class__.__name__})")
        if b"Limited mode" in body or status == 403:
            raise RokuError(LIMITED)
        if status != 200:
            raise RokuError(f"The Roku refused (HTTP {status})")
        try:
            player = _xml(body)
        except UpnpError as e:
            raise RokuError(str(e))
        self.state = player.attrs.get("state", "")
        return player

    async def _post(self, path: str) -> None:
        try:
            async with self.session.post(self.device["base"] + path, data=b"",
                                         timeout=aiohttp.ClientTimeout(total=5)) as response:
                body = await read_all(response, 4096)
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise RokuError(f"The Roku didn't answer ({e.__class__.__name__})")
        if b"Limited mode" in body or status == 403:
            raise RokuError(LIMITED)
        if status >= 400:
            raise RokuError(f"The Roku refused (HTTP {status})")


def _ms(text: str) -> float:
    """"12345 ms" in seconds; 0 when it isn't a time."""
    match = re.match(r"\s*(\d+)", text or "")
    return int(match.group(1)) / 1000 if match else 0.0
