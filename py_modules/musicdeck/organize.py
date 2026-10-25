"""
Where songs' files would go if the library were tidied by their tags (MediaMonkey's Auto-Organize): a pattern like
"{albumartist}/{album}/{disc-}{track} {title}" made into a path under a library folder, for every song, with
names made safe for Windows and Linux (and for FAT on cards and players), clashes found before anything moves.

The plan is shown first; moving is LocalService's (files, their .lrc beside them, folders left empty, and every
id that changes with the path: plays, favourites, ratings, playlists). The last moves are kept to undo.
"""
import os
import re
from typing import Any

DEFAULT = "{albumartist}/{album}/{disc-}{track} {title}"
FIELDS = {"artist", "albumartist", "album", "title", "track", "disc-", "year", "genre", "ext"}
TOKEN = re.compile(r"\{([\w-]+)\}")
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MAX_PART = 120  # characters in one folder or file name


class PatternError(ValueError):
    pass


def check(pattern: str) -> None:
    names = TOKEN.findall(pattern)
    if "title" not in names and "track" not in names:
        raise PatternError("The file name needs {title} or {track}, or songs would land on each other")
    unknown = [n for n in names if n not in FIELDS]
    if unknown:
        raise PatternError(f"Unknown field {{{unknown[0]}}}: use {', '.join('{' + f + '}' for f in sorted(FIELDS))}")
    if pattern.startswith(("/", "\\")) or ".." in pattern.replace("\\", "/").split("/"):
        raise PatternError("The pattern is a path inside the library folder")


def safe(part: str) -> str:
    """One folder or file name that every system takes."""
    part = UNSAFE.sub("_", part).strip().rstrip(". ")
    if part.lower().split(".")[0] in RESERVED:
        part = "_" + part
    return part[:MAX_PART].rstrip(". ") or "_"


def target(t: dict[str, Any], pattern: str, discs: int) -> str:
    """A song's path (relative, with its extension) by the pattern. `discs`: how many its album has ({disc-} is
    "2-" only on an album of several)."""
    ext = os.path.splitext(t["path"])[1].lower()
    values = {
        "artist": t.get("artist") or t.get("albumartist") or "Unknown artist",
        "albumartist": t.get("albumartist") or t.get("artist") or "Unknown artist",
        "album": t.get("album") or "Unknown album",
        "title": t.get("title") or os.path.splitext(os.path.basename(t["path"]))[0],
        "track": f"{t['track']:02d}" if t.get("track") else "",
        "disc-": f"{t['disc']}-" if discs > 1 and t.get("disc") else "",
        "year": t.get("year") or "",
        "genre": t.get("genre") or "",
        "ext": ext.lstrip("."),
    }
    parts = []
    for raw in pattern.replace("\\", "/").split("/"):
        filled = TOKEN.sub(lambda m: values.get(m.group(1), ""), raw)
        filled = re.sub(r"\s+", " ", filled).strip(" -_")
        parts.append(safe(filled))
    return os.path.join(*parts) + ext


def plan(tracks: dict[str, dict[str, Any]], root: str, pattern: str, discs_of: Any) -> dict[str, Any]:
    """{"moves": [(track id, from, to)], "same": n (already where they belong), "clashes": [(to, [track ids])],
    "untagged": n (left where they are: with neither an artist nor an album, there's nowhere better to put them)}"""
    check(pattern)
    moves, same, untagged = [], 0, 0
    wanted: dict[str, tuple[str, list[str]]] = {}
    for tid, t in tracks.items():
        if not (t.get("artist") or t.get("albumartist")) and not t.get("album"):
            untagged += 1
            continue
        to = os.path.join(root, target(t, pattern, discs_of(t)))
        wanted.setdefault(os.path.normcase(to), (to, []))[1].append(tid)
        if os.path.normcase(to) == os.path.normcase(t["path"]):
            same += 1
        else:
            moves.append((tid, t["path"], to))
    # two songs that would get one name (same tags): neither moves
    clashes = [(to, ids) for to, ids in wanted.values() if len(ids) > 1]
    clashing = {tid for _, ids in clashes for tid in ids}
    # a song whose place another file (not in the library, or not moving) already holds is a clash too
    taken = {os.path.normcase(t["path"]) for tid, t in tracks.items()} - {os.path.normcase(m[1]) for m in moves}
    for tid, src, to in moves:
        if tid not in clashing and (os.path.normcase(to) in taken or (os.path.exists(to) and os.path.normcase(to) != os.path.normcase(src))):
            clashes.append((to, [tid]))
            clashing.add(tid)
    return {"moves": [m for m in moves if m[0] not in clashing], "same": same, "clashes": clashes, "untagged": untagged}
