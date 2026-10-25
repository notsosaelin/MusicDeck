#!/usr/bin/env bash
# Builds mtp-helper (backend/mtp-helper/mtp.c: phones and players over MTP on Windows, through Windows
# Portable Devices) into bin/windows-x86_64/mtp-helper.exe: one static .exe needing only Windows' own DLLs.
# Linux doesn't need it (GVfs mounts phones there).
#
# Needs: MSYS2's UCRT64 shell, with pacman -S --needed mingw-w64-ucrt-x86_64-gcc
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${MSYSTEM:-}" != UCRT64 ]]; then
  echo "run this from MSYS2's UCRT64 shell" >&2
  exit 1
fi
OUT="$ROOT/bin/windows-x86_64"
mkdir -p "$OUT"
gcc -O2 -Wall -Wextra -Wno-unused-parameter -municode -static -s -o "$OUT/mtp-helper.exe" \
  "$ROOT/backend/mtp-helper/mtp.c" -lole32 -loleaut32 -luuid -lpropsys
echo "built $OUT/mtp-helper.exe"
dlls="$(objdump -p "$OUT/mtp-helper.exe" | sed -n 's/^\s*DLL Name: //p' \
  | grep -viE '^(kernel32|ole32|oleaut32|propsys|user32|advapi32|msvcrt)\.dll$|^api-ms-win-' || true)"
if [[ -n "$dlls" ]]; then
  echo "mtp-helper.exe needs DLLs Windows doesn't have: $dlls" >&2
  exit 1
fi
"$OUT/mtp-helper.exe" devices > /dev/null && echo "mtp-helper runs"
