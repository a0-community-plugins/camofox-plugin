#!/bin/bash
# CamoFox VNC Watchdog
# Monitors camoufox-bin display and keeps x11vnc pointed at the correct one.
# Handles crashes, respawns, and display number changes.

LAST_DISP=""

echo "[watchdog] Started PID=$$"

while true; do
  # Detect current display used by camoufox-bin
  DISP=$(for pid in $(pgrep -f 'camoufox-bin' 2>/dev/null); do
    cat /proc/$pid/environ 2>/dev/null | tr '\0' '\n' | grep ^DISPLAY=
  done | sort -u | head -1 | sed 's/DISPLAY=//')

  if [ -z "$DISP" ]; then
    # camoufox-bin not running — kill stale x11vnc if any
    if pgrep -f x11vnc > /dev/null 2>&1; then
      echo "[watchdog] camoufox-bin gone, killing stale x11vnc"
      pkill -f x11vnc 2>/dev/null
    fi
    LAST_DISP=""
    sleep 3
    continue
  fi

  X11VNC_RUNNING=$(pgrep -f x11vnc > /dev/null 2>&1 && echo yes || echo no)
  X11VNC_DISP=$(pgrep -fa x11vnc 2>/dev/null | grep -o 'display :[0-9]*' | awk '{print $2}' | head -1)

  # Restart x11vnc if: not running, or watching wrong display
  if [ "$X11VNC_RUNNING" = "no" ] || [ "$X11VNC_DISP" != "$DISP" ]; then
    echo "[watchdog] $(date): x11vnc state=$X11VNC_RUNNING watching=$X11VNC_DISP target=$DISP — restarting"
    pkill -f x11vnc 2>/dev/null
    sleep 1
    nohup x11vnc -display $DISP -rfbport 5999 -nopw -forever -shared \
      -listen 127.0.0.1 -noxdamage >> /tmp/x11vnc.log 2>&1 &
    echo "[watchdog] x11vnc started on $DISP (PID=$!)"
    LAST_DISP="$DISP"
    sleep 3
  fi

  sleep 3
done
