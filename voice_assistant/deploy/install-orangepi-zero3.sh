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
    xserver-xorg

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
install -d -o bmo -g bmo /run/bmo
install -d -m 0750 /etc/bmo

rsync -a \
    --exclude .venv \
    --exclude __pycache__ \
    --exclude '*.pyc' \
    "$SOURCE_DIR/" "$TARGET_DIR/"
chown -R bmo:bmo "$TARGET_DIR" /var/lib/bmo /run/bmo

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

systemctl daemon-reload
systemctl enable bmo-display.service

echo
echo "Base Orange Pi Zero 3 installation completed."
echo "Detected board: $MODEL"
echo
echo "Next:"
echo "  1. sudo ./deploy/install-voice-models.sh"
echo "  2. sudo -iu bmo"
echo "  3. Install Codex with the official standalone installer:"
echo "     curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh"
echo "  4. On this headless board run: codex login --device-auth"
echo "  5. Install/configure lark-cli, then authorize user scopes."
echo "  6. Exit the bmo shell and run: sudo systemctl enable --now bmo-display bmo-kiosk bmo-voice"
