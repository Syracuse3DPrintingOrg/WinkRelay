# 7. Optional: make it a Zigbee router

The Relay has a Zigbee radio that Wink used for its own hub features. Other
guides treat it as unusable. It isn't: it can join the Zigbee network you
already run (Zigbee2MQTT or ZHA) as a router. The Relay is powered all the
time, so it strengthens the mesh for your battery devices. Your Zigbee
coordinator sees it as a Wink "Wink Relay", mains powered, of type Router.

This does not move the Relay's own relays, buttons or sensors to Zigbee.
They stay on MQTT over Wi-Fi, which the dashboard needs anyway. The radio
only routes for other devices.

We did this on one Relay and joined it to a Zigbee2MQTT network on a Z-Stack
coordinator. It has survived reboots since. Joining ZHA should work the same
way, but we have not tried it.

## What is in there

| | |
|---|---|
| Chip | Silicon Labs Ember network coprocessor, EmberZNet 5.1.2, EZSP protocol 4 |
| Port | `/dev/ttymxc1`, 57600 baud, software flow control |
| Wink's own driver | `com.flex.edisonservice`, which talks to the chip at every boot |

## 1. Build the programs

```bash
daemon/build.sh          # if you have not: it also fetches the NDK
zigbee/build.sh
```

This gives `zigbee/out/zbrouter`, the program that runs on the Relay for
good, and `zigbee/out/tcpser`, a bridge used only for the one-time join.

## 2. Turn off Wink's Zigbee service

```bash
adb shell "su -c 'pm disable com.flex.edisonservice'"
```

It drives the radio at every boot and could set up Wink's old network again
on top of yours.

## 3. Join your network, once

The join runs from your computer with [bellows](https://github.com/zigpy/bellows),
the library behind Home Assistant's own Zigbee support. The radio is reached
through a bridge that listens only on the Relay itself, tunnelled over adb,
so it is never open to your network.

```bash
adb push zigbee/out/tcpser /data/local/tmp/
adb shell "su -c 'chmod 755 /data/local/tmp/tcpser; /data/local/tmp/tcpser /dev/ttymxc1 57600 6638 &'"
adb forward tcp:16638 tcp:6638
```

The second command may not return, because KingRoot's `su` waits for the
bridge; press Ctrl+C, and the bridge keeps running.

Find your network's channel and PAN id (Zigbee2MQTT: Settings > Advanced).
Check the radio can see it:

```bash
zigbee/join_router.py --channel 25 --pan-id 0x1A2B --scan-only
```

Open permit join on your coordinator for a few minutes, then:

```bash
zigbee/join_router.py --channel 25 --pan-id 0x1A2B
```

It should end with `NETWORK_UP` and `JOINED_NETWORK`. The script runs with
[uv](https://docs.astral.sh/uv/), which installs bellows for it. Close permit
join, then remove the bridge:

```bash
adb forward --remove tcp:16638
adb shell "su -c 'busybox pkill tcpser; rm /data/local/tmp/tcpser'"
```

Wink's network is still stored on the radio until this join replaces it.
That is expected.

## 4. Install the program that keeps it there

After every reset (a reboot, a power cut) the radio stays off the network
until something tells it to resume. `zbrouter` does that, and more:
- it resets the radio, registers the Relay as a Zigbee range extender, and
  resumes the stored network
- it answers the coordinator's questions about what the device is (this is
  how the interview learns "Wink", "Wink Relay" and mains power)
- it checks every 30 seconds that the Relay is still on the network, and
  resumes if not

```bash
adb push zigbee/out/zbrouter /data/local/tmp/
adb shell "su -c 'mount -o remount,rw /system; cat /data/local/tmp/zbrouter > /system/bin/zbrouter; chmod 755 /system/bin/zbrouter; mount -o remount,ro /system'"
```

The boot hook from chapter 5 starts it and restarts it within 30 seconds if it
stops. If you installed the hook before this chapter existed, install it
again to pick up that part. The program logs to
`/data/local/tmp/zbrouter.log`; run it with `-v` to log every frame.

In Zigbee2MQTT, interview the device again (its page, or the
`bridge/request/device/interview` request) so it picks up the name.

## Undo it

Delete `/system/bin/zbrouter` (the hook skips it when it is missing), remove
the device from your coordinator, and re-enable `com.flex.edisonservice` if
you want Wink's software back.

## Details that cost time

- The radio answers at 57600 baud with XON/XOFF; at 115200 it is silent, and
  with RTS/CTS the port hangs.
- bellows' own `join` command joins as an end device; the script here joins
  as a router.
- Each router answers a network scan, so your network appears several times.
  The script picks it by PAN id.
- A `-static` build aborts on this Android with a TLS alignment error; the
  build script makes position independent executables instead.
- The protocol notes (ASH framing, the EZSP 4 frame ids and layouts) are in
  the comments of `zigbee/zbrouter.c`.
