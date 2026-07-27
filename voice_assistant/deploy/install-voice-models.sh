#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo ./deploy/install-voice-models.sh" >&2
    exit 1
fi

MODEL_DIR="/opt/bmo/models"
SOURCE_DIR="/opt/bmo/src"
WHISPER_DIR="$SOURCE_DIR/whisper.cpp"
VOSK_NAME="vosk-model-small-cn-0.22"
VOSK_ZIP="/tmp/$VOSK_NAME.zip"

install -d "$MODEL_DIR" "$SOURCE_DIR"
apt-get update
apt-get install -y build-essential ca-certificates cmake curl git unzip

if [ ! -d "$WHISPER_DIR/.git" ]; then
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git "$WHISPER_DIR"
else
    git -C "$WHISPER_DIR" pull --ff-only
fi

cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DWHISPER_BUILD_TESTS=OFF \
    -DWHISPER_BUILD_EXAMPLES=ON
cmake --build "$WHISPER_DIR/build" --parallel 2
install -m 0755 "$WHISPER_DIR/build/bin/whisper-cli" /usr/local/bin/whisper-cli

if [ ! -f "$MODEL_DIR/ggml-base.bin" ]; then
    "$WHISPER_DIR/models/download-ggml-model.sh" base
    install -m 0644 "$WHISPER_DIR/models/ggml-base.bin" "$MODEL_DIR/ggml-base.bin"
fi

if [ ! -d "$MODEL_DIR/$VOSK_NAME" ]; then
    curl -fL \
        "https://alphacephei.com/vosk/models/$VOSK_NAME.zip" \
        -o "$VOSK_ZIP"
    unzip -q "$VOSK_ZIP" -d "$MODEL_DIR"
fi

chown -R bmo:bmo "$MODEL_DIR"

echo "Voice models are ready:"
echo "  $MODEL_DIR/ggml-base.bin"
echo "  $MODEL_DIR/$VOSK_NAME"
