#!/bin/sh
# Run as root inside CT 107. Separate venv for Whisper (faster-whisper on
# CTranslate2, CPU int8) so the service venv stays untouched while Whisper
# is evaluated. Models are fetched into $B/models/whisper on first use.
#
#   sh server/install-whisper.sh
set -eu
B=/opt/servitor-voice
V=$B/.venv-whisper
if [ ! -x "$V/bin/python" ]; then
  python3 -m venv "$V"
fi
"$V/bin/pip" install -q --disable-pip-version-check \
  faster-whisper==1.2.1 ctranslate2==4.8.2
install -d -o root -g root -m 0755 "$B/models/whisper"
"$V/bin/python" -c "import faster_whisper, ctranslate2; print('faster-whisper', faster_whisper.__version__, 'ctranslate2', ctranslate2.__version__)"
