#!/usr/bin/env bash
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-1}"
SCREEN_RES="${SCREEN_RES:-1366x768x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-7900}"

export DISPLAY=":${DISPLAY_NUM}"

# Ensure X11 socket dir exists (Xvfb won't create it when non-root).
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

Xvfb "${DISPLAY}" -screen 0 "${SCREEN_RES}" -ac +extension RANDR &
sleep 0.5

fluxbox >/tmp/fluxbox.log 2>&1 &

# Local-only VNC; no password (bind to localhost in docker-compose)
x11vnc -display "${DISPLAY}" -rfbport "${VNC_PORT}" -forever -shared -nopw -localhost >/tmp/x11vnc.log 2>&1 &

NOVNC_BIN=""
if command -v novnc_proxy >/dev/null 2>&1; then
  NOVNC_BIN="$(command -v novnc_proxy)"
elif [ -x /usr/share/novnc/utils/novnc_proxy ]; then
  NOVNC_BIN="/usr/share/novnc/utils/novnc_proxy"
fi

if [ -n "${NOVNC_BIN}" ]; then
  "${NOVNC_BIN}" --vnc "localhost:${VNC_PORT}" --listen "${NOVNC_PORT}" >/tmp/novnc.log 2>&1 &
else
  echo "novnc_proxy not found; noVNC will not start" >/tmp/novnc.log
fi

tail -f /tmp/x11vnc.log
