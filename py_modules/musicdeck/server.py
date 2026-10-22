"""
Loopback HTTP server the frontend's <audio> element plays from.

Every source goes through here, for two reasons:
  * local files can't be read by the Steam UI's browser context directly, and
  * remote streams need CORS headers, or the Web Audio graph (used for the EQ) outputs silence.
Requests must carry the per-session key, so other local processes can't use it as an open proxy.

HLS sources (SoundCloud, some radio stations, or a playlist a service built itself such as TIDAL's
DASH) are served as a rewritten playlist whose segment URLs point back at /fetch, so hls.js in the
frontend fetches every piece through here too. Service credentials (StreamSource.headers) are added
here when proxying, so they never appear in URLs the frontend sees.

Routes: /stream/{instance}/{track id}, /art/{instance}/{art id}, /fetch?u={base64 url}; all need ?k=.

While casting, a second site listens on the home network for the one device being cast to (see
open_lan): /c/{link} (the track), /c/{link}/art and /c/{link}/f?u= (HLS pieces). Each track gets
a fresh random link, only that device's address is answered, and the site closes when casting stops.
"""
import base64
import mimetypes
import re
import secrets
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Optional
from urllib.parse import quote, urljoin, urlparse

import aiohttp
from aiohttp import web

from . import debuglog
from .cache import Cache
from .net import tls
from .services import MusicService, ServiceError, StreamSource

# Bodies are relayed as received (compressed too, e.g. Spotify's generated covers), so the
# encoding has to go along with them
PASSTHROUGH_HEADERS = ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Content-Encoding",
                       "Last-Modified", "ETag", "Cache-Control"]
CORS = {"Access-Control-Allow-Origin": "*"}
HLS_TYPE = "application/vnd.apple.mpegurl"
URI_ATTR = re.compile(r'URI="([^"]+)"')
# Resolved remote URLs (e.g. from yt-dlp) are reused for a while; most expire after ~6 hours
RESOLVE_TTL = 3 * 3600
RESOLVED_KEPT = 500
# links handed to a cast device: the track playing, the one before (still closing) and spares
CAST_LINKS_KEPT = 4

if TYPE_CHECKING:
    from .offline import OfflineStore

ServiceLookup = Callable[[str], Optional[MusicService]]


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _unb64(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()


@dataclass
class CastLink:
    """A track as one cast device may fetch it (see open_lan)."""
    instance: str
    track: str
    art: Optional[str]
    # a format every renderer plays (AAC rather than Opus), for UPnP renderers
    compatible: bool
    # hosts this track's HLS pieces come from: /f relays to these only
    hosts: set[str] = field(default_factory=set)


class StreamServer:
    def __init__(self, lookup: ServiceLookup, logger, local_art: Optional[Callable[[str], Optional[str]]] = None,
                 offline: Optional["OfflineStore"] = None):
        self.lookup = lookup
        # resolves MusicDeck's own art ids (custom playlist pictures), which belong to no service
        self.local_art = local_art
        # downloaded tracks and their artwork, used before going to the network
        self.offline = offline
        self.logger = logger
        self.key = secrets.token_urlsafe(24)
        self.port = 0
        self.runner: Optional[web.AppRunner] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._resolved: Cache[StreamSource] = Cache(RESOLVE_TTL, RESOLVED_KEPT)
        # the home-network site, while casting
        self.lan_runner: Optional[web.AppRunner] = None
        self.lan_base = ""
        self.lan_peer = ""
        self.cast_links: Cache[CastLink] = Cache(RESOLVE_TTL, CAST_LINKS_KEPT)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls()),
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60),
            auto_decompress=False,
            # Follow redirects to the letter: re-quoting a signed URL (Libsyn's "audio%2Fmpeg", say)
            # breaks its signature and the CDN answers 403
            requote_redirect_url=False)
        app = web.Application()
        app.router.add_get("/stream/{instance}/{id:.+}", self._handle_stream)
        app.router.add_get("/art/{instance}/{id:.+}", self._handler(lambda s, i: s.art(i)))
        app.router.add_get("/fetch", self._handle_fetch)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        await web.SockSite(self.runner, sock).start()
        self.logger.info(f"Stream server listening on {self.base_url}")

    async def stop(self) -> None:
        await self.close_lan()
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

    # --- resolution ---

    async def resolve_stream(self, instance_id: str, track_id: str, compatible: bool = False) -> StreamSource:
        """Resolves (and caches) where a track's audio comes from. Raises ServiceError. `compatible`
        asks sources that offer a choice for a format any renderer plays (MusicService.compatible_stream)."""
        kept = self.offline.audio(instance_id, track_id) if self.offline else None
        if kept:
            return kept
        service = self.lookup(instance_id)
        if service is None:
            raise ServiceError("Unknown service")
        compatible = compatible and service.compatible_stream is not None
        cache_key = (instance_id, track_id, "compatible") if compatible else (instance_id, track_id)
        cached = self._resolved.get(cache_key)
        if cached:
            return cached
        source = await (service.compatible_stream(track_id) if compatible else service.stream(track_id))
        if source.url:
            # a minute short of when a signed link stops working
            life = min(source.expires - time.time() if source.expires else RESOLVE_TTL, RESOLVE_TTL) - 60
            self._resolved.put(cache_key, source, life)
        return source

    def forget(self, instance_id: str, track_id: str) -> None:
        self._resolved.pop((instance_id, track_id))
        self._resolved.pop((instance_id, track_id, "compatible"))

    # --- the home-network site, while casting ---

    async def open_lan(self, peer: str) -> None:
        """Serves cast links to `peer` (the device's address) only, on the interface that reaches it."""
        await self.close_lan()
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((peer, 9))  # sends nothing: it only picks the route, and our address on it
            address = probe.getsockname()[0]
        finally:
            probe.close()
        app = web.Application()
        app.router.add_get("/c/{link}", self._handle_cast_stream)
        app.router.add_get("/c/{link}/art", self._handle_cast_art)
        app.router.add_get("/c/{link}/f", self._handle_cast_fetch)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((address, 0))
        await web.SockSite(runner, sock).start()
        self.lan_runner, self.lan_peer = runner, peer
        self.lan_base = f"http://{address}:{sock.getsockname()[1]}"
        self.logger.info(f"Casting site listening on {self.lan_base} for {peer}")

    async def close_lan(self) -> None:
        if self.lan_runner:
            await self.lan_runner.cleanup()
        self.lan_runner, self.lan_base, self.lan_peer = None, "", ""
        self.cast_links.clear()

    async def cast_link(self, instance_id: str, track_id: str, art: Optional[str], compatible: bool) -> dict:
        """A fresh link to a track for the cast device, with what the device needs to play it:
        {"url", "art", "type" (MIME), "hls", "segments" (Chromecast's name for the HLS pieces' format)}."""
        if not self.lan_runner:
            raise ServiceError("Not casting")
        source = await self.resolve_stream(instance_id, track_id, compatible)
        token = secrets.token_urlsafe(18)
        self.cast_links.put(token, CastLink(instance_id, track_id, art, compatible))
        base = f"{self.lan_base}/c/{token}"
        hls = source.hls or source.playlist is not None
        return {"url": base, "art": f"{base}/art" if art else None, "hls": hls,
                "type": "application/x-mpegurl" if hls else await self._media_type(source),
                "segments": "fmp4" if source.playlist and "#EXT-X-MAP" in source.playlist else None}

    async def _media_type(self, source: StreamSource) -> str:
        """What the audio is: devices pick a decoder before they fetch anything."""
        if source.content_type:
            return source.content_type
        if source.path:
            return mimetypes.guess_type(source.path)[0] or "audio/mpeg"
        assert self.session and source.url
        try:
            async with self.session.get(source.url, headers={**source.headers, "Range": "bytes=0-1"},
                                        timeout=aiohttp.ClientTimeout(total=10)) as upstream:
                kind = upstream.headers.get("Content-Type", "").split(";")[0].strip().lower()
        except (aiohttp.ClientError, TimeoutError):
            kind = ""
        if kind.startswith("audio/") or kind in ("video/mp4", "video/webm", "application/ogg"):
            # YouTube labels its audio-only formats video/*
            return kind.replace("video/", "audio/")
        return mimetypes.guess_type(urlparse(source.url).path)[0] or "audio/mpeg"

    def _cast_request(self, request: web.Request) -> CastLink:
        if request.remote != self.lan_peer:
            raise web.HTTPForbidden()
        link = self.cast_links.get(request.match_info["link"])
        if link is None:
            raise web.HTTPNotFound()
        return link

    async def _handle_cast_stream(self, request: web.Request) -> web.StreamResponse:
        link = self._cast_request(request)
        try:
            source = await self.resolve_stream(link.instance, link.track, link.compatible)
        except (ServiceError, NotImplementedError):
            raise web.HTTPNotFound()
        if source.path:
            return self._file(source)
        piece = self._cast_piece(request.match_info["link"], link)
        if source.playlist is not None:
            return self._rewrite(source.playlist, "", piece)
        assert source.url
        if source.hls:
            return await self._playlist(source.url, source.headers, piece)
        response = await self._proxy(request, source)
        if response.status == 403:
            self.forget(link.instance, link.track)
        return response

    async def _handle_cast_art(self, request: web.Request) -> web.StreamResponse:
        link = self._cast_request(request)
        if not link.art:
            raise web.HTTPNotFound()
        local = self.local_art(link.art) if self.local_art else None
        if local:
            return self._file(StreamSource(path=local))
        kept = self.offline.picture(link.instance, link.art) if self.offline else None
        if kept:
            return self._file(kept)
        service = self.lookup(link.instance)
        if service is None:
            raise web.HTTPNotFound()
        try:
            source = await service.art(link.art)
        except (ServiceError, NotImplementedError):
            raise web.HTTPNotFound()
        return self._file(source) if source.path else await self._proxy(request, source)

    async def _handle_cast_fetch(self, request: web.Request) -> web.StreamResponse:
        link = self._cast_request(request)
        try:
            url = _unb64(request.query["u"])
        except (KeyError, ValueError):
            raise web.HTTPBadRequest()
        # only pieces of this track's own playlists, so the device can't use this to fetch anything else
        if urlparse(url).scheme not in ("https", "http") or urlparse(url).hostname not in link.hosts:
            raise web.HTTPForbidden()
        if ".m3u8" in url.split("?")[0]:
            return await self._playlist(url, {}, self._cast_piece(request.match_info["link"], link))
        return await self._proxy(request, StreamSource(url=url))

    def _cast_piece(self, token: str, link: CastLink) -> Callable[[str], str]:
        """Rewrites an HLS URI for the cast device, and lets /f relay to its host."""
        def piece(url: str) -> str:
            host = urlparse(url).hostname
            if host:
                link.hosts.add(host)
            return f"{self.lan_base}/c/{token}/f?u={_b64(url)}"
        return piece

    # --- handlers ---

    def _check_key(self, request: web.Request) -> None:
        if not secrets.compare_digest(request.query.get("k", ""), self.key):
            raise web.HTTPForbidden()

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        self._check_key(request)
        instance, track_id = request.match_info["instance"], request.match_info["id"]
        try:
            source = await self.resolve_stream(instance, track_id)
        except ServiceError as e:
            raise web.HTTPNotFound(text=str(e), headers=CORS)
        except NotImplementedError:
            raise web.HTTPNotFound(headers=CORS)
        if source.path:
            return self._file(source)
        if source.playlist is not None:
            return self._rewrite(source.playlist, "")
        assert source.url
        if source.hls:
            try:
                return await self._playlist(source.url, source.headers)
            except web.HTTPForbidden:
                # signed playlist expired between resolving and playing: resolve afresh, once
                debuglog.debug("hls", "playlist expired, re-resolving", instance=instance, track=track_id)
                self.forget(instance, track_id)
                source = await self.resolve_stream(instance, track_id)
                return await self._playlist(source.url, source.headers)
        response = await self._proxy(request, source)
        if response.status == 403:
            # expired or IP-bound URL: drop it so the next attempt resolves afresh
            self.forget(instance, track_id)
        return response

    def _handler(self, resolve: Callable[[MusicService, str], Awaitable[StreamSource]]):
        async def handle(request: web.Request) -> web.StreamResponse:
            self._check_key(request)
            local = self.local_art(request.match_info["id"]) if self.local_art else None
            if local:
                return self._file(StreamSource(path=local))
            kept = self.offline.picture(request.match_info["instance"], request.match_info["id"]) if self.offline else None
            if kept:
                return self._file(kept)
            service = self.lookup(request.match_info["instance"])
            if service is None:
                raise web.HTTPNotFound(text="Unknown service")
            try:
                source = await resolve(service, request.match_info["id"])
            except ServiceError as e:
                raise web.HTTPNotFound(text=str(e), headers=CORS)
            except NotImplementedError:
                raise web.HTTPNotFound(headers=CORS)
            if source.path:
                return self._file(source)
            assert source.url
            return await self._proxy(request, source)
        return handle

    async def _handle_fetch(self, request: web.Request) -> web.StreamResponse:
        """Proxies an HLS playlist or segment referenced from a rewritten playlist."""
        self._check_key(request)
        try:
            url = _unb64(request.query["u"])
        except (KeyError, ValueError):
            raise web.HTTPBadRequest()
        if not url.startswith(("https://", "http://")):  # some radio stations still serve plain HTTP
            raise web.HTTPBadRequest()
        if ".m3u8" in url.split("?")[0]:
            return await self._playlist(url, {})
        return await self._proxy(request, StreamSource(url=url))

    # --- responses ---

    @staticmethod
    def _file(source: StreamSource) -> web.StreamResponse:
        response = web.FileResponse(source.path, headers=CORS)
        if source.content_type:
            response.content_type = source.content_type
        return response

    def _fetch_url(self, url: str) -> str:
        return f"{self.base_url}/fetch?k={quote(self.key)}&u={_b64(url)}"

    async def _playlist(self, url: str, headers: dict[str, str],
                        link: Optional[Callable[[str], str]] = None) -> web.Response:
        assert self.session
        try:
            # the proxy session passes bytes through compressed; playlists must be read as text
            async with self.session.get(url, headers={**headers, "Accept-Encoding": "identity"}) as upstream:
                if upstream.status >= 400:
                    debuglog.debug("hls", "playlist error", status=upstream.status, url=url[:160])
                    if upstream.status in (401, 403, 410):
                        raise web.HTTPForbidden(text=f"Upstream HTTP {upstream.status}", headers=CORS)
                    raise web.HTTPBadGateway(text=f"Upstream HTTP {upstream.status}", headers=CORS)
                text = await upstream.text()
                base = str(upstream.url)
        except aiohttp.ClientError as e:
            self.logger.warning(f"Playlist request failed: {e}")
            raise web.HTTPBadGateway(headers=CORS)
        return self._rewrite(text, base, link)

    def _rewrite(self, text: str, base: str, link: Optional[Callable[[str], str]] = None) -> web.Response:
        """Points every URI in an HLS playlist at /fetch (for a cast device, `link`), so all segments
        come through this server."""
        link = link or self._fetch_url
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                line = URI_ATTR.sub(lambda m: f'URI="{link(urljoin(base, m.group(1)))}"', line)
            elif stripped:
                line = link(urljoin(base, stripped))
            lines.append(line)
        return web.Response(text="\n".join(lines) + "\n", content_type=HLS_TYPE, headers=CORS)

    async def _proxy(self, request: web.Request, source: StreamSource) -> web.StreamResponse:
        assert self.session
        headers = dict(source.headers)
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]
        try:
            upstream = await self.session.get(source.url, headers=headers)
        except aiohttp.ClientError as e:
            self.logger.warning(f"Upstream request failed: {e}")
            debuglog.debug("proxy", "request failed", error=str(e), url=(source.url or "")[:160])
            raise web.HTTPBadGateway(headers=CORS)
        response: Optional[web.StreamResponse] = None
        try:
            if upstream.status >= 400:
                debuglog.debug("proxy", "upstream error", status=upstream.status, url=source.url[:160],
                               range=headers.get("Range"))
                return web.Response(status=403 if upstream.status == 403 else 502,
                                    text=f"Upstream HTTP {upstream.status}", headers=CORS)
            response = web.StreamResponse(status=upstream.status, headers=CORS)
            for name in PASSTHROUGH_HEADERS:
                if name in upstream.headers:
                    response.headers[name] = upstream.headers[name]
            if source.content_type:
                response.headers["Content-Type"] = source.content_type
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await response.write(chunk)
            await response.write_eof()
            return response
        except (ConnectionResetError, aiohttp.ClientConnectionError):
            # the player seeking or skipping closes the connection mid-stream; that's normal
            if response is not None and response.prepared:
                return response
            raise web.HTTPBadGateway(headers=CORS)
        finally:
            upstream.release()
