#!/usr/bin/env python3
"""Load the wall page's Home Assistant token into the Wink Relay's browser,
then remove every trace of it except the browser's localStorage.

  device/provision-token.py <relay-ip> <panel page URL> <token file>

The token file holds one long-lived access token, for the panel's own
non-admin Home Assistant user (docs/5-wall-dashboard.md). Keep it chmod 600
and out of git.

The page stores a token it finds in its #token= fragment and drops the
fragment from the address, but the browser has already written the full
address to its history database by then, so this deletes that row and checks
that nothing with the token is left. Nothing secret is printed.
"""
import os
import subprocess
import sys
import tempfile
import time

DB = "/data/data/com.android.browser/databases/browser2.db"
SCRUB = f"""#!/system/bin/sh
sqlite3 {DB} "delete from history where url like '%token=%';"
echo "history rows with a token: $(sqlite3 {DB} "select count(*) from history where url like '%token=%';")"
echo "log lines with a token: $(logcat -d | grep -c 'token=')"
logcat -c
"""


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    ip, url, token_file = sys.argv[1:]
    dev = ip if ":" in ip else ip + ":5555"
    token = open(token_file).read().strip()

    def adb(*args):
        return subprocess.run(["adb", "-s", dev, *args], capture_output=True, text=True, timeout=60)

    subprocess.run(["adb", "connect", dev], capture_output=True, timeout=30)
    # A fresh ?p= makes this a real page load even if the panel is already
    # showing, and gets past Home Assistant's 31 day cache on /local/.
    r = adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
            "-n", "com.android.browser/.BrowserActivity",
            "--es", "com.android.browser.application_id", "wink-panel",
            "-d", f"{url}?p={int(time.time())}#token={token}")
    print((r.stdout + r.stderr).replace(token, "<token>").strip())
    time.sleep(8)
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(SCRUB)
    try:
        adb("push", f.name, "/data/local/tmp/panel-scrub.sh")
    finally:
        os.unlink(f.name)
    # adb joins its arguments with spaces, so hand the device shell one string.
    r = adb("shell", "su -c 'sh /data/local/tmp/panel-scrub.sh'")
    print(r.stdout.replace(token, "<token>").strip())
    adb("shell", "rm", "-f", "/data/local/tmp/panel-scrub.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
