#!/usr/bin/env python3
"""Offline test harness for the bathroom wall panel page.

  python3 panel/harness.py build/bathroom-panel.html <outdir>

Loads the page at a fake origin with Home Assistant's REST API mocked (nothing
live is touched), in a 480x800 touch viewport like the Wink Relay, and for each
scenario writes <outdir>/<scenario>.png plus <outdir>/report.json with:
  - page errors and console errors
  - whether the page scrolls (it must fit 480x800 exactly)
  - whether anything is animating (two frames 600 ms apart differ)
  - every API request the page made
  - tap tests: what the fan button and the run-for presets send
  - an ES5 parse check of every inline script (acorn, ecmaVersion 5)
  - every POST path and body per scenario, and a "checks" map (name -> pass) of
    every done-means item in the briefs, summarised at the end of the output

Scenarios: idle, timed, steam, offline, notoken, rejected (every request gets
a 401), pages (detail pages), night (night look in Ember, plus
night_humidity/night_settings/night_fan shots), alert (an alarm, its
dismissal, then an info alert arriving while settings is open, and a water
alarm shot), settings (both tabs, one tap of each control kind including the
"Light screen by day" switch, the re-read, the dial using the new fan length,
the 15 s return), day_dark (day with the light switch off: the dark day look),
and with HARNESS_LOOKS=1, looks (home with the fan running in each theme in
the light, dark day and night looks, plus the light detail pages and settings
per theme, and two contact sheets). The day scenarios run with the light
switch on, its default, so they show the light look. The settings services
(input_select, input_number, input_boolean, input_datetime, input_text)
change the mock's state as HA would; the fan script and fan/turn_off do not.

The home rows come from a layout (docs/layout.md). The scenarios above run
with no layout sensor and nothing cached, so they show the built-in bathroom
layout. The layout scenarios: variant (the layout sensor serves VARIANT: presets
10 and 20, a status row that toggles a switch, a humidity card and a plain
sensor card, and two tiles rows with toggle, run and show-only tiles, no
weather; every kind of tap, a double tap, the detail pages for the tapped
cards), variant_dark and variant_night (the same in the dark day and night
looks), rev_change (a new revision rebuilds the rows without a reload; the same
revision, new attributes on it and an invalid layout change nothing),
layout_edge (a bad cached layout, then a layout with an unknown row type, a
label with markup, an unknown icon, a missing entity, messy presets, no motion,
no usual level and a row that does not fit, then an invalid one, then every
label at 24 characters with the vanity lit), cached (the
variant from localStorage with no sensor: polled entities, quiet DOM, and the
self-update on Playwright's fake clock) and preview (the page as a layout
editor frames it, window.PANEL_PREVIEW set, requests to the preview API base).

HARNESS_ONLY=a,b runs just those scenarios (the checks cover what ran).

HARNESS_BASELINE=<dir> compares day_dark.png with <dir>/idle.png from a run of
the page before the light look, outside the clock and the forecast hours, to
show the dark day look is unchanged.

Page contract the harness relies on:
  - relative API paths: GET /api/states/<entity_id>, POST /api/services/<domain>/<service>
  - token from localStorage key "bathroomPanelToken", provisioned via #token=
  - main fan button carries data-tap="fan"; presets carry data-tap="run" data-min="<minutes>"
  - the gear carries data-tap="page" data-page="settings" and opens #page-settings; tabs
    carry data-tap="tab" data-tab="screen|room"; every settings control carries
    data-tap="set" data-entity data-value (a switch's data-value is the value a tap sets),
    time steppers data-tap="step" data-entity data-delta="30|-30"
  - the alert banner is #alert (class "alarm" or "info" while shown), data-tap="alert"
  - the home rows are the .row children of #rows; sensor cards carry data-tap="page"
    data-page="temp|humidity", the weather card data-page="weather"; a toggle tile or a
    status row that toggles carries data-tap="toggle" data-entity, a run tile
    data-tap="trigger" data-entity; the open detail page's caption is #tpL or #hpL
  - the layout cache is localStorage "bathroomPanelLayout" ({rev, layout}); preview
    mode reads window.PANEL_PREVIEW = {api, layout} and takes postMessage
    {type: "panel-layout", layout}
"""
import json, os, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from io import BytesIO

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
# acorn does the ES5 check (npm install in panel/). If headless Chromium
# cannot start for want of system libraries, run `npx playwright install-deps`,
# or unpack them somewhere and point HS_LIBS at them.
LIBS = os.environ.get('HS_LIBS', os.path.expanduser('~/.cache/hs-libs/usr/lib/x86_64-linux-gnu'))
ACORN = os.environ.get('ACORN', os.path.join(HERE, 'node_modules', 'acorn'))
ORIGIN = 'http://panel.test'
# On while self_update() runs: the server has a new copy of the page, so every
# load of it from then on carries the new version stamp.
NEW_COPY = {'on': False}
URL = ORIGIN + '/local/bathroom-panel.html'
LAYOUT_SENSOR = 'sensor.bathroom_panel_layout'
MOTION = 'binary_sensor.bathroom_motion'
PREVIEW_PAGE = '/panel-preview/bathroom_panel'
PREVIEW_API = '/api/panels/bathroom_panel/ha/'
# The contract's bathroom layout, the page's built-in default.
DEFAULT_LAYOUT = {
    'hero': {'type': 'fan_dial', 'fan': 'fan.bathroom_fan', 'timer': 'timer.bathroom_fan',
             'script': 'script.bathroom_fan_timer', 'steam': 'input_number.bathroom_fan_steam_baseline'},
    'header': {'motion': MOTION},
    'rows': [
        {'type': 'fan_presets', 'minutes': [15, 30, 60]},
        {'type': 'status', 'entity': 'light.bathroom_vanity', 'label': 'Vanity light', 'icon': 'mirror', 'tap': 'none'},
        {'type': 'sensors', 'cards': [
            {'kind': 'temperature', 'entity': 'sensor.bathroom_temperature', 'label': 'Temperature', 'icon': 'thermometer', 'baseline': None, 'rise': None},
            {'kind': 'humidity', 'entity': 'sensor.bathroom_humidity', 'label': 'Humidity', 'icon': 'water-percent',
             'baseline': 'sensor.bathroom_humidity_baseline', 'rise': 'sensor.bathroom_humidity_rise'}]},
        {'type': 'weather', 'entity': 'weather.home', 'label': 'Outside'}]}
VARIANT = {
    'hero': DEFAULT_LAYOUT['hero'], 'header': DEFAULT_LAYOUT['header'],
    'rows': [
        {'type': 'fan_presets', 'minutes': [10, 20]},
        {'type': 'status', 'entity': 'switch.bathroom_towel_warmer', 'label': 'Towel warmer', 'icon': 'fire', 'tap': 'toggle'},
        {'type': 'sensors', 'cards': [
            {'kind': 'humidity', 'entity': 'sensor.bathroom_humidity', 'label': 'Humidity', 'icon': 'water-percent',
             'baseline': 'sensor.bathroom_humidity_baseline', 'rise': 'sensor.bathroom_humidity_rise'},
            {'kind': 'sensor', 'entity': 'sensor.bathroom_heater_power', 'label': 'Heater power', 'icon': 'flash', 'baseline': None, 'rise': None}]},
        {'type': 'tiles', 'tiles': [
            {'entity': 'light.bathroom_ceiling', 'label': 'Ceiling', 'icon': 'ceiling-light', 'tap': 'toggle'},
            {'entity': 'script.bathroom_shower_mode', 'label': 'Shower mode', 'icon': 'shower', 'tap': 'run'},
            {'entity': 'binary_sensor.bathroom_door', 'label': 'Door', 'icon': 'door', 'tap': 'none'}]},
        {'type': 'tiles', 'tiles': [
            {'entity': 'input_boolean.bathroom_guest_mode', 'label': 'Guest mode', 'icon': 'bell', 'tap': 'toggle'},
            {'entity': 'scene.bathroom_relax', 'label': 'Relax', 'icon': 'sparkles', 'tap': 'run'}]}]}
EDGE = {
    'hero': dict(DEFAULT_LAYOUT['hero'], steam=None), 'header': {'motion': None},
    'rows': [
        {'type': 'clock'},
        {'type': 'status', 'entity': 'sensor.bathroom_missing', 'label': 'Door <b>x</b>', 'icon': 'nope', 'tap': 'toggle'},
        {'type': 'fan_presets', 'minutes': [15, 15, 0, 200, 45.5, '30', 45]},
        {'type': 'weather', 'entity': 'weather.home'},
        {'type': 'sensors', 'cards': [
            {'kind': 'temperature', 'entity': 'sensor.bathroom_temperature', 'label': 'Temperature', 'icon': 'thermometer', 'baseline': None, 'rise': None},
            {'kind': 'humidity', 'entity': 'sensor.bathroom_humidity', 'label': 'Humidity', 'icon': 'water-percent', 'baseline': None, 'rise': None}]},
        # 56 + 68 + 176 + 104 and three gaps fill the 440 px: this one does not fit.
        {'type': 'tiles', 'tiles': [{'entity': 'light.bathroom_ceiling', 'label': 'Ceiling', 'icon': 'ceiling-light', 'tap': 'toggle'}]}]}
# Every label at the longest the contract allows (24 characters), in a layout
# that fills the 440 px.
LONG_LABEL = 'Downstairs bathroom fans'
LONG_LABELS = {
    'hero': DEFAULT_LAYOUT['hero'], 'header': DEFAULT_LAYOUT['header'],
    'rows': [
        dict(DEFAULT_LAYOUT['rows'][1], label=LONG_LABEL),
        {'type': 'sensors', 'cards': [dict(c, label=LONG_LABEL) for c in DEFAULT_LAYOUT['rows'][2]['cards']]},
        dict(DEFAULT_LAYOUT['rows'][3], label=LONG_LABEL),
        {'type': 'tiles', 'tiles': [dict(t, label=LONG_LABEL) for t in VARIANT['rows'][3]['tiles']]}]}
LAYOUT_SCENARIOS =('variant', 'variant_dark', 'variant_night', 'rev_change', 'layout_edge', 'cached', 'preview')
# What the variant layout polls: the dial, motion, the alert, the look, the layout
# sensor and its rows' entities, nothing else.
VARIANT_ENTS = {'fan.bathroom_fan', 'timer.bathroom_fan', 'input_number.bathroom_fan_steam_baseline', MOTION,
                'input_text.bathroom_panel_alert', 'binary_sensor.bathroom_panel_night', 'input_select.bathroom_panel_theme',
                'input_boolean.bathroom_panel_day_light', 'input_number.bathroom_fan_default_minutes', LAYOUT_SENSOR,
                'switch.bathroom_towel_warmer', 'sensor.bathroom_humidity', 'sensor.bathroom_humidity_rise',
                'sensor.bathroom_heater_power', 'light.bathroom_ceiling', 'script.bathroom_shower_mode',
                'binary_sensor.bathroom_door', 'input_boolean.bathroom_guest_mode', 'scene.bathroom_relax'}


def layout_attrs(layout):
    return {'layout': layout, 'saved_at': '2026-09-24T17:00:00.000Z', 'icon': 'mdi:view-dashboard-edit',
            'friendly_name': 'Bathroom Panel Layout'}


def iso(dt):
    return dt.isoformat()


def st(eid, state, attrs=None, changed_ago=600):
    now = datetime.now(timezone.utc)
    t = iso(now - timedelta(seconds=changed_ago))
    return {'entity_id': eid, 'state': state, 'attributes': attrs or {},
            'last_changed': t, 'last_updated': t, 'last_reported': t,
            'context': {'id': 'x', 'parent_id': None, 'user_id': None}}


def scenario_states(name):
    now = datetime.now(timezone.utc)
    base = {
        'sensor.bathroom_humidity': st('sensor.bathroom_humidity', '71.6', {'unit_of_measurement': '%', 'device_class': 'humidity', 'friendly_name': 'Bathroom Panel Humidity'}),
        'sensor.bathroom_temperature': st('sensor.bathroom_temperature', '72.14', {'unit_of_measurement': '°F', 'device_class': 'temperature', 'friendly_name': 'Bathroom Panel Temperature'}),
        'sensor.bathroom_humidity_rise': st('sensor.bathroom_humidity_rise', '-1.4', {'unit_of_measurement': '%', 'friendly_name': 'Bathroom Humidity Rise'}),
        'sensor.bathroom_humidity_baseline': st('sensor.bathroom_humidity_baseline', '73.0', {'unit_of_measurement': '%'}),
        'input_number.bathroom_fan_steam_baseline': st('input_number.bathroom_fan_steam_baseline', '0.0', {'min': 0, 'max': 100, 'step': 0.1, 'unit_of_measurement': '%'}),
        'fan.bathroom_fan': st('fan.bathroom_fan', 'off', {'friendly_name': 'Bathroom Panel Fan', 'supported_features': 48}),
        'timer.bathroom_fan': st('timer.bathroom_fan', 'idle', {'duration': '0:30:00', 'editable': True, 'restore': True, 'icon': 'mdi:fan-clock', 'friendly_name': 'Bathroom Fan'}),
        'light.bathroom_vanity': st('light.bathroom_vanity', 'off', {'supported_color_modes': ['brightness'], 'color_mode': None, 'brightness': None, 'friendly_name': 'Bathroom Vanity'}),
        'binary_sensor.bathroom_motion': st('binary_sensor.bathroom_motion', 'off', {'device_class': 'motion', 'friendly_name': 'Bathroom Motion'}),
        'weather.home': st('weather.home', 'partlycloudy', {'temperature': 53, 'dew_point': 49, 'temperature_unit': '°F', 'humidity': 89, 'cloud_coverage': 67, 'uv_index': 0, 'pressure': 30.1, 'pressure_unit': 'inHg', 'wind_bearing': 59.0, 'wind_speed': 6.03, 'wind_speed_unit': 'mph', 'visibility_unit': 'mi', 'precipitation_unit': 'in', 'attribution': 'Weather forecast from met.no, delivered by the Norwegian Meteorological Institute.', 'friendly_name': 'Forecast Home', 'supported_features': 3}),
        # The panel's look and quick settings, and its alert line.
        'binary_sensor.bathroom_panel_night': st('binary_sensor.bathroom_panel_night', 'off', {'friendly_name': 'Bathroom Panel Night'}),
        'input_select.bathroom_panel_display_mode': st('input_select.bathroom_panel_display_mode', 'Auto', {'options': ['Auto', 'Day', 'Night']}),
        'input_select.bathroom_panel_theme': st('input_select.bathroom_panel_theme', 'Neon', {'options': ['Neon', 'Ocean', 'Ember', 'Mono']}),
        'input_number.bathroom_panel_day_brightness': st('input_number.bathroom_panel_day_brightness', '80.0', {'min': 10, 'max': 100, 'step': 5, 'mode': 'slider', 'unit_of_measurement': '%'}),
        'input_number.bathroom_panel_night_brightness': st('input_number.bathroom_panel_night_brightness', '15.0', {'min': 1, 'max': 60, 'step': 1, 'mode': 'slider', 'unit_of_measurement': '%'}),
        'input_number.bathroom_panel_screen_timeout': st('input_number.bathroom_panel_screen_timeout', '60.0', {'min': 15, 'max': 600, 'step': 5, 'mode': 'box', 'unit_of_measurement': 's'}),
        'input_boolean.bathroom_vanity_night_dim': st('input_boolean.bathroom_vanity_night_dim', 'on', {}),
        'input_number.bathroom_vanity_night_level': st('input_number.bathroom_vanity_night_level', '10.0', {'min': 1, 'max': 50, 'step': 1, 'mode': 'slider', 'unit_of_measurement': '%'}),
        'input_datetime.bathroom_vanity_night_start': st('input_datetime.bathroom_vanity_night_start', '23:30:00', {'has_date': False, 'has_time': True, 'hour': 23, 'minute': 30, 'second': 0, 'timestamp': 84600}),
        'input_datetime.bathroom_vanity_night_end': st('input_datetime.bathroom_vanity_night_end', '07:00:00', {'has_date': False, 'has_time': True, 'hour': 7, 'minute': 0, 'second': 0, 'timestamp': 25200}),
        'input_number.bathroom_fan_default_minutes': st('input_number.bathroom_fan_default_minutes', '30.0', {'min': 5, 'max': 60, 'step': 5, 'mode': 'slider', 'unit_of_measurement': 'min'}),
        'input_boolean.bathroom_fan_steam_auto': st('input_boolean.bathroom_fan_steam_auto', 'on', {}),
        'input_boolean.bathroom_fan_vanity_link': st('input_boolean.bathroom_fan_vanity_link', 'on', {}),
        'input_text.bathroom_panel_alert': st('input_text.bathroom_panel_alert', '', {'min': 0, 'max': 255, 'pattern': None, 'mode': 'text'}),
        'input_boolean.bathroom_panel_day_light': st('input_boolean.bathroom_panel_day_light', 'on', {'friendly_name': 'Bathroom Panel Light Screen By Day'}),
        # Only the layout scenarios show these.
        'switch.bathroom_towel_warmer': st('switch.bathroom_towel_warmer', 'off', {'friendly_name': 'Towel Warmer'}),
        'sensor.bathroom_heater_power': st('sensor.bathroom_heater_power', '1486.2', {'unit_of_measurement': 'W', 'device_class': 'power', 'state_class': 'measurement'}),
        'light.bathroom_ceiling': st('light.bathroom_ceiling', 'on', {'supported_color_modes': ['brightness'], 'color_mode': 'brightness', 'brightness': 204}),
        'script.bathroom_shower_mode': st('script.bathroom_shower_mode', 'off', {'friendly_name': 'Shower Mode'}),
        'binary_sensor.bathroom_door': st('binary_sensor.bathroom_door', 'off', {'device_class': 'door'}),
        'input_boolean.bathroom_guest_mode': st('input_boolean.bathroom_guest_mode', 'off', {}),
        'scene.bathroom_relax': st('scene.bathroom_relax', '2026-09-23T21:14:03.120442+00:00', {'friendly_name': 'Relax'}),
    }
    if name in ('variant', 'variant_dark', 'variant_night'):
        base[LAYOUT_SENSOR] = st(LAYOUT_SENSOR, '5', layout_attrs(VARIANT))
    elif name == 'rev_change':
        base[LAYOUT_SENSOR] = st(LAYOUT_SENSOR, '1', layout_attrs(DEFAULT_LAYOUT))
    if name == 'variant_dark':
        base['input_boolean.bathroom_panel_day_light']['state'] = 'off'
        base['switch.bathroom_towel_warmer']['state'] = 'on'
        base['binary_sensor.bathroom_door']['state'] = 'on'
    if name == 'variant_night':
        base['binary_sensor.bathroom_panel_night']['state'] = 'on'
        base['switch.bathroom_towel_warmer']['state'] = 'on'
        base['input_boolean.bathroom_guest_mode']['state'] = 'on'
    if name == 'day_dark':
        base['input_boolean.bathroom_panel_day_light']['state'] = 'off'
    if name == 'night':
        base['binary_sensor.bathroom_panel_night']['state'] = 'on'
        base['input_select.bathroom_panel_theme']['state'] = 'Ember'
    elif name == 'alert':
        base['input_text.bathroom_panel_alert']['state'] = 'alarm|Smoke|Smoke detected in the kitchen'
    if name in ('timed', 'looks'):
        base['fan.bathroom_fan'] = st('fan.bathroom_fan', 'on', {'supported_features': 48}, 379)
        base['timer.bathroom_fan'] = st('timer.bathroom_fan', 'active', {'duration': '0:30:00', 'remaining': '0:30:00', 'finishes_at': iso(now + timedelta(minutes=23, seconds=41)), 'editable': True, 'restore': True}, 379)
        base['light.bathroom_vanity'] = st('light.bathroom_vanity', 'on', {'supported_color_modes': ['brightness'], 'color_mode': 'brightness', 'brightness': 229})
        base['binary_sensor.bathroom_motion'] = st('binary_sensor.bathroom_motion', 'on', {'device_class': 'motion'}, 20)
        base['sensor.bathroom_humidity']['state'] = '74.0'
        base['sensor.bathroom_humidity_rise']['state'] = '1.2'
    elif name == 'steam':
        base['fan.bathroom_fan'] = st('fan.bathroom_fan', 'on', {'supported_features': 48}, 470)
        base['timer.bathroom_fan'] = st('timer.bathroom_fan', 'active', {'duration': '1:00:00', 'remaining': '1:00:00', 'finishes_at': iso(now + timedelta(minutes=52, seconds=10)), 'editable': True, 'restore': True}, 470)
        base['input_number.bathroom_fan_steam_baseline']['state'] = '76.2'
        base['sensor.bathroom_humidity']['state'] = '88.4'
        base['sensor.bathroom_humidity_rise']['state'] = '11.8'
        base['light.bathroom_vanity'] = st('light.bathroom_vanity', 'on', {'supported_color_modes': ['brightness'], 'color_mode': 'brightness', 'brightness': 255})
    return base


SCENARIOS = ['idle', 'timed', 'steam', 'offline', 'notoken', 'rejected', 'pages', 'night', 'alert', 'settings', 'day_dark',
             'variant', 'variant_dark', 'variant_night', 'rev_change', 'layout_edge', 'cached', 'preview']
# The day scenarios, which show the light look with the switch at its default.
LIGHT_SCENARIOS = ('idle', 'timed', 'steam', 'offline', 'notoken', 'rejected', 'pages', 'alert', 'settings',
                   'variant', 'rev_change', 'layout_edge', 'cached', 'preview')
# HARNESS_LOOKS=1 adds 'looks': every theme in the light, dark day and night looks.
if os.environ.get('HARNESS_LOOKS'):
    SCENARIOS.append('looks')
if os.environ.get('HARNESS_ONLY'):
    SCENARIOS = [n for n in os.environ['HARNESS_ONLY'].split(',') if n]

SETTINGS_ENTITIES = [
    'binary_sensor.bathroom_panel_night', 'input_select.bathroom_panel_display_mode', 'input_select.bathroom_panel_theme',
    'input_number.bathroom_panel_day_brightness', 'input_number.bathroom_panel_night_brightness',
    'input_number.bathroom_panel_screen_timeout', 'input_boolean.bathroom_vanity_night_dim',
    'input_number.bathroom_vanity_night_level', 'input_datetime.bathroom_vanity_night_start',
    'input_datetime.bathroom_vanity_night_end', 'input_number.bathroom_fan_default_minutes',
    'input_boolean.bathroom_fan_steam_auto', 'input_boolean.bathroom_fan_vanity_link',
    'input_boolean.bathroom_panel_day_light']
ALERT = 'input_text.bathroom_panel_alert'
DAY_LIGHT = 'input_boolean.bathroom_panel_day_light'


def apply_service(states, path, body):
    """The panel's settings services, and the toggles a layout's tiles send, change the
    mock's state the way HA would (the fan script and fan/turn_off do not: those tests
    rely on the page's optimistic state). Picking Day or Night also moves the night
    sensor, as HA's template does."""
    srv = path.split('?')[0][len('/api/services/'):]
    try:
        data = json.loads(body or '{}')
    except ValueError:
        return
    s = states.get(data.get('entity_id'))
    if s is None:
        return
    new = {'input_select/select_option': lambda: data.get('option'),
           'input_number/set_value': lambda: '%.1f' % float(data.get('value')),
           'input_boolean/turn_on': lambda: 'on', 'input_boolean/turn_off': lambda: 'off',
           'input_datetime/set_datetime': lambda: data.get('time'),
           'input_text/set_value': lambda: data.get('value')}.get(srv)
    if srv.split('/')[1:] == ['toggle'] and srv.split('/')[0] in ('light', 'switch', 'fan', 'input_boolean'):
        new = lambda: 'off' if s['state'] == 'on' else 'on'
    if new is None:
        return
    set_state(states, data['entity_id'], new())
    if data['entity_id'] == 'input_select.bathroom_panel_display_mode' and data.get('option') in ('Day', 'Night'):
        set_state(states, 'binary_sensor.bathroom_panel_night', 'on' if data['option'] == 'Night' else 'off')


def set_state(states, eid, value, attrs=None):
    s = states[eid]
    t = iso(datetime.now(timezone.utc))
    if s['state'] != value:
        s['last_changed'] = t
    s['state'], s['last_updated'] = value, t
    if attrs is not None:
        s['attributes'] = attrs


def visible_targets(page):
    """Every visible [data-tap] element as 'kind+detail:WxH'."""
    return page.evaluate("""Array.prototype.filter.call(document.querySelectorAll('[data-tap]'), function(e){
        var r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'})
      .map(function(e){var r=e.getBoundingClientRect();
        return e.getAttribute('data-tap')+(e.getAttribute('data-page')||e.getAttribute('data-min')||e.getAttribute('data-tab')||
          ((e.getAttribute('data-entity')||'').split('.')[1]||'')+(e.getAttribute('data-value')!==null?'='+e.getAttribute('data-value'):'')+
          (e.getAttribute('data-delta')?'/'+e.getAttribute('data-delta'):''))+':'+Math.round(r.width)+'x'+Math.round(r.height)})""")


def small_targets(targets, min_w, min_h):
    out = []
    for t in targets:
        w, h = t.rsplit(':', 1)[1].split('x')
        if int(w) < min_w or int(h) < min_h:
            out.append(t)
    return out


def avoid_minute_edge(page):
    """The clock repaints once a minute; keep a 600 ms animation check off the minute
    (the page's clock follows HA's Date header, up to a second off this one)."""
    s = time.time() % 60
    if s > 57.5 or s < 2.2:
        page.wait_for_timeout(int(((62.4 - s) % 60) * 1000))


def frames_differ(page, gap=600):
    avoid_minute_edge(page)
    a = page.screenshot()
    page.wait_for_timeout(gap)
    b = page.screenshot()
    diff = ImageChops.difference(Image.open(BytesIO(a)).convert('RGB'), Image.open(BytesIO(b)).convert('RGB'))
    return sum(1 for px in diff.getdata() if px != (0, 0, 0))


def post_list(calls, since):
    return [{'path': c['path'], 'body': c['body']} for c in calls[since:]
            if c['method'] == 'POST' and 'get_forecasts' not in c['path']]


def same_posts(got, want):
    """want: [(path, body dict)]; bodies compare as JSON."""
    if not isinstance(got, list) or len(got) != len(want):
        return False
    for g, (p, b) in zip(got, want):
        try:
            if g['path'] != p or json.loads(g['body'] or 'null') != b:
                return False
        except ValueError:
            return False
    return True


def page_state(page):
    return page.evaluate("""(function(){function vis(id){var e=document.getElementById(id);return !!e&&e.offsetWidth>0}
        var pages=['temp','humidity','weather','settings'].filter(function(n){return vis('page-'+n)});
        var a=document.getElementById('alert');
        return {home_visible: vis('home'), pages_visible: pages,
                alert_class: a ? a.className : null, alert_visible: !!a && a.offsetWidth>0,
                html_class: document.documentElement.className,
                fits: document.documentElement.scrollHeight<=800 && document.documentElement.scrollWidth<=480}})()""")


def settings_test(page, calls, outdir, tap, states):
    """Open the gear, render both tabs, tap one control of each kind and check the
    service calls, the re-read, and that the dial then uses the new fan length."""
    out = {'taps': {}}
    before = len(calls)
    out['open_posts'] = tap('[data-tap="page"][data-page="settings"]')
    page.wait_for_timeout(1000)
    out['opened'] = page_state(page)
    got = {c['path'][len('/api/states/'):] for c in calls[before:] if c['method'] == 'GET' and c['path'].startswith('/api/states/')}
    out['missing_gets_on_open'] = [e for e in SETTINGS_ENTITIES if e not in got]
    page.screenshot(path=os.path.join(outdir, 'page_settings_screen.png'))
    out['animated_pixels_changed_in_600ms'] = frames_differ(page)
    out['targets_screen'] = visible_targets(page)
    tap('[data-tap="tab"][data-tab="room"]')
    page.wait_for_timeout(300)
    out['room'] = page_state(page)
    page.screenshot(path=os.path.join(outdir, 'page_settings_room.png'))
    out['targets_room'] = visible_targets(page)
    out['small_targets'] = small_targets(out['targets_screen'] + out['targets_room'], 64, 56)

    def sel(eid, value=None, kind='set', delta=None):
        s = f'[data-tap="{kind}"][data-entity="{eid}"]'
        if value is not None:
            s += f'[data-value="{value}"]'
        if delta is not None:
            s += f'[data-delta="{delta}"]'
        return s

    svc = '/api/services/'
    tap('[data-tap="tab"][data-tab="screen"]')

    def html_class():
        return page.evaluate('document.documentElement.className')

    def tap_now(s):
        """Tap and read the look 150 ms later, then let the POST land."""
        n = len(calls)
        b = page.locator(s).first.bounding_box()
        page.touchscreen.tap(b['x'] + b['width'] / 2, b['y'] + b['height'] / 2)
        page.wait_for_timeout(150)
        cls = html_class()
        page.wait_for_timeout(1050)
        return post_list(calls, n), cls

    # "Light screen by day" is on by default: one tap sends turn_off and the
    # page turns dark at once (still day, so the dark day look).
    s = sel(DAY_LIGHT)
    dl = {'class_before': html_class(),
          'data_value_before': page.evaluate(f"document.querySelector('{s}').getAttribute('data-value')")}
    dl['posts'], dl['class_at_once'] = tap_now(s)
    dl['ok'] = same_posts(dl['posts'], [(svc + 'input_boolean/turn_off', {'entity_id': DAY_LIGHT})])
    dl['data_value_after'] = page.evaluate(f"document.querySelector('{s}').getAttribute('data-value')")
    dl['switch_lit_after'] = page.evaluate(f"document.querySelector('{s}').className.indexOf(' lit')>=0")
    out['taps']['day_light_off'] = dl
    page.screenshot(path=os.path.join(outdir, 'page_settings_screen_daydark.png'))
    plan = [
        ('mode_night', sel('input_select.bathroom_panel_display_mode', 'Night'),
         [(svc + 'input_select/select_option', {'entity_id': 'input_select.bathroom_panel_display_mode', 'option': 'Night'})]),
        ('theme_ocean', sel('input_select.bathroom_panel_theme', 'Ocean'),
         [(svc + 'input_select/select_option', {'entity_id': 'input_select.bathroom_panel_theme', 'option': 'Ocean'})]),
        ('day_brightness_100', sel('input_number.bathroom_panel_day_brightness', '100'),
         [(svc + 'input_number/set_value', {'entity_id': 'input_number.bathroom_panel_day_brightness', 'value': 100})]),
        ('timeout_120', sel('input_number.bathroom_panel_screen_timeout', '120'),
         [(svc + 'input_number/set_value', {'entity_id': 'input_number.bathroom_panel_screen_timeout', 'value': 120})]),
    ]
    for key, s, want in plan:
        got, cls = tap_now(s)
        out['taps'][key] = {'posts': got, 'ok': same_posts(got, want), 'class_at_once': cls,
                            'highlighted': page.evaluate(f"document.querySelector('{s}').className.indexOf(' on')>=0")}
    page.wait_for_timeout(1800)
    out['after_screen'] = page_state(page)
    page.screenshot(path=os.path.join(outdir, 'page_settings_screen_after.png'))
    tap('[data-tap="tab"][data-tab="room"]')
    vd = 'input_boolean.bathroom_vanity_night_dim'
    toggle_value = page.evaluate(f"document.querySelector('{sel(vd)}').getAttribute('data-value')")
    plan = [
        ('vanity_toggle', sel(vd), [(svc + 'input_boolean/turn_off', {'entity_id': vd})]),
        ('start_plus', sel('input_datetime.bathroom_vanity_night_start', kind='step', delta='30'),
         [(svc + 'input_datetime/set_datetime', {'entity_id': 'input_datetime.bathroom_vanity_night_start', 'time': '00:00:00'})]),
        ('fan_45', sel('input_number.bathroom_fan_default_minutes', '45'),
         [(svc + 'input_number/set_value', {'entity_id': 'input_number.bathroom_fan_default_minutes', 'value': 45})]),
    ]
    for key, s, want in plan:
        got = tap(s)
        out['taps'][key] = {'posts': got, 'ok': same_posts(got, want)}
    out['taps']['vanity_toggle']['data_value_before'] = toggle_value
    out['taps']['vanity_toggle']['data_value_after'] = page.evaluate(f"document.querySelector('{sel(vd)}').getAttribute('data-value')")
    last = len(calls)
    page.wait_for_timeout(1500)
    got = {c['path'][len('/api/states/'):] for c in calls[last:] if c['method'] == 'GET' and c['path'].startswith('/api/states/')}
    out['missing_rereads_after_change'] = [e for e in SETTINGS_ENTITIES if e not in got]
    out['after_room'] = page_state(page)
    out['start_time_shown'] = page.evaluate("document.getElementById('sv-vstart').textContent")
    page.screenshot(path=os.path.join(outdir, 'page_settings_room_after.png'))
    out['mock_after'] = {e: states[e]['state'] for e in SETTINGS_ENTITIES}
    tap('[data-tap="back"]')
    page.wait_for_timeout(800)
    out['after_back'] = page_state(page)
    got = tap('[data-tap="fan"]')
    out['dial_after'] = {'posts': got, 'ok': same_posts(got, [(svc + 'script/turn_on', {'entity_id': 'script.bathroom_fan_timer', 'variables': {'minutes': 45}})])}
    out['posts'] = post_list(calls, before)
    # Settings is a subpage like the others: 15 s without a touch returns home.
    page.wait_for_timeout(2500)
    tap('[data-tap="page"][data-page="settings"]')
    out['idle_return'] = {'opened': page_state(page)}
    page.wait_for_timeout(16000)
    out['idle_return']['after_16s'] = page_state(page)
    return out


def alert_test(page, calls, outdir, tap, states):
    """An alarm shows at load; one tap clears it in HA and hides it at once. Then with
    settings open an info alert arrives: the page goes home and shows it, calm."""
    out = {}
    out['alarm'] = page_state(page)
    out['alarm_box'] = page.locator('#alert').bounding_box()
    before = len(calls)
    b = out['alarm_box']
    page.touchscreen.tap(b['x'] + b['width'] / 2, b['y'] + b['height'] / 2)
    page.wait_for_timeout(120)
    out['hidden_at_once'] = not page_state(page)['alert_visible']
    page.wait_for_timeout(1100)
    want = [('/api/services/input_text/set_value', {'entity_id': ALERT, 'value': ''})]
    got = post_list(calls, before)
    out['dismiss'] = {'posts': got, 'ok': same_posts(got, want)}
    page.wait_for_timeout(2500)
    out['still_hidden'] = not page_state(page)['alert_visible']
    tap('[data-tap="page"][data-page="settings"]')
    out['settings_open'] = page_state(page)
    set_state(states, ALERT, 'info|Dryer|The dryer finished at 9:42 PM')
    page.wait_for_timeout(3000)
    out['info'] = page_state(page)
    page.screenshot(path=os.path.join(outdir, 'alert_info.png'))
    out['info_animated_pixels'] = frames_differ(page)
    out['targets'] = visible_targets(page)
    before = len(calls)
    got = tap('#alert')
    out['info_dismiss'] = {'posts': got, 'ok': same_posts(got, want), 'hidden': not page_state(page)['alert_visible']}
    # A water leak, to see its icon.
    set_state(states, ALERT, 'alarm|Water leak|Water under the washing machine')
    page.wait_for_timeout(3000)
    page.screenshot(path=os.path.join(outdir, 'alert_water.png'))
    out['water'] = page_state(page)
    return out


def night_extras(page, calls, outdir, tap, states):
    """Night look beyond home: a chart page, settings, and home with the fan running."""
    out = {}
    tap('[data-tap="page"][data-page="humidity"]')
    page.wait_for_timeout(1500)
    page.screenshot(path=os.path.join(outdir, 'night_humidity.png'))
    tap('[data-tap="back"]')
    tap('[data-tap="page"][data-page="settings"]')
    page.wait_for_timeout(800)
    page.screenshot(path=os.path.join(outdir, 'night_settings.png'))
    out['settings_animated_pixels'] = frames_differ(page)
    tap('[data-tap="back"]')
    now = datetime.now(timezone.utc)
    set_state(states, 'fan.bathroom_fan', 'on', {'supported_features': 48})
    set_state(states, 'timer.bathroom_fan', 'active', {'duration': '0:30:00', 'finishes_at': iso(now + timedelta(minutes=18, seconds=12))})
    page.wait_for_timeout(3000)
    page.screenshot(path=os.path.join(outdir, 'night_fan.png'))
    out['fan'] = page_state(page)
    return out


def contact_sheet(outdir, rows, dest, scale=0.5, gap=8):
    """rows: lists of PNG names in outdir; one sheet, scaled, on a mid grey."""
    ims = [[Image.open(os.path.join(outdir, n)).convert('RGB') for n in r] for r in rows]
    w, h = int(480 * scale), int(800 * scale)
    cols = max(len(r) for r in ims)
    sheet = Image.new('RGB', (cols * w + (cols + 1) * gap, len(ims) * h + (len(ims) + 1) * gap), (128, 128, 128))
    for y, r in enumerate(ims):
        for x, im in enumerate(r):
            sheet.paste(im.resize((w, h), Image.LANCZOS), (gap + x * (w + gap), gap + y * (h + gap)))
    sheet.save(os.path.join(outdir, dest))


def looks_test(page, outdir, tap):
    """Every theme in the light, dark day and night looks, set through settings, with
    the fan running: look_<theme>_<light|daydark|night>.png, and for the light look
    also the humidity and weather pages and settings (look_<theme>_light_<what>.png).
    looks_sheet.png has the themes across and light, dark day and night down;
    looks_light_sheet.png has the themes down and home, humidity, weather and
    settings across."""
    out = {}
    themes = ('Neon', 'Ocean', 'Ember', 'Mono')
    switch = f'[data-tap="set"][data-entity="{DAY_LIGHT}"]'

    def settings(*sels):
        tap('[data-tap="page"][data-page="settings"]')
        for s in sels:
            # A switch shows the value a tap sets, so a missing one is already right.
            if page.locator(s).count():
                tap(s)

    def home(name):
        tap('[data-tap="back"]')
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(outdir, name))
        return page.evaluate('document.documentElement.className')

    for theme in themes:
        t = theme.lower()
        settings(f'[data-tap="set"][data-value="{theme}"]', '[data-tap="set"][data-value="Day"]', switch + '[data-value="on"]')
        page.screenshot(path=os.path.join(outdir, f'look_{t}_light_settings.png'))
        out[f'{theme}_light'] = home(f'look_{t}_light.png')
        for pg in ('humidity', 'weather'):
            tap(f'[data-tap="page"][data-page="{pg}"]')
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(outdir, f'look_{t}_light_{pg}.png'))
            tap('[data-tap="back"]')
        settings(switch + '[data-value="off"]')
        out[f'{theme}_daydark'] = home(f'look_{t}_daydark.png')
        settings('[data-tap="set"][data-value="Night"]')
        out[f'{theme}_night'] = home(f'look_{t}_night.png')
    contact_sheet(outdir, [[f'look_{t.lower()}_{k}.png' for t in themes] for k in ('light', 'daydark', 'night')], 'looks_sheet.png')
    contact_sheet(outdir, [[f'look_{t.lower()}_light{k}.png' for k in ('', '_humidity', '_weather', '_settings')] for t in themes],
                  'looks_light_sheet.png')
    return out


def forecast_response(kind='hourly'):
    """Shape of POST /api/services/weather/get_forecasts?return_response for weather.home (met.no)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if kind == 'daily':
        days = [('sunny', 63, 48, 0.0), ('partlycloudy', 61, 50, 0.0), ('rainy', 57, 49, 0.31),
                ('pouring', 55, 47, 0.87), ('cloudy', 58, 45, 0.02), ('sunny', 64, 46, 0.0)]
        start = now.replace(hour=16)
        fc = [{'condition': c, 'datetime': (start + timedelta(days=i)).isoformat(), 'wind_bearing': 98.0,
               'uv_index': 5.3 - i * 0.5, 'temperature': hi, 'templow': lo, 'wind_speed': 9.82 + i,
               'precipitation': pr, 'humidity': 56 + i * 4} for i, (c, hi, lo, pr) in enumerate(days)]
        return {'changed_states': [], 'service_response': {'weather.home': {'forecast': fc}}}
    conds = ['partlycloudy', 'cloudy', 'rainy', 'pouring', 'rainy', 'cloudy', 'partlycloudy', 'sunny',
             'sunny', 'clear-night', 'clear-night', 'fog']
    fc = []
    for i in range(48):
        t = 55 + 7 * __import__('math').sin((i - 6) / 24.0 * 6.283)
        fc.append({'condition': conds[i % 12], 'datetime': (now + timedelta(hours=i + 1)).isoformat(),
                   'wind_bearing': 77.9, 'cloud_coverage': 67.2, 'uv_index': 0.0, 'temperature': round(t),
                   'wind_speed': 6.96, 'precipitation': 0.0 if i % 3 else 0.02, 'humidity': 88})
    return {'changed_states': [], 'service_response': {'weather.home': {'forecast': fc}}}


def history_response(path, states=None):
    """GET /api/history/period/<start>?filter_entity_id=a,b&minimal_response&no_attributes, 24 h of data:
    humidity idling around 74% with a shower spike to 92% eight hours ago, its baseline, the fan
    running through the shower, the room temperature, and a towel heater's power
    around the shower."""
    import math
    from urllib.parse import urlparse, parse_qs, unquote
    q = parse_qs(urlparse(path).query, keep_blank_values=True)
    ids = [i for i in unquote((q.get('filter_entity_id') or [''])[0]).split(',') if i]
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(hours=24)
    shower = now - timedelta(hours=8)
    out = []
    for eid in ids:
        pts = []
        for k in range(0, 24 * 60, 6):  # a point every 6 minutes
            t = t0 + timedelta(minutes=k)
            dt = (t - shower).total_seconds() / 60.0
            hum = 74 + 1.5 * math.sin(k / 180.0) + (18 * math.exp(-max(dt, 0) / 25.0) if 0 <= dt else 0)
            if eid == 'sensor.bathroom_humidity':
                v = '%.1f' % hum
            elif eid == 'sensor.bathroom_humidity_baseline':
                v = '%.1f' % (74 + 1.5 * math.sin(k / 180.0) + (6 * math.exp(-max(dt, 0) / 60.0) if 0 <= dt else 0))
            elif eid == 'sensor.bathroom_temperature':
                v = '%.2f' % (71 + 0.8 * math.sin(k / 240.0) + (1.2 * math.exp(-max(dt, 0) / 40.0) if 0 <= dt else 0))
            elif eid == 'fan.bathroom_fan':
                v = 'on' if 2 <= dt <= 45 else 'off'
            elif eid == 'sensor.bathroom_heater_power':
                v = '%.1f' % (1480 + 15 * math.sin(k / 7.0)) if -30 <= dt <= 40 else '0.0'
            else:
                continue
            if pts and pts[-1]['state'] == v:
                continue
            pts.append({'state': v, 'last_changed': t.isoformat()})
        # End at the scenario's current state, as real history does.
        cur = (states or {}).get(eid, {}).get('state')
        if cur is not None and pts and pts[-1]['state'] != cur:
            pts.append({'state': cur, 'last_changed': (now - timedelta(minutes=3)).isoformat()})
        if pts:
            first = dict(pts[0], entity_id=eid, attributes={}, last_updated=pts[0]['last_changed'])
            out.append([first] + pts[1:])
    return out


def pages_test(page, calls, outdir, tap):
    """Open each detail page, check it replaces the home screen and fits, go back, and check the
    15 second return to home. Page contract: a card with data-tap="page" data-page="<name>" opens
    #page-<name>; a visible data-tap="back" returns home; home is whatever holds [data-dial]."""
    out = {}

    def state():
        home = page.locator('[data-dial]').first.is_visible()
        shown = [n for n in ('temp', 'humidity', 'weather') if page.locator('#page-' + n).count() and page.locator('#page-' + n).first.is_visible()]
        dims = page.evaluate('({sh: document.documentElement.scrollHeight, sw: document.documentElement.scrollWidth})')
        return {'home_visible': home, 'pages_visible': shown, 'fits': dims['sh'] <= 800 and dims['sw'] <= 480}

    def back():
        loc = page.locator('[data-tap="back"]')
        for i in range(loc.count()):
            if loc.nth(i).is_visible():
                b = loc.nth(i).bounding_box()
                page.touchscreen.tap(b['x'] + b['width'] / 2, b['y'] + b['height'] / 2)
                page.wait_for_timeout(900)
                return True
        return False

    for name in ('temp', 'humidity', 'weather'):
        before = len(calls)
        r = tap(f'[data-tap="page"][data-page="{name}"]')
        page.wait_for_timeout(2300)
        page.screenshot(path=os.path.join(outdir, f'page_{name}.png'))
        s1 = state()
        s1['posts_on_open'] = r
        s1['requests_on_open'] = sorted({c['method'] + ' ' + c['path'].split('?')[0] for c in calls[before:]
                                         if '/api/states/' not in c['path']})
        s1['svg_points'] = page.evaluate("""(function(){var n=0,pl=document.querySelectorAll('#page-%s polyline, #page-%s path');
            for(var i=0;i<pl.length;i++){var a=pl[i].getAttribute('points')||pl[i].getAttribute('d')||'';n+=a.length}return n})()""" % (name, name))
        s1['went_back'] = back()
        s1['after_back'] = state()
        out[name] = s1
    # Idle return: open a page, touch nothing for 16 s.
    tap('[data-tap="page"][data-page="humidity"]')
    page.wait_for_timeout(1500)
    opened = state()
    page.wait_for_timeout(16000)
    out['idle_return'] = {'opened': opened, 'after_16s': state()}
    return out


def home_rows(page):
    """The home rows top to bottom: top, height, tap targets and text."""
    return page.evaluate("""Array.prototype.map.call(document.querySelectorAll('#rows > .row'), function(r){
        return {top: r.offsetTop, height: r.offsetHeight, text: r.textContent,
                taps: Array.prototype.map.call(r.querySelectorAll('[data-tap]'), function(e){
                  return e.getAttribute('data-tap') + ':' + (e.getAttribute('data-min') || e.getAttribute('data-entity') ||
                    e.getAttribute('data-page') || '')})}})""")


def minutes_shown(page):
    return page.evaluate("Array.prototype.map.call(document.querySelectorAll('#rows [data-min]'), function(e){return e.getAttribute('data-min')}).join(',')")


def cached_layout(page):
    return page.evaluate("(function(){try{return JSON.parse(localStorage.getItem('bathroomPanelLayout'))}catch(e){return 'ERR'}})()")


def wait_until(page, js, ms):
    try:
        page.wait_for_function(js, timeout=ms)
        return True
    except Exception:
        return False


def state_gets(calls, since=0, base='/api/'):
    return sorted({c['path'][len(base) + len('states/'):] for c in calls[since:]
                   if c['method'] == 'GET' and c['path'].startswith(base + 'states/')})


def rows_quiet(page, ms=6000):
    """DOM writes under the home rows in `ms` with nothing changing (the diffed writes
    should make none)."""
    page.evaluate("""window.__muts = 0; new MutationObserver(function(l){window.__muts += l.length})
        .observe(document.getElementById('rows'), {subtree: true, childList: true, attributes: true, characterData: true})""")
    page.wait_for_timeout(ms)
    return page.evaluate('window.__muts')


def tile_sel(eid):
    return f'#rows [data-entity="{eid}"]'


def variant_test(page, calls, outdir, tap, states):
    """The variant layout from the layout sensor: its rows, the cache, every kind of
    tap, a double tap, and the detail pages for the tapped cards."""
    out = {'start_minutes': page.evaluate('window.__startMins'), 'minutes': minutes_shown(page),
           'rows': home_rows(page), 'cache': cached_layout(page), 'targets': visible_targets(page)}
    mark = len(calls)
    svc = '/api/services/'

    def lit(sel):
        return page.evaluate(f"document.querySelector('{sel}').className.indexOf(' on')>=0")

    def tap_now(sel, twice=False):
        """Tap (twice, 150 ms apart, for a double tap) and read the tile 150 ms later."""
        n = len(calls)
        b = page.locator(sel).first.bounding_box()
        page.touchscreen.tap(b['x'] + b['width'] / 2, b['y'] + b['height'] / 2)
        if twice:
            page.wait_for_timeout(150)
            page.touchscreen.tap(b['x'] + b['width'] / 2, b['y'] + b['height'] / 2)
        page.wait_for_timeout(150)
        at_once = lit(sel)
        page.wait_for_timeout(1200)
        return post_list(calls, n), at_once

    out['preset10'] = tap('[data-tap="run"][data-min="10"]')
    page.wait_for_timeout(2500)
    s = tile_sel('light.bathroom_ceiling')
    t = {'lit_before': lit(s)}
    t['posts'], t['lit_at_once'] = tap_now(s)
    page.wait_for_timeout(2500)
    t['lit_after'], t['mock_after'] = lit(s), states['light.bathroom_ceiling']['state']
    out['toggle_tile'] = t
    s = tile_sel('input_boolean.bathroom_guest_mode')
    t = {'lit_before': lit(s)}
    t['posts'], t['lit_at_once'] = tap_now(s, twice=True)
    page.wait_for_timeout(2500)
    t['lit_after'], t['mock_after'] = lit(s), states['input_boolean.bathroom_guest_mode']['state']
    out['double_tap'] = t
    for key, eid in (('run_tile', 'script.bathroom_shower_mode'), ('scene_tile', 'scene.bathroom_relax')):
        s = tile_sel(eid)
        t = {}
        t['posts'], t['lit_at_once'] = tap_now(s)
        page.wait_for_timeout(1500)
        t['lit_after'] = lit(s)
        out[key] = t
    door = page.locator('#rows .qb', has_text='Door')
    out['none_tile'] = {'data_tap': door.first.get_attribute('data-tap'), 'text': door.first.text_content()}
    n = len(calls)
    b = door.first.bounding_box()
    page.touchscreen.tap(b['x'] + b['width'] / 2, b['y'] + b['height'] / 2)
    page.wait_for_timeout(1200)
    out['none_tile']['posts'] = post_list(calls, n)
    out['none_tile']['page'] = page_state(page)
    s = '#rows [data-tap="toggle"][data-entity="switch.bathroom_towel_warmer"]'
    n = len(calls)
    b = page.locator(s).first.bounding_box()
    page.touchscreen.tap(b['x'] + b['width'] / 2, b['y'] + b['height'] / 2)
    page.wait_for_timeout(150)
    out['status_toggle'] = {'text_at_once': page.locator(s + ' .vs').first.text_content(),
                            'lit_at_once': page.evaluate(f"document.querySelector('{s}').className.indexOf(' lit')>=0")}
    page.wait_for_timeout(1200)
    out['status_toggle']['posts'] = post_list(calls, n)
    page.wait_for_timeout(1500)
    page.screenshot(path=os.path.join(outdir, 'variant_after_taps.png'))

    def detail(name, shot):
        n = len(calls)
        tap(f'[data-tap="page"][data-page="{name}"]')
        page.wait_for_timeout(2300)
        page.screenshot(path=os.path.join(outdir, shot))
        p = 'tp' if name == 'temp' else 'hp'
        r = {'page': page_state(page),
             'caption': page.evaluate(f"document.getElementById('{p}L').textContent"),
             'value': page.evaluate(f"document.getElementById('{p}V').textContent"),
             'history': [c['path'].split('?')[1] for c in calls[n:] if '/history/period/' in c['path']],
             'svg_points': page.evaluate(f"""(function(){{var n=0,pl=document.querySelectorAll('#page-{name} path');
                 for(var i=0;i<pl.length;i++){{n+=(pl[i].getAttribute('d')||'').length}}return n}})()""")}
        if name == 'temp':
            r['outside_ref'] = page.evaluate("document.getElementById('tpR').className")
            r['stats'] = page.evaluate("[document.getElementById('tpA1').textContent, document.getElementById('tpC1').textContent]")
            r['axis'] = page.evaluate("Array.prototype.map.call(document.querySelectorAll('#tpG text'), function(t){return t.textContent})")
        tap('[data-tap="back"]')
        return r

    out['sensor_page'] = detail('temp', 'page_variant_sensor.png')
    out['humidity_page'] = detail('humidity', 'page_variant_humidity.png')
    out['after_back'] = page_state(page)
    out['gets_after_rebuild'] = state_gets(calls, mark)
    return out


def rev_change_test(page, calls, outdir, tap, states, loads):
    """The layout sensor at rev 1 with the default layout; same rev, no rebuild; rev 2
    brings the variant in without a reload; new attributes on the same rev and then an
    invalid layout at rev 3 are both left alone."""
    out = {'start_minutes': minutes_shown(page), 'cache_rev1': cached_layout(page)}
    page.evaluate("window.__harness = 1; window.__row0 = document.querySelector('#rows > .row')")
    out['quiet_mutations'] = rows_quiet(page, 6000)
    page.wait_for_timeout(5000)
    out['same_rev_same_rows'] = page.evaluate("document.querySelector('#rows > .row') === window.__row0")
    n = len(loads)
    set_state(states, LAYOUT_SENSOR, '2', layout_attrs(VARIANT))
    out['rebuilt'] = wait_until(page, "!!document.querySelector('#rows [data-min=\"10\"]')", 15000)
    out['no_reload'] = page.evaluate('window.__harness === 1') and not reloads_since(loads, n)
    out['minutes_rev2'] = minutes_shown(page)
    out['cache_rev2'] = cached_layout(page)
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(outdir, 'rev_change_rev2.png'))
    page.evaluate("window.__row0 = document.querySelector('#rows > .row')")
    set_state(states, LAYOUT_SENSOR, '2', layout_attrs(DEFAULT_LAYOUT))
    page.wait_for_timeout(12000)
    out['same_rev_new_attrs_ignored'] = (page.evaluate("document.querySelector('#rows > .row') === window.__row0")
                                         and minutes_shown(page) == '10,20')
    set_state(states, LAYOUT_SENSOR, '3', layout_attrs({'hero': VARIANT['hero'], 'rows': 'nope'}))
    page.wait_for_timeout(12000)
    out['invalid_ignored'] = (page.evaluate("document.querySelector('#rows > .row') === window.__row0")
                              and minutes_shown(page) == '10,20')
    out['cache_after_invalid'] = cached_layout(page)
    out['still_no_reload'] = page.evaluate('window.__harness === 1') and not reloads_since(loads, n)
    return out


def layout_edge_test(page, calls, outdir, tap, states):
    """A bad cached layout is ignored at start; then a layout with an unknown row type,
    a label with markup, an unknown icon, a missing entity with a tap its domain does not
    allow, messy presets, a weather row with no label, no motion and no steam, a
    humidity card with no usual level, and a last row that does not fit; then an
    invalid layout, left alone; then every label at 24 characters with the vanity
    lit, so its state is at its widest."""
    out = {'start_minutes': page.evaluate('window.__startMins'), 'minutes_before': minutes_shown(page),
           'cache_before': cached_layout(page)}
    states[LAYOUT_SENSOR] = st(LAYOUT_SENSOR, '7', layout_attrs(EDGE))
    out['applied'] = wait_until(page, "!!document.querySelector('#rows [data-min=\"45\"]')", 15000)
    page.wait_for_timeout(1000)
    mark = len(calls)
    out['rows'] = home_rows(page)
    out['minutes'] = minutes_shown(page)
    out['label'] = page.evaluate("""(function(){var v=document.querySelector('#rows .van .vl');
        return {text: v.textContent, elements: v.children.length}})()""")
    out['status'] = page.evaluate("""(function(){var c=document.querySelector('#rows .van');
        return {tap: c.getAttribute('data-tap'), state: c.querySelector('.vs').textContent,
                icon: c.querySelector('use').getAttribute('xlink:href')}})()""")
    out['weather_label'] = page.evaluate("document.querySelector('#rows .wx .wd .cap').textContent")
    out['motion_line'] = page.evaluate("document.getElementById('mot').className")
    out['cache'] = cached_layout(page)
    page.screenshot(path=os.path.join(outdir, 'layout_edge.png'))
    tap('[data-tap="page"][data-page="humidity"]')
    page.wait_for_timeout(2000)
    out['humidity_legend_usual'] = page.evaluate("document.getElementById('hpLu').offsetWidth")
    page.screenshot(path=os.path.join(outdir, 'layout_edge_humidity.png'))
    tap('[data-tap="back"]')
    out['dial'] = tap('[data-tap="fan"]')
    page.wait_for_timeout(2500)
    tap('[data-tap="page"][data-page="settings"]')
    page.wait_for_timeout(800)
    out['settings'] = page_state(page)
    tap('[data-tap="back"]')
    page.wait_for_timeout(8000)
    out['motion_gets'] = [e for e in state_gets(calls, mark) if e == MOTION]
    page.evaluate("window.__row0 = document.querySelector('#rows > .row')")
    bad = dict(VARIANT, hero=dict(VARIANT['hero'], fan='light.bathroom_ceiling'))
    set_state(states, LAYOUT_SENSOR, '8', layout_attrs(bad))
    page.wait_for_timeout(12000)
    out['invalid_ignored'] = (page.evaluate("document.querySelector('#rows > .row') === window.__row0")
                              and minutes_shown(page) == out['minutes'])
    out['cache_after_invalid'] = cached_layout(page)
    set_state(states, 'light.bathroom_vanity', 'on', dict(states['light.bathroom_vanity']['attributes'],
                                                           color_mode='brightness', brightness=229))
    set_state(states, LAYOUT_SENSOR, '9', layout_attrs(LONG_LABELS))
    out['long_applied'] = (wait_until(page, "!!document.querySelector('#rows .tln')", 15000)
                           and wait_until(page, "!!document.querySelector('#rows .van .vs b')", 15000))
    page.wait_for_timeout(500)
    # Each label: on its card, on one line, clear of the status row's state.
    out['long_labels'] = page.evaluate("""Array.prototype.map.call(
        document.querySelectorAll('#rows .vl, #rows .cl .cap, #rows .wd .cap, #rows .tln'), function(e){
          var c = e.parentNode; while (!/\\bcard\\b/.test(c.className)) { c = c.parentNode; }
          var r = e.getBoundingClientRect(), b = c.getBoundingClientRect(), s = c.querySelector('.vs');
          return {text: e.textContent, inside: r.left >= b.left && r.right <= b.right,
                  one_line: r.height === parseFloat(getComputedStyle(e).lineHeight),
                  clear: !s || r.right <= s.getBoundingClientRect().left}})""")
    page.screenshot(path=os.path.join(outdir, 'layout_edge_long.png'))
    return out


def reloads_since(loads, n):
    """Loads of the page since loads[n], but the version check's fetches (?u=), which
    read the page without showing it."""
    return [l for l in loads[n:] if '?u=' not in l]


def self_update(page, loads, ms=601000):
    """Ten minutes on the page's clock (Playwright's fake clock, installed before load):
    the page's version check fetches the page, served with a new stamp. Returns the
    page fetches and loads that followed."""
    n = len(loads)
    NEW_COPY['on'] = True
    try:
        page.clock.run_for(ms)
        page.wait_for_timeout(1500)
    finally:
        NEW_COPY['on'] = False
    new = loads[n:]
    return {'fetches': [l for l in new if '?u=' in l],
            # The fetch of the plain address past the cache, so the kept copy is the new one.
            'renews': [l for l in new if '?' not in l],
            'reloads': [l for l in new if '?b=' in l], 'url': page.url}


def cached_test(page, calls, outdir, tap, states, loads):
    """The variant kept in localStorage and no layout sensor: the variant from the first
    paint, only its entities polled, no forecast without a weather row, no DOM writes
    under the rows while nothing changes; and the self-update still works."""
    out = {'start_minutes': page.evaluate('window.__startMins'), 'minutes': minutes_shown(page),
           'gets': state_gets(calls), 'forecasts': sum(1 for c in calls if 'get_forecasts' in c['path'])}
    out['quiet_mutations'] = rows_quiet(page, 6000)
    out['update'] = self_update(page, loads)
    return out


def preview_test(page, calls, outdir, tap, states, loads):
    """Preview mode: the variant from PANEL_PREVIEW, every request to the preview base
    with no token, a posted draft shown and an invalid one ignored, service calls to the
    base, nothing stored, and no self-update."""
    base = PREVIEW_API
    out = {'start_minutes': page.evaluate('window.__startMins'), 'minutes': minutes_shown(page)}
    out['toggle'] = tap(tile_sel('light.bathroom_ceiling'))
    page.wait_for_timeout(1500)
    out['fan'] = tap('[data-tap="fan"]')
    page.wait_for_timeout(2500)
    n = len(calls)
    tap('[data-tap="page"][data-page="temp"]')
    page.wait_for_timeout(2000)
    out['history'] = [c['path'].split('?')[0] for c in calls[n:] if '/history/period/' in c['path']]
    out['sensor_caption'] = page.evaluate("document.getElementById('tpL').textContent")
    tap('[data-tap="back"]')
    out['gets'] = state_gets(calls, 0, base)
    draft = dict(DEFAULT_LAYOUT, rows=[{'type': 'fan_presets', 'minutes': [45]}] + DEFAULT_LAYOUT['rows'][1:])
    page.evaluate("window.postMessage({type: 'panel-layout', layout: %s}, '*')" % json.dumps(draft))
    out['draft_shown'] = wait_until(page, "document.querySelectorAll('#rows [data-min]').length === 1", 5000)
    out['draft_minutes'] = minutes_shown(page)
    page.wait_for_timeout(2500)
    page.screenshot(path=os.path.join(outdir, 'preview_draft.png'))
    page.evaluate("window.postMessage({type: 'panel-layout', layout: {hero: {type: 'fan_dial'}, rows: []}}, '*')")
    page.evaluate("window.postMessage(JSON.stringify({type: 'something-else', layout: %s}), '*')" % json.dumps(VARIANT))
    page.wait_for_timeout(1500)
    out['invalid_draft_ignored'] = minutes_shown(page) == '45'
    out['update'] = self_update(page, loads)
    out['storage_keys'] = page.evaluate("(function(){try{return localStorage.length}catch(e){return -1}})()")
    return out


def es5_check(html):
    scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S | re.I)
    out = []
    for i, js in enumerate(scripts):
        p = subprocess.run(['node', '-e', f"try{{require('{ACORN}').parse(require('fs').readFileSync(0,'utf8'),{{ecmaVersion:5}});console.log('ok')}}catch(e){{console.log('ES5 FAIL: '+e.message)}}"],
                           input=js, capture_output=True, text=True, timeout=30)
        out.append(p.stdout.strip() or p.stderr.strip())
    ext = re.findall(r'<script[^>]*\bsrc=["\']([^"\']+)', html, re.I)
    if ext:
        out.append('external scripts referenced (not allowed): ' + ', '.join(ext))
    return out


def run(page_path, outdir):
    html = open(page_path, encoding='utf-8').read()
    os.makedirs(outdir, exist_ok=True)
    report = {'page': page_path, 'bytes': len(html.encode()), 'es5': es5_check(html), 'scenarios': {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(env={**os.environ, 'LD_LIBRARY_PATH': LIBS})
        for name in SCENARIOS:
            ctx = browser.new_context(viewport={'width': 480, 'height': 800}, has_touch=True,
                                      is_mobile=True, device_scale_factor=1)
            page = ctx.new_page()
            states = scenario_states(name)
            calls, errors, loads = [], [], []
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            # "Failed to load resource" is the browser logging the mocked 4xx/5xx
            # responses themselves; only real script problems count.
            page.on('console', lambda m: m.type in ('error', 'warning') and 'Failed to load resource' not in m.text
                    and errors.append(f'console.{m.type}: {m.text}'))

            def handle(route, request, states=states, calls=calls, name=name, loads=loads):
                path = request.url[len(ORIGIN):].split('#')[0]
                hdr = {'Date': formatdate(usegmt=True), 'Content-Type': 'application/json'}
                if path.startswith('/local/bathroom-panel.html') or path.startswith(PREVIEW_PAGE):
                    loads.append(path)
                    body = html
                    if path.startswith(PREVIEW_PAGE):
                        # What an editor's /panel-preview/<slug> serves: the page from HA with this right after <head>.
                        body = body.replace('<head>', '<head><script>window.PANEL_PREVIEW=' +
                                            json.dumps({'api': PREVIEW_API, 'layout': VARIANT}) + '</script>', 1)
                    if NEW_COPY['on']:
                        # The version check's fetch finds a new copy on the server, and every load after it gets that copy.
                        body = re.sub(r'PANEL_VERSION = "[0-9a-f]{8,}"', 'PANEL_VERSION = "ffffffffff"', body)
                    return route.fulfill(status=200, body=body, headers={'Content-Type': 'text/html; charset=utf-8'})
                if path.startswith('/api/'):
                    calls.append({'method': request.method, 'path': path,
                                  'auth': (request.headers.get('authorization') or '')[:13],
                                  'body': request.post_data})
                    if name == 'preview':
                        # The editor's proxy: same paths after its base; it forwards reads, and
                        # any other service call answers [] without reaching Home Assistant.
                        if not path.startswith(PREVIEW_API):
                            return route.fulfill(status=404, body='{}', headers=hdr)
                        path = '/api/' + path[len(PREVIEW_API):]
                        if request.method == 'POST' and not path.startswith('/api/services/weather/get_forecasts'):
                            return route.fulfill(status=200, body='[]', headers=hdr)
                    if name == 'offline':
                        return route.fulfill(status=502, body='Bad Gateway', headers=hdr)
                    if name == 'rejected':
                        return route.fulfill(status=401, body='401: Unauthorized', headers=hdr)
                    m = re.match(r'/api/states/(.+)$', path)
                    if request.method == 'GET' and m:
                        s = states.get(m.group(1))
                        if s is None:
                            return route.fulfill(status=404, body='{"message":"Entity not found."}', headers=hdr)
                        return route.fulfill(status=200, body=json.dumps(s), headers=hdr)
                    if request.method == 'POST' and path.startswith('/api/services/weather/get_forecasts'):
                        kind = json.loads(request.post_data or '{}').get('type', 'hourly')
                        return route.fulfill(status=200, body=json.dumps(forecast_response(kind)), headers=hdr)
                    if request.method == 'GET' and path.startswith('/api/history/period/'):
                        return route.fulfill(status=200, body=json.dumps(history_response(path, states)), headers=hdr)
                    if request.method == 'POST' and path.startswith('/api/services/'):
                        apply_service(states, path, request.post_data)
                        return route.fulfill(status=200, body='[]', headers=hdr)
                    return route.fulfill(status=404, body='{}', headers=hdr)
                return route.fulfill(status=404, body='not found')

            page.route(ORIGIN + '/**', handle)
            if name in LAYOUT_SCENARIOS:
                # Which presets the rows had when the page's script finished, before any reading.
                page.add_init_script("""document.addEventListener('DOMContentLoaded', function () {
                    window.__startMins = Array.prototype.map.call(document.querySelectorAll('#rows [data-min]'),
                        function (e) { return e.getAttribute('data-min'); }).join(','); });""")
            if name == 'cached':
                page.add_init_script("localStorage.setItem('bathroomPanelLayout', %s)" % json.dumps(json.dumps({'rev': 5, 'layout': VARIANT})))
            if name == 'layout_edge':
                page.add_init_script("localStorage.setItem('bathroomPanelLayout', %s)" % json.dumps(json.dumps({'rev': 3, 'layout': {'hero': {}, 'rows': []}})))
            if name in ('cached', 'preview'):
                # The version check runs every 600 ticks: these two fast-forward to it.
                page.clock.install()
            page.goto(ORIGIN + PREVIEW_PAGE if name == 'preview' else (URL if name == 'notoken' else URL + '#token=mock-token'))
            page.wait_for_timeout(4500)
            avoid_minute_edge(page)
            shot = page.screenshot(path=os.path.join(outdir, f'{name}.png'))
            page.wait_for_timeout(600)
            shot2 = page.screenshot()
            diff = ImageChops.difference(Image.open(BytesIO(shot)).convert('RGB'), Image.open(BytesIO(shot2)).convert('RGB'))
            changed = sum(1 for px in diff.getdata() if px != (0, 0, 0))
            dims = page.evaluate('({sh: document.documentElement.scrollHeight, sw: document.documentElement.scrollWidth, bh: document.body.scrollHeight})')
            res = {'errors': errors, 'scroll': dims, 'fits_480x800': dims['sh'] <= 800 and dims['sw'] <= 480,
                   'animated_pixels_changed_in_600ms': changed,
                   'token_stored': page.evaluate("(function(){try{return localStorage.getItem('bathroomPanelToken')}catch(e){return 'ERR'}})()"),
                   'url_after_load': page.url,
                   'visible_text': page.evaluate('document.body.innerText').strip()[:600]}
            gets = sorted({c['path'].split('?')[0] if '/history/' in c['path'] else c['path'] for c in calls if c['method'] == 'GET'})
            res['gets'] = gets
            res['full_states_polled'] = any(c['path'] in ('/api/states', '/api/states/') for c in calls)
            res['forecast_calls_on_load'] = sum(1 for c in calls if 'get_forecasts' in c['path'])

            def drag_dial(minutes, shot=None):
                """Touch-drag on [data-dial] from 12 o'clock clockwise to the angle for `minutes` on a 60 min dial
                (with `shot`, a screenshot of the dial held there before letting go)."""
                loc = page.locator('[data-dial]')
                if loc.count() == 0:
                    return 'MISSING [data-dial]'
                box = loc.first.bounding_box()
                cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                r = min(box['width'], box['height']) / 2 * 0.86
                import math
                cdp = ctx.new_cdp_session(page)
                def pt(a):
                    return {'x': cx + r * math.sin(a), 'y': cy - r * math.cos(a)}
                before = len(calls)
                cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [pt(0.02)]})
                steps = 12
                end = 2 * math.pi * minutes / 60.0
                for k in range(1, steps + 1):
                    cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [pt(0.02 + (end - 0.02) * k / steps)]})
                    page.wait_for_timeout(30)
                if shot:
                    page.wait_for_timeout(200)
                    page.screenshot(path=os.path.join(outdir, shot))
                cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
                page.wait_for_timeout(1200)
                posts = [c for c in calls[before:] if c['method'] == 'POST' and 'get_forecasts' not in c['path']]
                return [{'path': c['path'], 'body': c['body']} for c in posts] or 'no POST sent'

            # Tap tests (touchscreen taps, like the wall panel)
            taps = {}
            def tap(sel):
                loc = page.locator(sel)
                if loc.count() == 0:
                    return f'MISSING {sel}'
                box = loc.first.bounding_box()
                before = len(calls)
                page.touchscreen.tap(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                page.wait_for_timeout(1200)
                posts = [c for c in calls[before:] if c['method'] == 'POST' and 'get_forecasts' not in c['path']]
                return [{'path': c['path'], 'body': c['body']} for c in posts] or 'no POST sent'
            # Each action flips the page's optimistic state while the mock keeps
            # reporting the original one, so let a poll (2 s) settle in between.
            if name in ('idle', 'timed'):
                taps['fan'] = tap('[data-tap="fan"]')
                page.wait_for_timeout(2500)
            if name == 'idle':
                taps['drag_to_15'] = drag_dial(15)
                page.wait_for_timeout(2500)
                taps['drag_to_45'] = drag_dial(45, 'idle_drag.png')
                page.wait_for_timeout(2500)
            res['look'] = page_state(page)
            if name == 'timed':
                for m in (15, 30, 60):
                    taps[f'run{m}'] = tap(f'[data-tap="run"][data-min="{m}"]')
                # Visible targets only: the settings controls sit on a hidden page here.
                res['touch_targets'] = visible_targets(page)
            if name == 'pages':
                res['pages'] = pages_test(page, calls, outdir, tap)
                res['touch_targets'] = visible_targets(page)
            if name == 'settings':
                res['settings'] = settings_test(page, calls, outdir, tap, states)
            if name == 'alert':
                res['alert'] = alert_test(page, calls, outdir, tap, states)
            if name == 'night':
                res['night'] = night_extras(page, calls, outdir, tap, states)
            if name == 'looks':
                res['looks'] = looks_test(page, outdir, tap)
            if name == 'variant':
                res['variant'] = variant_test(page, calls, outdir, tap, states)
            if name == 'rev_change':
                res['rev_change'] = rev_change_test(page, calls, outdir, tap, states, loads)
            if name == 'layout_edge':
                res['layout_edge'] = layout_edge_test(page, calls, outdir, tap, states)
            if name == 'cached':
                res['cached'] = cached_test(page, calls, outdir, tap, states, loads)
            if name == 'preview':
                res['preview'] = preview_test(page, calls, outdir, tap, states, loads)
            res['taps'] = taps
            # A layout may hold a light tile; there only a call naming the vanity counts.
            res['vanity_posts'] = [c['path'] for c in calls if c['method'] == 'POST' and (
                'light.bathroom_vanity' in (c['body'] or '') if name in LAYOUT_SCENARIOS else '/light/' in c['path'])]
            res['api_paths'] = sorted({c['method'] + ' ' + c['path'].split('?')[0] for c in calls})
            res['auth_headers'] = sorted({c['auth'] for c in calls if c['auth']})
            res['page_loads'] = loads
            res['posts'] = post_list(calls, 0)
            report['scenarios'][name] = res
            ctx.close()
        browser.close()

    base = os.environ.get('HARNESS_BASELINE')
    if base and 'day_dark' in report['scenarios']:
        report['day_dark_vs_baseline'] = differs_outside_clock(os.path.join(outdir, 'day_dark.png'), os.path.join(base, 'idle.png'))
    report['checks'] = checks(report)
    with open(os.path.join(outdir, 'report.json'), 'w') as f:
        json.dump(report, f, indent=1)
    print(json.dumps(report, indent=1))
    failed = [k for k, v in report['checks'].items() if not v]
    print('\nCHECKS: %d passed, %d failed' % (len(report['checks']) - len(failed), len(failed)))
    for k in failed:
        print('  FAIL', k)


def differs_outside_clock(a, b):
    """How two home screenshots differ, leaving out the clock and date and the forecast
    hours (all move with the time): the number of pixels and the largest channel
    difference. Text anti-aliasing alone moves a few
    dozen pixels by up to about 20 between runs of the same page; a colour or layout
    change shows as more than that."""
    ia, ib = Image.open(a).convert('RGB'), Image.open(b).convert('RGB')
    for im in (ia, ib):
        im.paste((0, 0, 0), (0, 0, 200, 80))
        im.paste((0, 0, 0), (16, 712, 464, 790))
    d = [max(px) for px in ImageChops.difference(ia, ib).getdata() if px != (0, 0, 0)]
    return {'pixels': len(d), 'max_channel_diff': max(d) if d else 0}


def checks(report):
    """Every done-means item from the three briefs, as name -> bool."""
    c, sc = {}, report['scenarios']
    svc = '/api/services/'
    run = lambda m: [(svc + 'script/turn_on', {'entity_id': 'script.bathroom_fan_timer', 'variables': {'minutes': m}})]
    c['es5'] = all(x == 'ok' for x in report['es5'])
    # Raised from 120 KB on purpose when the page learned layouts: the layout
    # code and the palette icons took the build from about 106 KB to about 125 KB.
    # Raised again to 140 KB when the one source learned the clock hero, camera
    # rows and pop-ups and the View Assist bridge (about 136 KB).
    c['size_under_140KB'] = report['bytes'] < 140 * 1000
    for n, r in sc.items():
        c[f'{n}.no_errors'] = not r['errors']
        c[f'{n}.fits_480x800'] = r['fits_480x800']
        c[f'{n}.no_vanity_posts'] = not r['vanity_posts']
        c[f'{n}.no_full_states_poll'] = not r['full_states_polled']
    for n in ('idle', 'offline', 'notoken', 'rejected', 'night', 'settings', 'day_dark') + LAYOUT_SCENARIOS:
        if n in sc:
            c[f'{n}.still'] = sc[n]['animated_pixels_changed_in_600ms'] == 0
    # With the light switch at its default the day is light; off, it is today's dark day.
    for n in LIGHT_SCENARIOS:
        if n in sc:
            c[f'{n}.light'] = 'light' in sc[n]['look']['html_class'].split()
    if 'day_dark' in sc:
        cls = sc['day_dark']['look']['html_class'].split()
        c['day_dark.dark_look'] = 'light' not in cls and 'night' not in cls
    if 'variant_dark' in sc:
        cls = sc['variant_dark']['look']['html_class'].split()
        c['variant_dark.dark_look'] = 'light' not in cls and 'night' not in cls
    if 'variant_night' in sc:
        cls = sc['variant_night']['look']['html_class'].split()
        c['variant_night.night_look'] = 'night' in cls and 'light' not in cls
    if 'day_dark_vs_baseline' in report:
        c['day_dark.matches_baseline'] = report['day_dark_vs_baseline']['max_channel_diff'] <= 32
    for n in ('timed', 'steam', 'alert'):
        if n in sc:
            c[f'{n}.animates'] = sc[n]['animated_pixels_changed_in_600ms'] > 0
    for n in ('idle', 'timed', 'steam'):
        if n in sc:
            c[f'{n}.one_forecast_on_load'] = sc[n]['forecast_calls_on_load'] == 1
    if 'idle' in sc:
        t = sc['idle']['taps']
        c['idle.fan_tap_30'] = same_posts(t.get('fan'), run(30))
        c['idle.drag_15'] = same_posts(t.get('drag_to_15'), run(15))
        c['idle.drag_45'] = same_posts(t.get('drag_to_45'), run(45))
    if 'timed' in sc:
        t = sc['timed']['taps']
        c['timed.fan_tap_off'] = same_posts(t.get('fan'), [(svc + 'fan/turn_off', {'entity_id': 'fan.bathroom_fan'})])
        for m in (15, 30, 60):
            c[f'timed.run{m}'] = same_posts(t.get(f'run{m}'), run(m))
    for n in ('timed', 'pages'):
        if n in sc:
            c[f'{n}.home_targets_64'] = bool(sc[n]['touch_targets']) and not small_targets(sc[n]['touch_targets'], 64, 64)
    if 'pages' in sc:
        p = sc['pages']['pages']
        for name, need in (('temp', 'GET /api/history/period/'), ('humidity', 'GET /api/history/period/'),
                           ('weather', 'POST /api/services/weather/get_forecasts')):
            q = p[name]
            c[f'pages.{name}'] = (not q['home_visible'] and q['pages_visible'] == [name] and q['fits'] and q['went_back']
                                  and q['after_back']['home_visible'] and any(x.startswith(need) for x in q['requests_on_open'])
                                  and (name == 'weather' or q['svg_points'] > 1000))
        c['pages.idle_return'] = p['idle_return']['after_16s']['home_visible']
    if 'night' in sc:
        r = sc['night']
        c['night.look'] = 'night' in r['look']['html_class'] and 't-ember' in r['look']['html_class'] and 'light' not in r['look']['html_class']
        c['night.settings_still'] = r['night']['settings_animated_pixels'] == 0
    if 'alert' in sc:
        a = sc['alert']['alert']
        c['alert.alarm_shown'] = a['alarm']['alert_visible'] and 'alarm' in a['alarm']['alert_class']
        c['alert.dismiss_post'] = a['dismiss']['ok']
        c['alert.hidden_at_once'] = a['hidden_at_once'] and a['still_hidden']
        c['alert.info_brings_home'] = (a['settings_open']['pages_visible'] == ['settings'] and a['info']['home_visible']
                                       and not a['info']['pages_visible'] and 'info' in a['info']['alert_class'])
        c['alert.info_still'] = a['info_animated_pixels'] == 0
        c['alert.info_dismiss'] = a['info_dismiss']['ok'] and a['info_dismiss']['hidden']
        c['alert.targets_64'] = not small_targets(a['targets'], 64, 64)
        c['alert.fits'] = a['info']['fits'] and a['water']['fits']
    if 'settings' in sc:
        s = sc['settings']['settings']
        c['settings.opens'] = s['opened']['pages_visible'] == ['settings'] and not s['opened']['home_visible']
        c['settings.fits_both_tabs'] = s['opened']['fits'] and s['room']['fits'] and s['after_room']['fits']
        c['settings.reads_all_on_open'] = not s['missing_gets_on_open']
        c['settings.targets_56x64'] = not s['small_targets']
        c['settings.still'] = s['animated_pixels_changed_in_600ms'] == 0
        for k, v in s['taps'].items():
            c[f'settings.tap_{k}'] = v['ok']
        c['settings.highlights_on_tap'] = all(s['taps'][k]['highlighted'] for k in ('mode_night', 'theme_ocean', 'day_brightness_100', 'timeout_120'))
        c['settings.toggle_flips'] = (s['taps']['vanity_toggle']['data_value_before'] == 'off'
                                      and s['taps']['vanity_toggle']['data_value_after'] == 'on')
        c['settings.rereads_after_change'] = not s['missing_rereads_after_change']
        c['settings.dial_uses_new_length'] = s['dial_after']['ok']
        c['settings.look_follows'] = 't-ocean' in s['after_screen']['html_class'] and 'night' in s['after_screen']['html_class']
        dl = s['taps']['day_light_off']
        c['settings.day_light_dark_at_once'] = ('light' in dl['class_before'].split() and dl['class_at_once'].split() == []
                                               and dl['data_value_before'] == 'off' and dl['data_value_after'] == 'on'
                                               and not dl['switch_lit_after'])
        c['settings.mode_switches_at_once'] = 'night' in s['taps']['mode_night']['class_at_once'].split()
        c['settings.idle_return'] = (s['idle_return']['opened']['pages_visible'] == ['settings']
                                     and s['idle_return']['after_16s']['home_visible'] and not s['idle_return']['after_16s']['pages_visible'])
        # Exactly the expected service calls over the whole scenario, in order.
        want = [(svc + 'input_boolean/turn_off', {'entity_id': DAY_LIGHT}),
                (svc + 'input_select/select_option', {'entity_id': 'input_select.bathroom_panel_display_mode', 'option': 'Night'}),
                (svc + 'input_select/select_option', {'entity_id': 'input_select.bathroom_panel_theme', 'option': 'Ocean'}),
                (svc + 'input_number/set_value', {'entity_id': 'input_number.bathroom_panel_day_brightness', 'value': 100}),
                (svc + 'input_number/set_value', {'entity_id': 'input_number.bathroom_panel_screen_timeout', 'value': 120}),
                (svc + 'input_boolean/turn_off', {'entity_id': 'input_boolean.bathroom_vanity_night_dim'}),
                (svc + 'input_datetime/set_datetime', {'entity_id': 'input_datetime.bathroom_vanity_night_start', 'time': '00:00:00'}),
                (svc + 'input_number/set_value', {'entity_id': 'input_number.bathroom_fan_default_minutes', 'value': 45})] + run(45)
        c['settings.exact_posts'] = same_posts(sc['settings']['posts'], want)
    if 'alert' in sc:
        clear = (svc + 'input_text/set_value', {'entity_id': ALERT, 'value': ''})
        c['alert.exact_posts'] = same_posts(sc['alert']['posts'], [clear, clear])
    if 'night' in sc:
        c['night.no_posts'] = sc['night']['posts'] == []
    c.update(layout_checks(sc, svc, run))
    if 'looks' in sc:
        lk = sc['looks']['looks']
        c['looks.classes'] = all(
            ('t-' + t.lower() in lk[f'{t}_light'].split() or t == 'Neon') and 'light' in lk[f'{t}_light'].split()
            and 'light' not in lk[f'{t}_daydark'].split() and 'night' not in lk[f'{t}_daydark'].split()
            and 'night' in lk[f'{t}_night'].split() and 'light' not in lk[f'{t}_night'].split()
            for t in ('Neon', 'Ocean', 'Ember', 'Mono'))
    return c


def layout_checks(sc, svc, run):
    """The layout scenarios' done-means items (docs/layout.md)."""
    c = {}
    if 'variant' in sc:
        v = sc['variant']['variant']
        c['variant.default_then_layout'] = v['start_minutes'] == '15,30,60' and v['minutes'] == '10,20'
        c['variant.rows'] = [(r['top'], r['height'], r['taps']) for r in v['rows']] == [
            (348, 68, ['run:10', 'run:20']), (428, 56, ['toggle:switch.bathroom_towel_warmer']),
            (496, 104, ['page:temp', 'page:humidity']),
            (612, 68, ['toggle:light.bathroom_ceiling', 'trigger:script.bathroom_shower_mode']),
            (692, 68, ['toggle:input_boolean.bathroom_guest_mode', 'trigger:scene.bathroom_relax'])]
        c['variant.no_weather'] = not any('Outside' in r['text'] for r in v['rows'])
        c['variant.cached'] = v['cache'] == {'rev': 5, 'layout': VARIANT}
        c['variant.preset_10'] = same_posts(v['preset10'], run(10))
        t = v['toggle_tile']
        c['variant.toggle_tile'] = (same_posts(t['posts'], [(svc + 'light/toggle', {'entity_id': 'light.bathroom_ceiling'})])
                                    and t['lit_before'] and not t['lit_at_once'] and not t['lit_after'] and t['mock_after'] == 'off')
        t = v['double_tap']
        c['variant.double_tap_one_action'] = (same_posts(t['posts'], [(svc + 'input_boolean/toggle', {'entity_id': 'input_boolean.bathroom_guest_mode'})])
                                              and not t['lit_before'] and t['lit_at_once'] and t['lit_after'] and t['mock_after'] == 'on')
        t = v['run_tile']
        c['variant.run_tile'] = (same_posts(t['posts'], [(svc + 'script/turn_on', {'entity_id': 'script.bathroom_shower_mode'})])
                                 and t['lit_at_once'] and not t['lit_after'])
        t = v['scene_tile']
        c['variant.scene_tile'] = same_posts(t['posts'], [(svc + 'scene/turn_on', {'entity_id': 'scene.bathroom_relax'})]) and t['lit_at_once']
        t = v['none_tile']
        c['variant.none_tile'] = t['data_tap'] is None and t['posts'] == [] and 'Closed' in t['text'] and t['page']['home_visible']
        t = v['status_toggle']
        c['variant.status_toggle'] = (same_posts(t['posts'], [(svc + 'switch/toggle', {'entity_id': 'switch.bathroom_towel_warmer'})])
                                      and t['text_at_once'] == 'On' and t['lit_at_once'])
        p = v['sensor_page']
        c['variant.sensor_card_page'] = (p['page']['pages_visible'] == ['temp'] and p['caption'] == 'Heater power'
                                         and p['value'].endswith('W') and 'gone' in p['outside_ref'].split()
                                         and [h.split('&')[0] for h in p['history']] == ['filter_entity_id=sensor.bathroom_heater_power']
                                         and p['svg_points'] > 200 and all(x.endswith('W') for x in p['stats']))
        p = v['humidity_page']
        c['variant.humidity_card_page'] = (p['page']['pages_visible'] == ['humidity'] and p['caption'] == 'Humidity'
                                           and [h.split('&')[0] for h in p['history']] ==
                                           ['filter_entity_id=sensor.bathroom_humidity,sensor.bathroom_humidity_baseline,fan.bathroom_fan']
                                           and p['svg_points'] > 1000)
        c['variant.back_home'] = v['after_back']['home_visible']
        c['variant.polls_only_layout_entities'] = set(v['gets_after_rebuild']) == VARIANT_ENTS
        c['variant.home_targets_64x56'] = bool(v['targets']) and not small_targets(v['targets'], 64, 56)
        c['variant.exact_posts'] = same_posts(sc['variant']['posts'], run(10) + [
            (svc + 'light/toggle', {'entity_id': 'light.bathroom_ceiling'}),
            (svc + 'input_boolean/toggle', {'entity_id': 'input_boolean.bathroom_guest_mode'}),
            (svc + 'script/turn_on', {'entity_id': 'script.bathroom_shower_mode'}),
            (svc + 'scene/turn_on', {'entity_id': 'scene.bathroom_relax'}),
            (svc + 'switch/toggle', {'entity_id': 'switch.bathroom_towel_warmer'})])
    if 'rev_change' in sc:
        r = sc['rev_change']['rev_change']
        c['rev_change.default_at_rev1'] = r['start_minutes'] == '15,30,60' and r['cache_rev1'] == {'rev': 1, 'layout': DEFAULT_LAYOUT}
        c['rev_change.rows_quiet'] = r['quiet_mutations'] == 0
        c['rev_change.same_rev_no_rebuild'] = r['same_rev_same_rows']
        c['rev_change.rebuilds_without_reload'] = (r['rebuilt'] and r['no_reload'] and r['minutes_rev2'] == '10,20'
                                                   and r['cache_rev2'] == {'rev': 2, 'layout': VARIANT})
        c['rev_change.same_rev_new_attrs_ignored'] = r['same_rev_new_attrs_ignored']
        c['rev_change.invalid_ignored'] = (r['invalid_ignored'] and r['still_no_reload']
                                           and r['cache_after_invalid'] == {'rev': 2, 'layout': VARIANT})
        c['rev_change.no_posts'] = sc['rev_change']['posts'] == []
    if 'layout_edge' in sc:
        e = sc['layout_edge']['layout_edge']
        c['layout_edge.bad_cache_ignored'] = e['start_minutes'] == '15,30,60' and e['minutes_before'] == '15,30,60'
        c['layout_edge.applied'] = e['applied'] and (e['cache'] or {}).get('rev') == 7
        c['layout_edge.unknown_and_overflow_skipped'] = [(r['top'], r['height']) for r in e['rows']] == [
            (348, 56), (416, 68), (496, 176), (684, 104)] and not any('light.bathroom_ceiling' in t for r in e['rows'] for t in r['taps'])
        c['layout_edge.label_literal'] = e['label'] == {'text': 'Door <b>x</b>', 'elements': 0}
        c['layout_edge.neutral_icon'] = e['status']['icon'] == '#i-nu'
        c['layout_edge.missing_entity_dashes'] = e['status']['state'] == '--'
        c['layout_edge.tap_not_allowed_is_none'] = e['status']['tap'] is None
        c['layout_edge.presets_cleaned'] = e['minutes'] == '15,45'
        c['layout_edge.weather_label_default'] = e['weather_label'] == 'Outside'
        c['layout_edge.no_motion'] = e['motion_line'] == 'mot' and not e['motion_gets']
        c['layout_edge.no_usual_line'] = e['humidity_legend_usual'] == 0
        c['layout_edge.dial_works'] = same_posts(e['dial'], run(30))
        c['layout_edge.settings_opens'] = e['settings']['pages_visible'] == ['settings']
        c['layout_edge.invalid_ignored'] = e['invalid_ignored'] and (e['cache_after_invalid'] or {}).get('rev') == 7
        c['layout_edge.long_labels_fit'] = e['long_applied'] and len(e['long_labels']) == 7 and all(
            x['text'] == LONG_LABEL and x['inside'] and x['one_line'] and x['clear'] for x in e['long_labels'])
    if 'cached' in sc:
        k = sc['cached']['cached']
        c['cached.used_at_start'] = k['start_minutes'] == '10,20' and k['minutes'] == '10,20'
        c['cached.polls_only_layout_entities'] = set(k['gets']) == VARIANT_ENTS
        c['cached.no_forecast_without_weather'] = k['forecasts'] == 0
        c['cached.rows_quiet'] = k['quiet_mutations'] == 0
        c['cached.self_update_still_works'] = len(k['update']['fetches']) >= 1 and len(k['update']['renews']) == 1 and \
            len(k['update']['reloads']) == 1
    if 'preview' in sc:
        r, pv = sc['preview'], sc['preview']['preview']
        c['preview.layout_from_preview'] = pv['start_minutes'] == '10,20' and pv['minutes'] == '10,20'
        c['preview.all_requests_to_preview_base'] = bool(r['api_paths']) and all(
            x.split(' ', 1)[1].startswith(PREVIEW_API) for x in r['api_paths'])
        c['preview.no_token'] = (not r['auth_headers'] and r['token_stored'] is None
                                 and 'No token' not in r['visible_text'] and 'Connected' in r['visible_text'])
        c['preview.polls_only_layout_entities'] = set(pv['gets']) == VARIANT_ENTS - {LAYOUT_SENSOR}
        c['preview.services_to_preview_base'] = (
            same_posts(pv['toggle'], [(PREVIEW_API + 'services/light/toggle', {'entity_id': 'light.bathroom_ceiling'})])
            and same_posts(pv['fan'], [(PREVIEW_API + 'services/script/turn_on',
                                        {'entity_id': 'script.bathroom_fan_timer', 'variables': {'minutes': 30}})]))
        c['preview.history_to_preview_base'] = bool(pv['history']) and all(h.startswith(PREVIEW_API + 'history/period/') for h in pv['history'])
        c['preview.sensor_card_page'] = pv['sensor_caption'] == 'Heater power'
        c['preview.draft_rerenders'] = pv['draft_shown'] and pv['draft_minutes'] == '45'
        c['preview.invalid_draft_ignored'] = pv['invalid_draft_ignored']
        c['preview.no_storage_writes'] = pv['storage_keys'] == 0
        c['preview.no_self_update'] = not pv['update']['fetches'] and not pv['update']['reloads'] and len(r['page_loads']) == 1
    return c


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
