#!/usr/bin/env bash
# Builds bin/spotify-helper as a fully static x86_64 Linux binary (musl), so it runs on SteamOS,
# Bazzite and other distros without depending on their libraries.
#
# Needs: rustup with the x86_64-unknown-linux-musl target, cargo-zigbuild, and zig on PATH.
#   rustup target add x86_64-unknown-linux-musl
#   cargo install --locked cargo-zigbuild
# Works from Linux or WSL.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/musicdeck-build/spotify-helper}"
cd "$ROOT/backend/spotify-helper"
CARGO_TARGET_DIR="$TARGET_DIR" cargo zigbuild --release --locked --target x86_64-unknown-linux-musl
mkdir -p "$ROOT/bin"
install -m 0755 "$TARGET_DIR/x86_64-unknown-linux-musl/release/spotify-helper" "$ROOT/bin/spotify-helper"
echo "built $ROOT/bin/spotify-helper"
