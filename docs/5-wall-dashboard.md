# 5. Put the dashboard on the wall

The Relay's stock browser is a 2013 WebKit, far too old for Home Assistant's
own frontend. The page in `panel/` is written for it: plain ES5 JavaScript,
480 x 800 portrait, one file with everything inline, served by Home
Assistant from `/config/www`.

## Build the page

```bash
cd panel
cp config.example.json config.json
python3 build.py
```

`config.json` holds:

| Key | What |
|---|---|
| `out` | The file name to write in `panel/build/`, and to copy to `/config/www/` |
| `title` | The page title |
| `variant.layout` | The built-in home screen: the fan dial's fan, timer, script and steam helper, the motion sensor shown at the top, and the rows below the dial ([layout reference](layout.md)) |
| `variant.ent` | The panel's own helpers, from `wink_panel.yaml` and `wink_bathroom_fan.yaml`. Set `layout` to null if you will not publish a layout sensor |
| `variant.keys` | The names the page uses in the browser's storage |

Replace the entity ids in `variant.layout` with yours (the defaults match the
packages and the discovery script's defaults, plus `weather.home` and
`binary_sensor.bathroom_motion`). A missing entity shows as "--" rather than
breaking the page.

## Serve it from Home Assistant

Copy `panel/build/bathroom-panel.html` to `/config/www/` on Home Assistant
(the Samba, SSH or File editor add-on all work). It is then at
`http://<home-assistant>:8123/local/bathroom-panel.html`.

Serving it from Home Assistant matters: a page opened from the Relay's own
storage is a different origin, and the browser blocks its calls to the API.

Home Assistant serves `/local/` with a 31 day cache lifetime. The page checks
its own version stamp every 10 minutes and reloads itself when you copy a new
build over it, so updating is just copying the file again.

## Give it a token

`/local/` needs no login. If your Home Assistant is reachable from the
internet, so is everything in `/config/www`. **Never put a token in the page.**
The page takes its token once, from the address, keeps it in the browser's
local storage, and removes it from the address.

1. In Home Assistant, create a user for the panel (Settings > People > Users,
   "Can only log in from the local network" on, not an administrator).
2. Log in as that user, open its profile, Security tab, and create a
   long-lived access token.
3. Save the token in a file on your computer, readable only by you
   (`chmod 600`), outside this repo.
4. Load it into the Relay:

   ```bash
   device/provision-token.py <relay-ip> http://<home-assistant>:8123/local/bathroom-panel.html <token-file>
   ```

   This opens the page with the token, then deletes the browser history row
   and log lines that captured it, and prints how many are left (both should
   be 0). It never prints the token.

Be clear about what a non-admin token protects: it cannot change Home
Assistant's configuration or users, but it can still control every device
through the API from anywhere on your network. Anyone with adb access to the
Relay can read it. That is why limiting adb (below) matters.

After a rejected token the page tries only once every five minutes, since
each rejection counts as a failed login in Home Assistant.

## Set up the browser

```bash
device/setup-browser.sh <relay-ip> http://<home-assistant>:8123/local/bathroom-panel.html America/New_York
```

This makes the stock browser full screen with no address bar, sets the page
as its home page, sets Android's time zone (it is often unset, and the page's
clock then runs on GMT), and turns off a soft home button overlay some units
show. Reboot the Relay afterwards for the time zone.

## Install the boot hook

The boot hook keeps the dashboard working through reboots, power cuts and
Wi-Fi trouble:

```bash
adb push device/panel-boot.sh device/install-boot-hook.sh /data/local/tmp/
adb shell "su -c 'sh /data/local/tmp/install-boot-hook.sh http://<home-assistant>:8123/local/bathroom-panel.html <your-computer-ip>'"
```

The second address is optional: when given, the hook allows adb connections
only from that address. The command does not return while the hook runs;
press Ctrl+C once it prints "running as pid". To remove it:
`install-boot-hook.sh remove`.

Every 30 seconds the hook:

1. **Turns Wi-Fi power saving off.** This is what makes an untuned Relay feel
   slow. The Broadcom radio naps after 200 ms idle: on our unit that measured
   128 ms average and up to 1.2 s round trips, against 23 ms with it off.
   Android turns it back on after every DHCP renewal, hence the loop.
2. **Brings the page back to the front** if anything else has the screen.
3. **Reopens the page when Home Assistant comes back.** After a power cut the
   Relay often boots faster than Home Assistant, and the browser never
   retries a page that failed to load.

Once at boot it also stops Android from dimming the backlight to 2% a
minute after the last touch, which otherwise leaves a screen woken by
motion barely lit. The daemon sets the brightness instead, from Home
Assistant.

The hook is started from `/system/bin/edison-senzinit`, the Wink's own
sensor set-up script, which init runs as root at every boot. The usual
place, `install-recovery.sh`, belongs to KingRoot and refuses writes even
from root. The installer keeps the original as
`/system/bin/edison-senzinit.orig`. It logs what it does to
`/data/local/tmp/wink-panel-boot.log`.

## Using the dashboard

- Drag the ring to run the fan for 5 to 60 minutes; tap the middle for the
  default run, or to stop it. The quick buttons below run it for a set time.
- Tap the temperature, humidity or weather card for its detail page: a 24
  hour graph (the humidity graph also draws the usual level and shades the
  fan's runs), or today, the next 24 hours and the week. Any page returns
  home after 15 seconds without a touch.
- The gear opens quick settings. Screen: display mode (Auto, Day, Night),
  light look by day, theme, day and night brightness, how long the screen
  stays on. Room: the vanity's night dimming and hours, the fan's run length,
  whether steam starts the fan, whether the fan button lights the vanity.
- Alerts from `script.bathroom_panel_alert` appear as a banner over the dial
  and bring the page home. Tap to dismiss.
- The dot at the top right shows the link to Home Assistant: green when
  connected, red with a reason when not.

## Test changes offline

The harness loads the page in headless Chromium with Home Assistant's API
faked, at the Relay's screen size, and runs about 200 checks (no scrolling,
taps send the right calls, every look renders, the code parses as ES5)
without touching anything real:

```bash
cd panel
npm install
pip install playwright pillow && playwright install chromium
python3 harness.py build/bathroom-panel.html /tmp/panel-check
```

Screenshots of every scenario land in the output folder, with
`report.json`.

## Optional: change the home screen without rebuilding

The rows under the dial can come from Home Assistant instead of the
built-in layout: see [layouts](layout.md).

Next: [Sensors and calibration](6-sensors-and-calibration.md).
