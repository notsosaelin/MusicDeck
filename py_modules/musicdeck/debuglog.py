"""
Optional verbose log for troubleshooting, toggled from the plugin settings.

Frontend (player) and backend (streams, services) events land in one file so a playback problem can
be read as a single timeline. Off by default; when off, `debug()` costs a single boolean check.

Logs get shared in bug reports, so nothing secret may reach them: `redact` takes the query out of
every web address (where Subsonic tokens, signed links and the stream server's key travel) and
masks values named like credentials. It's applied to everything this log writes, to the plugin's
main log (`Redacting`), and to error messages shown in the UI.
"""
import json
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

URL = re.compile(r"(https?://[^\s'\"<>?#]+)\?[^\s'\"<>#]*")
CREDENTIAL = re.compile(r"(?i)\b(token|access_token|refresh_token|api_?key|password|passwd|secret|"
                        r"signature|x-plex-token|x-musicdeck-key|sk|api_sig|session_key)(\s*[=:]\s*\"?)([^\s&'\",;]+)")
BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]{16,}")


def redact(value: Any) -> Any:
    """`value` with addresses' queries and credential-looking values hidden (strings, and strings
    inside lists and dicts)."""
    if isinstance(value, str):
        value = URL.sub(r"\1?…", value)
        value = CREDENTIAL.sub(r"\1\2…", value)
        return BEARER.sub(r"\1 …", value)
    if isinstance(value, dict):
        return {k: "…" if CREDENTIAL.match(f"{k}=x") else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class Redacting(logging.Filter):
    """Redacts a logger's messages and tracebacks (for the plugin's main log)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = redact(record.getMessage()), None
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True

_logger = logging.getLogger("musicdeck.debug")
_logger.propagate = False
_handler: Optional[logging.Handler] = None
path = ""


def configure(log_dir: str, enabled: bool) -> None:
    global _handler, path
    path = os.path.join(log_dir, "musicdeck-debug.log")
    if enabled and _handler is None:
        os.makedirs(log_dir, exist_ok=True)
        _handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _logger.addHandler(_handler)
        _logger.setLevel(logging.DEBUG)
        _logger.debug("backend  debug log enabled")
    elif not enabled and _handler is not None:
        _logger.debug("backend  debug log disabled")
        _logger.removeHandler(_handler)
        _handler.close()
        _handler = None


def enabled() -> bool:
    return _handler is not None


def debug(source: str, event: str, /, **data: Any) -> None:
    # positional only: a field named "source" or "event" is just a field (it raised before, and lost what it was in)
    if _handler is None:
        return
    extra = " " + json.dumps(redact(data), default=str, ensure_ascii=False) if data else ""
    _logger.debug(f"{source:<8} {redact(event)}{extra}")
