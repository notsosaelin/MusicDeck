"""
Phones and players over MTP on Windows, where nothing mounts them as folders (GVfs does on Linux):
bin/windows-x86_64/mtp-helper.exe (backend/mtp-helper/mtp.c) talks to them through Windows Portable
Devices and answers in JSON lines. Each call here runs it once; `fetch` sends it a batch, so a phone
is opened once for a whole index's worth of songs.

Windows Portable Devices lists USB sticks and card readers as well (as "wpdbusenum" devices): those
are drive letters, found as such (devices.py), so they're left out here.
"""
import json
import os
import re
import subprocess
from typing import Any, Callable, Optional

from . import native

TIMEOUT = 30


def helper(runtime_dir: Optional[str] = None) -> Optional[str]:
    return native.helper("mtp-helper", runtime_dir) if native.WINDOWS else None


def _lines(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _run(exe: str, *args: str, timeout: float = TIMEOUT) -> list[dict[str, Any]]:
    result = subprocess.run([exe, *args], capture_output=True, timeout=timeout, **native.NO_WINDOW)
    lines = _lines(result.stdout.decode("utf-8", "replace"))
    errors = [line["error"] for line in lines if "error" in line and "id" not in line]
    if result.returncode != 0 or errors:
        raise OSError(errors[0] if errors else f"mtp-helper stopped ({result.returncode})")
    return lines


#: what Windows' generic MTP driver calls every device, which says nothing of which it is
DRIVER_NAMES = ("mtp usb device", "(standard mtp device)", "mtp device", "")


def phones(exe: str) -> list[dict[str, str]]:
    """[{"id": its WPD device id, "name", "maker", "serial"}] of what's plugged in by MTP, by what the
    device itself says it is (its own name, else its model; its maker) rather than its driver's words;
    [] when the helper can't say (it's run every few seconds: a hiccup isn't a crash)."""
    try:
        found = _run(exe, "devices", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for d in found:
        if not d.get("id") or "wpdbusenum" in d["id"].lower():
            continue
        named = [n for n in (d.get("own_name"), d.get("model"), d.get("name")) if (n or "").strip().lower() not in DRIVER_NAMES]
        makers = [m for m in (d.get("own_maker"), d.get("maker")) if (m or "").strip().lower() not in DRIVER_NAMES]
        out.append({"id": d["id"], "name": (named[0] if named else "Phone").strip(),
                    "maker": (makers[0] if makers else "").strip(), "serial": (d.get("serial") or "").strip()})
    return out


USB_PART = re.compile(r"usb#(vid_[0-9a-f]{4}&pid_[0-9a-f]{4}(?:&mi_\d+)?)#([^#]+)#", re.IGNORECASE)


def stable_key(device_id: str, serial: str = "") -> str:
    """What stays the same about a phone between plugs and USB ports: its vendor and product, and its
    serial number: the one it says itself, else the one in its WPD id (\\\\?\\usb#vid_18d1&pid_4ee1#<serial>#
    {...}), unless that's one Windows made up (with "&" in it: a composite device's interface); the whole
    id otherwise."""
    m = USB_PART.search(device_id)
    if not m:
        return device_id.lower()
    usb = re.sub(r"&mi_\d+", "", m.group(1).lower())
    serial = serial or ("" if "&" in m.group(2) else m.group(2))
    return f"{usb}#{serial.lower()}" if serial else f"{m.group(1)}#{m.group(2)}".lower()


def listing(exe: str, device: str, tops: tuple[str, ...]) -> tuple[list[dict], list[dict]]:
    """(storages: {"id", "name", "free", "total"}, entries: {"type": "file" | "folder", "storage",
    "path" (under the storage, with /), "id", "size", "mtime"}) of what's under the top folders named,
    on each storage that has any of them (or the whole of one that has none)."""
    lines = _run(exe, "list", device, *tops, timeout=600)
    storages = [line for line in lines if line.get("type") == "storage"]
    entries = [line for line in lines if line.get("type") in ("file", "folder")]
    return storages, entries


def fetch(exe: str, device: str, wanted: list[tuple[str, str]],
          done: Optional[Callable[[str, bool], None]] = None) -> set[str]:
    """Copies objects (id, local path) off the device, one helper for them all; the ids that came."""
    if not wanted:
        return set()
    for _, dest in wanted:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    proc = subprocess.Popen([exe, "get", device], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, **native.NO_WINDOW)
    assert proc.stdin and proc.stdout
    got = set()
    try:
        for object_id, dest in wanted:
            proc.stdin.write(f"{object_id}\t{dest}\n".encode("utf-8"))
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                break
            answer = _lines(line.decode("utf-8", "replace"))
            ok = bool(answer and answer[0].get("ok"))
            if ok:
                got.add(object_id)
            if done:
                done(object_id, ok)
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return got


def put(exe: str, device: str, storage: str, folder: str, path: str, name: Optional[str] = None) -> str:
    """Copies a file onto the device, into `folder` (made if it isn't there) on `storage`; its new id."""
    lines = _run(exe, "put", device, storage, folder, path, *([name] if name else []), timeout=900)
    return lines[0].get("id", "") if lines else ""


def delete(exe: str, device: str, object_id: str) -> None:
    _run(exe, "delete", device, object_id)
