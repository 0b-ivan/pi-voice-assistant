#!/bin/sh
# Run as root inside CT 107. Billy's own voice: RVC voice conversion after
# Piper (server/rvc_worker.py, docs/text-to-speech.md#billys-stimme-mit-rvc).
#
#   sh server/install-rvc.sh MODEL.zip|MODEL.pth [MODEL.index]
#       installs/updates the worker (own Python 3.10 venv, PyTorch CPU) and
#       the model, starts servitor-rvc. The voice service is left alone:
#       measure first with server/bench-rvc.py.
#   sh server/install-rvc.sh --enable    Billy speaks through RVC
#   sh server/install-rvc.sh --disable   back to the plain natural voice
#
# The model never goes into the repository; it is copied from a local file
# (e.g. an RVC v2 model downloaded by hand). Private use only.
set -eu
B=/opt/servitor-voice
R=$B/rvc
SRC=$(cd "$(dirname "$0")" && pwd)
VOICE_ENV=${SERVITOR_ENV_FILE:-/etc/servitor-voice.env}
RVC_ENV=/etc/servitor-rvc.env
URL=http://127.0.0.1:8767
UV_VERSION=0.5.11
# rvc-python pins fairseq 0.12.2 / numpy 1.23.5 (Python <= 3.10). torch stays
# below 2.6: newer versions refuse fairseq's HuBERT checkpoint by default.
TORCH_VERSION=2.1.2
RVC_PYTHON_VERSION=0.1.5
BASE=https://huggingface.co/Daswer123/RVC_Base/resolve/main

voice_restart() {
  if systemctl is-enabled --quiet servitor-voice.service 2>/dev/null; then
    systemctl restart servitor-voice.service
  fi
}

case "${1:-}" in
  --enable)
    curl -fsS "$URL/health" | grep -q '"ready": true' \
      || { echo "servitor-rvc is not ready; see journalctl -u servitor-rvc" >&2; exit 1; }
    sed -i '/^SERVITOR_RVC_URL=/d' "$VOICE_ENV"
    echo "SERVITOR_RVC_URL=$URL" >> "$VOICE_ENV"
    grep -q '^SERVITOR_RVC_TIMEOUT_SECONDS=' "$VOICE_ENV" \
      || echo "SERVITOR_RVC_TIMEOUT_SECONDS=10" >> "$VOICE_ENV"
    voice_restart
    echo "Billy speaks through RVC (SERVITOR_RVC_URL=$URL)"
    exit 0 ;;
  --disable)
    sed -i '/^SERVITOR_RVC_URL=/d' "$VOICE_ENV"
    voice_restart
    echo "RVC off; Billy keeps the plain natural voice"
    exit 0 ;;
  ''|-*) echo "usage: $0 MODEL.zip|MODEL.pth [MODEL.index] | --enable | --disable" >&2; exit 2 ;;
esac

MODEL_SOURCE=$1
INDEX_SOURCE=${2:-}
[ -f "$MODEL_SOURCE" ] || { echo "no such file: $MODEL_SOURCE" >&2; exit 1; }

if [ "$SRC" != "$B/repo/server" ]; then
  install -d -o root -g root -m 0755 "$B/repo/server"
  install -o root -g root -m 0644 "$SRC/rvc_worker.py" "$SRC/bench-rvc.py" "$B/repo/server/"
fi

DEBIAN_FRONTEND=noninteractive apt-get install -y -q ffmpeg build-essential unzip curl python3-venv >/dev/null
install -d -o root -g root -m 0755 "$R" "$R/models"

# uv brings its own Python 3.10 (the CT's system Python is newer). Its
# interpreters go below $R, not /root, so the servitor user can run them.
if [ "$("$R/tools/bin/uv" --version 2>/dev/null)" != "uv $UV_VERSION" ]; then
  python3 -m venv "$R/tools"
  "$R/tools/bin/pip" install -q "uv==$UV_VERSION"
fi
export UV_PYTHON_INSTALL_DIR="$R/python"
if ! "$R/venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 10)' 2>/dev/null; then
  rm -rf "$R/venv"
  "$R/tools/bin/uv" venv -q --python 3.10 "$R/venv"
fi
# CPU wheels first (no CUDA, ~200 MB instead of several GB); the second call
# then keeps them, since 2.1.2+cpu satisfies ==2.1.2.
"$R/tools/bin/uv" pip install -q --python "$R/venv/bin/python" \
  --index-url https://download.pytorch.org/whl/cpu \
  "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION"
# setuptools < 70: pyworld still imports pkg_resources.
"$R/tools/bin/uv" pip install -q --python "$R/venv/bin/python" \
  "rvc-python==$RVC_PYTHON_VERSION" "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION" \
  "setuptools<70"

# HuBERT and RMVPE belong next to the package; the worker never downloads.
LIB=$("$R/venv/bin/python" -c \
  'import importlib.util; print(importlib.util.find_spec("rvc_python").submodule_search_locations[0])')
install -d -o root -g root -m 0755 "$LIB/base_model"
for file in hubert_base.pt rmvpe.pt; do
  if [ ! -s "$LIB/base_model/$file" ]; then
    curl -fsSL --retry 3 -o "$LIB/base_model/$file.part" "$BASE/$file"
    mv "$LIB/base_model/$file.part" "$LIB/base_model/$file"
    chmod 0644 "$LIB/base_model/$file"
  fi
done
(cd "$LIB/base_model" && sha256sum hubert_base.pt rmvpe.pt)

# Model: a downloaded .zip (model .pth + .index) or a bare .pth.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
case "$MODEL_SOURCE" in
  *.zip)
    unzip -q -o "$MODEL_SOURCE" -d "$WORK/unpacked"
    # Training checkpoints (G_*.pth, D_*.pth) are no voice models.
    PTH=$(find "$WORK/unpacked" -type f -name '*.pth' ! -name 'G_*' ! -name 'D_*' \
      -printf '%s %p\n' | sort -rn | head -n 1 | cut -d' ' -f2-)
    # The retrieval index RVC uses is added_*.index; trained_*.index is a by-product.
    [ -n "$INDEX_SOURCE" ] || INDEX_SOURCE=$(find "$WORK/unpacked" -type f -name 'added_*.index' \
      | head -n 1)
    [ -n "$INDEX_SOURCE" ] || INDEX_SOURCE=$(find "$WORK/unpacked" -type f -name '*.index' \
      | head -n 1) ;;
  *.pth)
    PTH=$MODEL_SOURCE ;;
  *) echo "model must be a .zip or .pth file" >&2; exit 1 ;;
esac
[ -n "$PTH" ] || { echo "no RVC model (.pth) found in $MODEL_SOURCE" >&2; exit 1; }
NAME=$(basename "$PTH" .pth)
install -d -o root -g root -m 0755 "$R/models/$NAME"
install -o root -g root -m 0644 "$PTH" "$R/models/$NAME/model.pth"
ln -sfn "$R/models/$NAME/model.pth" "$R/models/current.pth"
if [ -n "$INDEX_SOURCE" ]; then
  install -o root -g root -m 0644 "$INDEX_SOURCE" "$R/models/$NAME/model.index"
  ln -sfn "$R/models/$NAME/model.index" "$R/models/current.index"
else
  rm -f "$R/models/current.index"
fi

if [ ! -f "$RVC_ENV" ]; then
  umask 077
  cat > "$RVC_ENV" <<ENV
SERVITOR_RVC_MODEL=$R/models/current.pth
SERVITOR_RVC_INDEX=$R/models/current.index
SERVITOR_RVC_VERSION=v2
# Semitones; Thorsten is close to Billy, tune with server/bench-rvc.py --pitch.
SERVITOR_RVC_PITCH=0
# rmvpe sounds best; pm is faster on the CPU.
SERVITOR_RVC_F0_METHOD=rmvpe
SERVITOR_RVC_INDEX_RATE=0.5
SERVITOR_RVC_PROTECT=0.33
SERVITOR_RVC_RMS_MIX=0.25
SERVITOR_RVC_THREADS=4
SERVITOR_RVC_BIND=127.0.0.1
SERVITOR_RVC_PORT=8767
SERVITOR_RVC_WORKDIR=/run/servitor-rvc
ENV
  chown root:servitor "$RVC_ENV"
  chmod 0640 "$RVC_ENV"
fi

install -o root -g root -m 0644 "$SRC/servitor-rvc.service" /etc/systemd/system/servitor-rvc.service
systemctl daemon-reload
systemctl enable servitor-rvc.service >/dev/null
systemctl restart servitor-rvc.service

# Loading PyTorch, the model, HuBERT and RMVPE plus one warm-up conversion.
i=0
until curl -fsS "$URL/health" 2>/dev/null | grep -q '"ready": true'; do
  i=$((i + 1))
  if [ "$i" -gt 90 ] || ! systemctl is-active --quiet servitor-rvc.service; then
    journalctl -u servitor-rvc.service -n 30 --no-pager >&2
    echo "servitor-rvc did not become ready" >&2
    exit 1
  fi
  sleep 2
done
curl -fsS "$URL/health"; echo
echo "installed RVC model $NAME. Next: measure with"
echo "  $B/.venv/bin/python $B/repo/server/bench-rvc.py"
echo "then switch Billy over with: sh $0 --enable"
