# 4. Connect it to Home Assistant

## Publish the device

`homeassistant/wink_discovery.py` publishes retained MQTT discovery messages
that turn the Relay's topics into one Home Assistant device. It reads the
broker and login from your copy of the Relay's config:

```bash
homeassistant/wink_discovery.py --ini my_wink_manager.ini --dry-run   # look first
homeassistant/wink_discovery.py --ini my_wink_manager.ini
```

It runs with [uv](https://docs.astral.sh/uv/), which fetches paho-mqtt for
you; or `pip install paho-mqtt` and run it with `python3`.

The defaults suit the bathroom fan setup in this repo:

| Entity | What |
|---|---|
| `fan.bathroom_fan` | LOAD 1 |
| `switch.bathroom_panel_load_2` | LOAD 2 |
| `sensor.bathroom_temperature`, `sensor.bathroom_humidity` | The Si7020 sensor (raw unless you give offsets, see step 6) |
| `sensor.bathroom_panel_light_level`, `_infrared_level` | Ambient light, raw counts (about 256 in the dark; not lux) |
| `sensor.bathroom_panel_proximity`, `_proximity_c` | Raw proximity, two channels (diagnostic) |
| `binary_sensor.bathroom_panel_presence` | A hand at the panel |
| `binary_sensor.bathroom_panel_screen` | Backlight on or off |
| `binary_sensor.bathroom_panel_connection` | The Relay is connected to the broker |
| 6 device triggers | Upper and lower button: press, double press, long press |

For another room or another load, change the options: `--slug kitchen
--name "Kitchen Panel" --area Kitchen --load1 light --load1-name Ceiling`
gives `light.kitchen_ceiling`, `sensor.kitchen_humidity` and so on. Run it
with `--help` for the full list. The ids are only suggestions to Home
Assistant on first sight; after that, rename entities in Home Assistant.
`--remove` takes every entity away again.

Every entity except Connection goes unavailable when the Relay drops off the
broker.

## Load the packages

The two files in `homeassistant/packages/` hold the helpers, template
sensors, scripts and automations the dashboard and the fan logic use.

1. If your `configuration.yaml` does not load packages yet, add:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Copy `wink_panel.yaml` (always) and `wink_bathroom_fan.yaml` (for a
   bathroom fan) into `/config/packages/`.
3. In each file, replace `binary_sensor.bathroom_motion` and
   `light.bathroom_vanity` with your own motion sensor and light. If you have
   no motion sensor, delete the automations that use it.
4. Check the configuration (Developer tools > YAML > Check configuration),
   then restart Home Assistant.
5. Set the helpers' starting values once (Settings > Devices & services >
   Helpers): the comments next to each helper in the packages give the values
   we use. The packages leave out `initial` on purpose, so a restart never
   puts a setting back.

### What the panel package does

| | |
|---|---|
| Display helpers | Display mode (Auto, Day, Night), theme, day and night backlight, screen timeout, light look by day |
| `binary_sensor.bathroom_panel_night` | Night: always in Night mode, never in Day mode, sunset to sunrise in Auto |
| `Bathroom.PanelDisplay` | Sends the Relay its day or night backlight level and screen timeout (retained MQTT) |
| `Bathroom.PanelWake` | Wakes the screen with motion or the light, and keeps it on while the room is in use. The Relay's own proximity sensor only notices a hand near the glass, never a person walking in |
| `Bathroom.PanelOffline` | A notification when the Relay has been offline for 10 minutes, taken back when it returns |
| `script.bathroom_panel_alert` | Puts an alert on the panel (`level` alarm or info, `title`, `message`, `tag`, `minutes`) and wakes the screen. Call it from your own automations, for example a leak sensor or the dryer finishing |

### What the bathroom fan package does

| | |
|---|---|
| `Bathroom.FanRuntime` | Every fan run gets an end time in `timer.bathroom_fan` (the default length for a plain switch-on, including the wall button), and the fan goes off when it runs out. Puts the fan and the timer back in step after a restart or a lost command |
| `Bathroom.FanSteam` | Fan on when humidity has stayed 5 points above the last hour's average for a minute. Off once it has been within 3 points of where it started for 20 minutes, never before 30 minutes, at most 60. Handles the rebound after a shower and a second shower |
| `Bathroom.PanelButtons` | Upper: press = fan (switched by the Relay itself), double press = everything off. Lower: press = vanity light on or off (dim at night), double press = vanity between dim and full |
| `Bathroom.VanityMotion` | Vanity on with motion (dim at night), off 500 s after motion stops |
| `script.bathroom_fan_timer` | Run the fan for `minutes`: the dashboard's dial and quick buttons call it |
| `script.bathroom_all_off` | Fan and vanity off |

The fan goes by `sensor.bathroom_room_humidity`, which points at the Relay's
own humidity reading. That sensor is inside the wall box and misses most of a
shower once the fan is running (step 6 has the numbers). It works, but late.
A cheap Bluetooth hygrometer in the room's air does much better: change the
one line in "Bathroom Room Humidity" to use it.

## Security

The Relay's adb port is a root shell for anyone on your network, and its
config file holds your broker password. Limit who can reach it: the boot hook
in step 5 can restrict adb to one address, and your router can keep the
Relay off the internet entirely. Nothing in this setup needs internet access.

Next: [Put the dashboard on the wall](5-wall-dashboard.md).
