#!/usr/bin/env bash
# Build zbrouter and tcpser for the Relay with Android NDK r22b.
#
#   zigbee/build.sh [path-to-android-ndk-r22b]
#
# Without a path it uses the NDK daemon/build.sh downloaded into
# daemon/.cache. Results go to zigbee/out/. Built as PIE: a -static build
# aborts on Android 4.3 with a TLS alignment error.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NDK="${1:-$HERE/../daemon/.cache/android-ndk-r22b}"
CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi18-clang"
[[ -x "$CC" ]] || { echo "no NDK r22b at $NDK: run daemon/build.sh first, or pass its path" >&2; exit 1; }
mkdir -p "$HERE/out"
for p in zbrouter tcpser; do
  "$CC" -O2 -Wall -fPIE -pie -o "$HERE/out/$p" "$HERE/$p.c"
  echo "$HERE/out/$p"
done
