#!/usr/bin/env bash
# Point the Wink Relay's stock browser at the wall page, full screen.
#
#   device/setup-browser.sh <relay-ip> <panel page URL> [time zone]
#
# For example:
#   device/setup-browser.sh 10.0.0.50 http://10.0.0.10:8123/local/bathroom-panel.html America/New_York
#
# It stops the browser, writes its preferences (full screen, quick controls
# so no address bar shows, the page as home page, no form or security
# prompts), sets Android's time zone if one is given (it is often unset, and
# the browser then runs its clock on GMT), and disables the soft home button
# overlay some units carry. A reboot afterwards applies the time zone.
set -euo pipefail

IP="${1:-}"
URL="${2:-}"
TZ_NAME="${3:-}"
[[ -n "$IP" && -n "$URL" ]] || { echo "usage: $0 <relay-ip> <page-url> [time-zone]" >&2; exit 2; }
D="$IP:5555"
adb connect "$D" >/dev/null
A=(adb -s "$D")
root() { "${A[@]}" shell "su -c '$1'"; }

PREFS=/data/data/com.android.browser/shared_prefs/com.android.browser_preferences.xml
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<XML
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="fullscreen" value="true" />
    <boolean name="enable_quick_controls" value="true" />
    <boolean name="show_security_warnings" value="false" />
    <boolean name="save_formdata" value="false" />
    <string name="homepage">$URL</string>
</map>
XML

root 'am force-stop com.android.browser'
"${A[@]}" push "$TMP" /data/local/tmp/browser_prefs.xml
# Keep the browser's own owner on the file, or it cannot read it.
OWNER="$(root 'ls -ld /data/data/com.android.browser' | awk '{print $2}' | tr -d '\r')"
root "mkdir -p /data/data/com.android.browser/shared_prefs"
root "cat /data/local/tmp/browser_prefs.xml > $PREFS"
root "chown $OWNER:$OWNER $PREFS /data/data/com.android.browser/shared_prefs"
root "chmod 660 $PREFS"
root 'rm /data/local/tmp/browser_prefs.xml'

root 'pm disable com.home.button.bottom' >/dev/null 2>&1 || true
if [[ -n "$TZ_NAME" ]]; then
  root "setprop persist.sys.timezone $TZ_NAME"
  echo "time zone set to $TZ_NAME: reboot the Relay to apply it"
fi
"${A[@]}" shell am start -a android.intent.action.VIEW -d "$URL" \
  -n com.android.browser/.BrowserActivity --es com.android.browser.application_id wink-panel >/dev/null
echo "browser set up"
