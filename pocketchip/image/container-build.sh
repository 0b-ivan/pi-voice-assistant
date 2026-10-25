#!/bin/sh
set -eu
# Needs --privileged inside the disposable Docker VM/CI runner for chroot/binfmt.
# Register only ARM ELF, with a fixed interpreter fd so chroot needs no host binary.
# Debian arm64 omits an ARM binfmt entry even on hosts without AArch32 execution.
registry=/proc/sys/fs/binfmt_misc
if [ ! -e "$registry/register" ]; then
    mount -t binfmt_misc binfmt_misc "$registry"
fi
if [ ! -e "$registry/proximus-armhf" ]; then
    printf ':proximus-armhf:M::\177ELF\001\001\001\000\000\000\000\000\000\000\000\000\002\000\050\000:\377\377\377\377\377\377\377\000\377\377\377\377\377\377\377\377\376\377\377\377:/usr/bin/qemu-arm-static:F\n' > "$registry/register"
    trap 'printf -- -1 > "$registry/proximus-armhf"' EXIT
fi
/src/pocketchip/image/build.sh
