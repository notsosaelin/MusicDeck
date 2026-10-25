#!/usr/bin/env bash
# Builds cdrskin for Windows (x86_64; Windows on Arm runs it too) into bin/windows-x86_64/cdrskin.exe: the
# same libburn 1.5.8 as Linux's (scripts/build_cdrskin.sh), talking to the drive through libcdio, whose
# Windows driver sends the drive its commands (SCSI pass-through) as libburn's Linux adapter does with SG_IO.
# One static .exe that needs only Windows' own DLLs. Both release tarballs are checked against their SHA-256
# (backend/cdrskin/UPSTREAM) before anything of them runs.
#
# libburn is built as released: what MinGW lacks of POSIX (signals, fcntl, pipe...) comes from
# backend/cdrskin/windows/compat.h, included into every file by the compiler, and compat.c. libcdio has three
# mmc_* functions of the same name as libburn's; they're renamed in libcdio's library (objcopy), which a
# shared build wouldn't need.
#
# Needs: MSYS2's UCRT64 shell, with
#   pacman -S --needed mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-pkgconf make
# Set LIBBURN_TARBALL / LIBCDIO_TARBALL to reuse downloaded release archives (the same SHA-256 checks apply).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPAT="$ROOT/backend/cdrskin/windows"
LIBBURN_VERSION=1.5.8
LIBBURN_SHA256=8e24dd99f5b7cafbecf0116d61b619ee89098e20263e6f47c793aaf4a98d6473
LIBBURN_URL="https://files.libburnia-project.org/releases/libburn-$LIBBURN_VERSION.tar.gz"
LIBCDIO_VERSION=2.4.0
LIBCDIO_SHA256=8e6a305f81e45a78b41ce696d34497cc208d97075469d7304c11a6d44bf006ac
LIBCDIO_URL="https://github.com/libcdio/libcdio/releases/download/$LIBCDIO_VERSION/libcdio-$LIBCDIO_VERSION.tar.bz2"
if [[ "${MSYSTEM:-}" != UCRT64 ]]; then
  echo "run this from MSYS2's UCRT64 shell" >&2
  exit 1
fi
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch() {  # fetch <url> <sha256> <dest> <reuse this file, if set>
  if [[ -n "$4" ]]; then cp "$4" "$3"; else curl -sSfL "$1" -o "$3"; fi
  echo "$2  $3" | sha256sum -c -
}
fetch "$LIBBURN_URL" "$LIBBURN_SHA256" "$WORK/libburn.tar.gz" "${LIBBURN_TARBALL:-}"
fetch "$LIBCDIO_URL" "$LIBCDIO_SHA256" "$WORK/libcdio.tar.bz2" "${LIBCDIO_TARBALL:-}"
tar xzf "$WORK/libburn.tar.gz" -C "$WORK"
tar xjf "$WORK/libcdio.tar.bz2" -C "$WORK"
PREFIX="$WORK/prefix"
CFLAGS_COMMON="-O2 -D_FILE_OFFSET_BITS=64"

# libcdio: only the library, static; no Joliet (libburn doesn't read ISO file systems), so no iconv
(
  cd "$WORK/libcdio-$LIBCDIO_VERSION"
  CFLAGS="$CFLAGS_COMMON" ./configure --prefix="$PREFIX" --disable-shared --enable-static --disable-cxx \
    --disable-cpp-progs --disable-example-progs --disable-joliet --disable-cddb --disable-vcd-info \
    --without-cd-drive --without-cd-info --without-cdda-player --without-cd-read --without-iso-info \
    --without-iso-read >/dev/null
  make -j"$(nproc)" >/dev/null
  make install >/dev/null
)
objcopy --redefine-sym mmc_get_configuration=cdio_mmc_get_configuration \
  --redefine-sym mmc_read_cd=cdio_mmc_read_cd --redefine-sym mmc_set_speed=cdio_mmc_set_speed \
  "$PREFIX/lib/libcdio.a"

gcc $CFLAGS_COMMON -c "$COMPAT/compat.c" -o "$WORK/compat.o"
ar rcs "$PREFIX/lib/libmdcompat.a" "$WORK/compat.o"

# libburn and cdrskin, with libcdio as their system adapter
(
  cd "$WORK/libburn-$LIBBURN_VERSION"
  export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
  CFLAGS="$CFLAGS_COMMON -include $COMPAT/compat.h -I$COMPAT/include -I$PREFIX/include" \
  LIBS="-L$PREFIX/lib -lmdcompat $(pkg-config --static --libs libcdio) -lws2_32" \
    ./configure --enable-libcdio --disable-shared --enable-static --disable-ldconfig-at-install > "$WORK/configure.log"
  grep -q "enabled  use of libcdio as system adapter" "$WORK/configure.log" \
    || { echo "libburn's configure didn't take libcdio" >&2; exit 1; }
  # -all-static: libtool's word for linking even the C runtime's pieces in
  make -j"$(nproc)" LDFLAGS="-all-static -s" >/dev/null
)
OUT="$ROOT/bin/windows-x86_64"
mkdir -p "$OUT"
cp "$WORK/libburn-$LIBBURN_VERSION/cdrskin/cdrskin.exe" "$OUT/cdrskin.exe"
cp "$WORK/libburn-$LIBBURN_VERSION/COPYING" "$OUT/cdrskin.COPYING"
cp "$WORK/libcdio-$LIBCDIO_VERSION/COPYING" "$OUT/cdrskin.libcdio.COPYING"
echo "built $OUT/cdrskin.exe"

# it needs nothing but Windows' own DLLs, starts, and is cdrskin $LIBBURN_VERSION on libcdio
dlls="$(objdump -p "$OUT/cdrskin.exe" | sed -n 's/^\s*DLL Name: //p' | grep -viE '^(kernel32|winmm|ws2_32|advapi32|user32|msvcrt)\.dll$|^api-ms-win-' || true)"
if [[ -n "$dlls" ]]; then
  echo "cdrskin.exe needs DLLs Windows doesn't have: $dlls" >&2
  exit 1
fi
version_output="$("$OUT/cdrskin.exe" --version 2>&1)"
grep -q "sg-libcdio .*libcdio $LIBCDIO_VERSION" <<< "$version_output" && grep -q "cdrskin version   :  $LIBBURN_VERSION" <<< "$version_output" \
  && echo "cdrskin $LIBBURN_VERSION runs (libcdio $LIBCDIO_VERSION)" || { echo "the built cdrskin.exe doesn't run" >&2; exit 1; }
