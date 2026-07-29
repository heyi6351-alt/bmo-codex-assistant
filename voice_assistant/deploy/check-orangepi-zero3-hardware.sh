#!/bin/sh
set -u

# Two-part self-check. The HARDWARE ACCEPTANCE section decides the exit code so a
# hardware installer can sign off a board BEFORE the owner authorizes any
# accounts. The OWNER PROVISIONING section is informational only (NOTE lines):
# Codex/GitHub/Lark auth is the owner's job — per the bringup doc the hardware
# installer must not touch those credentials — so a missing auth must never make
# the hardware check report failure.

PASS=0
FAIL=0
NOTES=0

ok() {
    PASS=$((PASS + 1))
    printf 'PASS  %s\n' "$1"
}

bad() {
    FAIL=$((FAIL + 1))
    printf 'FAIL  %s\n' "$1"
}

note() {
    NOTES=$((NOTES + 1))
    printf 'NOTE  %s\n' "$1"
}

printf '== Hardware acceptance (determines exit code) ==\n'

MODEL="unknown"
if [ -r /proc/device-tree/model ]; then
    MODEL="$(tr -d '\000' </proc/device-tree/model)"
fi
case "$MODEL" in
    *"OrangePi Zero3"*|*"Orange Pi Zero3"*|*"Orange Pi Zero 3"*)
        ok "board: $MODEL"
        ;;
    *) bad "board is not Orange Pi Zero 3: $MODEL" ;;
esac

if [ "$(uname -m)" = "aarch64" ]; then
    ok "architecture: aarch64"
else
    bad "architecture: $(uname -m)"
fi

MEMTOTAL_KIB="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo 2>/dev/null || true)"
if [ -n "$MEMTOTAL_KIB" ] && [ "$MEMTOTAL_KIB" -ge 1500000 ]; then
    ok "memory: ${MEMTOTAL_KIB} KiB"
else
    bad "expected the 2 GB board; MemTotal is ${MEMTOTAL_KIB:-unknown} KiB"
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

# The on-screen face (chromium kiosk) needs no account credentials, so it is a
# genuine hardware/display acceptance item.
if systemctl is-active --quiet bmo-kiosk.service; then
    ok "bmo-kiosk.service active"
else
    bad "bmo-kiosk.service inactive (start with: systemctl enable --now bmo-kiosk)"
fi

# git/gh binaries are installed by install-orangepi-zero3.sh, so their presence
# is a hardware-side install check (their authentication is owner-only, below).
if command -v git >/dev/null 2>&1; then
    ok "Git CLI installed"
else
    bad "Git CLI missing"
fi

if command -v gh >/dev/null 2>&1; then
    ok "GitHub CLI installed"
else
    bad "GitHub CLI missing"
fi

printf '\n== Owner provisioning (informational; does not affect exit code) ==\n'

# bmo-voice needs the owner's Codex/Lark install and a resolvable audio device;
# it legitimately stays inactive during the hardware-only phase.
if systemctl is-active --quiet bmo-voice.service; then
    note "bmo-voice.service active"
else
    note "bmo-voice.service inactive (owner authorizes Codex/Lark, then: systemctl enable --now bmo-voice)"
fi

# Acceptance requires all three services to auto-recover after a reboot, which
# means each must be enabled. Report, but do not fail, so hardware can sign off
# before the owner enables the auth-dependent voice service.
for svc in bmo-display bmo-kiosk bmo-voice; do
    if systemctl is-enabled --quiet "$svc.service" 2>/dev/null; then
        note "$svc.service enabled (auto-starts on boot)"
    else
        note "$svc.service not enabled for boot; run: systemctl enable $svc"
    fi
done

if command -v gh >/dev/null 2>&1 && id bmo >/dev/null 2>&1 \
    && runuser -u bmo -- gh auth status >/dev/null 2>&1; then
    note "GitHub CLI authenticated for bmo"
else
    note "GitHub CLI not authenticated for bmo (owner: sudo -iu bmo gh auth login)"
fi

if id bmo >/dev/null 2>&1 && runuser -u bmo -- sh -lc 'command -v codex' >/dev/null 2>&1; then
    note "Codex CLI available for bmo"
else
    note "Codex CLI not installed for bmo (owner step)"
fi

if id bmo >/dev/null 2>&1 && runuser -u bmo -- sh -lc 'command -v lark-cli' >/dev/null 2>&1; then
    note "lark-cli available for bmo"
else
    note "lark-cli not installed for bmo (owner step)"
fi

printf '\nHardware check: %s passed, %s failed, %s notes\n' "$PASS" "$FAIL" "$NOTES"
if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
