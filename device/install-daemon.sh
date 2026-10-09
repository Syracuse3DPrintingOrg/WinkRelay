#!/usr/bin/env bash
# Install the patched wink-relay-manager (daemon/out/wink_manager) on a rooted
# Wink Relay, in place of the stock /system/bin/edisonwink.
#
#   device/install-daemon.sh <relay-ip> [path/to/wink_manager.ini]
#
# Before touching anything it pulls the stock daemon into
# device/backup/<relay-ip>/ (once; a later run never overwrites that copy) and
# keeps the binary it replaces on the device as /system/bin/edisonwink.prev.
# With an ini file it also pushes that as /sdcard/wink_manager.ini, after
# pulling the old one into the backup folder. It disables the stock Wink app
# and its updater, then restarts the daemon (no reboot needed).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
IP="${1:-}"
INI="${2:-}"
BIN="$HERE/../daemon/out/wink_manager"
[[ -n "$IP" ]] || { echo "usage: $0 <relay-ip> [wink_manager.ini]" >&2; exit 2; }
[[ -f "$BIN" ]] || { echo "no $BIN: run daemon/build.sh first" >&2; exit 1; }

D="$IP:5555"
adb connect "$D" >/dev/null
A=(adb -s "$D")
root() { "${A[@]}" shell "su -c '$1'"; }

[[ "$("${A[@]}" get-state 2>/dev/null)" == device ]] || { echo "adb cannot reach $D" >&2; exit 1; }
root id | grep -q 'uid=0' || { echo "no root on $D: see docs/2-get-into-the-relay.md" >&2; exit 1; }
root 'ls /system/bin/edisonwink' | grep -q edisonwink || { echo "$D does not look like a Wink Relay" >&2; exit 1; }

BK="$HERE/backup/$IP"
mkdir -p "$BK"
if [[ ! -f "$BK/edisonwink.stock" ]]; then
  echo "==> backing up the stock daemon to $BK"
  root 'cp /system/bin/edisonwink /sdcard/edisonwink.stock'
  "${A[@]}" pull /sdcard/edisonwink.stock "$BK/edisonwink.stock"
  root 'rm /sdcard/edisonwink.stock'
fi

if [[ -n "$INI" ]]; then
  "${A[@]}" pull /sdcard/wink_manager.ini "$BK/wink_manager.ini.$(date +%Y%m%d%H%M%S)" >/dev/null 2>&1 || true
  chmod 600 "$BK"/wink_manager.ini.* 2>/dev/null || true
  "${A[@]}" push "$INI" /sdcard/wink_manager.ini
fi

echo "==> disabling the stock Wink app and its updater"
for p in com.quirky.android.wink.projectone com.wink.winkupgrade; do
  root "pm disable $p" || echo "    ($p not present)"
done

echo "==> installing"
"${A[@]}" push "$BIN" /data/local/tmp/wink_manager
root 'mount -o remount,rw /system'
root 'stop edisonwink'
root 'cp /system/bin/edisonwink /system/bin/edisonwink.prev'
root 'cat /data/local/tmp/wink_manager > /system/bin/edisonwink'
root 'chmod 755 /system/bin/edisonwink'
root 'rm /data/local/tmp/wink_manager'
root 'mount -o remount,ro /system' || true
root 'start edisonwink'
sleep 3
root 'ps' | grep -q edisonwink && echo "==> running" || echo "!! edisonwink is not running: see docs/troubleshooting.md"
