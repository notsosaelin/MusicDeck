"""Noticing music copied into the local library's folders, so it's scanned without a rescan by hand.

Linux's inotify, through ctypes (Decky's Python has no package for it): a watch on every folder under
the library's roots, new folders watched as they appear. Copying an album is many events over many
seconds, so a change is only reported once the folders have been quiet for `quiet` seconds. Where
inotify can't be had (another OS, or the system's watch limit reached), `available` is False and the
caller relies on its periodic scan instead.
"""
import asyncio
import ctypes
import ctypes.util
import logging
import os
import struct
from typing import Callable, Optional

from . import tagging

IN_CLOSE_WRITE = 0x008
IN_MOVED_FROM = 0x040
IN_MOVED_TO = 0x080
IN_CREATE = 0x100
IN_DELETE = 0x200
IN_DELETE_SELF = 0x400
IN_ISDIR = 0x40000000
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000
MASK = IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF
EVENT = struct.Struct("iIII")  # wd, mask, cookie, len (then the name, padded)
# what the library writes itself, or what doesn't change what's in it
IGNORED_SUFFIXES = (".lrc", ".txt", ".jpg", ".jpeg", ".png", ".webp", ".nfo", ".log", ".cue")

_log = logging.getLogger("musicdeck")


def _libc() -> Optional[ctypes.CDLL]:
    name = ctypes.util.find_library("c")
    try:
        libc = ctypes.CDLL(name or "libc.so.6", use_errno=True)
        libc.inotify_init1  # noqa: B018 - only Linux's libc has it
        return libc
    except (OSError, AttributeError):
        return None


class FolderWatcher:
    def __init__(self, roots: list[str], on_change: Callable[[], None], quiet: float = 30.0):
        self.roots = roots
        self.on_change = on_change
        self.quiet = quiet
        self.fd = -1
        self.dirs: dict[int, str] = {}
        self.timer: Optional[asyncio.TimerHandle] = None
        self.libc = _libc()
        self.available = False

    def start(self) -> None:
        if self.libc is None or not self.roots:
            return
        self.fd = self.libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            return
        for root in self.roots:
            self._watch_tree(root)
        if not self.dirs:
            self.stop()
            return
        asyncio.get_running_loop().add_reader(self.fd, self._read)
        self.available = True

    def stop(self) -> None:
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.fd >= 0:
            try:
                asyncio.get_running_loop().remove_reader(self.fd)
            except (RuntimeError, ValueError):
                pass
            os.close(self.fd)
            self.fd = -1
        self.dirs.clear()
        self.available = False

    def _watch_tree(self, top: str) -> None:
        for directory, dirnames, _ in os.walk(top):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            wd = self.libc.inotify_add_watch(self.fd, os.fsencode(directory), MASK)
            if wd < 0:
                # out of watches (fs.inotify.max_user_watches): the periodic scan covers the rest
                _log.info(f"Can't watch more folders for new music ({os.strerror(ctypes.get_errno())})")
                return
            self.dirs[wd] = directory

    def _read(self) -> None:
        try:
            data = os.read(self.fd, 64 * 1024)
        except BlockingIOError:
            return
        relevant = False
        at = 0
        while at + EVENT.size <= len(data):
            wd, mask, _, length = EVENT.unpack_from(data, at)
            name = data[at + EVENT.size:at + EVENT.size + length].rstrip(b"\0").decode("utf-8", "surrogateescape")
            at += EVENT.size + length
            parent = self.dirs.get(wd)
            if mask & IN_DELETE_SELF:
                self.dirs.pop(wd, None)
                continue
            if parent is None or not name or name.startswith(".") or tagging.is_temp(name):
                continue
            if mask & IN_ISDIR:
                if mask & (IN_CREATE | IN_MOVED_TO):
                    self._watch_tree(os.path.join(parent, name))
                relevant = True
            elif not name.lower().endswith(IGNORED_SUFFIXES):
                relevant = True
        if relevant:
            self._settle()

    def _settle(self) -> None:
        """Reports the change once nothing has changed for `quiet` seconds."""
        if self.timer:
            self.timer.cancel()
        self.timer = asyncio.get_running_loop().call_later(self.quiet, self._fire)

    def _fire(self) -> None:
        self.timer = None
        self.on_change()
