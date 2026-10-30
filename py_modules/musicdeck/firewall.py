"""
Windows Firewall and your devices: another device reaches this PC on MusicDeck's two ports (peers.py), which Windows
blocks until it's allowed, and blocks for good once its "allow this app" prompt was turned down (rules blocking
Decky's program, which win over any rule allowing a port). Settings shows "Allow device connections" while that's
so; it asks Windows (an administrator's yes, Windows' own prompt) to replace those blocks with rules letting in
MusicDeck's ports only, from the home network only, and the internet link's helper (farlink.py: musicdeck-net.exe,
which only answers paired devices) from anywhere. It's done once: the helper waits for it to start (main.py), so
Windows never stops to ask "allow this app?" about it, and its rules stay as MusicDeck updates (the same path).
"""
import asyncio
import base64
import os
import subprocess
import sys
from typing import Optional

from . import peers

RULE = "MusicDeck: your devices"
FAR_RULE = RULE + " (over the internet)"
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


async def state(helper: Optional[str] = None) -> str:
    """"ok" (nothing in the way, or MusicDeck's rules there), "blocked" (Windows blocks Decky's program or the
    internet link's `helper`, or MusicDeck's rules aren't there yet), "" (not Windows, or unknown)."""
    if not WINDOWS:
        return ""
    program = _program().replace("'", "''")
    far = (helper or "").replace("'", "''")
    out = await _powershell(
        f"$p = '{program}'; $h = '{far}'; "
        "$block = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Program -eq $p -or ($h -and $_.Program -eq $h) } | "
        "Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }).Count; "
        f"$ours = @(Get-NetFirewallRule -DisplayName '{RULE}*' -ErrorAction SilentlyContinue).Count; "
        # the helper let in: by Allow device connections, or by someone's "Allow" on Windows' own prompt
        "$link = if ($h) { @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Program -eq $h } | Get-NetFirewallRule | "
        "Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' }).Count } else { 1 }; "
        "Write-Output \"$block $ours $link\"")
    if not out:
        return ""
    try:
        block, ours, link = (int(x) for x in out.split()[-3:])
    except ValueError:
        return ""
    return "blocked" if block or not ours or not link else "ok"


async def allow(helper: Optional[str] = None) -> bool:
    """Asks Windows (its administrator prompt) to let MusicDeck's ports in from the home network and the internet
    link's `helper` in from anywhere, taking out the rules that block either. True once it's done."""
    if not WINDOWS:
        return False
    program = _program().replace("'", "''")
    far = (helper or "").replace("'", "''")
    inner = (
        f"$p = '{program}'; $h = '{far}'; "
        "Get-NetFirewallApplicationFilter | Where-Object { $_.Program -eq $p -or ($h -and $_.Program -eq $h) } | "
        "Get-NetFirewallRule | Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' } | Remove-NetFirewallRule; "
        f"Get-NetFirewallRule -DisplayName '{RULE}*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
        f"New-NetFirewallRule -DisplayName '{RULE}' -Direction Inbound -Program $p -Protocol TCP "
        f"-LocalPort {peers.PORT} -RemoteAddress LocalSubnet -Action Allow | Out-Null; "
        f"New-NetFirewallRule -DisplayName '{RULE} (finding them)' -Direction Inbound -Program $p -Protocol UDP "
        f"-LocalPort {peers.GROUP[1]} -RemoteAddress LocalSubnet -Action Allow | Out-Null; "
        # the internet link: UDP from anywhere, to the helper only (it answers paired devices and nothing else)
        f"if ($h) {{ New-NetFirewallRule -DisplayName '{FAR_RULE}' -Direction Inbound -Program $h -Protocol UDP "
        "-Action Allow | Out-Null }")
    await _powershell(
        "Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-EncodedCommand','{_encoded(inner)}'", timeout=180)
    return await state(helper) == "ok"
