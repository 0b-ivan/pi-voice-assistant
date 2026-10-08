#!/bin/sh
# Download the openWakeWord ONNX models for src/wakeword.py (no package, no
# sudo: models/ belongs to the service user). Hashes pinned to release v0.5.1.
# Code: Apache-2.0. Pre-trained models: CC BY-NC-SA 4.0 (non-commercial use).
#
#   sh scripts/install-wakeword.sh
# then in /etc/pi-voice-assistant.env: PTT_WAKE_WORD=hey_jarvis_v0.1
set -eu
D=${PTT_WAKE_MODEL_DIR:-/opt/pi-voice-assistant/models/wakeword}
BASE=https://github.com/dscripka/openWakeWord/releases/download/v0.5.1
mkdir -p "$D"
while read -r sha name; do
  if ! echo "$sha  $D/$name" | sha256sum -c --status 2>/dev/null; then
    curl -fsSL --retry 3 -o "$D/$name.part" "$BASE/$name"
    echo "$sha  $D/$name.part" | sha256sum -c --status || { echo "hash mismatch: $name" >&2; exit 1; }
    mv "$D/$name.part" "$D/$name"
  fi
done <<LIST
ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f melspectrogram.onnx
70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f embedding_model.onnx
94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb hey_jarvis_v0.1.onnx
LIST
ls -l "$D"
