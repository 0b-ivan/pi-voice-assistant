#!/bin/sh
# Run as root inside CT 107. Builds llama-server (pinned tag, CPU-native) and
# downloads a verified GGUF model as the offline LLM fallback for the Servitor
# service. Idempotent: an existing build/model with the right hash is kept.
#
#   sh server/install-llm.sh [3b|1.5b]
set -eu
B=/opt/servitor-voice
L=$B/llm
SRC=$(cd "$(dirname "$0")" && pwd)
LLAMA_TAG=v0.5.0
LLAMA_COMMIT=7fe450e19305b828c199d602c23a8337aaa1f03b  # v0.5.0^{} (peeled)
HF=https://huggingface.co

case "${1:-3b}" in
  3b)
    MODEL=qwen2.5-3b-instruct-q4_k_m.gguf
    URL=$HF/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/$MODEL
    SHA=626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d ;;
  1.5b)
    MODEL=qwen2.5-1.5b-instruct-q4_k_m.gguf
    URL=$HF/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/$MODEL
    SHA=6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e ;;
  *) echo "usage: $0 [3b|1.5b]" >&2; exit 2 ;;
esac

install -d -o root -g root -m 0755 "$L" "$L/bin" "$L/models"

if [ "$("$L/bin/llama-server" --version 2>&1 | grep -c "$(echo $LLAMA_COMMIT | cut -c1-7)")" = 0 ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q cmake build-essential >/dev/null
  rm -rf "$L/src"
  git clone -q --depth 1 --branch "$LLAMA_TAG" https://github.com/ggml-org/llama.cpp "$L/src"
  [ "$(git -C "$L/src" rev-parse HEAD)" = "$LLAMA_COMMIT" ] || { echo "llama.cpp tag moved" >&2; exit 1; }
  # GGML_NATIVE: tuned for this host's CPU (AVX2); no CUDA/curl/web downloads.
  cmake -S "$L/src" -B "$L/src/build" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON \
    -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF >/dev/null
  cmake --build "$L/src/build" --target llama-server -j "$(nproc)" >/dev/null
  install -o root -g root -m 0755 "$L/src/build/bin/llama-server" "$L/bin/llama-server"
  rm -rf "$L/src"
fi

if ! echo "$SHA  $L/models/$MODEL" | sha256sum -c --status 2>/dev/null; then
  curl -fsSL --retry 3 -o "$L/models/$MODEL.part" "$URL"
  echo "$SHA  $L/models/$MODEL.part" | sha256sum -c --status || { echo "model hash mismatch" >&2; exit 1; }
  mv "$L/models/$MODEL.part" "$L/models/$MODEL"
  chmod 0644 "$L/models/$MODEL"
fi
ln -sfn "$L/models/$MODEL" "$L/models/current.gguf"

install -o root -g root -m 0644 "$SRC/servitor-llm.service" /etc/systemd/system/servitor-llm.service
systemctl daemon-reload
systemctl enable servitor-llm.service >/dev/null
systemctl restart servitor-llm.service
echo "installed llama-server $LLAMA_TAG with $MODEL"
