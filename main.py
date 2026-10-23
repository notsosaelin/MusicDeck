import asyncio
import functools
import json
import os
import time
from datetime import datetime
from typing import Any, Awaitable, Optional

import aiohttp

# The decky plugin module is provided by Decky Loader at runtime (see decky.pyi for intellisense)
import decky

from musicdeck import backup, debuglog, lyrics
from musicdeck.background import cancel_all, spawn
from musicdeck.audio_output import OutputWatcher
from musicdeck import version
from musicdeck.cast import CastManager
from musicdeck.lastfm import Lastfm, LastfmError
from musicdeck.listenbrainz import ListenBrainz
from musicdeck.net import tls
from musicdeck.offline import OfflineStore
from musicdeck.playlists import PlaylistStore
from musicdeck.server import StreamServer
from musicdeck.services import PLANNED_TYPES, SERVICE_TYPES, MusicService, ServiceError, StreamSource
from musicdeck.recents import Recents
from musicdeck.settings import DEFAULTS, Settings, save_json

# Config keys never sent to the frontend; a service storing a secret under another name adds it here
SECRET_KEYS = {"password", "token", "server_token", "refresh_token", "salt", "secret", "cookies", "auth"}
# Marks a source restored from a backup whose sign-in has to be done again (backups hold none)
RESTORED_SIGNED_OUT = "sign_in_again"
# Settings the frontend may write directly (set_setting); everything else has its own endpoint
FRONTEND_SETTINGS = {"qam_mode", "volume", "eq", "eq_per_output", "playback", "debug_log", "center"}
# the media center's choices (settings.DEFAULTS["center"]); anything else is refused
CENTER_CHOICES = {"open_on": {"last", "playing", "home", "library"}, "background": {"art", "plain"},
                  "cards": {"small", "medium", "large"}, "lyrics": {"small", "medium", "large"},
                  "idle_off": {0, 1, 2, 5, 10}}
# What can be set to play with a game: things with a track list of their own
GAME_MUSIC_KINDS = {"album", "playlist"}
# The same play report for the same track within this many seconds is a duplicate (see scrobble)
SCROBBLE_DEDUPE = 90


async def _result(work: Awaitable[Any]) -> dict[str, Any]:
    """Wraps a call so the frontend gets {ok, data} or {ok: false, error} instead of a bare exception."""
    try:
        return {"ok": True, "data": await work}
    except ServiceError as e:
        return {"ok": False, "error": debuglog.redact(str(e))}
    except NotImplementedError:
        return {"ok": False, "error": "Not supported by this service"}
    except Exception as e:
        decky.logger.exception("Unexpected error")
        return {"ok": False, "error": debuglog.redact(f"Unexpected error: {e}")}


def _when_ready(method):
    """Frontend calls can arrive while _main is still starting up; hold them until it's done."""
    @functools.wraps(method)
    async def wrapper(self, *args):
        await self.ready.wait()
        return await method(self, *args)
    return wrapper


async def _announce_change() -> None:
    """Tells every loaded copy of the frontend to reload the state (see sources.ts). Decky can keep
    showing a plugin page from before a reload, and changes made there must still reach the copy
    that's playing."""
    await decky.emit("state_changed")


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
        # the output sound plays on ({"id", "name"}), when PipeWire says
        self.output: Optional[dict[str, str]] = None
        self.output_watcher: Optional[OutputWatcher] = None
        # a Last.fm sign-in waiting for the user to approve it: {"key", "secret", "token"}
        self.lastfm_link: Optional[dict[str, str]] = None

    # --- lifecycle ---

    async def _main(self):
        decky.logger.addFilter(debuglog.Redacting())
        # sign-ins, Spotify's stored credentials, listening history and logs: this user's only, even
        # on distros whose home folders others can read
        for folder in (decky.DECKY_PLUGIN_SETTINGS_DIR, decky.DECKY_PLUGIN_RUNTIME_DIR, decky.DECKY_PLUGIN_LOG_DIR):
            try:
                os.makedirs(folder, exist_ok=True)
                os.chmod(folder, 0o700)
            except OSError as e:
                decky.logger.warning(f"Couldn't make {folder} private: {e}")
        self.settings = Settings(decky.DECKY_PLUGIN_SETTINGS_DIR)
        self.playlists = PlaylistStore(decky.DECKY_PLUGIN_SETTINGS_DIR)
        self.recents = Recents(decky.DECKY_PLUGIN_RUNTIME_DIR)
        debuglog.configure(decky.DECKY_PLUGIN_LOG_DIR, bool(self.settings.get("debug_log")))
        self.instances = {}
        self.lifecycle = asyncio.Lock()
        self.offline = OfflineStore(decky.DECKY_PLUGIN_RUNTIME_DIR, _announce_change)
        self.server = StreamServer(self.instances.get, decky.logger, local_art=self.playlists.art_path,
                                   offline=self.offline)
        self.http = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls()),
            timeout=aiohttp.ClientTimeout(total=10),
            # none of these sites needs cookies, and NetEase cuts its search results short for a
            # client that keeps the cookie it sets
            cookie_jar=aiohttp.DummyCookieJar())
        self.cast = CastManager(self.server, self.instances.get, decky.emit, self.http, decky.logger)
        self.listenbrainz = ListenBrainz(self.http, os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR,
                                                                 "listenbrainz-pending.json"))
        self.lastfm = Lastfm(self.http, os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "lastfm-pending.json"))
        try:
            await self.server.start()
            for service in self.settings.services():
                await self._start_instance(service)
            decky.logger.info(f"MusicDeck started with {len(self.instances)} service(s)")
            self._sync_offline()  # finishes downloads an earlier session left
            self.output_watcher = OutputWatcher(self._output_changed, decky.logger)
            self.output_watcher.start()
        finally:
            # release waiting calls even on failure, so the UI shows errors instead of hanging
            self.ready.set()

    async def _unload(self):
        await cancel_all()
        if self.output_watcher:
            await self.output_watcher.stop()
        await self.cast.disconnect()
        await self.offline.stop()
        for instance in list(self.instances.values()):
            await instance.stop()
        self.instances.clear()
        await self.server.stop()
        if self.http:
            await self.http.close()

    async def _uninstall(self):
        pass

    async def _start_instance(self, service: dict[str, Any]) -> None:
        # one start/stop at a time: two overlapping (a save while a token rotation restarts it) would
        # each start an instance, and the one replaced first would never be stopped
        async with self.lifecycle:
            await self._start_instance_now(service)

    async def _start_instance_now(self, service: dict[str, Any]) -> None:
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
        # a short note for the user from the source (what new albums got), shown as a toast
        instance.announce = lambda text, sid=service["id"]: spawn(decky.emit("source_note", sid, text), "source note")
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
            "downloadable": bool(instance and instance.downloadable),
            "castable": bool(instance and instance.castable),
            # the media center's tabs for it (MusicService.center_tabs)
            "tabs": instance.center_tabs() if instance else [{"id": "home", "title": "Home"}],
            # how Now Playing shows its tracks (MusicService.NOW_PLAYING)
            "now_playing": instance.NOW_PLAYING if instance else "music",
            "status": instance.status() if instance else {},
        }

    @_when_ready
    async def get_state(self) -> dict[str, Any]:
        return {
            "version": version.current(),
            "services": [self._public_service(s) for s in self.settings.services()],
            "active_service": self.settings.get("active_service"),
            "qam_mode": self.settings.get("qam_mode"),
            "volume": self.settings.get("volume"),
            # the EQ for the output in use when each output has its own (eq_per_output), else the one EQ
            "eq": self._current_eq(),
            "eq_per_output": bool(self.settings.get("eq_per_output")),
            "output": self.output,
            "playback": self.settings.get("playback"),
            "debug_log": bool(self.settings.get("debug_log")),
            "debug_log_path": debuglog.path,
            # file pickers start here; distros don't all use /home/deck
            "user_home": decky.DECKY_USER_HOME,
            "server": {"base": self.server.base_url, "key": self.server.key},
            "service_types": SERVICE_TYPES.listing(),
            "planned_types": PLANNED_TYPES,
            "listenbrainz": {"user": self.settings.get("listenbrainz").get("user", ""),
                             "pending": self.listenbrainz.pending_count()},
            "lastfm": {"user": self.settings.get("lastfm").get("user", ""),
                       "has_key": bool(self.settings.get("lastfm").get("key")),
                       "pending": self.lastfm.pending_count()},
            "game_music": self.settings.get("game_music"),
            "offline": self.offline.status(),
            # only today's choices: one an older version saved (since renamed) would make saving fail
            "center": {k: self.settings.get("center").get(k, d) for k, d in DEFAULTS["center"].items()},
        }

    @_when_ready
    async def set_eq(self, eq: dict[str, Any], output_id: Optional[str] = None) -> None:
        """Saves the EQ: for `output_id` (the output it was set on) when each output has its own,
        else the one EQ."""
        if not _valid_setting("eq", eq):
            decky.logger.warning(f"Ignored an invalid EQ: {eq!r}")
            return
        if self.settings.get("eq_per_output") and isinstance(output_id, str) and output_id:
            eqs = dict(self.settings.get("output_eqs"))
            known = eqs.get(output_id, {}).get("name")
            name = self.output["name"] if self.output and self.output["id"] == output_id else known or output_id
            eqs[output_id] = {"name": name, "eq": eq}
            self.settings.set("output_eqs", eqs)
        else:
            self.settings.set("eq", eq)
        await _announce_change()

    def _current_eq(self) -> dict[str, Any]:
        """The EQ that applies now: the output's own, or the one EQ (also where an output starts from)."""
        if self.settings.get("eq_per_output") and self.output:
            own = self.settings.get("output_eqs").get(self.output["id"])
            if own:
                return own["eq"]
        return self.settings.get("eq")

    async def _output_changed(self, output: Optional[dict[str, str]]) -> None:
        decky.logger.info(f"Audio output: {output['name'] if output else 'none'}")
        self.output = output
        await _announce_change()

    @_when_ready
    async def set_setting(self, key: str, value: Any) -> None:
        if key not in FRONTEND_SETTINGS:
            raise ValueError(f"Setting {key} is not writable")
        if not _valid_setting(key, value):
            decky.logger.warning(f"Ignored an invalid value for {key}: {value!r}")
            return
        self.settings.set(key, value)
        if key == "debug_log":
            debuglog.configure(decky.DECKY_PLUGIN_LOG_DIR, bool(value))
        await _announce_change()

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
            config.pop(RESTORED_SIGNED_OUT, None)
            if previous and not self.settings.find_service(service["id"]):
                # removed while it signed in: saving now would bring it back
                raise ServiceError("That source was removed")
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
            await _announce_change()
            return self._public_service(saved)
        return await _result(work())

    @_when_ready
    async def remove_service(self, instance_id: str) -> None:
        async with self.lifecycle:
            instance = self.instances.pop(instance_id, None)
            if instance:
                await instance.stop()
                instance.remove()
        decky.logger.info(f"Removed source {instance_id} ({getattr(instance, 'type', '?')})")
        self.settings.remove_service(instance_id)
        self.playlists.delete_for_instance(instance_id)
        self._sync_offline()
        self.recents.forget(instance_id=instance_id)
        self._forget_game_music(lambda m: m["instance_id"] == instance_id)
        await _announce_change()

    @_when_ready
    async def set_active_service(self, instance_id: str) -> None:
        if self.settings.find_service(instance_id):
            self.settings.set("active_service", instance_id)
            await _announce_change()

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
    async def browse(self, instance_id: str) -> dict[str, Any]:
        """Genres, moods and charts, for sources with feature "browse"."""
        return await _result(self._instance(instance_id).browse())

    @_when_ready
    async def home(self, instance_id: str) -> dict[str, Any]:
        async def work():
            return self._with_ours(instance_id, await self._instance(instance_id).home())
        return await _result(work())

    def _with_ours(self, instance_id: str, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Home with what was played lately and the playlists made in MusicDeck before what the source
        suggests."""
        ours = self.playlists.for_instance(instance_id)
        instance = self.instances.get(instance_id)
        if instance and instance.NOW_PLAYING == "radio":
            # a radio source's are the stations listened to, not the genre or country they were found in
            stations = self.recents.stations_for(instance_id)
            return ([{"title": "Jump back in", "items": [], "tracks": stations, "display": "covers"}] if stations else [])                 + ([{"title": "Your playlists", "items": ours}] if ours else []) + sections
        # what was played shows as it is now (renamed, new artwork), not as it was then: MusicDeck
        # playlists from the store, the rest as their source knows them; what's gone is left out
        current = {c["id"]: c for c in ours}
        recent = []
        for c in self.recents.for_instance(instance_id):
            if PlaylistStore.is_ours(c["id"]):
                now = current.get(c["id"])
            else:
                now = instance.current(c) if instance else c
            if now:
                recent.append(now)
        return (([{"title": "Jump back in", "items": recent}] if recent else [])
                + ([{"title": "Your playlists", "items": ours}] if ours else []) + sections)

    @_when_ready
    async def track_details(self, instance_id: str, track: dict[str, Any]) -> dict[str, Any]:
        """More about the playing track for Now Playing (MusicService.details): notes, facts, related."""
        if not isinstance(track, dict):
            return {"ok": False, "error": "No track"}
        return await _result(self._call(instance_id, lambda s: s.details(track)))

    @_when_ready
    async def center_tab(self, instance_id: str, tab_id: str) -> dict[str, Any]:
        """One of a source's media center tabs (MusicService.tab): sections, each saying how to show it.
        Home gets what was played lately and MusicDeck's playlists, as home() does; a section of the
        user's playlists ("mine") gets MusicDeck's playlists too."""
        async def work():
            sections = await self._instance(instance_id).cached_tab(tab_id)
            if tab_id == "home":
                return self._with_ours(instance_id, sections)
            ours = self.playlists.for_instance(instance_id)
            return [{**s, "items": ours + s["items"]} if s.get("mine") and ours else s for s in sections]
        return await _result(work())

    @_when_ready
    async def open_collection(self, instance_id: str, kind: str, collection_id: str) -> dict[str, Any]:
        if PlaylistStore.is_ours(collection_id):
            async def local():
                try:
                    page = self.playlists.page(collection_id)
                except KeyError:
                    raise ServiceError("Playlist not found")
                return {**page, "offline": self._offline_state(instance_id, collection_id, page["tracks"])}
            return await _result(local())
        return await _result(self._call(instance_id, lambda s: s.open(kind, collection_id)))

    @_when_ready
    async def search(self, instance_id: str, query: str) -> dict[str, Any]:
        if self.settings.find_service(instance_id):
            self.recents.add_search(instance_id, query)
        return await _result(self._call(instance_id, lambda s: s.search(query)))

    @_when_ready
    async def search_history(self, instance_id: str) -> list[str]:
        """A source's recent searches, newest first."""
        return self.recents.searches(instance_id)

    @_when_ready
    async def forget_search(self, instance_id: str, query: str) -> None:
        """Drops one recent search, or all of them ("")."""
        self.recents.forget_search(instance_id, query)

    # --- playback ---

    @_when_ready
    async def log_events(self, entries: list[dict[str, Any]]) -> None:
        """Batched frontend events for the debug log: [{"t": ms, "event": str, "data": {...}}]."""
        for entry in (entries if isinstance(entries, list) else [])[:200]:
            if not isinstance(entry, dict):
                continue
            data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
            # "t" is when it happened; the event's own fields can't clash with debug()'s parameters
            fields = {"t": entry.get("t"), **data}
            fields = {k: v for k, v in fields.items() if k not in ("source", "event")}
            debuglog.debug("player", str(entry.get("event", "?")), **fields)

    @_when_ready
    async def stream_info(self, instance_id: str, track_id: str) -> dict[str, Any]:
        """Resolves a track before playback (and warms the cache when prefetching the next one).
        Tells the player whether to use hls.js."""
        async def work():
            source = await self.server.resolve_stream(instance_id, track_id)
            debuglog.debug("stream", "resolved", instance=instance_id, track=track_id, hls=source.hls,
                           local=bool(source.path), expires=source.expires)
            instance = self.instances.get(instance_id)
            # long tracks (podcast episodes, books) start where they were left; offline, from the start
            start_at = 0
            if instance and "progress" in instance.features:
                try:
                    start_at = await instance.resume_position(track_id)
                except ServiceError as e:
                    debuglog.debug("progress", "no resume point", track=track_id, error=str(e))
            # expires: when the stream's link stops working (signed links), so a track paused past it
            # is fetched afresh on resume
            return {"hls": source.hls, "gain": source.gain, "start_at": start_at, "expires": source.expires}
        return await _result(work())

    @_when_ready
    async def forget_stream(self, instance_id: str, track_id: str) -> None:
        """Drops a cached stream URL (e.g. expired mid-play) so the next attempt resolves afresh."""
        debuglog.debug("stream", "forget", instance=instance_id, track=track_id)
        self.server.forget(instance_id, track_id)

    # --- casting (see musicdeck/cast) ---

    @_when_ready
    async def cast_devices(self) -> dict[str, Any]:
        """Speakers and TVs on the network: [{"id", "name", "model", "kind"}]."""
        return await _result(self.cast.search())

    @_when_ready
    async def cast_start(self, device_id: str) -> dict[str, Any]:
        return await _result(self.cast.connect(str(device_id)))

    @_when_ready
    async def cast_load(self, instance_id: str, track: dict[str, Any], start: float, autoplay: bool) -> dict[str, Any]:
        if not isinstance(track, dict) or not isinstance(start, (int, float)):
            return {"ok": False, "error": "Bad track"}
        return await _result(self.cast.load(str(instance_id), track, start, bool(autoplay)))

    @_when_ready
    async def cast_control(self, action: str, value: Optional[float] = None) -> dict[str, Any]:
        return await _result(self.cast.control(str(action), value))

    @_when_ready
    async def cast_stop(self) -> dict[str, Any]:
        return await _result(self.cast.disconnect())

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
        # ListenBrainz keeps music listening history; podcast episodes (feature "progress") aren't that
        if token and track and instance and "progress" not in instance.features:
            report = self.listenbrainz.listen if submission else self.listenbrainz.now_playing
            spawn(report(token, track, instance.display_name), "ListenBrainz report")
        lastfm = self.settings.get("lastfm")
        if lastfm.get("session_key") and track and instance and "progress" not in instance.features:
            spawn(self._report_lastfm(lastfm, track, submission), "Last.fm report")
        if not self.settings.get("playback").get("scrobble", True) or not instance \
                or "scrobble" not in instance.features:
            return {"ok": True, "data": None}
        return await _result(self._call(instance_id, lambda s: s.scrobble(track_id, submission)))

    async def _report_lastfm(self, account: dict[str, str], track: dict[str, Any], submission: bool) -> None:
        if not submission:
            return await self.lastfm.now_playing(account, track)
        if not await self.lastfm.scrobble(account, track):
            # the session was revoked on Last.fm: unlinked, so it shows; the plays wait for a new link
            decky.logger.info("Last.fm no longer accepts the session; unlinked")
            self.settings.set("lastfm", {**self.settings.get("lastfm"), "user": "", "session_key": ""})
            await _announce_change()

    @_when_ready
    async def lastfm_start(self, key: str, secret: str) -> dict[str, Any]:
        """Starts linking Last.fm with the user's API account (blank: the one used before). Returns the
        page where the user approves MusicDeck; lastfm_poll says when they have."""
        async def work():
            saved = self.settings.get("lastfm")
            key_ = (key or "").strip() or saved.get("key", "")
            secret_ = (secret or "").strip() or (saved.get("secret", "") if key_ == saved.get("key") else "")
            if not key_ or not secret_:
                raise ServiceError("Enter the API key and the shared secret")
            try:
                token = await self.lastfm.get_token(key_, secret_)
            except LastfmError as e:
                raise ServiceError({10: "Last.fm doesn't know that API key",
                                    13: "That shared secret isn't the API key's"}.get(e.code, str(e))) from e
            except aiohttp.ClientError as e:
                raise ServiceError(f"Could not reach Last.fm: {e}") from e
            self.lastfm_link = {"key": key_, "secret": secret_, "token": token}
            return {"url": self.lastfm.auth_url(key_, token)}
        return await _result(work())

    @_when_ready
    async def lastfm_poll(self) -> dict[str, Any]:
        """waiting (not approved yet), done (linked: the user name) or error."""
        link = self.lastfm_link
        if not link:
            return {"status": "error", "error": "Linking expired; start again"}
        try:
            session = await self.lastfm.get_session(link["key"], link["secret"], link["token"])
        except LastfmError as e:
            self.lastfm_link = None
            return {"status": "error", "error": "Linking timed out; start again" if e.code == 15 else str(e)}
        except aiohttp.ClientError:
            return {"status": "waiting"}  # a dropped request; the next poll tries again
        if not session:
            return {"status": "waiting"}
        self.lastfm_link = None
        self.settings.set("lastfm", {"key": link["key"], "secret": link["secret"], **session})
        await _announce_change()
        return {"status": "done", "user": session["user"]}

    @_when_ready
    async def lastfm_cancel(self) -> None:
        self.lastfm_link = None

    @_when_ready
    async def lastfm_unlink(self) -> None:
        """Stops scrobbling to Last.fm. The API account is kept, so linking again is one approval."""
        self.settings.set("lastfm", {**self.settings.get("lastfm"), "user": "", "session_key": ""})
        await _announce_change()

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
            # episodes and books are talk, not songs: only their source's own (a book's chapters)
            if not found and self.http and not (instance and "progress" in instance.features):
                found = await lyrics.lookup(self.http, track)
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

    @_when_ready
    async def create_service_playlist(self, instance_id: str, name: str, track_ids: list[str]) -> dict[str, Any]:
        """A new playlist on the source itself (feature "playlists"), with these tracks in it."""
        name = (name or "").strip()[:100] if isinstance(name, str) else ""
        if not name:
            return {"ok": False, "error": "A playlist needs a name"}
        if not isinstance(track_ids, list) or not all(isinstance(t, str) for t in track_ids):
            return {"ok": False, "error": "No tracks"}
        return await _result(self._call(instance_id, lambda s: s.create_playlist(name, track_ids)))

    # --- managing a library (local files: features "tags" and "artwork") ---

    async def _managed(self, instance_id: str, feature: str, work) -> dict[str, Any]:
        async def run():
            instance = self._instance(instance_id)
            if feature not in instance.features:
                raise ServiceError("Not supported by this source")
            return await work(instance)
        return await _result(run())

    @_when_ready
    async def get_tags(self, instance_id: str, kind: str, item_id: str) -> dict[str, Any]:
        """The tag editor's fields for a track or an album (`kind`)."""
        return await self._managed(instance_id, "tags", lambda s: s.tag_fields(kind, item_id))

    @_when_ready
    async def set_tags(self, instance_id: str, kind: str, item_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        result = await self._managed(instance_id, "tags", lambda s: s.edit_tags(kind, item_id, changes))
        if result["ok"]:
            await _announce_change()
        return result

    @_when_ready
    async def undo_tags(self, instance_id: str, kind: str, item_id: str) -> dict[str, Any]:
        return await self._managed(instance_id, "tags", lambda s: s.undo_tags(kind, item_id))

    @_when_ready
    async def lookup_album(self, instance_id: str, album_id: str) -> dict[str, Any]:
        """MusicBrainz releases that may be this album."""
        return await self._managed(instance_id, "tags", lambda s: s.lookup(album_id))

    @_when_ready
    async def lookup_changes(self, instance_id: str, album_id: str, release_id: str) -> dict[str, Any]:
        return await self._managed(instance_id, "tags", lambda s: s.lookup_changes(album_id, release_id))

    @_when_ready
    async def apply_lookup(self, instance_id: str, album_id: str, release_id: str) -> dict[str, Any]:
        return await self._managed(instance_id, "tags", lambda s: s.apply_lookup(album_id, release_id))

    @_when_ready
    async def album_art(self, instance_id: str, album_id: str, action: str, image_path: str = "") -> dict[str, Any]:
        """An album's own picture: "set" (a file the user picked), "find" (the Cover Art Archive), "clear"."""
        async def act(s: MusicService) -> Any:
            if action == "set":
                return await s.set_album_art(album_id, image_path)
            if action == "find":
                return await s.find_album_art(album_id)
            if action == "clear":
                return await s.clear_album_art(album_id)
            raise ServiceError("Unknown artwork action")
        return await self._managed(instance_id, "artwork", act)

    @_when_ready
    async def artist_art(self, instance_id: str, artist_id: str, action: str, image_path: str = "") -> dict[str, Any]:
        """An artist's picture: "set" (a file the user picked), "find" (Deezer's catalogue), "clear"."""
        async def act(s: MusicService) -> Any:
            if action == "set":
                return await s.set_artist_art(artist_id, image_path)
            if action == "find":
                return await s.find_artist_art(artist_id)
            if action == "clear":
                return await s.clear_artist_art(artist_id)
            raise ServiceError("Unknown artwork action")
        return await self._managed(instance_id, "artwork", act)

    @_when_ready
    async def save_lyrics(self, instance_id: str, track_id: str) -> dict[str, Any]:
        """A track's lyrics from the lyrics sites, kept beside it as an .lrc file."""
        return await self._managed(instance_id, "tags", lambda s: s.save_lyrics(track_id))

    @_when_ready
    async def find_lyrics(self, instance_id: str, album_id: str) -> dict[str, Any]:
        """Lyrics for every track of an album without them: {"found", "total"}."""
        return await self._managed(instance_id, "tags", lambda s: s.find_lyrics(album_id))

    @_when_ready
    async def find_missing_lyrics(self, instance_id: str) -> dict[str, Any]:
        """Lyrics for every track without, in the background (progress in status "lyrics")."""
        async def start(s: MusicService) -> None:
            s.find_missing_lyrics()
        return await self._managed(instance_id, "tags", start)

    @_when_ready
    async def dismiss_review(self, instance_id: str, album_id: str) -> dict[str, Any]:
        """Takes a new album off the list to review (the source's "Ask me" for MusicBrainz tags)."""
        return await self._managed(instance_id, "tags", lambda s: s.dismiss_review(album_id))

    @_when_ready
    async def find_missing_art(self, instance_id: str) -> dict[str, Any]:
        """Looks for every named album without a picture in the background (progress in status "art")."""
        async def start(s: MusicService) -> None:
            s.find_missing_art()
        return await self._managed(instance_id, "artwork", start)

    # --- session (queue) persistence ---

    def _session_path(self) -> str:
        return os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "session.json")

    @_when_ready
    async def save_session(self, session: dict[str, Any]) -> None:
        """Stores the queue locally (every source) and on the server when it supports queue sync."""
        session = {**_clean_session(session), "saved_at": time.time()}
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
                local = _clean_session(json.load(f))
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
        if not local or not local["queue"] or any(item["instanceId"] != self.settings.get("active_service")
                                                  for item in local["queue"]):
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
            # checked after the (slow) fetch, so a double tap finds the first copy already made
            source = {"kind": kind, "id": collection_id}
            copy = self.playlists.find_copy(instance_id, source)
            if copy:
                raise ServiceError(f"Already in your playlists as “{copy['name']}”")
            return self.playlists.create(instance_id, page["title"], page["tracks"], source=source)
        return await _result(work())

    @_when_ready
    async def playlist_add(self, playlist_id: str, tracks: list[dict[str, Any]]) -> int:
        added = self.playlists.add(playlist_id, tracks)
        self._sync_offline()
        return added

    @_when_ready
    async def playlist_remove(self, playlist_id: str, track_id: str) -> None:
        self.playlists.remove(playlist_id, track_id)
        self._sync_offline()

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
        self._sync_offline()
        self.recents.forget(collection_id=playlist_id)
        if self._forget_game_music(lambda m: m["collection"]["id"] == playlist_id):
            await _announce_change()

    # --- downloads (offline.py) ---

    def _sync_offline(self) -> None:
        """Brings the downloaded files in line with the playlists marked for download."""
        wanted = {}
        for p in self.playlists.offline():
            instance = self.instances.get(p["instance_id"])
            if instance and instance.downloadable:
                wanted.update({(p["instance_id"], t["id"]): t for t in p["tracks"]})
        self.offline.sync(wanted, self._resolve_for_offline)

    async def _resolve_for_offline(self, instance_id: str, kind: str, item_id: str) -> StreamSource:
        instance = self._instance(instance_id)
        return await (instance.stream(item_id) if kind == "stream" else instance.art(item_id))

    def _offline_state(self, instance_id: str, playlist_id: str, tracks: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """A MusicDeck playlist's download state for its page, or None where its source can't download."""
        instance = self.instances.get(instance_id)
        if not instance or not instance.downloadable:
            return None
        return {"on": self.playlists.is_offline(playlist_id), "total": len(tracks),
                "saved": sum(1 for t in tracks if self.offline.has(instance_id, t["id"]))}

    @_when_ready
    async def set_playlist_offline(self, playlist_id: str, on: bool) -> dict[str, Any]:
        """Downloads a MusicDeck playlist for offline listening, or removes the download."""
        async def work():
            if not self.playlists.set_offline(playlist_id, bool(on)):
                raise ServiceError("That playlist was deleted")
            self._sync_offline()
            await _announce_change()
        return await _result(work())

    @_when_ready
    async def playlist_offline(self, instance_id: str, playlist_id: str) -> Optional[dict[str, Any]]:
        """How far a playlist's download got (the page asks again as downloads finish)."""
        p = self.playlists.find(playlist_id)
        return self._offline_state(instance_id, playlist_id, p["tracks"]) if p else None

    @_when_ready
    async def retry_downloads(self) -> None:
        self.offline.retry(self._resolve_for_offline)
        await _announce_change()

    @_when_ready
    async def remove_downloads(self) -> None:
        """Every playlist stops being downloaded, and the files are deleted."""
        for p in self.playlists.offline():
            self.playlists.set_offline("md:" + p["id"], False)
        self._sync_offline()
        await _announce_change()

    # --- backups (backup.py) ---

    @_when_ready
    async def export_backup(self) -> dict[str, Any]:
        """Writes a backup (no sign-ins in it) to Documents, or home; returns where."""
        async def work():
            data = backup.build(self.settings.data, self.playlists.playlists, self.playlists.art_dir,
                                decky.DECKY_PLUGIN_RUNTIME_DIR, SECRET_KEYS)
            for source, stored in zip(data["sources"], self.settings.services()):
                source["signed_in"] = any(k in SECRET_KEYS for k in stored["config"])
            documents = os.path.join(decky.DECKY_USER_HOME, "Documents")
            folder = documents if os.path.isdir(documents) else decky.DECKY_USER_HOME
            path = os.path.join(folder, f"MusicDeck backup {datetime.now():%Y-%m-%d %H%M}.json")
            try:
                save_json(path, data)
                os.chmod(path, 0o600)  # server addresses and listening history are nobody else's business
            except OSError as e:
                raise ServiceError(f"Couldn't write the backup: {e.strerror}")
            return path
        return await _result(work())

    @_when_ready
    async def import_backup(self, path: str) -> dict[str, Any]:
        """Adds what a backup has and this Deck doesn't; returns a sentence about what came back."""
        async def work():
            try:
                data = backup.read(path)
            except backup.BackupError as e:
                raise ServiceError(str(e))
            fresh = not self.settings.services()
            have = {s["id"] for s in self.settings.services()}
            added, signed_out = [], []
            for s in data.get("sources", []):
                if not backup.valid_source(s, SERVICE_TYPES) or s["id"] in have:
                    continue
                config = {k: v for k, v in s["config"].items() if k not in SECRET_KEYS and k != RESTORED_SIGNED_OUT}
                if s.get("signed_in") is True:
                    config[RESTORED_SIGNED_OUT] = True
                    signed_out.append(s["name"])
                saved = self.settings.upsert_service({"id": s["id"], "type": s["type"], "name": s["name"][:100],
                                                     "config": config})
                await self._start_instance(saved)
                added.append(s["id"])
            sources = {s["id"] for s in self.settings.services()}
            playlists = 0
            for p in data.get("playlists", []):
                if backup.valid_playlist(p, sources) and self.playlists.restore(
                        p, backup.art_bytes(data.get("playlist_art", {}).get(p["id"]))):
                    playlists += 1
            for name in backup.file_names(added):
                target = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, name)
                if name in data.get("files", {}) and not os.path.exists(target):
                    save_json(target, data["files"][name])
            settings = data.get("settings", {})
            if fresh:
                for key in backup.PREFERENCES:
                    if key in settings and _valid_setting(key, settings[key]):
                        self.settings.set(key, settings[key])
                if settings.get("active_service") in sources:
                    self.settings.set("active_service", settings["active_service"])
            games = {(m["instance_id"], m["appid"]) for m in self.settings.get("game_music")}
            restored_games = [m for m in settings.get("game_music") or []
                              if isinstance(m, dict) and m.get("instance_id") in sources and isinstance(m.get("appid"), int)
                              and isinstance(m.get("collection"), dict) and (m["instance_id"], m["appid"]) not in games]
            if restored_games:
                self.settings.set("game_music", self.settings.get("game_music") + restored_games)
            self._sync_offline()  # downloaded playlists download again
            await _announce_change()
            summary = f"{backup.count(len(added), 'source')} and {backup.count(playlists, 'playlist')} restored"
            if signed_out:
                summary += f"; sign in again to {', '.join(signed_out)}"
            return summary
        return await _result(work())

    @_when_ready
    async def collection_action(self, instance_id: str, kind: str, collection_id: str, call: str) -> dict[str, Any]:
        """A page's own button (Page.action with "call"), e.g. subscribing to a podcast; returns what
        to tell the user."""
        async def work():
            instance = self._instance(instance_id)
            if not hasattr(instance, "collection_action"):
                raise ServiceError("This source has no such action")
            return await instance.collection_action(kind, collection_id, call)
        return await _result(work())

    @_when_ready
    async def save_progress(self, instance_id: str, track: dict[str, Any], position: float, duration: float) -> None:
        """How far a long track (a podcast episode, a book) got, so it resumes there (feature "progress")."""
        instance = self.instances.get(instance_id)
        numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (position, duration))
        if instance is not None and "progress" in instance.features and numbers and isinstance(track, dict):
            try:
                await instance.save_progress(track, position, duration)
            except ServiceError as e:  # the next report (a few seconds on) tries again
                debuglog.debug("progress", "not saved", instance=instance_id, error=str(e))

    @_when_ready
    async def set_played(self, instance_id: str, track: dict[str, Any], played: bool) -> dict[str, Any]:
        async def work():
            instance = self._instance(instance_id)
            if "progress" not in instance.features or not isinstance(track, dict):
                raise ServiceError("This source doesn't keep track of what's been played")
            await instance.set_played(track, bool(played))
        return await _result(work())

    @_when_ready
    async def played_station(self, instance_id: str, track: dict[str, Any]) -> None:
        """A live station was listened to: it leads a radio source's "Jump back in" row."""
        instance = self.instances.get(instance_id)
        if instance and instance.NOW_PLAYING == "radio":
            self.recents.add_station(instance_id, track)

    @_when_ready
    async def played_collection(self, instance_id: str, collection: dict[str, Any]) -> None:
        """An album or playlist was played: it leads the source's "Jump back in" row."""
        if self.settings.find_service(instance_id):
            self.recents.add(instance_id, collection)

    # --- music that starts with a game (src/games.ts plays it) ---

    @_when_ready
    async def set_game_music(self, instance_id: str, appid: int, game: str,
                             collection: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Sets the album or playlist that plays when a game starts, for one source; None removes it."""
        async def work():
            if not self.settings.find_service(instance_id):
                raise ServiceError("That source is no longer configured")
            if not isinstance(appid, int) or isinstance(appid, bool) or appid <= 0:
                raise ServiceError("That isn't a game")
            entries = [m for m in self.settings.get("game_music")
                       if not (m["instance_id"] == instance_id and m["appid"] == appid)]
            if collection is not None:
                if not isinstance(collection, dict) or collection.get("kind") not in GAME_MUSIC_KINDS \
                        or not collection.get("id"):
                    raise ServiceError("Only an album or a playlist can play with a game")
                keep = ("id", "kind", "title", "subtitle", "art")
                entries.append({"instance_id": instance_id, "appid": appid, "game": str(game or appid),
                                "collection": {k: collection.get(k) for k in keep}})
            self.settings.set("game_music", entries)
            await _announce_change()
            return entries
        return await _result(work())

    def _forget_game_music(self, gone) -> bool:
        """Drops game music whose source or playlist went away; True if any did."""
        entries = self.settings.get("game_music")
        kept = [m for m in entries if not gone(m)]
        if len(kept) != len(entries):
            self.settings.set("game_music", kept)
        return len(kept) != len(entries)


def _clean_session(session: Any) -> dict[str, Any]:
    """A saved queue with anything malformed dropped: {"queue": [{"instanceId", "track"}], "index",
    "position", "saved_at"}. The file outlives versions and the frontend, so it's never trusted."""
    if not isinstance(session, dict):
        session = {}
    queue = [item for item in session.get("queue") or [] if isinstance(item, dict)
             and isinstance(item.get("instanceId"), str) and isinstance(item.get("track"), dict)
             and item["track"].get("id")] if isinstance(session.get("queue"), list) else []

    def number(key: str) -> float:
        value = session.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    index = int(number("index"))
    return {**session, "queue": queue, "index": min(max(index, 0), max(len(queue) - 1, 0)),
            "position": max(number("position"), 0), "saved_at": number("saved_at")}


def _valid_setting(key: str, value: Any) -> bool:
    """A frontend setting keeps the shape of its default (settings.DEFAULTS), so a bad value can't be
    saved and then break the next start."""
    default = DEFAULTS[key]
    if key == "volume":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
    if key == "qam_mode":
        return value in ("replace", "separate")
    if key == "center" and isinstance(value, dict):
        if any(k in CENTER_CHOICES and (not isinstance(value[k], (str, int)) or value[k] not in CENTER_CHOICES[k])
               for k in value):
            return False
    if isinstance(default, dict):
        # only known fields, each of its default's kind (EQ gains: as many numbers as there are bands)
        return isinstance(value, dict) and all(k in default and _same_kind(v, default[k]) for k, v in value.items())
    return isinstance(value, type(default))


def _same_kind(value: Any, default: Any) -> bool:
    number = (int, float)
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, number):
        return isinstance(value, number) and not isinstance(value, bool)
    if isinstance(default, list):
        return (isinstance(value, list) and len(value) == len(default)
                and all(_same_kind(v, d) for v, d in zip(value, default)))
    return isinstance(value, type(default))


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
