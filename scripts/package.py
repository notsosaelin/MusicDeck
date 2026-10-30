"""Builds one zip per platform in the layout Decky expects, for "Install plugin from ZIP" in developer
mode, each with its SHA-256 beside it:

    out/MusicDeck-<version>-linux-x86_64.zip    Steam Deck, SteamOS, Bazzite and other x86_64 Linux
    out/MusicDeck-<version>-linux-arm64.zip     Armada, DroidDeck, Steam Frame and other ARM64 Linux
    out/MusicDeck-<version>-windows-x86_64.zip  Windows (Windows on Arm runs it too)

Each carries the same plugin with only its own platform's helpers (bin/<platform>/, see
musicdeck/native.py); the plugin's folder inside is MusicDeck on every one, as Decky expects.

    pnpm build && pnpm smoke && python scripts/vendor.py && python scripts/package.py
"""
import hashlib
import json
import pathlib
import shutil
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = json.loads((ROOT / "plugin.json").read_text())["name"]
VERSION = json.loads((ROOT / "package.json").read_text())["version"]
FILES = ["main.py", "plugin.json", "package.json", "LICENSE", "README.md", "CHANGELOG.md"]
DIRS = ["dist", "py_modules", "assets", "bin"]

if not (ROOT / "dist" / "index.js").exists():
    raise SystemExit("dist/index.js missing; run `pnpm build` first")
# a zip of an old build would carry this version's number and the last one's code
built = (ROOT / "dist" / "index.js").stat().st_mtime
newer = [p for p in (ROOT / "src").rglob("*") if p.is_file() and p.stat().st_mtime > built]
if newer:
    raise SystemExit(f"dist/index.js is older than {newer[0].relative_to(ROOT)}; run `pnpm build` again")
# every platform the zip is for (musicdeck/native.py): Linux x86_64 and arm64, Windows x86_64 (which
# Windows on Arm runs too)
HELPERS = {"linux-x86_64": ["qjs", "spotify-helper", "hashab", "musicdeck-net", "fpcalc"],
           "linux-arm64": ["qjs", "spotify-helper", "hashab", "musicdeck-net", "fpcalc"],
           "windows-x86_64": ["qjs.exe", "spotify-helper.exe", "hashab.exe", "musicdeck-net.exe", "fpcalc.exe",
                              "python/python.exe"]}
if not (ROOT / "py_modules" / "yt_dlp").exists():
    raise SystemExit("vendored files missing; run `python scripts/vendor.py` first")
for platform_name, names in HELPERS.items():
    for name in names:
        if not (ROOT / "bin" / platform_name / name).exists():
            fix = ("bash scripts/build_spotify_helper.sh" if name.startswith("spotify")
                   else "bash scripts/build_net_helper.sh" if name.startswith("musicdeck-net")
                   else "bash scripts/build_hashab.sh" if name.startswith("hashab") else "python scripts/vendor.py")
            raise SystemExit(f"bin/{platform_name}/{name} missing; run `{fix}` first")
# cdrskin (burning CDs): bundled when built, with where its source is (GPL); without it, a build burns no
# CDs unless the system has cdrskin
CDRSKIN = {"linux-x86_64": ("cdrskin", "bash scripts/build_cdrskin.sh"),
           "linux-arm64": ("cdrskin", "bash scripts/build_cdrskin.sh"),
           "windows-x86_64": ("cdrskin.exe", "bash scripts/build_cdrskin_windows.sh, in MSYS2's UCRT64 shell")}
for platform_name, (name, fix) in CDRSKIN.items():
    if (ROOT / "bin" / platform_name / name).exists():
        shutil.copyfile(ROOT / "backend" / "cdrskin" / "UPSTREAM", ROOT / "bin" / platform_name / "cdrskin.UPSTREAM")
    else:
        print(f"note: no bin/{platform_name}/{name} ({fix}): that build burns no CDs")
# mtp-helper (phones over MTP, Windows: Linux has GVfs): bundled when built
if not (ROOT / "bin" / "windows-x86_64" / "mtp-helper.exe").exists():
    print("note: no bin/windows-x86_64/mtp-helper.exe (bash scripts/build_mtp_helper.sh, in MSYS2's UCRT64 shell): "
          "that build doesn't see phones")

OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
# zips of this version from before (one for every platform, or another platform list) go
for old in OUT.glob(f"{NAME}-{VERSION}*.zip*"):
    old.unlink()

for platform_name in HELPERS:
    out = OUT / f"{NAME}-{VERSION}-{platform_name}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            z.write(ROOT / name, f"{NAME}/{name}")
        for directory in DIRS:
            # bin/: only this platform's helpers
            base = ROOT / "bin" / platform_name if directory == "bin" else ROOT / directory
            for path in sorted(base.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".map":
                    arcname = f"{NAME}/{path.relative_to(ROOT).as_posix()}"
                    if directory == "bin":
                        # Windows has no executable bit to copy, so mark bundled binaries rwxr-xr-x explicitly
                        info = zipfile.ZipInfo.from_file(path, arcname)
                        info.external_attr = 0o100755 << 16
                        info.compress_type = zipfile.ZIP_DEFLATED
                        z.writestr(info, path.read_bytes())
                    else:
                        z.write(path, arcname)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (OUT / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n")
    print(f"wrote {out.name} ({out.stat().st_size / 1e6:.1f} MB)  sha256 {digest}")
