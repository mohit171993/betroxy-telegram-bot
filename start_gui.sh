#!/usr/bin/env bash
set -e
export DISPLAY=:99
export TZ=Asia/Dubai
export LIBGL_ALWAYS_SOFTWARE=1
export CHROME_PROFILE_DIR=/data/betroxy_chrome_profile_v3

mkdir -p /tmp/.X11-unix /data /home/browser /run/dbus
chown -R browser:browser /data /home/browser

# Give Chrome a normal DBus environment instead of a partially missing desktop.
if [ ! -S /run/dbus/system_bus_socket ]; then
  dbus-daemon --system --fork || true
fi

# 1368 is divisible by 4, avoiding x11vnc framebuffer glitches seen at 1365.
Xvfb :99 -screen 0 1368x900x24 -ac +extension GLX +render -noreset &

# Minimal normal desktop window manager. feh prevents the old Fluxbox wallpaper
# xmmessage popup from blocking the browser.
runuser -u browser -- env DISPLAY=:99 HOME=/home/browser TZ=Asia/Dubai fluxbox >/tmp/fluxbox.log 2>&1 &
sleep 1

# Start one persistent, visible Google Chrome. No Playwright launches Chrome.
runuser -u browser -- dbus-run-session -- env \
  DISPLAY=:99 HOME=/home/browser TZ=Asia/Dubai LIBGL_ALWAYS_SOFTWARE=1 \
  PYTHONUNBUFFERED=1 CHROME_PROFILE_DIR="$CHROME_PROFILE_DIR" \
  python /app/cloud_gui_browser.py &

# IMPORTANT: do not attach Playwright/checker while the user is logging in.
# The gate only starts the safe 3-link checker after Chrome has actually stored
# an Instagram sessionid cookie. Cookie contents are never read or logged.
runuser -u browser -- env \
  DISPLAY=:99 HOME=/home/browser TZ=Asia/Dubai PYTHONUNBUFFERED=1 \
  CHROME_PROFILE_DIR="$CHROME_PROFILE_DIR" \
  python /app/cloud_login_gate.py &

PASS="${LOGIN_SETUP_TOKEN:-betroxy}"
PASS="${PASS:0:8}"
x11vnc -storepasswd "$PASS" /tmp/vnc.pass >/dev/null
x11vnc -display :99 -rfbauth /tmp/vnc.pass -forever -shared -rfbport 5900 \
  -noxdamage -nowf -nowcr -noscr &

exec websockify --web=/usr/share/novnc/ "${PORT:-8080}" localhost:5900
