#!/bin/sh
# Do not confuse software Xorg in QEMU with physical display validation.
set -eu
if grep -q 'proximus.xorg_smoke=1' /proc/cmdline; then
    exec /usr/bin/startx /opt/proximus/xinitrc -- :0 vt1 -keeptty -nolisten tcp -config /etc/X11/xorg-dummy.conf
fi
exec /usr/bin/startx /opt/proximus/xinitrc -- :0 vt1 -keeptty -nolisten tcp
