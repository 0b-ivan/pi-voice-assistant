#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mode=${1:-interactive}
case "$mode" in
    smoke) extra='systemd.unit=proximus-smoke.service'; display='none' ;;
    interactive) extra=''; display='vnc=127.0.0.1:0' ;;
    *) echo 'Usage: run-qemu.sh [interactive|smoke]' >&2; exit 2 ;;
esac
# Snapshot mode: never writes the base disk. No exposed SSH port or physical USB.
exec qemu-system-arm -M virt -cpu cortex-a15 -smp 1 -m 512 -accel tcg \
    -kernel build/zImage -initrd build/initrd.img \
    -append "root=/dev/vda rw rootwait console=ttyAMA0 $extra" \
    -drive if=none,file=build/rootfs.ext4,format=raw,id=root,snapshot=on \
    -device virtio-blk-device,drive=root \
    -device virtio-gpu-device,xres=480,yres=272 \
    -device virtio-keyboard-device -device virtio-tablet-device \
    -nic none -display "$display" -serial stdio -monitor none -no-reboot
