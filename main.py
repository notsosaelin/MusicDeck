import asyncio
import functools
import json
import os
import ssl
import time
from typing import Any, Awaitable, Optional

import aiohttp
import certifi

# The decky plugin module is provided by Decky Loader at runtime (see decky.pyi for intellisense)
import decky

from musicdeck import debuglog, lyrics
from musicdeck.playlists import PlaylistStore
from musicdeck.server import StreamServer
from musicdeck.services import PLANNED_TYPES, SERVICE_TYPES, MusicService, ServiceError
from musicdeck.settings import Settings

# Config keys never sent to the frontend
SECRET_KEYS = {"password", "token", "refresh_token", "salt", "secret", "cookies", "auth"}
# Settings the frontend may write directly
SCROBBLE_DEDUPE = 90
FRONTEND_SETTINGS = {"qam_mode", "volume", "eq", "playback", "debug_log"}


async def _result(work: Awaitable[Any]) -> dict[str, Any]:
    """Wraps a call so the frontend gets {ok, data} or {ok: false, error} instead of a bare exception."""
    try:
        return {"ok": True, "data": await work}
    except ServiceError as e:
        return {"ok": False, "error": str(e)}
    except NotImplementedError:
        return {"ok": False, "error": "Not supported by this service"}
    except Exception as e:
        decky.logger.exception("Unexpected error")
        return {"ok": False, "error": f"Unexpected error: {e}"}


def _when_ready(method):
    """Frontend calls can arrive while _main is still starting up; hold them until it's done."""
    @functools.wraps(method)
    async def wrapper(self, *args):
        await self.ready.wait()
        return await method(self, *args)
    return wrapper


class Plugin:
    settings: Settings
    server: StreamServer
    playlists: PlaylistStore
    instances: dict[str, MusicService]

    def __init__(self):
        self.ready = asyncio.Event()
        self.http: Optional[aiohttp.ClientSession] = None  # shared client for LRCLIB etc.
        self.qobuz_logins: dict[str, Any] = {}
        self.tidal_logins: dict[str, Any] = {}
        self.jellyfin_logins: dict[str, Any] = {}
        self.spotify_logins: dict[str, Any] = {}
        self.recent_scrobbles: dict[tuple[str, str, bool], float] = {}

    # --- lifecycle ---

    async def _main(self):
        self.settings = Settings(decky.DECKY_PLUGIN_SETTINGS_DIR)
        self.playlists = PlaylistStore(decky.DECKY_PLUGIN_SETTINGS_DIR)
        debuglog.configure(decky.DECKY_PLUGIN_LOG_DIR, bool(self.settings.get("debug_log")))
        self.instances = {}
        self.server = StreamServer(self.instances.get, decky.logger, local_art=self.playlists.art_path)
        self.http = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
            timeout=aiohttp.ClientTimeout(total=10))
        try:
            await self.server.start()
            for service in self.settings.services():
                await self._start_instance(service)
            decky.logger.info(f"MusicDeck started with {len(self.instances)} service(s)")
        finally:
            # release waiting calls even on failure, so the UI shows errors instead of hanging
            self.ready.set()

    async def _unload(self):
        for instance in list(self.instances.values()):
            await instance.stop()
        self.instances.clear()
        await self.server.stop()
        if self.http:
            await self.http.close()

    async def _uninstall(self):
        pass

    async def _start_instance(self, service: dict[str, Any]) -> None:
        old = self.instances.pop(service["id"], None)
        if old:
            await old.stop()
        cls = SERVICE_TYPES.get(service["type"])
        if cls is None:
            decky.logger.warning(f"Skipping service of unknown type {service['type']}")
            return
        instance = cls(service["id"], service["config"], decky.DECKY_PLUGIN_RUNTIME_DIR)
        if hasattr(instance, "save_config"):
            # services whose credentials rotate (Spotify refresh tokens) persist them through this
            instance.save_config = lambda config, sid=service["id"]: self._persist_config(sid, config)
        try:
            await instance.start()
        except Exception:
            decky.logger.exception(f"Failed to start service {service['id']}")
        self.instances[service["id"]] = instance

    def _persist_config(self, instance_id: str, config: dict[str, Any]) -> None:
        stored = self.settings.find_service(instance_id)
        if stored:
            stored["config"] = dict(config)
            self.settings.save()

    def _instance(self, instance_id: str) -> MusicService:
        instance = self.instances.get(instance_id)
        if instance is None:
            raise ServiceError("That service is no longer configured")
        return instance

    # --- state & settings ---

    def _public_service(self, service: dict[str, Any]) -> dict[str, Any]:
        instance = self.instances.get(service["id"])
        return {
            "id": service["id"],
            "type": service["type"],
            "name": service["name"],
            "config": {k: v for k, v in service["config"].items() if k not in SECRET_KEYS},
            "has_credentials": any(k in SECRET_KEYS for k in service["config"]),
            "features": sorted(instance.features) if instance else [],
            "status": instance.status() if instance else {},
        }

    @_when_ready
    async def get_state(self) -> dict[str, Any]:
        return {
            "services": [self._public_service(s) for s in self.settings.services()],
            "active_service": self.settings.get("active_service"),
            "qam_mode": self.settings.get("qam_mode"),
            "volume": self.settings.get("volume"),
            "eq": self.settings.get("eq"),
            "playback": self.settings.get("playback"),
            "debug_log": bool(self.settings.get("debug_log")),
            "debug_log_path": debuglog.path,
            # file pickers start here; distros don't all use /home/deck
            "user_home": decky.DECKY_USER_HOME,
            "server": {"base": self.server.base_url, "key": self.server.key},
            "service_types": [{"type": c.type, "display_name": c.display_name} for c in SERVICE_TYPES.values()],
            "planned_types": PLANNED_TYPES,
        }

    @_when_ready
    async def set_setting(self, key: str, value: Any) -> None:
        if key not in FRONTEND_SETTINGS:
            raise ValueError(f"Setting {key} is not writable")
        self.settings.set(key, value)
        if key == "debug_log":
            debuglog.configure(decky.DECKY_PLUGIN_LOG_DIR, bool(value))

    @_when_ready
    async def save_service(self, service: dict[str, Any]) -> dict[str, Any]:
        async def work():
            cls = SERVICE_TYPES.get(service.get("type", ""))
            if cls is None:
                raise ServiceError("Unknown service type")
            previous = self.settings.find_service(service["id"]) if service.get("id") else None
            previous_config = previous["config"] if previous else None
            config = cls.prepare_config(dict(service.get("config", {})), previous_config)
            config = await cls.authenticate(config, previous_config, decky.DECKY_PLUGIN_RUNTIME_DIR)
            saved = self.settings.upsert_service({
                "id": service.get("id"),
                "type": cls.type,
                "name": (service.get("name") or "").strip() or cls.display_name,
                "config": config,
            })
            await self._start_instance(saved)
            return self._public_service(saved)
        return await _result(work())

    @_when_ready
    async def remove_service(self, instance_id: str) -> None:
        instance = self.instances.pop(instance_id, None)
        if instance:
            await instance.stop()
        decky.logger.info(f"Removed source {instance_id} ({getattr(instance, 'type', '?')})")
        self.settings.remove_service(instance_id)
        self.playlists.delete_for_instance(instance_id)

    @_when_ready
    async def set_active_service(self, instance_id: str) -> None:
        if self.settings.find_service(instance_id):
            self.settings.set("active_service", instance_id)

    @_when_ready
    async def test_service(self, instance_id: str) -> dict[str, Any]:
        return await _result(self._call(instance_id, lambda s: s.test()))

    @_when_ready
    async def rescan(self, instance_id: str) -> None:
        instance = self.instances.get(instance_id)
        if instance is not None and hasattr(instance, "rescan"):
            instance.rescan()

    # --- browsing ---

    async def _call(self, instance_id: str, fn) -> Any:
        return await fn(self._instance(instance_id))

    @_when_ready
    async def home(self, instance_id: str) -> dict[str, Any]:
        async def work():
            sections = await self._instance(instance_id).home()
            ours = self.playlists.for_instance(instance_id)
            # MusicDeck playlists come first: they're what the user made
            return ([{"title": "Your playlists", "items": ours}] if ours else []) + sections
        return await _result(work())

    @_when_ready
    async def open_collection(self, instance_id: str, kind: str, collection_id: str) -> dict[str, Any]:
        if PlaylistStore.is_ours(collection_id):
            async def local():
                try:
                    return self.playlists.page(collection_id)
                except KeyError:
                    raise ServiceError("Playlist not found")
            return await _result(local())
        return await _result(self._call(instance_id, lambda s: s.open(kind, collection_id)))

    @_when_ready
    async def search(self, instance_id: str, query: str) -> dict[str, Any]:
        return await _result(self._call(instance_id, lambda s: s.search(query)))

    # --- playback ---

    @_when_ready
    async def log_events(self, entries: list[dict[str, Any]]) -> None:
        """Batched frontend events for the debug log: [{"t": ms, "event": str, "data": {...}}]."""
        for entry in entries[:200]:
            debuglog.debug("player", entry.get("event", "?"), at=entry.get("t"), **(entry.get("data") or {}))

    @_when_ready
    async def stream_info(self, instance_id: str, track_id: str) -> dict[str, Any]:
        """Resolves a track before playback (and warms the cache when prefetching the next one).
        Tells the player whether to use hls.js."""
        async def work():
            source = await self.server.resolve_stream(instance_id, track_id)
            debuglog.debug("stream", "resolved", instance=instance_id, track=track_id, hls=source.hls,
                           local=bool(source.path), expires=source.expires)
            return {"hls": source.hls, "gain": source.gain}
        return await _result(work())

    @_when_ready
    async def forget_stream(self, instance_id: str, track_id: str) -> None:
        """Drops a cached stream URL (e.g. expired mid-play) so the next attempt resolves afresh."""
        debuglog.debug("stream", "forget", instance=instance_id, track=track_id)
        self.server.forget(instance_id, track_id)

    @_when_ready
    async def qobuz_login_start(self) -> dict[str, Any]:
        """Starts a Qobuz OAuth sign-in; the frontend opens the returned URL in Steam's browser."""
        async def work():
            from musicdeck.services.qobuz import QobuzLogin
            for old in self.qobuz_logins.values():
                await old.close()
            login = QobuzLogin(decky.DECKY_PLUGIN_RUNTIME_DIR)
            self.qobuz_logins = {login.id: login}
            url = await login.start()
            return {"login_id": login.id, "url": url}
        return await _result(work())

    @_when_ready
    async def qobuz_login_poll(self, login_id: str, service: dict[str, Any]) -> dict[str, Any]:
        """Waiting / error, or once signed in: saves the source (token only) and returns it."""
        login = self.qobuz_logins.get(login_id)
        if not login:
            return {"status": "error", "error": "Sign-in expired; start again"}
        if login.status != "done":
            return {"status": login.status, "error": login.error}
        self.qobuz_logins.pop(login_id, None)
        saved = await self.save_service({**service, "type": "qobuz",
                                         "config": {**service.get("config", {}), **(login.result or {})}})
        if not saved["ok"]:
            return {"status": "error", "error": saved["error"]}
        return {"status": "done", "service": saved["data"]}

    @_when_ready
    async def qobuz_login_cancel(self, login_id: str) -> None:
        login = self.qobuz_logins.pop(login_id, None)
        if login:
            if login._task:
                login._task.cancel()
            await login.close()

    @_when_ready
    async def jellyfin_login_start(self, url: str) -> dict[str, Any]:
        """Starts Jellyfin Quick Connect; the frontend shows the returned code to approve."""
        async def work():
            from musicdeck.services.jellyfin import JellyfinQuickConnect
            for old in self.jellyfin_logins.values():
                await old.close()
            login = JellyfinQuickConnect(url)
            self.jellyfin_logins = {login.id: login}
            code = await login.start()
            return {"login_id": login.id, "code": code}
        return await _result(work())

    @_when_ready
    async def jellyfin_login_poll(self, login_id: str, service: dict[str, Any]) -> dict[str, Any]:
        """Waiting / error, or once approved: saves the source (token only) and returns it."""
        login = self.jellyfin_logins.get(login_id)
        if not login:
            return {"status": "error", "error": "Sign-in expired; start again"}
        if login.status != "done":
            return {"status": login.status, "error": login.error}
        self.jellyfin_logins.pop(login_id, None)
        saved = await self.save_service({**service, "type": "jellyfin",
                                         "config": {**service.get("config", {}), **(login.result or {})}})
        if not saved["ok"]:
            return {"status": "error", "error": saved["error"]}
        return {"status": "done", "service": saved["data"]}

    @_when_ready
    async def jellyfin_login_cancel(self, login_id: str) -> None:
        login = self.jellyfin_logins.pop(login_id, None)
        if login:
            await login.close()

    @_when_ready
    async def tidal_login_start(self) -> dict[str, Any]:
        """Starts TIDAL's device login; the frontend opens the returned link in Steam's browser."""
        async def work():
            from musicdeck.services.tidal import TidalLogin
            for old in self.tidal_logins.values():
                await old.close()
            login = TidalLogin()
            self.tidal_logins = {login.id: login}
            url = await login.start()
            return {"login_id": login.id, "url": url}
        return await _result(work())

    @_when_ready
    async def tidal_login_poll(self, login_id: str, service: dict[str, Any]) -> dict[str, Any]:
        """Waiting / error, or once approved: saves the source (refresh token only) and returns it."""
        login = self.tidal_logins.get(login_id)
        if not login:
            return {"status": "error", "error": "Sign-in expired; start again"}
        if login.status != "done":
            return {"status": login.status, "error": login.error}
        self.tidal_logins.pop(login_id, None)
        saved = await self.save_service({**service, "type": "tidal",
                                         "config": {**service.get("config", {}), **(login.result or {})}})
        if not saved["ok"]:
            return {"status": "error", "error": saved["error"]}
        return {"status": "done", "service": saved["data"]}

    @_when_ready
    async def tidal_login_cancel(self, login_id: str) -> None:
        login = self.tidal_logins.pop(login_id, None)
        if login:
            await login.close()

    @_when_ready
    async def spotify_login_start(self, client_id: str = "") -> dict[str, Any]:
        """Starts Spotify's two-step sign-in; the frontend follows the URL the poll reports."""
        async def work():
            from musicdeck.services.spotify import SpotifyLogin
            for old in self.spotify_logins.values():
                await old.cancel()
            login = SpotifyLogin(decky.DECKY_PLUGIN_RUNTIME_DIR, client_id or "")
            self.spotify_logins = {login.id: login}
            url = await login.start()
            return {"login_id": login.id, "url": url}
        return await _result(work())

    @_when_ready
    async def spotify_login_poll(self, login_id: str, service: dict[str, Any]) -> dict[str, Any]:
        login = self.spotify_logins.get(login_id)
        if not login:
            return {"status": "error", "error": "Sign-in expired; start again"}
        if login.step == "error":
            self.spotify_logins.pop(login_id, None)
            return {"status": "error", "error": login.error}
        if login.step != "done":
            return {"status": "waiting", "url": login.url}
        self.spotify_logins.pop(login_id, None)
        saved = await self.save_service({**service, "type": "spotify",
                                         "config": {**service.get("config", {}), **(login.result or {})}})
        if not saved["ok"]:
            return {"status": "error", "error": saved["error"]}
        return {"status": "done", "service": saved["data"]}

    @_when_ready
    async def spotify_login_cancel(self, login_id: str) -> None:
        login = self.spotify_logins.pop(login_id, None)
        if login:
            await login.cancel()

    @_when_ready
    async def youtube_update(self, instance_id: str) -> dict[str, Any]:
        """Updates yt-dlp for YouTube sources (see musicdeck/ytdlp_update.py)."""
        async def work():
            instance = self._instance(instance_id)
            if not hasattr(instance, "update_ytdlp"):
                raise ServiceError("Not a YouTube source")
            return await instance.update_ytdlp()
        return await _result(work())

    # --- per-track features ---

    @_when_ready
    async def star(self, instance_id: str, track_id: str, starred: bool) -> dict[str, Any]:
        return await _result(self._call(instance_id, lambda s: s.star(track_id, starred)))

    @_when_ready
    async def scrobble(self, instance_id: str, track_id: str, submission: bool) -> dict[str, Any]:
        if not self.settings.get("playback").get("scrobble", True):
            return {"ok": True, "data": None}
        # Safety net against double counting (reloads, retries): the same report for the same track
        # within SCROBBLE_DEDUPE seconds is dropped. A genuine replay can only start after the song
        # ends, so it's never inside the window.
        key = (instance_id, track_id, submission)
        now = time.time()
        if now - self.recent_scrobbles.get(key, 0) < SCROBBLE_DEDUPE:
            debuglog.debug("scrobble", "duplicate dropped", track=track_id, submission=submission)
            return {"ok": True, "data": None}
        self.recent_scrobbles = {k: t for k, t in self.recent_scrobbles.items() if now - t < SCROBBLE_DEDUPE}
        self.recent_scrobbles[key] = now
        return await _result(self._call(instance_id, lambda s: s.scrobble(track_id, submission)))

    @_when_ready
    async def get_lyrics(self, instance_id: str, track: dict[str, Any]) -> dict[str, Any]:
        async def work():
            instance = self.instances.get(instance_id)
            found = await instance.lyrics(track) if instance and "lyrics" in instance.features else None
            if not found and self.http:
                found = await lyrics.from_lrclib(self.http, track)
            return found
        return await _result(work())

    @_when_ready
    async def radio(self, instance_id: str, track: dict[str, Any]) -> dict[str, Any]:
        return await _result(self._call(instance_id, lambda s: s.radio(track)))

    @_when_ready
    async def user_playlists(self, instance_id: str) -> dict[str, Any]:
        return await _result(self._call(instance_id, lambda s: s.user_playlists()))

    @_when_ready
    async def add_to_playlist(self, instance_id: str, playlist_id: str, track_ids: list[str]) -> dict[str, Any]:
        return await _result(self._call(instance_id, lambda s: s.add_to_playlist(playlist_id, track_ids)))

    # --- session (queue) persistence ---

    def _session_path(self) -> str:
        return os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "session.json")

    @_when_ready
    async def save_session(self, session: dict[str, Any]) -> None:
        """Stores the queue locally (every source) and on the server when it supports queue sync."""
        session = {**session, "saved_at": time.time()}
        os.makedirs(decky.DECKY_PLUGIN_RUNTIME_DIR, exist_ok=True)
        tmp = self._session_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(session, f)
        os.replace(tmp, self._session_path())
        items = session.get("queue", [])
        instance_ids = {item["instanceId"] for item in items}
        if len(instance_ids) == 1 and self.settings.get("playback").get("queue_sync", True):
            instance = self.instances.get(instance_ids.pop())
            if instance and "queue_sync" in instance.features:
                try:
                    await instance.save_queue([i["track"]["id"] for i in items], session.get("index", 0),
                                              session.get("position", 0))
                except Exception as e:
                    decky.logger.warning(f"Queue sync failed: {e}")

    @_when_ready
    async def load_session(self) -> Optional[dict[str, Any]]:
        """The most recent queue: the local copy, or the server's when it was changed more recently
        (i.e. the user kept listening on another device)."""
        local: Optional[dict[str, Any]] = None
        try:
            with open(self._session_path(), "r", encoding="utf-8") as f:
                local = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        active = self.instances.get(self.settings.get("active_service") or "")
        if active and "queue_sync" in active.features and self.settings.get("playback").get("queue_sync", True):
            try:
                remote = await active.load_queue()
            except Exception:
                remote = None
            if remote:
                changed = _parse_time(remote.get("changed"))
                if not local or (changed and changed > local.get("saved_at", 0) + 5):
                    return {"queue": [{"instanceId": active.instance_id, "track": t} for t in remote["tracks"]],
                            "index": remote["index"], "position": remote["position"],
                            "from_device": remote.get("changed_by")}
        # only resume a queue from the source that's live now (no mixing sources)
        if local and any(item.get("instanceId") != self.settings.get("active_service")
                         for item in local.get("queue", [])):
            return None
        return local


    # --- MusicDeck playlists (stored on the device, per source) ---

    @_when_ready
    async def local_playlists(self, instance_id: str) -> list[dict[str, Any]]:
        return self.playlists.for_instance(instance_id)

    @_when_ready
    async def create_playlist(self, instance_id: str, name: str, tracks: list[dict[str, Any]]) -> dict[str, Any]:
        return self.playlists.create(instance_id, name, tracks)

    @_when_ready
    async def save_as_playlist(self, instance_id: str, kind: str, collection_id: str) -> dict[str, Any]:
        """Copies a source's playlist or album into a MusicDeck playlist (so it can be kept, edited,
        and later downloaded)."""
        async def work():
            page = await self._instance(instance_id).open(kind, collection_id)
            return self.playlists.create(instance_id, page["title"], page["tracks"],
                                         source={"kind": kind, "id": collection_id})
        return await _result(work())

    @_when_ready
    async def playlist_add(self, playlist_id: str, tracks: list[dict[str, Any]]) -> int:
        return self.playlists.add(playlist_id, tracks)

    @_when_ready
    async def playlist_remove(self, playlist_id: str, track_id: str) -> None:
        self.playlists.remove(playlist_id, track_id)

    @_when_ready
    async def playlist_rename(self, playlist_id: str, name: str) -> None:
        self.playlists.rename(playlist_id, name)

    @_when_ready
    async def playlist_set_art(self, playlist_id: str, image_path: str) -> dict[str, Any]:
        async def work():
            try:
                self.playlists.set_art(playlist_id, image_path)
            except (ValueError, OSError) as e:
                raise ServiceError(str(e))
        return await _result(work())

    @_when_ready
    async def playlist_clear_art(self, playlist_id: str) -> None:
        self.playlists.clear_art(playlist_id)

    @_when_ready
    async def playlist_delete(self, playlist_id: str) -> None:
        self.playlists.delete(playlist_id)


def _parse_time(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
