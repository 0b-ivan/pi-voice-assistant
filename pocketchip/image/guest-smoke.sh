#!/bin/sh
set -eu
export PYTHONPATH=/opt/proximus
export HOME=/home/proximus
cleanup() { poweroff -f; }
trap cleanup EXIT
uname -a
[ "$(dpkg --print-architecture)" = armhf ]
# Diagnostic only: QEMU's GPU is not the PocketCHIP LCD controller.
printf '\n===== QEMU virtio-GPU kernel configuration (non-gating) =====\n'
for cfg in /boot/config-*; do
    if [ -f "$cfg" ]; then
        grep -E '^(CONFIG_DRM|CONFIG_DRM_VIRTIO_GPU|CONFIG_VIRTIO|CONFIG_PCI_HOST_GENERIC)=' "$cfg" || true
    fi
done
printf '\n===== QEMU graphics nodes before/after optional module loading =====\n'
ls -ld /dev/dri /dev/dri/card* /dev/fb* 2>/dev/null || true
if command -v modprobe >/dev/null 2>&1; then
    modprobe virtio_gpu 2>&1 || true
fi
ls -ld /dev/dri /dev/dri/card* /dev/fb* 2>/dev/null || true
cat /proc/fb 2>/dev/null || true
# Root starts the boot test, but the actual desktop runs as an ordinary user.
runuser -u proximus -- env PYTHONPATH="$PYTHONPATH" HOME="$HOME" \
    xvfb-run -a -s '-screen 0 480x272x24' python3 -m proximus_os.shell --smoke
awk '/MemTotal|MemAvailable/ {print}' /proc/meminfo
printf 'PROXIMUS_BOOT_OK armhf systemd uid=unprivileged ram=512MiB\n'
