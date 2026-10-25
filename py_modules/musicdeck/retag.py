"""
Tag edits for many songs at once (Library tools, and a selection's or album's "Tag tools"), as Mp3tag does them.
Each makes a plan, {track id: {field: new value}}, from the index's fields; the caller shows it, then writes it
through the tag editor (so every file's edit can be undone). Fields are the editor's (tagging.FIELDS).

  from_names  fields read from the file names by a pattern: "{track} - {artist} - {title}"
  case        Title Case (small words lower, but first and last) or Sentence case, for titles, artists, albums
  renumber    track numbers 1, 2, 3... in file-name order (disc by disc), and optionally "of n"
  replace     find and replace in a field (plain text, any case)
"""
import os
import re
from typing import Any

TOKENS = ("artist", "albumartist", "album", "title", "track", "disc", "year", "genre", "composer")
TOKEN = re.compile(r"\{(\w+)\}")
# words Title Case leaves small (unless first or last)
SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor", "of", "off", "on", "or", "out",
         "over", "the", "to", "up", "vs", "via", "with", "feat", "ft"}
CASE_FIELDS = ("title", "artist", "album", "albumartist")


class PatternError(ValueError):
    """A pattern that can't be used, said so the user can fix it."""


def name_pattern(pattern: str) -> re.Pattern:
    """A file-name pattern ("{track} - {title}") as a regular expression with a group per field."""
    names = TOKEN.findall(pattern)
    if not names:
        raise PatternError("Put at least one field in braces, e.g. {title}")
    unknown = [n for n in names if n not in TOKENS]
    if unknown:
        raise PatternError(f"Unknown field {{{unknown[0]}}}: use {', '.join('{' + t + '}' for t in TOKENS)}")
    if len(set(names)) != len(names):
        raise PatternError("Each field only once")
    out, at = "^", 0
    for match in TOKEN.finditer(pattern):
        out += re.escape(pattern[at:match.start()])
        name = match.group(1)
        out += rf"(?P<{name}>\d+)" if name in ("track", "disc", "year") else rf"(?P<{name}>.+?)"
        at = match.end()
    return re.compile(out + re.escape(pattern[at:]) + "$")


def from_names(tracks: dict[str, dict[str, Any]], ids: list[str], pattern: str) -> dict[str, dict[str, Any]]:
    regex = name_pattern(pattern)
    plan: dict[str, dict[str, Any]] = {}
    for tid in ids:
        stem = os.path.splitext(os.path.basename(tracks[tid]["path"]))[0]
        match = regex.match(stem)
        if not match:
            continue
        changes = {}
        for field, value in match.groupdict().items():
            value = value.strip()
            if field in ("track", "disc"):
                value = int(value)
            if value != tracks[tid].get(field):
                changes[field] = value
        if changes:
            plan[tid] = changes
    return plan


def title_case(text: str) -> str:
    words = text.split(" ")
    out = []
    for i, word in enumerate(words):
        bare = word.strip("([{\"'").lower()
        if not word:
            out.append(word)
        elif word.isupper() and len(word) > 1 and any(c.isalpha() for c in word) and len(bare) <= 4:
            out.append(word)  # an acronym (AC/DC, NYC) stays as it is
        elif 0 < i < len(words) - 1 and bare in SMALL:
            out.append(word.lower())
        else:
            lead = len(word) - len(word.lstrip("([{\"'"))
            out.append(word[:lead] + word[lead:lead + 1].upper() + word[lead + 1:].lower())
    return " ".join(out)


def sentence_case(text: str) -> str:
    lowered = text.lower()
    return lowered[:1].upper() + lowered[1:]


def case(tracks: dict[str, dict[str, Any]], ids: list[str], style: str, fields: list[str]) -> dict[str, dict[str, Any]]:
    change = title_case if style == "title" else sentence_case
    plan: dict[str, dict[str, Any]] = {}
    for tid in ids:
        changes = {}
        for field in fields:
            if field not in CASE_FIELDS:
                continue
            old = tracks[tid].get(field) or ""
            new = change(old)
            if old and new != old:
                changes[field] = new
        if changes:
            plan[tid] = changes
    return plan


def renumber(tracks: dict[str, dict[str, Any]], ids: list[str]) -> dict[str, dict[str, Any]]:
    """1, 2, 3... in file-name order, counted again on each disc."""
    by_disc: dict[int, list[str]] = {}
    for tid in ids:
        by_disc.setdefault(tracks[tid].get("disc") or 0, []).append(tid)
    plan: dict[str, dict[str, Any]] = {}
    for disc_ids in by_disc.values():
        disc_ids.sort(key=lambda tid: os.path.basename(tracks[tid]["path"]).lower())
        for n, tid in enumerate(disc_ids, 1):
            if tracks[tid].get("track") != n:
                plan[tid] = {"track": n}
    return plan


def replace(tracks: dict[str, dict[str, Any]], ids: list[str], field: str, find: str, to: str) -> dict[str, dict[str, Any]]:
    if field not in ("title", "artist", "album", "albumartist", "genre", "composer", "label"):
        raise PatternError("Pick a text field")
    if not find:
        raise PatternError("Say what to find")
    regex = re.compile(re.escape(find), re.I)
    plan: dict[str, dict[str, Any]] = {}
    for tid in ids:
        old = tracks[tid].get(field) or ""
        new = regex.sub(lambda _: to, old).strip()
        if new != old:
            plan[tid] = {field: new}
    return plan
