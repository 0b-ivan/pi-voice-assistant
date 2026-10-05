#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-piper.sh" >&2
  exit 1
fi

VOICE="de_DE-thorsten-low"
BASE="/opt/pi-voice-assistant"
VENV="${BASE}/.venv"
TTS_DIR="${BASE}/tts"

apt-get update
apt-get install -y python3-venv alsa-utils

install -d -o obivan -g obivan -m 0755 "${BASE}" "${TTS_DIR}"

if [[ ! -x "${VENV}/bin/python" ]]; then
  runuser -u obivan -- python3 -m venv "${VENV}"
fi

runuser -u obivan -- "${VENV}/bin/pip" install --upgrade pip
runuser -u obivan -- "${VENV}/bin/pip" install "piper-tts==1.8.0"
runuser -u obivan -- "${VENV}/bin/python" \
  -m piper.download_voices \
  --data-dir "${TTS_DIR}" \
  "${VOICE}"

echo "Piper installed."
echo "Voice: ${TTS_DIR}/${VOICE}.onnx"
echo "Test: ${BASE}/src/speak.py \"Hallo Ivan, ich kann jetzt komplett lokal sprechen.\""
