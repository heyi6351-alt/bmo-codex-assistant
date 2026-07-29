#!/bin/sh
set -eu

PORT="${BMO_DISPLAY_PORT:-8765}"

xset -dpms
xset s off
xset s noblank

if command -v xrandr >/dev/null 2>&1 && [ -n "${BMO_DISPLAY_MODE:-}" ]; then
    OUTPUT="$(xrandr --query | awk '/ connected/{print $1; exit}')"
    if [ -n "$OUTPUT" ]; then
        xrandr --output "$OUTPUT" --mode "$BMO_DISPLAY_MODE" 2>/dev/null || true
    fi
fi

# bmo-kiosk orders After= bmo-display, but a Type=simple unit is "active" the
# instant the process forks — before the HTTP socket is listening. Chromium
# loads its URL exactly once, so a connection-refused at boot would leave a
# permanent error page. Wait (bounded) for the face server to answer first.
if command -v curl >/dev/null 2>&1; then
    i=0
    while [ "$i" -lt 40 ]; do
        if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
            break
        fi
        i=$((i + 1))
        sleep 0.5
    done
fi

if command -v unclutter >/dev/null 2>&1; then
    unclutter -idle 0.2 -root &
fi

exec chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-translate \
    --overscroll-history-navigation=0 \
    --app="http://127.0.0.1:${PORT}"
