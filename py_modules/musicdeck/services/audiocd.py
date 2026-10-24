"""
An audio CD in a drive plugged into the device, played as a source while it's in (found by
musicdeck/devices.py, read by musicdeck/cd.py), and ripped to Local files on request.

* Names: MusicBrainz knows most CDs by their disc ID; the release's names for this disc (and its cover,
  from the Cover Art Archive) are kept on the Deck (cd-<disc id>.json, .jpg), so a disc played before is
  named at once, offline too. A disc MusicBrainz doesn't know plays as "Track 1", "Track 2"...
* Playing: each track a WAV read from the drive as it plays (cd.TrackWav), seeking included.
* Ripping: into the first Local files source's first folder, <artist>/<album>/<nn> <title>.flac, encoded by
  ffmpeg (Bazzite has it) from the raw audio piped to it, then tagged with mutagen (names, numbers, year,
  the release's MusicBrainz id, the cover). Local files sees the new album arrive and gets it ready like
  any other. Without ffmpeg, ripping says so.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable, Optional

import aiohttp

from .. import cd, models, native
from ..background import spawn
from ..musicbrainz import MusicBrainz
from ..net import tls
from .base import MusicService, ServiceError, StreamSource

ALBUM = "disc"
COVER = "cover"
_safe = models.safe_name


class AudioCdService(MusicService):
    """config: {"drive": "/dev/sr0", "toc": [[number, lba, audio], ...], "disc_id"}. `rip_to` (set by the
    plugin) gives the folder to rip into: Local files' first, or "" when there's no Local files source."""

    type = "cd"
    display_name = "Audio CD"
    features: frozenset[str] = frozenset()

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        toc = [tuple(t) for t in config.get("toc") or []]
        self.toc: list[tuple[int, int, bool]] = [(int(n), int(lba), bool(a)) for n, lba, a in toc]
        self.tracks = cd.tracks(self.toc)
        self.disc_id: str = config.get("disc_id") or cd.disc_id(self.toc)
        self.reader = cd.cd_reader(config.get("drive", ""))
        self.meta_path = os.path.join(runtime_dir, f"cd-{self.disc_id}.json")
        self.cover_path = os.path.join(runtime_dir, f"cd-{self.disc_id}.jpg")
        self.meta: dict[str, Any] = {}
        self.http: Optional[aiohttp.ClientSession] = None
        self.lookup: Optional[asyncio.Task[None]] = None
        self.rip_task: Optional[asyncio.Task[None]] = None
        self.rip_progress = {"running": False, "done": 0, "total": 0, "error": "", "folder": ""}
        self.rip_to: Callable[[], str] = lambda: ""
        self.announce: Optional[Any] = None

    async def start(self) -> None:
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            self.meta = meta if isinstance(meta, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            self.http = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls()),
                                              timeout=aiohttp.ClientTimeout(total=20))
            self.lookup = spawn(self._look_up(), "naming the CD")

    async def stop(self) -> None:
        for task in (self.lookup, self.rip_task):
            if task:
                task.cancel()
        if self.http:
            await self.http.close()

    async def _look_up(self) -> None:
        """The disc's names (and cover) from MusicBrainz, kept for next time."""
        assert self.http
        mb = MusicBrainz(self.http)
        try:
            found = await mb.by_disc(self.disc_id)
            if not found:
                self.meta = {"unknown": True}
            else:
                release = await mb.release(found[0])
                disc = found[1]
                titles = [t for t in release["tracks"] if t["disc"] == disc]
                self.meta = {"release": release["id"], "title": release["title"], "artist": release["artist"],
                             "year": release["year"], "genres": release["genres"], "disc": disc,
                             "discs": release.get("discs", 1),
                             "tracks": [{"title": t["title"], "artist": t["artist"]} for t in titles]}
                cover = await mb.cover(release["id"], release.get("group", ""))
                if cover:
                    with open(self.cover_path, "wb") as f:
                        f.write(cover[0])
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(self.meta, f)
        except ServiceError as e:
            # offline, or MusicBrainz busy: the disc plays unnamed, and is asked about next time
            self.meta = {"error": str(e)}
        if self.announce and self.meta.get("title"):
            self.announce(f"{self.meta['title']} by {self.meta['artist']}")

    # --- what's on it ---

    def _title(self) -> str:
        return self.meta.get("title") or "Audio CD"

    def _artist(self) -> str:
        return self.meta.get("artist") or ""

    def _art(self) -> Optional[str]:
        return COVER if os.path.isfile(self.cover_path) else None

    def _track(self, i: int) -> dict[str, Any]:
        t = self.tracks[i]
        named = (self.meta.get("tracks") or [])
        name = named[i] if i < len(named) else {}
        return models.track(f"t{t['number']}", name.get("title") or f"Track {t['number']}",
                            name.get("artist") or self._artist(), self._title(),
                            t["frames"] / cd.FRAMES_PER_SECOND, art=self._art(), album_id=ALBUM)

    def _album(self) -> dict[str, Any]:
        sub = " · ".join(x for x in [self._artist(), self.meta.get("year") or ""] if x) or models.count(
            len(self.tracks), "track")
        return models.collection(ALBUM, "album", self._title(), sub, self._art())

    def name(self) -> str:
        return " · ".join(x for x in [self.meta.get("title"), self._artist()] if x) or "Audio CD"

    def status(self) -> dict[str, Any]:
        return {"tracks": len(self.tracks), "title": self.meta.get("title") or "", "rip": dict(self.rip_progress)}

    async def home(self) -> list[dict[str, Any]]:
        return [models.section("In the drive", [self._album()],
                               [self._track(i) for i in range(len(self.tracks))])]

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind != "album" or id != ALBUM:
            raise ServiceError("Not on this CD")
        album = self._album()
        action = None
        subtitle = album["subtitle"]
        if self.rip_progress["running"]:
            subtitle += f" · ripping {self.rip_progress['done'] + 1} of {self.rip_progress['total']}"
        elif self.rip_to():
            action = {"label": "Rip again" if self.rip_progress["done"] and not self.rip_progress["error"]
                      else "Rip to Local files", "call": "rip"}
        return models.page(album["title"], subtitle, album["art"],
                           tracks=[self._track(i) for i in range(len(self.tracks))], action=action)

    async def search(self, query: str) -> dict[str, Any]:
        words = query.lower().split()
        found = [self._track(i) for i in range(len(self.tracks))
                 if all(w in (self._track(i)["title"] + " " + self._track(i)["artist"]).lower() for w in words)]
        return models.page(f"Results for “{query}”", tracks=found)

    def _index(self, track_id: str) -> int:
        number = int(track_id[1:]) if re.fullmatch(r"t\d{1,2}", track_id or "") else -1
        for i, t in enumerate(self.tracks):
            if t["number"] == number:
                return i
        raise ServiceError("Not on this CD")

    async def stream(self, track_id: str) -> StreamSource:
        t = self.tracks[self._index(track_id)]
        if not self.reader:
            raise ServiceError("CDs can't be read on this system")
        return StreamSource(reader=cd.TrackWav(t["start"], t["frames"], self.reader.read), content_type="audio/wav")

    async def art(self, art_id: str) -> StreamSource:
        if art_id != COVER or not os.path.isfile(self.cover_path):
            raise ServiceError("Art not found")
        return StreamSource(path=self.cover_path)

    # --- ripping ---

    async def collection_action(self, kind: str, collection_id: str, call: str) -> str:
        if call != "rip" or kind != "album" or collection_id != ALBUM:
            raise ServiceError("Not something a CD does")
        target = self.rip_to()
        if not target or not os.path.isdir(target):
            raise ServiceError("Add a Local files source first: the CD is ripped into its folder")
        if not shutil.which("ffmpeg"):
            raise ServiceError("Ripping needs ffmpeg, which this system doesn't have")
        if not self.reader:
            raise ServiceError("CDs can't be read on this system")
        if self.rip_progress["running"]:
            return "Already ripping this CD"
        folder = os.path.join(target, _safe(self._artist() or "Unknown artist", "Unknown artist"),
                              _safe(self._title(), f"Audio CD {self.disc_id[:8]}"))
        self.rip_progress = {"running": True, "done": 0, "total": len(self.tracks), "error": "", "folder": folder}
        self.rip_task = spawn(self._rip(folder), "ripping a CD")
        return f"Ripping to {folder}"

    async def _rip(self, folder: str) -> None:
        try:
            os.makedirs(folder, exist_ok=True)
            cover = None
            if os.path.isfile(self.cover_path):
                with open(self.cover_path, "rb") as f:
                    cover = f.read()
            for i in range(len(self.tracks)):
                track = self._track(i)
                name = f"{self.tracks[i]['number']:02d} {_safe(track['title'], 'Track')}.flac"
                await asyncio.to_thread(self._rip_track, i, os.path.join(folder, name), track, cover)
                self.rip_progress["done"] = i + 1
            self.rip_progress["running"] = False
            if self.announce:
                self.announce(f"Ripped {self._title()} into Local files")
        except Exception as e:  # noqa: BLE001 - a scratched disc or a full card: said, not raised
            self.rip_progress.update(running=False, error=str(e) or type(e).__name__)
            if self.announce:
                self.announce(f"Ripping stopped: {self.rip_progress['error']}")

    def _rip_track(self, i: int, path: str, track: dict[str, Any], cover: Optional[bytes]) -> None:
        """One track: its frames read from the drive into ffmpeg's FLAC encoder, then tagged."""
        assert self.reader
        t = self.tracks[i]
        temp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.part.flac")
        proc = subprocess.Popen([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                                 "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0",
                                 "-c:a", "flac", "-compression_level", "5", temp],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                **native.NO_WINDOW)
        try:
            assert proc.stdin
            done = 0
            while done < t["frames"]:
                n = min(cd.CHUNK * 4, t["frames"] - done)
                proc.stdin.write(self.reader.read(t["start"] + done, n))
                done += n
            proc.stdin.close()
            if proc.wait() != 0:
                raise ServiceError(f"ffmpeg couldn't encode track {t['number']}")
            self._tag(temp, track, i, cover)
            os.replace(temp, path)
        finally:
            if proc.poll() is None:
                proc.kill()
            if os.path.exists(temp):
                os.remove(temp)

    def _tag(self, path: str, track: dict[str, Any], i: int, cover: Optional[bytes]) -> None:
        from mutagen.flac import FLAC, Picture
        audio = FLAC(path)
        tags = {"title": track["title"], "artist": track["artist"] or self._artist(), "album": self._title(),
                "albumartist": self._artist(), "tracknumber": f"{self.tracks[i]['number']}/{len(self.tracks)}",
                "discnumber": f"{self.meta.get('disc', 1)}/{self.meta.get('discs', 1)}",
                "date": self.meta.get("year") or "", "musicbrainz_albumid": self.meta.get("release") or ""}
        genres = self.meta.get("genres") or []
        for key, value in tags.items():
            if value:
                audio[key] = value
        if genres:
            audio["genre"] = genres
        if cover:
            picture = Picture()
            picture.type, picture.mime, picture.data = 3, "image/jpeg", cover
            audio.add_picture(picture)
        audio.save()
