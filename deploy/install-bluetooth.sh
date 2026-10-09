#!/bin/sh
# Bluetooth speakers for pi-ptt. Run once as root on the Pi:
#   sudo sh ~/pi-voice-setup/install-bluetooth.sh
# Installs bluez-alsa (ALSA <-> A2DP), lets pi-ptt talk to BlueZ (group
# bluetooth) and switches the radio on.
set -eu
apt-get install -y bluez-alsa-utils libasound2-plugin-bluez
# Proximus sends audio to speakers: A2DP source role only.
mkdir -p /etc/systemd/system/bluealsa.service.d
printf '[Service]\nExecStart=\nExecStart=/usr/bin/bluealsa -p a2dp-source\n' \
    > /etc/systemd/system/bluealsa.service.d/proximus.conf
usermod -aG bluetooth obivan
mkdir -p /etc/systemd/system/pi-ptt.service.d
printf '[Service]\nSupplementaryGroups=bluetooth audio\n' \
    > /etc/systemd/system/pi-ptt.service.d/bluetooth.conf
rfkill unblock bluetooth || true
systemctl daemon-reload
systemctl enable --now bluetooth bluealsa
systemctl restart bluealsa pi-ptt
bluetoothctl power on
echo "bluetooth ready"
