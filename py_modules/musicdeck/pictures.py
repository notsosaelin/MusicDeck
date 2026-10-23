"""Artists' pictures for the local source, from Deezer's public catalogue API (no account, no key: the
same pictures its web player shows). Only metadata is asked for; Deezer's music isn't played. Asked only
when the user looks for an artist's picture, or for every missing picture.
"""
from typing import Any, Optional

import aiohttp

from . import version
from .services.base import ServiceError

API = "https://api.deezer.com"
MAX_PICTURE_BYTES = 15 * 1024 * 1024
PICTURE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def plain(text: str) -> str:
    """A name compared loosely: case, punctuation and spacing aside ("AC/DC" is "ac dc")."""
    return " ".join("".join(c if c.isalnum() else " " for c in text.casefold()).split())


async def download(session: aiohttp.ClientSession, url: str, headers: Optional[dict[str, str]] = None,
                   types: dict[str, str] = PICTURE_TYPES) -> Optional[tuple[bytes, str]]:
    """A whole picture (bytes, extension), or None: not a picture, too big, or cut short."""
    try:
        async with session.get(url, headers=headers) as resp:
            ext = types.get(resp.content_type)
            if resp.status != 200 or not ext or (resp.content_length or 0) > MAX_PICTURE_BYTES:
                return None
            # to the end: content.read(n) would hand back only what has arrived so far
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > MAX_PICTURE_BYTES:
                    return None
            if not data or len(data) < (resp.content_length or 0):
                return None
            return bytes(data), ext
    except (aiohttp.ClientError, TimeoutError):
        return None


async def artist_picture(session: aiohttp.ClientSession, name: str) -> Optional[tuple[bytes, str]]:
    """The picture of the artist named exactly `name` (the best known of several), or None."""
    headers = {"User-Agent": f"MusicDeck/{version.VERSION}"}
    try:
        async with session.get(f"{API}/search/artist", params={"q": name, "limit": 10}, headers=headers) as resp:
            if resp.status != 200:
                raise ServiceError(f"Deezer returned HTTP {resp.status}")
            data: Any = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as e:
        raise ServiceError(f"Couldn't reach Deezer: {e}") from e
    found = [a for a in (data or {}).get("data") or []
             if isinstance(a, dict) and plain(a.get("name") or "") == plain(name)
             # an artist without a picture of their own has Deezer's blank one ("/artist//")
             and a.get("picture_xl") and "/artist//" not in a["picture_xl"]]
    if not found:
        return None
    best = max(found, key=lambda a: int(a.get("nb_fan") or 0))
    return await download(session, best["picture_xl"], headers)
