"""
Last.fm scrobbling, for every source, alongside ListenBrainz (listenbrainz.py).

Last.fm wants each app to have its own API account, so the user brings one (an API key and its shared
secret, from last.fm/api/account/create), as with Spotify's client ID. Linking is Last.fm's desktop
sign-in: MusicDeck gets a token, the user approves it on Last.fm's own page in Steam's browser, and the
token becomes a session key that doesn't expire. The password never passes through MusicDeck, and the
secret and session key stay in the backend's settings, never sent to the frontend.

Plays that can't be sent (offline, Last.fm down) wait in a small file and go with the next one.
"""
import hashlib
import json
import os
import time
from typing import Any, Optional

import aiohttp

from . import debuglog

API = "https://ws.audioscrobbler.com/2.0/"
AUTH_PAGE = "https://www.last.fm/api/auth/"
MAX_PENDING = 1000
BATCH = 50  # track.scrobble takes up to 50 at a time

# error codes (last.fm/api/errorcodes)
INVALID_SESSION = 9
NOT_AUTHORIZED_YET = 14
TOKEN_EXPIRED = 15
INVALID_KEY = 10
BAD_SIGNATURE = 13
TEMPORARY = {11, 16, 29}  # offline, temporarily unavailable, rate limited


class LastfmError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def sign(params: dict[str, str], secret: str) -> str:
    """Last.fm's api_sig: md5 of every parameter (but format) as name+value, in name order, then the secret."""
    text = "".join(k + params[k] for k in sorted(params) if k not in ("format", "callback")) + secret
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def scrobble_params(plays: list[dict[str, Any]]) -> dict[str, str]:
    """track.scrobble's numbered fields for a batch of plays ({"track", "timestamp"})."""
    params: dict[str, str] = {}
    for i, play in enumerate(plays):
        track = play["track"]
        params[f"artist[{i}]"] = track.get("artist") or ""
        params[f"track[{i}]"] = track.get("title") or ""
        params[f"timestamp[{i}]"] = str(int(play["timestamp"]))
        if track.get("album"):
            params[f"album[{i}]"] = track["album"]
        if track.get("duration"):
            params[f"duration[{i}]"] = str(int(track["duration"]))
    return params


class Lastfm:
    def __init__(self, http: aiohttp.ClientSession, pending_path: str):
        self.http = http
        self.pending_path = pending_path

    async def call(self, method: str, params: dict[str, str], key: str, secret: str, post: bool = False) -> dict[str, Any]:
        params = {**params, "method": method, "api_key": key}
        params["api_sig"] = sign(params, secret)
        params["format"] = "json"
        request = self.http.post(API, data=params) if post else self.http.get(API, params=params)
        async with request as resp:
            data = await resp.json(content_type=None)
        if isinstance(data, dict) and "error" in data:
            raise LastfmError(int(data["error"]), str(data.get("message") or "Last.fm error"))
        if resp.status >= 400:
            raise LastfmError(0, f"HTTP {resp.status}")
        return data

    # --- linking ---

    async def get_token(self, key: str, secret: str) -> str:
        return str((await self.call("auth.getToken", {}, key, secret))["token"])

    @staticmethod
    def auth_url(key: str, token: str) -> str:
        return f"{AUTH_PAGE}?api_key={key}&token={token}"

    async def get_session(self, key: str, secret: str, token: str) -> Optional[dict[str, str]]:
        """{"user", "session_key"} once the user has approved the token on Last.fm, else None."""
        try:
            session = (await self.call("auth.getSession", {"token": token}, key, secret))["session"]
        except LastfmError as e:
            if e.code == NOT_AUTHORIZED_YET:
                return None
            raise
        return {"user": str(session["name"]), "session_key": str(session["key"])}

    # --- plays ---

    async def now_playing(self, account: dict[str, str], track: dict[str, Any]) -> None:
        if not _scrobblable(track):
            return
        params = {"artist": track.get("artist") or "", "track": track.get("title") or ""}
        if track.get("album"):
            params["album"] = track["album"]
        if track.get("duration"):
            params["duration"] = str(int(track["duration"]))
        try:
            await self.call("track.updateNowPlaying", {**params, "sk": account["session_key"]},
                            account["key"], account["secret"], post=True)
        except (aiohttp.ClientError, LastfmError, ValueError) as e:
            debuglog.debug("lastfm", "now playing not sent", error=str(e) or type(e).__name__)

    async def scrobble(self, account: dict[str, str], track: dict[str, Any], timestamp: Optional[int] = None) -> bool:
        """Records a play, sending any held back from earlier with it. False if Last.fm no longer
        accepts the session (revoked): the account should be unlinked; the plays are kept."""
        if not _scrobblable(track):
            return True
        pending = self._load() + [{"track": _slim(track), "timestamp": timestamp or int(time.time())}]
        sent = 0
        valid = True
        try:
            while sent < len(pending):
                batch = pending[sent:sent + BATCH]
                await self.call("track.scrobble", {**scrobble_params(batch), "sk": account["session_key"]},
                                account["key"], account["secret"], post=True)
                sent += len(batch)
        except LastfmError as e:
            valid = e.code not in (INVALID_SESSION, INVALID_KEY, BAD_SIGNATURE)
            debuglog.debug("lastfm", "plays kept for later", code=e.code, error=str(e), pending=len(pending) - sent)
        except (aiohttp.ClientError, ValueError) as e:
            debuglog.debug("lastfm", "offline; plays kept for later", error=str(e), pending=len(pending) - sent)
        self._save(pending[sent:])
        return valid

    # --- unsent plays ---

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


def _slim(track: dict[str, Any]) -> dict[str, Any]:
    return {k: track.get(k) for k in ("artist", "title", "album", "duration")}


def _scrobblable(track: dict[str, Any]) -> bool:
    # a station isn't a play; Last.fm needs an artist and a title (and ignores tracks under 30 s)
    return not track.get("live") and bool(track.get("title")) and bool(track.get("artist")) \
        and not (track.get("duration") and track["duration"] < 30)
