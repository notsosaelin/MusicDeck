# Working on MusicDeck

## Running builds and tests: no run is left to hang

- Every build, test or install command runs under a hard limit: `timeout <seconds>` (bash), or the tool's own
  timeout. Limits: a test suite 300 s, `pnpm build` / `smoke` 180 s, a helper build 600 s, `package.py` 300 s.
  A run that hits its limit is a failure to report, not something to wait out.
- Output goes to a log under `out/` as the run goes (not only at the end), and that log is what's checked.
- Tests and builds run in the foreground. Anything that has to run in the background is checked within two
  minutes by its log (lines growing, CPU used); one that hasn't moved in two minutes is stuck: kill it,
  confirm it's gone (`ps` / `Get-Process`), and say so.
- Never `sleep` or wait on a background task without a check of what it's doing.
- Interactive tools (pacman, installers) aren't run hidden: give the user the exact commands to run instead.
- Comparing against the committed code (a `git worktree` under `out/`) needs the complete `py_modules/`
  (`python scripts/vendor.py` there), or its results mean nothing.
- `pkill -f <pattern>` inside `bash -c '...'` matches its own shell; kill by PID, or a pattern that can't
  match the command doing the killing.

## Mistakes already made here, not to make again

- WSL commands with `$`, quotes, `<` or pipes are never written inline in `wsl.exe -- bash -c "..."` from
  PowerShell (PowerShell eats them), nor run from Git Bash (it rewrites `/mnt/...` paths): write a script
  under `out/winbuild/` with the Write tool, then `wsl.exe -- bash /mnt/d/MusicDeck/out/winbuild/<it>.sh`.
- WSL's files (its `/tmp`) are read from Windows at `//wsl.localhost/Ubuntu-24.04/...`, not through
  `wsl.exe` one-liners.
- Code with backslashes (Windows paths, `\\.\D:`) is changed with the Edit tool, never through a heredoc'd
  Python or sed replace.
- A file changed by a shell command (sed, a script) is Read again before the Edit tool touches it.
- Python run with output to a log is run with `-u`: block-buffered prints make a killed run's log lie
  about where it stopped.
- WSL's VM starts when first called, so `time.monotonic()` there can be under a minute: don't treat 0.0 as
  "long ago" (DeviceService's space cache did).
- pacman updates are the user's to run in a visible MSYS2 window (they prompt; hidden, they hang).
- Before a release, the backend is deployed to the Deck (`$MUSICDECK_DEVICE`, in CLAUDE.local.md; key login;
  `MUSICDECK_DEVICE=... python scripts/deploy.py --backend`) and its newest log in `~/homebrew/logs/MusicDeck/`
  (the dated `2026-*.log`, not `musicdeck-debug.log`, which is often newer) checked for errors: Decky's Python lacks parts of the standard library (0.31.0 shipped not starting there).
  `test_cast.py` loads the backend with those parts blocked; a module that needs one imports it when called.
- Desktop Steam is tested through its debugger: this PC's on 127.0.0.1:8080, the Deck's through
  `ssh -N -L 18090:localhost:8080` (run with a `timeout`, and reopened when it ends).
- Desktop test scripts that wait use SharedJSContext's timers (`window.opener.setTimeout` from the main
  window): a minimized or covered Steam window's own timers are throttled and the script hangs.
- UI changes are looked at, not only queried: `bash out/winbuild/shot.sh <window title> <name> [js]` brings
  the window to the front (`SteamClient.Window.BringToFront`; a covered one isn't painted and the capture
  hangs) and screenshots it through the debugger, on the PC and on the Deck alike (X captures of the Deck
  come out black; the debugger's don't). `cdp.py ... --hover x,y` shows hover states; the Deck's
  coordinates are CSS pixels, half its screen's. "hidden" visibility on the PC means covered, not broken.
- Steam's dialog windows are opened by Steam's own code: their flags (resizable...) are in the
  `about:blank?createflags=` address handed to `window.open`, not in showModal's options (ignored).
- `scripts/cdp.py <title>` picks the target titled exactly that first: the main window is "Steam",
  the Decky side "SharedJSContext"; each test script's first line says which it runs in.

## Where things build

- Native helpers for Linux (cdrskin, hashab, spotify-helper): WSL (Ubuntu), zig in `~/.local/zig`, Rust via
  rustup. Call WSL from PowerShell, not Git Bash (which rewrites `/mnt/...` paths).
- Windows helpers (cdrskin.exe, mtp-helper.exe): MSYS2's UCRT64 shell (`C:\msys64`).
- Frontend and Python tests: Windows (`npx -y pnpm@9`, Python with aiohttp and certifi).
- Release steps: docs/DEVELOPMENT.md, "Versions and releasing".
