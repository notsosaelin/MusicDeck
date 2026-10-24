#!/usr/bin/env bash
# Builds cdrskin (libburn's CD burner, GPL-2.0-or-later: what burn.py burns audio CDs with) for the Linux
# platforms MusicDeck ships for, into bin/<platform>/cdrskin: static musl binaries, so they run on SteamOS,
# Bazzite or any Linux as they are (none of them ship a burner). libburn's release tarball is downloaded
# and checked against its SHA-256 (backend/cdrskin/UPSTREAM) before anything of it runs.
#
# Needs: zig (on PATH, or ~/.local/zig), curl, make. Works from Linux or WSL. Pass platforms to build only
# those (linux-x86_64, linux-arm64).
# Set LIBBURN_TARBALL to reuse a downloaded release archive (the same SHA-256 check applies).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/zig:$PATH"
VERSION=1.5.8
SHA256=8e24dd99f5b7cafbecf0116d61b619ee89098e20263e6f47c793aaf4a98d6473
URL="https://files.libburnia-project.org/releases/libburn-$VERSION.tar.gz"
declare -A TARGETS=([linux-x86_64]=x86_64-linux-musl [linux-arm64]=aarch64-linux-musl)
PLATFORMS=("${@:-linux-x86_64 linux-arm64}")
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
if [[ -n "${LIBBURN_TARBALL:-}" ]]; then
  cp "$LIBBURN_TARBALL" "$WORK/libburn.tar.gz"
else
  curl -sSfL "$URL" -o "$WORK/libburn.tar.gz"
fi
echo "$SHA256  $WORK/libburn.tar.gz" | sha256sum -c -
tar xzf "$WORK/libburn.tar.gz" -C "$WORK"
SRC="$WORK/libburn-$VERSION"
for platform in ${PLATFORMS[@]}; do
  target="${TARGETS[$platform]}"
  build="$WORK/build-$platform"
  mkdir -p "$build"
  (
    cd "$build"
    # libburn's own build, cross-compiled by zig; static, without libcdio (it talks SG_IO to the drive itself)
    CC="zig cc -target $target" AR="zig ar" RANLIB="zig ranlib" \
      "$SRC/configure" --host="$target" --disable-shared --enable-static --disable-libcdio \
      --disable-ldconfig-at-install >/dev/null
    make -j"$(nproc)" LDFLAGS="-static -s" >/dev/null
  )
  mkdir -p "$ROOT/bin/$platform"
  cp "$build/cdrskin/cdrskin" "$ROOT/bin/$platform/cdrskin"
  cp "$SRC/COPYING" "$ROOT/bin/$platform/cdrskin.COPYING"
  chmod 0755 "$ROOT/bin/$platform/cdrskin"
  echo "built $ROOT/bin/$platform/cdrskin"
done
# the one that runs here: it starts, and is cdrskin $VERSION
if [[ " ${PLATFORMS[*]} " == *" linux-x86_64 "* && "$(uname -m)" == x86_64 ]]; then
  version_output="$("$ROOT/bin/linux-x86_64/cdrskin" --version 2>&1)"
  grep -q "libburn.*$VERSION\|cdrskin $VERSION" <<< "$version_output" \
    && echo "cdrskin $VERSION runs" || { echo "the built cdrskin doesn't run" >&2; exit 1; }
fi
