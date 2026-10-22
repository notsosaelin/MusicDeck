"""Builds out/MusicDeck.zip in the layout Decky expects, for "Install plugin from ZIP" in developer mode.

    pnpm build && python scripts/vendor.py && python scripts/package.py
"""
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = json.loads((ROOT / "plugin.json").read_text())["name"]
FILES = ["main.py", "plugin.json", "package.json", "LICENSE", "README.md"]
DIRS = ["dist", "py_modules", "assets", "bin"]

if not (ROOT / "dist" / "index.js").exists():
    raise SystemExit("dist/index.js missing; run `pnpm build` first")
if not (ROOT / "py_modules" / "yt_dlp").exists() or not (ROOT / "bin" / "qjs").exists():
    raise SystemExit("vendored files missing; run `python scripts/vendor.py` first")
if not (ROOT / "bin" / "spotify-helper").exists():
    raise SystemExit("bin/spotify-helper missing; run `bash scripts/build_spotify_helper.sh` first")

out = ROOT / "out" / f"{NAME}.zip"
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
print(f"wrote {out}")
