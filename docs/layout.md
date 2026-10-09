# Home screen layouts

Everything under the fan dial is a layout: a list of rows. The page has one
built in (`variant.layout` in `panel/config.json`). It can also take one from
Home Assistant, so you can rearrange the screen without rebuilding the page.

## The layout

```jsonc
{
  "hero": {
    "type": "fan_dial",
    "fan": "fan.bathroom_fan",                          // fan.*
    "timer": "timer.bathroom_fan",                      // timer.*
    "script": "script.bathroom_fan_timer",              // script.*, takes `minutes`
    "steam": "input_number.bathroom_fan_steam_baseline" // input_number.* or null
  },
  "header": { "motion": "binary_sensor.bathroom_motion" }, // binary_sensor.* or null
  "rows": [ Row, ... ]                                  // 1 to 8 rows, top to bottom
}
```

The rows fill the space under the dial: **440 px** tall, with **12 px**
between rows. A row that does not fit is left out.

| Row `type` | Height | Fields |
|---|---|---|
| `fan_presets` | 68 | `minutes`: 1 to 3 different whole numbers, 1 to 120. Runs the hero's script for that long |
| `status` | 56 | `entity` (any), `label`, `icon`, `tap` (`none` or `toggle`). One wide line with the entity's state on the right |
| `sensors` | 104 | `cards`: exactly 2 sensor cards |
| `weather` | 176 | `entity` (weather.*), `label` (default "Outside"). Tap opens the forecast page |
| `tiles` | 68 | `tiles`: 1 to 3 tiles sharing the row |

A sensor card is `{ "kind", "entity", "label", "icon", "baseline", "rise" }`:

- `temperature`: a sensor in °F or °C; tap opens its 24 hour graph.
- `humidity`: a sensor in %; `baseline` and `rise` (or null) give the
  "usual" line and the note under the value. Tap opens the humidity page,
  which also shades the fan's runs.
- `sensor`: any numeric sensor; tap opens its 24 hour graph.

A tile is `{ "entity", "label", "icon", "tap" }`:

- `toggle`: for `light`, `switch`, `fan`, `input_boolean`. Lit while on.
- `run`: for `script`, `scene`, `button`, `input_button`.
- `none`: any entity; shows its state.

`label` is 1 to 24 characters of plain text. A missing entity shows "--", an
unknown icon a plain ring, an unknown row type is skipped: a bad layout
cannot break the dial or the settings.

## Icons

| Key | Key | Key | Key |
|---|---|---|---|
| `mirror` | `fan` | `motion` | `play` |
| `thermometer` | `power` | `speaker` | `bell` |
| `water-percent` | `toggle` | `television` | `flash` |
| `lightbulb` | `door` | `shower` | `gauge` |
| `ceiling-light` | `lock` | `sparkles` | `home` |
| `lamp` | `fire` | `moon` | `sun` |

## Sending a layout from Home Assistant

The page polls the sensor named in `variant.ent.layout`
(`sensor.bathroom_panel_layout` by default). Its state is a revision number
and its `layout` attribute the layout. When the revision changes, the page
redraws within about 10 seconds and keeps the layout for its next start.

Create the sensor once with a retained discovery message, attached to the
Relay's device (Developer tools > Actions, `mqtt.publish`):

```yaml
action: mqtt.publish
data:
  topic: homeassistant/sensor/winkrelay_bathroom/panel_layout/config
  retain: true
  payload: >-
    {"name": "Layout", "unique_id": "winkrelay_bathroom_panel_layout",
     "default_entity_id": "sensor.bathroom_panel_layout",
     "state_topic": "wink/panels/bathroom/layout",
     "value_template": "{{ '{{' }} value_json.rev {{ '}}' }}",
     "json_attributes_topic": "wink/panels/bathroom/layout",
     "json_attributes_template": "{{ '{{' }} {'layout': value_json.layout} | tojson {{ '}}' }}",
     "entity_category": "diagnostic",
     "device": {"identifiers": ["winkrelay_bathroom"]}}
```

Then publish a layout, retained, raising `rev` each time:

```yaml
action: mqtt.publish
data:
  topic: wink/panels/bathroom/layout
  retain: true
  payload: >-
    {"rev": 2, "layout": {"hero": {"type": "fan_dial", "fan": "fan.bathroom_fan",
     "timer": "timer.bathroom_fan", "script": "script.bathroom_fan_timer",
     "steam": "input_number.bathroom_fan_steam_baseline"},
     "header": {"motion": "binary_sensor.bathroom_motion"},
     "rows": [{"type": "fan_presets", "minutes": [10, 20, 40]},
              {"type": "tiles", "tiles": [
                {"entity": "light.bathroom_vanity", "label": "Vanity", "icon": "mirror", "tap": "toggle"},
                {"entity": "script.bathroom_all_off", "label": "All off", "icon": "power", "tap": "run"}]},
              {"type": "weather", "entity": "weather.home", "label": "Outside"}]}}
```

The `{{ '{{' }}` pieces keep Home Assistant from rendering the templates
meant for the sensor while it publishes. Keep a layout under about 1 KB.
To go back to the built-in layout for good, publish an empty retained
payload to both topics and set `ent.layout` to null in your config.
