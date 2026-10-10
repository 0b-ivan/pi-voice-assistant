#!/bin/sh
# Run as root inside CT 107. Installs faster-whisper (CTranslate2, CPU int8)
# into the service venv and fetches the model, so that SERVITOR_STT=whisper
# in /etc/servitor-voice.env can use it (src/whisper_stt.py). Without that
# setting the service keeps using Vosk only.
#
#   sh server/install-whisper.sh [MODEL]      # default: small
set -eu
B=/opt/servitor-voice
V=$B/.venv
MODEL=${1:-small}
"$V/bin/pip" install -q --disable-pip-version-check \
  faster-whisper==1.2.1 ctranslate2==4.8.2
install -d -o root -g root -m 0755 "$B/models/whisper"
"$V/bin/python" - "$MODEL" "$B/models/whisper" <<'PY'
import sys
import faster_whisper
import ctranslate2
faster_whisper.WhisperModel(sys.argv[1], device='cpu', compute_type='int8',
                            download_root=sys.argv[2])
print('faster-whisper', faster_whisper.__version__, 'ctranslate2', ctranslate2.__version__,
      'model', sys.argv[1], 'ready')
PY
