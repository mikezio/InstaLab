#!/usr/bin/env bash
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-1}"
SCREEN_RES="${SCREEN_RES:-1366x768x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-7900}"

export DISPLAY=":${DISPLAY_NUM}"
export TZ="${TZ:-America/New_York}"

# Ensure X11 socket dir exists (Xvfb won't create it when non-root).
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

LOCK_FILE="/tmp/.X${DISPLAY_NUM}-lock"
SOCKET_FILE="/tmp/.X11-unix/X${DISPLAY_NUM}"
if [ -f "${LOCK_FILE}" ]; then
  pid="$(cat "${LOCK_FILE}" 2>/dev/null || true)"
  if [ -n "${pid}" ] && kill -0 "${pid}" >/dev/null 2>&1; then
    echo "Xvfb already running on display ${DISPLAY} (pid ${pid}); skipping cleanup"
  else
    rm -f "${LOCK_FILE}" "${SOCKET_FILE}"
  fi
fi

Xvfb "${DISPLAY}" -screen 0 "${SCREEN_RES}" -ac +extension RANDR &
for i in {1..20}; do
  [ -S "${SOCKET_FILE}" ] && break
  sleep 0.2
done

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
