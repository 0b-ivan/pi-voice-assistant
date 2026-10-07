#!/bin/sh
# Run as root inside CT 107. Downloads a pinned, MD5-checked Vosk model next
# to the existing small one. Switching is a separate step: set
# VOSK_MODEL_PATH in /etc/servitor-voice.env and restart servitor-voice.
#
#   sh server/install-stt-model.sh [de-0.21]
set -eu
M=/opt/servitor-voice/models
case "${1:-de-0.21}" in
  de-0.21)
    NAME=vosk-model-de-0.21
    MD5=23298ddeb602739016956144ac4c74de ;;
  *) echo "usage: $0 [de-0.21]" >&2; exit 2 ;;
esac

if [ ! -d "$M/$NAME" ]; then
  T=$(mktemp -d -p "$M")
  trap 'rm -rf "$T"' EXIT
  curl -fsSL --retry 3 -o "$T/$NAME.zip" "https://alphacephei.com/vosk/models/$NAME.zip"
  echo "$MD5  $T/$NAME.zip" | md5sum -c --status || { echo "model checksum mismatch" >&2; exit 1; }
  /opt/servitor-voice/.venv/bin/python -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    "$T/$NAME.zip" "$T"
  chown -R root:root "$T/$NAME"
  chmod -R a+rX,go-w "$T/$NAME"
  mv "$T/$NAME" "$M/$NAME"
fi
du -sh "$M/$NAME"
