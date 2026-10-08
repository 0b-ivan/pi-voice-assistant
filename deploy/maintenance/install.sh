#!/bin/sh
# Install the maintenance worker on the Pi or on CT 107. Run as root:
#   sh install.sh obivan pi-ptt                 (Pi)
#   sh install.sh servitor servitor-voice reboot-only   (CT 107: never updates)
# The voice service may only create request files; root does the work.
set -eu
user="$1"
service="$2"
mode="${3:-all}"
here="$(cd "$(dirname "$0")" && pwd)"
install -m 0755 "$here/proximus-maintenance" /usr/local/sbin/proximus-maintenance
units="proximus-reboot.service proximus-reboot.path"
[ "$mode" = reboot-only ] || units="$units proximus-update.service proximus-update.path"
for unit in $units; do
    install -m 0644 "$here/$unit" "/etc/systemd/system/$unit"
done
if [ "$mode" = reboot-only ]; then
    systemctl disable --now proximus-update.path 2>/dev/null || true
    rm -f /etc/systemd/system/proximus-update.path /etc/systemd/system/proximus-update.service
fi
group="$(id -gn "$user")"
printf 'd /run/proximus-maintenance 0755 root root -\nd /run/proximus-maintenance/requests 0770 root %s -\n' \
    "$group" > /etc/tmpfiles.d/proximus-maintenance.conf
systemd-tmpfiles --create /etc/tmpfiles.d/proximus-maintenance.conf
mkdir -p "/etc/systemd/system/$service.service.d"
printf '[Service]\nReadWritePaths=-/run/proximus-maintenance/requests\n' \
    > "/etc/systemd/system/$service.service.d/maintenance.conf"
systemctl daemon-reload
systemctl enable --now proximus-reboot.path
[ "$mode" = reboot-only ] || systemctl enable --now proximus-update.path
systemctl restart "$service"
echo "maintenance worker installed for $user ($service)"
