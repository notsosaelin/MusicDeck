"""
The trash, as desktop file managers keep it (freedesktop.org's Trash specification): music deleted from
Local files goes there, not away, so it can be brought back, from MusicDeck (Local files' Recently
deleted: game mode has no file manager) or from the desktop's Trash.

* A file in the home's file system goes to ~/.local/share/Trash ($XDG_DATA_HOME/Trash): files/<name> and
  info/<name>.trashinfo (where it was, when it went).
* One elsewhere (an SD card, a second drive) goes to that drive's own trash, <its top>/.Trash-<uid>, as
  the spec says: moving it there is a rename, and a trash across drives would be a copy.
* Windows: the Recycle Bin (shell API), which MusicDeck doesn't restore from.
"""
import ctypes
import datetime
import os
import urllib.parse
from typing import Optional

#: Windows' Recycle Bin, or the spec's trash folders (tests use those everywhere)
RECYCLE_BIN = os.name == "nt"


def _uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 1000


def _mount_top(path: str) -> str:
    """The top of the file system a path is on."""
    path = os.path.abspath(path)
    dev = os.stat(path).st_dev
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            return path
        try:
            if os.stat(parent).st_dev != dev:
                return path
        except OSError:
            return path
        path = parent


def _home_trash() -> str:
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "Trash")


def trash_dir_for(path: str) -> tuple[str, Optional[str]]:
    """(the trash a path goes to, the top its .trashinfo paths are relative to, or None for absolute)."""
    home_trash = _home_trash()
    os.makedirs(os.path.join(home_trash, "files"), exist_ok=True)
    if os.stat(path).st_dev == os.stat(home_trash).st_dev:
        return home_trash, None
    top = _mount_top(path)
    return os.path.join(top, f".Trash-{_uid()}"), top


def _free_name(files_dir: str, info_dir: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    candidate, n = name, 1
    while os.path.lexists(os.path.join(files_dir, candidate)) or \
            os.path.lexists(os.path.join(info_dir, candidate + ".trashinfo")):
        n += 1
        candidate = f"{stem}.{n}{ext}"
    return candidate


def trash(path: str) -> dict[str, str]:
    """Moves a file or folder to the trash; returns where it went: {"original", "trashed", "info"}
    ("" for both on Windows, where it's the Recycle Bin's)."""
    path = os.path.abspath(path)
    if RECYCLE_BIN:
        _recycle(path)
        return {"original": path, "trashed": "", "info": ""}
    trash_root, top = trash_dir_for(path)
    files_dir, info_dir = os.path.join(trash_root, "files"), os.path.join(trash_root, "info")
    os.makedirs(files_dir, exist_ok=True)
    os.makedirs(info_dir, exist_ok=True)
    if top is not None:
        os.chmod(trash_root, 0o700)
    name = _free_name(files_dir, info_dir, os.path.basename(path.rstrip(os.sep)) or "item")
    shown = os.path.relpath(path, top) if top else path
    info = os.path.join(info_dir, name + ".trashinfo")
    # the info first, as the spec asks: a file in files/ without one would be lost to the desktop's Trash
    with open(info, "x", encoding="utf-8") as f:
        f.write("[Trash Info]\nPath=" + urllib.parse.quote(shown) + "\nDeletionDate="
                + datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + "\n")
    target = os.path.join(files_dir, name)
    try:
        os.rename(path, target)
    except OSError:
        os.remove(info)
        raise
    return {"original": path, "trashed": target, "info": info}


def restore(item: dict[str, str]) -> bool:
    """Puts a trashed file or folder back where it was; False when it's gone from the trash (emptied)
    or something else is there now."""
    trashed, original = item.get("trashed", ""), item.get("original", "")
    if not trashed or not os.path.lexists(trashed) or os.path.lexists(original):
        return False
    os.makedirs(os.path.dirname(original), exist_ok=True)
    os.rename(trashed, original)
    try:
        os.remove(item.get("info", ""))
    except OSError:
        pass
    return True


def still_there(item: dict[str, str]) -> bool:
    return bool(item.get("trashed")) and os.path.lexists(item["trashed"])


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]


def _recycle(path: str) -> None:
    """Windows: to the Recycle Bin, silently (FO_DELETE with FOF_ALLOWUNDO)."""
    op = _SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = path + "\0"  # a double-NUL-terminated list
    op.fFlags = 0x0040 | 0x0010 | 0x0400 | 0x0004  # ALLOWUNDO, NOCONFIRMATION, NOERRORUI, SILENT
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # type: ignore[attr-defined]
    if result != 0:
        raise OSError(f"Couldn't move {path} to the Recycle Bin ({result})")
