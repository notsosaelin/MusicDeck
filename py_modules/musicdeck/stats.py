"""
Your listening, across every source: each listen (a song played past the point that counts, as for
ListenBrainz) is a line in plays.jsonl, and the stats page sums them for a time: songs, hours, top artists,
albums and songs, when in the day and week you listen, the days in a row.

A line: {"t" (seconds since 1970), "src" (the source's id), "id", "title", "artist", "album", "album_id",
"artist_id", "art", "dur" (seconds)}, and "dev" for a listen on another of the user's devices (peers.py). The log keeps the last MAX_LINES listens (years of them): when it grows
past that, the oldest go.
"""
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional

MAX_LINES = 200_000
TOP = 10
PERIODS = ("week", "month", "year", "all")


class PlayLog:
    def __init__(self, path: str):
        self.path = path
        # the log's length, counted from the file the first time a listen is added
        self.lines: Optional[int] = None

    def add(self, source_id: str, track: dict[str, Any], when: Optional[float] = None, device: str = "") -> None:
        entry = {"t": int(when or time.time()), "src": source_id, "id": str(track.get("id") or ""),
                 "title": str(track.get("title") or "")[:300], "artist": str(track.get("artist") or "")[:300],
                 "album": str(track.get("album") or "")[:300], "album_id": track.get("album_id") or "",
                 "artist_id": track.get("artist_id") or "", "art": track.get("art") or "",
                 "dur": round(float(track.get("duration") or track.get("dur") or 0))}
        if device:
            entry["dev"] = device
        if self.lines is None:
            try:
                with open(self.path, "rb") as f:
                    self.lines = sum(1 for _ in f)
            except FileNotFoundError:
                self.lines = 0
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.lines += 1
        if self.lines > MAX_LINES * 1.1:
            self._trim()

    def _trim(self) -> None:
        entries = self.read()
        keep = entries[-MAX_LINES:]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in keep)
        os.replace(tmp, self.path)
        self.lines = len(keep)

    def read(self, since: float = 0) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue  # a line cut short by a crash
                    if isinstance(entry, dict) and entry.get("t", 0) >= since:
                        out.append(entry)
        except FileNotFoundError:
            pass
        return out

    def summary(self, period: str = "month", now: Optional[float] = None) -> dict[str, Any]:
        """The stats for the last week, month or year (rolling), or for all time."""
        now = now or time.time()
        since = {"week": now - 7 * 86400, "month": now - 30 * 86400, "year": now - 365 * 86400}.get(period, 0)
        entries = self.read(since)
        artists: Counter = Counter()
        artist_time: Counter = Counter()
        albums: Counter = Counter()
        songs: Counter = Counter()
        info: dict[tuple, dict[str, Any]] = {}
        hours = [0] * 24
        weekdays = [0] * 7
        days: set = set()
        seconds = 0
        for e in entries:
            seconds += e.get("dur", 0)
            artist = e.get("artist") or "Unknown artist"
            artists[artist] += 1
            artist_time[artist] += e.get("dur", 0)
            info.setdefault(("artist", artist), {"name": artist, "id": e.get("artist_id"), "src": e.get("src"), "art": e.get("art")})
            if e.get("album"):
                key = ("album", e["album"].lower(), artist.lower())
                albums[key] += 1
                info.setdefault(key, {"name": e["album"], "artist": artist, "id": e.get("album_id"), "src": e.get("src"),
                                      "art": e.get("art")})
            key = ("song", (e.get("title") or "").lower(), artist.lower())
            songs[key] += 1
            # the newest listen's details: a song played from two sources is shown as it last played
            info[key] = {"name": e.get("title") or "", "artist": artist, "id": e.get("id"), "src": e.get("src"),
                         "art": e.get("art"), "album": e.get("album"), "album_id": e.get("album_id"), "dur": e.get("dur", 0),
                         "artist_id": e.get("artist_id")}
            moment = datetime.fromtimestamp(e["t"])
            hours[moment.hour] += 1
            weekdays[moment.weekday()] += 1
            days.add(moment.date())

        def top(counter: Counter, kind: str) -> list[dict[str, Any]]:
            return [{**info[k], "plays": n} for k, n in counter.most_common(TOP)] if kind != "artist" else [
                {**info[("artist", k)], "plays": n, "seconds": artist_time[k]} for k, n in counter.most_common(TOP)]

        return {
            "period": period,
            "plays": len(entries),
            "seconds": seconds,
            "songs": len(songs),
            "artists_count": len(artists),
            "days": len(days),
            "streak": _streak(days, datetime.fromtimestamp(now).date()),
            "top_artists": top(artists, "artist"),
            "top_albums": top(albums, "album"),
            "top_songs": top(songs, "song"),
            "hours": hours,
            "weekdays": weekdays,
            "first": entries[0]["t"] if entries else None,
        }


def _streak(days: set, today) -> int:
    """Days in a row with music, up to today (or yesterday: today may not have started yet)."""
    day = today if today in days else today - timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n

