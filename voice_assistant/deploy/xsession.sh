#!/bin/sh
set -eu

xset -dpms
xset s off
xset s noblank

if command -v xrandr >/dev/null 2>&1 && [ -n "${BMO_DISPLAY_MODE:-}" ]; then
    OUTPUT="$(xrandr --query | awk '/ connected/{print $1; exit}')"
    if [ -n "$OUTPUT" ]; then
        xrandr --output "$OUTPUT" --mode "$BMO_DISPLAY_MODE" 2>/dev/null || true
    fi
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
    --app=http://127.0.0.1:8765
