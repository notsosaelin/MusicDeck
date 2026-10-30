"""
The internet link between paired devices: the musicdeck-net helper (backend/musicdeck-net, Rust, iroh), driven
with JSON lines on its stdin and stdout.

Each device has a key of its own (net.key in the settings folder) whose public half is its node id; paired devices
learn each other's over the home network (peers.py, signed) and the helper is told them ("allow"). It keeps a
connection to each one that's on, wherever it is: directly when a path can be made through the routers (hole
punching), otherwise through n0's free public relays, which pass the encrypted traffic on without being able to
read it. A device is found by its id through n0's DNS and the Mainline DHT. Nothing else answers or is dialled,
and only a few requests cross (the helper's ALLOWED; checked again by peers.py): what's playing, playing on another
device as a speaker, the playlists, the listens. No Local files or discs, nothing else of MusicDeck. The router is
never asked to open a port.
"""
import asyncio
import contextlib
import json
import os
import time
from typing import Any, Awaitable, Callable, Optional

MAX_LINE = 16 * 1024 * 1024
RESTART_AFTER = (2, 10, 60)  # seconds before starting it again, after its 1st, 2nd, later exit


class FarError(Exception):
    pass


class Farlink:
    def __init__(self, exe: str, key_path: str, log,
                 on_request: Callable[[str, dict[str, Any]], Awaitable[Any]],
                 on_peer: Callable[[str, bool, Optional[str]], Awaitable[None]], args: Optional[list[str]] = None):
        self.exe = exe
        self.args = args or []
        self.key_path = key_path
        self.log = log
        self.on_request = on_request
        self.on_peer = on_peer
        self.node: Optional[str] = None
        # node id -> {"online", "via" ("direct" | "relay"), "at"}
        self.peers: dict[str, dict[str, Any]] = {}
        self.allowed: list[str] = []
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.calls: dict[int, asyncio.Future] = {}
        self.next_id = 1
        self.ready = asyncio.Event()
        self.task: Optional[asyncio.Task] = None
        self.stopping = False

    async def start(self) -> None:
        self.task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        self.stopping = True
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(Exception):
                self.proc.stdin.close()
                await asyncio.wait_for(self.proc.wait(), 3)
            if self.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
        if self.task:
            self.task.cancel()

    async def _run(self) -> None:
        exits = 0
        while not self.stopping:
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    self.exe, "--key", self.key_path, *self.args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=MAX_LINE,
                    **({"creationflags": 0x08000000} if os.name == "nt" else {}))  # no console window
            except OSError as e:
                self.log.info(f"Your devices: the internet link can't start ({e}); nearby only")
                return
            started = time.monotonic()
            try:
                await self._read()
            finally:
                self.ready.clear()
                for fut in self.calls.values():
                    if not fut.done():
                        fut.set_exception(FarError("the link stopped"))
                self.calls.clear()
                for node, peer in self.peers.items():
                    if peer.get("online"):
                        peer.update(online=False, via=None)
                        await self.on_peer(node, False, None)
            if self.stopping:
                return
            exits = 0 if time.monotonic() - started > 600 else exits + 1
            self.log.info(f"Your devices: the internet link stopped (exit {self.proc.returncode}); starting it again")
            await asyncio.sleep(RESTART_AFTER[min(exits, len(RESTART_AFTER)) - 1] if exits else 1)

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            try:
                line = await self.proc.stdout.readline()
            except ValueError:  # a line over MAX_LINE: never from the helper
                continue
            if not line:
                return
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            op = msg.get("op")
            if op == "ready" and isinstance(msg.get("node"), str):
                self.node = msg["node"]
                self.ready.set()
                self._send({"op": "allow", "nodes": self.allowed})
            elif op == "result":
                fut = self.calls.pop(msg.get("id"), None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(FarError(str(msg["error"])[:200]))
                    else:
                        fut.set_result(msg.get("res"))
            elif op == "request" and isinstance(msg.get("req"), dict):
                asyncio.ensure_future(self._answer(msg.get("rid"), str(msg.get("node", "")), msg["req"]))
            elif op == "peer" and isinstance(msg.get("node"), str):
                online, via = bool(msg.get("online")), msg.get("via") if msg.get("via") in ("direct", "relay") else None
                was = self.peers.get(msg["node"], {})
                self.peers[msg["node"]] = {"online": online, "via": via, "at": time.time()}
                if was.get("online") != online:
                    await self.on_peer(msg["node"], online, via)
            elif op == "log":
                self.log.info(f"Your devices (internet): {str(msg.get('msg'))[:300]}")
            elif op == "stats":
                fut = self.calls.pop("stats", None)
                if fut and not fut.done():
                    fut.set_result(msg)

    async def _answer(self, rid: Any, node: str, req: dict[str, Any]) -> None:
        try:
            res = await self.on_request(node, req)
            self._send({"op": "reply", "rid": rid, "res": res})
        except Exception as e:  # anything refused or failed: a short reason, never a traceback
            self._send({"op": "reply", "rid": rid, "error": type(e).__name__})

    def _send(self, msg: dict[str, Any]) -> None:
        if self.proc and self.proc.stdin and self.proc.returncode is None:
            with contextlib.suppress(Exception):
                self.proc.stdin.write(json.dumps(msg).encode() + b"\n")

    def allow(self, nodes: list[str]) -> None:
        """The paired devices' node ids: the only ones it answers and keeps in touch with."""
        self.allowed = sorted(set(nodes))
        for node in [n for n in self.peers if n not in self.allowed]:
            self.peers.pop(node, None)
        self._send({"op": "allow", "nodes": self.allowed})

    def online(self, node: Optional[str]) -> bool:
        return bool(node and self.peers.get(node, {}).get("online"))

    def via(self, node: Optional[str]) -> Optional[str]:
        return self.peers.get(node or "", {}).get("via") if self.online(node) else None

    async def call(self, node: str, method: str, path: str, body: Any = None, timeout: float = 15) -> Any:
        if not self.ready.is_set():
            raise FarError("the link isn't running")
        cid = self.next_id
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.calls[cid] = fut
        self._send({"op": "call", "id": cid, "node": node,
                    "req": {"method": method, "path": path, **({"body": body} if body is not None else {})}})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise FarError("no answer")
        finally:
            self.calls.pop(cid, None)

    async def stats(self) -> dict[str, Any]:
        """Bytes sent and received since it started: {"tx", "rx", "per": {node: {"tx", "rx"}}}."""
        if not self.ready.is_set():
            return {}
        fut = asyncio.get_running_loop().create_future()
        self.calls["stats"] = fut
        self._send({"op": "stats"})
        try:
            return await asyncio.wait_for(fut, 5)
        except asyncio.TimeoutError:
            return {}
