#!/system/bin/sh
# Wall panel boot hook for the Wink Relay (Android 4.3).
#
# Installed as /system/etc/wink-panel-boot.sh and started once per boot, as
# root, by one line appended to /system/bin/edison-senzinit: the Wink's own
# sensor set-up script, which init runs at boot as its one-shot wink_senz
# service. (install-recovery.sh, the usual spot, belongs to KingRoot and is
# write-protected.) Install and remove with install-boot-hook.sh, which also
# writes the settings this reads from /system/etc/wink-panel.conf:
#
#   URL=http://<home-assistant>:8123/local/<page>.html
#   ADB_ALLOW=<address of the one computer allowed to use adb>   (optional)
#
# Every 30 seconds it:
#  1. Keeps Wi-Fi power saving off. The Broadcom radio ships in power-save mode
#     (wl PM 2): it naps after 200 ms idle and only wakes on the access point's
#     beacons, which measured 128 ms average and up to 1.2 s round trips on the
#     LAN, against 23 ms with it off. Android turns power saving back on after
#     every DHCP renewal, so setting it once is not enough.
#  2. Keeps the panel page in front. After boot the home screen comes up first;
#     if the browser is ever closed or killed, the page comes back within 30 s.
#  3. Reopens the page when Home Assistant comes back after being unreachable.
#     The stock browser never retries a page that failed to load, so a panel
#     opened while Home Assistant (or Wi-Fi) was still starting would otherwise
#     sit on an error page for good.
#  4. Keeps /system/bin/zbrouter running, if it is installed: the host that
#     puts the Relay's Zigbee radio back on its network as a router after
#     every boot (docs/7-zigbee-router.md).
#
# Every open adds a throwaway ?b= query: Home Assistant serves /local/ with a
# 31 day cache lifetime, and the browser would otherwise keep showing an old
# copy of the page after it is updated. localStorage (the panel's token) is
# per origin, so it is not affected.
#
# Log: /data/local/tmp/wink-panel-boot.log (actions only, capped at 64 KB).

CONF=/system/etc/wink-panel.conf
URL=$(busybox sed -n 's/^URL=//p' $CONF 2>/dev/null)
ADB_ALLOW=$(busybox sed -n 's/^ADB_ALLOW=//p' $CONF 2>/dev/null)
# The scheme, host and port of the page: Home Assistant itself.
HA=$(echo "$URL" | busybox sed 's#^\(https*://[^/]*\).*#\1#')
PROBE=$HA/manifest.json
WL=/system/xbin/wl
LOG=/data/local/tmp/wink-panel-boot.log
PIDFILE=/data/local/tmp/wink-panel-boot.pid

log() {
  if [ -f $LOG ] && [ "$(busybox wc -c < $LOG)" -gt 65536 ]; then
    mv $LOG $LOG.old
  fi
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> $LOG
}

# One copy at a time, in case it is also started by hand. The pidfile survives
# a reboot and boot-time pids repeat, so check the pid is really this script.
# The [.] keeps grep from matching its own command line in /proc.
OLD=$(cat $PIDFILE 2>/dev/null)
if [ -n "$OLD" ] && [ "$OLD" != "$$" ] && grep -q 'wink-panel-boot[.]sh' /proc/$OLD/cmdline 2>/dev/null; then
  exit 0
fi
echo $$ > $PIDFILE
# Started by hand from an adb shell, keep running after that shell closes.
trap '' HUP

if [ -z "$URL" ]; then
  log "no URL= in $CONF, nothing to show"
  exit 1
fi

# adb on TCP is a root shell for anyone on the network. With ADB_ALLOW set,
# only that address may connect.
if [ -n "$ADB_ALLOW" ]; then
  iptables -D INPUT -p tcp --dport 5555 ! -s "$ADB_ALLOW" -j DROP 2>/dev/null
  iptables -I INPUT -p tcp --dport 5555 ! -s "$ADB_ALLOW" -j DROP && log "adb limited to $ADB_ALLOW"
fi

# Is Home Assistant answering? This busybox wget has no timeout option and
# can hang for minutes on an unreachable host, so give it 8 seconds.
ha_up() {
  busybox wget -q -s "$PROBE" > /dev/null 2>&1 &
  WP=$!
  n=0
  while kill -0 $WP 2> /dev/null; do
    n=$((n + 1))
    if [ $n -ge 8 ]; then
      kill $WP 2> /dev/null
      return 1
    fi
    sleep 1
  done
  wait $WP
}

ZB=/system/bin/zbrouter
ZBPID=/data/local/tmp/zbrouter.pid

# Start the Zigbee radio's host unless it is already running. Same pidfile
# check as above: boot-time pids repeat.
zb_check() {
  [ -x $ZB ] || return
  P=$(cat $ZBPID 2>/dev/null)
  if [ -n "$P" ] && grep -q 'zbrouter' /proc/$P/cmdline 2>/dev/null; then
    return
  fi
  $ZB < /dev/null > /dev/null 2>&1 &
  echo $! > $ZBPID
  log "zigbee router host started (pid $!)"
}

open_panel() {
  am start -a android.intent.action.VIEW -d "$URL?b=$(date +%s)" \
    -n com.android.browser/.BrowserActivity \
    --es com.android.browser.application_id wink-panel > /dev/null 2>&1
  log "panel opened ($1)"
}

until [ "$(getprop sys.boot_completed)" = "1" ]; do
  sleep 2
done
sleep 10
log "boot hook started"

# Android dims the backlight to 2% a minute after the last touch, so a screen
# woken by motion or by Home Assistant came on barely lit. The daemon owns the
# backlight level (Relay/screen/brightness); Android is told never to dim.
if [ "$(settings get system screen_off_timeout)" != "2147483647" ]; then
  settings put system screen_off_timeout 2147483647
  log "android idle dimming turned off"
fi

# Home Assistant counts as unreachable only after two failed probes in a row:
# on marginal Wi-Fi a single probe fails now and then, and each false alarm
# would reload the panel for nothing.
WAS_UP=1
FAILS=0
while true; do
  zb_check
  case "$($WL PM 2>/dev/null)" in
    0*) ;;
    *) $WL PM 0 && log "wifi power save turned off" ;;
  esac

  if ha_up; then FAILS=0; else FAILS=$((FAILS + 1)); fi
  if [ $FAILS -ge 2 ]; then UP=0; else UP=1; fi
  if ! dumpsys activity activities | grep -q "mFocusedActivity.*com.android.browser"; then
    open_panel "browser was not in front"
  elif [ $UP = 1 ] && [ $WAS_UP = 0 ]; then
    open_panel "Home Assistant is reachable again"
  fi
  [ $UP = 0 ] && [ $WAS_UP = 1 ] && log "Home Assistant not reachable"
  WAS_UP=$UP

  sleep 30
done
