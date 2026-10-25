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
- Before a release, the backend is deployed to the Deck (`notsosaelin@192.168.1.155`, key login;
  `MUSICDECK_DEVICE=... python scripts/deploy.py --backend`) and its newest log in `~/homebrew/logs/MusicDeck/`
  checked for errors: Decky's Python lacks parts of the standard library (0.31.0 shipped not starting there).
  `test_cast.py` loads the backend with those parts blocked; a module that needs one imports it when called.
- Desktop Steam is tested through its debugger: this PC's on 127.0.0.1:8080, the Deck's through
  `ssh -N -L 18090:localhost:8080` (run with a `timeout`, and reopened when it ends).
- Desktop test scripts that wait use SharedJSContext's timers (`window.opener.setTimeout` from the main
  window): a minimized or covered Steam window's own timers are throttled and the script hangs.
- Screenshots of desktop Steam don't work here (a minimized window captures the wallpaper; the Deck's
  Xwayland gives black): check the DOM, `document.visibilityState` and `GetWindowDimensions` instead.
  On this PC "hidden" usually means covered by another window: compare with the main window's.
- `scripts/cdp.py <title>` picks the target titled exactly that first: the main window is "Steam",
  the Decky side "SharedJSContext"; each test script's first line says which it runs in.

## Where things build

- Native helpers for Linux (cdrskin, hashab, spotify-helper): WSL (Ubuntu), zig in `~/.local/zig`, Rust via
  rustup. Call WSL from PowerShell, not Git Bash (which rewrites `/mnt/...` paths).
- Windows helpers (cdrskin.exe, mtp-helper.exe): MSYS2's UCRT64 shell (`C:\msys64`).
- Frontend and Python tests: Windows (`npx -y pnpm@9`, Python with aiohttp and certifi).
- Release steps: docs/DEVELOPMENT.md, "Versions and releasing".
