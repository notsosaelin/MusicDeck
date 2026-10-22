"""
Optional verbose log for troubleshooting, toggled from the plugin settings.

Frontend (player) and backend (streams, services) events land in one file so a playback problem can
be read as a single timeline. Off by default; when off, `debug()` costs a single boolean check.
"""
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

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


def debug(source: str, event: str, **data: Any) -> None:
    if _handler is None:
        return
    extra = " " + json.dumps(data, default=str, ensure_ascii=False) if data else ""
    _logger.debug(f"{source:<8} {event}{extra}")
