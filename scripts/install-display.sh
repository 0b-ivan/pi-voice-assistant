#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-display.sh" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_was_active=0
if systemctl is-active --quiet pi-display.service; then
  service_was_active=1
fi

# Use the same package preflight as the regular git update installer.
bash "${repo_root}/scripts/ensure-system-packages.sh" \
  python3-venv python3-pil python3-qrcode iw

install -d -m 0755 /opt/pi-voice-assistant/src
install -m 0644   "${repo_root}/src/display.py"   /opt/pi-voice-assistant/src/display.py
install -m 0644   "${repo_root}/src/hotspot_config.py" /opt/pi-voice-assistant/src/hotspot_config.py

if [[ ! -x /opt/pi-voice-assistant/.venv-display/bin/python ]]; then
  python3 -m venv     --system-site-packages     /opt/pi-voice-assistant/.venv-display
fi

/opt/pi-voice-assistant/.venv-display/bin/python -c 'import qrcode; print("QR bereit")'

/opt/pi-voice-assistant/.venv-display/bin/python -m pip install   adafruit-blinka   adafruit-circuitpython-rgb-display

install -m 0644   "${repo_root}/deploy/pi-display.service"   /etc/systemd/system/pi-display.service

systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/pi-display.service

if [[ ${service_was_active} -eq 1 ]]; then
  systemctl restart pi-display.service
  echo "pi-display.service restarted."
else
  echo
  echo "Display service installed."
  echo "Start with:"
  echo "  sudo systemctl enable --now pi-display.service"
fi
