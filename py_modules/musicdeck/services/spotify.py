"""
Spotify, the way Spotifast does it:

* Two OAuth (PKCE) sign-ins in the browser; MusicDeck never sees the password.
  - Playback: Spotify's desktop client identity with the "streaming" scope. The access token goes
    to the helper once, and librespot turns it into reusable credentials it stores itself.
  - Web API: the public app identity shared by spotify-player and ncspot, or the user's own
    developer app (its own rate limit), for library, search and changes. Only its refresh token is
    stored.
* Reads that Spotify's own clients make over the session (playlists, the playlist library, albums,
  artists, lyrics) go through the helper instead of the Web API, as Spotifast does, so the shared
  app's rate limit only meets what the session can't answer.
* Audio: bin/spotify-helper (Rust, librespot) serves each track's decrypted original file (Ogg
  Vorbis) over loopback HTTP, so Steam's browser decodes it like any other source. Playback needs
  Spotify Premium, as with every librespot-based client.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import ssl
import time
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp
import certifi
from aiohttp import web

from .. import debuglog, models
from .base import MusicService, ServiceError, SignIn, StreamSource

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
HELPER_NAME = "spotify-helper"
REPLY_LIMIT = 64 * 1024 * 1024
API = "https://api.spotify.com/v1"
AUTHORIZE = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"

# Identities and redirect ports exactly as Spotifast registers them (the redirect must match)
PLAYBACK_CLIENT_ID = "65b708073fc0480ea92a077233ca87bd"
PLAYBACK_PORT = 8898
PLAYBACK_SCOPES = ["streaming"]
WEB_CLIENT_ID = "d420a117a32841c2b3474932e49fb54b"
WEB_PORT = 8989
WEB_SCOPES = ["playlist-modify-private", "playlist-modify-public", "playlist-read-collaborative",
              "playlist-read-private", "user-follow-read", "user-library-modify", "user-library-read",
              "user-read-private", "user-read-recently-played", "user-top-read"]
LOGIN_TIMEOUT = 10 * 60
CACHE_TTL = 180
# A Retry-After longer than this isn't waited out: the request fails with the wait in the message,
# and every request fails fast until it passes (retrying during a penalty only lengthens it).
MAX_WAIT = 4
# The Web API's search caps at 10 results per type for apps in development mode (personal apps).
SEARCH_LIMIT = {"shared": 20, "personal": 10}


def web_client(config: dict[str, Any]) -> str:
    return (config.get("client_id") or "").strip() or WEB_CLIENT_ID


class _Gate:
    """Per Web API app: at most two requests in flight, and a shared Retry-After cooldown."""

    gates: dict[str, "_Gate"] = {}

    def __init__(self):
        self.slots = asyncio.Semaphore(2)
        self.until = 0.0

    @classmethod
    def of(cls, client_id: str) -> "_Gate":
        return cls.gates.setdefault(client_id, cls())

    def wait(self) -> float:
        return max(0.0, self.until - time.time())


def _session(timeout: float = 20) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
        timeout=aiohttp.ClientTimeout(total=timeout))


def helper_path(runtime_dir: str) -> Optional[str]:
    """Shipped in the plugin's bin/; a copy in the data folder also works (e.g. side-loaded)."""
    for p in (os.path.join(runtime_dir, "bin", HELPER_NAME), os.path.join(PLUGIN_ROOT, "bin", HELPER_NAME)):
        if os.path.isfile(p):
            if not os.access(p, os.X_OK):
                try:
                    os.chmod(p, 0o755)
                except OSError:
                    pass
            return p
    return None


def account_dir(runtime_dir: str, username: str) -> str:
    """Where one account's playback credentials live (the helper's --cache); one folder per account,
    so several Spotify sources don't overwrite each other's sign-in."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", username) or "unknown"
    return os.path.join(runtime_dir, "spotify", "accounts", safe)


def _adopt_legacy_credentials(runtime_dir: str, username: str) -> None:
    """Before multi-account support every account shared spotify/credentials; move it to its account."""
    legacy = os.path.join(runtime_dir, "spotify", "credentials")
    target = os.path.join(account_dir(runtime_dir, username), "credentials")
    if not os.path.isdir(legacy) or os.path.exists(target):
        return
    try:
        with open(os.path.join(legacy, "credentials.json"), "r", encoding="utf-8") as f:
            owner = json.load(f).get("username")
    except (OSError, ValueError):
        return
    if owner == username:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(legacy, target)


class SpotifyHelper:
    """The librespot helper process: JSON lines on stdin/stdout, audio on a loopback port.
    `cache_dir` holds one account's stored playback credentials."""

    def __init__(self, runtime_dir: str, cache_dir: str):
        self.runtime_dir = runtime_dir
        self.cache_dir = cache_dir
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.port = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 1
        self.lock = asyncio.Lock()
        self.reader: Optional[asyncio.Task] = None

    async def ensure(self) -> None:
        async with self.lock:
            if self.proc and self.proc.returncode is None and self.reader and not self.reader.done():
                return
            if self.proc and self.proc.returncode is None:
                self.proc.kill()  # alive but no longer read: replies would never arrive
            binary = helper_path(self.runtime_dir)
            if not binary:
                raise ServiceError("The Spotify helper is missing; reinstall MusicDeck")
            os.makedirs(self.cache_dir, exist_ok=True)
            env = dict(os.environ)
            env.pop("LD_LIBRARY_PATH", None)  # static binary; never pick up Decky's bundled libraries
            self.proc = await asyncio.create_subprocess_exec(
                binary, "--cache", self.cache_dir, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env,
                # replies are one JSON line each, and a big playlist or discography runs to megabytes
                limit=REPLY_LIMIT)
            assert self.proc.stdout
            ready = json.loads(await asyncio.wait_for(self.proc.stdout.readline(), 15))
            self.port = int(ready["port"])
            debuglog.debug("spotify", "helper started", port=self.port, version=ready.get("version"))
            self.reader = asyncio.get_event_loop().create_task(self._read(self.proc))

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError as e:
                # a reply over the limit: the stream can't be resynchronised, so restart the helper
                debuglog.debug("spotify", "helper reply too large", error=str(e))
                proc.kill()
                break
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            future = self.pending.pop(msg.get("id"), None)
            if future and not future.done():
                if "error" in msg:
                    future.set_exception(ServiceError(str(msg["error"])))
                else:
                    future.set_result(msg["result"])
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ServiceError("The Spotify helper stopped unexpectedly"))
        self.pending.clear()

    async def call(self, op: str, timeout: float = 45, **args: Any) -> Any:
        await self.ensure()
        assert self.proc and self.proc.stdin
        rid = self.next_id
        self.next_id += 1
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[rid] = future
        self.proc.stdin.write((json.dumps({"id": rid, "op": op, **args}) + "\n").encode())
        await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            debuglog.debug("spotify", "helper timed out", op=op)
            raise ServiceError("Spotify took too long to answer; try again") from None
        finally:
            self.pending.pop(rid, None)

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        if self.reader:
            self.reader.cancel()


def _image(images: Optional[list[dict[str, Any]]], want: int = 300) -> Optional[str]:
    if not images:
        return None
    ordered = sorted(images, key=lambda i: i.get("width") or 0)
    return next((i["url"] for i in ordered if (i.get("width") or 0) >= want), ordered[-1]["url"])


class SpotifyService(MusicService):
    """config: {"refresh_token", "client_id" (optional personal app), "signed_with", "username",
    "display_name", "product", "bitrate"}"""

    type = "spotify"
    display_name = "Spotify"
    features = frozenset({"star", "playlists", "radio", "lyrics"})

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        return config.get("username") or None

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.helper = SpotifyHelper(runtime_dir, account_dir(runtime_dir, config.get("username") or ""))
        self.http: Optional[aiohttp.ClientSession] = None
        self.access_token = ""
        self.token_expires = 0.0
        self.token_lock = asyncio.Lock()
        self.client_id = web_client(config)
        self.gate = _Gate.of(self.client_id)
        self.cache: dict[str, tuple[float, Any]] = {}
        self.liked: dict[str, bool] = {}

    @classmethod
    def sign_in(cls, options: dict[str, Any], runtime_dir: str) -> SignIn:
        return SpotifyLogin(runtime_dir, options.get("client_id") or "")

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """Sign-in happens through SpotifyLogin; saving only edits name/bitrate and keeps the tokens."""
        refresh = config.get("refresh_token") or (previous or {}).get("refresh_token")
        if not refresh:
            raise ServiceError("Sign in with Spotify first")
        merged = {**(previous or {}), **{k: v for k, v in config.items() if v is not None}}
        merged["client_id"] = (merged.get("client_id") or "").strip()
        # a refresh token belongs to the app that issued it
        signed_with = config.get("signed_with") or (previous or {}).get("signed_with") or WEB_CLIENT_ID
        if web_client(merged) != signed_with:
            raise ServiceError("Sign in again to use the new client ID")
        merged["signed_with"] = signed_with
        merged["refresh_token"] = refresh
        merged["bitrate"] = int(merged.get("bitrate") or 320)
        return merged

    async def start(self) -> None:
        if self.config.get("username"):
            await asyncio.to_thread(_adopt_legacy_credentials, self.runtime_dir, self.config["username"])
        self.http = _session()

    async def stop(self) -> None:
        await self.helper.stop()
        if self.http:
            await self.http.close()

    def remove(self) -> None:
        shutil.rmtree(self.helper.cache_dir, ignore_errors=True)

    # --- Web API ---

    async def _token(self) -> str:
        async with self.token_lock:
            if self.access_token and time.time() < self.token_expires - 90:
                return self.access_token
            assert self.http
            form = {"grant_type": "refresh_token", "refresh_token": self.config.get("refresh_token", ""),
                    "client_id": self.client_id}
            async with self.http.post(TOKEN, data=form) as resp:
                data = await resp.json(content_type=None)
            if resp.status != 200 or "access_token" not in data:
                raise ServiceError("Spotify signed you out; edit the source and sign in again")
            self.access_token = data["access_token"]
            self.token_expires = time.time() + int(data.get("expires_in", 3600))
            if data.get("refresh_token") and data["refresh_token"] != self.config.get("refresh_token"):
                self.config["refresh_token"] = data["refresh_token"]
                if self.save_config:
                    self.save_config(self.config)
            return self.access_token

    async def _api(self, method: str, path: str, params: Optional[dict[str, Any]] = None,
                   body: Optional[Any] = None) -> Any:
        assert self.http
        for attempt in range(3):
            if self.gate.wait() > 0:
                raise ServiceError(f"Spotify is rate-limiting MusicDeck; try again in {int(self.gate.wait()) + 1} s")
            headers = {"Authorization": f"Bearer {await self._token()}"}
            url = path if path.startswith("http") else API + path
            try:
                async with self.gate.slots:
                    async with self.http.request(method, url, params=params, json=body, headers=headers) as resp:
                        if resp.status == 401 and attempt == 0:
                            self.access_token = ""
                            continue
                        if resp.status == 429:
                            try:
                                wait = max(1, int(resp.headers.get("Retry-After", "2")))
                            except ValueError:
                                wait = 2
                            self.gate.until = max(self.gate.until, time.time() + wait)
                            debuglog.debug("spotify", "rate limited", path=path.split("?")[0], retry_after=wait)
                            if wait > MAX_WAIT or attempt == 2:
                                raise ServiceError(f"Spotify is rate-limiting MusicDeck; try again in {wait} s")
                        else:
                            if resp.status == 204:
                                return None
                            if resp.status == 404:
                                raise ServiceError("Not found on Spotify")
                            if resp.status == 403:
                                raise ServiceError("Spotify doesn't allow this here")
                            if resp.status >= 400:
                                raise ServiceError(f"Spotify returned HTTP {resp.status}")
                            text = await resp.text()
                            return json.loads(text) if text else None
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach Spotify: {e}") from e
            await asyncio.sleep(self.gate.wait())
        raise ServiceError("Spotify is busy; try again in a moment")

    async def _get(self, path: str, **params: Any) -> Any:
        """GETs are cached briefly: browsing back and forth shouldn't spend the rate limit."""
        params = {k: v for k, v in params.items() if v is not None}
        key = path + "?" + urlencode(sorted(params.items()))
        hit = self.cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        data = await self._api("GET", path, params=params)
        if len(self.cache) > 300:
            self.cache.clear()
        self.cache[key] = (time.time() + CACHE_TTL, data)
        return data

    async def _read(self, op: str, **args: Any) -> Any:
        """A read over the librespot session (helper), cached like a GET."""
        key = f"helper:{op}:{json.dumps(args, sort_keys=True)}"
        hit = self.cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        data = await self.helper.call(op, timeout=60, **args)
        self.cache[key] = (time.time() + CACHE_TTL, data)
        return data

    async def test(self) -> str:
        me = await self._get("/me")
        status = await self.helper.call("login_cached")
        plan = "Premium" if me.get("product") == "premium" else (me.get("product") or "").capitalize()
        return f"Signed in as {me.get('display_name') or status.get('username')} ({plan})"

    # --- mapping ---

    def _track(self, t: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not t or t.get("type", "track") != "track" or not t.get("id") or t.get("is_local"):
            return None
        if t.get("is_playable") is False:
            return None
        album = t.get("album") or {}
        artists = t.get("artists") or []
        return models.track(t["id"], t.get("name", ""), ", ".join(a.get("name", "") for a in artists),
                            album.get("name", ""), (t.get("duration_ms") or 0) / 1000,
                            art=_image(album.get("images")), album_id=album.get("id"),
                            artist_id=artists[0].get("id") if artists else None)

    def _tracks(self, items: list[Any]) -> list[dict[str, Any]]:
        out = []
        for item in items or []:
            # saved/playlist items wrap the track; plain lists don't
            t = self._track(item.get("track") or item.get("item") if isinstance(item, dict) and
                            ("track" in item or "item" in item) else item)
            if t:
                out.append(t)
        return out

    @staticmethod
    def _album(a: dict[str, Any]) -> dict[str, Any]:
        artists = ", ".join(x.get("name", "") for x in a.get("artists") or [])
        year = (a.get("release_date") or "")[:4]
        return models.collection(a["id"], "album", a.get("name", ""), " · ".join(x for x in [artists, year] if x),
                                 _image(a.get("images")))

    @staticmethod
    def _artist(a: dict[str, Any]) -> dict[str, Any]:
        followers = (a.get("followers") or {}).get("total")
        return models.collection(a["id"], "artist", a.get("name", ""),
                                 f"{followers:,} followers" if followers else "", _image(a.get("images")))

    @staticmethod
    def _playlist(p: dict[str, Any]) -> dict[str, Any]:
        owner = (p.get("owner") or {}).get("display_name", "")
        count = (p.get("tracks") or p.get("items") or {}).get("total")
        sub = " · ".join(x for x in [owner, models.count(count, "track") if count is not None else ""] if x)
        return models.collection(p["id"], "playlist", p.get("name", ""), sub, _image(p.get("images")))

    async def _starred(self, tracks: list[dict[str, Any]], limit: int = 120) -> None:
        """Marks which tracks are in Liked Songs: answers are remembered, and only the first
        `limit` unknown tracks are asked about (40 per request), so long lists stay cheap."""
        unknown = list({t["id"]: t for t in tracks if t["id"] not in self.liked}.values())[:limit]
        for i in range(0, len(unknown), 40):
            chunk = unknown[i:i + 40]
            try:
                flags = await self._api("GET", "/me/library/contains",
                                        params={"uris": ",".join(f"spotify:track:{t['id']}" for t in chunk)})
            except ServiceError:
                break
            for t, liked in zip(chunk, flags or []):
                self.liked[t["id"]] = bool(liked)
        for t in tracks:
            if t["id"] in self.liked:
                t["starred"] = self.liked[t["id"]]

    async def _all(self, path: str, limit: int = 50, max_items: int = 1000, **params: Any) -> list[Any]:
        items: list[Any] = []
        page = await self._get(path, limit=limit, **params)
        while page:
            items += page.get("items") or []
            if not page.get("next") or len(items) >= max_items:
                break
            page = await self._api("GET", page["next"])
        return items

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        await self._fix_display_name()
        jobs = {
            "playlists": self._library_playlists(),
            "recent": self._get("/me/player/recently-played", limit=30),
            "top_tracks": self._get("/me/top/tracks", limit=20, time_range="short_term"),
            "albums": self._get("/me/albums", limit=20),
            "top_artists": self._get("/me/top/artists", limit=20),
            "new": self._get("/browse/new-releases", limit=20),
        }
        got = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
        ok = {k: v for k, v in got.items() if isinstance(v, (dict, list))}
        if not ok:
            raise next(v for v in got.values() if isinstance(v, Exception))
        sections = []
        if "playlists" in ok:
            sections.append(models.section("Your playlists", [self._playlist(p) for p in ok["playlists"] if p]))
        if "recent" in ok:
            seen, recent = set(), []
            for t in self._tracks(ok["recent"].get("items", [])):
                if t["id"] not in seen:
                    seen.add(t["id"])
                    recent.append(t)
            sections.append(models.section("Recently played", [], recent))
        if "top_tracks" in ok:
            sections.append(models.section("Your top tracks", [], self._tracks(ok["top_tracks"].get("items", []))))
        if "albums" in ok:
            sections.append(models.section("Saved albums", [self._album(i["album"]) for i in ok["albums"].get("items", [])]))
        if "top_artists" in ok:
            sections.append(models.section("Your top artists", [self._artist(a) for a in ok["top_artists"].get("items", [])]))
        if "new" in ok:
            sections.append(models.section("New releases", [self._album(a) for a in (ok["new"].get("albums") or {}).get("items", [])]))
        sections.append(models.section("Library", [models.collection("liked", "list", "Liked Songs")]))
        return [s for s in sections if s["items"] or s["tracks"]]

    async def _fix_display_name(self) -> None:
        """Sign-ins made while the Web API was rate-limited stored the account id as the name."""
        if self.config.get("display_name") not in (None, "", self.config.get("username")):
            return
        try:
            name = (await self._read("profile")).get("display_name")
        except ServiceError:
            return
        if name and name != self.config.get("username"):
            self.config["display_name"] = name
            if self.save_config:
                self.save_config(self.config)

    async def _library_playlists(self) -> list[dict[str, Any]]:
        try:
            return await self._read("rootlist")
        except ServiceError as e:
            debuglog.debug("spotify", "rootlist over the session failed", error=str(e))
            return await self._all("/me/playlists", max_items=500)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id == "liked":
            tracks = self._tracks(await self._all("/me/tracks", max_items=2000))
            for t in tracks:
                t["starred"] = True
            return models.page("Liked Songs", models.count(len(tracks), "track"), tracks=tracks)
        if kind == "album":
            try:
                a = await self._read("album", target=id)
                tracks = self._tracks(a.get("tracks", []))
                await self._starred(tracks)
                c = self._album(a)
                return models.page(c["title"], c["subtitle"], c["art"], tracks=tracks)
            except ServiceError as e:
                debuglog.debug("spotify", "album over the session failed", error=str(e))
            a = await self._get(f"/albums/{id}")
            items = (a.get("tracks") or {}).get("items", [])
            if (a.get("tracks") or {}).get("next"):
                items += await self._all(f"/albums/{id}/tracks", offset=len(items))
            tracks = self._tracks([{**t, "album": a} for t in items])
            await self._starred(tracks)
            c = self._album(a)
            return models.page(c["title"], c["subtitle"], c["art"], tracks=tracks)
        if kind == "artist":
            try:
                a = await self._read("artist", target=id)
                tracks = self._tracks(a.get("top", []))
                await self._starred(tracks)
                return models.page(a.get("name", ""), "Artist", _image(a.get("images")),
                                   collections=[self._album(x) for x in a.get("albums", [])], tracks=tracks)
            except ServiceError as e:
                debuglog.debug("spotify", "artist over the session failed", error=str(e))
            a, top, albums = await asyncio.gather(
                self._get(f"/artists/{id}"), self._get(f"/artists/{id}/top-tracks"),
                self._all(f"/artists/{id}/albums", limit=50, max_items=200, include_groups="album,single"),
                return_exceptions=True)
            if isinstance(a, Exception):
                raise a
            tracks = self._tracks(top.get("tracks", [])) if isinstance(top, dict) else []
            await self._starred(tracks)
            releases = [self._album(x) for x in albums] if isinstance(albums, list) else []
            return models.page(a.get("name", ""), self._artist(a)["subtitle"], _image(a.get("images")),
                               collections=releases, tracks=tracks)
        if kind == "playlist":
            try:
                p = await self._read("playlist", target=id)
                tracks = self._tracks(p.get("items", []))
                await self._starred(tracks)
                c = self._playlist(p)
                # Spotify's generated mixes have no cover of their own
                art = c["art"] or next((t["art"] for t in tracks if t.get("art")), None)
                return models.page(c["title"], c["subtitle"], art, tracks=tracks)
            except ServiceError as e:
                debuglog.debug("spotify", "playlist over the session failed", error=str(e))
            p = await self._get(f"/playlists/{id}", fields="name,owner(display_name),images,tracks(total),items(total)")
            items = await self._all(f"/playlists/{id}/items", limit=100, max_items=3000, additional_types="track")
            tracks = self._tracks(items)
            await self._starred(tracks)
            c = self._playlist({**p, "id": id})
            return models.page(c["title"], c["subtitle"], c["art"], tracks=tracks)
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        limit = SEARCH_LIMIT["shared" if self.client_id == WEB_CLIENT_ID else "personal"]
        try:
            data = await self._get("/search", q=query, type="track,album,artist,playlist", limit=limit)
        except ServiceError as e:
            if "rate-limiting" in str(e) or limit == 10:
                raise
            data = await self._get("/search", q=query, type="track,album,artist,playlist", limit=10)
        tracks = self._tracks((data.get("tracks") or {}).get("items", []))
        await self._starred(tracks)
        collections = [self._artist(a) for a in (data.get("artists") or {}).get("items", []) if a]
        collections += [self._album(a) for a in (data.get("albums") or {}).get("items", []) if a]
        collections += [self._playlist(p) for p in (data.get("playlists") or {}).get("items", []) if p]
        return models.page(f"Results for “{query}”", collections=collections, tracks=tracks, tracks_first=True)

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        await self._api("PUT" if starred else "DELETE", "/me/library", params={"uris": f"spotify:track:{track_id}"})
        self.liked[track_id] = starred
        self.cache = {k: v for k, v in self.cache.items() if not k.startswith("/me/tracks")}

    async def user_playlists(self) -> list[dict[str, Any]]:
        items = await self._library_playlists()
        me = self.config.get("username") or (await self._get("/me")).get("id")
        return [self._playlist(p) for p in items if p and ((p.get("owner") or {}).get("id") == me
                                                           or p.get("collaborative"))]

    async def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        await self._api("POST", f"/playlists/{playlist_id}/items",
                        body={"uris": [f"spotify:track:{t}" for t in track_ids]})
        self.cache = {k: v for k, v in self.cache.items() if playlist_id not in k and "rootlist" not in k}

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        """Recommendations where Spotify still allows them; otherwise the artist's top tracks plus
        more from the same album, shuffled, so the music keeps going."""
        try:
            rec = await self._get("/recommendations", seed_tracks=track["id"], limit=50)
            tracks = self._tracks(rec.get("tracks", []))
            if tracks:
                return tracks
        except ServiceError:
            pass
        import random
        pool: list[dict[str, Any]] = []
        if track.get("artist_id"):
            try:
                artist = await self._read("artist", target=track["artist_id"])
                pool += self._tracks(artist.get("top", []))
                # and a few songs from each of the artist's latest releases
                for album in artist.get("albums", [])[:4]:
                    pool += self._tracks((await self._read("album", target=album["id"])).get("tracks", [])[:5])
            except ServiceError:
                pass
        if len(pool) < 15 and track.get("artist") and not self.gate.wait():
            data = await self._get("/search", q=f"artist:{track['artist'].split(',')[0]}", type="track", limit=10)
            pool += self._tracks((data.get("tracks") or {}).get("items", []))
        unique = {t["id"]: t for t in pool if t["id"] != track["id"]}
        result = list(unique.values())
        random.shuffle(result)
        return result

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            return await self._read("lyrics", target=track["id"])
        except ServiceError:
            return None

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        await self.helper.call("login_cached", timeout=60)
        bitrate = int(self.config.get("bitrate") or 320)
        base = f"http://127.0.0.1:{self.helper.port}"
        gain = None
        assert self.http
        try:
            # opens the file once: reads Spotify's loudness data and warms the helper's key cache
            async with self.http.get(f"{base}/info/{track_id}", params={"bitrate": bitrate}) as resp:
                info = await resp.json(content_type=None) if resp.status == 200 else None
                if resp.status != 200:
                    raise ServiceError((await resp.text())[:200] or f"Spotify helper HTTP {resp.status}")
            gain = info.get("gain") if info else None
        except aiohttp.ClientError as e:
            raise ServiceError(f"Spotify helper unreachable: {e}") from e
        return StreamSource(url=f"{base}/track/{track_id}?bitrate={bitrate}", gain=models.gain(
            gain.get("track"), gain.get("album"), gain.get("track_peak"), gain.get("album_peak")) if gain else None,
            # loopback URL never expires, but the helper may restart on a new port
            expires=time.time() + 600)

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith("https://"):
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id)


class SpotifyLogin(SignIn):
    """Both Spotify sign-ins in one go: playback first, then the Web API.

    Each is OAuth with PKCE: the browser shows Spotify's own page, Spotify redirects to
    http://127.0.0.1:<port>/login on this device, and the one-time code is exchanged here.
    """

    def __init__(self, runtime_dir: str, client_id: str = ""):
        super().__init__()
        self.runtime_dir = runtime_dir
        self.client_id = client_id.strip() or WEB_CLIENT_ID
        self.step = "playback"  # playback | web | done | error
        self._runner: Optional[web.AppRunner] = None
        self._task: Optional[asyncio.Task] = None
        self._code: Optional[asyncio.Future] = None
        self._state = ""

    @property  # type: ignore[override]
    def status(self) -> str:
        return self.step if self.step in ("done", "error") else "waiting"

    @status.setter
    def status(self, value: str) -> None:
        pass  # derived from `step`; SignIn.__init__ assigns it

    async def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._run())
        for _ in range(50):  # until the first URL is ready (or it failed)
            if self.url or self.step == "error":
                break
            await asyncio.sleep(0.1)
        if self.step == "error":
            raise ServiceError(self.error)

    async def _listen(self, port: int) -> None:
        await self._close_listener()
        app = web.Application()
        app.router.add_get("/login", self._redirect)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, "127.0.0.1", port).start()
        except OSError:
            raise ServiceError(f"Port {port} is busy (is another Spotify app signing in?); close it and retry")

    async def _redirect(self, request: web.Request) -> web.Response:
        code = request.query.get("code")
        ok = bool(code) and request.query.get("state") == self._state
        if ok and self._code and not self._code.done():
            self._code.set_result(code)
        elif self._code and not self._code.done():
            self._code.set_exception(ServiceError(request.query.get("error") or "Spotify didn't send a sign-in code"))
        message = "Signed in. Returning to MusicDeck…" if ok else "Sign-in didn't complete. Go back to MusicDeck and try again."
        return web.Response(text=f"<html><body style='font-family:sans-serif;padding:2em'><h2>{message}</h2></body></html>",
                            content_type="text/html")

    async def _authorize(self, client_id: str, port: int, scopes: list[str], dialog: bool) -> dict[str, Any]:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self._state = secrets.token_urlsafe(16)
        redirect = f"http://127.0.0.1:{port}/login"
        await self._listen(port)
        self._code = asyncio.get_event_loop().create_future()
        params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect,
                  "code_challenge_method": "S256", "code_challenge": challenge, "state": self._state,
                  "scope": " ".join(scopes)}
        if dialog:
            params["show_dialog"] = "true"
        self.url = f"{AUTHORIZE}?{urlencode(params)}"
        code = await asyncio.wait_for(self._code, LOGIN_TIMEOUT)
        async with _session() as http:
            async with http.post(TOKEN, data={"grant_type": "authorization_code", "code": code,
                                               "redirect_uri": redirect, "client_id": client_id,
                                               "code_verifier": verifier}) as resp:
                data = await resp.json(content_type=None)
        if "access_token" not in data:
            if data.get("error") == "invalid_client":
                raise ServiceError("Spotify doesn't know that client ID; check it in your developer dashboard")
            raise ServiceError(f"Spotify sign-in failed: {data.get('error_description') or data.get('error') or resp.status}")
        return data

    async def _run(self) -> None:
        # signs in into a scratch folder; the credentials move to their account's folder at the end
        scratch = os.path.join(self.runtime_dir, "spotify", f"sign-in-{self.id}")
        helper = SpotifyHelper(self.runtime_dir, scratch)
        try:
            playback = await self._authorize(PLAYBACK_CLIENT_ID, PLAYBACK_PORT, PLAYBACK_SCOPES, dialog=True)
            account = await helper.call("login_token", token=playback["access_token"], timeout=60)
            self.step = "web"
            webgrant = await self._authorize(self.client_id, WEB_PORT, WEB_SCOPES, dialog=False)
            async with _session() as http:
                async with http.get(f"{API}/me", headers={"Authorization": f"Bearer {webgrant['access_token']}"}) as resp:
                    me = await resp.json(content_type=None) if resp.status == 200 else {}
            if not me.get("display_name"):
                # the Web API can be rate-limited right at sign-in; the session has the name too
                try:
                    me["display_name"] = (await helper.call("profile")).get("display_name")
                except ServiceError:
                    pass
            if me.get("product") and me["product"] != "premium":
                raise ServiceError("Spotify playback needs a Premium account")
            self.result = {"refresh_token": webgrant.get("refresh_token", ""), "username": account.get("username", ""),
                           "display_name": me.get("display_name") or account.get("username", ""),
                           "product": me.get("product", ""), "signed_with": self.client_id,
                           "client_id": "" if self.client_id == WEB_CLIENT_ID else self.client_id}
            await helper.stop()
            target = account_dir(self.runtime_dir, self.result["username"])
            shutil.rmtree(target, ignore_errors=True)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(scratch, target)
            self.step = "done"
            debuglog.debug("spotify", "signed in", user=self.result["username"])
        except asyncio.TimeoutError:
            self.step, self.error = "error", "Sign-in timed out"
        except asyncio.CancelledError:
            self.step, self.error = "error", "Sign-in cancelled"
        except ServiceError as e:
            self.step, self.error = "error", str(e)
        except Exception as e:
            self.step, self.error = "error", f"Sign-in failed: {e}"
        finally:
            await helper.stop()
            shutil.rmtree(scratch, ignore_errors=True)
            await self._close_listener()

    async def _close_listener(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        await self._close_listener()
