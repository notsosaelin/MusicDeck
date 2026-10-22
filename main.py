import asyncio
import functools
import json
import os
import ssl
import time
from datetime import datetime
from typing import Any, Awaitable, Optional

import aiohttp
import certifi

# The decky plugin module is provided by Decky Loader at runtime (see decky.pyi for intellisense)
import decky

from musicdeck import debuglog, lyrics
from musicdeck.listenbrainz import ListenBrainz
from musicdeck.playlists import PlaylistStore
from musicdeck.server import StreamServer
from musicdeck.services import PLANNED_TYPES, SERVICE_TYPES, MusicService, ServiceError
from musicdeck.settings import Settings

# Config keys never sent to the frontend; a service storing a secret under another name adds it here
SECRET_KEYS = {"password", "token", "refresh_token", "salt", "secret", "cookies", "auth"}
# Settings the frontend may write directly (set_setting); everything else has its own endpoint
FRONTEND_SETTINGS = {"qam_mode", "volume", "eq", "playback", "debug_log"}
# The same play report for the same track within this many seconds is a duplicate (see scrobble)
SCROBBLE_DEDUPE = 90


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
    """MusicDeck's backend. Decky calls _main/_unload; every other public method is an endpoint the
    frontend calls through `callable()` (typed in src/api.ts).

    Conventions: endpoints that can fail for reasons the user should see return
    {"ok": True, "data": ...} or {"ok": False, "error": message} via _result; everything waits for
    start-up via @_when_ready. Sources are `MusicService` instances keyed by their instance id.
    """

    settings: Settings
    server: StreamServer
    playlists: PlaylistStore
    instances: dict[str, MusicService]

    def __init__(self):
        self.ready = asyncio.Event()
        self.http: Optional[aiohttp.ClientSession] = None  # shared client for LRCLIB etc.
        # the sign-in in progress, if any (SignIn by id)
        self.sign_ins: dict[str, Any] = {}
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
        self.listenbrainz = ListenBrainz(self.http, os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR,
                                                                 "listenbrainz-pending.json"))
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
        # lets a service persist config it changes itself (rotated tokens, favourite stations)
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
            "listenbrainz": {"user": self.settings.get("listenbrainz").get("user", ""),
                             "pending": self.listenbrainz.pending_count()},
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
            others = [s for s in self.settings.services() if s["type"] == cls.type and s["id"] != service.get("id")]
            account = cls.account(config)
            twin = next((s for s in others if account and cls.account(s["config"]) == account), None)
            if twin:
                raise ServiceError(f"That account is already added as “{twin['name']}”")
            saved = self.settings.upsert_service({
                "id": service.get("id"),
                "type": cls.type,
                "name": (service.get("name") or "").strip() or _unique_name(cls.display_name, others),
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
            instance.remove()
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

    # --- sign-in (OAuth pages, device codes; see services/base.py:SignIn) ---

    @_when_ready
    async def sign_in_start(self, service_type: str, options: dict[str, Any]) -> dict[str, Any]:
        """Starts a sign-in for sources that don't take a password in the form (see SignIn). The
        frontend opens `url` in Steam's browser or shows `code`, then polls sign_in_poll."""
        async def work():
            cls = SERVICE_TYPES.get(service_type)
            attempt = cls.sign_in(options or {}, decky.DECKY_PLUGIN_RUNTIME_DIR) if cls else None
            if attempt is None:
                raise ServiceError("This source doesn't sign in that way")
            for old in self.sign_ins.values():  # one attempt at a time: the newest wins
                await old.close()
            self.sign_ins = {attempt.id: attempt}
            await attempt.start()
            return {"login_id": attempt.id, "url": attempt.url, "code": attempt.code}
        return await _result(work())

    @_when_ready
    async def sign_in_poll(self, login_id: str, service: dict[str, Any]) -> dict[str, Any]:
        """Waiting (with the page to show, which can change), error, or done: then the source is
        saved with the sign-in's result and returned."""
        attempt = self.sign_ins.get(login_id)
        if not attempt:
            return {"status": "error", "error": "Sign-in expired; start again"}
        if attempt.status == "error":
            self.sign_ins.pop(login_id, None)
            return {"status": "error", "error": attempt.error}
        if attempt.status != "done":
            return {"status": "waiting", "url": attempt.url}
        self.sign_ins.pop(login_id, None)
        saved = await self.save_service({**service, "config": {**service.get("config", {}), **(attempt.result or {})}})
        if not saved["ok"]:
            return {"status": "error", "error": saved["error"]}
        return {"status": "done", "service": saved["data"]}

    @_when_ready
    async def sign_in_cancel(self, login_id: str) -> None:
        attempt = self.sign_ins.pop(login_id, None)
        if attempt:
            await attempt.close()

    # --- source-specific ---

    @_when_ready
    async def youtube_update(self, instance_id: str) -> dict[str, Any]:
        """Updates yt-dlp for YouTube sources (see musicdeck/ytdlp_update.py)."""
        async def work():
            instance = self._instance(instance_id)
            if not hasattr(instance, "update_ytdlp"):
                raise ServiceError("Not a YouTube source")
            return await instance.update_ytdlp()
        return await _result(work())

    @_when_ready
    async def steam_library(self, instance_id: str, owned: list[dict[str, Any]], running: Optional[int]) -> None:
        """Owned soundtracks and the running game, which only Steam's UI knows (src/steam.ts)."""
        instance = self.instances.get(instance_id)
        if instance is not None and hasattr(instance, "set_library"):
            instance.set_library(owned, running)

    @_when_ready
    async def on_air(self, instance_id: str, track_id: str) -> dict[str, Any]:
        """What a live station is playing right now (radio), or None."""
        async def work():
            instance = self._instance(instance_id)
            return await instance.on_air(track_id) if hasattr(instance, "on_air") else None
        return await _result(work())

    # --- per-track features ---

    @_when_ready
    async def star(self, instance_id: str, track_id: str, starred: bool) -> dict[str, Any]:
        return await _result(self._call(instance_id, lambda s: s.star(track_id, starred)))

    @_when_ready
    async def scrobble(self, instance_id: str, track_id: str, submission: bool,
                       track: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """A play started (submission=False) or counts as a listen (True). Goes to the source's own
        server when it keeps play history and "Report plays" is on, and to ListenBrainz if linked."""
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

        token = self.settings.get("listenbrainz").get("token")
        instance = self.instances.get(instance_id)
        if token and track and instance:
            report = self.listenbrainz.listen if submission else self.listenbrainz.now_playing
            asyncio.get_event_loop().create_task(report(token, track, instance.display_name))
        if not self.settings.get("playback").get("scrobble", True) or not instance \
                or "scrobble" not in instance.features:
            return {"ok": True, "data": None}
        return await _result(self._call(instance_id, lambda s: s.scrobble(track_id, submission)))

    @_when_ready
    async def set_listenbrainz(self, token: str) -> dict[str, Any]:
        """Links ListenBrainz with a user token (from listenbrainz.org/settings), or unlinks with ""."""
        async def work():
            token_ = (token or "").strip()
            if not token_:
                self.settings.set("listenbrainz", {"token": "", "user": ""})
                return {"user": ""}
            try:
                user = await self.listenbrainz.validate(token_)
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach ListenBrainz: {e}") from e
            if not user:
                raise ServiceError("ListenBrainz didn't accept that token")
            self.settings.set("listenbrainz", {"token": token_, "user": user})
            return {"user": user}
        return await _result(work())

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


def _unique_name(base: str, others: list[dict[str, Any]]) -> str:
    """The default name for a new source: "Spotify", then "Spotify 2" for a second account."""
    taken = {s["name"] for s in others}
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base} {n}"
    return name


def _parse_time(value: Optional[str]) -> Optional[float]:
    """An ISO 8601 time (as Subsonic servers send it) as epoch seconds, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
