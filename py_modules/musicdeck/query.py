"""
Search words with fields, as foobar2000 and MusicBee take them: `artist:nirvana year:>1990 rating:>=4 "come as"`.

  field:value     the field contains it (text) or equals it (numbers)
  field:=value    exactly
  field:>n  <n  >=n  <=n   numbers (year, rating, plays, skips, length in seconds, bitrate in kbps)
  "two words"     together; plain words must all be somewhere in the title, artist, album, album artist or composer

Unknown fields are plain words. `parse` gives (conditions, words); `holds` tests one song's fields.
"""
import re
import shlex
from typing import Any

TEXT = {"artist": "artist", "albumartist": "albumartist", "album": "album", "title": "title", "genre": "genre",
        "composer": "composer", "label": "label", "format": "format", "path": "path"}
NUMBERS = {"year": "year", "rating": "rating", "plays": "plays", "skips": "skips", "length": "duration",
           "duration": "duration", "bitrate": "bitrate", "track": "track", "disc": "disc"}
TERM = re.compile(r"^(\w+):(>=|<=|=|>|<)?(.*)$")


def parse(query: str) -> tuple[list[tuple[str, str, Any]], list[str]]:
    """[(field, op, value)] and the plain words, lower-cased."""
    try:
        parts = shlex.split(query)
    except ValueError:  # an unclosed quote: words as they are
        parts = query.split()
    conditions, words = [], []
    for part in parts:
        match = TERM.match(part)
        field = match.group(1).lower() if match else ""
        if match and (field in TEXT or field in NUMBERS) and match.group(3):
            op, value = match.group(2) or ":", match.group(3).strip().lower()
            if field in NUMBERS:
                try:
                    conditions.append((NUMBERS[field], op if op != ":" else "=", float(value)))
                except ValueError:
                    words.append(part.lower())
            else:
                conditions.append((TEXT[field], op, value))
        elif part.strip():
            words.append(part.lower())
    return conditions, words


def has_fields(query: str) -> bool:
    return bool(parse(query)[0])


def holds(values: dict[str, Any], conditions: list[tuple[str, str, Any]], words: list[str]) -> bool:
    """`values`: a song's fields (texts lower-cased; "genre" may be a list; numbers as numbers)."""
    for field, op, want in conditions:
        have = values.get(field)
        if isinstance(want, float):
            try:
                have = float(have or 0)
            except (TypeError, ValueError):
                return False
            if not {"=": have == want, ">": have > want, "<": have < want, ">=": have >= want, "<=": have <= want}[op]:
                return False
        else:
            texts = have if isinstance(have, list) else [have or ""]
            texts = [str(x).lower() for x in texts]
            if op == "=" and want not in texts:
                return False
            if op != "=" and not any(want in x for x in texts):
                return False
    if words:
        blob = " ".join(str(values.get(k) or "") for k in ("title", "artist", "album", "albumartist", "composer")).lower()
        return all(w in blob for w in words)
    return True


def sort_name(text: str) -> str:
    """How a name sorts: "The Beatles" under B, case aside."""
    lowered = (text or "").strip().lower()
    return lowered[4:] if lowered.startswith("the ") and len(lowered) > 4 else lowered
