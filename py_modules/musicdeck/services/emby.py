"""
Emby, through the same REST API as Jellyfin (Jellyfin began as a fork of Emby), so this is the
Jellyfin source with the differences found against a real Emby 4.10 server:

- single items, favourites and "played" live under /Users/<id>/... (the newer paths Jellyfin uses
  answer 404);
- a playlist query filtered to audio returns every audio item, so it isn't filtered;
- there's no Quick Connect: sign-in is the username and password (exchanged for a token on save);
- the lyrics endpoint errors, so lyrics come from LRCLIB like for most sources.
"""
from typing import Any, Optional

from .base import SignIn
from .jellyfin import JellyfinService


class EmbyService(JellyfinService):
    type = "emby"
    display_name = "Emby"
    features = frozenset({"star", "scrobble", "radio", "playlists"})
    PLAYLIST_FILTER: dict[str, str] = {}
    SIGN_IN_HINT = "Enter your username and password"

    @classmethod
    def sign_in(cls, options: dict[str, Any], runtime_dir: str) -> Optional[SignIn]:
        return None

    async def _item(self, id: str) -> dict[str, Any]:
        return await self._get(f"/Users/{self.config.get('user_id')}/Items/{id}")

    async def star(self, track_id: str, starred: bool) -> None:
        await self._request("POST" if starred else "DELETE", f"/Users/{self.config.get('user_id')}/FavoriteItems/{track_id}")

    async def scrobble(self, track_id: str, submission: bool) -> None:
        if submission:
            await self._request("POST", f"/Users/{self.config.get('user_id')}/PlayedItems/{track_id}")
        else:
            await super().scrobble(track_id, submission)
