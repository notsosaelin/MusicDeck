"""
One library: every source's music together, as Steam's library is every game together whatever store it came
from. Home and "Your music" (playlists, albums, artists, songs) gather what each source has; each collection and
track says which source it's from ("source": its instance id), so opening, playing, rating or downloading it goes
to that source as before. A source's own tabs (Qobuz's picks, Local files' folders) stay its own. Search asks each
source from the frontend (src/library.ts), so each source's results show as they come.

Sources are asked at once, each given a few seconds: a slow or failing one is left out, not waited for.
"""
import asyncio
import re
from typing import Any, Awaitable, Callable, Optional

from . import models

#: the one library's tabs: its id, title, and which of a source's tabs (by id, of the user's own) go into it
MERGED: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("playlists", "Playlists", ("playlists", "musicdeck-playlists")),
    ("albums", "Albums", ("albums", "soundtracks")),
    ("artists", "Artists", ("artists", "composers")),
    ("songs", "Songs", ("songs", "liked")),
)
MERGED_IDS = {m[0] for m in MERGED}
#: how long a source may take before the rest are shown without it
SECONDS = 8.0
#: on Home, the newest of what was played, from every source
JUMP_BACK = 12
#: on Home, the kinds of cards the sources' rows take turns with
HOME_KINDS = ("album", "playlist")
#: a Home row of fewer cards than this is passed over for a fuller one
HOME_ROW_MIN = 3

# sections a source's Home starts with that the one Home has already, from every source at once
OWN_HOME = {"Jump back in", "Your playlists"}


def tag(value: Any, source: str) -> Any:
    """A copy of sections, a page, collections or tracks, each collection and track saying it's `source`'s."""
    if isinstance(value, list):
        return [tag(v, source) for v in value]
    if not isinstance(value, dict):
        return value
    out = dict(value)
    if "id" in out and ("kind" in out or "title" in out) and "items" not in out and "collections" not in out:
        out.setdefault("source", source)  # a collection or a track
        return out
    for key in ("items", "tracks", "collections"):
        if isinstance(out.get(key), list):
            out[key] = tag(out[key], source)
    if isinstance(out.get("extra"), list):
        out["extra"] = [{**x, "tracks": tag(x.get("tracks") or [], source)} if isinstance(x, dict) else x for x in out["extra"]]
    return out


def tabs_for(merged_id: str) -> tuple[str, ...]:
    return next((m[2] for m in MERGED if m[0] == merged_id), ())


def merged_tabs(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The one library's tabs some source has something for: {"id", "title"}. `sources`: {"tabs": its
    center_tabs()} each; the user's own MusicDeck playlists count for Playlists anywhere."""
    have = {t["id"] for s in sources for t in s.get("tabs", []) if t.get("group", "yours") == "yours"}
    return [{"id": mid, "title": title} for mid, title, ids in MERGED if have & set(ids) or (mid == "playlists" and sources)]


_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)


def sort_key(item: dict[str, Any]) -> tuple[str, str]:
    title = str(item.get("title") or "")
    return (_ARTICLE.sub("", title).casefold(), str(item.get("subtitle") or "").casefold())


def merge_tab(title: str, parts: list[tuple[str, list[dict[str, Any]]]], sort: bool = True) -> list[dict[str, Any]]:
    """Sources' sections for one of the one library's tabs as one: every collection in one grid (in title
    order), every track in one list (source by source), each tagged with its source. `parts`: (source,
    its sections), in the sources' order."""
    items: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source, sections in parts:
        for s in sections:
            if not isinstance(s, dict) or s.get("notice"):
                continue
            for c in tag(s.get("items") or [], source):
                key = (source, str(c.get("kind")), str(c.get("id")))
                if key not in seen:  # a playlist both in a "mine" section and MusicDeck's own
                    seen.add(key)
                    items.append(c)
            tracks.extend(tag(s.get("tracks") or [], source))
    if sort:
        items.sort(key=sort_key)
    if not items and not tracks:
        return []
    return [{"title": "", "items": items, "tracks": tracks, "display": "grid"}]


def merge_home(jump_back: list[dict[str, Any]], playlists: list[dict[str, Any]],
               homes: list[tuple[str, str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Home: what was played lately, from every source, then each source under its name: a row of its cards, then a
    list of its songs. The rows take turns, albums then playlists, where a source has both, so Home reads as a mix
    rather than the same kind over and over; a row of a few cards over one card alone. (Your playlists are Browse's.)
    `jump_back`: already tagged; `homes`: (source, its name, its Home's sections), in the sources' order."""
    out: list[dict[str, Any]] = []
    if jump_back:
        out.append({"title": "Jump back in", "items": [c for c in jump_back if "kind" in c],
                    "tracks": [t for t in jump_back if "kind" not in t]})
    turn = 0
    for source, name, sections in homes:
        shown = [s for s in sections if isinstance(s, dict) and s.get("title") not in OWN_HOME and not s.get("notice")
                 and not (s.get("items") and models.is_categories(s))]
        rows = [s for s in shown if s.get("items")]
        wanted = HOME_KINDS[turn % len(HOME_KINDS)]
        cards = (next((s for s in rows if _mostly(s, wanted) and len(s["items"]) >= HOME_ROW_MIN), None)
                 or next((s for s in rows if len(s["items"]) >= HOME_ROW_MIN), None) or (rows[0] if rows else None))
        songs = next((s for s in shown if s.get("tracks")), None)
        titled = lambda s: f"{name} · {s['title']}" if s.get("title") else name  # noqa: E731
        if cards:
            out.append(tag({**cards, "title": titled(cards), "tracks": []}, source))
            if _mostly(cards, wanted):
                turn += 1
        if songs:
            out.append(tag({**songs, "title": titled(songs), "items": []}, source))
    return out


def _mostly(section: dict[str, Any], kind: str) -> bool:
    """Whether most of a section's cards are of `kind` ("album", "playlist")."""
    items = section.get("items") or []
    return bool(items) and sum(1 for c in items if c.get("kind") == kind) * 2 > len(items)


async def gather(calls: list[tuple[str, Callable[[], Awaitable[Any]]]], seconds: float = SECONDS,
                 log: Optional[Any] = None) -> list[tuple[str, Any]]:
    """(source, what it gave) for each source that answered in time, in the order asked."""
    async def one(source: str, call: Callable[[], Awaitable[Any]]) -> Optional[tuple[str, Any]]:
        try:
            return source, await asyncio.wait_for(call(), seconds)
        except Exception as e:  # noqa: BLE001 - a source that fails is left out, the others still show
            if log:
                log.info(f"One library: {source} left out ({type(e).__name__}: {e})")
            return None
    results = await asyncio.gather(*[one(s, c) for s, c in calls])
    return [r for r in results if r is not None]
