#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-piper.sh" >&2
  exit 1
fi

BASE="/opt/pi-voice-assistant"
VENV="${BASE}/.venv"
TTS_DIR="${BASE}/tts"
VOICES=(
  "de_DE-thorsten-low"
  "de_DE-thorsten_emotional-medium"
)

apt-get update
apt-get install -y python3-venv alsa-utils ffmpeg

install -d -o root -g root -m 0755 "${BASE}" "${BASE}/src" "${BASE}/scripts"
install -d -o obivan -g obivan -m 0755 "${VENV}" "${TTS_DIR}"

if [[ ! -x "${VENV}/bin/python" ]]; then
  runuser -u obivan -- python3 -m venv "${VENV}"
fi

runuser -u obivan -- "${VENV}/bin/pip" install --upgrade pip
runuser -u obivan -- "${VENV}/bin/pip" install "piper-tts==1.8.0"
runuser -u obivan -- "${VENV}/bin/python" \
  -m piper.download_voices \
  --data-dir "${TTS_DIR}" \
  "${VOICES[@]}"

echo "Piper installed."
echo "Normal voice: ${TTS_DIR}/de_DE-thorsten-low.onnx"
echo "Servitor voice: ${TTS_DIR}/de_DE-thorsten_emotional-medium.onnx"
echo "Test: ${BASE}/src/speak.py \"SYSTEM NOMINAL. SERVITOR BEREIT.\""
