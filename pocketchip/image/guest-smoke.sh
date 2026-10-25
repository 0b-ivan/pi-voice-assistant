#!/bin/sh
set -eu
export PYTHONPATH=/opt/proximus
export HOME=/home/proximus
cleanup() { poweroff -f; }
trap cleanup EXIT
uname -a
[ "$(dpkg --print-architecture)" = armhf ]
# Root starts the boot test, but the actual desktop runs as an ordinary user.
runuser -u proximus -- env PYTHONPATH="$PYTHONPATH" HOME="$HOME" \
    xvfb-run -a -s '-screen 0 480x272x24' python3 -m proximus_os.shell --smoke
awk '/MemTotal|MemAvailable/ {print}' /proc/meminfo
printf 'PROXIMUS_BOOT_OK armhf systemd uid=unprivileged ram=512MiB\n'
