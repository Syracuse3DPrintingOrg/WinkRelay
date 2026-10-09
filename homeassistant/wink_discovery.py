#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["paho-mqtt>=2"]
# ///
"""Publish (or remove) the MQTT Discovery configs that make a Wink Relay show
up in Home Assistant as one device.

  homeassistant/wink_discovery.py --ini my_wink_manager.ini
  homeassistant/wink_discovery.py --ini my_wink_manager.ini --remove
  homeassistant/wink_discovery.py --dry-run          # print, publish nothing

The broker address and login are read from a copy of the Relay's own
wink_manager.ini, so nothing secret has to be typed on the command line. Run
it with uv (which installs paho-mqtt on the fly) or with python3 after
`pip install paho-mqtt`.

With the defaults, the entities are named for a bathroom panel whose LOAD 1
runs the fan (fan.bathroom_fan, sensor.bathroom_humidity, ...), which is what
the Home Assistant packages and the wall page in this repo expect. Change
--slug, --name, --area and --load1 for another room or another load.
default_entity_id only applies when an entity is first registered; after
that Home Assistant's entity registry owns the id, so rename it there.

The light, proximity, presence and status topics need the patched daemon
(daemon/). Every entity except Connection goes unavailable when the Relay
drops off the broker, through the daemon's last will on <prefix>/status.
"""
import argparse
import json
import re
import sys


def snake(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def entities(a):
    P = a.prefix
    node = a.node or f"winkrelay_{a.slug}"
    avail = {"availability_topic": f"{P}/status", "payload_available": "online",
             "payload_not_available": "offline"}
    e = []

    def load(index, kind, name, eid):
        cfg = {
            "name": name,
            "unique_id": f"{node}_load_{index + 1}",
            "default_entity_id": f"{kind}.{eid}",
            "state_topic": f"{P}/relays/{index}/state",
            "command_topic": f"{P}/relays/{index}",
            "payload_on": "ON", "payload_off": "OFF",
            "optimistic": False, "qos": 1,
        }
        if kind == "light":
            cfg.update({"state_value_template": "{{ value }}"})
        return (kind, f"load_{index + 1}", cfg)

    e.append(load(0, a.load1, a.load1_name, f"{a.slug}_{snake(a.load1_name)}"))
    if a.load2 != "none":
        e.append(load(1, a.load2, a.load2_name, f"{a.slug}_panel_{snake(a.load2_name)}"))

    e.append(("sensor", "temperature", {
        "name": "Temperature",
        "unique_id": f"{node}_temperature",
        "default_entity_id": f"sensor.{a.slug}_temperature",
        "state_topic": f"{P}/sensors/temperature",
        "device_class": "temperature", "state_class": "measurement",
        "unit_of_measurement": "°C",
        "suggested_display_precision": 1,
        "value_template": "{{ (value | float + %s) | round(2) }}" % a.temperature_offset,
    }))
    e.append(("sensor", "humidity", {
        "name": "Humidity",
        "unique_id": f"{node}_humidity",
        "default_entity_id": f"sensor.{a.slug}_humidity",
        "state_topic": f"{P}/sensors/humidity",
        "device_class": "humidity", "state_class": "measurement",
        "unit_of_measurement": "%",
        "value_template": ("{{ ([[(value | float) + %s, 0] | max, 100] | min) | round(1) }}"
                           % a.humidity_offset),
    }))
    e.append(("binary_sensor", "screen", {
        "name": "Screen",
        "unique_id": f"{node}_screen",
        "default_entity_id": f"binary_sensor.{a.slug}_panel_screen",
        "state_topic": f"{P}/screen/state",
        "payload_on": "ON", "payload_off": "OFF",
        "device_class": "light", "entity_category": "diagnostic",
    }))
    # Si114x ambient light, raw counts (about 256 in the dark; not lux).
    e.append(("sensor", "light", {
        "name": "Light level",
        "unique_id": f"{node}_light",
        "default_entity_id": f"sensor.{a.slug}_panel_light_level",
        "state_topic": f"{P}/sensors/light",
        "state_class": "measurement",
        "icon": "mdi:brightness-6",
    }))
    e.append(("sensor", "light_ir", {
        "name": "Infrared level",
        "unique_id": f"{node}_light_ir",
        "default_entity_id": f"sensor.{a.slug}_panel_infrared_level",
        "state_topic": f"{P}/sensors/light_ir",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "icon": "mdi:led-outline",
    }))
    # Si114x proximity: channel A drives presence and the screen wake; C is the
    # other LED channel. Raw counts, reported only when they move.
    for ch, suffix in (("proximity", ""), ("proximity_c", " C")):
        e.append(("sensor", ch, {
            "name": "Proximity" + suffix,
            "unique_id": f"{node}_{ch}",
            "default_entity_id": f"sensor.{a.slug}_panel_{ch}",
            "state_topic": f"{P}/sensors/{ch}",
            "state_class": "measurement",
            "entity_category": "diagnostic",
            "icon": "mdi:signal-distance-variant",
        }))
    e.append(("binary_sensor", "presence", {
        "name": "Presence",
        "unique_id": f"{node}_presence",
        "default_entity_id": f"binary_sensor.{a.slug}_panel_presence",
        "state_topic": f"{P}/proximity/state",
        "payload_on": "ON", "payload_off": "OFF",
        "device_class": "occupancy",
    }))
    for _, _, payload in e:
        payload.update(avail)
    # This one reports availability, so it never goes unavailable itself.
    e.append(("binary_sensor", "connection", {
        "name": "Connection",
        "unique_id": f"{node}_connection",
        "default_entity_id": f"binary_sensor.{a.slug}_panel_connection",
        "state_topic": f"{P}/status",
        "payload_on": "online", "payload_off": "offline",
        "device_class": "connectivity", "entity_category": "diagnostic",
    }))
    # The buttons as device triggers (selectable in the automation editor).
    # Button 0 is the upper one. The daemon puts the click count in the topic
    # and always sends the payload "ON".
    for btn, label in ((0, "upper"), (1, "lower")):
        for action, clicks, ttype in (("click", 1, "button_short_press"),
                                      ("click", 2, "button_double_press"),
                                      ("held", 1, "button_long_press")):
            e.append(("device_automation", f"{label}_{action}_{clicks}", {
                "automation_type": "trigger",
                "topic": f"{P}/buttons/{btn}/{action}/{clicks}",
                "payload": "ON",
                "type": ttype,
                "subtype": f"{label}_button",
            }))
    device = {
        "identifiers": [node],
        "name": a.name,
        "manufacturer": "Wink",
        "model": "Relay PRLAY-WH01",
        "sw_version": "wink-relay-manager 1.3 + WinkRelay sensors patch",
        "suggested_area": a.area,
    }
    return node, device, e


def read_ini(path):
    cfg = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if "=" in line and not line.startswith((";", "#")):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ini", help="a copy of the Relay's wink_manager.ini (broker and login)")
    ap.add_argument("--prefix", default=None, help="mqtt_topic_prefix (default: from the ini, else Relay)")
    ap.add_argument("--discovery-prefix", default="homeassistant")
    ap.add_argument("--slug", default="bathroom", help="start of every entity id (default bathroom)")
    ap.add_argument("--name", default="Bathroom Panel", help="device name")
    ap.add_argument("--area", default="Bathroom")
    ap.add_argument("--node", default=None, help="device identifier (default winkrelay_<slug>)")
    ap.add_argument("--load1", choices=["fan", "light", "switch"], default="fan")
    ap.add_argument("--load1-name", default="Fan")
    ap.add_argument("--load2", choices=["fan", "light", "switch", "none"], default="switch")
    ap.add_argument("--load2-name", default="Load 2")
    ap.add_argument("--humidity-offset", type=float, default=0.0,
                    help="points added to the raw humidity (docs/6-sensors-and-calibration.md)")
    ap.add_argument("--temperature-offset", type=float, default=0.0,
                    help="degrees C added to the raw temperature")
    ap.add_argument("--remove", action="store_true", help="clear every config this publishes")
    ap.add_argument("--dry-run", action="store_true", help="print topics and payloads, publish nothing")
    a = ap.parse_args()

    cfg = read_ini(a.ini) if a.ini else {}
    a.prefix = a.prefix or cfg.get("mqtt_topic_prefix", "Relay")
    node, device, ents = entities(a)
    msgs = []
    for component, obj, payload in ents:
        topic = f"{a.discovery_prefix}/{component}/{node}/{obj}/config"
        msgs.append((topic, "" if a.remove else json.dumps({**payload, "device": device})))

    if a.dry_run:
        for topic, body in msgs:
            print(topic, body or "(empty: removes the entity)")
        return 0
    if not cfg.get("mqtt_address"):
        sys.exit("--ini with mqtt_address is needed to publish (or use --dry-run)")

    import paho.mqtt.client as mqtt
    m = re.match(r"^(?:tcp|mqtt)://([^:/]+)(?::(\d+))?", cfg["mqtt_address"])
    if not m:
        sys.exit("cannot read mqtt_address %r" % cfg["mqtt_address"])
    host, port = m.group(1), int(m.group(2) or 1883)
    cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{node}-discovery")
    if cfg.get("mqtt_username"):
        cl.username_pw_set(cfg["mqtt_username"], cfg.get("mqtt_password", ""))
    cl.connect(host, port, 30)
    cl.loop_start()
    for topic, body in msgs:
        cl.publish(topic, body, qos=1, retain=True).wait_for_publish(10)
        print(("removed   " if a.remove else "published ") + topic)
    cl.loop_stop()
    cl.disconnect()
    print(f"{'Removed' if a.remove else 'Published'} {len(msgs)} discovery configs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
