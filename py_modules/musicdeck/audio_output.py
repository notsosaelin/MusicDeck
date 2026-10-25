"""Which output the sound plays on (the speakers, headphones, a Bluetooth device, a TV over HDMI), so the
equalizer can be set for each (main.py). Read from PipeWire through its PulseAudio side with `pactl`:
Steam's own audio API sees the speakers and the headphone jack as one device, and PipeWire tells them
apart by the sink's active port. `pactl subscribe` says when anything about the sinks changes.

On Windows, the default output endpoint (winio.default_audio_output, the MMDevice API), looked at every
POLL_SECONDS: its id is the same for the same device, so each still has an equalizer of its own."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any, Awaitable, Callable

from . import winio
from .background import spawn

# how long to let a burst of changes (a device appearing sends several) settle before reading them
SETTLE_SECONDS = 0.5
# pactl stops when PipeWire restarts; how long to wait before following it again
RESTART_SECONDS = 5
# Windows: how often the default output is looked at
POLL_SECONDS = 2


def describe(sinks: list[dict[str, Any]], default: str) -> dict[str, str] | None:
    """The output the default sink plays on, as {"id", "name"}: the id stays the same for the same
    device and port (a Bluetooth sink's name carries its address), the name is for people."""
    sink = next((s for s in sinks if s.get("name") == default), None)
    if not sink:
        return None
    props = sink.get("properties") or {}
    port = sink.get("active_port") or ""
    port_name = next((p.get("description") for p in sink.get("ports") or [] if p.get("name") == port), None)
    device = props.get("device.description") or sink.get("description") or default
    if props.get("device.api") == "bluez5" or props.get("device.bus") == "bluetooth":
        name = device  # the headphones' or speaker's own name
    elif "speaker" in port:
        name = "Speakers"
    elif "headphone" in port:
        name = "Headphones"
    else:
        name = port_name or device
    return {"id": f"{default}#{port}" if port else default, "name": name}


class OutputWatcher:
    """Follows the output in use and calls `on_change` with its description whenever it changes."""

    def __init__(self, on_change: Callable[[dict[str, str] | None], Awaitable[None]], logger: Any):
        self.on_change = on_change
        self.logger = logger
        self.current: dict[str, str] | None = None
        self.task: asyncio.Task | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.pending: asyncio.TimerHandle | None = None
        self.pactl = shutil.which("pactl")
        self.env = dict(os.environ)

    def start(self) -> None:
        if winio.WINDOWS and not self.task:
            self.task = asyncio.create_task(self._poll_windows())
            return
        if self.pactl and not self.task:
            # the plugin runs as the user, but not always with the session's environment
            self.env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            self.task = asyncio.create_task(self._follow())

    async def stop(self) -> None:
        if self.pending:
            self.pending.cancel()
        if self.process and self.process.returncode is None:
            self.process.kill()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None

    async def _follow(self) -> None:
        while True:
            try:
                self.process = await asyncio.create_subprocess_exec(
                    self.pactl, "subscribe", stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, env=self.env)
                await self.refresh()
                loop = asyncio.get_running_loop()
                assert self.process.stdout
                async for line in self.process.stdout:
                    # "Event 'change' on sink #55", "... on server" (the default sink changing), "... on card"
                    if any(word in line for word in (b" sink ", b" server", b" card ")):
                        if self.pending:
                            self.pending.cancel()
                        self.pending = loop.call_later(SETTLE_SECONDS, lambda: spawn(self.refresh(), "audio output re-read"))
                await self.process.wait()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.warning(f"Couldn't follow the audio output: {e}")
            await asyncio.sleep(RESTART_SECONDS)

    async def _poll_windows(self) -> None:
        while True:
            try:
                output = await asyncio.to_thread(winio.default_audio_output)
                if output != self.current:
                    self.current = output
                    await self.on_change(output)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.warning(f"Couldn't read the audio output: {e}")
            await asyncio.sleep(POLL_SECONDS)

    async def refresh(self) -> None:
        try:
            sinks = json.loads(await self._pactl("-f", "json", "list", "sinks") or "[]")
            default = (await self._pactl("get-default-sink")).strip()
            output = describe(sinks, default)
        except Exception as e:
            self.logger.warning(f"Couldn't read the audio output: {e}")
            return
        if output != self.current:
            self.current = output
            await self.on_change(output)

    async def _pactl(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            self.pactl, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=self.env)
        out, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        return out.decode(errors="replace")
