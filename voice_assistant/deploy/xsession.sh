#!/bin/sh
set -eu

xset -dpms
xset s off
xset s noblank

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
