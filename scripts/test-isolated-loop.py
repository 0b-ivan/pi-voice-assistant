#!/usr/bin/env python3
"""Test a saved microphone capture through isolated STT, local TTS."""
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from transcribe import transcribe_with_provider
from speak import speak

os.environ.update(PTT_MEMORY_MODE='isolated', TTS_VOICE_PROFILE='servitor',
    TTS_SERVITOR_MODEL='/opt/pi-voice-assistant/tts/de_DE-thorsten_emotional-medium.onnx',
    TTS_PIPER_SPEAKER_ID='4', TTS_PIPER_LENGTH_SCALE='1.10',
    TTS_PIPER_NOISE_SCALE='0.30', TTS_PIPER_NOISE_W_SCALE='0.25',
    TTS_PIPER_SENTENCE_SILENCE='0.32', TTS_DSP_MODE='buffered',
    TTS_SERVITOR_AURA='reference', OPENBLAS_NUM_THREADS='1',
    OPENROUTER_LLM_MAX_TOKENS='96')
started = time.monotonic()
text, provider = transcribe_with_provider(sys.argv[1])
print(json.dumps(dict(event='diagnostic_transcript', text=text, provider=provider)), flush=True)
reply = 'System bereit. Der Servitor erwartet deinen Befehl.'
print(json.dumps(dict(event='diagnostic_local_reply', text=reply)), flush=True)
speak(reply)
print(json.dumps(dict(event='diagnostic_complete', seconds=round(time.monotonic()-started,3))), flush=True)
