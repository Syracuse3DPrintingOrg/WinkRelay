#!/usr/bin/env bash
# Put a previous daemon back on the Wink Relay.
#
#   device/rollback-daemon.sh <relay-ip>          # the build before the last install
#   device/rollback-daemon.sh <relay-ip> stock    # the stock daemon from device/backup/<relay-ip>/
#
# "stock" also turns the Wink app back on.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
IP="${1:-}"
[[ -n "$IP" ]] || { echo "usage: $0 <relay-ip> [stock]" >&2; exit 2; }
D="$IP:5555"
adb connect "$D" >/dev/null
A=(adb -s "$D")
root() { "${A[@]}" shell "su -c '$1'"; }

root 'mount -o remount,rw /system'
root 'stop edisonwink'
if [[ "${2:-}" == stock ]]; then
  [[ -f "$HERE/backup/$IP/edisonwink.stock" ]] || { echo "no backup/$IP/edisonwink.stock" >&2; exit 1; }
  "${A[@]}" push "$HERE/backup/$IP/edisonwink.stock" /data/local/tmp/edisonwink.stock
  root 'cat /data/local/tmp/edisonwink.stock > /system/bin/edisonwink'
  root 'rm /data/local/tmp/edisonwink.stock'
  root 'pm enable com.quirky.android.wink.projectone' || true
else
  root 'cat /system/bin/edisonwink.prev > /system/bin/edisonwink'
fi
root 'chmod 755 /system/bin/edisonwink'
root 'mount -o remount,ro /system' || true
root 'start edisonwink'
echo "rolled back"
