#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-voice-service.sh" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Do not replace a running root-owned provisioning service's script while it
# owns wlan0, nftables rules, and an active WPA2 key/portal session.
if systemctl is-active --quiet proximus-setup.service; then
  echo "Stop the active setup AP first: sudo systemctl stop proximus-setup.service" >&2
  exit 1
fi
# The service needs this group even when the optional SHIM is disabled.
# Validate dependencies before stopping an already working voice service.
if ! getent group i2c >/dev/null || ! /usr/bin/python3 -c 'import smbus'; then
  echo "Install hardware dependency first: sudo apt install python3-smbus i2c-tools" >&2
  exit 1
fi
# Required for the physical AP QR screen and associated-client monitor.
# Perform apt only for missing packages, before stopping a healthy service.
bash "${repo_root}/scripts/ensure-system-packages.sh" python3-qrcode iw alsa-utils
if ! /usr/bin/python3 -c 'import qrcode' >/dev/null 2>&1; then
  echo "QR module unavailable to system Python despite Debian package." >&2
  exit 1
fi
if [[ -x /opt/pi-voice-assistant/.venv-display/bin/python ]] &&
   ! /opt/pi-voice-assistant/.venv-display/bin/python -c 'import qrcode' >/dev/null 2>&1; then
  echo "Display venv cannot import qrcode. Reinstall display venv with --system-site-packages." >&2
  exit 1
fi

# Enable the analog jack and persist its mixer state before stopping a healthy
# service. Preserve existing speaker, capture and digital playback levels.
/usr/bin/python3 "${repo_root}/scripts/configure-wm8960.py" --if-present

service_was_active=0
if systemctl is-active --quiet pi-ptt.service; then
  service_was_active=1
  systemctl stop pi-ptt.service
fi

install -d -m 0755 /opt/pi-voice-assistant/src /opt/pi-voice-assistant/scripts
install -m 0644 "${repo_root}/src/ptt.py" /opt/pi-voice-assistant/src/ptt.py
install -m 0644 "${repo_root}/src/ptt_config.py" /opt/pi-voice-assistant/src/ptt_config.py
install -m 0644 "${repo_root}/src/llm.py" /opt/pi-voice-assistant/src/llm.py
install -m 0644 "${repo_root}/src/menu.py" /opt/pi-voice-assistant/src/menu.py
install -m 0644 "${repo_root}/src/intents.py" /opt/pi-voice-assistant/src/intents.py
install -m 0644 "${repo_root}/src/power.py" /opt/pi-voice-assistant/src/power.py
install -m 0644 "${repo_root}/src/pisugar_client.py" /opt/pi-voice-assistant/src/pisugar_client.py
install -m 0644 "${repo_root}/src/pisugar_button.py" /opt/pi-voice-assistant/src/pisugar_button.py
install -m 0644 "${repo_root}/src/battery_standby.py" /opt/pi-voice-assistant/src/battery_standby.py
install -m 0644 "${repo_root}/src/energy.py" /opt/pi-voice-assistant/src/energy.py
install -m 0644 "${repo_root}/src/netprobe.py" /opt/pi-voice-assistant/src/netprobe.py
install -m 0644 "${repo_root}/src/sysmon.py" /opt/pi-voice-assistant/src/sysmon.py
install -m 0644 "${repo_root}/src/memory.py" /opt/pi-voice-assistant/src/memory.py
install -m 0644 "${repo_root}/src/maintenance.py" /opt/pi-voice-assistant/src/maintenance.py
install -m 0644 "${repo_root}/src/logwatch.py" /opt/pi-voice-assistant/src/logwatch.py
install -m 0644 "${repo_root}/src/nightwatch.py" /opt/pi-voice-assistant/src/nightwatch.py
install -m 0644 "${repo_root}/src/nightwatch_client.py" /opt/pi-voice-assistant/src/nightwatch_client.py
install -m 0644 "${repo_root}/src/nightwatch_training.py" /opt/pi-voice-assistant/src/nightwatch_training.py
install -m 0644 "${repo_root}/src/security_audit.py" /opt/pi-voice-assistant/src/security_audit.py
install -m 0644 "${repo_root}/src/enroll.py" /opt/pi-voice-assistant/src/enroll.py
install -m 0644 "${repo_root}/src/audio_output.py" /opt/pi-voice-assistant/src/audio_output.py
install -m 0644 "${repo_root}/src/bluetooth.py" /opt/pi-voice-assistant/src/bluetooth.py
install -m 0644 "${repo_root}/src/boardled.py" /opt/pi-voice-assistant/src/boardled.py
install -m 0644 "${repo_root}/src/people.py" /opt/pi-voice-assistant/src/people.py
install -m 0644 "${repo_root}/src/speaker.py" /opt/pi-voice-assistant/src/speaker.py
install -m 0644 "${repo_root}/src/voice_quality.py" /opt/pi-voice-assistant/src/voice_quality.py
install -m 0644 "${repo_root}/src/voice_profile.py" /opt/pi-voice-assistant/src/voice_profile.py
install -m 0644 "${repo_root}/src/voice_enroll.py" /opt/pi-voice-assistant/src/voice_enroll.py
install -m 0644 "${repo_root}/src/enroll_phrases.py" /opt/pi-voice-assistant/src/enroll_phrases.py
install -m 0644 "${repo_root}/src/alarm_audio.py" /opt/pi-voice-assistant/src/alarm_audio.py
install -m 0644 "${repo_root}/src/alarms.py" /opt/pi-voice-assistant/src/alarms.py
install -m 0644 "${repo_root}/src/wlan.py" /opt/pi-voice-assistant/src/wlan.py
install -m 0644 "${repo_root}/src/network_control.py" /opt/pi-voice-assistant/src/network_control.py
install -m 0644 "${repo_root}/src/network_info.py" /opt/pi-voice-assistant/src/network_info.py
install -m 0644 "${repo_root}/src/proximus_setup.py" /opt/pi-voice-assistant/src/proximus_setup.py
install -m 0644 "${repo_root}/src/hotspot_control.py" /opt/pi-voice-assistant/src/hotspot_control.py
install -m 0644 "${repo_root}/src/hotspot_config.py" /opt/pi-voice-assistant/src/hotspot_config.py
install -m 0644 "${repo_root}/src/cue.py" /opt/pi-voice-assistant/src/cue.py
install -m 0644 "${repo_root}/src/device_control.py" /opt/pi-voice-assistant/src/device_control.py
install -m 0644 "${repo_root}/src/weather.py" /opt/pi-voice-assistant/src/weather.py
install -m 0644 "${repo_root}/src/weather_icons.py" /opt/pi-voice-assistant/src/weather_icons.py
install -m 0644 "${repo_root}/src/agenda.py" /opt/pi-voice-assistant/src/agenda.py
install -m 0644 "${repo_root}/src/pronounce.py" /opt/pi-voice-assistant/src/pronounce.py
install -m 0644 "${repo_root}/src/skull.py" /opt/pi-voice-assistant/src/skull.py
install -m 0644 "${repo_root}/src/face.py" /opt/pi-voice-assistant/src/face.py
install -m 0644 "${repo_root}/src/mood.py" /opt/pi-voice-assistant/src/mood.py
install -m 0644 "${repo_root}/src/visemes.py" /opt/pi-voice-assistant/src/visemes.py
install -m 0644 "${repo_root}/src/protocol.py" /opt/pi-voice-assistant/src/protocol.py
install -D -m 0644 "${repo_root}/assets/display/doom-faces.png" \
  /opt/pi-voice-assistant/models/display/doom-faces.png
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
install -m 0644 "${repo_root}/src/settings.py" /opt/pi-voice-assistant/src/settings.py
# pi-display draws the menu from menu.py and imports alarms/netprobe/power/skull
# from here too; it must run the same version, or menu entries are shifted.
install -m 0644 "${repo_root}/src/display.py" /opt/pi-voice-assistant/src/display.py
install -m 0644 "${repo_root}/src/lore.py" /opt/pi-voice-assistant/src/lore.py
install -m 0644 "${repo_root}/src/lore_engrams.json" /opt/pi-voice-assistant/src/lore_engrams.json
install -m 0644 "${repo_root}/src/dialog.py" /opt/pi-voice-assistant/src/dialog.py
install -m 0644 "${repo_root}/src/story.py" /opt/pi-voice-assistant/src/story.py
install -m 0644 "${repo_root}/src/variants.py" /opt/pi-voice-assistant/src/variants.py
# Trams/trains, reminders and calendar entries (TRANSIT_*, CALDAV_*).
install -m 0644 "${repo_root}/src/journeys.py" /opt/pi-voice-assistant/src/journeys.py
install -m 0644 "${repo_root}/src/transit.py" /opt/pi-voice-assistant/src/transit.py
install -m 0755 "${repo_root}/scripts/install-vosk.sh" /opt/pi-voice-assistant/scripts/install-vosk.sh
install -m 0755 "${repo_root}/scripts/install-piper.sh" /opt/pi-voice-assistant/scripts/install-piper.sh
install -m 0755 "${repo_root}/scripts/configure-wm8960.py" /opt/pi-voice-assistant/scripts/configure-wm8960.py
install -m 0644 "${repo_root}/deploy/pi-ptt.service" /etc/systemd/system/pi-ptt.service
# Privileged helper is root-owned: the voice service can only start/stop its
# systemd unit over the narrowly scoped D-Bus/Polkit authorization below.
install -D -o root -g root -m 0755 "${repo_root}/scripts/proximus-battery-standby.py" \
  /usr/local/libexec/proximus-battery-standby.py
install -m 0644 "${repo_root}/deploy/proximus-battery-standby.service" \
  /etc/systemd/system/proximus-battery-standby.service
if [[ ! -e /etc/proximus-battery-standby.env ]]; then
  install -o root -g root -m 0644 "${repo_root}/config/proximus-battery-standby.env.example" \
    /etc/proximus-battery-standby.env
fi
# On-demand only: never enable or start the provisioning AP at install time.
install -m 0644 "${repo_root}/deploy/proximus-setup.service" /etc/systemd/system/proximus-setup.service
# Remove the obsolete sudo rule from pre-fix PR #110. The PTT unit retains
# NoNewPrivileges=yes and now uses a narrowly scoped systemd D-Bus Polkit rule.
rm -f /etc/sudoers.d/proximus-setup
# Power off / reboot for the service user (empty battery, confirmed voice command).
if [[ -d /etc/polkit-1/rules.d ]]; then
  install -m 0644 "${repo_root}/deploy/50-pi-voice-poweroff.rules" \
    /etc/polkit-1/rules.d/50-pi-voice-poweroff.rules
  install -m 0644 "${repo_root}/deploy/51-pi-voice-network.rules" \
    /etc/polkit-1/rules.d/51-pi-voice-network.rules
  install -m 0644 "${repo_root}/deploy/52-pi-voice-hotspot.rules" \
    /etc/polkit-1/rules.d/52-pi-voice-hotspot.rules
  install -m 0644 "${repo_root}/deploy/53-pi-voice-battery-standby.rules" \
    /etc/polkit-1/rules.d/53-pi-voice-battery-standby.rules
fi

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
systemd-analyze verify /etc/systemd/system/pi-ptt.service /etc/systemd/system/proximus-setup.service \
  /etc/systemd/system/proximus-battery-standby.service

if [[ ${created_voice_env} -eq 1 ]]; then
  echo "Service left stopped until /etc/pi-voice-assistant.env is reviewed."
elif [[ ${service_was_active} -eq 1 ]]; then
  systemctl start pi-ptt.service
  echo "pi-ptt.service restarted."
  # The display shares menu.py and other modules: never leave it on the old code.
  if systemctl is-active --quiet pi-display.service; then
    systemctl restart pi-display.service
    echo "pi-display.service restarted."
  fi
else
  echo "Install complete. Start with: sudo systemctl enable --now pi-ptt.service"
fi
