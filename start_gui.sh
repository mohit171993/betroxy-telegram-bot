#!/usr/bin/env bash
set -e
export DISPLAY=:99
export TZ=Asia/Dubai
mkdir -p /tmp/.X11-unix /data /home/browser
chown -R browser:browser /data /home/browser

Xvfb :99 -screen 0 1365x900x24 -ac +extension GLX +render -noreset &
fluxbox &

# Start a normal Google Chrome process as a non-root desktop user.
# This avoids --no-sandbox and Playwright's automation launch flags while keeping
# a single persistent profile that the checker can attach to over local CDP.
runuser -u browser -- env DISPLAY=:99 HOME=/home/browser TZ=Asia/Dubai PYTHONUNBUFFERED=1 python /app/cloud_gui_browser.py &

# Start the safe 3-link checker. It only attaches to the already-running Chrome.
python /app/cloud_checker_gui_shared_test.py &

PASS="${LOGIN_SETUP_TOKEN:-betroxy}"
PASS="${PASS:0:8}"
x11vnc -storepasswd "$PASS" /tmp/vnc.pass >/dev/null
x11vnc -display :99 -rfbauth /tmp/vnc.pass -forever -shared -rfbport 5900 -noxdamage &
exec websockify --web=/usr/share/novnc/ "${PORT:-8080}" localhost:5900
