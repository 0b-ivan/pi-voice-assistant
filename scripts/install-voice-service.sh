#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-voice-service.sh" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The service needs this group even when the optional SHIM is disabled.
# Validate dependencies before stopping an already working voice service.
if ! getent group i2c >/dev/null || ! /usr/bin/python3 -c 'import smbus'; then
  echo "Install hardware dependency first: sudo apt install python3-smbus i2c-tools" >&2
  exit 1
fi
service_was_active=0
if systemctl is-active --quiet pi-ptt.service; then
  service_was_active=1
  systemctl stop pi-ptt.service
fi

install -d -m 0755 /opt/pi-voice-assistant/src /opt/pi-voice-assistant/scripts
install -m 0644 "${repo_root}/src/ptt.py" /opt/pi-voice-assistant/src/ptt.py
install -m 0644 "${repo_root}/src/llm.py" /opt/pi-voice-assistant/src/llm.py
install -m 0644 "${repo_root}/src/menu.py" /opt/pi-voice-assistant/src/menu.py
install -m 0644 "${repo_root}/src/intents.py" /opt/pi-voice-assistant/src/intents.py
install -m 0644 "${repo_root}/src/power.py" /opt/pi-voice-assistant/src/power.py
install -m 0644 "${repo_root}/src/netprobe.py" /opt/pi-voice-assistant/src/netprobe.py
install -m 0644 "${repo_root}/src/alarms.py" /opt/pi-voice-assistant/src/alarms.py
install -m 0644 "${repo_root}/src/wlan.py" /opt/pi-voice-assistant/src/wlan.py
install -m 0644 "${repo_root}/src/skull.py" /opt/pi-voice-assistant/src/skull.py
install -m 0644 "${repo_root}/src/endpoint.py" /opt/pi-voice-assistant/src/endpoint.py
install -m 0644 "${repo_root}/src/wake_listener.py" /opt/pi-voice-assistant/src/wake_listener.py
install -m 0644 "${repo_root}/src/wakeword.py" /opt/pi-voice-assistant/src/wakeword.py
install -m 0644 "${repo_root}/src/remote_turn.py" /opt/pi-voice-assistant/src/remote_turn.py
install -m 0644 "${repo_root}/src/transcribe.py" /opt/pi-voice-assistant/src/transcribe.py
install -m 0644 "${repo_root}/src/piper_worker.py" /opt/pi-voice-assistant/src/piper_worker.py
install -m 0644 "${repo_root}/src/runtime_metrics.py" /opt/pi-voice-assistant/src/runtime_metrics.py
install -m 0755 "${repo_root}/src/speak.py" /opt/pi-voice-assistant/src/speak.py
install -m 0644 "${repo_root}/src/button_shim.py" /opt/pi-voice-assistant/src/button_shim.py
install -m 0644 "${repo_root}/src/voice_controls.py" /opt/pi-voice-assistant/src/voice_controls.py
install -m 0644 "${repo_root}/src/voice_effects.py" /opt/pi-voice-assistant/src/voice_effects.py
install -m 0644 "${repo_root}/src/system_status.py" /opt/pi-voice-assistant/src/system_status.py
install -m 0755 "${repo_root}/scripts/install-vosk.sh" /opt/pi-voice-assistant/scripts/install-vosk.sh
install -m 0755 "${repo_root}/scripts/install-piper.sh" /opt/pi-voice-assistant/scripts/install-piper.sh
install -m 0644 "${repo_root}/deploy/pi-ptt.service" /etc/systemd/system/pi-ptt.service

if [[ ! -e /etc/pi-ptt.env ]]; then
  install -m 0644 "${repo_root}/config/ptt.env.example" /etc/pi-ptt.env
  echo "Created /etc/pi-ptt.env; review it before starting the service."
fi

created_voice_env=0
if [[ ! -e /etc/pi-voice-assistant.env ]]; then
  install -o root -g obivan -m 0640 "${repo_root}/config/openrouter.env.example" /etc/pi-voice-assistant.env
  created_voice_env=1
  echo "Created /etc/pi-voice-assistant.env (root:obivan, 0640); review STT_PROVIDER and credentials before starting."
fi

systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/pi-ptt.service

if [[ ${created_voice_env} -eq 1 ]]; then
  echo "Service left stopped until /etc/pi-voice-assistant.env is reviewed."
elif [[ ${service_was_active} -eq 1 ]]; then
  systemctl start pi-ptt.service
  echo "pi-ptt.service restarted."
else
  echo "Install complete. Start with: sudo systemctl enable --now pi-ptt.service"
fi
