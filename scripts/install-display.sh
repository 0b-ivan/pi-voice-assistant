#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-display.sh" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

apt-get update
apt-get install -y python3-venv python3-pil

install -d -m 0755 /opt/pi-voice-assistant/src
install -m 0644   "${repo_root}/src/display.py"   /opt/pi-voice-assistant/src/display.py

if [[ ! -x /opt/pi-voice-assistant/.venv-display/bin/python ]]; then
  python3 -m venv     --system-site-packages     /opt/pi-voice-assistant/.venv-display
fi

/opt/pi-voice-assistant/.venv-display/bin/python -m pip install   --upgrade pip

/opt/pi-voice-assistant/.venv-display/bin/python -m pip install   adafruit-blinka   adafruit-circuitpython-rgb-display

install -m 0644   "${repo_root}/deploy/pi-display.service"   /etc/systemd/system/pi-display.service

systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/pi-display.service

echo
echo "Display service installed."
echo "Start with:"
echo "  sudo systemctl enable --now pi-display.service"
