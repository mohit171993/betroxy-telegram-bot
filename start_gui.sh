#!/usr/bin/env bash
set -e
export DISPLAY=:99
export TZ=Asia/Dubai
mkdir -p /tmp/.X11-unix /data /home/browser
chown -R browser:browser /data /home/browser

Xvfb :99 -screen 0 1365x900x24 -ac +extension GLX +render -noreset &

# Start Fluxbox for a normal desktop window manager.  feh is installed so
# Fluxbox's background helper does not raise the old xmmessage popup.
runuser -u browser -- env DISPLAY=:99 HOME=/home/browser TZ=Asia/Dubai fluxbox >/tmp/fluxbox.log 2>&1 &
sleep 1

# Start persistent Google Chrome in its own DBus desktop session.  Chrome is a
# normal process (not Playwright-launched); the checker attaches to it over CDP.
runuser -u browser -- dbus-run-session -- env \
  DISPLAY=:99 HOME=/home/browser TZ=Asia/Dubai PYTHONUNBUFFERED=1 \
  python /app/cloud_gui_browser.py &

# Checker is safe test mode and reconnects to the shared browser.
python /app/cloud_checker_gui_shared_test.py &

PASS="${LOGIN_SETUP_TOKEN:-betroxy}"
PASS="${PASS:0:8}"
x11vnc -storepasswd "$PASS" /tmp/vnc.pass >/dev/null
x11vnc -display :99 -rfbauth /tmp/vnc.pass -forever -shared -rfbport 5900 -noxdamage &
exec websockify --web=/usr/share/novnc/ "${PORT:-8080}" localhost:5900
