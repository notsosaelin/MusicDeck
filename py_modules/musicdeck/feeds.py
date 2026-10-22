"""
Podcast feeds: RSS 2.0 with Apple's podcast tags, read without the standard library's xml package
(Decky's Python leaves it out). A small, forgiving XML reader is enough for feeds: elements,
attributes, text, CDATA, comments and entities. Namespaces stay as prefixes ("itunes:image").
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

TOKEN = re.compile(
    r"<!--.*?-->"                                    # comment
    r"|<!\[CDATA\[(?P<cdata>.*?)\]\]>"               # CDATA: text taken as it is
    r"|<\?.*?\?>|<![A-Za-z][^>]*>"                   # declarations, DOCTYPE
    r"|<(?P<close>/?)(?P<tag>[\w:.-]+)(?P<attrs>(?:\s+[^\s=/>]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?)*)\s*(?P<empty>/?)>"
    r"|(?P<text>[^<]+)",
    re.S)
ATTR = re.compile(r"([^\s=/>]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?")
ENTITY = re.compile(r"&(#[xX][0-9a-fA-F]+|#\d+|\w+);")
ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ", "hellip": "…",
            "mdash": "—", "ndash": "–", "rsquo": "’", "lsquo": "‘", "rdquo": "”", "ldquo": "“",
            "copy": "©", "reg": "®", "trade": "™", "eacute": "é", "egrave": "è", "agrave": "à"}
TAGS = re.compile(r"<[^>]+>")
ENCODING = re.compile(rb'^\s*<\?xml[^>]*encoding=["\']([\w.-]+)["\']')


def unescape(text: str) -> str:
    def one(m: re.Match) -> str:
        name = m.group(1)
        if name.startswith("#"):
            try:
                return chr(int(name[2:], 16) if name[1] in "xX" else int(name[1:]))
            except (ValueError, OverflowError):
                return m.group(0)
        return ENTITIES.get(name, m.group(0))
    return ENTITY.sub(one, text)


def tidy(text: str) -> str:
    """Titles and names: plain text already (entities were decoded when read), just tidied."""
    return " ".join(text.split())


def plain_text(html: str) -> str:
    """Show notes and descriptions are often HTML: tags dropped, entities decoded, spaces tidied."""
    return " ".join(unescape(TAGS.sub(" ", html)).split())


class Element:
    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag = tag
        self.attrs = attrs
        self.children: list["Element"] = []
        self.parts: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self.parts).strip()

    def find(self, tag: str) -> Optional["Element"]:
        return next((c for c in self.children if c.tag == tag), None)

    def findall(self, tag: str) -> list["Element"]:
        return [c for c in self.children if c.tag == tag]

    def text_of(self, tag: str) -> str:
        child = self.find(tag)
        return child.text if child else ""


def parse_xml(text: str) -> Element:
    """The document as a tree under a nameless root. Unclosed or stray tags don't stop it: a closing
    tag closes back to its opening one, and one that matches nothing is ignored."""
    root = Element("", {})
    stack = [root]
    pos = 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:  # a lone "<" in text
            stack[-1].parts.append("<")
            pos += 1
            continue
        pos = m.end()
        if m.group("cdata") is not None:
            stack[-1].parts.append(m.group("cdata"))
        elif m.group("text") is not None:
            stack[-1].parts.append(unescape(m.group("text")))
        elif m.group("tag"):
            tag = m.group("tag")
            if m.group("close"):
                for depth in range(len(stack) - 1, 0, -1):
                    if stack[depth].tag == tag:
                        del stack[depth:]
                        break
                continue
            attrs = {a.group(1): unescape(a.group(2) or a.group(3) or a.group(4) or "")
                     for a in ATTR.finditer(m.group("attrs") or "")}
            element = Element(tag, attrs)
            stack[-1].children.append(element)
            if not m.group("empty"):
                stack.append(element)
    return root


def decode(data: bytes) -> str:
    m = ENCODING.match(data)
    try:
        return data.decode(m.group(1).decode() if m else "utf-8")
    except (LookupError, UnicodeDecodeError):
        return data.decode("utf-8", "replace")


def duration_seconds(value: str) -> float:
    """itunes:duration: "1:02:03", "62:03" or seconds ("3723", "3723.5")."""
    try:
        parts = [float(p) for p in value.strip().split(":")]
    except ValueError:
        return 0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds if len(parts) <= 3 else 0


MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
RFC822 = re.compile(r"(\d{1,2})\s+(\w{3})\w*\s+(\d{2,4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([+-]\d{4}|[A-Z]{1,4})?")
ZONES = {"GMT": 0, "UT": 0, "UTC": 0, "Z": 0, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6,
         "PST": -8, "PDT": -7}


def published(value: str) -> float:
    """pubDate (RFC 822, e.g. "Tue, 10 Jun 2025 04:00:00 +0000") as epoch seconds; 0 if unreadable."""
    m = RFC822.search(value or "")
    if not m or m.group(2).lower()[:3] not in MONTHS:
        return 0
    day, month, year, hour, minute, second, zone = m.groups()
    year_n = int(year) + (2000 if len(year) == 2 else 0)
    try:
        when = datetime(year_n, MONTHS[month.lower()[:3]], int(day), int(hour), int(minute), int(second or 0),
                        tzinfo=timezone.utc)
    except ValueError:
        return 0
    if zone and zone[0] in "+-":
        offset = timedelta(hours=int(zone[1:3]), minutes=int(zone[3:5]))
        when -= offset if zone[0] == "+" else -offset
    elif zone in ZONES:
        when -= timedelta(hours=ZONES[zone])
    return when.timestamp()


def _image(element: Element) -> str:
    img = element.find("itunes:image")
    if img is not None and img.attrs.get("href"):
        return img.attrs["href"]
    legacy = element.find("image")
    return legacy.text_of("url") if legacy else ""


def parse_feed(data: bytes) -> dict[str, Any]:
    """A podcast: {"title", "author", "description", "art", "episodes": [episode]}, episodes newest first,
    each {"guid", "title", "url", "duration", "published", "description", "art"}. Items without an audio
    file (announcements, video-only) are left out. Raises ValueError if this isn't a podcast feed."""
    channel = parse_xml(decode(data)).find("rss")
    channel = channel.find("channel") if channel else None
    if channel is None:
        raise ValueError("Not a podcast feed")
    art = _image(channel)
    episodes = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        url = enclosure.attrs.get("url", "") if enclosure is not None else ""
        kind = enclosure.attrs.get("type", "") if enclosure is not None else ""
        if not url.startswith(("https://", "http://")) or (kind and not kind.startswith(("audio/", "video/mp4"))):
            continue
        episodes.append({
            "guid": item.text_of("guid") or url,
            "title": tidy(item.text_of("title")) or "Episode",
            "url": url,
            "duration": duration_seconds(item.text_of("itunes:duration")),
            "published": published(item.text_of("pubDate")),
            "description": plain_text(item.text_of("itunes:summary") or item.text_of("description"))[:500],
            "art": _image(item) or art,
        })
    episodes.sort(key=lambda e: e["published"], reverse=True)
    return {
        "title": tidy(channel.text_of("title")) or "Podcast",
        "author": tidy(channel.text_of("itunes:author") or channel.text_of("managingEditor")),
        "description": plain_text(channel.text_of("itunes:summary") or channel.text_of("description"))[:500],
        "art": art,
        "episodes": episodes,
    }
