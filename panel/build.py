#!/usr/bin/env python3
"""Build the Wink Relay wall page from panel.src.html and a config file.

  cd panel
  python3 build.py            # reads config.json, writes build/<out>
  python3 build.py my.json    # another config

config.json starts as a copy of config.example.json. It names the page's
file, its title, the built-in home screen layout and the Home Assistant
entities the dial, settings and alerts use (docs/5-wall-dashboard.md).

The Material Design Icons (Apache 2.0) the page uses go in as <symbol>s under
short ids, read from icons/ rather than typed by hand. Short ids keep the
page small, since every <use> repeats its id, and the inline CSS and JS lose
their comments and indentation: the Relay's browser is slow, and the page
should stay under about 140 KB. Copy the result to /config/www/ on your Home
Assistant; a panel already showing the page reloads itself within 10 minutes
when its version stamp changes.
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, 'icons')
ICONS = [('fan', 'fan'), ('fx', 'fan-off'), ('mi', 'mirror'), ('th', 'thermometer'),
         ('wp', 'water-percent'), ('wk', 'walk'), ('ms', 'motion-sensor'), ('wo', 'wifi-off'),
         ('al', 'alert-circle-outline'), ('su', 'weather-sunny'), ('ni', 'weather-night'),
         ('pc', 'weather-partly-cloudy'), ('pn', 'weather-night-partly-cloudy'),
         ('cl', 'weather-cloudy'), ('ra', 'weather-rainy'), ('po', 'weather-pouring'),
         ('sn', 'weather-snowy'), ('sr', 'weather-snowy-rainy'), ('fg', 'weather-fog'),
         ('ha', 'weather-hail'), ('li', 'weather-lightning'), ('lr', 'weather-lightning-rainy'),
         ('wi', 'weather-windy'), ('wv', 'weather-windy-variant'), ('bk', 'arrow-left'),
         ('cg', 'cog-outline'), ('ao', 'alert-octagon'), ('fr', 'fire'), ('wa', 'water-alert'),
         ('td', 'tumble-dryer'), ('br', 'bell-ring'), ('x', 'close'), ('pl', 'plus'), ('mn', 'minus'),
         ('ck', 'check'), ('au', 'brightness-auto'), ('b6', 'brightness-6'), ('ho', 'home-outline'),
         ('ln', 'lightbulb-night-outline'),
         # The plain ring a layout's unknown icon falls back to.
         ('nu', 'circle-outline')]
# The icons a layout may name (docs/layout.md, "Icons"): its key, the short
# id here and the MDI name. Keys the page already carries reuse their id.
PALETTE = [('mirror', 'mi', 'mirror'), ('thermometer', 'th', 'thermometer'),
           ('water-percent', 'wp', 'water-percent'), ('lightbulb', 'lb', 'lightbulb-outline'),
           ('ceiling-light', 'ce', 'ceiling-light'), ('lamp', 'fl', 'floor-lamp'), ('fan', 'fan', 'fan'),
           ('power', 'pw', 'power'), ('toggle', 'to', 'toggle-switch-outline'), ('door', 'dr', 'door'),
           ('lock', 'lk', 'lock-outline'), ('motion', 'ms', 'motion-sensor'), ('speaker', 'sp', 'speaker'),
           ('television', 'tv', 'television'), ('shower', 'sh', 'shower-head'), ('sparkles', 'cr', 'creation'),
           ('play', 'py', 'play-circle-outline'), ('bell', 'be', 'bell-outline'), ('flash', 'fs', 'flash'),
           ('gauge', 'gu', 'gauge'), ('home', 'ho', 'home-outline'), ('moon', 'ni', 'weather-night'),
           ('sun', 'su', 'weather-sunny'), ('fire', 'fr', 'fire')]


def symbol(short, name):
    path = os.path.join(D, name + '.svg')
    if not os.path.exists(path):
        sys.exit('icon %s not found in %s' % (name, D))
    d = re.search(r'<path d="([^"]+)"', open(path, encoding='utf-8').read()).group(1)
    return '<symbol id="i-%s" viewBox="0 0 24 24"><path d="%s"/></symbol>' % (short, d)


def slim(m):
    """Drop /* */ comments and indentation from inline CSS and JS. Nothing in
    them has "/*" inside a string or a regex, and ES5 has no multi-line
    strings, so this changes no token."""
    body = re.sub(r'/\*.*?\*/', '', m.group(2), flags=re.S)
    return m.group(1) + '\n'.join(l.strip() for l in body.split('\n') if l.strip()) + m.group(3)


def build(cfg):
    have = dict(ICONS)
    for key, short, name in PALETTE:
        assert have.setdefault(short, name) == name, 'icon id %s is taken by %s' % (short, have[short])
    html = open(os.path.join(HERE, 'panel.src.html'), encoding='utf-8').read()
    html = html.replace('<!--ICONS-->', '\n'.join(symbol(s, n) for s, n in have.items()))
    assert html.count('/*PALETTE*/{}') == 1
    html = html.replace('/*PALETTE*/{}', json.dumps({k: s for k, s, _ in PALETTE}, separators=(',', ':')))
    assert html.count('/*VARIANT*/null') == 1
    html = html.replace('/*VARIANT*/null', json.dumps(cfg['variant'], separators=(',', ':')))
    assert html.count('<title>Bathroom</title>') == 1
    html = html.replace('<title>Bathroom</title>', '<title>%s</title>' % cfg['title'])
    html = re.sub(r'(<(?:style|script)>\n?)(.*?)(</(?:style|script)>)', slim, html, flags=re.S)
    # The page compares this stamp with the copy on the server to know when to
    # reload itself (Home Assistant caches /local/ for 31 days).
    html = html.replace('__PANEL_VERSION__', hashlib.md5(html.encode()).hexdigest()[:10])
    os.makedirs(os.path.join(HERE, 'build'), exist_ok=True)
    dst = os.path.join(HERE, 'build', cfg['out'])
    open(dst, 'w', encoding='utf-8').write(html)
    print(dst, len(html.encode()), 'bytes')


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'config.json')
    if not os.path.exists(name):
        sys.exit('%s not found: copy config.example.json to config.json and edit it' % name)
    build(json.load(open(name, encoding='utf-8')))


if __name__ == '__main__':
    main()
