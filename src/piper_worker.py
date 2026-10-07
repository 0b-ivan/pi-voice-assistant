#!/usr/bin/env python3
"""One-shot direct Piper synthesis; exit releases the model before playback."""
import argparse
import json
import os
from pathlib import Path
import sys
import wave

from runtime_metrics import phase, process_ready


def load_voice(model):
    from piper import PiperVoice
    level = os.environ.get('TTS_PIPER_GRAPH_OPTIMIZATION', 'all').strip().lower()
    if level == 'all':
        return PiperVoice.load(model)
    if level not in ('basic', 'disabled'):
        raise ValueError('TTS_PIPER_GRAPH_OPTIMIZATION must be all, basic or disabled')
    # Piper 1.8.0 load() does not expose SessionOptions. Build the same CPU
    # voice/config with the selected optimization level, without loading twice.
    import onnxruntime as ort
    from piper.config import PiperConfig
    options = ort.SessionOptions()
    options.graph_optimization_level = (
        ort.GraphOptimizationLevel.ORT_ENABLE_BASIC if level == 'basic'
        else ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    )
    with open(str(model) + '.json', encoding='utf-8') as source:
        config = PiperConfig.from_dict(json.load(source))
    return PiperVoice(
        config=config,
        session=ort.InferenceSession(str(model), sess_options=options,
                                    providers=['CPUExecutionProvider']),
        download_dir=Path.cwd(),
    )


def synthesize(model, target, profile, text):
    process_ready('tts')
    print(json.dumps(dict(version=1, event='tts_worker_config',
                          graph_optimization=os.environ.get('TTS_PIPER_GRAPH_OPTIMIZATION', 'all'))),
          flush=True)
    with phase('tts', 'import'):
        import piper
        from voice_controls import _synthesize_voice
    with phase('tts', 'model_load'):
        voice = load_voice(model)
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
