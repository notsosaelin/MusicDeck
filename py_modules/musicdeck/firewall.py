"""
Windows Firewall and your devices: another device reaches this PC on MusicDeck's two ports (peers.py), which Windows
blocks until it's allowed, and blocks for good once its "allow this app" prompt was turned down (rules blocking
Decky's program, which win over any rule allowing a port). Settings shows "Let your devices reach this PC" while
that's so; it asks Windows (an administrator's yes, Windows' own prompt) to replace those blocks with rules letting
in MusicDeck's ports only, from the home network only.
"""
import asyncio
import base64
import os
import subprocess
import sys
from typing import Optional

from . import peers

RULE = "MusicDeck: your devices"
WINDOWS = os.name == "nt"


def _encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode()


async def _powershell(script: str, timeout: float = 20) -> Optional[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded(script),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return out.decode("utf-8", "replace").strip()
    except (OSError, asyncio.TimeoutError):
        return None


def _program() -> str:
    """Decky's program, which the plugin's backend runs in (Windows' rules name it)."""
    return sys.executable


async def state() -> str:
    """"ok" (nothing in the way, or MusicDeck's rules there), "blocked" (Windows blocks Decky's program), "" (not
    Windows, or unknown)."""
    if not WINDOWS:
        return ""
    program = _program().replace("'", "''")
    out = await _powershell(
        f"$p = '{program}'; "
        "$block = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -eq $p } | "
        "Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }).Count; "
        f"$ours = @(Get-NetFirewallRule -DisplayName '{RULE}*' -ErrorAction SilentlyContinue).Count; "
        "Write-Output \"$block $ours\"")
    if not out:
        return ""
    try:
        block, ours = (int(x) for x in out.split()[-2:])
    except ValueError:
        return ""
    return "blocked" if block or not ours else "ok"


async def allow() -> bool:
    """Asks Windows (its administrator prompt) to let MusicDeck's ports in from the home network, taking out the
    rules that block Decky's program. True once it's done."""
    if not WINDOWS:
        return False
    program = _program().replace("'", "''")
    inner = (
        f"$p = '{program}'; "
        "Get-NetFirewallApplicationFilter | Where-Object { $_.Program -eq $p } | Get-NetFirewallRule | "
        "Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' } | Remove-NetFirewallRule; "
        f"Get-NetFirewallRule -DisplayName '{RULE}*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
        f"New-NetFirewallRule -DisplayName '{RULE}' -Direction Inbound -Program $p -Protocol TCP "
        f"-LocalPort {peers.PORT} -RemoteAddress LocalSubnet -Action Allow | Out-Null; "
        f"New-NetFirewallRule -DisplayName '{RULE} (finding them)' -Direction Inbound -Program $p -Protocol UDP "
        f"-LocalPort {peers.GROUP[1]} -RemoteAddress LocalSubnet -Action Allow | Out-Null")
    await _powershell(
        "Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-EncodedCommand','{_encoded(inner)}'", timeout=180)
    return await state() == "ok"
