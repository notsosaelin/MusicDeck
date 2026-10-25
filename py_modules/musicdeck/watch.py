"""Noticing music copied into the local library's folders, so it's scanned without a rescan by hand.

Linux's inotify, through ctypes (Decky's Python has no package for it): a watch on every folder under
the library's roots, new folders watched as they appear. Copying an album is many events over many
seconds, so a change is only reported once the folders have been quiet for `quiet` seconds. Where
inotify can't be had (macOS, or the system's watch limit reached), `available` is False and the caller
relies on its periodic scan instead; that covers what's past MAX_WATCHED folders too.

On Windows, ReadDirectoryChangesW: one watch on each root takes in every folder under it, read in a
thread of its own (it waits for changes), what it says handed to the event loop as inotify's is.
"""
import asyncio
import ctypes
import ctypes.util
import logging
import os
import struct
import threading
import time
from typing import Callable, Optional

from . import tagging, winio

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

FILE_LIST_DIRECTORY = 0x0001
FILE_SHARE_ALL = 0x7
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
#: file and folder names, sizes and writes: what copying music in changes
NOTIFY_FILTER = 0x1 | 0x2 | 0x8 | 0x10
WIN_BUFFER = 64 * 1024
#: Linux: the most folders watched, and the longest the first walk of them may take
MAX_WATCHED = 10_000
WALK_SECONDS = 2.0


def parse_notify(data: bytes) -> list[tuple[int, str]]:
    """FILE_NOTIFY_INFORMATION records: (action, path under the watched folder, with backslashes)."""
    found, at = [], 0
    while at + 12 <= len(data):
        following, action, length = struct.unpack_from("<III", data, at)
        found.append((action, data[at + 12:at + 12 + length].decode("utf-16-le", "replace")))
        if not following:
            break
        at += following
    return found


def relevant(path: str) -> bool:
    """Whether a change to `path` (under a library root) is music arriving or leaving, not the library's
    own files (lyrics, covers) or a hidden folder's, or a file still being written under a temporary name."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts or any(p.startswith(".") for p in parts) or tagging.is_temp(parts[-1]):
        return False
    return not parts[-1].lower().endswith(IGNORED_SUFFIXES)


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
        self.libc = None if winio.WINDOWS else _libc()
        self.available = False
        # Windows: a handle on each root being watched, and the loop to tell
        self.handles: list[int] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self) -> None:
        if winio.WINDOWS and self.roots:
            self._start_windows()
            return
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

    def _start_windows(self) -> None:
        self.loop = asyncio.get_running_loop()
        k32 = winio.kernel32
        for root in self.roots:
            handle = k32.CreateFileW(root, FILE_LIST_DIRECTORY, FILE_SHARE_ALL, None, winio.OPEN_EXISTING,
                                     FILE_FLAG_BACKUP_SEMANTICS, None)
            if handle in (None, winio.INVALID_HANDLE):
                continue
            self.handles.append(handle)
            threading.Thread(target=self._read_windows, args=(handle,), daemon=True, name="musicdeck-watch").start()
        self.available = bool(self.handles)

    def _read_windows(self, handle: int) -> None:
        """Waits for changes under one root (in its own thread) until stop() cancels the wait."""
        k32 = winio.kernel32
        buf = ctypes.create_string_buffer(WIN_BUFFER)
        got = ctypes.c_ulong(0)
        while True:
            ok = k32.ReadDirectoryChangesW(ctypes.c_void_p(handle), buf, WIN_BUFFER, True, NOTIFY_FILTER,
                                           ctypes.byref(got), None, None)
            if not ok:
                return  # stopped (its handle cancelled or closed), or the folder went away
            # nothing came back: more changed than the buffer holds, which is a change too
            changes = parse_notify(buf.raw[:got.value]) if got.value else [(0, "?")]
            if any(relevant(path) for _, path in changes) and self.loop and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self._settle)

    def stop(self) -> None:
        if self.timer:
            self.timer.cancel()
            self.timer = None
        for handle in self.handles:
            winio.kernel32.CancelIoEx(ctypes.c_void_p(handle), None)
            winio.kernel32.CloseHandle(handle)
        self.handles.clear()
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
        # a root that's a whole drive (or "/") would be walked for minutes, on the event loop: watching stops
        # at MAX_WATCHED folders or WALK_SECONDS, and the periodic scan covers the rest
        deadline = time.monotonic() + WALK_SECONDS
        for directory, dirnames, _ in os.walk(top):
            if len(self.dirs) >= MAX_WATCHED or time.monotonic() > deadline:
                _log.info(f"Not watching every folder under {top} for new music: there are too many")
                return
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
