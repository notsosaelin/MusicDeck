"""
Another of the user's devices running MusicDeck (peers.py), played on as a speaker or TV is: this device keeps the
queue and its controls, the other plays each song as it's told and says where it's got to, once a second, so this
one shows it as it goes. Unlike a TV, it isn't given a link to this machine's casting site: it's given the song, and
plays it as a handed-over one plays (from the same service there, or streamed from here over the paired devices'
own port, which a Windows firewall lets through once allowed).
"""
import asyncio
from typing import Any, Callable, Optional

POLL_EVERY = 1.0
# polls in a row that may fail before the device counts as gone
PATIENCE = 6


class PeerError(Exception):
    """The other device refused or didn't answer; the message is for the user."""


class PeerRenderer:
    kind = "musicdeck"

    def __init__(self, peers, peer_id: str, services: Callable[[], list[dict[str, Any]]],
                 on_status: Callable[[dict], None], on_gone: Callable[[str], None]):
        self.peers, self.peer_id, self.services = peers, peer_id, services
        self.on_status, self.on_gone = on_status, on_gone
        self.volume = 1.0
        self.poller: Optional[asyncio.Task] = None
        self.failures = 0

    @property
    def name(self) -> str:
        return self.peers.paired.get(self.peer_id, {}).get("name", "the other device")

    async def _call(self, method: str, path: str, payload: Any = None) -> Any:
        from ..peers import PeerError as Unreachable
        try:
            return await self.peers.call(self.peer_id, method, path, payload, timeout=6)
        except Unreachable as e:
            raise PeerError(str(e))

    async def connect(self) -> None:
        got = await self._call("POST", "/p/cast/start", {})
        if isinstance(got, dict) and isinstance(got.get("volume"), (int, float)):
            self.volume = float(got["volume"])
        self.poller = asyncio.create_task(self._poll())

    async def close(self, stop_player: bool = True) -> None:
        if self.poller:
            self.poller.cancel()
        try:
            await self._call("POST", "/p/cast/end", {"stop": bool(stop_player)})
        except PeerError:
            pass

    async def load_track(self, instance: str, track: dict, start: float, autoplay: bool) -> None:
        got = await self._call("POST", "/p/cast/load", {"instance": instance, "track": track, "start": float(start or 0),
                                                        "autoplay": bool(autoplay), "services": self.services()})
        if isinstance(got, dict) and got.get("ok") is False:
            away = not self.peers.near(self.peer_id)
            raise PeerError(f"{self.name} can't play this song" + (
                ": songs from Local files and discs only play on the same network" if away else ""))

    async def _control(self, action: str, value: Optional[float] = None) -> None:
        await self._call("POST", "/p/cast/control", {"action": action, "value": value})

    async def play(self) -> None:
        await self._control("play")

    async def pause(self) -> None:
        await self._control("pause")

    async def stop(self) -> None:
        await self._control("stop")

    async def seek(self, seconds: float) -> None:
        await self._control("seek", seconds)

    async def set_volume(self, level: float) -> None:
        await self._control("volume", level)
        self.volume = level

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(POLL_EVERY)
            try:
                status = await self._call("GET", "/p/cast/status")
                self.failures = 0
            except PeerError:
                self.failures += 1
                if self.failures >= PATIENCE:
                    self.on_gone(f"{self.name} stopped answering")
                    return
                continue
            if not isinstance(status, dict):
                continue
            if status.get("gone"):
                self.on_gone(str(status["gone"]))
                return
            if isinstance(status.get("volume"), (int, float)):
                self.volume = float(status["volume"])
            self.on_status({k: status[k] for k in ("state", "position", "duration", "ended", "error") if k in status})
