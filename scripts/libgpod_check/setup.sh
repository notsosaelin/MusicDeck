#!/bin/sh
# Builds gpodtool against libgpod 0.8.3 without installing anything: the Debian/Ubuntu packages are
# downloaded and unpacked into ~/gpod/root (no root needed), and an en_US locale made there for libgpod's
# sorting. For Ubuntu 26.04 (WSL works); package names may differ elsewhere.
set -e
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p ~/gpod && cd ~/gpod
apt-get download libgpod4t64 libgpod-dev libgpod-common libimobiledevice-1.0-6 libplist-2.0-4 \
    libusbmuxd-2.0-7 libimobiledevice-glue-1.0-0 libgio-2.0-dev libgdk-pixbuf-2.0-dev
for f in *.deb; do dpkg -x "$f" root; done
R=$PWD/root
mkdir -p tool locale
cc "$here/gpodtool.c" -o tool/gpodtool -I"$R/usr/include/gpod-1.0" -I"$R/usr/include/glib-2.0" \
    -I"$R/usr/lib/x86_64-linux-gnu/glib-2.0/include" -L"$R/usr/lib/x86_64-linux-gnu" \
    -l:libgpod.so.4 -l:libglib-2.0.so.0 -l:libgobject-2.0.so.0 -Wl,-rpath,"$R/usr/lib/x86_64-linux-gnu"
localedef -i en_US -f UTF-8 locale/en_US.UTF-8
echo "built ~/gpod/tool/gpodtool; now: python3 $here/check.py"
