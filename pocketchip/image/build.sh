#!/bin/sh
# Linux only; writes regular files below pocketchip/build/, never a block device.
set -eu
cd "$(dirname "$0")/.."
[ "$(id -u)" = 0 ] || { echo 'Build requires root inside an isolated Linux environment.' >&2; exit 1; }
[ "$(uname -s)" = Linux ] || exit 1
mkdir -p build
if [ -L build ] || [ -e build/rootfs ] || [ -e build/rootfs.ext4 ]; then
    echo 'Use a fresh build directory (existing image/rootfs is not overwritten).' >&2; exit 1;
fi
# Debian validates repository metadata using its archive keyring; never --allow-unauthenticated.
mmdebstrap --architectures=armhf --variant=minbase \
    --include=systemd-sysv,linux-image-armmp,initramfs-tools,python3,python3-tk,python3-pil,xvfb,xauth,xinit,xserver-xorg-core,xserver-xorg-input-libinput,xserver-xorg-video-fbdev,xserver-xorg-video-dummy,x11-xserver-utils,fonts-dejavu-core,network-manager,xterm,dbus-user-session,libpam-systemd,ca-certificates,iproute2 \
    trixie build/rootfs \
    'deb https://deb.debian.org/debian trixie main' \
    'deb https://deb.debian.org/debian trixie-updates main' \
    'deb https://security.debian.org/debian-security trixie-security main'
root=build/rootfs
mkdir -p "$root/opt/proximus" "$root/etc/proximus" "$root/etc/systemd/journald.conf.d"
cp -R proximus_os "$root/opt/proximus/"
find "$root/opt/proximus" -name __pycache__ -type d -exec rm -r {} +
cp LICENSE "$root/opt/proximus/LICENSE"
cp image/guest-smoke.sh "$root/opt/proximus/guest-smoke.sh"
cp systemd/proximus-smoke.service "$root/etc/systemd/system/"
cp systemd/proximus-xorg-smoke.service "$root/etc/systemd/system/"
cp image/xorg-smoke.sh "$root/opt/proximus/xorg-smoke.sh"
chmod 755 "$root/opt/proximus/xorg-smoke.sh"
cp systemd/proximus-session.service "$root/etc/systemd/system/"
cp image/xinitrc "$root/opt/proximus/xinitrc"
cp image/xorg-dummy.conf "$root/etc/X11/xorg-dummy.conf"
cp image/start-session.sh "$root/opt/proximus/start-session.sh"
chmod 755 "$root/opt/proximus/start-session.sh"
cp config/journald.conf "$root/etc/systemd/journald.conf.d/proximus.conf"
cp config/client.json.example "$root/etc/proximus/client.json.example"
chmod 755 "$root/opt/proximus/guest-smoke.sh"
chroot "$root" useradd --create-home --shell /bin/bash proximus
# Both accounts remain locked. Session autostart is an explicitly isolated VM profile.
chroot "$root" passwd -l root
chroot "$root" passwd -l proximus
ln -sf /usr/share/zoneinfo/Europe/Berlin "$root/etc/localtime"
printf 'proximus-qemu\n' > "$root/etc/hostname"
# startx/xauth uses the local hostname for X11 display cookies. This offline
# guest has no DNS service, so ensure that its hostname resolves locally.
printf '127.0.0.1 localhost\n127.0.1.1 proximus-qemu\n::1 localhost ip6-localhost ip6-loopback\n' > "$root/etc/hosts"
printf '/dev/vda / ext4 defaults,noatime 0 1\n' > "$root/etc/fstab"
printf 'virtio_mmio\nvirtio_blk\nvirtio_gpu\nvirtio_input\next4\n' >> "$root/etc/initramfs-tools/modules"
chroot "$root" update-initramfs -u -k all
chroot "$root" systemctl enable proximus-session.service
chroot "$root" systemctl disable apt-daily.timer apt-daily-upgrade.timer || true
chroot "$root" apt-get clean
# shellcheck disable=SC2016 # dpkg-query expands these fields, not the shell.
chroot "$root" dpkg-query -W '-f=${Package}\t${Version}\t${source:Package}\t${source:Version}\n' > build/packages.tsv
cp build/packages.tsv "$root/etc/proximus/packages.tsv"
# Keep all installed package copyright files in the rootfs; no firmware bundled.
cp "$root"/boot/vmlinuz-* build/zImage
cp "$root"/boot/initrd.img-* build/initrd.img
truncate -s 1536M build/rootfs.ext4
mkfs.ext4 -q -F -L proximus-qemu -d "$root" build/rootfs.ext4
sha256sum build/zImage build/initrd.img build/rootfs.ext4 > build/SHA256SUMS
printf 'Proximus QEMU image built. NOT a PocketCHIP NAND/USB image.\n'
