#!/usr/bin/env bash
# Builds hashab (backend/hashab: the iPod nano 6G/7G database signature) for every platform MusicDeck
# ships for, into bin/<platform>/ (see musicdeck/native.py): static Linux binaries (musl) for x86_64 and
# arm64, and a Windows x86_64 .exe. Then checks the build that runs here against the 100 test vectors in
# backend/hashab/test-data.json.
#
# Needs: zig (on PATH, or ~/.local/zig). Works from Linux or WSL. Pass platforms to build only those.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/zig:$PATH"
declare -A TARGETS=(
  [linux-x86_64]=x86_64-linux-musl
  [linux-arm64]=aarch64-linux-musl
  [windows-x86_64]=x86_64-windows-gnu
)
PLATFORMS=("${@:-linux-x86_64 linux-arm64 windows-x86_64}")
SRC="$ROOT/backend/hashab"
for platform in ${PLATFORMS[@]}; do
  exe=hashab
  [[ "$platform" == windows-* ]] && exe=hashab.exe
  mkdir -p "$ROOT/bin/$platform"
  zig cc -target "${TARGETS[$platform]}" -O2 -std=c11 -fwrapv -static -s -I"$SRC/src" \
    "$SRC/main.c" "$SRC"/src/*.c -o "$ROOT/bin/$platform/$exe"
  chmod 0755 "$ROOT/bin/$platform/$exe"
  echo "built $ROOT/bin/$platform/$exe"
done
# the test vectors, through the binary that runs here
if [[ " ${PLATFORMS[*]} " == *" linux-x86_64 "* && "$(uname -m)" == x86_64 ]]; then
  python3 - "$ROOT/bin/linux-x86_64/hashab" "$SRC/test-data.json" <<'EOF'
import json, subprocess, sys
exe, cases = sys.argv[1], json.load(open(sys.argv[2]))
bad = [c for c in cases if subprocess.run([exe, c["sha1"], c["uuid"]], capture_output=True, text=True).stdout.strip() != c["target"]]
print(f"{len(cases) - len(bad)} of {len(cases)} test vectors match")
sys.exit(1 if bad else 0)
EOF
fi
