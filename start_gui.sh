#!/usr/bin/env bash
set -e
export DISPLAY=:99
mkdir -p /tmp/.X11-unix
Xvfb :99 -screen 0 1365x900x24 -ac +extension GLX +render -noreset &
fluxbox &
python /app/cloud_gui_browser.py &
PASS="${LOGIN_SETUP_TOKEN:-betroxy}"
PASS="${PASS:0:8}"
x11vnc -storepasswd "$PASS" /tmp/vnc.pass >/dev/null
x11vnc -display :99 -rfbauth /tmp/vnc.pass -forever -shared -rfbport 5900 -noxdamage &
exec websockify --web=/usr/share/novnc/ "${PORT:-8080}" localhost:5900
