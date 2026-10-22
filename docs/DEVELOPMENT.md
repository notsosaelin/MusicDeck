# Developing MusicDeck

Everything needed to build, test, run on a device, debug and release. For how the code is
organised, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Repository layout

```
main.py                    Decky backend entry point: the Plugin class and every RPC endpoint
py_modules/musicdeck/      backend package (services/, stream server, settings, playlists, ...)
py_modules/<others>/       vendored third-party packages (not in git; see scripts/vendor.py)
src/                       frontend (TypeScript + React, built by rollup into dist/index.js)
backend/spotify-helper/    Rust source of bin/spotify-helper
bin/                       built native helpers: spotify-helper, qjs (not in git)
scripts/                   vendor, package, deploy, helper build, CDP tool
tests/test_backend.py      backend test suite (no device, no network)
docs/                      this documentation
decky.pyi                  type stubs for Decky's `decky` module (from the plugin template)
```

## Requirements

- Node 18+ and pnpm 9 (`npx -y pnpm@9` works without installing)
- Python 3.10+ (for scripts and tests)
- For the Spotify helper: Rust (rustup), the `x86_64-unknown-linux-musl` target, cargo-zigbuild
  and zig. Linux or WSL.

## Build

```sh
pnpm install
pnpm build                              # src/ -> dist/index.js
python scripts/vendor.py                # third-party Python into py_modules/, QuickJS-NG into bin/qjs
bash scripts/build_spotify_helper.sh    # bin/spotify-helper (static musl binary)
python scripts/package.py               # out/MusicDeck.zip
```

- `vendor.py` installs exactly the versions pinned in `requirements.txt`, pure-Python wheels only
  (Decky's Python is frozen and can't load most compiled extensions), and downloads QuickJS-NG with a
  pinned SHA-256. aiohttp and certifi come with Decky and are not vendored.
- `build_spotify_helper.sh` builds with `--locked`; `Cargo.lock` pins `vergen` to 9.0.6 because
  later versions break librespot's build script.

## Install on a device

Enable Decky's developer mode, then **Decky → Settings → Developer → Install plugin from ZIP** with
`out/MusicDeck.zip`. Do this once; afterwards use the deploy script.

## Fast iteration: deploy and hot-reload

```sh
# one-time: SSH key login, and Decky → Developer → "Allow remote CEF debugging"
ssh-copy-id deck@steamdeck.local
pip install websockets

export MUSICDECK_DEVICE=deck@steamdeck.local
pnpm build && python scripts/deploy.py              # frontend change: ~5 s
pnpm build && python scripts/deploy.py --backend    # Python, py_modules or bin/ changed
```

`deploy.py` copies the build over SSH and reloads the plugin through Steam's debugger (see the
script's docstring). Without remote debugging it still copies; reload from Decky's developer page.

## Tests

```sh
python scripts/vendor.py       # the tests use the vendored packages in py_modules/ (mutagen, ...)
pip install aiohttp certifi    # these come with Decky on a device, so they aren't vendored
python tests/test_backend.py
```

The suite stubs the `decky` module, builds a small tagged music library in a temp folder, starts the
real `Plugin` and stream server, and checks them over HTTP. Local fake servers stand in for
ListenBrainz and an Icecast station, so it needs no network. It covers:

- local library scanning, tags, art, lyrics, playlists, folder confinement;
- the stream server: keys, ranges, CORS, art, generated HLS playlists;
- MusicDeck playlists and artwork, play-report de-duplication, session handling;
- every service's mapping and config rules (Spotify, TIDAL, Jellyfin, radio, Bandcamp, Subsonic);
- Emby against a fake server: its own paths for items, favourites and plays, the session ids its
  play report needs, unfiltered playlists, password-only sign-in and errors that name Emby;
- Plex against a fake plex.tv and server: both sign-in codes, server discovery (owned first, dead
  addresses skipped, shared servers' tokens), the picker, open servers, ratings, transcoding,
  timed lyrics, title search, refused ids and image paths;
- podcasts against a fake directory and feed: the XML reader (CDATA, entities, broken markup),
  subscribing, home rows, resuming and marking played, refused ids;
- the Live Music Archive against a fake archive.org: home rows (on this day really today), shows,
  bands with true counts, radio, query-safe search, ids that try to climb out of an item;
- lyrics lookups (LRCLIB down or rate limited is an error, not "no lyrics"), "Jump back in" and
  recent searches, game music;
- Steam soundtracks against a fake Steam folder: libraries, manifests, DLC soundtracks inside a
  game, duplicate formats, installs finishing, art, the running-game row;
- several accounts of one service: naming, duplicates refused, per-account Spotify credentials;
- the shared sign-in flow, ListenBrainz (offline queue included);
- abuse: every endpoint called for a source or playlist that's gone, garbage input, double and
  triple taps run at once (saves, removals, switches, rescans), corrupt saved files, and bad
  requests to the stream server. Nothing may raise, lose data, or answer with a server error;
- that no MusicDeck module imports a standard-library package Decky's Python lacks.

Also run `npx tsc --noEmit` (or `pnpm typecheck`) for the frontend.

Each service was additionally checked against its real API with throwaway scripts (create the
service, call `home`/`search`/`open`/`stream`, fetch the first bytes of the stream). Do the same
when you change a service; public test servers: `demo.navidrome.org`, `demo.jellyfin.org/stable`.

## Debugging

### Logs

| Log | Where | What |
| --- | --- | --- |
| Plugin log | `~/homebrew/logs/MusicDeck/<date>.log` | Backend start-up, errors, tracebacks |
| Debug log | `~/homebrew/logs/MusicDeck/musicdeck-debug.log` | Turn on in settings → Troubleshooting. Every stream resolution, proxy error, HLS event, player state change (from the frontend's `dlog`), sign-in step |

### The `window.__musicdeck` handle

`index.tsx` exposes a debug handle in Steam's SharedJSContext. With the CEF debugger (Chrome at
`chrome://inspect`, or `scripts/cdp.py`), you can drive everything:

```js
const m = window.__musicdeck;
m.pluginState.get()                       // services, settings, server address
m.player.state.get()                      // queue, index, playing, shuffle, repeat
m.api.search(id, "query")                 // any backend endpoint (see src/api.ts)
m.player.setVolume(0.001)                 // near-silent while testing playback
m.switchSource(id)                        // change the live source
m.sourceDialog("tidal")                   // open a source's add dialog (or pass a source id to edit)
m.nameDialog()                            // the playlist name prompt
m.setSleepTimer(0.25)                     // a 15-second sleep timer
m.syncSteam()                             // resend owned soundtracks and the running game
m.games.reconcile([2149420])              // pretend a game started (by app id); reconcile([]) stops it
m.games.runningGame.set({ appid, name })  // show the "play with this game" buttons without a game
m.navigation / m.panelTab                 // the QAM panel's navigation state
```

Screenshots of the QAM: `python scripts/cdp.py QuickAccess "1" --shot qam.png`. Dialogs render in
the "Steam Big Picture Mode" target. (The screen must be on; a sleeping display captures black.)

### Common problems

- **The plugin doesn't load at all** (no Soundtracks takeover, missing from Decky): check the plugin
  log for an import error. Usually a top-level import of a module Decky's Python lacks.
- **The QAM shows something other than what Settings says** (an old source, a queue that doesn't
  move): a copy of the panel or the settings page from before a reload. Current builds follow the
  newest copy by themselves (see "Quick Access Menu integration" in ARCHITECTURE.md); if a build
  without that is still loaded, close and reopen the menu.
- **"Could not reach …"**: the device is offline or the service changed host; the message is the
  service's own error.
- **SoundCloud/Qobuz stop working everywhere**: their web bundles changed; update the regexes at the
  top of the service file. **YouTube**: Settings → the source's menu → Update yt-dlp.
- **Spotify "audio key error"**: the account can't get audio keys through librespot (often new
  accounts). Not a MusicDeck bug.
- **The QAM shows Decky's error screen**: a render error in the panel. Press Retry; the stack trace
  in that screen names the component.

## Conventions

- **Python**: standard library + aiohttp; `async` everywhere in services; lines up to ~120
  characters; module docstrings explain *how the source works* and class docstrings list config
  keys. Errors meant for the user are `ServiceError("plain sentence")`; anything else is a bug and
  is logged with a traceback.
- **TypeScript/React**: Prettier with `printWidth: 120` (`.prettierrc.json`); Decky's own components
  (`@decky/ui`) over custom ones, so the UI matches Steam's; state outside React lives in `Store`s.
- **UI rules**: native look first (Steam's dialogs, fields, focus highlights); B goes back, no back
  arrows; one source live at a time, chosen in the plugin settings, never mixed in the QAM.
- **Secrets**: never store passwords; keep tokens out of URLs and out of the frontend
  (`SECRET_KEYS`).
- **Sources without accounts stay signed out** (SoundCloud, YouTube, Bandcamp, radio): user data for
  them is kept on the Deck.
- **No DRM circumvention**, and no hidden browsers or other side processes to get around missing
  features; if a source can't work natively, document it under "Not possible" in SOURCES.md.
- **Commits**: one feature or fix per commit, message says what and why.

## Releasing

1. Bump `version` in `package.json` (the frontend reports it to Decky).
2. `pnpm build && python scripts/vendor.py && bash scripts/build_spotify_helper.sh && python scripts/package.py`
3. Install the zip on a device and smoke-test every source you can sign in to.
4. Tag the commit and attach `out/MusicDeck.zip`.
