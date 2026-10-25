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
printf 'PROXIMUS_XORG_BOOT_FAILED\n' >&2
poweroff -f
exit 1
