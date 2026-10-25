"""
Which release an album is: found in every catalogue MusicDeck asks (MusicBrainz, Apple, Deezer), weighed
against the files themselves, and the catalogues' word on one record put together. The way beets and Picard
judge a match, without a fingerprint: by the songs, not only the names.

* Each candidate's track list is set beside the album's files (match_tracks): by disc and track number where
  both say them, else by title and length. Then a confidence (0-100): how close the lengths are (the
  strongest sign: two releases of one name rarely have the same running times), the titles, the track count,
  the album's title and artist, its year. Without lengths (a catalogue that doesn't give them) the rest
  weigh more.
* Candidates that are the same record (as many tracks, the same lengths) become one (merge): MusicBrainz's
  ids, titles and credits where it has the record; the genre and label of whichever catalogue gives them;
  the largest cover. The user picks a record, never a catalogue.
"""
import difflib
from typing import Any, Optional

from .pictures import plain

#: a track's length this far off still counts as the same recording (fades, gaps, pressings)
LENGTH_SLACK = 3
#: and this far off, as nothing like it
LENGTH_FAR = 20
#: covers by size, best first (what each catalogue's links give)
COVER_RANK = ["Apple Music", "Deezer", "MusicBrainz"]


def similar(a: str, b: str) -> float:
    """0-1: how alike two names are, ignoring case, accents and punctuation ("Kind of Blue (Legacy
    Edition)" and "Kind of Blue" are close, not the same)."""
    x, y = plain(a or ""), plain(b or "")
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    return difflib.SequenceMatcher(None, x, y).ratio()


def length_closeness(a: float, b: float) -> Optional[float]:
    """1 when two lengths agree (within LENGTH_SLACK s), falling to 0 at LENGTH_FAR off; None unknown."""
    if not a or not b:
        return None
    off = abs(a - b)
    return 1.0 if off <= LENGTH_SLACK else max(0.0, 1 - (off - LENGTH_SLACK) / (LENGTH_FAR - LENGTH_SLACK))


def match_tracks(files: list[dict[str, Any]], tracks: list[dict[str, Any]]) -> list[Optional[int]]:
    """For each file ({"title", "track", "disc", "duration"}), the index of the release track it is, or None.
    Track numbers first (when the files have them and the release has that disc and number), then the best
    title-and-length pairs among what's left; nothing is matched twice, and a poor pair isn't made."""
    found: list[Optional[int]] = [None] * len(files)
    taken: set[int] = set()
    at = {(int(t.get("disc") or 1), int(t.get("track") or 0)): i for i, t in enumerate(tracks)}
    for i, f in enumerate(files):
        key = (int(f.get("disc") or 1), int(f.get("track") or 0))
        j = at.get(key) if key[1] else None
        if j is None and key[1] and len({d for d, _ in at}) == 1:
            j = at.get((next(iter(at))[0], key[1]))  # files say disc 0 or nothing, the release one disc
        if j is not None and j not in taken:
            # a number that agrees, unless the names and lengths both say otherwise
            close = length_closeness(f.get("duration") or 0, tracks[j].get("length") or 0)
            if similar(f.get("title", ""), tracks[j].get("title", "")) > 0.3 or (close is not None and close > 0.5):
                found[i] = j
                taken.add(j)
    pairs = []
    for i, f in enumerate(files):
        if found[i] is not None:
            continue
        for j, t in enumerate(tracks):
            if j in taken:
                continue
            close = length_closeness(f.get("duration") or 0, t.get("length") or 0)
            score = similar(f.get("title", ""), t.get("title", "")) * 0.65 + (close if close is not None else 0.5) * 0.35
            pairs.append((score, i, j))
    for score, i, j in sorted(pairs, reverse=True):
        if score < 0.5 or found[i] is not None or j in taken:
            continue
        found[i] = j
        taken.add(j)
    return found


def confidence(files: list[dict[str, Any]], album: str, artist: str, year: str, release: dict[str, Any]) -> int:
    """How sure it is that the files are this release: 0-100."""
    tracks = release.get("tracks") or []
    if not tracks or not files:
        return 0
    mapping = match_tracks(files, tracks)
    pairs = [(files[i], tracks[j]) for i, j in enumerate(mapping) if j is not None]
    count = 1 - abs(len(files) - len(tracks)) / max(len(files), len(tracks))
    covered = len(pairs) / len(files)
    titles = sum(similar(f.get("title", ""), t.get("title", "")) for f, t in pairs) / len(files)
    closeness = [c for c in (length_closeness(f.get("duration") or 0, t.get("length") or 0) for f, t in pairs) if c is not None]
    lengths = sum(closeness) / len(files) if closeness else None
    parts = [(similar(album, release.get("title", "")), 0.15), (count * covered, 0.15), (titles, 0.25)]
    if artist:
        parts.append((max(similar(artist, release.get("artist", "")),
                          1.0 if plain(release.get("artist", "")) in ("various artists", "various") and not artist else 0), 0.10))
    if lengths is not None:
        parts.append((lengths, 0.35))
    total = sum(score * weight for score, weight in parts) / sum(weight for _, weight in parts)
    # the year, when both say one: a little more sure when they agree (reissues often don't)
    if year and release.get("year"):
        total += 0.03 if year[:4] == release["year"][:4] else -0.02
    return max(0, min(100, round(total * 100)))


def same_record(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two catalogues' releases of one record: as many tracks, of the same lengths (where both say them)
    and much the same titles."""
    ta, tb = a.get("tracks") or [], b.get("tracks") or []
    if not ta or len(ta) != len(tb):
        return False
    lengths = [length_closeness(x.get("length") or 0, y.get("length") or 0) for x, y in zip(ta, tb)]
    known = [c for c in lengths if c is not None]
    if known and sum(known) / len(known) < 0.8:
        return False
    return sum(similar(x.get("title", ""), y.get("title", "")) for x, y in zip(ta, tb)) / len(ta) > 0.7


def merge(group: list[dict[str, Any]]) -> dict[str, Any]:
    """One record from catalogues that agree on it: MusicBrainz's release as the base (its ids, titles and
    credits, the year it first came out) where it's among them, else the most confident; genres and the label
    from whichever has them; the largest cover; every catalogue named in "sources"."""
    base = next((r for r in group if r.get("source") == "MusicBrainz"), None) or max(group, key=lambda r: r.get("confidence", 0))
    merged = dict(base)
    for r in sorted(group, key=lambda r: -r.get("confidence", 0)):
        for field in ("year", "label"):
            if not merged.get(field) and r.get(field):
                merged[field] = r[field]
        if not merged.get("genres") and r.get("genres"):
            merged["genres"] = r["genres"]
        # a track's length where the base doesn't know one
        if r is not base and len(r.get("tracks") or []) == len(merged.get("tracks") or []):
            merged["tracks"] = [{**t, "length": t.get("length") or o.get("length") or 0}
                                for t, o in zip(merged["tracks"], r["tracks"])]
    covers = sorted((r for r in group if r.get("cover")), key=lambda r: COVER_RANK.index(r["source"]) if r.get("source") in COVER_RANK else 9)
    if covers:
        merged["cover"] = covers[0]["cover"]
    merged["sources"] = sorted({r.get("source", "") for r in group} - {""}, key=lambda s: COVER_RANK.index(s) if s in COVER_RANK else 9)
    merged["confidence"] = max(r.get("confidence", 0) for r in group)
    merged["also"] = [r["id"] for r in group if r is not base]
    return merged


def records(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates (each a full release with "source" and "confidence") as records, most likely first: the
    ones that are the same record merged."""
    groups: list[list[dict[str, Any]]] = []
    for c in sorted(candidates, key=lambda r: -r.get("confidence", 0)):
        for g in groups:
            if same_record(g[0], c):
                g.append(c)
                break
        else:
            groups.append([c])
    return sorted((merge(g) for g in groups), key=lambda r: -r["confidence"])
