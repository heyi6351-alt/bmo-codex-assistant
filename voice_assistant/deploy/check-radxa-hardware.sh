#!/bin/sh
set -u

PASS=0
FAIL=0

ok() {
    PASS=$((PASS + 1))
    printf 'PASS  %s\n' "$1"
}

bad() {
    FAIL=$((FAIL + 1))
    printf 'FAIL  %s\n' "$1"
}

MODEL="unknown"
if [ -r /proc/device-tree/model ]; then
    MODEL="$(tr -d '\000' </proc/device-tree/model)"
fi
case "$MODEL" in
    *"Radxa ZERO 3W"*|*"Radxa Zero 3W"*) ok "board: $MODEL" ;;
    *) bad "board is not Radxa ZERO 3W: $MODEL" ;;
esac

if [ "$(uname -m)" = "aarch64" ]; then
    ok "architecture: aarch64"
else
    bad "architecture: $(uname -m)"
fi

if lsusb 2>/dev/null | grep -qi 'respeaker\|xmos'; then
    ok "ReSpeaker USB device detected"
else
    bad "ReSpeaker USB device not detected"
fi

if arecord -l 2>/dev/null | grep -qi 'respeaker\|xmos\|xu316'; then
    ok "ALSA capture device detected"
else
    bad "ALSA capture device missing"
fi

if aplay -l 2>/dev/null | grep -qi 'respeaker\|xmos\|xu316'; then
    ok "ALSA playback device detected"
else
    bad "ALSA playback device missing"
fi

HDMI_CONNECTED=0
for status in /sys/class/drm/*/status; do
    if [ -r "$status" ] && grep -qx connected "$status"; then
        HDMI_CONNECTED=1
        break
    fi
done
if [ "$HDMI_CONNECTED" -eq 1 ]; then
    ok "HDMI display connected"
else
    bad "HDMI display not connected"
fi

if systemctl is-active --quiet bmo-display.service; then
    ok "bmo-display.service active"
else
    bad "bmo-display.service inactive"
fi

if curl -fsS http://127.0.0.1:8765/healthz 2>/dev/null | grep -q '"ok": true'; then
    ok "BMO face health endpoint"
else
    bad "BMO face health endpoint unavailable"
fi

printf '\nHardware check: %s passed, %s failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
