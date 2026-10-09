#!/system/bin/sh
# Install, update or (with "remove") uninstall the wall panel boot hook on the
# Wink Relay. Runs on the device as root. From your computer:
#
#   adb push device/panel-boot.sh device/install-boot-hook.sh /data/local/tmp/
#   adb shell "su -c 'sh /data/local/tmp/install-boot-hook.sh http://<ha>:8123/local/<page>.html [adb-allow-address]'"
#   adb shell "su -c 'sh /data/local/tmp/install-boot-hook.sh remove'"
#
# The hook is one line appended to /system/bin/edison-senzinit, the Wink's own
# sensor set-up script that init runs as root once per boot (service wink_senz,
# one-shot, so init leaves the hook running after the script ends). The file is
# rewritten in place so it keeps its owner and mode, and a copy of the
# original is kept as /system/bin/edison-senzinit.orig the first time.
#
# When started over adb the command does not return while the hook runs (su
# keeps the session open); it is safe to interrupt it once "running as pid"
# has printed.

INIT=/system/bin/edison-senzinit
BOOT=/system/etc/wink-panel-boot.sh
CONF=/system/etc/wink-panel.conf
TMP=/data/local/tmp/senzinit.new
PIDFILE=/data/local/tmp/wink-panel-boot.pid
LINE="/system/bin/sh $BOOT < /dev/null > /dev/null 2>&1 &"

# Stop a running hook, but only if the pidfile still points at it: pids get
# reused after a reboot. The [.] keeps grep from matching itself.
stop_hook() {
  OLD=$(cat $PIDFILE 2>/dev/null)
  if [ -n "$OLD" ] && grep -q 'wink-panel-boot[.]sh' /proc/$OLD/cmdline 2>/dev/null; then
    kill "$OLD"
    sleep 1
    echo "stopped the running hook (pid $OLD)"
  fi
}

grep -q " /system .*rw," /proc/mounts || mount -o remount,rw /system

if [ "$1" = "remove" ]; then
  busybox sed '/ink.panel/d' $INIT > $TMP && cat $TMP > $INIT
  stop_hook
  rm -f $BOOT $CONF $TMP $PIDFILE
  echo "removed"
  exit 0
fi

if [ -z "$1" ]; then
  echo "usage: install-boot-hook.sh <panel page URL> [adb-allow-address] | remove"
  exit 2
fi

[ -f $INIT.orig ] || cat $INIT > $INIT.orig
echo "URL=$1" > $CONF
[ -n "$2" ] && echo "ADB_ALLOW=$2" >> $CONF
chmod 644 $CONF
cat /data/local/tmp/panel-boot.sh > $BOOT
chmod 755 $BOOT
if ! grep -q 'wink-panel-boot[.]sh' $INIT; then
  cat $INIT > $TMP
  echo "" >> $TMP
  echo "# Wink panel: Wi-Fi power save off, panel page kept in front" >> $TMP
  echo "$LINE" >> $TMP
  cat $TMP > $INIT
  rm -f $TMP
fi
grep -n 'ink.panel' $INIT

# A running hook keeps executing the code it started with, so replace it.
stop_hook
/system/bin/sh $BOOT < /dev/null > /dev/null 2>&1 &
sleep 1
echo "installed; running as pid $(cat $PIDFILE 2>/dev/null) (script $(busybox md5sum $BOOT | busybox cut -c1-8))"
