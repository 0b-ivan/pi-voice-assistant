#!/bin/sh
# Pi only, as root, after deploy/maintenance/install.sh (uses its request
# folder): journal in RAM, copies to the memory stick, no syslog on the SD card.
#   sh deploy/logsync/install.sh
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
if [ ! -e /etc/tmpfiles.d/proximus-maintenance.conf ]; then
    echo "install the maintenance worker first: sh deploy/maintenance/install.sh obivan pi-ptt" >&2
    exit 1
fi
install -m 0755 "$here/proximus-logsync" /usr/local/sbin/proximus-logsync
install -d -m 0755 /etc/systemd/journald.conf.d
install -m 0644 "$here/journald-proximus.conf" /etc/systemd/journald.conf.d/50-proximus.conf
for unit in proximus-logsync.service proximus-logsync.timer proximus-logsync.path \
        proximus-logsync-shutdown.service; do
    install -m 0644 "$here/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl restart systemd-journald
systemctl enable --now proximus-logsync.timer proximus-logsync.path proximus-logsync-shutdown.service
# rsyslog would write a second copy of everything to /var/log on the SD card.
if systemctl is-enabled --quiet rsyslog.service 2>/dev/null; then
    systemctl disable --now rsyslog.service
    echo "rsyslog disabled: the journal (RAM, copied to the stick) is the only log"
fi
# An old persistent journal on the SD card: archive it to the stick, then remove it.
if [ -d /var/log/journal ] && [ -n "$(ls -A /var/log/journal 2>/dev/null)" ]; then
    /usr/local/sbin/proximus-logsync || true
    archive="/mnt/proximus-memory/logs/sd-journal-$(date +%F).log.gz"
    if [ -d /mnt/proximus-memory/logs ] \
            && journalctl -D /var/log/journal -o short-iso-precise --no-pager -q | gzip > "$archive"; then
        rm -rf /var/log/journal
        echo "old SD-card journal archived to $archive and removed"
    else
        rm -f "$archive"
        echo "old SD-card journal kept in /var/log/journal (stick missing): run again with the stick"
    fi
fi
/usr/local/sbin/proximus-logsync || true
echo "journal in RAM; copies to the stick in /mnt/proximus-memory/logs"
