"""
Soulseek: finding music in what other people share, and getting it onto the Deck (slsk.py speaks the
protocol). It isn't a streaming service: playing a track downloads it first (into a cache), and an album
is downloaded into a folder of the user's (by default ~/Music/Soulseek), where the local files source,
watching ~/Music, picks it up and gets it ready like any new album.

Soulseek needs a name and password; a name nobody has is registered by signing in with it. Its protocol
has no tokens, so the password is kept (in the settings file, which only the user can read, never in
backups). Nothing is shared unless the user picks folders to share (`share_dirs`): then other users can
search, browse and download from them (two at a time), and the router is asked to forward the port.
"""
import asyncio
import base64
import json
import os
import re
import time
from typing import Any, Optional

from .. import models
from ..background import spawn
import aiohttp

from ..igd import PortMapping
from ..slsk import Client, Shares, SharedFile, SoulseekError
from .base import MusicService, ServiceError, StreamSource

AUDIO = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav"}
LOSSLESS = {".flac", ".wav"}
CACHE_LIMIT = 1024 * 1024 * 1024  # what played tracks may take on the Deck, oldest let go first
PLAY_WAIT = 90.0  # seconds a track that's played may take to start arriving before the user is told
MAX_FILE = 2 * 1024 * 1024 * 1024
SEARCH_WAIT = 6.0
RESCAN_SECONDS = 3600  # the shared folders indexed again this often
TRIES = 3  # a file that fails is asked for again this many times in all, a while apart


def _id(*parts: str) -> str:
    return base64.urlsafe_b64encode(json.dumps(parts).encode("utf-8")).decode("ascii").rstrip("=")


def _parts(id: str) -> list[str]:
    try:
        parts = json.loads(base64.urlsafe_b64decode(id + "=" * (-len(id) % 4)))
    except (ValueError, json.JSONDecodeError):
        raise ServiceError("Not a Soulseek item")
    if not isinstance(parts, list) or not all(isinstance(p, (str, int)) for p in parts):
        raise ServiceError("Not a Soulseek item")
    return parts


def _safe(name: str) -> str:
    """A name from someone else's share made safe as one file or folder name on the Deck."""
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", name).strip().strip(".")
    return name[:150] or "untitled"


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _quality(f: SharedFile) -> str:
    ext = _ext(f.path)[1:].upper()
    if _ext(f.path) in LOSSLESS:
        return " ".join(x for x in (ext, f"{f.bit_depth}/{f.sample_rate / 1000:g}" if f.bit_depth and f.sample_rate else "") if x)
    return " ".join(x for x in (ext, f"{f.bitrate} kbps" if f.bitrate else "") if x)


class SoulseekService(MusicService):
    """config: {"username", "password", "download_dir", "share_dirs": [folders]}"""

    type = "soulseek"
    display_name = "Soulseek"
    # no lyrics of its own: the lyrics sites are asked, as for most sources
    features: frozenset[str] = frozenset()

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.client: Optional[Client] = None
        self.error = ""
        self.cache_dir = os.path.join(runtime_dir, f"soulseek-{instance_id}")
        self.index_path = os.path.join(runtime_dir, f"soulseek-{instance_id}.json")
        # what was found lately, so a folder can be opened and a track played: {id: SharedFile}
        self.files: dict[str, SharedFile] = {}
        self.folders: dict[str, list[SharedFile]] = {}
        # albums downloaded: [{"title", "user", "folder", "dir", "at", "files": [names]}]
        self.downloaded: list[dict[str, Any]] = []
        self.album_jobs: dict[str, dict[str, Any]] = {}
        self.announce: Optional[Any] = None
        self.connecting: Optional[asyncio.Task] = None
        # what's shared: the folders' index, kept between runs so a rescan only reads what changed
        self.shares = Shares(self.config.get("share_dirs") or [])
        self.shares_path = os.path.join(runtime_dir, f"soulseek-{instance_id}-shares.json")
        self.rescanning: Optional[asyncio.Task] = None
        self.http: Optional[aiohttp.ClientSession] = None
        self.mapping: Optional[PortMapping] = None
        self.mapped = False

    @property
    def download_dir(self) -> str:
        return os.path.abspath(os.path.expanduser(self.config.get("download_dir") or "~/Music/Soulseek"))

    @classmethod
    def prepare_config(cls, config: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
        config = dict(config)
        config["username"] = str(config.get("username") or "").strip()[:30]
        if not config.get("password") and previous:
            config["password"] = previous.get("password", "")
        if not config["username"] or not config.get("password"):
            raise ServiceError("Enter a Soulseek name and password (a new name is registered when you sign in)")
        dirs = config.get("share_dirs")
        config["share_dirs"] = [d for d in dirs if isinstance(d, str) and d][:20] if isinstance(dirs, list) else []
        return config

    @classmethod
    async def authenticate(cls, config: dict[str, Any], previous: Optional[dict[str, Any]],
                           runtime_dir: str) -> dict[str, Any]:
        """Signs in once, so a wrong password is said now rather than found later."""
        client = Client(config["username"], config["password"], port=0)
        try:
            await client.connect()
        except SoulseekError as e:
            raise ServiceError(str(e))
        finally:
            await client.close()
        return config

    @classmethod
    def account(cls, config: dict[str, Any]) -> Optional[str]:
        return config.get("username", "").lower() or None

    async def start(self) -> None:
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.downloaded = data.get("downloaded", []) if isinstance(data, dict) else []
        except (FileNotFoundError, json.JSONDecodeError):
            self.downloaded = []
        self.connecting = spawn(self._connect(), "connecting to Soulseek")
        if self.shares.folders:
            self.rescanning = spawn(self._keep_shares(), "indexing Soulseek shares")

    async def _keep_shares(self) -> None:
        """Indexes the shared folders now and every RESCAN_SECONDS, telling the server the counts."""
        while True:
            try:
                with open(self.shares_path, "r", encoding="utf-8") as f:
                    known = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                known = {}
            index = await asyncio.to_thread(self.shares.scan, known if isinstance(known, dict) else {})
            tmp = self.shares_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(index, f)
            os.replace(tmp, self.shares_path)
            if self.client and self.client.connected:
                self.client.shares_changed()
            await asyncio.sleep(RESCAN_SECONDS)

    async def _connect(self) -> None:
        delay = 5.0
        while True:
            self.client = Client(self.config["username"], self.config["password"], shares=self.shares)
            try:
                await self.client.connect()
                self.error = ""
                spawn(self._map_port(), "asking the router for Soulseek's port")
                return
            except SoulseekError as e:
                self.error = str(e)
                if "password" in self.error or "name" in self.error:
                    return  # trying again won't help
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)

    async def _client(self) -> Client:
        if self.client and self.client.connected:
            return self.client
        if self.connecting is None or self.connecting.done():
            self.connecting = spawn(self._connect(), "connecting to Soulseek")
        try:
            await asyncio.wait_for(asyncio.shield(self.connecting), 15)
        except asyncio.TimeoutError:
            pass
        if not (self.client and self.client.connected):
            raise ServiceError(self.error or "Not connected to Soulseek yet")
        return self.client

    async def _map_port(self) -> None:
        """Asks the router to forward our port, so users behind their own routers can reach us too."""
        if not self.client:
            return
        if self.http is None:
            self.http = aiohttp.ClientSession()
        self.mapping = PortMapping(self.http, self.client.port, "MusicDeck Soulseek")
        self.mapped = await self.mapping.open()

    async def stop(self) -> None:
        for task in (self.connecting, self.rescanning):
            if task:
                task.cancel()
        if self.mapping:
            await self.mapping.close()
        if self.http:
            await self.http.close()
        if self.client:
            await self.client.close()

    def remove(self) -> None:
        import shutil
        shutil.rmtree(self.cache_dir, ignore_errors=True)
        for path in (self.index_path, self.shares_path):
            try:
                os.remove(path)
            except OSError:
                pass

    def status(self) -> dict[str, Any]:
        active = len(self.client.downloads) if self.client else 0
        dirs, files = self.shares.counts()
        return {"connected": bool(self.client and self.client.connected), "error": self.error,
                "downloading": active + sum(1 for j in self.album_jobs.values() if not j.get("done")),
                "user": self.config.get("username", ""), "shared": files,
                "uploading": len(self.client.uploading) if self.client else 0, "reachable": self.mapped}

    async def test(self) -> str:
        await self._client()
        return f"Signed in as {self.config['username']}"

    def _save(self) -> None:
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"downloaded": self.downloaded[-200:]}, f)
        os.replace(tmp, self.index_path)

    # --- mapping ---

    def _track(self, f: SharedFile) -> dict[str, Any]:
        tid = _id(f.user, f.path, str(f.size))
        self.files[tid] = f
        folder = f.folder.rpartition("\\")[2]
        stem = os.path.splitext(f.name)[0]
        artist = folder.split(" - ", 1)[0] if " - " in folder else ""
        return models.track(tid, stem, artist or f.user, folder, float(f.duration or 0),
                            album_id=_id(f.user, f.folder))

    def _folder(self, files: list[SharedFile]) -> dict[str, Any]:
        f = files[0]
        fid = _id(f.user, f.folder)
        self.folders[fid] = files
        name = f.folder.rpartition("\\")[2] or f.folder
        speed = f"{f.speed / 1_000_000:.1f} MB/s" if f.speed else ""
        about = " · ".join(x for x in (f.user, models.count(len(files), "track"), _quality(f),
                                       "free slot" if f.free_slot else f"queue {f.queue}", speed) if x)
        return models.collection(fid, "album", name, about)

    # --- browsing ---

    async def home(self) -> list[dict[str, Any]]:
        sections = []
        busy = [j for j in self.album_jobs.values() if not j.get("done")]
        if busy:
            sections.append(models.section("Downloading", [
                models.collection(j["id"], "album", j["title"], f"{self._progress(j)} · {j['user']}") for j in busy]))
        done = [d for d in reversed(self.downloaded) if os.path.isdir(d.get("dir", ""))]
        if done:
            sections.append(models.section("Downloaded", [
                models.collection(_id("downloaded", d["dir"]), "folder", d["title"], d["user"]) for d in done[:40]]))
        if not sections:
            sections.append(models.notice("Search for an artist or album: Soulseek finds it in what other people "
                                          "share. Download an album and it's added to your local files."))
        return sections

    async def search(self, query: str) -> dict[str, Any]:
        client = await self._client()
        try:
            found = await client.search(query.strip(), SEARCH_WAIT)
        except SoulseekError as e:
            raise ServiceError(str(e))
        audio = [f for f in found if _ext(f.path) in AUDIO and 0 < f.size <= MAX_FILE]
        # the best places to get it first: a free slot, then speed; lossless, then bitrate
        rank = lambda f: (not f.free_slot, f.queue, -f.speed, _ext(f.path) not in LOSSLESS, -(f.bitrate or 0))  # noqa: E731
        folders: dict[tuple[str, str], list[SharedFile]] = {}
        for f in sorted(audio, key=rank):
            folders.setdefault((f.user, f.folder), []).append(f)
        albums = [sorted(v, key=lambda f: f.name.lower()) for v in folders.values() if len(v) > 1]
        return models.page(f"Results for “{query}”", models.count(len(audio), "file"),
                           collections=[self._folder(v) for v in albums[:40]],
                           tracks=[self._track(f) for f in sorted(audio, key=rank)[:100]],
                           notice=None if audio else "Nobody sharing it answered. Try fewer words, or later.")

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "album":
            files = self.folders.get(id)
            job = self.album_jobs.get(id)
            if not files:
                raise ServiceError("Search again to open this: Soulseek results don't keep")
            files = await self._whole_folder(files)
            c = self._folder(files)
            busy = job and not job.get("done")
            user = files[0].user
            return models.page(c["title"], c["subtitle"], tracks=[self._track(f) for f in files],
                               collections=[models.collection(_id("user", user), "folder", f"Everything {user} shares",
                                                              "Their whole library")],
                               tracks_first=True,
                               action={"label": "Cancel download", "call": "cancel"} if busy else
                               {"label": "Download album", "call": "download"},
                               notice=f"Downloading: {self._progress(job)}" if busy else None)
        if kind == "folder" and _parts(id)[0] == "user" and len(_parts(id)) == 2:
            user = str(_parts(id)[1])
            client = await self._client()
            try:
                library = await client.browse(user)
            except SoulseekError as e:
                raise ServiceError(str(e))
            albums = []
            for folder, files in sorted(library.items(), key=lambda kv: kv[0].lower()):
                audio = [f for f in files if _ext(f.path) in AUDIO and 0 < f.size <= MAX_FILE]
                if audio:
                    albums.append(self._folder(sorted(audio, key=lambda f: f.name.lower())))
            return models.page(f"{user}'s library", models.count(len(albums), "folder"), collections=albums,
                               notice=None if albums else f"{user} shares no music")
        if kind == "folder":
            parts = _parts(id)
            folder = next((d for d in self.downloaded if d["dir"] == parts[1]), None) if len(parts) == 2 else None
            if not folder or not os.path.isdir(folder["dir"]):
                raise ServiceError("That download isn't there any more")
            names = sorted(n for n in os.listdir(folder["dir"]) if _ext(n) in AUDIO)
            return models.page(folder["title"], f"Downloaded from {folder['user']} · in your local files", tracks=[
                models.track(_id("file", os.path.join(folder["dir"], n)), os.path.splitext(n)[0], folder["user"],
                             folder["title"]) for n in names])
        raise ServiceError("Unknown collection kind")

    async def _whole_folder(self, files: list[SharedFile]) -> list[SharedFile]:
        """All the music in a result's folder (a search only lists the files that matched), with the
        search's speed and queue; what was found if the user doesn't answer."""
        f = files[0]
        try:
            client = await self._client()
            whole = await client.folder(f.user, f.folder)
        except (ServiceError, SoulseekError):
            return files
        audio = sorted((x for x in whole if _ext(x.path) in AUDIO and 0 < x.size <= MAX_FILE), key=lambda x: x.name.lower())
        for x in audio:
            x.free_slot, x.speed, x.queue = f.free_slot, f.speed, f.queue
        if audio:
            self.folders[_id(f.user, f.folder)] = audio
        return audio or files

    def _progress(self, job: dict[str, Any]) -> str:
        """"4 of 12 · 38%": the files done, and the bytes of the whole album so far."""
        got = job["got"]
        sizes = job.get("sizes") or {}
        received = sum(d.received for d in (self.client.downloads.values() if self.client else [])
                       if (d.user, d.path) in sizes)
        done = sum(sizes[k] for k in job.get("finished", ()) if k in sizes)
        total = sum(sizes.values()) or 1
        return f"{got} of {job['total']} · {min(99, int(100 * (done + received) / total))}%"

    async def collection_action(self, kind: str, collection_id: str, call: str) -> str:
        if call == "cancel" and kind == "album":
            job = self.album_jobs.get(collection_id)
            if not job or job.get("done"):
                return "Not downloading"
            job["cancelled"] = True
            if self.client:
                for user, path in job.get("sizes", {}):
                    self.client.cancel(user, path)
            return f"Stopped downloading {job['title']}"
        if call != "download" or kind != "album":
            raise ServiceError("No such action")
        files = self.folders.get(collection_id)
        if not files:
            raise ServiceError("Search again to download this: Soulseek results don't keep")
        files = await self._whole_folder(files)
        job = self.album_jobs.get(collection_id)
        if job and not job.get("done"):
            return "Already downloading"
        f = files[0]
        title = f.folder.rpartition("\\")[2] or "Download"
        target = os.path.join(self.download_dir, _safe(title))
        job = self.album_jobs[collection_id] = {"id": collection_id, "title": title, "user": f.user, "dir": target,
                                                "got": 0, "total": len(files), "finished": set(),
                                                "sizes": {(x.user, x.path): x.size for x in files}}
        spawn(self._download_album(job, files), "Soulseek album download")
        return f"Downloading {title} into {self.download_dir}"

    async def _download_album(self, job: dict[str, Any], files: list[SharedFile]) -> None:
        async def one(f: SharedFile) -> bool:
            for attempt in range(TRIES):
                if job.get("cancelled"):
                    return False
                try:
                    client = await self._client()
                    await client.download(f.user, f.path, f.size, os.path.join(job["dir"], _safe(f.name)))
                    job["got"] += 1
                    job["finished"].add((f.user, f.path))
                    return True
                except (ServiceError, SoulseekError) as e:
                    if "Cancelled" in str(e) or "not shared" in str(e).lower():
                        return False
                    # carries on from what it got, a while later
                    await asyncio.sleep(10 * (attempt + 1))
            return False

        # all asked for at once: the user's client queues them and sends them one after another
        results = await asyncio.gather(*[one(f) for f in files])
        failed = sum(1 for r in results if not r)
        job["done"] = True
        if job["got"]:
            self.downloaded.append({"title": job["title"], "user": job["user"], "dir": job["dir"], "at": time.time()})
            self._save()
        if self.announce and not job.get("cancelled"):
            self.announce(f"{job['title']}: " + (f"downloaded, {job['got']} files" if not failed else
                                                 f"{job['got']} of {len(files)} files downloaded"))

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        parts = _parts(track_id)
        if parts[0] == "file" and len(parts) == 2:
            path = os.path.abspath(str(parts[1]))
            if not any(path.startswith(d["dir"] + os.sep) for d in self.downloaded) or not os.path.isfile(path):
                raise ServiceError("That file isn't there any more")
            return StreamSource(path=path)
        f = self.files.get(track_id)
        if not f:
            if len(parts) != 3:
                raise ServiceError("Not a Soulseek track")
            f = SharedFile(str(parts[0]), str(parts[1]), int(parts[2]))
        if _ext(f.path) not in AUDIO or not 0 < f.size <= MAX_FILE:
            raise ServiceError("Not a music file")
        dest = os.path.join(self.cache_dir, track_id[:120] + _ext(f.path))
        if not os.path.isfile(dest):
            client = await self._client()
            self._trim_cache()
            try:
                await client.download(f.user, f.path, f.size, dest, wait=PLAY_WAIT)
            except SoulseekError as e:
                raise ServiceError(f"{e}. It keeps downloading; play it again in a while")
        return StreamSource(path=dest)

    def _trim_cache(self) -> None:
        try:
            files = sorted((os.path.join(self.cache_dir, n) for n in os.listdir(self.cache_dir)), key=os.path.getmtime)
        except OSError:
            return
        total = sum(os.path.getsize(p) for p in files)
        while files and total > CACHE_LIMIT:
            p = files.pop(0)
            total -= os.path.getsize(p)
            os.remove(p)
