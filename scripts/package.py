"""Builds out/MusicDeck-<version>.zip in the layout Decky expects, for "Install plugin from ZIP" in
developer mode, and its SHA-256 beside it (out/MusicDeck-<version>.zip.sha256).

    pnpm build && pnpm smoke && python scripts/vendor.py && python scripts/package.py
"""
import hashlib
import json
import pathlib
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
HELPERS = {"linux-x86_64": ["qjs", "spotify-helper"], "linux-arm64": ["qjs", "spotify-helper"],
           "windows-x86_64": ["qjs.exe", "spotify-helper.exe"]}
if not (ROOT / "py_modules" / "yt_dlp").exists():
    raise SystemExit("vendored files missing; run `python scripts/vendor.py` first")
for platform_name, names in HELPERS.items():
    for name in names:
        if not (ROOT / "bin" / platform_name / name).exists():
            fix = "bash scripts/build_spotify_helper.sh" if name.startswith("spotify") else "python scripts/vendor.py"
            raise SystemExit(f"bin/{platform_name}/{name} missing; run `{fix}` first")

out = ROOT / "out" / f"{NAME}-{VERSION}.zip"
out.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in FILES:
        z.write(ROOT / name, f"{NAME}/{name}")
    for directory in DIRS:
        for path in sorted((ROOT / directory).rglob("*")):
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
(out.parent / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n")
print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)\nsha256 {digest}")
