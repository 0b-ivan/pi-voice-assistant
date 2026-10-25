#!/bin/sh
# Real Xorg boot acceptance on QEMU virt. Does not touch hardware.
set -eu
systemctl start proximus-session.service
i=0
while [ "$i" -lt 100 ]; do
    if journalctl -b -u proximus-session.service --no-pager -o cat | grep -q 'PROXIMUS_GUI_OK'; then
        if ! systemctl is-failed --quiet proximus-session.service; then
            printf 'PROXIMUS_XORG_BOOT_OK armhf Xorg Tk 480x272\n'
            poweroff -f
            exit 0
        fi
    fi
    if systemctl is-failed --quiet proximus-session.service; then
        break
    fi
    sleep 1
    i=$((i + 1))
done
systemctl status proximus-session.service --no-pager || true
journalctl -b -u proximus-session.service --no-pager -n 120 || true
# Xorg normally writes its detailed errors to a user-local file rather than
# systemd's journal. Print both possible locations into the CI boot log.
for log in \
    /home/proximus/.local/share/xorg/Xorg.0.log \
    /home/proximus/.local/share/xorg/Xorg.0.log.old \
    /var/log/Xorg.0.log \
    /var/log/Xorg.0.log.old \
    /home/proximus/.xsession-errors; do
    if [ -f "$log" ]; then
        printf '\n===== %s =====\n' "$log"
        tail -n 140 "$log" || true
    fi
done
printf '\n===== Local hostname lookup =====\n'
getent hosts "$(hostname)" || true
printf '\n===== Logind sessions =====\n'
loginctl list-sessions --no-pager || true
printf '\n===== Display devices =====\n'
ls -l /dev/tty1 /dev/dri/card* /dev/fb* 2>/dev/null || true
printf 'PROXIMUS_XORG_BOOT_FAILED\n' >&2
poweroff -f
exit 1
