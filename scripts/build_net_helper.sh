#!/usr/bin/env bash
# Builds musicdeck-net (the internet link between paired devices: musicdeck/farlink.py) for every platform
# MusicDeck ships for, into bin/<platform>/: static Linux binaries (musl) for x86_64 and arm64 and a Windows
# x86_64 .exe, as build_spotify_helper.sh does.
#
# Needs: rustup with the targets below, cargo-zigbuild, and zig (on PATH, or ~/.local/zig, or `pip install ziglang`).
#   rustup target add x86_64-unknown-linux-musl aarch64-unknown-linux-musl x86_64-pc-windows-gnu
#   cargo install --locked cargo-zigbuild
# Works from Linux, WSL, or Git Bash on Windows. Pass platforms to build only those: build_net_helper.sh linux-arm64
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/zig:$PATH"
TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/musicdeck-build/musicdeck-net}"
declare -A TARGETS=(
  [linux-x86_64]=x86_64-unknown-linux-musl
  [linux-arm64]=aarch64-unknown-linux-musl
  [windows-x86_64]=x86_64-pc-windows-gnu
)
PLATFORMS=("${@:-linux-x86_64 linux-arm64 windows-x86_64}")
cd "$ROOT/backend/musicdeck-net"
for platform in ${PLATFORMS[@]}; do
  target="${TARGETS[$platform]}"
  CARGO_TARGET_DIR="$TARGET_DIR" cargo zigbuild --release --locked --target "$target"
  exe=musicdeck-net
  [[ "$platform" == windows-* ]] && exe=musicdeck-net.exe
  mkdir -p "$ROOT/bin/$platform"
  install -m 0755 "$TARGET_DIR/$target/release/$exe" "$ROOT/bin/$platform/$exe"
  echo "built $ROOT/bin/$platform/$exe"
done
