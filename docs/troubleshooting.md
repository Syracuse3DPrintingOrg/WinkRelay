# Troubleshooting

## Working on the Relay's shell

- `adb root` does nothing; use `su -c`. Give the device one string, or the
  quoting falls apart: `adb shell "su -c 'command here'"`.
- The shell has no `head`, `tail` or `wc`. Busybox, if present, has `sed`,
  `wc` and `md5sum`. For anything longer than a line, push a script to
  `/data/local/tmp` and run it with `su -c 'sh /data/local/tmp/x.sh'`.
- KingRoot's `su` keeps the adb session open while anything it started is
  still running, so starting a background job over adb never returns. Press
  Ctrl+C after it has started.

## The screen is slow to respond

Almost always Wi-Fi power saving, not the CPU. Ping the Relay 30 times: a
healthy result is 2 to 10 ms; averages over 100 ms with spikes near a second
mean power saving is on. The boot hook turns it off every 30 seconds; check
it is running (`/data/local/tmp/wink-panel-boot.log`). As root,
`/system/xbin/wl PM` prints `0` when it is off. Also check the signal with
`wl rssi`: a metal wall box costs a lot on 2.4 GHz.

The load average reads 2.0 even when idle. That is two display kernel
threads that always count as busy, not real work.

## The page shows "No token" or "Token rejected"

Run `device/provision-token.py` again with a valid token. After a rejection
the page waits five minutes between tries.

## The page shows an old version

It checks for a new copy every 10 minutes and reloads itself. The boot hook
also opens it with a fresh query at every boot.

## The screen is black but the backlight is on

Something else holds the screen. Check what is in front:
`adb shell "dumpsys activity | grep mPausingActivity"`. On our unit a stuck
KingRoot notification once blocked the browser; force stopping did not clear
it, a reboot did.

## A woken screen is very dim

Android dims the backlight to 2% a minute after the last touch when nothing
it counts as a user touched the screen. The boot hook stops that by setting
`screen_off_timeout` to its maximum; check the hook ran at boot.

## The clock on the page is hours off

Android's time zone was never set. Run `device/setup-browser.sh` with a
time zone, or `setprop persist.sys.timezone <Area/City>` as root, and reboot.

## The daemon will not stay running

`adb shell "su -c 'logcat -d'"` and look for `edisonwink`. A build from a
newer NDK than r22b, or an unpatched upstream build in a humid room, can
crash at start. Roll back with `device/rollback-daemon.sh <relay-ip>`.

## Changes vanish after a reboot

If every change you make to `/system` or `/data` is gone after a reboot, the
flash has likely worn out and become read-only. This has been reported on
several Relays and there is no known fix.

## The fan turns on by itself after a shower

Check that the humidity offset was measured with the room quiet and the fan
off, never through a shower (step 6). A steep correction turns every small
rebound into a big one.
