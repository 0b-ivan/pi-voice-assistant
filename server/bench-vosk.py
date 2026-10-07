#!/usr/bin/env python3
"""Compare Vosk models on synthetic German questions (CT 107).

Synthesizes each sentence with Piper (Thorsten emotional, neutral speakers
and two speaking rates), resamples to 16 kHz mono like the Pi upload, and
recognizes it with every model given. Prints the word error rate, recognition
time per second of audio and the peak RSS after loading each model.

Synthetic speech is cleaner than a real microphone; use it to compare models,
not as an absolute accuracy figure.

  /opt/servitor-voice/.venv/bin/python server/bench-vosk.py MODEL_DIR [MODEL_DIR ...]
"""
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
import wave

PIPER_MODEL = os.environ.get('SERVITOR_PIPER_MODEL',
                             '/opt/servitor-voice/tts/de_DE-thorsten_emotional-medium.onnx')
SENTENCES = (
    'wie hoch ist der eiffelturm',
    'was ist die hauptstadt von australien',
    'wie spät ist es in tokio',
    'wie viele minuten hat ein tag',
    'wie viel ist siebzehn mal dreiundzwanzig',
    'erkläre kurz was ein raspberry pi ist',
    'stell einen timer auf zehn minuten',
    'wie wird das wetter morgen in berlin',
    'was ist der unterschied zwischen wetter und klima',
    'nenne drei planeten unseres sonnensystems',
)
VOICES = ((0, 1.0), (4, 1.15))  # (speaker id, length scale)


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, 1):
        current = [i]
        for j, hyp in enumerate(hypothesis, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ref != hyp)))
        previous = current
    return previous[-1]


def synthesize(directory):
    from piper import PiperVoice
    from piper.config import SynthesisConfig
    voice = PiperVoice.load(PIPER_MODEL)
    clips = []
    for index, sentence in enumerate(SENTENCES):
        for speaker, scale in VOICES:
            raw = os.path.join(directory, f'{index}-{speaker}.wav')
            with wave.open(raw, 'wb') as output:
                voice.synthesize_wav(sentence, output, syn_config=SynthesisConfig(
                    speaker_id=speaker, length_scale=scale))
            pcm = subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', raw, '-ar', '16000',
                                  '-ac', '1', '-f', 's16le', '-'],
                                 check=True, capture_output=True).stdout
            clips.append((sentence, pcm))
    return clips


def recognize(model_dir, clips):
    """Runs in a child so each model's memory is measured on its own."""
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    started = time.monotonic()
    model = Model(model_dir)
    load = time.monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    errors = words = 0
    audio = busy = 0.0
    misses = []
    for sentence, pcm in clips:
        recognizer = KaldiRecognizer(model, 16000)
        started = time.monotonic()
        for offset in range(0, len(pcm), 3200):
            recognizer.AcceptWaveform(pcm[offset:offset + 3200])
        text = json.loads(recognizer.FinalResult()).get('text', '')
        busy += time.monotonic() - started
        audio += len(pcm) / 32000
        reference, hypothesis = sentence.split(), text.split()
        errors += distance(reference, hypothesis)
        words += len(reference)
        if reference != hypothesis:
            misses.append(f'{sentence!r} -> {text!r}')
    return dict(model=os.path.basename(model_dir), load_s=round(load, 1),
                rss_mib=round(rss), wer=round(errors / words * 100, 1),
                rtf=round(busy / audio, 3), misses=misses)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--child':
        clips = json.load(open(sys.argv[3]))
        clips = [(sentence, bytes.fromhex(pcm)) for sentence, pcm in clips]
        print(json.dumps(recognize(sys.argv[2], clips)))
        return
    with tempfile.TemporaryDirectory() as directory:
        clips = synthesize(directory)
        data = os.path.join(directory, 'clips.json')
        json.dump([(sentence, pcm.hex()) for sentence, pcm in clips], open(data, 'w'))
        for model_dir in sys.argv[1:]:
            result = json.loads(subprocess.run(
                [sys.executable, __file__, '--child', model_dir, data],
                check=True, capture_output=True, text=True).stdout)
            print(f"{result['model']}: WER {result['wer']} %  RTF {result['rtf']}  "
                  f"load {result['load_s']} s  peak RSS {result['rss_mib']} MiB  "
                  f"({len(clips)} clips)")
            for miss in result['misses']:
                print('   ', miss)


if __name__ == '__main__':
    main()
