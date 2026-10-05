#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-voice-service.sh" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_was_active=0
if systemctl is-active --quiet pi-ptt.service; then
  service_was_active=1
  systemctl stop pi-ptt.service
fi

install -d -m 0755 /opt/pi-voice-assistant/src
install -m 0644 "${repo_root}/src/ptt.py" /opt/pi-voice-assistant/src/ptt.py
install -m 0644 "${repo_root}/src/transcribe.py" /opt/pi-voice-assistant/src/transcribe.py
install -m 0644 "${repo_root}/deploy/pi-ptt.service" /etc/systemd/system/pi-ptt.service

if [[ ! -e /etc/pi-ptt.env ]]; then
  install -m 0644 "${repo_root}/config/ptt.env.example" /etc/pi-ptt.env
  echo "Created /etc/pi-ptt.env; review it before starting the service."
fi

created_openrouter_env=0
if [[ ! -e /etc/pi-voice-assistant.env ]]; then
  install -o root -g obivan -m 0640 "${repo_root}/config/openrouter.env.example" /etc/pi-voice-assistant.env
  created_openrouter_env=1
  echo "Created /etc/pi-voice-assistant.env (root:obivan, 0640); set OPENROUTER_API_KEY with Vim before starting."
fi

systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/pi-ptt.service

if [[ ${created_openrouter_env} -eq 1 ]]; then
  echo "Service left stopped until OPENROUTER_API_KEY is configured."
elif [[ ${service_was_active} -eq 1 ]]; then
  systemctl start pi-ptt.service
  echo "pi-ptt.service restarted."
else
  echo "Install complete. Start with: sudo systemctl enable --now pi-ptt.service"
fi
