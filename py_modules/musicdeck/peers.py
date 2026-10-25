"""
Your other devices: MusicDeck on another Steam (the PC, the Deck) on the same home network, paired once, then
keeping each other up to date: each one's listening adds to the other's stats, and what one was playing can be
picked up on the other ("Continue on this device"), its songs from Local files streamed from where they are.

Finding each other: every copy says where it is on a multicast group of the home network (GROUP), once a minute
and when asked, so each lists the others nearby.

Pairing (numeric comparison, as Bluetooth pairs): the device asked to pair (it shows a prompt) and the one asking
each make an X25519 key, swap the public halves, and both show a six-digit code made from the shared secret and the
exchange. The person checks the two codes match and accepts on the device that was asked. Someone listening in
learns nothing of the secret; someone in the middle would make the two codes differ (a 1 in a million chance
otherwise). The pair's key comes from the same secret and is kept by both, in the settings folder (private to the
user, as the sign-ins are).

A device takes pairing requests only while its Your devices settings are open (and two minutes after).
After pairing, every request between them is signed with that key (HMAC-SHA256 over the method, the path with its
query, the time and the body's hash), refused when the time is more than SKEW off or the signature was seen before;
links to songs and pictures carry a signature of their own and a time they stop working. Only addresses of the home
network are answered. The traffic itself isn't encrypted (Decky's Python has no fast cipher): what passes is the
music and what was listened to, on the home network only.
"""
import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import struct
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

import aiohttp
from aiohttp import web

from . import x25519
from .cast import netif

GROUP = ("239.255.77.82", 41377)
PORT = 41378  # the API's; another when it's taken
ANNOUNCE_EVERY = 60
# a device not heard from for this long isn't nearby any more
NEARBY_FOR = 200
SKEW = 300
LINK_LIFE = 6 * 3600
PAIR_LIFE = 120  # seconds a pairing prompt waits for an answer
PROMPT_GAP = 10  # seconds between pairing prompts from one address (no flood of prompts)
# a device takes pairing requests only while its Your devices is open, and this long after (as Bluetooth is
# "discoverable"): on a café's Wi-Fi nobody can make a prompt appear out of nowhere
PAIRABLE_FOR = 120
MAX_BODY = 1 << 20
PLAYS_PAGE = 5000
SYNC_EVERY = 600


class PeerError(Exception):
    """Something to tell the user (a device that can't be reached, a refused pairing)."""


def _mac(key: bytes, *parts: str) -> str:
    return hmac.new(key, "\n".join(parts).encode(), hashlib.sha256).hexdigest()


def _home(address: str) -> bool:
    """An address of the home network (or this machine): the only ones answered."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def pairing_secrets(shared: bytes, initiator_pub: bytes, answerer_pub: bytes, initiator: str, answerer: str) -> tuple[bytes, str]:
    """(the pair's key, the six-digit code both screens show) from the X25519 secret and the exchange."""
    transcript = hashlib.sha256(b"musicdeck-pair\n" + initiator_pub + answerer_pub
                                + f"\n{initiator}\n{answerer}".encode()).digest()
    key = hmac.new(shared, b"key" + transcript, hashlib.sha256).digest()
    number = int.from_bytes(hmac.new(shared, b"code" + transcript, hashlib.sha256).digest()[:4], "big") % 1_000_000
    return key, f"{number:06d}"


class _Listener(asyncio.DatagramProtocol):
    def __init__(self, peers: "Peers"):
        self.peers = peers

    def datagram_received(self, data: bytes, addr) -> None:
        self.peers._heard(data, addr[0])


class Peers:
    def __init__(self, folder: str, name: str, log, emit: Callable[..., Awaitable[None]],
                 session: Callable[[], dict[str, Any]], services: Callable[[], list[dict[str, Any]]],
                 plays, serve: Callable[[web.Request, str, str, str], Awaitable[web.StreamResponse]],
                 http: aiohttp.ClientSession, version: str = ""):
        self.path = os.path.join(folder, "peers.json")
        self.log = log
        self.emit = emit
        self.session = session
        self.services = services
        self.plays = plays
        self.serve = serve
        self.http = http
        self.version = version
        data = self._load()
        me = data.get("me") if isinstance(data.get("me"), dict) else {}
        self.me = {"id": me.get("id") or secrets.token_hex(8), "name": me.get("name") or name}
        self.paired: dict[str, dict[str, Any]] = {k: v for k, v in (data.get("paired") or {}).items()
                                                  if isinstance(v, dict) and isinstance(v.get("key"), str)}
        self.synced: dict[str, float] = {k: v for k, v in (data.get("synced") or {}).items() if isinstance(v, (int, float))}
        self.nearby: dict[str, dict[str, Any]] = {}
        # pairing: prompts waiting for an answer here (by request id), and ones asked of another device
        self.prompts: dict[str, dict[str, Any]] = {}
        self.prompted_at: dict[str, float] = {}
        self.pairable_until = 0.0
        # music handed here by a paired device ("Play on" there): (its id, {"session", "services"})
        self.on_play: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None
        self.seen_sigs: dict[str, float] = {}
        self.port = 0
        self.runner: Optional[web.AppRunner] = None
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.tasks: list[asyncio.Task] = []
        self._save()

    # --- keeping what's paired ---

    def _load(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"me": self.me, "paired": self.paired, "synced": self.synced}, f)
        os.replace(tmp, self.path)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    # --- starting and stopping ---

    async def start(self) -> None:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post("/p/pair", self._pair_asked)
        app.router.add_get("/p/pair/{rid}", self._pair_status)
        app.router.add_get("/p/hello", self._signed(self._hello))
        app.router.add_get("/p/session", self._signed(self._session))
        app.router.add_post("/p/took-over", self._signed(self._took_over))
        app.router.add_post("/p/play", self._signed(self._play))
        app.router.add_get("/p/plays", self._signed(self._plays))
        app.router.add_post("/p/unpair", self._signed(self._unpaired))
        app.router.add_get("/p/stream/{instance}/{id:.+}", self._linked("stream"))
        app.router.add_get("/p/art/{instance}/{id:.+}", self._linked("art"))
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        for port in (PORT, 0):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if os.name != "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", port))
            except OSError:
                sock.close()
                continue
            await web.SockSite(self.runner, sock).start()
            self.port = sock.getsockname()[1]
            break
        await self._listen()
        self.tasks.append(asyncio.ensure_future(self._announcing()))
        self.tasks.append(asyncio.ensure_future(self._syncing()))
        self.log.info(f"Your devices: this is {self.me['name']}, on port {self.port}; {len(self.paired)} paired")

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.transport:
            self.transport.close()
        if self.runner:
            await self.runner.cleanup()

    # --- finding each other ---

    async def _listen(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        netif.quiet_resets(sock)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", GROUP[1]))
        except OSError as e:
            self.log.info(f"Your devices: can't listen for others ({e}); they can still find this one")
            sock.close()
            return
        for local in ["0.0.0.0", *netif.local_addresses()]:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                struct.pack("4s4s", socket.inet_aton(GROUP[0]), socket.inet_aton(local)))
        self.transport, _ = await loop.create_datagram_endpoint(lambda: _Listener(self), sock=sock)

    def _hello_packet(self, ask: bool = False) -> bytes:
        return json.dumps({"musicdeck": 1, "id": self.me["id"], "name": self.me["name"], "port": self.port,
                           **({"ask": True} if ask else {})}).encode()

    async def announce(self, ask: bool = False) -> None:
        """Says where this one is (and, `ask`, asks the others to say where they are): to the multicast group, and
        as a broadcast from each of this machine's addresses, as many home routers pass that between Wi-Fi devices
        and not multicast."""
        packet = self._hello_packet(ask)
        with contextlib.suppress(OSError):
            await netif.send_everywhere(asyncio.DatagramProtocol, packet, GROUP, 1, 0.05, ttl=1)
        for local in netif.local_addresses():
            with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind((local, 0))
                sock.sendto(packet, ("255.255.255.255", GROUP[1]))

    async def _announcing(self) -> None:
        await self.announce(ask=True)
        while True:
            await asyncio.sleep(ANNOUNCE_EVERY)
            await self.announce()

    def _heard(self, data: bytes, address: str) -> None:
        try:
            hello = json.loads(data[:2048])
        except ValueError:
            return
        if not isinstance(hello, dict) or hello.get("musicdeck") != 1 or not _home(address):
            return
        peer_id, port = hello.get("id"), hello.get("port")
        if not isinstance(peer_id, str) or peer_id == self.me["id"] or not isinstance(port, int) or not 0 < port < 65536:
            return
        self.nearby[peer_id] = {"name": str(hello.get("name") or "Another device")[:80], "address": address,
                                "port": port, "seen": time.time()}
        known = self.paired.get(peer_id)
        if known and (known.get("address"), known.get("port")) != (address, port):
            known.update(address=address, port=port)
            self._save()
        if hello.get("ask"):
            asyncio.ensure_future(self.announce())

    def _where(self, peer_id: str) -> Optional[str]:
        near = self.nearby.get(peer_id)
        if near and time.time() - near["seen"] < NEARBY_FOR:
            return f"http://{near['address']}:{near['port']}"
        known = self.paired.get(peer_id)
        if known and known.get("address") and known.get("port"):
            return f"http://{known['address']}:{known['port']}"
        return None

    def listing(self) -> dict[str, Any]:
        """{"me", "paired": [{"id", "name", "online"}], "nearby": [{"id", "name"}] (not paired)}."""
        now = time.time()
        fresh = {k for k, v in self.nearby.items() if now - v["seen"] < NEARBY_FOR}
        return {"me": self.me,
                "paired": [{"id": k, "name": v.get("name", ""), "online": k in fresh} for k, v in self.paired.items()],
                "nearby": [{"id": k, "name": self.nearby[k]["name"]} for k in sorted(fresh) if k not in self.paired]}

    # --- pairing: asked here ---

    async def _pair_asked(self, request: web.Request) -> web.Response:
        address = request.remote or ""
        if not _home(address):
            raise web.HTTPForbidden()
        if time.time() > self.pairable_until:
            raise web.HTTPForbidden(text="Not taking pairing requests")
        if time.time() - self.prompted_at.get(address, 0) < PROMPT_GAP:
            raise web.HTTPTooManyRequests()
        try:
            body = await request.json()
            their_id, their_name = str(body["id"])[:64], str(body.get("name") or "Another device")[:80]
            their_pub = bytes.fromhex(body["pub"])
            if len(their_pub) != 32 or their_id == self.me["id"]:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise web.HTTPBadRequest()
        self.prompted_at[address] = time.time()
        private, public = x25519.keypair()
        try:
            key, code = pairing_secrets(x25519.shared(private, their_pub), their_pub, public, their_id, self.me["id"])
        except ValueError:
            raise web.HTTPBadRequest()
        rid = secrets.token_hex(8)
        self._forget_old_prompts()
        self.prompts[rid] = {"id": their_id, "name": their_name, "key": key.hex(), "code": code, "state": "asking",
                             "at": time.time(), "address": address, "port": body.get("port")}
        await self.emit("peer_pair_request", {"rid": rid, "name": their_name, "code": code})
        return web.json_response({"id": self.me["id"], "name": self.me["name"], "pub": public.hex(), "rid": rid})

    def _forget_old_prompts(self) -> None:
        now = time.time()
        for rid in [r for r, p in self.prompts.items() if now - p["at"] > PAIR_LIFE * 2]:
            self.prompts.pop(rid, None)

    async def _pair_status(self, request: web.Request) -> web.Response:
        """How the prompt was answered; an accepted one says so with the pair's key, so only the device that
        asked (which has it) can tell."""
        prompt = self.prompts.get(request.match_info["rid"])
        if not prompt or not _home(request.remote or ""):
            raise web.HTTPNotFound()
        state = prompt["state"]
        if state == "asking" and time.time() - prompt["at"] > PAIR_LIFE:
            state = prompt["state"] = "expired"
        proof = _mac(bytes.fromhex(prompt["key"]), "accepted", request.match_info["rid"]) if state == "accepted" else ""
        return web.json_response({"state": state, "proof": proof})

    async def answer(self, rid: str, accept: bool) -> bool:
        """The person's answer to a pairing prompt here: accepted, the asking device is paired."""
        prompt = self.prompts.get(rid)
        if not prompt or prompt["state"] != "asking" or time.time() - prompt["at"] > PAIR_LIFE:
            return False
        prompt["state"] = "accepted" if accept else "refused"
        if accept:
            port = prompt.get("port")
            self.paired[prompt["id"]] = {"name": prompt["name"], "key": prompt["key"], "address": prompt["address"],
                                         "port": port if isinstance(port, int) else PORT, "since": time.time()}
            self._save()
            self.log.info(f"Your devices: paired with {prompt['name']}")
            asyncio.ensure_future(self.sync_plays(prompt["id"]))
        return True

    # --- pairing: asking another ---

    async def pair(self, peer_id: str) -> dict[str, Any]:
        """Asks a device nearby to pair: {"code"} to show here while the other shows the same; the pairing
        finishes in the background when the other accepts ("peer_paired" or "peer_pair_failed" is emitted)."""
        base = self._where(peer_id)
        if not base or peer_id in self.paired:
            raise PeerError("That device isn't nearby")
        private, public = x25519.keypair()
        try:
            async with self.http.post(f"{base}/p/pair", json={"id": self.me["id"], "name": self.me["name"],
                                                               "pub": public.hex(), "port": self.port},
                                      timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 429:
                    raise PeerError("Try again in a moment")
                if resp.status == 403:
                    raise PeerError("Open Settings on that device too, then pair")
                resp.raise_for_status()
                body = await resp.json()
            their_pub = bytes.fromhex(body["pub"])
            if body.get("id") != peer_id or len(their_pub) != 32:
                raise PeerError("That device answered oddly")
            key, code = pairing_secrets(x25519.shared(private, their_pub), public, their_pub, self.me["id"], peer_id)
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, ValueError) as e:
            raise PeerError(f"Couldn't reach that device ({type(e).__name__})")
        name = str(body.get("name") or self.nearby.get(peer_id, {}).get("name") or "Another device")[:80]
        self.tasks.append(asyncio.ensure_future(self._await_answer(base, peer_id, name, str(body.get("rid", "")), key)))
        return {"code": code, "name": name}

    async def _await_answer(self, base: str, peer_id: str, name: str, rid: str, key: bytes) -> None:
        deadline = time.time() + PAIR_LIFE + 5
        while time.time() < deadline:
            await asyncio.sleep(1.5)
            try:
                async with self.http.get(f"{base}/p/pair/{quote(rid)}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    status = await resp.json() if resp.status == 200 else {"state": "gone"}
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                continue
            state = status.get("state")
            if state == "asking":
                continue
            if state == "accepted" and hmac.compare_digest(str(status.get("proof", "")), _mac(key, "accepted", rid)):
                near = self.nearby.get(peer_id, {})
                self.paired[peer_id] = {"name": name, "key": key.hex(), "address": near.get("address"),
                                        "port": near.get("port") or PORT, "since": time.time()}
                self._save()
                self.log.info(f"Your devices: paired with {name}")
                await self.emit("peer_paired", {"id": peer_id, "name": name})
                await self.sync_plays(peer_id)
                return
            break
        await self.emit("peer_pair_failed", {"id": peer_id, "name": name})

    async def unpair(self, peer_id: str) -> None:
        if peer_id not in self.paired:
            return
        with contextlib.suppress(PeerError):
            await self.call(peer_id, "POST", "/p/unpair", {}, timeout=3)
        self.paired.pop(peer_id, None)
        self.synced.pop(peer_id, None)
        self._save()

    # --- signed requests ---

    def _headers(self, peer_id: str, method: str, path: str, body: bytes) -> dict[str, str]:
        key = bytes.fromhex(self.paired[peer_id]["key"])
        now = str(int(time.time()))
        nonce = secrets.token_hex(8)
        return {"X-MD-Peer": self.me["id"], "X-MD-Time": now, "X-MD-Nonce": nonce,
                "X-MD-Sig": _mac(key, method, path, now, nonce, hashlib.sha256(body).hexdigest())}

    async def call(self, peer_id: str, method: str, path: str, payload: Any = None, timeout: float = 8) -> Any:
        """A signed request to a paired device; its JSON answer."""
        base = self._where(peer_id)
        if not base or peer_id not in self.paired:
            raise PeerError("That device isn't paired")
        body = json.dumps(payload).encode() if payload is not None else b""
        headers = self._headers(peer_id, method, path, body)
        if body:
            headers["Content-Type"] = "application/json"
        try:
            async with self.http.request(method, base + path, data=body or None, headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 401:
                    raise PeerError("That device doesn't know this one any more: pair them again")
                resp.raise_for_status()
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise PeerError(f"Couldn't reach {self.paired[peer_id].get('name', 'that device')} ({type(e).__name__})")

    def _check(self, request: web.Request, body: bytes) -> str:
        """The paired device a request is from, or 401."""
        if not _home(request.remote or ""):
            raise web.HTTPForbidden()
        peer_id = request.headers.get("X-MD-Peer", "")
        peer = self.paired.get(peer_id)
        when, nonce, sig = (request.headers.get(h, "") for h in ("X-MD-Time", "X-MD-Nonce", "X-MD-Sig"))
        if not peer or not when.isdigit() or abs(time.time() - int(when)) > SKEW or not nonce:
            raise web.HTTPUnauthorized()
        expected = _mac(bytes.fromhex(peer["key"]), request.method, request.path_qs, when, nonce,
                        hashlib.sha256(body).hexdigest())
        if not hmac.compare_digest(expected, sig) or sig in self.seen_sigs:
            raise web.HTTPUnauthorized()
        now = time.time()
        self.seen_sigs[sig] = now
        if len(self.seen_sigs) > 2000:
            self.seen_sigs = {s: t for s, t in self.seen_sigs.items() if now - t < SKEW * 2}
        if request.remote and peer.get("address") != request.remote:
            peer["address"] = request.remote
            self._save()
        return peer_id

    def _signed(self, handler: Callable[[web.Request, str, Any], Awaitable[Any]]):
        async def handle(request: web.Request) -> web.Response:
            body = await request.read()
            peer_id = self._check(request, body)
            try:
                payload = json.loads(body) if body else None
            except ValueError:
                raise web.HTTPBadRequest()
            return web.json_response(await handler(request, peer_id, payload))
        return handle

    def link(self, peer_id: str, kind: str, instance: str, item_id: str) -> str:
        """A link to a song or picture on a paired device, good for LINK_LIFE."""
        base = self._where(peer_id)
        if not base or peer_id not in self.paired:
            raise PeerError("That device isn't reachable")
        expires = str(int(time.time()) + LINK_LIFE)
        sig = _mac(bytes.fromhex(self.paired[peer_id]["key"]), "link", kind, instance, item_id, expires)
        return (f"{base}/p/{kind}/{quote(instance, safe='')}/{quote(item_id, safe='')}"
                f"?peer={self.me['id']}&exp={expires}&sig={sig}")

    def _linked(self, kind: str):
        async def handle(request: web.Request) -> web.StreamResponse:
            if not _home(request.remote or ""):
                raise web.HTTPForbidden()
            peer = self.paired.get(request.query.get("peer", ""))
            instance, item_id = request.match_info["instance"], request.match_info["id"]
            expires = request.query.get("exp", "")
            if not peer or not expires.isdigit() or int(expires) < time.time() or instance.startswith("peer~"):
                raise web.HTTPUnauthorized()
            if not hmac.compare_digest(_mac(bytes.fromhex(peer["key"]), "link", kind, instance, item_id, expires),
                                       request.query.get("sig", "")):
                raise web.HTTPUnauthorized()
            return await self.serve(request, kind, instance, item_id)
        return handle

    # --- what a paired device asks ---

    async def _hello(self, request, peer_id, payload) -> dict[str, Any]:
        return {"id": self.me["id"], "name": self.me["name"], "version": self.version}

    async def _session(self, request, peer_id, payload) -> dict[str, Any]:
        return {"name": self.me["name"], "session": self.session(), "services": self.services()}

    async def _took_over(self, request, peer_id, payload) -> dict[str, Any]:
        await self.emit("peer_took_over", {"name": self.paired[peer_id].get("name", "")})
        return {"ok": True}

    async def _play(self, request, peer_id, payload) -> dict[str, Any]:
        """A paired device hands its music here (its Play on): it plays here from where it was."""
        if not isinstance(payload, dict) or not isinstance(payload.get("session"), dict) or not self.on_play:
            raise web.HTTPBadRequest()
        await self.on_play(peer_id, payload)
        return {"ok": True}

    def targets(self) -> list[dict[str, str]]:
        """The paired devices that are on, as places to play ("Play on"): {"id": "musicdeck:<id>", "name", ...}."""
        now = time.time()
        return [{"id": f"musicdeck:{k}", "name": v.get("name", ""), "model": "MusicDeck", "kind": "musicdeck"}
                for k, v in self.paired.items() if k in self.nearby and now - self.nearby[k]["seen"] < NEARBY_FOR]

    async def _plays(self, request, peer_id, payload) -> dict[str, Any]:
        """This device's own listens (not ones it was told of) since `since`, a page at a time."""
        try:
            since = float(request.query.get("since", "0"))
        except ValueError:
            since = 0
        own = [e for e in self.plays.read(since) if not e.get("dev") and e.get("t", 0) > since]
        return {"plays": own[:PLAYS_PAGE], "more": len(own) > PLAYS_PAGE}

    async def _unpaired(self, request, peer_id, payload) -> dict[str, Any]:
        self.paired.pop(peer_id, None)
        self.synced.pop(peer_id, None)
        self._save()
        return {"ok": True}

    # --- keeping up with each other ---

    async def sync_plays(self, peer_id: str) -> int:
        """Adds a paired device's listens since last time to this one's (so the stats are of both); how many."""
        added = 0
        for _ in range(20):  # pages, at most
            since = self.synced.get(peer_id, 0)
            try:
                got = await self.call(peer_id, "GET", f"/p/plays?since={since}", timeout=15)
            except PeerError:
                break
            plays = [p for p in got.get("plays", []) if isinstance(p, dict) and isinstance(p.get("t"), (int, float))]
            if not plays:
                break
            for p in plays:
                # its pictures come from the device it was played on
                src = str(p.get("src") or "")
                self.plays.add(f"peer~{peer_id}~{src}" if src else "", p, when=p["t"], device=peer_id)
            added += len(plays)
            self.synced[peer_id] = max(p["t"] for p in plays)
            self._save()
            if not got.get("more"):
                break
        return added

    async def _syncing(self) -> None:
        while True:
            await asyncio.sleep(SYNC_EVERY)
            for peer_id in list(self.paired):
                if self._where(peer_id):
                    await self.sync_plays(peer_id)

    async def sessions(self) -> list[dict[str, Any]]:
        """What each paired device that's on was playing: {"peer", "name", "session", "services"}."""
        async def one(peer_id: str) -> Optional[dict[str, Any]]:
            try:
                got = await self.call(peer_id, "GET", "/p/session", timeout=4)
            except PeerError:
                return None
            return {"peer": peer_id, "name": self.paired[peer_id].get("name") or got.get("name", ""),
                    "session": got.get("session") or {}, "services": got.get("services") or []}
        found = await asyncio.gather(*[one(p) for p in self.paired if self._where(p)])
        return [f for f in found if f]


class PeerSource:
    """A paired device's source, for playing what it was playing: its songs and pictures streamed from it."""
    compatible_stream = None
    features: frozenset = frozenset()
    NOW_PLAYING = "music"

    def __init__(self, peers: Peers, peer_id: str, instance: str):
        self.peers, self.peer_id, self.instance = peers, peer_id, instance

    async def stream(self, track_id: str):
        from .services.base import ServiceError, StreamSource
        try:
            return StreamSource(url=self.peers.link(self.peer_id, "stream", self.instance, track_id))
        except PeerError as e:
            raise ServiceError(str(e))

    async def art(self, art_id: str):
        from .services.base import ServiceError, StreamSource
        try:
            return StreamSource(url=self.peers.link(self.peer_id, "art", self.instance, art_id))
        except PeerError as e:
            raise ServiceError(str(e))


def peer_source(peers: Optional[Peers], instance_id: str) -> Optional[PeerSource]:
    """"peer~<device>~<its source>" as that device's source, when the device is paired."""
    if not peers or not instance_id.startswith("peer~"):
        return None
    parts = instance_id.split("~", 2)
    if len(parts) != 3 or parts[1] not in peers.paired:
        return None
    return PeerSource(peers, parts[1], parts[2])
