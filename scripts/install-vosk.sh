#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-vosk.sh" >&2
  exit 1
fi

VOSK_VERSION="0.3.45"
MODEL_NAME="vosk-model-small-de-0.15"
MODEL_URL="https://alphacephei.com/vosk/models/${MODEL_NAME}.zip"
ROOT="/opt/pi-voice-assistant"
VENDOR_DIR="${ROOT}/vendor"
MODELS_DIR="${ROOT}/models"
MODEL_DIR="${MODELS_DIR}/${MODEL_NAME}"

service_was_active=0
if systemctl is-active --quiet pi-ptt.service; then
  service_was_active=1
  systemctl stop pi-ptt.service
fi

apt-get update
apt-get install -y python3-pip ca-certificates curl unzip

install -d -m 0755 "${VENDOR_DIR}" "${MODELS_DIR}"

/usr/bin/python3 -m pip install   --disable-pip-version-check   --no-cache-dir   --upgrade   --target "${VENDOR_DIR}"   --break-system-packages   "vosk==${VOSK_VERSION}"

if [[ ! -d "${MODEL_DIR}" ]]; then
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' EXIT
  curl --fail --location --retry 3     --output "${tmpdir}/${MODEL_NAME}.zip"     "${MODEL_URL}"
  unzip -q "${tmpdir}/${MODEL_NAME}.zip" -d "${tmpdir}"
  mv "${tmpdir}/${MODEL_NAME}" "${MODEL_DIR}"
fi

chown -R root:root "${VENDOR_DIR}" "${MODELS_DIR}"
chmod -R a+rX "${VENDOR_DIR}" "${MODELS_DIR}"

/usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "/opt/pi-voice-assistant/vendor")
import vosk
print("Vosk import OK:", vosk.__file__)
PY

echo "Vosk ${VOSK_VERSION} installed."
echo "German model: ${MODEL_DIR}"
echo "Set STT_PROVIDER=vosk or STT_PROVIDER=auto in /etc/pi-voice-assistant.env."

if [[ ${service_was_active} -eq 1 ]]; then
  systemctl start pi-ptt.service
  echo "pi-ptt.service restarted."
fi
