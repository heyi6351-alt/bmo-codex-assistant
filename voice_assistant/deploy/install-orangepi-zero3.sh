#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root: sudo ./deploy/install-orangepi-zero3.sh" >&2
    exit 1
fi

ARCH="$(uname -m)"
if [ "$ARCH" != "aarch64" ] && [ "$ARCH" != "arm64" ]; then
    echo "Expected an ARM64 board, got: $ARCH" >&2
    exit 1
fi

MODEL="unknown"
if [ -r /proc/device-tree/model ]; then
    MODEL="$(tr -d '\000' </proc/device-tree/model)"
fi
case "$MODEL" in
    *"OrangePi Zero3"*|*"Orange Pi Zero3"*|*"Orange Pi Zero 3"*)
        ;;
    *)
        if [ "${BMO_ALLOW_OTHER_BOARD:-0}" != "1" ]; then
            echo "Expected Orange Pi Zero 3, detected: $MODEL" >&2
            echo "Set BMO_ALLOW_OTHER_BOARD=1 only for a deliberate compatible-board test." >&2
            exit 1
        fi
        ;;
esac

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SOURCE_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="/opt/bmo/voice_assistant"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
    alsa-utils \
    build-essential \
    ca-certificates \
    chromium \
    cmake \
    curl \
    espeak-ng \
    git \
    gh \
    jq \
    libasound2-dev \
    libportaudio2 \
    nodejs \
    npm \
    pkg-config \
    portaudio19-dev \
    python3 \
    python3-pip \
    python3-venv \
    rsync \
    unclutter \
    unzip \
    xinit \
    x11-xserver-utils \
    xserver-xorg \
    xserver-xorg-legacy

if ! id bmo >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash bmo
fi

for group in audio video input render; do
    if getent group "$group" >/dev/null 2>&1; then
        usermod -a -G "$group" bmo
    fi
done

install -d -o bmo -g bmo /opt/bmo
install -d -o bmo -g bmo "$TARGET_DIR"
install -d -o bmo -g bmo /opt/bmo/models
install -d -o bmo -g bmo /var/lib/bmo/projects
install -d -o bmo -g bmo /var/lib/bmo/codex_workspace
install -d -o bmo -g bmo /run/bmo
install -d -m 0750 /etc/bmo

rsync -a \
    --exclude .venv \
    --exclude __pycache__ \
    --exclude '*.pyc' \
    "$SOURCE_DIR/" "$TARGET_DIR/"
chown -R bmo:bmo "$TARGET_DIR" /var/lib/bmo /run/bmo

# Codex's working directory must be writable at runtime. bmo-voice runs under
# ProtectSystem=strict, which mounts /opt read-only, so the workspace lives in
# the writable state tree. Seed it with the policy file shipped in the repo.
install -m 0644 -o bmo -g bmo \
    "$SOURCE_DIR/codex_workspace/AGENTS.md" \
    /var/lib/bmo/codex_workspace/AGENTS.md

if [ ! -x "$TARGET_DIR/.venv/bin/python" ]; then
    runuser -u bmo -- python3 -m venv "$TARGET_DIR/.venv"
fi
runuser -u bmo -- "$TARGET_DIR/.venv/bin/pip" install \
    --disable-pip-version-check \
    -r "$TARGET_DIR/requirements.txt"

if [ ! -e /etc/bmo/voice.env ]; then
    install -m 0640 -o root -g bmo "$SOURCE_DIR/.env.example" /etc/bmo/voice.env
fi
if [ ! -e /etc/bmo/display.env ]; then
    install -m 0644 "$SOURCE_DIR/deploy/display.env.example" /etc/bmo/display.env
fi

install -m 0644 "$SOURCE_DIR/deploy/bmo-voice.service" \
    /etc/systemd/system/bmo-voice.service
install -m 0644 "$SOURCE_DIR/deploy/bmo-display.service" \
    /etc/systemd/system/bmo-display.service
install -m 0644 "$SOURCE_DIR/deploy/bmo-kiosk.service" \
    /etc/systemd/system/bmo-kiosk.service
install -m 0755 "$SOURCE_DIR/deploy/xsession.sh" \
    "$TARGET_DIR/deploy/xsession.sh"

# /run is tmpfs and is wiped on every boot; recreate /run/bmo declaratively so
# bmo-voice's ReadWritePaths=/run/bmo bind mount always has a source.
install -m 0644 "$SOURCE_DIR/deploy/bmo-tmpfiles.conf" \
    /etc/tmpfiles.d/bmo.conf
systemd-tmpfiles --create /etc/tmpfiles.d/bmo.conf

# The kiosk launches Xorg as the unprivileged bmo user from a systemd unit on a
# server image with no display manager. Allow the setuid X wrapper to grant it
# a graphical seat instead of refusing with "only console users are allowed".
if [ ! -e /etc/X11/Xwrapper.config ]; then
    install -d /etc/X11
    printf 'allowed_users=anybody\nneeds_root_rights=yes\n' \
        >/etc/X11/Xwrapper.config
fi

systemctl daemon-reload
systemctl enable bmo-display.service

echo
echo "Base Orange Pi Zero 3 installation completed."
echo "Detected board: $MODEL"
echo
echo "Next (hardware installer — no credentials needed):"
echo "  1. sudo ./deploy/install-voice-models.sh"
echo "  2. Bring up the screen: sudo systemctl enable --now bmo-display bmo-kiosk"
echo "  3. Verify the hardware:  sudo ./deploy/check-orangepi-zero3-hardware.sh"
echo "     (Auth-dependent lines report NOTE, not FAIL, so hardware can be"
echo "      signed off before the owner authorizes any accounts.)"
echo
echo "Account authorization is the BMO OWNER's job, NOT the hardware installer's."
echo "Do not enter Codex, GitHub, or Lark credentials on the owner's behalf."
echo "See the owner section of HARDWARE_BRINGUP_ORANGEPI_ZERO3_ZH.md. In short,"
echo "as the bmo user (sudo -iu bmo):"
echo "  - curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh"
echo "  - codex login --device-auth"
echo "  - gh auth login"
echo "  - install/configure lark-cli, then: lark-cli auth login --domain calendar,task,im"
echo "Then start the assistant: sudo systemctl enable --now bmo-voice"
