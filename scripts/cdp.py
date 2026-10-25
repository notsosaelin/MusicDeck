"""
Evaluate JavaScript in one of Steam's UI contexts, through its Chrome DevTools debugger. Handy for
inspecting and driving MusicDeck on a device (see "Debugging" in docs/DEVELOPMENT.md).

    ssh -N -L 18090:localhost:8080 deck@steamdeck.local &      # tunnel to Steam's debugger
    python scripts/cdp.py SharedJSContext "window.__musicdeck.player.state.get()"
    python scripts/cdp.py QuickAccess "1" --shot qam.png       # screenshot the Quick Access Menu
    python scripts/cdp.py SharedJSContext path/to/script.js    # run a file; may be an async IIFE
    python scripts/cdp.py MusicDeck "1" --hover 150,150 --shot mini.png   # with the mouse over it

Targets (by title): SharedJSContext (where MusicDeck runs), QuickAccess_* (the QAM), "Steam Big
Picture Mode" (the main window, where dialogs render). Needs `pip install websockets` and Decky's
CEF remote debugging enabled.
"""
import asyncio
import base64
import json
import os
import sys
import urllib.request

import websockets

BASE = os.environ.get("MUSICDECK_CDP", "http://127.0.0.1:18090")


async def main() -> None:
    title, code = sys.argv[1], sys.argv[2]
    if os.path.isfile(code):
        with open(code, encoding="utf-8") as f:
            code = f.read()
    targets = json.load(urllib.request.urlopen(BASE + "/json"))
    # the target of exactly that title first ("Steam", desktop Steam's main window, is in "Steam Root Menu" too)
    target = next((t for t in targets if t["title"] == title), None) or next(t for t in targets if title in t["title"])
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=2**26) as ws:
        ids = iter(range(1, 1_000_000))

        async def call(method: str, **params):
            msg_id = next(ids)
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == msg_id:
                    return msg

        result = await call("Runtime.evaluate", expression=code, awaitPromise=True, returnByValue=True)
        inner = result.get("result", {})
        if "exceptionDetails" in inner:
            print("EXCEPTION:", json.dumps(inner["exceptionDetails"], indent=1))
        else:
            print(json.dumps(inner.get("result", {}).get("value"), indent=1))
        if "--hover" in sys.argv:  # the mouse moved to x,y first (CSS :hover), as a person's would be
            x, y = (float(v) for v in sys.argv[sys.argv.index("--hover") + 1].split(","))
            await call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            await asyncio.sleep(0.4)
        if "--shot" in sys.argv:
            out = sys.argv[sys.argv.index("--shot") + 1]
            shot = await call("Page.captureScreenshot", format="png")
            with open(out, "wb") as f:
                f.write(base64.b64decode(shot["result"]["data"]))
            print("screenshot ->", out)


asyncio.run(main())
