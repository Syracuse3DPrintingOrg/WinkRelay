# 6. Sensors and calibration

Measured on one Relay, first on a bench and then in a bathroom wall. Treat
the numbers as one unit's and measure your own; the patterns held
throughout.

## Temperature and humidity (Si7020)

The daemon publishes raw readings. Correct them in Home Assistant with the
discovery script's `--humidity-offset` and `--temperature-offset` (points and
degrees C added to the raw value), so the correction can change without
touching the device.

Our unit read 21 points of humidity too high on the bench and 24.6 too high
in the wall box, and its temperature 0.6 °C low in the wall. Its slope was 1:
it followed the room point for point, so a plain offset is right. Yours will
differ.

**How to measure it.** Put a reference sensor (any decent hygrometer Home
Assistant can read) right next to the panel, leave the room empty, and let
Home Assistant record both for a whole night. Compare them minute by minute.
The average difference is your offset; the spread should be a few tenths.
Throw away the first half hour after moving anything.

**Where it stops matching.** The sensor is inside the wall box, behind the
glass, and that limits it:

- **It misses most of a shower.** With the exhaust fan running it hardly
  follows the room: a reference on the panel's face went from 57% to 84% in
  20 minutes while the panel held near 52%. Do not try to fit a slope
  through a shower: the fit we got (0.3) tripled every small rebound and
  switched the fan on and off every 10 to 15 minutes. If the fan matters,
  use a sensor in the room's air (see step 4).
- **Its offset drifts with the room.** One morning it read 3.6 points low,
  against 0.5 high the night before.
- **For about an hour after a shower it reads 1 to 3 points low.**
- **The backlight warms it.** The day brightness adds about 0.7 °C, and up to
  1.7 °C by the end of a shower. Take the temperature offset with the screen
  dark, since it is dark most of the time.

The daemon reports humidity on every 0.1% change, so a shower is a burst of
samples. That is why the fan package's baseline is time weighted
(`average_step`) rather than a median, which a burst drags up within minutes.

## Proximity (Si114x)

- It only sees a hand. A person standing at the panel changed the reading by
  nothing measurable; a hand about 10 cm from the glass added 1,500 to 2,100
  counts. Do not expect it to notice someone walking into the room: use a
  motion sensor through Home Assistant (the `Bathroom.PanelWake` automation).
- The resting level drifts with heat, about 1,400 counts over a night, so a
  fixed threshold is wrong within a day. The patch compares each reading with
  a slowly tracked resting level instead (`proximity_rise`,
  `proximity_follow`). Upstream's example `proximity_threshold=5000` sits far
  below our unit's resting level of about 43,000, which is why a stock
  Relay's screen never turns off.
- An exhaust fan's draught cools the sensor and the reading climbs about 10
  counts a second while it runs. `proximity_follow=64` lets the resting
  level keep up with that, so the fan starting does not wake the screen.
- Leave `send_proximity_trigger` off: it publishes twice a second for as long
  as anything is near.

To test wake logic without a person, inject a reading as root:
`sendevent /dev/input/event3 3 25 <value>` four times (channels A, B, C and
a 0), then `sendevent /dev/input/event3 0 0 0`.

## Ambient light (Si114x)

Raw counts, about 256 in the dark with about 20 counts of noise, and
occasional one second spikes to about 508 that mean nothing. Useful for
"is the light on", not for lux.

That is the whole setup. Optional next: [make it a Zigbee router](7-zigbee-router.md).
If something misbehaves, see [troubleshooting](troubleshooting.md).
