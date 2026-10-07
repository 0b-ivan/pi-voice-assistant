#!/bin/sh
# Run as root inside CT 107. Idempotent; keeps an existing /etc/servitor-voice.env.
set -eu
B=/opt/servitor-voice
SRC=$(cd "$(dirname "$0")" && pwd)
if [ "$SRC" != "$B/repo/server" ]; then
  # Copy from another checkout. Run from $B/repo itself (git checkout of the
  # wanted commit) the files are already in place.
  install -d -o root -g root -m 0755 "$B/repo/server" "$B/repo/src"
  install -o root -g root -m 0644 "$SRC/servitor_server.py" "$B/repo/server/servitor_server.py"
  # The server imports transcribe/llm/voice_controls/voice_effects from the
  # matching src tree; replace it as a whole so no stale module survives.
  rm -f "$B/repo/src/"*.py
  install -o root -g root -m 0644 "$SRC/../src/"*.py "$B/repo/src/"
fi
if git -C "$SRC/.." rev-parse HEAD >/dev/null 2>&1; then
  git -C "$SRC/.." rev-parse HEAD > "$B/DEPLOYED"
fi
install -o root -g root -m 0644 "$SRC/servitor-voice.service" /etc/systemd/system/servitor-voice.service
if [ ! -f /etc/servitor-voice.env ]; then
  TOKEN=$("$B/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(36))')
  umask 077
  cat > /etc/servitor-voice.env <<ENV
LANG=C.UTF-8
SERVITOR_API_TOKEN=$TOKEN
SERVITOR_BIND=0.0.0.0
SERVITOR_PORT=8765
SERVITOR_WORKDIR=/run/servitor-voice
SERVITOR_MAX_AUDIO_SECONDS=30
SERVITOR_RATE_LIMIT_PER_MINUTE=20
SERVITOR_PIPER_MODEL=$B/tts/de_DE-thorsten_emotional-medium.onnx
VOSK_MODEL_PATH=$B/models/vosk-model-small-de-0.15
STT_PROVIDER=vosk
TTS_VOICE_PROFILE=servitor
TTS_SERVITOR_AURA=reference
TTS_PIPER_SPEAKER_ID=4
TTS_PIPER_LENGTH_SCALE=1.10
TTS_PIPER_NOISE_SCALE=0.30
TTS_PIPER_NOISE_W_SCALE=0.25
TTS_PIPER_SENTENCE_SILENCE=0.32
TTS_FFMPEG_BIN=/usr/bin/ffmpeg
OPENROUTER_LLM_MODEL=openai/gpt-5.4-mini
OPENROUTER_API_KEY=REPLACE_ME
ENV
  chown root:servitor /etc/servitor-voice.env
  chmod 0640 /etc/servitor-voice.env
fi
systemctl daemon-reload
systemctl enable servitor-voice.service >/dev/null
systemctl restart servitor-voice.service
echo installed
