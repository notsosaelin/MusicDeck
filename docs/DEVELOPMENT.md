# Developing MusicDeck

Everything needed to build, test, run on a device, debug and release. For how the code is
organised, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Repository layout

```
main.py                    Decky backend entry point: the Plugin class and every RPC endpoint
py_modules/musicdeck/      backend package (services/, stream server, settings, playlists, ...)
py_modules/<others>/       vendored third-party packages (not in git; see scripts/vendor.py)
src/                       frontend (TypeScript + React, built by rollup into dist/index.js)
backend/spotify-helper/    Rust source of the Spotify helper
bin/<platform>/            built native helpers, spotify-helper and qjs, per platform (linux-x86_64,
                           linux-arm64, windows-x86_64; not in git; see musicdeck/native.py)
scripts/                   vendor, package, deploy, helper build, CDP tool
tests/test_backend.py      backend test suite (no device, no network)
tests/ui/                  frontend logic tests (Node's test runner; Steam's pieces are stand-ins)
docs/                      this documentation
decky.pyi                  type stubs for Decky's `decky` module (from the plugin template)
```

## Requirements

- Node 18+ and pnpm 9 (`npx -y pnpm@9` works without installing)
- Python 3.10+ (for scripts and tests)
- For the Spotify helper: Rust (rustup), the `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl`
  and `x86_64-pc-windows-gnu` targets, cargo-zigbuild
  and zig. Linux or WSL.
- For hashab and cdrskin: zig (on PATH, or `~/.local/zig`), make. Linux or WSL.
- For Windows' cdrskin and mtp-helper: [MSYS2](https://www.msys2.org)'s UCRT64 shell, with
  `pacman -S --needed mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-pkgconf make`.

## Build

```sh
pnpm install
pnpm build                              # src/ -> dist/index.js
python scripts/vendor.py                # third-party Python into py_modules/, QuickJS-NG into bin/<platform>/
bash scripts/build_spotify_helper.sh    # bin/<platform>/spotify-helper: static musl for x86_64 and arm64 Linux, Windows .exe
bash scripts/build_hashab.sh            # bin/<platform>/hashab (the iPod nano 6G/7G signature), checked against its test vectors
bash scripts/build_cdrskin.sh           # bin/linux-*/cdrskin (burning CDs): libburn's release, checked by SHA-256, static musl
bash scripts/build_cdrskin_windows.sh   # bin/windows-x86_64/cdrskin.exe: libburn on libcdio, static (MSYS2 UCRT64 shell)
bash scripts/build_mtp_helper.sh        # bin/windows-x86_64/mtp-helper.exe: phones over MTP on Windows (MSYS2 UCRT64 shell)
pnpm smoke                              # the built bundle loads, wires its player, unloads cleanly
python scripts/package.py               # out/MusicDeck-<version>-<platform>.zip (linux-x86_64, linux-arm64,
                                        # windows-x86_64), each with its own helpers, and their .sha256
```

- `vendor.py` installs exactly the versions pinned in `requirements.txt`, pure-Python wheels only
  (Decky's Python is frozen and can't load most compiled extensions), and downloads QuickJS-NG with a
  pinned SHA-256. aiohttp and certifi come with Decky and are not vendored.
- `build_spotify_helper.sh` builds with `--locked`; `Cargo.lock` pins `vergen` to 9.0.6 because
  later versions break librespot's build script.
- `build_cdrskin.sh` accepts `LIBBURN_TARBALL=/path/to/libburn-1.5.8.tar.gz` to reuse a downloaded
  archive; it checks the same pinned SHA-256 before building either Linux platform.
- `build_cdrskin_windows.sh` builds the same libburn for Windows with libcdio 2.4.0 as its SCSI
  transport (both tarballs SHA-256-pinned; `LIBBURN_TARBALL` and `LIBCDIO_TARBALL` reuse downloads).
  libburn's source isn't changed: `backend/cdrskin/windows/compat.h` is force-included for the POSIX
  functions MinGW lacks, and libcdio's three `mmc_*` functions that clash with libburn's are renamed in
  its static library. It checks the .exe needs only Windows' DLLs and runs. No burner has tried it yet.

## Install on a device

Enable Decky's developer mode, then **Decky → Settings → Developer → Install plugin from ZIP** with
the zip for its platform (`out/MusicDeck-<version>-linux-x86_64.zip` for a Steam Deck). Do this once; afterwards
use the deploy script.

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
python tests/test_cast.py      # casting: mDNS, a fake Chromecast, a fake UPnP renderer, the network site
python tests/test_ipod.py      # iPods: the database format against libgpod's (fixtures), a pretend iPod synced
python tests/test_burn.py      # burning CDs: cdrskin's output read, CD-Text, a burn planned and run (cdrskin stood in for)
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
- Audiobookshelf against a fake server: password and API-key sign-in, token renewal (and the new
  pair saved), home shelves, books as files named by chapter, resuming in the right file, saving
  the place in the whole book, played/unplayed, chapters as lyrics, unplayable formats, refused ids;
- downloads against a fake source of every stream kind: ranged files, servers without ranges,
  fragmented-MP4 and MP3 HLS (master playlists followed), encrypted and generated streams refused;
  playing, seeking and artwork with the source gone; one copy for two playlists; files deleted with
  the last playlist holding them; giving up when unreachable; stopping before the disk fills;
- backups: no sign-in in the file, restoring a removed source (signed out, marked) and its playlist
  and picture, restoring twice, refused files, and a hostile backup (odd ids, unknown types, smuggled
  tokens, paths out of the data folder, a script as a picture, bad preferences);
- podcasts against a fake directory and feed: the XML reader (CDATA, entities, broken markup),
  subscribing, home rows, resuming and marking played, refused ids;
- the Live Music Archive against a fake archive.org: home rows (on this day really today), shows,
  bands with true counts, radio, query-safe search, ids that try to climb out of an item;
- lyrics lookups against a fake LRCLIB and NetEase (retries, the backup, synced over unsynced,
  matching past covers and instrumentals, credits stripped; a site that can't be asked is an
  error, not "no lyrics"), "Jump back in" and
  recent searches, game music;
- Steam soundtracks against a fake Steam folder: libraries, manifests, DLC soundtracks inside a
  game, duplicate formats, installs finishing, art, the running-game row;
- several accounts of one service: naming, duplicates refused, per-account Spotify credentials;
- the shared sign-in flow, ListenBrainz (offline queue included);
- abuse: every endpoint called for a source or playlist that's gone, garbage input, double and
  triple taps run at once (saves, removals, switches, rescans), corrupt saved files, and bad
  requests to the stream server. Nothing may raise, lose data, or answer with a server error;
- that no MusicDeck module imports a standard-library package Decky's Python lacks.

The frontend's logic has its own tests, run by Node (22.15 or later) with nothing to install:

```sh
pnpm test        # node --import ./tests/ui/setup.mjs --test tests/ui/*.test.ts
```

`tests/ui/setup.mjs` compiles the TypeScript with the project's own compiler (`loader.mjs`), resolves
imports as the bundler does, and puts stand-ins in place of what only exists inside Steam
(`tests/ui/fakes/`: Decky's packages, whose `callable` answers from a `backend` object the test fills,
hls.js, toasts), plus an `<audio>` element that plays at once and a silent Web Audio graph. They
cover the player's queue (playing, Play next and Add to queue shuffled or not, moving, removing,
stale stream answers), its transport (wrapping, repeat, previous, volume and seek limits), stores
and bounded caches, and media URLs. Logic that draws nothing belongs here; what Steam draws is
checked on a device (below).

Also run `npx tsc --noEmit` (or `pnpm typecheck`) for the frontend.

The built bundle has a smoke test of its own, as the UI tests run the sources and not what the bundler
made of them (it once dropped a getter read that wired casting):

```sh
pnpm build && pnpm smoke     # tests/smoke/bundle.test.mjs against dist/index.js
```

It loads `dist/index.js` with stand-ins for Decky's globals, starts the plugin, checks the player and
its cast element are wired, the route and listeners are registered and the state arrives, then
unloads it and checks every listener, the route and the debug handle are gone. The process must then
exit at once: a timer left running keeps it alive, so a hang is a leak. It also fails on JSX text that
starts with `//` (a comment written among JSX children shows on screen).

Each service was additionally checked against its real API with throwaway scripts (create the
service, call `home`/`search`/`open`/`stream`, fetch the first bytes of the stream). Do the same
when you change a service; public test servers: `demo.navidrome.org`, `demo.jellyfin.org/stable`.

### iPods against libgpod

`tests/test_ipod.py` checks the iPod code against databases libgpod wrote (`tests/fixtures/ipod`). To check
against libgpod itself (after changing `itunesdb.py`), on Linux or WSL:

```sh
sh scripts/libgpod_check/setup.sh       # libgpod 0.8.3 unpacked into ~/gpod (no root), gpodtool built
python3 scripts/libgpod_check/check.py  # libgpod writes, we read; we write, libgpod reads (covers too)
python3 scripts/libgpod_check/check.py --fixtures   # and refresh tests/fixtures/ipod
```

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
m.centerTab.set({ tab: "playing" })       // the media center's tab; m.openMediaCenter() opens it
m.openInLibrary({ id, kind: "album", title: "" })  // a page, in the media center when it's showing
m.previewSource(id)                       // another source's Home/Browse/Search, the live one left playing;
                                          // m.previewSource() goes back (for checking each source's pages)
m.api.virtualCd("/path/to/disc.cue")      // a disc image in a virtual drive, found as a CD is (cdimage.py);
                                          // m.api.virtualCd("") unplugs the drive. A test image laid out to a
                                          // real pressing (so MusicBrainz names it): out/winbuild/make_real_cd.py
```

Screenshots of the QAM: `python scripts/cdp.py QuickAccess "1" --shot qam.png`. Dialogs render in
the "Steam Big Picture Mode" target. (The screen must be on; a sleeping display captures black.)

In desktop Steam, each window is a target by its title: "Steam" (the main window, where Music is),
"MusicDeck" (the mini player), "MusicDeck Settings" and the other dialogs. A window covered by others
isn't painted, so its capture waits forever: bring it to the front first
(`SteamClient.Window.BringToFront()` in it). `--hover x,y` moves the mouse there before the capture, for
hover states (CSS pixels: on a 2x display, half the screenshot's).

```js
const d = m.desktop.current;              // desktop Steam's parts, while it's showing
d.showMusic(); d.openMini(); d.mini()     // Music, the mini player (its window)
d.showSettings(); d.showEqualizer()       // the dialogs
m.lookup.open(sourceId, album, files)     // Look up details for an album
```

Desktop Steam (src/desktop): MusicDeck still runs in SharedJSContext, and draws into the main window, whose
target is titled "Steam" (its parts carry `data-musicdeck`: the tab, the section, the hover menu, the
soundtrack button). From SharedJSContext, `window.__musicdeck.desktop.current` drives it without the mouse:
`.showMusic()`, `.hideMusic()`, `.openMini()`, `.mini()` (the mini player's window), `.state()`. Scripts run in
the "Steam" target reach SharedJSContext as `window.opener`. Dialogs (`showModal`) open as windows of their own
("Decky Dialog"); menus (`showContextMenu`) draw inside the window that was clicked, and close on Escape. A
Steam window's `screenX`/`screenY` don't follow it once it's moved: ask `SteamClient.Window.GetWindowDimensions()`.

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

## Versions and releasing

Every build put on a device has a number of its own, set in one place: `version` in `package.json`
(the frontend reports it to Decky, the backend reads it in `musicdeck/version.py` for the User-Agent
sources see, and Decky → MusicDeck shows it). `python scripts/bump.py` raises the last number (a fix
or a small change); `bump.py minor` the middle one (new features); `bump.py major` the first. Commit
the bump as "Release <version>" and tag it `v<version>`. `scripts/deploy.py` copies `package.json`
along and pauses the music before it reloads the plugin.

Every release also brings the docs up to date: the README for anything a user sees or presses
differently, ARCHITECTURE / SOURCES / ADDING_A_SOURCE / this file where the change touches them, and
an entry in `CHANGELOG.md` (what changed, for the people using it).

1. `python scripts/bump.py [minor|major]`.
2. Update the docs and add the version to `CHANGELOG.md`.
3. `pnpm typecheck && pnpm test && python tests/test_backend.py && python tests/test_cast.py && python tests/test_ipod.py && python tests/test_burn.py && python tests/test_windows.py && python tests/test_desktop.py` (on Windows
   too: `test_windows.py` asks the PC's own drives and sound output there).
4. `pnpm build && pnpm smoke && python scripts/vendor.py && bash scripts/build_spotify_helper.sh && bash scripts/build_hashab.sh && bash scripts/build_cdrskin.sh && python scripts/package.py`, with `bash scripts/build_cdrskin_windows.sh` and
   `bash scripts/build_mtp_helper.sh` run in MSYS2's UCRT64 shell before `package.py`
5. Install the zip on a device and smoke-test every source you can sign in to.
6. Commit as "Release <version>", tag `v<version>`, and attach the three `out/MusicDeck-<version>-<platform>.zip` and their `.sha256`
   to the GitHub release `v<version>` of `updates.REPO` (`py_modules/musicdeck/updates.py`): installed copies find
   it there and offer the update. A draft or pre-release isn't offered.
