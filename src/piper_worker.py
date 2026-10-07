#!/usr/bin/env python3
"""One-shot direct Piper synthesis; exit releases the model before playback."""
import argparse
import json
import os
import sys
import wave

from runtime_metrics import phase, process_ready


def synthesize(model, target, profile, text):
    process_ready('tts')
    with phase('tts', 'import'):
        from piper import PiperVoice
        from voice_controls import _synthesize_voice
    with phase('tts', 'model_load'):
        voice = PiperVoice.load(model)
    # Preserve the existing CLI Servitor defaults when no explicit tuning is set.
    for key, value in [('TTS_PIPER_LENGTH_SCALE', '1.10'),
                       ('TTS_PIPER_NOISE_SCALE', '0.30'),
                       ('TTS_PIPER_NOISE_W_SCALE', '0.25')]:
        os.environ.setdefault(key, value)
    with phase('tts', 'synthesis'):
        with wave.open(target, 'wb') as output:
            _synthesize_voice(voice, text, output, profile)
    with wave.open(target, 'rb') as audio:
        print(json.dumps(dict(version=1, event='tts_audio_ready',
                    audio_duration_ms=round(audio.getnframes() * 1000 / audio.getframerate()),
                    sample_rate=audio.getframerate(), channels=audio.getnchannels())), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model')
    parser.add_argument('target')
    parser.add_argument('profile', choices=['normal', 'servitor'])
    args = parser.parse_args()
    synthesize(args.model, args.target, args.profile, sys.stdin.read().strip())


if __name__ == '__main__':
    main()
