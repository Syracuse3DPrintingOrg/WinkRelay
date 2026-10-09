# 3. Install the daemon

The Relay's hardware is driven by `/system/bin/edisonwink`, which init
starts at boot and restarts if it dies. You replace it with
[wink-relay-manager](https://github.com/jimpastos/wink-relay-manager) 1.3,
built with this repo's patch.

## What the patch adds

Every addition is off unless its key is in the config file, so with a stock
upstream config the patched build behaves like upstream.

- The ambient light (visible and infrared) and proximity readings, as raw
  counts, and a presence on/off from the proximity sensor.
- An online/offline status topic with an MQTT last will, so Home Assistant
  knows when the Relay is gone.
- Screen wake on a hand near the glass, measured against a slowly tracked
  resting level instead of a fixed threshold (the fixed one never works for
  long: the sensor drifts with heat).
- Backlight brightness and screen timeout set from Home Assistant over MQTT.
- A time limit per relay: the Relay turns a load off by itself after N
  seconds on, so a fan switched on at the wall cannot run forever while Home
  Assistant or the broker is down.
- A configurable double press window (upstream's 150 ms is hard to hit).
- Fixes: a crash at 100% humidity (a 10 byte buffer overflowed, and a modern
  NDK aborts on that, so the daemon restarted in a loop in a steamy room); a
  thread race in the scheduler; a memory leak on every MQTT message received;
  an empty MQTT payload read as "on" (clearing a retained topic switched the
  load on); the screen state never reported after a restart.

Every key is listed in [the MQTT reference](mqtt-reference.md).

## Build it

```bash
daemon/build.sh
```

This clones upstream at the commit the patch was made against, applies the
patch, downloads Android NDK r22b if you do not pass the path of one, and
leaves the binary in `daemon/out/wink_manager`. Use r22b: a newer NDK fails
to build upstream's code.

## Write its config

```bash
cp daemon/wink_manager.ini.example my_wink_manager.ini
```

Fill in your broker's address and a login for the Relay. Give the Relay its
own broker user, not a Home Assistant user: the file sits on the Relay's
`/sdcard`, where any app on it can read it. The `.gitignore` here keeps
`*.ini` files out of git.

The button flags decide what each button does on its own:

| Flag | Meaning |
|---|---|
| 1 | Toggle this button's relay locally (works with Home Assistant down) |
| 2 | Report clicks (press, double press) |
| 4 | Report holds |
| 8 | Report releases |
| 16 | Grab touch input |
| 32 | Toggle the other relay instead |

The example uses 7 for the upper button (switches LOAD 1 and reports) and 6
for the lower one (reports only, so Home Assistant decides what it does: in
the bathroom package, it toggles a light on another switch).

## Install it

```bash
device/install-daemon.sh <relay-ip> my_wink_manager.ini
```

The script checks it is talking to a rooted Wink Relay, saves the stock
daemon to `device/backup/<relay-ip>/` (once; later runs never overwrite that
copy), pushes your config, disables the Wink app and its updater, installs
the new daemon and restarts it. The build it replaced stays on the Relay as
`/system/bin/edisonwink.prev`.

Check it is talking to your broker:

```bash
mosquitto_sub -h <broker> -u <user> -P <password> -v -t 'Relay/#'
```

Within a few seconds you should see `Relay/status online`, the relay states,
temperature and humidity. Press a button and its click appears.

## Roll back

```bash
device/rollback-daemon.sh <relay-ip>          # the build before the last install
device/rollback-daemon.sh <relay-ip> stock    # Wink's own daemon, and the Wink app back on
```

Next: [Connect it to Home Assistant](4-home-assistant.md).
