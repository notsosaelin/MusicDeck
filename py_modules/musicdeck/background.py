"""Work started in the background (a play report, a re-read after a change) that nothing awaits.

asyncio keeps only a weak reference to a task: one nobody holds can be garbage-collected before it
finishes, and its exception is only ever logged as "Task exception was never retrieved". spawn()
holds each task until it's done and logs what it raised."""
import asyncio
import logging
from typing import Any, Coroutine

_running: set[asyncio.Task] = set()
_log = logging.getLogger("musicdeck")


def spawn(work: Coroutine[Any, Any, Any], what: str) -> asyncio.Task:
    task = asyncio.ensure_future(work)
    _running.add(task)

    def done(t: asyncio.Task) -> None:
        _running.discard(t)
        if not t.cancelled() and t.exception() is not None:
            _log.warning(f"{what} failed: {t.exception()!r}")

    task.add_done_callback(done)
    return task


async def cancel_all() -> None:
    """At unload: stop what's still running, so nothing outlives the plugin."""
    tasks = list(_running)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
