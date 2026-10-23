"""
Steam soundtracks: the soundtrack apps the user owns on Steam, played from the files Steam installs.

Where the pieces come from:

* Installed soundtracks: Steam's own records on disk. steamapps/libraryfolders.vdf lists every
  library (internal storage, SD cards, other drives); each library's appmanifest_<appid>.acf says
  what is installed and in which folder. Soundtrack apps install to steamapps/music/<installdir>.
  Older soundtracks are DLC of their game instead: their files land inside the game's folder, and
  the game's manifest lists the DLC under InstalledDepots. For those, folders inside the game
  named like "Soundtrack", "OST" or "Music" are scanned (never the whole game, whose sound
  effects aren't music).
* Owned soundtracks, their game and cover art: only Steam's UI knows what the account owns, so
  the frontend sends it (src/steam.ts -> Plugin.steam_library -> set_library) along with the
  game that is running. The backend keeps the last copy on disk, so the home view works before
  the frontend has synced.

Soundtracks often ship the same album several times (MP3, FLAC and WAV folders); one copy is kept
(see _pick_formats). Each soundtrack is one album, whatever its files' album tags say. Not
installed soundtracks open as a page with an Install action, which starts Steam's own install
dialog (steam://install/<appid>); the next visit after the download finds it.

Tags, artwork, lyrics and the incremental index reuse the local-files code (local.py).
"""
import asyncio
import json
import os
import random
import re
import time
from typing import Any, Optional

from .. import models
from .base import MusicService, ServiceError, StreamSource
from .local import (AUDIO_EXTENSIONS, COVER_EXTENSIONS, COVER_NAMES, _hash, extract_embedded_art, read_lyrics,
                    read_tags)

INDEX_VERSION = 1
# Steam installs (and removes) apps under these, so their modification times tell when to rescan
WATCHED = ("steamapps", os.path.join("steamapps", "music"))
# Folders inside a game that hold its soundtrack DLC's files
SOUNDTRACK_DIR = re.compile(r"soundtrack|\bost\b|music", re.I)
SOUNDTRACK_DIR_DEPTH = 3
# One copy of an album shipped in several formats: the first of these that is present in full
FORMAT_PREFERENCE = ["flac", "m4a", "ogg", "mp3", "wav"]
FORMAT_FAMILY = {".flac": "flac", ".m4a": "m4a", ".mp4": "m4a", ".aac": "m4a", ".ogg": "ogg", ".oga": "ogg",
                 ".opus": "ogg", ".webm": "ogg", ".mp3": "mp3", ".wav": "wav"}
# Cover URLs come from the frontend; only Steam's own image hosts are fetched
ART_HOSTS = re.compile(r"^https://([a-z0-9-]+\.)*(steamstatic\.com|steampowered\.com|steamcontent\.com)/")
# StateFlags bit Steam sets once an app is fully installed
FULLY_INSTALLED = 4


# --- Steam's files ---

VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*')


def parse_vdf(text: str) -> dict[str, Any]:
    """Steam's text KeyValues format (.vdf, .acf): quoted keys and values, nested with braces.
    Keys are lower-cased, since Steam isn't consistent about case."""
    root: dict[str, Any] = {}
    stack = [root]
    key: Optional[str] = None
    for match in VDF_TOKEN.finditer(text):
        string, brace = match.group(1), match.group(2)
        if brace == "{":
            child: dict[str, Any] = {}
            stack[-1][(key or "").lower()] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif string is not None:
            value = string.replace('\\"', '"').replace("\\\\", "\\")
            if key is None:
                key = value
            else:
                stack[-1][key.lower()] = value
                key = None
    return root


def _read_vdf(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return parse_vdf(f.read())
    except OSError:
        return {}


def steam_roots(home: str) -> list[str]:
    """Steam's data folder: the native install, its ~/.steam link, and the Flatpak."""
    found = []
    for candidate in (os.path.join(home, ".local", "share", "Steam"), os.path.join(home, ".steam", "steam"),
                      os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam")):
        real = os.path.realpath(candidate)
        if os.path.isdir(os.path.join(real, "steamapps")) and real not in found:
            found.append(real)
    return found


def library_folders(root: str) -> list[str]:
    """Every Steam library (the main one, SD cards, other drives) that exists right now."""
    libraries = [root]
    data = _read_vdf(os.path.join(root, "steamapps", "libraryfolders.vdf")).get("libraryfolders", {})
    for entry in data.values():
        path = entry.get("path") if isinstance(entry, dict) else None
        if path and os.path.isdir(os.path.join(path, "steamapps")):
            real = os.path.realpath(path)
            if real not in libraries:
                libraries.append(real)
    return libraries


def _manifests(library: str) -> list[dict[str, Any]]:
    steamapps = os.path.join(library, "steamapps")
    try:
        names = [n for n in os.listdir(steamapps) if n.startswith("appmanifest_") and n.endswith(".acf")]
    except OSError:
        return []
    manifests = []
    for name in names:
        state = _read_vdf(os.path.join(steamapps, name)).get("appstate")
        if isinstance(state, dict) and state.get("appid"):
            manifests.append({**state, "library": library})
    return manifests


def _installed(manifest: dict[str, Any]) -> bool:
    try:
        return bool(int(manifest.get("stateflags", "0")) & FULLY_INSTALLED)
    except ValueError:
        return False


def _soundtrack_dirs(game_dir: str) -> list[str]:
    """Folders inside a game whose name says they hold its soundtrack (outermost matches only)."""
    found = []
    base_depth = game_dir.rstrip(os.sep).count(os.sep)
    for directory, dirnames, _ in os.walk(game_dir):
        if directory.count(os.sep) - base_depth >= SOUNDTRACK_DIR_DEPTH:
            dirnames[:] = []
            continue
        for d in list(dirnames):
            if SOUNDTRACK_DIR.search(d):
                found.append(os.path.join(directory, d))
                dirnames.remove(d)  # its contents are scanned as part of it
    return found


def find_soundtracks(home: str, owned_dlc: set[int]) -> dict[str, dict[str, Any]]:
    """Installed soundtracks by app id: {"appid", "name", "dirs", "installed", "updated"}.

    `installed` is False while Steam is still downloading. `owned_dlc` are soundtrack app ids known
    (from the frontend) to be DLC; they're looked for inside their game's folder.
    """
    found: dict[str, dict[str, Any]] = {}
    games: list[dict[str, Any]] = []
    for root in steam_roots(home):
        for library in library_folders(root):
            for manifest in _manifests(library):
                appid = str(manifest["appid"])
                music = os.path.join(library, "steamapps", "music", manifest.get("installdir", ""))
                if manifest.get("installdir") and os.path.isdir(music):
                    found[appid] = {"appid": appid, "name": (manifest.get("name") or appid).strip(),
                                    "dirs": [music], "installed": _installed(manifest),
                                    "updated": float(manifest.get("lastupdated") or 0)}
                else:
                    games.append(manifest)
    for game in games:
        depots = game.get("installeddepots") or {}
        dlc = {str(d.get("dlcappid")) for d in depots.values() if isinstance(d, dict) and d.get("dlcappid")}
        dlc = {appid for appid in dlc if int(appid) in owned_dlc and appid not in found}
        if not dlc:
            continue
        game_dir = os.path.join(game["library"], "steamapps", "common", game.get("installdir", ""))
        dirs = _soundtrack_dirs(game_dir) if os.path.isdir(game_dir) else []
        for appid in dlc:
            # several soundtrack DLCs of one game can't be told apart on disk; they share its folders
            found[appid] = {"appid": appid, "name": "", "dirs": dirs, "installed": _installed(game),
                            "updated": float(game.get("lastupdated") or 0)}
    return found


def _pick_formats(paths: list[str]) -> list[str]:
    """Drops the extra copies when an album is shipped in several formats.

    Formats with (nearly) as many files as the largest one are copies of the same album; only the
    preferred one is kept. Formats with fewer files are extras (bonus tracks) and stay.
    """
    families: dict[str, list[str]] = {}
    for p in paths:
        families.setdefault(FORMAT_FAMILY.get(os.path.splitext(p)[1].lower(), "other"), []).append(p)
    largest = max((len(v) for v in families.values()), default=0)
    copies = [f for f, v in families.items() if len(v) >= 0.8 * largest]
    if len(copies) < 2:
        return paths
    rank = {f: i for i, f in enumerate(FORMAT_PREFERENCE)}
    keep = min(copies, key=lambda f: rank.get(f, len(rank)))
    return [p for f, v in families.items() if f == keep or f not in copies for p in v]


def _folder_cover(dirs: list[str]) -> Optional[str]:
    """A cover image in the soundtrack's folder or one level down: the usual names first, then
    the largest image."""
    folders = []
    for top in dirs:
        folders.append(top)
        try:
            folders += [os.path.join(top, d) for d in sorted(os.listdir(top)) if os.path.isdir(os.path.join(top, d))]
        except OSError:
            pass
    images: list[tuple[int, str]] = []
    for directory in folders:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            stem, ext = os.path.splitext(name.lower())
            if ext not in COVER_EXTENSIONS:
                continue
            path = os.path.join(directory, name)
            if stem in COVER_NAMES:
                return path
            try:
                images.append((os.path.getsize(path), path))
            except OSError:
                pass
    return max(images)[1] if images else None


class SteamService(MusicService):
    """Soundtracks owned on Steam. No account: Steam itself is signed in.

    config: {} (nothing to set up)
    """

    type = "steam"
    display_name = "Steam soundtracks"
    features = frozenset({"star", "lyrics", "radio"})

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.home_dir = os.environ.get("DECKY_USER_HOME") or os.path.expanduser("~")
        self.index_path = os.path.join(runtime_dir, f"steam-{instance_id}.json")
        self.owned_path = os.path.join(runtime_dir, f"steam-{instance_id}-owned.json")
        self.favourites_path = os.path.join(runtime_dir, f"steam-{instance_id}-favourites.json")
        # appid -> {"appid", "name", "dirs", "installed", "updated", "tracks": {track id: tag record}}
        self.soundtracks: dict[str, dict[str, Any]] = {}
        # appid -> {"appid", "name", "parent", "parent_name", "cover"} (from the frontend)
        self.owned: dict[str, dict[str, Any]] = {}
        self.running: Optional[str] = None
        self.favourites: set[str] = set()
        self.covers: dict[str, Optional[str]] = {}
        self.artists: dict[str, str] = {}
        self.watched: dict[str, float] = {}
        self.scan_task: Optional[asyncio.Task[None]] = None
        self.scan_progress = {"scanning": False, "found": 0, "last_scan": 0.0, "error": ""}

    async def start(self) -> None:
        await asyncio.to_thread(self._load)
        self.rescan()

    async def stop(self) -> None:
        if self.scan_task:
            self.scan_task.cancel()

    def remove(self) -> None:
        for path in (self.index_path, self.owned_path, self.favourites_path):
            try:
                os.remove(path)
            except OSError:
                pass

    async def test(self) -> str:
        if not steam_roots(self.home_dir):
            raise ServiceError("Steam's library folder wasn't found")
        installed = sum(1 for s in self.soundtracks.values() if s["installed"])
        return f"{models.count(installed, 'soundtrack')} installed, {len(self.owned)} owned"

    def status(self) -> dict[str, Any]:
        return {**self.scan_progress, "installed": sum(1 for s in self.soundtracks.values() if s["installed"]),
                "owned": len(self.owned)}

    # --- what the frontend knows ---

    def set_library(self, owned: list[dict[str, Any]], running: Optional[int]) -> None:
        """Owned soundtracks and the running game, from Steam's UI (see src/steam.ts)."""
        fresh = {str(a["appid"]): {"appid": str(a["appid"]), "name": (a.get("name") or "").strip(),
                                   "parent": str(a["parent"]) if a.get("parent") else None,
                                   "parent_name": (a.get("parent_name") or "").strip(),
                                   "cover": a.get("cover") if ART_HOSTS.match(a.get("cover") or "") else None}
                 for a in owned if a.get("appid")}
        self.running = str(running) if running else None
        if fresh != self.owned:
            new_dlc = set(fresh) - set(self.owned)
            self.owned = fresh
            os.makedirs(self.runtime_dir, exist_ok=True)
            tmp = self.owned_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self.owned.values()), f)
            os.replace(tmp, self.owned_path)
            if new_dlc:
                self.rescan()  # newly known DLC may already be installed inside its game

    # --- scanning ---

    def _watched_times(self) -> dict[str, float]:
        times = {}
        for root in steam_roots(self.home_dir):
            for library in library_folders(root):
                for sub in WATCHED:
                    try:
                        times[os.path.join(library, sub)] = os.stat(os.path.join(library, sub)).st_mtime
                    except OSError:
                        pass
        return times

    def _check_for_changes(self) -> None:
        """Steam installing or removing something changes its folders' times: rescan then."""
        if self.watched != self._watched_times():
            self.rescan()

    def rescan(self) -> None:
        if self.scan_task and not self.scan_task.done():
            return
        self.scan_progress.update(scanning=True, found=0, error="")
        self.scan_task = asyncio.get_event_loop().create_task(self._scan())

    async def _scan(self) -> None:
        try:
            await asyncio.to_thread(self._scan_blocking)
            self.scan_progress["last_scan"] = time.time()
        except Exception as e:
            self.scan_progress["error"] = str(e)
            raise
        finally:
            self.scan_progress["scanning"] = False

    def _scan_blocking(self) -> None:
        watched = self._watched_times()
        previous = {t["path"]: t for s in self.soundtracks.values() for t in s["tracks"].values()}
        soundtracks = find_soundtracks(self.home_dir, {int(a) for a in self.owned})
        count = 0
        for appid, soundtrack in soundtracks.items():
            paths = []
            for top in soundtrack["dirs"]:
                for directory, dirnames, filenames in os.walk(top):
                    dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                    paths += [os.path.join(directory, n) for n in sorted(filenames)
                              if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS]
            tracks = {}
            for path in _pick_formats(paths):
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                old = previous.get(path)
                if old and old["mtime"] == st.st_mtime and old["size"] == st.st_size:
                    record = old
                else:
                    record = {"path": path, "mtime": st.st_mtime, "size": st.st_size, **read_tags(path)}
                tracks[_hash(path)] = record
                count += 1
                self.scan_progress["found"] = count
            soundtrack["tracks"] = tracks
        self.soundtracks = soundtracks
        self.covers = {}
        self.artists = {}
        self.watched = watched
        self._save()

    def _load(self) -> None:
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") == INDEX_VERSION:
                self.soundtracks = data.get("soundtracks", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        try:
            with open(self.owned_path, "r", encoding="utf-8") as f:
                self.owned = {a["appid"]: a for a in json.load(f)}
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        try:
            with open(self.favourites_path, "r", encoding="utf-8") as f:
                self.favourites = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        os.makedirs(self.runtime_dir, exist_ok=True)
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": INDEX_VERSION, "soundtracks": self.soundtracks}, f)
        os.replace(tmp, self.index_path)

    # --- model ---

    def _name(self, appid: str) -> str:
        owned = self.owned.get(appid, {})
        installed = self.soundtracks.get(appid, {})
        return owned.get("name") or installed.get("name") or f"Soundtrack {appid}"

    def _sorted_tracks(self, appid: str) -> list[tuple[str, dict[str, Any]]]:
        tracks = self.soundtracks.get(appid, {}).get("tracks", {})
        return sorted(tracks.items(), key=lambda kv: (kv[1]["disc"], kv[1]["track"],
                                                      os.path.basename(kv[1]["path"]).lower()))

    def _artist(self, appid: str) -> str:
        """The soundtrack's composer, as most of its files name them."""
        if appid not in self.artists:
            names: dict[str, int] = {}
            for t in self.soundtracks.get(appid, {}).get("tracks", {}).values():
                name = t["albumartist"] or t["artist"]
                if name:
                    names[name] = names.get(name, 0) + 1
            self.artists[appid] = max(names, key=names.__getitem__) if names else ""
        return self.artists[appid]

    def _track(self, tid: str, t: dict[str, Any], appid: str) -> dict[str, Any]:
        composer = self._artist(appid)
        return models.track(tid, t["title"], t["artist"] or t["albumartist"], self._name(appid), t["duration"],
                            art=f"c:{appid}", album_id=appid, artist_id=_hash(composer.lower()) if composer else None,
                            starred=tid in self.favourites, gain=t.get("rg"))

    def _playable(self) -> list[str]:
        return [a for a, s in self.soundtracks.items() if s["installed"] and s.get("tracks")]

    def _collection(self, appid: str) -> dict[str, Any]:
        installed = appid in self.soundtracks and self.soundtracks[appid]["installed"]
        if installed:
            n = len(self.soundtracks[appid].get("tracks", {}))
            subtitle = " · ".join(x for x in (self._artist(appid), models.count(n, "track")) if x)
        elif appid in self.soundtracks:
            subtitle = "Installing…"
        else:
            subtitle = "Not installed"
        game = self.owned.get(appid, {}).get("parent_name")
        if game and not installed and not self._name(appid).lower().startswith(game.lower()):
            subtitle += f" · {game}"
        return models.collection(appid, "album", self._name(appid), subtitle, art=f"c:{appid}")

    def _by_name(self, appids: list[str]) -> list[str]:
        return sorted(appids, key=lambda a: self._name(a).lower())

    def _track_index(self) -> dict[str, tuple[str, dict[str, Any]]]:
        return {tid: (appid, t) for appid in self._playable() for tid, t in self.soundtracks[appid]["tracks"].items()}

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        self._check_for_changes()
        installed = self._by_name(self._playable())
        not_installed = self._by_name([a for a in self.owned if a not in installed])
        sections = []
        if self.running:
            playing = [a for a, o in self.owned.items() if o.get("parent") == self.running]
            if playing:
                game = self.owned[playing[0]].get("parent_name") or "the game you're playing"
                sections.append(models.section(f"From {game}", [self._collection(a) for a in self._by_name(playing)]))
        if installed:
            recent = sorted(installed, key=lambda a: self.soundtracks[a].get("updated", 0), reverse=True)
            if len(installed) > 5:
                sections.append(models.section("Recently installed", [self._collection(a) for a in recent[:5]]))
            sections.append(models.section("Installed", [self._collection(a) for a in installed]))
            n = sum(len(self.soundtracks[a]["tracks"]) for a in installed)
            sections.append(models.section("Library", [
                models.collection("favourites", "list", "Favourite tracks", models.count(len(self.favourites), "track")),
                models.collection("artists", "list", "Composers"),
                models.collection("tracks", "list", "All tracks", models.count(n, "track")),
            ]))
        if not_installed:
            sections.append(models.section("Not installed", [self._collection(a) for a in not_installed]))
        return sections

    # --- media center ---

    TABS = (("home", "Home"), ("soundtracks", "Soundtracks"), ("favourites", "Favourites"),
            ("composers", "Composers"))

    async def tab(self, tab_id: str) -> list[dict[str, Any]]:
        if tab_id == "home":
            self._check_for_changes()
            sections = [s for s in await self.home() if not models.is_categories(s)]
            favourites = self._open_list("favourites")["tracks"]
            return sections + ([models.section("Favourite tracks", [], favourites, display="grid")] if favourites else [])
        if tab_id == "soundtracks":
            self._check_for_changes()
            installed = self._by_name(self._playable())
            return [models.section("Soundtracks", [self._collection(a) for a in installed], display="grid")] \
                if installed else [models.notice("No soundtracks installed")]
        if tab_id == "favourites":
            favourites = self._open_list("favourites")["tracks"]
            return [models.section("Favourite tracks", [], favourites, display="grid")] if favourites else \
                [models.notice("Press Y on a track and choose Favourite to keep it here")]
        if tab_id == "composers":
            self._check_for_changes()
            return [models.section("Composers", self._open_list("artists")["collections"], display="grid")]
        return await super().tab(tab_id)

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        self._check_for_changes()
        if kind == "album":
            return self._open_soundtrack(id)
        if kind == "artist":
            appids = [a for a in self._by_name(self._playable()) if _hash(self._artist(a).lower()) == id]
            if not appids:
                raise ServiceError("Composer not found")
            return models.page(self._artist(appids[0]), models.count(len(appids), "soundtrack"),
                               collections=[self._collection(a) for a in appids],
                               tracks=[self._track(tid, t, a) for a in appids for tid, t in self._sorted_tracks(a)])
        if kind == "list":
            return self._open_list(id)
        raise ServiceError(f"Unknown collection kind: {kind}")

    def _open_soundtrack(self, appid: str) -> dict[str, Any]:
        if not appid.isdigit():
            raise ServiceError("Soundtrack not found")
        if appid in self._playable():
            c = self._collection(appid)
            return models.page(c["title"], c["subtitle"], c["art"],
                               tracks=[self._track(tid, t, appid) for tid, t in self._sorted_tracks(appid)])
        if appid not in self.owned and appid not in self.soundtracks:
            raise ServiceError("Soundtrack not found")
        if appid in self.soundtracks and self.soundtracks[appid]["installed"]:
            # installed, but nothing playable was found (a DLC whose folder isn't recognisable)
            return models.page(self._name(appid), "No music files found in its folder", f"c:{appid}")
        installing = appid in self.soundtracks
        return models.page(self._name(appid), "Installing…" if installing else "Not installed", f"c:{appid}",
                           action=None if installing else {"label": "Install", "url": f"steam://install/{appid}"},
                           notice="Playable here when Steam finishes" if installing else None)

    def _open_list(self, id: str) -> dict[str, Any]:
        index = self._track_index()
        if id == "favourites":
            favs = [(tid, *index[tid]) for tid in self.favourites if tid in index]
            favs.sort(key=lambda x: (self._name(x[1]).lower(), x[2]["disc"], x[2]["track"]))
            return models.page("Favourite tracks", models.count(len(favs), "track"),
                               tracks=[self._track(tid, t, a) for tid, a, t in favs])
        if id == "tracks":
            return models.page("All tracks", tracks=[self._track(tid, t, a) for a in self._by_name(self._playable())
                                                     for tid, t in self._sorted_tracks(a)])
        if id == "artists":
            artists: dict[str, tuple[str, int]] = {}
            for a in self._playable():
                name = self._artist(a)
                if name:
                    key = _hash(name.lower())
                    artists[key] = (name, artists.get(key, (name, 0))[1] + 1)
            return models.page("Composers", collections=[
                models.collection(k, "artist", name, models.count(n, "soundtrack"))
                for k, (name, n) in sorted(artists.items(), key=lambda kv: kv[1][0].lower())])
        raise ServiceError(f"Unknown list: {id}")

    async def search(self, query: str) -> dict[str, Any]:
        q = query.strip().lower()
        if not q:
            return models.page("Search")
        appids = set(self._playable()) | set(self.owned)
        matches = [a for a in self._by_name(list(appids))
                   if q in self._name(a).lower() or q in (self.owned.get(a, {}).get("parent_name") or "").lower()
                   or (a in self.soundtracks and q in self._artist(a).lower())]
        tracks = [(tid, a, t) for tid, (a, t) in self._track_index().items()
                  if q in t["title"].lower() or q in (t["artist"] or "").lower()]
        return models.page(f"Results for “{query}”", collections=[self._collection(a) for a in matches[:20]],
                           tracks=[self._track(tid, t, a) for tid, a, t in tracks[:100]])

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        found = self._track_index().get(track_id)
        if not found or not os.path.isfile(found[1]["path"]):
            raise ServiceError("Track not found; was the soundtrack uninstalled?")
        return StreamSource(path=found[1]["path"])

    async def art(self, art_id: str) -> StreamSource:
        """A soundtrack's cover: an image in its folder, else Steam's album art, else one embedded in a track."""
        kind, _, appid = art_id.partition(":")
        if kind != "c":
            raise ServiceError("Art not found")
        if appid not in self.covers:
            dirs = self.soundtracks.get(appid, {}).get("dirs", [])
            self.covers[appid] = await asyncio.to_thread(_folder_cover, dirs) if dirs else None
        if self.covers[appid]:
            return StreamSource(path=self.covers[appid])
        cover = self.owned.get(appid, {}).get("cover")
        if cover:
            return StreamSource(url=cover)
        tracks = self._sorted_tracks(appid)
        embedded = await asyncio.to_thread(extract_embedded_art, tracks[0][1]["path"]) if tracks else None
        if not embedded:
            raise ServiceError("No art")
        cached = os.path.join(self.runtime_dir, "art-cache", f"steam-{appid}")
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with open(cached, "wb") as f:
            f.write(embedded[0])
        self.covers[appid] = cached
        return StreamSource(path=cached, content_type=embedded[1])

    # --- features ---

    async def star(self, track_id: str, starred: bool) -> None:
        if starred:
            self.favourites.add(track_id)
        else:
            self.favourites.discard(track_id)
        os.makedirs(self.runtime_dir, exist_ok=True)
        tmp = self.favourites_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(self.favourites), f)
        os.replace(tmp, self.favourites_path)

    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        found = self._track_index().get(track["id"])
        return await asyncio.to_thread(read_lyrics, found[1]["path"]) if found else None

    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        """More from the same soundtrack, then from the others, shuffled."""
        index = self._track_index()
        if track["id"] not in index:
            raise ServiceError("Track not found")
        appid = index[track["id"]][0]
        same = [tid for tid, (a, _) in index.items() if a == appid and tid != track["id"]]
        rest = [tid for tid, (a, _) in index.items() if a != appid]
        random.shuffle(same)
        random.shuffle(rest)
        return [self._track(tid, index[tid][1], index[tid][0]) for tid in (same[:15] + rest)[:50]]
