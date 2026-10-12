#!/usr/bin/env bash
# Install only missing Debian packages. Safe to invoke on upgrades and first boot.
# Call before stopping pi-ptt/pi-display; abort deployment if apt fails.
set -euo pipefail
if (( EUID != 0 )); then
  echo "System package installation requires root." >&2
  exit 1
fi

missing=()
for package in "$@"; do
  # Fixed allowlist: this helper must never become a shell/apt injection path.
  case "$package" in
    python3-qrcode|iw|python3-venv|python3-pil|alsa-utils) ;;
    *) echo "Unsupported runtime package: $package" >&2; exit 2 ;;
  esac
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
    missing+=("$package")
  fi
done

if (( ${#missing[@]} )); then
  apt-get update
  apt-get install -y --no-install-recommends "${missing[@]}"
fi
