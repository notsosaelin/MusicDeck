"""
Spotify Soloist: Spotify's own headless player, for the accounts librespot can't get audio for.

Some Spotify accounts (newer ones) are refused the audio keys librespot asks for ("audio key error"), while
everything else librespot does for them (sign-in, the session's catalogue reads) works. Their audio comes from
Soloist instead (https://developer.spotify.com/documentation/soloist): Spotify's own client without a window,
which shows up in Spotify's apps as a Connect device and is driven over a WebSocket. It plays to the system's
audio itself (PipeWire or PulseAudio, WASAPI), not into Steam's browser, so the player's EQ, levelling,
crossfade and casting don't apply to what it plays (src/player.ts gives it a deck of its own, as casting has).

- It isn't ours to ship ("Do not redistribute"): it's fetched from Spotify's servers on first use, into the data
  folder, and fetched again before it runs out (a build stops working 90 days after it's built: exit code 10).
- Each user needs their own API key (developer.spotify.com/dashboard/soloist, with Premium), stored in the
  account's data folder with `soloist setup-api-key` rather than passed on the command line.
- It signs in through Spotify Connect: once, the user picks it under Devices in a Spotify app on the same
  network; it keeps the session in its data folder after that.
- Its WebSocket has no authentication at all, so it listens on 127.0.0.1 only, on a port the system picks.
- It's given one track at a time (the player keeps the queue). When one ends, Spotify's autoplay starts
  something else: that's paused at once and reported as the end of the track.

Reports, as casting's (cast/__init__.py): {"state": "playing"|"paused"|"buffering"|"idle", "position",
"ended"?, "error"?, "volume"?}.
"""
import asyncio
import collections
import datetime
import json
import os
import re
import shutil
import signal
import socket
import time
from typing import Any, Callable, Optional

import aiohttp

from . import debuglog, native
from .services.base import ServiceError

BUILDS = "https://soloist-builds.spotifycdn.com/"
ARCHIVES = {
    "linux-x86_64": "soloist_release_x86_64.tar.gz",
    "linux-arm64": "soloist_release_arm64.tar.gz",
    "windows-x86_64": "soloist_release_windows_x86_64.zip",
    # Windows on Arm runs x86_64 programs (native._candidates)
    "windows-arm64": "soloist_release_windows_x86_64.zip",
}
DASHBOARD = "https://developer.spotify.com/dashboard/soloist"
# builds stop working 90 days after they're built; a new one is fetched well before
REFRESH_DAYS = 75
EXIT_EXPIRED = 10
DOWNLOAD_LIMIT = 200 * 1024 * 1024
# starting up (the WebSocket's port written), restoring a stored session, a track starting
START_SECONDS = 20
LOGIN_SECONDS = 10
PLAY_SECONDS = 10
# another track coming on this close to the end of ours means ours ended (autoplay, or queued on the phone)
END_MARGIN = 6
# after a track starts, this long for its volume to settle on the player's (see Soloist.hold_volume_until)
VOLUME_HOLD_SECONDS = 3.0
OUTPUT_KEPT = 200

_install_lock = asyncio.Lock()


def archive_name(platform_name: Optional[str] = None) -> Optional[str]:
    return ARCHIVES.get(platform_name or native.platform_id())


def supported() -> bool:
    return archive_name() is not None


def executable(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "soloist", "bin", "soloist.exe" if native.WINDOWS else "soloist")


def build_date(version: str) -> Optional[datetime.date]:
    """The build's date in `soloist --version`: "soloist 1.4.0.3 build 1795197722 (20261120) (g...) (linux/x86_64)"."""
    match = re.search(r"\((\d{8})\)", version or "")
    if not match:
        return None
    try:
        return datetime.datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _stale(info: dict[str, Any], today: Optional[datetime.date] = None) -> bool:
    today = today or datetime.date.today()
    built = build_date(info.get("version") or "")
    if built:
        return (today - built).days >= REFRESH_DAYS
    # no build date to go by: by when it was fetched
    fetched = info.get("fetched")
    return not isinstance(fetched, (int, float)) or time.time() - fetched > REFRESH_DAYS * 86400


def _info_path(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "soloist", "bin", "build.json")


def _read_info(runtime_dir: str) -> Optional[dict[str, Any]]:
    try:
        with open(_info_path(runtime_dir), encoding="utf-8") as f:
            info = json.load(f)
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


def _extract(archive: str, target: str, member: str, zipped: bool) -> None:
    """Writes the one program in Spotify's archive to `target` (nothing else in it, wherever its paths point)."""
    try:
        _extract_member(archive, target, member, zipped)
    except ServiceError:
        raise
    except Exception as e:  # tarfile.ReadError, zipfile.BadZipFile, a short read...
        raise ServiceError(f"Spotify's Soloist download is damaged ({type(e).__name__})") from None


def _extract_member(archive: str, target: str, member: str, zipped: bool) -> None:
    if zipped:
        import zipfile
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if not entry.is_dir() and os.path.basename(entry.filename) == member:
                    with z.open(entry) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return
    else:
        import tarfile
        with tarfile.open(archive, "r:gz") as t:
            for entry in t.getmembers():
                if entry.isfile() and os.path.basename(entry.name) == member:
                    src = t.extractfile(entry)
                    if src is None:
                        break
                    with src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return
    raise ServiceError("Spotify's Soloist download had no program in it")


async def _version(exe: str) -> str:
    env = _environment()
    proc = await asyncio.create_subprocess_exec(exe, "--version", stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, env=env, **native.NO_WINDOW)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
    except asyncio.TimeoutError:
        proc.kill()
        raise ServiceError("Spotify Soloist didn't start")
    return out.decode("utf-8", "replace").strip()


async def install(runtime_dir: str, http: aiohttp.ClientSession, force: bool = False,
                  builds: str = BUILDS) -> str:
    """Soloist's program, fetched from Spotify if it isn't here, is getting old, or (`force`) has run out."""
    async with _install_lock:
        exe = executable(runtime_dir)
        info = _read_info(runtime_dir)
        have = os.path.isfile(exe)
        if have and info and not force and not _stale(info):
            return exe
        name = archive_name()
        if not name:
            raise ServiceError(f"Spotify Soloist isn't made for this system ({native.platform_id()})")
        folder = os.path.dirname(exe)
        os.makedirs(folder, exist_ok=True)
        archive = os.path.join(folder, name + ".part")
        fresh = exe + ".new"
        try:
            async with http.get(builds + name, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                if resp.status != 200:
                    raise ServiceError(f"Spotify's server answered {resp.status}")
                size = 0
                with open(archive, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1 << 16):
                        size += len(chunk)
                        if size > DOWNLOAD_LIMIT:
                            raise ServiceError("The download is far bigger than Soloist should be")
                        f.write(chunk)
            await asyncio.to_thread(_extract, archive, fresh, os.path.basename(exe), name.endswith(".zip"))
            if not native.WINDOWS:
                os.chmod(fresh, 0o755)
            version = await _version(fresh)
            if not version.lower().startswith("soloist"):
                raise ServiceError("What Spotify sent isn't Soloist")
            try:
                os.replace(fresh, exe)
            except PermissionError:
                # Windows: another account's copy is running from it; the old one does until it's restarted
                if have and not force:
                    return exe
                raise ServiceError("Spotify Soloist is in use; try again in a moment")
            with open(_info_path(runtime_dir), "w", encoding="utf-8") as f:
                json.dump({"version": version, "fetched": time.time()}, f)
            debuglog.debug("soloist", "installed", version=version)
            return exe
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ServiceError) as e:
            debuglog.debug("soloist", "install failed", error=repr(e))
            if have and not force:
                return exe  # the copy here still works; try again next time
            if isinstance(e, ServiceError):
                raise ServiceError(f"Couldn't get Spotify Soloist: {e}") from None
            raise ServiceError("Couldn't download Spotify Soloist; check the internet connection") from None
        finally:
            for leftover in (archive, fresh):
                try:
                    os.remove(leftover)
                except OSError:
                    pass


def _environment() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)  # never Decky's bundled libraries for a system program
    if not native.WINDOWS:
        # the plugin runs as the user, but not always with the session's environment (PipeWire's socket is there)
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _parent_death_hook() -> Optional[Callable[[], None]]:
    """Linux: what the started Soloist runs first so the system ends it when this backend goes. Decky kills a
    plugin that's slow to stop (5 s), so stop() isn't always reached; left running, the old Soloist holds its data
    folder and the next one can't start ("cannot lock data directory"). Looked up here, not in the child."""
    if native.WINDOWS:
        return None
    try:
        import ctypes
        prctl = ctypes.CDLL(None, use_errno=True).prctl
    except Exception:  # not there: _end_leftover still clears one up next time
        return None

    def hook() -> None:
        try:
            prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
        except Exception:
            pass
    return hook


def _is_our_soloist(pid: int, data_dir: str) -> bool:
    """Process `pid` is a Soloist (a pid in an old file may be some other program's by now)."""
    if native.WINDOWS:
        import subprocess
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 timeout=10, **native.NO_WINDOW).stdout.decode("utf-8", "replace").lower()
        except (OSError, subprocess.SubprocessError):
            return False
        return '"soloist.exe"' in out
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = f.read().split(b"\0")
    except OSError:
        return False
    return bool(args) and os.path.basename(args[0]) == b"soloist" and os.fsencode(data_dir) in args


def device_name() -> str:
    """What it's called in Spotify's Devices list."""
    host = (socket.gethostname() or "").split(".")[0].strip()
    return f"MusicDeck ({host})" if host else "MusicDeck"


class TrackFailed(ServiceError):
    """This track wouldn't play; every other ServiceError here is about Soloist itself (not paired, not
    running, no key...), so the next track would fail the same way and the player stops instead of skipping."""


class Soloist:
    """One account's Soloist: the process, its WebSocket, and the one track it's playing for the player."""

    def __init__(self, runtime_dir: str, data_dir: str, http: Callable[[], aiohttp.ClientSession],
                 on_status: Callable[[dict[str, Any]], None], name: Optional[str] = None,
                 builds: str = BUILDS):
        self.runtime_dir = runtime_dir
        self.data_dir = data_dir
        self.http = http
        self.on_status = on_status
        self.name = name or device_name()
        self.builds = builds
        self.key = ""
        self.lock = asyncio.Lock()
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.tasks: set[asyncio.Task] = set()
        self.output: collections.deque[str] = collections.deque(maxlen=OUTPUT_KEPT)
        # "off", "installing", "starting", "pairing" (waiting for Spotify Connect), "ready", "error"
        self.phase = "off"
        self.problem = ""
        self.expired = False
        self.logged_in: Optional[bool] = None
        # the Spotify username it's signed in as (from its output), checked against the source's
        self.account: Optional[str] = None
        self.expected_account: Optional[str] = None
        self.active = False
        self.login_changed = asyncio.Event()
        # a track was refused for want of pairing: once it's paired, the player is told, and plays it on
        self.refused_unpaired = False
        # what it's playing, as it says
        self.current: Optional[str] = None
        self.state = "idle"
        self.pos_ms = 0.0
        self.pos_at_ms = 0.0
        self.speed = 0.0
        self.volume: Optional[int] = None
        # until then, a volume it reports isn't someone turning it: becoming Spotify's active device, it takes the
        # volume Spotify keeps for it (whatever a phone left it at), over --initial-volume and the player's own
        self.hold_volume_until = 0.0
        # what the player asked for: the track (URI), its length, where it's cued (not started yet)
        self.want: Optional[str] = None
        self.duration = 0.0
        self.cued: Optional[float] = None
        # ours, while the phone has put something else on (play here brings it back)
        self.taken: Optional[str] = None
        self.ours = False
        self.last_ours = 0.0
        self.level = 1.0
        self.generation = 0
        self.waiting: dict[str, list[asyncio.Future]] = {}
        self.last_sent: Optional[dict[str, Any]] = None

    # --- setup ---

    def set_key(self, key: str) -> None:
        self.key = (key or "").strip()

    def setup_status(self) -> dict[str, Any]:
        phase, problem = self.phase, self.problem
        if phase == "pairing" and not self._awaiting_pairing():
            phase = "starting"  # "not logged in" while it brings a stored session back: not a reason to pair it
        if phase == "ready" and self.other_account():
            phase, problem = "other_account", self._other_account_problem()
        return {"phase": phase, "device": self.name, "problem": problem, "dashboard": DASHBOARD}

    def other_account(self) -> bool:
        """It's paired with a different Spotify account from the source's."""
        return bool(self.logged_in and self.account and self.expected_account and self.account != self.expected_account)

    def _other_account_problem(self) -> str:
        return (f"Spotify Soloist is signed in to a different Spotify account. In Spotify, signed in as this "
                f"source's account, choose “{self.name}” under Devices")

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.returncode is None and self.ws and not self.ws.closed)

    async def ensure(self) -> None:
        """Running, its WebSocket open, and (if it has a stored session) signed in, or as far as it gets."""
        async with self.lock:
            if self.running:
                return
            await self._shutdown()
            if not self.key:
                self._set_phase("error", "Add a Spotify Soloist API key first")
                raise ServiceError(self.problem)
            try:
                self._set_phase("installing")
                exe = await install(self.runtime_dir, self.http(), force=self.expired, builds=self.builds)
                self.expired = False
                self._set_phase("starting")
                await self._end_leftover()
                await self._store_key(exe)
                port = await self._spawn(exe)
                await self._connect(port)
            except ServiceError as e:
                await self._shutdown()
                self._set_phase("error", str(e))
                raise
        # a stored session comes back within a moment; without one it waits for Spotify Connect
        await self._wait_login(LOGIN_SECONDS)

    async def _end_leftover(self) -> None:
        """A Soloist still running on this data folder from before (the backend killed before it could stop it,
        or another copy of MusicDeck): ended, or this one can't start."""
        try:
            with open(os.path.join(self.data_dir, "soloist.pid"), encoding="utf-8") as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return
        if pid <= 0 or pid == os.getpid() or not _is_our_soloist(pid, self.data_dir):
            return
        debuglog.debug("soloist", "ending one left running", pid=pid)
        for sig, seconds in ((signal.SIGTERM, 5.0), (getattr(signal, "SIGKILL", signal.SIGTERM), 3.0)):
            try:
                os.kill(pid, sig)
            except OSError:
                return
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                await asyncio.sleep(0.2)
                if not _is_our_soloist(pid, self.data_dir):
                    return
        raise ServiceError("An earlier Spotify Soloist is still running and won't stop")

    async def _store_key(self, exe: str) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            exe, "setup-api-key", "--data-dir", self.data_dir, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=_environment(), **native.NO_WINDOW)
        try:
            out, _ = await asyncio.wait_for(proc.communicate((self.key + "\n").encode()), 20)
        except asyncio.TimeoutError:
            proc.kill()
            raise ServiceError("Spotify Soloist didn't take the API key")
        if proc.returncode != 0:
            debuglog.debug("soloist", "setup-api-key failed", code=proc.returncode,
                           out=out.decode("utf-8", "replace")[-300:])
            raise ServiceError("Spotify Soloist didn't take the API key")

    async def _spawn(self, exe: str) -> int:
        port_file = os.path.join(self.data_dir, "ws.port")
        try:
            os.remove(port_file)
        except OSError:
            pass
        volume = self.volume if self.volume is not None else round(self.level * 100)
        self.output.clear()
        self.proc = await asyncio.create_subprocess_exec(
            exe, "--device-name", self.name, "--data-dir", self.data_dir,
            # its signed-in session lives in its cache folder (Users/<name>-user, client_token), not with its
            # data: kept in the account's folder, never somewhere temporary, or it'd have to be paired again
            "--cache-dir", os.path.join(self.data_dir, "cache"), "--cache-size", "300",
            "--initial-volume", str(max(0, min(100, volume))), "--ws", "127.0.0.1:0",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=_environment(), **native.NO_WINDOW,
            **({"preexec_fn": hook} if (hook := _parent_death_hook()) else {}))
        self._spawn_task(self._follow(self.proc))
        debuglog.debug("soloist", "started", pid=self.proc.pid)
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            if self.proc.returncode is not None:
                raise ServiceError(self._exit_problem(self.proc.returncode))
            try:
                with open(port_file, encoding="utf-8") as f:
                    text = f.read().strip()
                if text.isdigit() and int(text) > 0:
                    return int(text)
            except OSError:
                pass
            await asyncio.sleep(0.1)
        raise ServiceError("Spotify Soloist didn't start")

    def _exit_problem(self, code: int) -> str:
        if code == EXIT_EXPIRED:
            self.expired = True
            return "This copy of Spotify Soloist has run out; MusicDeck fetches a new one next time"
        tail = " ".join(line for line in list(self.output)[-3:])
        debuglog.debug("soloist", "exited", code=code, output=tail[-500:])
        return f"Spotify Soloist stopped (exit code {code})"

    async def _follow(self, proc: asyncio.subprocess.Process) -> None:
        """Keeps the end of its output (for problems), and notices it stopping."""
        assert proc.stdout
        while True:
            try:
                line = await proc.stdout.readline()
            except (ValueError, OSError):
                continue
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip()
            self.output.append(text)
            # "2026-11-20 20:32:28.596: logged in as <username>": whose account it plays as
            signed = re.search(r"logged in as (\S+)", text)
            if signed:
                self.account = signed.group(1)
                self.logged_in = True
                self._set_phase("ready")
                self.login_changed.set()
                self._paired_now()
            elif "waiting for login" in text.lower():
                self.login_changed.set()
            if " E [" in text or "error" in text.lower():
                debuglog.debug("soloist", "said", line=text[-300:])
        code = await proc.wait()
        if proc is not self.proc:
            return
        problem = self._exit_problem(code)
        self._set_phase("error", problem)
        self.logged_in = None
        self.login_changed.set()
        if self.want and (self.ours or self.cued is None):
            self.want, self.ours = None, False
            self._send_status({"state": "idle", "error": problem})

    async def _connect(self, port: int) -> None:
        try:
            # (no ClientWSTimeout: Decky bundles its own aiohttp, older than that)
            self.ws = await asyncio.wait_for(
                self.http().ws_connect(f"ws://127.0.0.1:{port}", max_msg_size=8 * 1024 * 1024), 10)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            raise ServiceError(f"Couldn't reach Spotify Soloist: {e}") from None
        self._spawn_task(self._read(self.ws))

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    message = json.loads(msg.data)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self.on_message(message)
        except (aiohttp.ClientError, OSError) as e:
            debuglog.debug("soloist", "websocket failed", error=repr(e))

    async def _wait_login(self, seconds: float) -> None:
        """Until it says it's signed in, or that it's waiting for Spotify Connect (no session to bring back). Its
        WebSocket says "not logged in" while it's still bringing a stored session back, so that alone isn't it."""
        deadline = time.monotonic() + seconds
        while self.running and not self.logged_in:
            if self._awaiting_pairing():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.login_changed.clear()
            await self._quietly("get_auth_state")  # it may not announce a login it finishes later
            try:
                await asyncio.wait_for(self.login_changed.wait(), min(remaining, 0.5))
            except asyncio.TimeoutError:
                pass

    async def refresh_login(self) -> None:
        """Asks again whether it's signed in (the settings dialog, while the user pairs it)."""
        if self.running:
            self.login_changed.clear()
            await self._quietly("get_auth_state")
            try:
                await asyncio.wait_for(self.login_changed.wait(), 1)
            except asyncio.TimeoutError:
                pass

    def _awaiting_pairing(self) -> bool:
        """It has said it has no session and waits for Spotify Connect: "No session to restore" (with -v), "waiting
        for login — connect to ... from your Spotify app". (A stored session: "restoring session", "logged in as".)"""
        lines = [line.lower() for line in self.output]
        return any("no session to restore" in line or "waiting for login" in line for line in lines)

    def _set_phase(self, phase: str, problem: str = "") -> None:
        self.phase, self.problem = phase, problem

    async def stop(self) -> None:
        async with self.lock:
            await self._shutdown()
            self._set_phase("off")
        for task in list(self.tasks):
            task.cancel()

    async def _shutdown(self) -> None:
        ws, proc = self.ws, self.proc
        self.ws, self.proc = None, None
        if ws and not ws.closed:
            await ws.close()
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        self.logged_in = None
        self.account = None
        self.current, self.ours = None, False

    def _spawn_task(self, work) -> None:
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # --- talking to it ---

    async def send(self, command: str, **args: Any) -> None:
        ws = self.ws
        if not ws or ws.closed:
            raise ServiceError("Spotify Soloist isn't running")
        try:
            await ws.send_json({"type": "command", "command": command, **args})
        except (aiohttp.ClientError, OSError, RuntimeError) as e:
            raise ServiceError(f"Spotify Soloist didn't answer: {e}") from None

    def on_message(self, m: dict[str, Any]) -> None:
        kind = m.get("type")
        if kind == "auth_state":
            self.logged_in = bool(m.get("logged_in"))
            self.active = bool(m.get("is_active"))
            self._set_phase("ready" if self.logged_in else "pairing")
            self.login_changed.set()
            if self.logged_in:
                self._paired_now()
            return
        if kind == "error":
            debuglog.debug("soloist", "error", message=str(m.get("message"))[:300])
            return
        if kind in ("command_result", "queue_changed", "context_changed", "options_changed"):
            return
        if kind in ("playback_state", "track_changed") and isinstance(m.get("item"), dict):
            # before its position: that's the new track's, and ours ended where it last was
            self._item(m["item"].get("uri") or None)
        if "is_active" in m:
            self.active = bool(m["is_active"])
        if isinstance(m.get("status"), str):
            self.state = m["status"]
        if isinstance(m.get("position"), dict):
            self._position(m["position"])
        if isinstance(m.get("volume"), (int, float)):
            self.volume = int(m["volume"])
            wanted = round(self.level * 100)
            if time.monotonic() < self.hold_volume_until and abs(self.volume - wanted) > 1:
                debuglog.debug("soloist", "volume put back", said=self.volume, wanted=wanted)
                self.volume = wanted
                self._spawn_task(self._quietly("set_volume", volume=wanted))
        if kind == "device_changed":
            debuglog.debug("soloist", "device changed", message=json.dumps(m)[:300])
        self._report(volume_only=kind == "volume_changed")

    def _position(self, p: dict[str, Any]) -> None:
        if isinstance(p.get("position_ms"), (int, float)):
            self.pos_ms = float(p["position_ms"])
            self.pos_at_ms = float(p["timestamp_ms"]) if isinstance(p.get("timestamp_ms"), (int, float)) else time.time() * 1000
            self.speed = float(p["speed"]) if isinstance(p.get("speed"), (int, float)) else 0.0

    def position(self) -> float:
        ms = self.pos_ms
        if self.state == "playing" and self.speed:
            ms += max(0.0, time.time() * 1000 - self.pos_at_ms) * self.speed
        return max(0.0, ms / 1000)

    def _item(self, uri: Optional[str]) -> None:
        self.current = uri
        for future in self.waiting.pop(uri or "", []):
            if not future.done():
                future.set_result(True)
        if not self.want or uri == self.want:
            if self.want and uri == self.want and not self.ours:
                # ours has come on: what's known of state and position until it says is the track before's
                self.ours = True
                self.state, self.pos_ms, self.pos_at_ms, self.speed = "buffering", 0.0, time.time() * 1000, 0.0
            return
        if not self.ours:
            return  # still the one before ours
        # something else came on: ours ended (autoplay, or the phone's queue), or the phone took over
        ended = not self.duration or self.last_ours >= self.duration - END_MARGIN
        debuglog.debug("soloist", "track replaced", ended=ended, at=round(self.last_ours, 1), now=uri)
        want, self.want, self.ours = self.want, None, False
        if ended:
            self._spawn_task(self._quietly("pause"))  # autoplay's choice isn't heard
            self._send_status({"state": "idle", "ended": True, "position": self.duration})
        else:
            # what the phone chose plays on; play here brings ours back, from where it was
            self.taken = want
            self._send_status({"state": "paused", "position": self.last_ours})

    async def _quietly(self, command: str, **args: Any) -> None:
        try:
            await self.send(command, **args)
        except ServiceError:
            pass

    def _report(self, volume_only: bool = False) -> None:
        status: dict[str, Any] = {}
        if self.volume is not None:
            status["volume"] = self.volume / 100
        if self.want and self.ours and not volume_only:
            self.last_ours = self.position()
            state = self.state if self.state in ("playing", "paused", "buffering", "idle") else "buffering"
            at_end = self.last_ours >= self.duration - END_MARGIN if self.duration else self.last_ours > 0
            if state == "idle" and at_end:
                # it stopped at the end without starting anything else (autoplay off)
                self.want, self.ours = None, False
                status.update({"state": "idle", "ended": True, "position": self.duration or self.last_ours})
            elif state == "idle":
                status.update({"state": "paused", "position": self.last_ours})
            else:
                status.update({"state": state, "position": self.last_ours})
        if list(status) == ["volume"] and self.last_sent is not None and self.last_sent.get("volume") == status["volume"]:
            return  # its answer to a volume the player set: nothing new
        if status:
            self._send_status(status)

    def _paired_now(self) -> None:
        if self.refused_unpaired:
            self.refused_unpaired = False
            debuglog.debug("soloist", "paired; the player plays on")
            self._send_status({"paired": True})

    def _send_status(self, status: dict[str, Any]) -> None:
        # it sends two or three messages per change (state, playback state, position): pass on what's new
        last = self.last_sent
        if last is not None and not status.get("ended") and not status.get("error") and \
                {k: v for k, v in status.items() if k != "position"} == {k: v for k, v in last.items() if k != "position"} and \
                abs(float(status.get("position", 0)) - float(last.get("position", 0))) < 0.5:
            return
        self.last_sent = status
        try:
            self.on_status(status)
        except Exception as e:  # a report that can't be sent mustn't stop the reader
            debuglog.debug("soloist", "report failed", error=repr(e))

    # --- the player's track ---

    async def load(self, track_id: str, start: float, autoplay: bool, duration: float) -> None:
        """Plays (or, without `autoplay`, cues until play) a track from `start` seconds."""
        self.generation += 1
        self.last_sent = None
        self.taken = None
        self.want = "spotify:track:" + track_id
        self.ours = False
        self.duration = max(0.0, float(duration or 0))
        self.last_ours = start
        if not autoplay:
            self.cued = start
            self._send_status({"state": "paused", "position": start})
            if self.running and self.state == "playing":
                await self._quietly("pause")
            return
        self.cued = None
        await self._play(self.want, start, self.generation)

    async def _play(self, uri: str, start: float, generation: int) -> None:
        await self.ensure()
        if not self.logged_in:
            await self._wait_login(LOGIN_SECONDS)  # started a moment ago, still bringing its session back
        if not self.logged_in:
            self.refused_unpaired = True
            raise ServiceError(f"Pair Spotify Soloist first: in Spotify on your phone, choose “{self.name}” under Devices")
        if self.other_account():
            raise ServiceError(self._other_account_problem())
        if generation != self.generation:
            return
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.waiting.setdefault(uri, []).append(future)
        self.current = None
        self.hold_volume_until = time.monotonic() + PLAY_SECONDS + VOLUME_HOLD_SECONDS
        if not self.active:
            await self.send("activate")
        await self.send("play", uri=uri)
        try:
            await asyncio.wait_for(future, PLAY_SECONDS)
        except asyncio.TimeoutError:
            if generation == self.generation:
                self.want, self.ours = None, False
                raise TrackFailed("Spotify Soloist couldn't play this track") from None
            return
        finally:
            futures = self.waiting.get(uri)
            if futures and future in futures:
                futures.remove(future)
        if generation == self.generation and start > 0.5:
            await self.send("seek", position_ms=int(start * 1000))
        # the player's volume, once it's playing (see hold_volume_until)
        self.hold_volume_until = time.monotonic() + VOLUME_HOLD_SECONDS
        await self._quietly("set_volume", volume=round(self.level * 100))

    async def control(self, action: str, value: Optional[float] = None) -> None:
        """play, pause, stop, seek (seconds) or volume (0..1)."""
        if action == "volume":
            self.level = max(0.0, min(1.0, float(value or 0)))
            if self.running:
                await self.send("set_volume", volume=round(self.level * 100))
            return
        if action == "stop":
            self.want, self.cued, self.taken = None, None, None
            if self.ours and self.running:
                await self._quietly("pause")
            self.ours = False
            return
        if not self.want and self.taken:
            # the phone took it over: seeking moves where ours comes back; play brings it back
            if action == "seek":
                self.last_ours = max(0.0, float(value or 0))
            elif action == "play":
                self.want, self.taken = self.taken, None
                await self._play(self.want, self.last_ours, self.generation)
            return
        if not self.want:
            return
        if action == "seek":
            seconds = max(0.0, float(value or 0))
            self.last_ours = seconds
            if self.cued is not None:
                self.cued = seconds  # starts there when played
            elif self.ours and self.running:
                await self.send("seek", position_ms=int(seconds * 1000))
        elif action == "pause":
            if self.ours and self.running:
                await self.send("pause")
        elif action == "play":
            if self.cued is not None:
                start, self.cued = self.cued, None
                await self._play(self.want, start, self.generation)
            elif self.ours and self.running and self.current == self.want:
                await self.send("play")
            else:
                # it was stopped, or something else took it over: start ours again where it was
                await self._play(self.want, self.last_ours, self.generation)
        else:
            raise ServiceError("Unknown control")
