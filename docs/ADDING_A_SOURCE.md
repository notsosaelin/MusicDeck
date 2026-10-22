# Adding a music source

A source is one Python class plus a few small frontend entries. The UI is generic: it renders
whatever Pages and Sections your service returns, and only offers features your service declares.
This guide builds a complete example, step by step. Read [ARCHITECTURE.md](ARCHITECTURE.md) first
if you haven't.

**Before you start, check the source is possible.** It must give out audio that Steam's browser
engine can play *without DRM*: MP3, AAC, FLAC, Ogg Vorbis/Opus, WAV, or HLS/DASH of those, over
HTTP(S). If every stream is Widevine/FairPlay-protected (Apple Music, Deezer, Amazon Music), it
can't be supported; don't work around that. See [SOURCES.md](SOURCES.md#not-possible).

## 1. The service class

Create `py_modules/musicdeck/services/example.py`:

```python
"""
Example Music, through its public REST API. No account needed.

Say here, in two or three sentences: how it signs in (if at all), where streams come from, and
anything surprising. This docstring is the first thing the next maintainer reads.
"""
import ssl
from typing import Any, Optional

import aiohttp
import certifi

from .. import models
from .base import MusicService, ServiceError, StreamSource

API = "https://api.example.com/v1"


class ExampleService(MusicService):
    """config: {"region"}   <- list every key you store in config, and what it is"""

    type = "example"               # stable id: stored in settings, used by the frontend
    display_name = "Example Music"  # shown in "Add a source"
    # Only what you implement. The UI hides the rest (no heart without "star", etc.)
    features = frozenset({"star", "radio", "lyrics"})

    def __init__(self, instance_id: str, config: dict[str, Any], runtime_dir: str):
        super().__init__(instance_id, config, runtime_dir)
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        # Always verify TLS with certifi: Decky's Python has no reliable system CA store
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
            timeout=aiohttp.ClientTimeout(total=20))

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    async def _get(self, path: str, **params: Any) -> Any:
        assert self.session
        try:
            async with self.session.get(API + path, params=params) as resp:
                if resp.status == 404:
                    raise ServiceError("Not found on Example Music")
                if resp.status >= 400:
                    raise ServiceError(f"Example Music returned HTTP {resp.status}")
                return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            # ServiceError messages are shown to the user as-is: keep them short and plain
            raise ServiceError(f"Could not reach Example Music: {e}") from e

    # --- mapping: service JSON -> MusicDeck's model (models.py) ---

    @staticmethod
    def _track(t: dict[str, Any]) -> dict[str, Any]:
        return models.track(str(t["id"]), t["title"], t["artist"]["name"], t["album"]["title"],
                            t["duration_ms"] / 1000, art=t["album"]["cover_url"],
                            album_id=str(t["album"]["id"]), artist_id=str(t["artist"]["id"]))

    @staticmethod
    def _album(a: dict[str, Any]) -> dict[str, Any]:
        return models.collection(str(a["id"]), "album", a["title"], a["artist"]["name"], a["cover_url"])

    # --- browsing ---

    async def test(self) -> str:
        await self._get("/status")
        return "Connected to Example Music"

    async def home(self) -> list[dict[str, Any]]:
        charts = await self._get("/charts/albums")
        return [models.section("Top albums", [self._album(a) for a in charts["items"]])]

    async def open(self, kind: str, id: str) -> dict[str, Any]:
        if kind == "album":
            a = await self._get(f"/albums/{id}")
            c = self._album(a)
            return models.page(c["title"], c["subtitle"], c["art"], tracks=[self._track(t) for t in a["tracks"]])
        raise ServiceError(f"Unknown collection kind: {kind}")

    async def search(self, query: str) -> dict[str, Any]:
        r = await self._get("/search", q=query)
        return models.page(f"Results for “{query}”", collections=[self._album(a) for a in r["albums"]],
                           tracks=[self._track(t) for t in r["tracks"]], tracks_first=True)

    # --- media ---

    async def stream(self, track_id: str) -> StreamSource:
        info = await self._get(f"/tracks/{track_id}/stream")
        # expires: when the signed URL stops working; the server caches it until then
        return StreamSource(url=info["url"], expires=info["expires_at"])

    async def art(self, art_id: str) -> StreamSource:
        if not art_id.startswith("https://"):  # art ids come back from the UI: validate them
            raise ServiceError("Bad art id")
        return StreamSource(url=art_id)

    # --- features (one method per entry in `features`) ---

    async def star(self, track_id: str, starred: bool) -> None: ...
    async def radio(self, track: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def lyrics(self, track: dict[str, Any]) -> Optional[dict[str, Any]]: ...
```

### What each method must do

| Method | Required | Returns / does |
| --- | --- | --- |
| `prepare_config(config, previous)` | if the dialog has fields | Normalise what the dialog sent before saving. Called synchronously. Turn passwords into something non-reversible here, or in `authenticate`. |
| `authenticate(config, previous, runtime_dir)` | if signing in uses the dialog's fields | Async; exchange a typed password for a token, drop the password, keep the token when editing without retyping. Raise `ServiceError` to show a message in the dialog. |
| `sign_in(options, runtime_dir)` | if sign-in happens on a web page or with a code | Return a `SignIn` (see below). |
| `account(config)` | if it has accounts | A stable key for the signed-in account (user id, or server + user). Lets people add several accounts and stops the same one being added twice. |
| `remove()` | if it keeps files | Delete what it stored in `runtime_dir` when the user removes the source. |
| `start()` / `stop()` | yes | Create / close HTTP sessions, helper processes. Called when the source is created, edited or removed. |
| `test()` | recommended | A short status shown after saving ("Signed in as …"). Raise `ServiceError` if it doesn't work. |
| `home()` | yes | List of Sections for the Library tab. Put a `models.section("Library", [...list collections])` last if you have fixed lists like "Favourite tracks". |
| `open(kind, id)` | yes | A Page for a Collection the user selected. `kind` is whatever you put in the Collection. |
| `search(query)` | yes | A Page. Set `tracks_first=True` if songs are the best matches. A Page can also carry an `action` (header button, e.g. Steam's `steam://install/<appid>`) and a `notice`. |
| `stream(track_id)` | yes | A `StreamSource`; see below. |
| `art(art_id)` | if you return art ids | A `StreamSource` for the image. |
| `star`, `scrobble`, `lyrics`, `radio`, `user_playlists`, `add_to_playlist`, `save_queue`, `load_queue` | per `features` | See `base.py` for signatures and meaning. |

### StreamSource

```python
StreamSource(path="/music/a.flac")                   # a local file; served with byte ranges
StreamSource(url="https://cdn/x.mp3", expires=t)     # proxied with byte ranges; cached until t
StreamSource(url=..., headers={"Authorization": ...})  # headers added by the server, never seen by the UI
StreamSource(url="https://cdn/x.m3u8", hls=True)     # HLS: rewritten so segments come through the server
StreamSource(playlist="#EXTM3U...", hls=True)        # HLS you built yourself (e.g. from DASH)
StreamSource(..., gain=models.gain(-6.5))            # loudness learnt while resolving (ReplayGain dB)
```

Rules that keep playback reliable:

- **Set `expires`** for signed URLs, a little before they really expire. The server re-resolves
  after that, and also when the CDN answers 403.
- **Raise `ServiceError`** with a clear reason ("only a preview on your plan", "buyers only") when a
  track can't play. The player shows it and skips to the next track.
- Return **live streams** (radio) as tracks with `live=True` and duration 0: no seek bar, and the
  player reconnects instead of skipping when the stream drops.

### Configuration and secrets

- Everything in `self.config` is saved in `settings.json`. The frontend receives it too, **except**
  keys listed in `SECRET_KEYS` in `main.py` (`token`, `refresh_token`, `password`, …). If you store
  a secret under another name, add the name there.
- To persist a config change at runtime (a rotated refresh token, a favourite station), call
  `self.save_config(self.config)`. The plugin sets this attribute on every instance.
- Files belong in `self.runtime_dir` (`~/homebrew/data/MusicDeck`); name them with your type and
  `self.instance_id` (or the account), never one shared file per type: people can add the same
  service twice. Delete them in `remove()`.

### Signing in on the web or with a code

If the user signs in on the service's website (OAuth) or approves a code, implement a `SignIn`:

```python
class ExampleLogin(SignIn):
    async def start(self) -> None:
        self.url = "https://example.com/device?code=ABCD"   # or: self.code = "123456" to show it
        self.status = "waiting"
        self._task = asyncio.get_event_loop().create_task(self._wait())

    async def _wait(self) -> None:
        try:
            token = await poll_until_approved()
            self.result = {"refresh_token": token}   # merged into the source's config on save
            self.status = "done"
        except ServiceError as e:
            self.status, self.error = "error", str(e)

    async def close(self) -> None:
        if not self._task.done():
            self._task.cancel()

class ExampleService(MusicService):
    @classmethod
    def sign_in(cls, options, runtime_dir):
        return ExampleLogin()
```

`TidalLogin` (device code, polled), `QobuzLogin` (OAuth redirect to a loopback listener) and
`JellyfinQuickConnect` (a code shown in the dialog) are complete examples of the three common shapes.
`authenticate()` still runs on save; make it accept the config your `result` produced.

## 2. Register it

`py_modules/musicdeck/services/__init__.py`:

```python
from .example import ExampleService

SERVICE_TYPES = {
    ...,
    ExampleService.type: ExampleService,   # order here is the order in "Add a source"
}
```

## 3. Frontend

All in `src/components/`:

1. **Icon**: add `example: <SomeIcon />` to `TYPE_ICONS` in `Settings.tsx` (`react-icons/fa` or
   `react-icons/si` have most brands).
2. **Status line**: add a case to `describeStatus` in `Settings.tsx`, e.g. `"No account"` or the
   signed-in user. It appears under the source's name.
3. **Dialog fields** (skip if your source has no settings): add an `ExampleFields` component in
   `ServiceModal.tsx` next to the others and render it with `{type === "example" && <ExampleFields ... />}`.
   Use the local `Field` wrapper and Decky's `TextField`, `DropdownItem`, `ToggleField`.
   - A web sign-in button: call `signIn("example", draft)` from `signIn.ts` after closing the
     dialog, and add an entry to `PROVIDERS` there (display name, options, "Signed in as" text).
   - A code shown in the dialog: copy `JellyfinFields` (`signInStart` / `signInPoll`).

Nothing else in the UI needs to change: browsing, search, playback, queue, lyrics, favourites and
menus all work from your Pages and `features`.

## 4. Tests

Add a section to `tests/test_backend.py` that runs **without the network**:

- mapping: feed `_track` / `_album` a real-looking JSON object (copy one from the service) and check
  the Track fields;
- config: `prepare_config` / `authenticate` keep tokens when editing and never keep passwords;
- anything you parse by hand (pages, manifests, metadata) with a recorded sample.

Then check against the real service with a throwaway script (the scratch scripts used during
development did exactly that: create the service, call `home`, `search`, `open`, `stream`, and
fetch the first bytes of the stream). Run the suite:

```sh
python tests/test_backend.py
```

The suite also fails if your module imports a standard-library package that Decky's Python lacks
(`xml`, `html`, `webbrowser`, `getpass`) at the top level. Parse with `re` or `json` instead, or
import inside a function with a fallback.

## 5. On a device

Build, deploy and try it from the Quick Access Menu: add the source, browse every kind of page you
return, search, play (seek, skip, let a track end), favourite, and check the debug log for errors.
[DEVELOPMENT.md](DEVELOPMENT.md) describes the deploy and debugging tools.

## 6. Document it

- Add a row to the sources table in `README.md`.
- Add a section to [SOURCES.md](SOURCES.md): sign-in, endpoints, stream format, what's stored,
  limitations, reference projects.

## Checklist

- [ ] Streams play without DRM in Steam's browser engine
- [ ] Module docstring explains sign-in and streaming; class docstring lists config keys
- [ ] `features` lists exactly what's implemented
- [ ] Every user-facing failure is a `ServiceError` with a short, plain message
- [ ] Secrets are in `SECRET_KEYS`; no passwords stored; no credentials in URLs the UI sees
- [ ] Two sources of this type can coexist: files per instance/account, `account()` if it signs in,
  `remove()` cleans up
- [ ] Signed URLs have `expires`
- [ ] No top-level import of `xml`, `html`, `webbrowser`, `getpass`
- [ ] Offline tests added; `python tests/test_backend.py` passes; `npx tsc --noEmit` passes
- [ ] Tried on a device: browse, search, play, seek, skip, favourites
- [ ] README table and SOURCES.md updated
