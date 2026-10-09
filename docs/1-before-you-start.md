# 1. Before you start

## What you need

- A Wink Relay (model PRLAY-WH01). This guide was written on one with board
  "Edison MB", an i.MX6 DualLite with 820 MB of RAM, running Android 4.3.
- A Home Assistant install with the MQTT integration and a broker
  (the Mosquitto add-on is the usual choice).
- A computer on the same network with `adb` (Android platform tools), `git`,
  Python 3 and, for building the daemon, about 2 GB free for the Android NDK.
  Linux or macOS work as written; on Windows, use WSL.
- A 2.4 GHz Wi-Fi network the Relay can join.

## Wiring and safety

The Relay replaces a light switch and switches mains voltage. If you are not
comfortable working in a switch box, have an electrician install it.

- Turn the breaker off before you open the box, and check the wires are dead.
- **A neutral wire is required.** The Relay's screen runs all the time, so it
  cannot steal power through the load the way a plain switch can.
- It does not work in a 3-way (two switch) circuit.
- Each relay is on or off only; there is no dimming.
- The ratings we worked from are 8 A (960 W) per output for general loads
  and 3 A (360 W) for incandescent lamps. Check the label on your unit and
  stay well under them. A bathroom exhaust fan or LED lighting is fine.
- The screen module clips off the back plate. It runs on 5 V from the back
  plate (pogo pads marked Rel1, 5V, GND, Rel2), so you can bench test the
  screen module with a 5 V supply and no mains at all. The relays will not
  switch anything that way, but everything in steps 2 to 5 works.

## How the pieces fit

Wink's own software (an Android app plus a native daemon,
`/system/bin/edisonwink`) is replaced by
[wink-relay-manager](https://github.com/jimpastos/wink-relay-manager), a
small native program that drives the relays, buttons, screen backlight and
sensors and talks MQTT. Home Assistant sees the Relay through MQTT discovery.

The screen shows a web page served by Home Assistant itself (from
`/config/www`), in Android's stock browser. The page talks to Home Assistant's
REST API with a token of its own. A boot hook keeps that page on screen.

Plan for about an afternoon if your Relay can already be reached over adb
with root, longer if you still need root (step 2).

Next: [Get into the Relay](2-get-into-the-relay.md).
