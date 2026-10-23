"""
ListenBrainz scrobbling (listenbrainz.org, the open listening-history service by MetaBrainz).

Works for every source: the frontend reports plays with the track's details, and listens go to
ListenBrainz in the API's own format. A listen that can't be sent (offline, server trouble) is kept
in a small file and sent with the next one, so plays made away from Wi-Fi aren't lost.
The user's token is stored in MusicDeck's settings and never sent back to the frontend.
"""
import json
import os
import time
from typing import Any, Optional

import aiohttp

from . import debuglog
from .version import VERSION

API = "https://api.listenbrainz.org/1"
CLIENT = "MusicDeck"
MAX_PENDING = 1000  # oldest listens are dropped beyond this many unsent
BATCH = 100  # ListenBrainz accepts up to 1000 per import; smaller batches fail less painfully


class TokenRejected(Exception):
    """ListenBrainz says the token is invalid (revoked or mistyped)."""


def track_metadata(track: dict[str, Any], source: str) -> dict[str, Any]:
    info: dict[str, Any] = {"media_player": CLIENT, "submission_client": CLIENT,
                            "submission_client_version": VERSION, "music_service_name": source}
    if track.get("duration"):
        info["duration_ms"] = int(track["duration"] * 1000)
    meta = {"artist_name": track.get("artist") or "", "track_name": track.get("title") or "",
            "additional_info": info}
    if track.get("album"):
        meta["release_name"] = track["album"]
    return meta


class ListenBrainz:
    def __init__(self, http: aiohttp.ClientSession, pending_path: str):
        self.http = http
        self.pending_path = pending_path

    async def _post(self, token: str, payload: dict[str, Any]) -> None:
        async with self.http.post(f"{API}/submit-listens", json=payload,
                                  headers={"Authorization": f"Token {token}"}) as resp:
            if resp.status == 401:
                raise TokenRejected()
            if resp.status >= 400:
                raise aiohttp.ClientResponseError(resp.request_info, (), status=resp.status,
                                                  message=(await resp.text())[:200])

    async def validate(self, token: str) -> Optional[str]:
        """The account's user name, or None if ListenBrainz doesn't recognise the token."""
        async with self.http.get(f"{API}/validate-token", headers={"Authorization": f"Token {token}"}) as resp:
            data = await resp.json(content_type=None)
        return data.get("user_name") if data.get("valid") else None

    async def now_playing(self, token: str, track: dict[str, Any], source: str) -> None:
        if not _listenable(track):
            return
        try:
            await self._post(token, {"listen_type": "playing_now",
                                     "payload": [{"track_metadata": track_metadata(track, source)}]})
        except (aiohttp.ClientError, TokenRejected) as e:
            debuglog.debug("listenbrainz", "now playing not sent", error=str(e) or type(e).__name__)

    async def listen(self, token: str, track: dict[str, Any], source: str, listened_at: Optional[int] = None) -> None:
        """Records a finished listen, sending any held back from earlier along with it."""
        if not _listenable(track):
            return
        pending = self._load() + [{"listened_at": listened_at or int(time.time()),
                                   "track_metadata": track_metadata(track, source)}]
        sent = 0
        try:
            while sent < len(pending):
                batch = pending[sent:sent + BATCH]
                await self._post(token, {"listen_type": "single" if len(batch) == 1 else "import", "payload": batch})
                sent += len(batch)
        except TokenRejected:
            debuglog.debug("listenbrainz", "token rejected; listens kept for later", pending=len(pending) - sent)
        except aiohttp.ClientError as e:
            debuglog.debug("listenbrainz", "offline; listens kept for later", pending=len(pending) - sent, error=str(e))
        self._save(pending[sent:])

    # --- unsent listens ---

    def _load(self) -> list[dict[str, Any]]:
        try:
            with open(self.pending_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (FileNotFoundError, ValueError):
            return []

    def _save(self, pending: list[dict[str, Any]]) -> None:
        pending = pending[-MAX_PENDING:]
        if not pending:
            if os.path.exists(self.pending_path):
                os.remove(self.pending_path)
            return
        tmp = self.pending_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pending, f)
        os.replace(tmp, self.pending_path)

    def pending_count(self) -> int:
        return len(self._load())


def _listenable(track: dict[str, Any]) -> bool:
    # a station isn't a listen; a track needs at least a title and an artist to be useful
    return not track.get("live") and bool(track.get("title")) and bool(track.get("artist"))
