#!/bin/sh
# Install the maintenance worker on the Pi or on CT 107. Run as root:
#   sh install.sh obivan pi-ptt                 (Pi)
#   sh install.sh servitor servitor-voice       (CT 107)
# The voice service may only create request files; root does the work.
set -eu
user="$1"
service="$2"
here="$(cd "$(dirname "$0")" && pwd)"
install -m 0755 "$here/proximus-maintenance" /usr/local/sbin/proximus-maintenance
for unit in proximus-update.service proximus-reboot.service proximus-update.path proximus-reboot.path; do
    install -m 0644 "$here/$unit" "/etc/systemd/system/$unit"
done
group="$(id -gn "$user")"
printf 'd /run/proximus-maintenance 0755 root root -\nd /run/proximus-maintenance/requests 0770 root %s -\n' \
    "$group" > /etc/tmpfiles.d/proximus-maintenance.conf
systemd-tmpfiles --create /etc/tmpfiles.d/proximus-maintenance.conf
mkdir -p "/etc/systemd/system/$service.service.d"
printf '[Service]\nReadWritePaths=-/run/proximus-maintenance/requests\n' \
    > "/etc/systemd/system/$service.service.d/maintenance.conf"
systemctl daemon-reload
systemctl enable --now proximus-update.path proximus-reboot.path
systemctl restart "$service"
echo "maintenance worker installed for $user ($service)"
