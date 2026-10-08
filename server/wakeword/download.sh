#!/bin/sh
# Data for training a custom wake word on CT 107 (resumable, idempotent).
#   sh server/wakeword/download.sh [/opt/servitor-voice/train]
# - openWakeWord negative features (ACAV100M, ~2000 h, 17.3 GB) and the
#   false-positive validation set (~11 h), Hugging Face davidscripka
# - Piper voices for synthetic positives/negatives (German, English, Dutch;
#   multi-speaker MLS/LibriTTS/VCTK for variety)
# - MIT environmental impulse responses (room reverb)
set -u
T=${1:-/opt/servitor-voice/train}
HF=https://huggingface.co
mkdir -p "$T/data" "$T/voices" "$T/rir"

fetch() {  # url target
  [ -s "$2" ] && return 0
  curl -fsSL --retry 5 -C - -o "$2.part" "$1" && mv "$2.part" "$2"
}

for f in openwakeword_features_ACAV100M_2000_hrs_16bit.npy validation_set_features.npy; do
  fetch "$HF/datasets/davidscripka/openwakeword_features/resolve/main/$f" "$T/data/$f"
done

for v in \
  de/de_DE/mls/medium/de_DE-mls-medium \
  de/de_DE/eva_k/x_low/de_DE-eva_k-x_low \
  de/de_DE/karlsson/low/de_DE-karlsson-low \
  de/de_DE/kerstin/low/de_DE-kerstin-low \
  de/de_DE/pavoque/low/de_DE-pavoque-low \
  de/de_DE/ramona/low/de_DE-ramona-low \
  de/de_DE/thorsten/medium/de_DE-thorsten-medium \
  en/en_US/libritts_r/medium/en_US-libritts_r-medium \
  en/en_GB/vctk/medium/en_GB-vctk-medium \
  nl/nl_NL/mls/medium/nl_NL-mls-medium; do
  name=$(basename "$v")
  for ext in onnx onnx.json; do
    fetch "$HF/rhasspy/piper-voices/resolve/main/$v.$ext" "$T/voices/$name.$ext"
  done
done

# Impulse responses: list the 16 kHz folder and fetch each file.
curl -fsSL "$HF/api/datasets/davidscripka/MIT_environmental_impulse_responses/tree/main/16khz" \
  | python3 -c 'import json,sys; [print(f["path"]) for f in json.load(sys.stdin) if f["path"].endswith(".wav")]' \
  | while read -r path; do
      fetch "$HF/datasets/davidscripka/MIT_environmental_impulse_responses/resolve/main/$path" \
            "$T/rir/$(basename "$path")"
    done
du -sh "$T/data" "$T/voices" "$T/rir"
echo DOWNLOADS_DONE
