# 2. Get into the Relay

Everything after this step happens over adb on the network, with root. The
board has no USB port, so adb runs over Wi-Fi (TCP port 5555).

Read this whole page before you start. The hard part is root, and the usual
way to get it may no longer work.

## Never sign in to Wink

If the Relay offers a Wink login, skip it. Signing in has been reported to
pull firmware updates that turn adb off and undo this work. If yours has
already been signed in or updated, a factory reset (below) takes it back to
its original image.

## Join Wi-Fi

On first boot, or after a factory reset, the Relay's setup screen asks for
Wi-Fi before it asks for a Wink login. Join your 2.4 GHz network there, then
stop before the login.

If the Wink app is already in the way, open Android underneath it: cover the
proximity sensor (the small window at the top of the screen) with a finger
and hold the lower button until the relay has clicked about four times. That
brings up the Android home screen, and with it the normal Settings app for
Wi-Fi.

Give the Relay a DHCP reservation in your router now, so its address never
changes. Every script in this repo takes that address.

## Reach it with adb

From your computer:

```bash
adb connect <relay-ip>:5555
adb -s <relay-ip>:5555 shell getprop ro.build.version.release   # prints 4.3
```

On the unit this guide was written on, adb answered on the network with no
authorization prompt (`ro.secure=0`, `ro.debuggable=1`), but the shell runs
as an ordinary user, and `adb root` does nothing on this build. Some guides
say to tick "USB debugging" in Settings > Developer options first (untick and
tick it again if adb will not connect); do that if `adb connect` is refused.

## Root

The installer needs `su`. Check whether you already have it:

```bash
adb -s <relay-ip>:5555 shell "su -c id"
```

`uid=0(root)` means you are done with this step.

If not: for years the standard method was **KingRoot**. Install the KingRoot
APK with `adb install`, open it on the Relay's screen (from the Android home
screen above), and let it root. Its `su` then answers `su -c` over adb. Our
unit was rooted this way in 2021, and everything in this guide uses that `su`.

Be aware of the current state of things:

- In 2025 and 2026, several people reported that KingRoot stalls at 17% with
  "Network Error": its online service appears to be gone. Framaroot also
  fails. We have not found a confirmed root method for a fresh Relay since.
  If you find one, please open an issue or a pull request here.
- KingRoot is resident afterwards: it runs a few background services and
  phones home. Block the Relay's internet access at your router once it is
  rooted. Do not uninstall KingRoot unless you have replaced its `su` with
  another (SuperSU is the usual choice), or you lose root.
- People have reported Relays whose flash has worn out: changes revert on
  every reboot. KingRoot's heavy writing is blamed. There is no fix for that
  but another unit.
- A serial console is reported on four pads under the green D1 LED (top to
  bottom GND, RX, TX; 115200 8N1). It is the way back from a boot loop. We
  have not used it.

## If something goes wrong: factory reset

Take the screen module off the back plate, hold the upper button, and press
the reset button. In the recovery menu, wipe the cache, do a factory reset,
then "reset to factory image". This rolls back all of Wink's updates. It
also removes root, if the unit had it.

## Turn off what you do not need

`device/install-daemon.sh` (next step) disables the Wink app
(`com.quirky.android.wink.projectone`) and its updater
(`com.wink.winkupgrade`) for you. Note that the upstream README writes the
first one as `pm disable http://com.quirky.android.wink.projectone`: the
`http://` is a typo. Leaving the Wink app enabled shows its dead login screen
on the wall and keeps trying to reach servers that no longer exist.

Next: [Install the daemon](3-install-the-daemon.md).
