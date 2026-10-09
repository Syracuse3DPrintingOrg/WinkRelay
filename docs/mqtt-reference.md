# MQTT topics and daemon settings

`Relay` below is `mqtt_topic_prefix` from the config file.

## Topics the Relay publishes

| Topic | Payload | Notes |
|---|---|---|
| `Relay/relays/0/state`, `Relay/relays/1/state` | `ON` / `OFF` | Retained. 0 is LOAD 1 (upper) |
| `Relay/sensors/temperature` | float, °C | Retained, raw |
| `Relay/sensors/humidity` | float, % | Retained, raw, on every 0.1% change |
| `Relay/screen/state` | `ON` / `OFF` | With `send_screen_state=true` |
| `Relay/buttons/<0\|1>/click/<count>` | `ON` | The click count is in the topic. 0 is the upper button |
| `Relay/buttons/<0\|1>/held/<count>` | `ON` | |
| `Relay/buttons/<0\|1>/released/<count>` | `ON` | With flag 8 |
| `Relay/sensors/light`, `Relay/sensors/light_ir` | raw counts | Patch, `send_light_level=true` |
| `Relay/sensors/proximity`, `Relay/sensors/proximity_c` | raw counts | Patch, `send_proximity_level=true` |
| `Relay/proximity/state` | `ON` / `OFF` | Patch, `send_presence=true` |
| `Relay/status` | `online` / `offline` | Patch, `send_availability=true`; retained, with a last will |
| `Relay/proximity/trigger` | int | `send_proximity_trigger=true`; floods, leave it off |

## Topics the Relay listens to

| Topic | Payload | Notes |
|---|---|---|
| `Relay/relays/0`, `Relay/relays/1` | `ON`/`OFF`, `1`/`0`, `true`/`false` | Any case. An empty payload is ignored (patch) |
| `Relay/screen` | `ON` / `OFF` | Wakes the screen and restarts its timeout |
| `Relay/screen/brightness` | percent | Patch. Send retained; re-applied at every wake |
| `Relay/screen/timeout` | seconds | Patch. Send retained |
| `Relay/command/reboot`, `Relay/command/exit` | any | |

## Config file keys

`/sdcard/wink_manager.ini`. Keys marked "patch" need the patched build.

| Key | Default | What |
|---|---|---|
| `mqtt_address` | | `tcp://host:port` |
| `mqtt_username`, `mqtt_password` | | Broker login |
| `mqtt_clientid` | | |
| `mqtt_topic_prefix` | | `Relay` in this guide |
| `relay_upper_flags`, `relay_lower_flags` | | Button flags: 1 local toggle, 2 clicks, 4 holds, 8 releases, 16 grab touch, 32 toggle the other relay |
| `initial_relay_upper_state`, `initial_relay_lower_state` | keep | Relay state at start |
| `hide_status_bar` | false | |
| `screen_timeout` | | Seconds the screen stays on |
| `screen_brightness` | | Patch. Backlight percent until Home Assistant sends one |
| `send_screen_state` | false | |
| `proximity_threshold` | 5000 | Fixed "near" level; unused while `proximity_rise` is set |
| `proximity_rise` | | Patch. "Near" = this far above the tracked resting level (600 works) |
| `proximity_follow` | 256 | Patch. How fast the resting level follows: 1/N of the way per reading (two a second). 64 keeps up with a fan's draught |
| `send_proximity_trigger` | false | Leave off |
| `send_light_level`, `light_report_delta` | false, 32 | Patch |
| `send_proximity_level`, `proximity_report_delta` | false, 500 | Patch |
| `send_presence`, `presence_off_delay` | false, 5 | Patch. Seconds before presence goes off |
| `send_availability` | false | Patch. Status topic and last will |
| `relay_upper_max_on`, `relay_lower_max_on` | 0 | Patch. Seconds a relay may stay on without a fresh "on" command; 0 is no limit |
| `click_window_ms` | 150 | Patch. How long after a release to wait for a second press. A single press acts this much later |
| `temperature_threshold`, `humidity_threshold` | | Minimum change to report |
