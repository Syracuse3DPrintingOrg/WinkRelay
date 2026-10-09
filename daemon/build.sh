#!/usr/bin/env bash
# Build wink-relay-manager with this repo's patch applied.
#
#   daemon/build.sh [path-to-android-ndk-r22b]
#
# Clones the upstream source at the commit the patch was made against,
# applies wink-relay-manager-ha-sensors.patch, and builds it with Android NDK
# r22b (the toolchain the upstream 1.3 binary was built with). Without an NDK
# path it downloads r22b for Linux into daemon/.cache (about 1 GB unpacked).
# The result is daemon/out/wink_manager, an ARM binary for the Relay.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM=https://github.com/jimpastos/wink-relay-manager.git
COMMIT=f664e2a
NDK="${1:-}"

if [[ -z "$NDK" ]]; then
  NDK="$HERE/.cache/android-ndk-r22b"
  if [[ ! -x "$NDK/ndk-build" ]]; then
    mkdir -p "$HERE/.cache"
    echo "==> downloading Android NDK r22b"
    curl -fL -o "$HERE/.cache/ndk.zip" https://dl.google.com/android/repository/android-ndk-r22b-linux-x86_64.zip
    (cd "$HERE/.cache" && unzip -q ndk.zip && rm -f ndk.zip)
  fi
fi
[[ -x "$NDK/ndk-build" ]] || { echo "no ndk-build in $NDK" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> fetching upstream at $COMMIT"
git clone -q "$UPSTREAM" "$WORK/src"
git -C "$WORK/src" checkout -q "$COMMIT"
patch -d "$WORK/src" -p1 < "$HERE/wink-relay-manager-ha-sensors.patch"

echo "==> building"
(cd "$WORK/src" && "$NDK/ndk-build" NDK_APPLICATION_MK=./Application.mk NDK_PROJECT_PATH=. -j4)

mkdir -p "$HERE/out"
cp -f "$WORK/src/libs/armeabi-v7a/wink_manager" "$HERE/out/wink_manager"
file "$HERE/out/wink_manager" || true
md5sum "$HERE/out/wink_manager"
