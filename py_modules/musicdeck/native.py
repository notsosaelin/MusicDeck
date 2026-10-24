"""
The platform MusicDeck runs on, and the native helpers built for it.

Decky Loader runs on Linux (x86_64 and, since 3.3, arm64: SteamOS, Bazzite, Armada on ARM handhelds,
DroidDeck on Android) and, experimentally, on Windows (x86_64 and arm64, e.g. installed by Playhub). The
plugin's Python is the same everywhere; only the helper programs (QuickJS for YouTube, the Spotify
helper) are built per platform, into bin/<platform>/. Windows on arm64 runs the x86_64 builds through
its own emulation, so it falls back to them.
"""
import os
import platform
import subprocess
from typing import Optional

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WINDOWS = os.name == "nt"


def platform_id(system: Optional[str] = None, machine: Optional[str] = None) -> str:
    """"linux-x86_64", "linux-arm64", "windows-x86_64", "windows-arm64", "macos-arm64"..."""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    os_name = {"windows": "windows", "darwin": "macos"}.get(system, "linux")
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64", "armv8": "arm64"}.get(machine, machine)
    return f"{os_name}-{arch}"


def _candidates(platform_name: str) -> list[str]:
    # Windows on Arm runs x86_64 programs through Prism; nothing else has a fallback
    return [platform_name, "windows-x86_64"] if platform_name == "windows-arm64" else [platform_name]


def helper(name: str, runtime_dir: Optional[str], platform_name: Optional[str] = None) -> Optional[str]:
    """A helper program built for this platform: in the data folder's bin/ (a side-loaded copy) or the
    plugin's, under bin/<platform>/, made executable if unpacking lost that. None when there's no build
    for this platform. Builds from before per-platform folders sat in bin/ itself, for linux-x86_64."""
    platform_name = platform_name or platform_id()
    exe = name + (".exe" if platform_name.startswith("windows") else "")
    bases = [b for b in (runtime_dir, PLUGIN_ROOT) if b]
    places = [os.path.join(base, "bin", p, exe) for base in bases for p in _candidates(platform_name)]
    if platform_name == "linux-x86_64":
        places += [os.path.join(base, "bin", name) for base in bases]
    for path in places:
        if os.path.isfile(path):
            if not WINDOWS and not os.access(path, os.X_OK):
                try:
                    os.chmod(path, 0o755)  # zips extracted on some systems lose the executable bit
                except OSError:
                    pass
            return path
    return None


#: For every helper process: on Windows, no console window of its own (Decky runs without one, so each
#: program it starts would otherwise open a window over the game)
NO_WINDOW: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
