"""
Copy a development build to a device and hot-reload the plugin, without packaging or reinstalling.

    MUSICDECK_DEVICE=deck@steamdeck.local python scripts/deploy.py            # frontend only
    MUSICDECK_DEVICE=deck@steamdeck.local python scripts/deploy.py --backend  # + Python and bin/

Every build put on a device gets a version of its own: run scripts/bump.py first (package.json goes
along, so Decky shows it). Reloading restarts the player, so music that's playing is paused first.

Needs: the plugin installed once from the zip (so ~/homebrew/plugins/MusicDeck exists), SSH key
login to the device (`ssh-copy-id`), `pnpm build` run first, and for the reload, Decky's CEF
remote debugging enabled (Decky settings → Developer) plus `pip install websockets`. Without those
it still copies the files; reload from Decky's developer settings instead.

The plugin folder is owned by root, but its py_modules/ and dist/ are writable by the user Decky
installs as, which is all this needs. Binaries (spotify-helper, qjs) for the device's CPU go to the
plugin's data folder (bin/<platform>/), where MusicDeck also looks for them.
"""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TUNNEL_PORT = 18090  # local end of the SSH tunnel to Steam's CEF debugger (port 8080 on the device)


def ssh(device: str, command: str) -> str:
    return subprocess.run(["ssh", device, command], check=True, capture_output=True, text=True).stdout


def scp(device: str, local: pathlib.Path, remote: str) -> None:
    subprocess.run(["scp", "-q", str(local), f"{device}:{remote}"], check=True)


def upload(device: str, backend: bool) -> None:
    home = ssh(device, "echo $HOME").strip()
    plugin, data = f"{home}/homebrew/plugins/MusicDeck", f"{home}/homebrew/data/MusicDeck"
    scp(device, ROOT / "dist" / "index.js", f"{plugin}/dist/index.js")
    scp(device, ROOT / "package.json", f"{plugin}/package.json")  # the version Decky and the backend show
    print("frontend copied")
    if not backend:
        return
    with tempfile.TemporaryDirectory() as tmp:
        # one archive instead of ~1000 small files (yt-dlp's extractors)
        archive = pathlib.Path(tmp) / "backend.tgz"
        with tarfile.open(archive, "w:gz") as tar:
            skip = lambda info: None if "__pycache__" in info.name else info  # noqa: E731
            tar.add(ROOT / "py_modules", arcname="py_modules", filter=skip)
            tar.add(ROOT / "main.py", arcname="main.py")
        ssh(device, f"mkdir -p {data}/bin")
        scp(device, archive, f"{data}/backend.tgz")
    # a running helper keeps its binary busy, and the old backend must not see half-copied files
    ssh(device, f"pkill -x spotify-helper; cd {plugin} && find py_modules -mindepth 1 -delete; "
                f"tar xzf {data}/backend.tgz --overwrite -C {plugin} && rm {data}/backend.tgz")
    # the helpers built for the device's CPU (bin/<platform>/, see musicdeck/native.py); copies from
    # before per-platform folders go
    platform_name = "linux-arm64" if ssh(device, "uname -m").strip() in ("aarch64", "arm64") else "linux-x86_64"
    ssh(device, f"mkdir -p {data}/bin/{platform_name} && rm -f {data}/bin/qjs {data}/bin/spotify-helper")
    for binary in (ROOT / "bin" / platform_name).glob("*"):
        # copied beside it and renamed over it: a running helper (musicdeck-net, restarted as soon as it's
        # stopped) keeps its file busy, and a rename doesn't mind
        target = f"{data}/bin/{platform_name}/{binary.name}"
        scp(device, binary, f"{target}.new")
        ssh(device, f"chmod 755 {target}.new && mv -f {target}.new {target}")
    print(f"backend copied ({platform_name})")


async def reload(device: str, backend: bool) -> None:
    import websockets  # only needed for the reload

    tunnel = subprocess.Popen(["ssh", "-N", "-L", f"{TUNNEL_PORT}:localhost:8080", device])
    try:
        for _ in range(30):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_PORT}/json", timeout=2))
                break
            except OSError:
                await asyncio.sleep(0.5)
        else:
            raise SystemExit("Steam's debugger isn't reachable; enable CEF remote debugging in Decky")
        target = next(t for t in targets if t["title"] == "SharedJSContext")
        version = json.loads((ROOT / "package.json").read_text())["version"]
        # the reload restarts the player: pause what's playing first (it picks up from there), rather
        # than cut it off mid-song
        js = ("(async () => { const p = window.__musicdeck?.player; "
              "if (p?.state.get().playing) { await p.fadeOutAndPause(1); } ")
        if backend:
            # restarting the backend makes Decky import the frontend itself; importing it again on top
            # loads the plugin twice in a row
            js += "await DeckyBackend.call('loader/reload_plugin', 'MusicDeck'); return 'reloaded'; })()"
        else:
            js += f"await DeckyPluginLoader.importPlugin('MusicDeck', '{version}'); return 'reloaded'; }})()"
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=2**24) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                      "params": {"expression": js, "awaitPromise": True, "returnByValue": True}}))
            print(json.loads(await ws.recv())["result"]["result"].get("value"))
    finally:
        tunnel.terminate()


if __name__ == "__main__":
    device = os.environ.get("MUSICDECK_DEVICE")
    if not device:
        raise SystemExit("Set MUSICDECK_DEVICE=user@host (SSH key login to the device)")
    with_backend = "--backend" in sys.argv
    upload(device, with_backend)
    if "--no-reload" not in sys.argv:
        asyncio.run(reload(device, with_backend))
