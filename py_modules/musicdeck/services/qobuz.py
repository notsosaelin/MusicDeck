"""
Qobuz with the user's own subscription, through the API its web player uses (the approach of
streamrip and qobuz-dl).

* App credentials: the web player's app id and request-signing secrets are read from its JS
  bundle, validated with a signed test request, cached for a day and refreshed if Qobuz rejects
  them.
* Sign-in: Qobuz's own OAuth page, opened in Steam's browser (the approach of Qobine). The password
  only ever goes to qobuz.com; a one-shot loopback listener catches the authorization code, which is
  exchanged for a user auth token. Only the token is stored.
* Streams: `track/getFileUrl` (signed) returns a direct FLAC/MP3 link at the chosen quality, which
  the stream server proxies like any other remote file.
"""
import asyncio
import base64
import secrets as pysecrets
import socket
import hashlib
import json
import os
import re
import ssl
import time
from collections import OrderedDict
from typing import Any, Optional

import aiohttp
import certifi
from aiohttp import web

from .. import debuglog, models
from .base import MusicService, ServiceError, StreamSource

API = "https://www.qobuz.com/api.json/0.2"
PLAYER = "https://play.qobuz.com"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
BUNDLE_RE = re.compile(r'<script src="(/resources/\d+\.\d+\.\d+-[a-z]\d{3}/bundle\.js)"></script>')
APP_ID_RE = re.compile(r'production:\{api:\{appId:"(?P<app_id>\d{9})",appSecret:"(\w{32})')
SEED_RE = re.compile(r'[a-z]\.initialSeed\("(?P<seed>[\w=]+)",window\.utimezone\.(?P<timezone>[a-z]+)\)')
APP_TTL = 24 * 3600
# A long-lived public track Qobuz itself uses for this check in open-source clients
TEST_TRACK = "19512574"
# The web player's OAuth key, used to exchange an authorization code (as Qobine does)
OAUTH_PRIVATE_KEY = "6lz8C03UDIC7"
OAUTH_TIMEOUT = 10 * 60

QUALITIES = {5: "MP3 320", 6: "FLAC 16-bit/44.1 kHz", 7: "FLAC 24-bit/96 kHz", 27: "FLAC 24-bit/192 kHz"}
FEATURED = [("new-releases-full", "New releases"), ("qobuzissims", "Qobuzissimes"),
            ("editor-picks", "Editor's picks"), ("most-streamed", "Most streamed")]


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT},
        connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
        timeout=aiohttp.ClientTimeout(total=20))


async def _scrape_app(session: aiohttp.ClientSession) -> dict[str, Any]:
    """App id + candidate secrets from the web player's bundle (see streamrip's QobuzSpoofer)."""
    async with session.get(f"{PLAYER}/login") as resp:
        match = BUNDLE_RE.search(await resp.text())
    if not match:
        raise ServiceError("Qobuz changed its web player; couldn't find app details")
    async with session.get(PLAYER + match.group(1)) as resp:
        bundle = await resp.text()
    app = APP_ID_RE.search(bundle)
    if not app:
        raise ServiceError("Qobuz changed its web player; couldn't find the app id")
    seeds: "OrderedDict[str, list[str]]" = OrderedDict()
    for m in SEED_RE.finditer(bundle):
        seeds[m.group("timezone")] = [m.group("seed")]
    # the bundle's ternaries evaluate the second timezone first
    keys = list(seeds)
    if len(keys) > 1:
        seeds.move_to_end(keys[1], last=False)
    names = "|".join(tz.capitalize() for tz in seeds)
    info_re = re.compile(rf'name:"\w+/(?P<timezone>{names})",info:"(?P<info>[\w=]+)",extras:"(?P<extras>[\w=]+)"')
    for m in info_re.finditer(bundle):
        seeds[m.group("timezone").lower()] += [m.group("info"), m.group("extras")]
    secrets = []
    for parts in seeds.values():
        try:
            secret = base64.standard_b64decode("".join(parts)[:-44]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if secret:
            secrets.append(secret)
    if not secrets:
        raise ServiceError("Qobuz changed its web player; couldn't find signing secrets")
    return {"app_id": app.group("app_id"), "secrets": secrets}


def _signature(track_id: str, format_id: int, ts: str, secret: str) -> str:
    raw = f"trackgetFileUrlformat_id{format_id}intentstreamtrack_id{track_id}{ts}{secret}"
    return hashlib.md5(raw.encode()).hexdigest()


class QobuzService(MusicService):
    """config: {"email", "user_id", "token", "quality"}  (password exchanged for token at save time)"""

    type = "qobuz"
    display_name = "Qobuz"
    features = frozenset({"star", "playlists", "scrobble"})

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None
        self.app: Optional[dict[str, Any]] = None
        self._app_lock = asyncio.Lock()

    # --- credentials ---

    @staticmethod
    def _app_cache(runtime_dir: str) -> str:
        return os.path.join(runtime_dir, "qobuz-app.json")

    @classmethod
    async def _app_credentials(cls, session: aiohttp.ClientSession, runtime_dir: str,
                               refresh: bool = False, user_token: Optional[str] = None) -> dict[str, Any]:
        path = cls._app_cache(runtime_dir)
        if not refresh:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if time.time() - cached.get("at", 0) < APP_TTL and cached.get("secret"):
                    return cached
            except (FileNotFoundError, json.JSONDecodeError):
                pass
        app = await _scrape_app(session)
        app["secret"] = await cls._pick_secret(session, app, user_token)
        app["at"] = time.time()
        os.makedirs(runtime_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(app, f)
        return app

    @staticmethod
    async def _pick_secret(session: aiohttp.ClientSession, app: dict[str, Any], user_token: Optional[str]) -> str:
        """The bundle has several candidate secrets; the right one is accepted by a signed request."""
        headers = {"X-App-Id": app["app_id"]}
        if user_token:
            headers["X-User-Auth-Token"] = user_token
        for secret in app["secrets"]:
            ts = str(int(time.time()))
            params = {"request_ts": ts, "request_sig": _signature(TEST_TRACK, 5, ts, secret), "track_id": TEST_TRACK,
                      "format_id": 5, "intent": "stream"}
            async with session.get(f"{API}/track/getFileUrl", params=params, headers=headers) as resp:
                # 400 = bad signature; 200/401 = signature fine (401 only means no subscription/token)
                if resp.status in (200, 401):
                    return secret
        raise ServiceError("Qobuz rejected every signing secret from its web player")

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """Sign-in happens through OAuth (QobuzLogin); saving only edits name/quality and keeps the token."""
        quality = int(config.get("quality") or (previous or {}).get("quality") or 6)
        token = config.get("token") or (previous or {}).get("token")
        if not token:
            raise ServiceError("Sign in with Qobuz first")
        merged = {**(previous or {}), **{k: v for k, v in config.items() if v is not None}}
        merged["quality"] = quality
        merged["token"] = token
        return merged

    async def start(self) -> None:
        self.session = _session()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    async def _ensure_app(self, refresh: bool = False) -> dict[str, Any]:
        async with self._app_lock:
            if self.app is None or refresh:
                assert self.session
                self.app = await self._app_credentials(self.session, self.runtime_dir, refresh, self.config.get("token"))
            return self.app

    async def _get(self, path: str, **params: Any) -> Any:
        assert self.session
        for attempt in range(2):
            app = await self._ensure_app(refresh=attempt > 0)
            headers = {"X-App-Id": app["app_id"], "X-User-Auth-Token": self.config.get("token", "")}
            try:
                async with self.session.get(f"{API}/{path}", params={k: v for k, v in params.items() if v is not None},
                                            headers=headers) as resp:
                    if resp.status == 400 and attempt == 0:
                        continue  # app id / secret rotated: scrape fresh ones and retry once
                    if resp.status == 401:
                        raise ServiceError("Qobuz signed you out; edit the source and sign in again")
                    if resp.status == 404:
                        raise ServiceError("Not found on Qobuz")
                    if resp.status >= 400:
                        raise ServiceError(f"Qobuz returned HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach Qobuz: {e}") from e
        raise ServiceError("Qobuz rejected the request")

    async def test(self) -> str:
        data = await self._get("user/get", user_id=self.config.get("user_id"))
        plan = ((data.get("credential") or {}).get("parameters") or {}).get("short_label") or "subscription"
        return f"Signed in as {data.get('display_name') or self.config.get('email')} ({plan})"

    # --- mapping ---

    @staticmethod
    def _image(obj: dict[str, Any]) -> Optional[str]:
        image = obj.get("image") or {}
        if isinstance(image, dict):
            return image.get("large") or image.get("small") or image.get("thumbnail")
        return None

    def _track(self, t: dict[str, Any], album: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        if not t.get("streamable", True):
            return None
        album = t.get("album") or album or {}
        performer = (t.get("performer") or {}).get("name") or (album.get("artist") or {}).get("name") or ""
        title = t.get("title", "") + (f" ({t['version']})" if t.get("version") else "")
        audio = t.get("audio_info") or {}
        return models.track(str(t["id"]), title, performer, album.get("title", ""), float(t.get("duration") or 0),
                            art=self._image(album), album_id=album.get("id"),
                            artist_id=str((album.get("artist") or {}).get("id") or "") or None,
                            starred=bool(t.get("favorited_at")),
                            gain=models.gain(audio.get("replaygain_track_gain"), None, audio.get("replaygain_track_peak")))

    def _tracks(self, items: list[dict[str, Any]], album: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        return [t for t in (self._track(i, album) for i in items or []) if t]

    def _album(self, a: dict[str, Any]) -> dict[str, Any]:
        year = str(a.get("released_at") and time.gmtime(a["released_at"]).tm_year or "")
        hires = " · Hi-Res" if a.get("hires_streamable") else ""
        sub = " · ".join(x for x in [(a.get("artist") or {}).get("name", ""), year] if x) + hires
        return models.collection(str(a["id"]), "album", a.get("title", ""), sub, self._image(a))

    def _artist(self, a: dict[str, Any]) -> dict[str, Any]:
        count = a.get("albums_count")
        return models.collection(str(a["id"]), "artist", a.get("name", ""),
                                 models.count(count, "album") if count else "", self._image(a))

    def _playlist(self, p: dict[str, Any]) -> dict[str, Any]:
        images = p.get("images300") or p.get("images150") or p.get("images") or []
        sub = " · ".join(x for x in [(p.get("owner") or {}).get("name", ""),
                                      models.count(p.get("tracks_count") or 0, "track")] if x)
        return models.collection(str(p["id"]), "playlist", p.get("name", ""), sub, images[0] if images else None)

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        user_id = self.config.get("user_id")
        jobs = [self._get("playlist/getUserPlaylists", user_id=user_id, limit=50),
                self._get("favorite/getUserFavorites", user_id=user_id, type="albums", limit=50)]
        jobs += [self._get("album/getFeatured", type=kind, limit=20) for kind, _ in FEATURED]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        sections = []
        playlists, favourites, *featured = results
        if isinstance(playlists, dict):
            items = [self._playlist(p) for p in (playlists.get("playlists") or {}).get("items", [])]
            if items:
                sections.append(models.section("Your playlists", items))
        library = [models.collection("favourite-tracks", "list", "Favourite tracks")]
        if isinstance(favourites, dict):
            albums = [self._album(a) for a in (favourites.get("albums") or {}).get("items", [])]
            if albums:
                sections.append(models.section("Favourite albums", albums))
        for (_, title), result in zip(FEATURED, featured):
            if isinstance(result, dict):
                albums = [self._album(a) for a in (result.get("albums") or {}).get("items", [])]
                if albums:
                    sections.append(models.section(title, albums))
        sections.append(models.section("Library", library))
        if all(isinstance(r, Exception) for r in results):
            raise results[0]
        return sections

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "list" and id == "favourite-tracks":
            data = await self._get("favorite/getUserFavorites", user_id=self.config.get("user_id"), type="tracks",
                                   limit=500)
            tracks = self._tracks((data.get("tracks") or {}).get("items", []))
            for t in tracks:
                t["starred"] = True
            return models.page("Favourite tracks", models.count(len(tracks), "track"), tracks=tracks)
        if kind == "album":
            a = await self._get("album/get", album_id=id)
            c = self._album(a)
            return models.page(c["title"], c["subtitle"], c["art"],
                               tracks=self._tracks((a.get("tracks") or {}).get("items", []), album=a))
        if kind == "artist":
            a = await self._get("artist/get", artist_id=id, extra="albums", limit=100)
            albums = sorted((a.get("albums") or {}).get("items", []), key=lambda x: x.get("released_at") or 0,
                            reverse=True)
            return models.page(a.get("name", ""), self._artist(a)["subtitle"], self._image(a),
                               collections=[self._album(x) for x in albums])
        if kind == "playlist":
            p = await self._get("playlist/get", playlist_id=id, extra="tracks", limit=500)
            c = self._playlist(p)
            return models.page(c["title"], c["subtitle"], c["art"],
                               tracks=self._tracks((p.get("tracks") or {}).get("items", [])))
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        data = await self._get("catalog/search", query=query, limit=30)
        artists = [self._artist(a) for a in (data.get("artists") or {}).get("items", [])[:8]]
        albums = [self._album(a) for a in (data.get("albums") or {}).get("items", [])]
        playlists = [self._playlist(p) for p in (data.get("playlists") or {}).get("items", [])]
        return models.page(f"Results for “{query}”", collections=artists + albums + playlists,
                           tracks=self._tracks((data.get("tracks") or {}).get("items", [])), tracks_first=True)

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        await self._get("favorite/create" if starred else "favorite/delete", track_ids=track_id)

    async def scrobble(self, track_id: str, submission: bool) -> None:
        """Qobuz counts a play when the stream starts (as its web player reports it)."""
        if submission:
            return
        assert self.session
        app = await self._ensure_app()
        events = json.dumps([{"track_id": int(track_id), "date": int(time.time()),
                              "user_id": int(self.config.get("user_id") or 0),
                              "format_id": int(self.config.get("quality") or 6)}])
        async with self.session.post(f"{API}/track/reportStreamingStart", data={"events": events},
                                     headers={"X-App-Id": app["app_id"],
                                              "X-User-Auth-Token": self.config.get("token", "")}) as resp:
            debuglog.debug("qobuz", "reported stream start", track=track_id, status=resp.status)

    async def user_playlists(self) -> list[dict[str, Any]]:
        data = await self._get("playlist/getUserPlaylists", user_id=self.config.get("user_id"), limit=100)
        user_id = self.config.get("user_id")
        return [self._playlist(p) for p in (data.get("playlists") or {}).get("items", [])
                if str((p.get("owner") or {}).get("id")) == str(user_id)]

    async def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        await self._get("playlist/addTracks", playlist_id=playlist_id, track_ids=",".join(track_ids))

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        quality = int(self.config.get("quality") or 6)
        for attempt in range(2):
            app = await self._ensure_app(refresh=attempt > 0)
            ts = str(int(time.time()))
            data = None
            try:
                data = await self._get("track/getFileUrl", track_id=track_id, format_id=quality, intent="stream",
                                       request_ts=ts, request_sig=_signature(track_id, quality, ts, app["secret"]))
                break
            except ServiceError as e:
                if attempt == 1:
                    raise
                debuglog.debug("qobuz", "getFileUrl failed, refreshing app details", error=str(e))
        url = (data or {}).get("url")
        if not url:
            if (data or {}).get("sample"):
                raise ServiceError("Qobuz only offers a preview of this track on your plan")
            raise ServiceError("Qobuz didn't return a stream for this track")
        debuglog.debug("qobuz", "stream", track=track_id, format=data.get("format_id"), bit_depth=data.get("bit_depth"),
                       rate=data.get("sampling_rate"))
        # file links are short-lived; resolve again when replaying later
        return StreamSource(url=url, expires=time.time() + 20 * 60)

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith("https://"):
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id)


class QobuzLogin:
    """One sign-in attempt: a loopback listener for Qobuz's OAuth redirect, then the code exchange.

    Qobuz redirects the browser to http://localhost:<port>/?code_autorisation=<code>; Steam's browser
    runs on the same device, so the listener (bound to 127.0.0.1 only) receives it directly.
    """

    def __init__(self, runtime_dir: str):
        self.runtime_dir = runtime_dir
        self.id = pysecrets.token_urlsafe(8)
        self.status = "starting"  # starting | waiting | done | error
        self.error = ""
        self.result: Optional[dict[str, Any]] = None
        self.url = ""
        self.app_id = ""
        self._runner: Optional[web.AppRunner] = None
        self._code: asyncio.Future = asyncio.get_event_loop().create_future()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> str:
        async with _session() as session:
            app = await QobuzService._app_credentials(session, self.runtime_dir)
        self.app_id = app["app_id"]
        web_app = web.Application()
        web_app.router.add_get("/", self._redirect)
        self._runner = web.AppRunner(web_app, access_log=None)
        await self._runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        await web.SockSite(self._runner, sock).start()
        self.url = (f"https://www.qobuz.com/signin/oauth?ext_app_id={self.app_id}"
                    f"&redirect_url=http://localhost:{port}")
        self.status = "waiting"
        self._task = asyncio.get_event_loop().create_task(self._finish())
        return self.url

    async def _redirect(self, request: web.Request) -> web.Response:
        code = request.query.get("code_autorisation") or request.query.get("code")
        if code and not self._code.done():
            self._code.set_result(code)
        message = ("Signed in to Qobuz. You can close this page and go back to MusicDeck." if code
                   else "Qobuz didn't send a sign-in code. Go back to MusicDeck and try again.")
        return web.Response(text=f"<html><body style='font-family:sans-serif;padding:2em'><h2>{message}</h2>"
                                 f"</body></html>", content_type="text/html")

    async def _finish(self) -> None:
        try:
            code = await asyncio.wait_for(self._code, OAUTH_TIMEOUT)
            async with _session() as session:
                async with session.get(f"{API}/oauth/callback",
                                       params={"code": code, "private_key": OAUTH_PRIVATE_KEY},
                                       headers={"X-App-Id": self.app_id}) as resp:
                    if resp.status >= 400:
                        raise ServiceError(f"Qobuz sign-in failed (HTTP {resp.status})")
                    data = await resp.json(content_type=None)
                token = data.get("token") or data.get("user_auth_token")
                user_id = str(data.get("user_id") or "")
                if not token or not user_id:
                    raise ServiceError("Qobuz didn't return a sign-in token")
                headers = {"X-App-Id": self.app_id, "X-User-Auth-Token": token}
                async with session.get(f"{API}/user/get", params={"user_id": user_id}, headers=headers) as resp:
                    user = await resp.json(content_type=None) if resp.status == 200 else {}
            if not ((user.get("credential") or {}).get("parameters")):
                raise ServiceError("This Qobuz account has no streaming subscription")
            self.result = {"user_id": user_id, "token": token,
                           "email": user.get("email") or user.get("display_name") or ""}
            self.status = "done"
            debuglog.debug("qobuz", "signed in", user_id=user_id)
        except asyncio.TimeoutError:
            self.status, self.error = "error", "Sign-in timed out"
        except ServiceError as e:
            self.status, self.error = "error", str(e)
        except Exception as e:
            self.status, self.error = "error", f"Sign-in failed: {e}"
        finally:
            await self.close()

    async def close(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
