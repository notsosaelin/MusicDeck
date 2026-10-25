"""
A library's clean-up report (Local files' Library tools): what's untidy, each with its fix.

  duplicates     the same song more than once (same artist and title, lengths within a few seconds), whatever
                 the folder or format: the best copy (lossless, then the highest bitrate) is the one to keep
  split albums   one album shown as several: files in one folder with one album name but different album
                 artists (some with none), so the library groups them apart; fixed by giving them one
  untagged       songs missing an artist, an album or a track number
  missing files  songs in the index whose file has gone (moved or deleted outside MusicDeck): a rescan drops them

Albums without a cover are found by LocalService (whether one exists means looking in folders and files).
These are plain functions over the index ({track id: fields}), so they can be tested without a library.
"""
import os
import re
from collections import Counter, defaultdict
from typing import Any, Callable

LENGTH_SLACK = 3  # seconds two copies of a song may differ by
LOSSLESS = {"FLAC", "ALAC", "WAV", "AIFF", "Monkey's Audio", "WavPack", "DSD", "TAK", "TTA"}
# words that differ between copies of one song without making it another
NOISE = re.compile(r"\s*[\(\[](?:\d{4} )?(?:remaster(?:ed)?|album version|explicit|clean|mono|stereo)[^\)\]]*[\)\]]\s*", re.I)
PUNCTUATION = re.compile(r"[^\w\s]")


def norm(text: str) -> str:
    """A name compared loosely: case, punctuation, "the", remaster notes and spacing aside."""
    text = NOISE.sub(" ", text or "").lower()
    text = PUNCTUATION.sub(" ", text)
    text = re.sub(r"^the\s+", "", text.strip())
    return " ".join(text.split())


def quality_rank(t: dict[str, Any]) -> tuple:
    """Higher is better: lossless first, then bits, sample rate and bitrate."""
    return (t.get("format") in LOSSLESS, t.get("bits") or 0, t.get("rate") or 0, t.get("bitrate") or 0, t.get("size") or 0)


def duplicates(tracks: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Groups of track ids that are one song, the best copy first; biggest groups first."""
    by_name: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tid, t in tracks.items():
        artist = norm(t.get("artist") or t.get("albumartist") or "")
        title = norm(t.get("title") or "")
        if title and artist:
            by_name[(artist, title)].append(tid)
    groups: list[list[str]] = []
    for ids in by_name.values():
        if len(ids) < 2:
            continue
        # within a name, songs of about the same length are copies; a live take minutes longer isn't
        ids.sort(key=lambda tid: tracks[tid].get("duration") or 0)
        cluster = [ids[0]]
        for tid in ids[1:]:
            if abs((tracks[tid].get("duration") or 0) - (tracks[cluster[-1]].get("duration") or 0)) <= LENGTH_SLACK:
                cluster.append(tid)
            else:
                if len(cluster) > 1:
                    groups.append(cluster)
                cluster = [tid]
        if len(cluster) > 1:
            groups.append(cluster)
    for group in groups:
        group.sort(key=lambda tid: quality_rank(tracks[tid]), reverse=True)
    groups.sort(key=lambda g: (-len(g), norm(tracks[g[0]].get("title") or "")))
    return groups


def split_albums(tracks: dict[str, dict[str, Any]], album_key: Callable[[dict[str, Any]], str],
                 album_folder: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    """Albums shown as several: [{"album", "folder", "parts": [{"album_id", "albumartist", "tracks"}], "suggest"}],
    "suggest" being the album artist to give them all (the one most of its files have)."""
    by_place: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tid, t in tracks.items():
        if t.get("album"):
            by_place[(album_folder(t), t["album"].lower())].append(tid)
    out = []
    for (folder, _), ids in by_place.items():
        parts: dict[str, list[str]] = defaultdict(list)
        for tid in ids:
            parts[album_key(tracks[tid])].append(tid)
        if len(parts) < 2:
            continue
        named = Counter(tracks[tid]["albumartist"] for tid in ids if tracks[tid].get("albumartist"))
        artists = {(tracks[tid].get("artist") or "").lower() for tid in ids if tracks[tid].get("artist")}
        suggest = named.most_common(1)[0][0] if named else ("Various artists" if len(artists) > 1 else
                                                             next(iter(tracks[tid]["artist"] for tid in ids if tracks[tid].get("artist")), ""))
        out.append({"album": tracks[ids[0]]["album"], "folder": folder, "suggest": suggest, "ids": ids,
                    "parts": [{"album_id": key, "albumartist": tracks[members[0]].get("albumartist") or "",
                               "tracks": len(members)} for key, members in parts.items()]})
    out.sort(key=lambda s: s["album"].lower())
    return out


def untagged(tracks: dict[str, dict[str, Any]]) -> list[tuple[str, list[str]]]:
    """Songs missing details a library sorts by: [(track id, ["artist", "album", "track number"])]."""
    out = []
    for tid, t in tracks.items():
        missing = [name for name, have in (("artist", t.get("artist") or t.get("albumartist")), ("album", t.get("album")),
                                            ("track number", t.get("track"))) if not have]
        if missing:
            out.append((tid, missing))
    out.sort(key=lambda m: tracks[m[0]]["path"].lower())
    return out


def missing_files(tracks: dict[str, dict[str, Any]]) -> list[str]:
    return [tid for tid, t in tracks.items() if not os.path.exists(t["path"])]
