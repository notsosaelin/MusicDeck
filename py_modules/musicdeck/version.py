"""MusicDeck's version, from the plugin's package.json (the one place it's set; Decky shows it too), and
the User-Agent the sources' servers see."""
import json
import os

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read() -> str:
    try:
        with open(os.path.join(PLUGIN_ROOT, "package.json"), encoding="utf-8") as f:
            return str(json.load(f).get("version") or "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


def current() -> str:
    """The version installed now: a frontend update brings a new package.json without restarting the
    backend, which read VERSION when it started."""
    return _read()


VERSION = _read()
USER_AGENT = f"MusicDeck/{VERSION}"
