#!/usr/bin/env python3
"""Compare speech recognizers on synthetic German questions (CT 107).

Synthesizes each sentence with Piper (Thorsten emotional, two speakers and
speaking rates), resamples to 16 kHz mono like the Pi upload, and runs every
recognizer given:

  vosk:MODEL_DIR      streamed in 0.1 s blocks like /v1/turn; the latency
                      that matters is only the finalize after "release"
  whisper:NAME        faster-whisper, CPU int8, runs after "release" on the
                      whole clip; needs --whisper-python (its own venv)

Prints the word error rate (case and punctuation ignored, digits spelled
out), latency after release (mean/max), load time and peak RSS, plus every
miss. Synthetic speech is cleaner than a real microphone; use it to compare
recognizers, not as an absolute accuracy figure.

  /opt/servitor-voice/.venv/bin/python server/bench-stt.py \\
      --whisper-python /opt/servitor-voice/.venv-whisper/bin/python \\
      vosk:/opt/servitor-voice/models/vosk-model-small-de-0.15 whisper:small
"""
import argparse
import json
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
import wave

PIPER_MODEL = os.environ.get('SERVITOR_PIPER_MODEL',
                             '/opt/servitor-voice/tts/de_DE-thorsten_emotional-medium.onnx')
WHISPER_DIR = os.environ.get('WHISPER_MODEL_DIR', '/opt/servitor-voice/models/whisper')
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
NUMBERS = {'3': 'drei', '10': 'zehn', '17': 'siebzehn', '23': 'dreiundzwanzig'}


def normalize(text):
    text = text.lower().replace('×', ' mal ').replace('wieviel', 'wie viel')
    words = re.findall(r'[\wäöüß]+', text)
    return [NUMBERS.get(word, word) for word in words]


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


def run_vosk(model_dir, clips):
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    started = time.monotonic()
    model = Model(model_dir)
    load = time.monotonic() - started
    results = []
    for _sentence, pcm in clips:
        recognizer = KaldiRecognizer(model, 16000)
        for offset in range(0, len(pcm), 3200):  # happens while the button is held
            recognizer.AcceptWaveform(pcm[offset:offset + 3200])
        started = time.monotonic()
        text = json.loads(recognizer.FinalResult()).get('text', '')
        results.append((text, time.monotonic() - started))
    return load, results


def run_whisper(name, clips):
    import numpy
    from faster_whisper import WhisperModel
    started = time.monotonic()
    model = WhisperModel(name, device='cpu', compute_type='int8',
                         cpu_threads=os.cpu_count() or 4, download_root=WHISPER_DIR)
    load = time.monotonic() - started
    results = []
    for _sentence, pcm in clips:
        audio = numpy.frombuffer(pcm, dtype=numpy.int16).astype(numpy.float32) / 32768
        started = time.monotonic()
        segments, _info = model.transcribe(audio, language='de', beam_size=1,
                                           condition_on_previous_text=False)
        text = ' '.join(segment.text for segment in segments)
        results.append((text, time.monotonic() - started))
    return load, results


def child(spec, data):
    clips = [(sentence, bytes.fromhex(pcm)) for sentence, pcm in json.load(open(data))]
    kind, _, target = spec.partition(':')
    load, results = (run_vosk if kind == 'vosk' else run_whisper)(target, clips)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(json.dumps(dict(load=load, rss=rss, results=results)))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--whisper-python', default='/opt/servitor-voice/.venv-whisper/bin/python')
    parser.add_argument('--child', nargs=2, metavar=('SPEC', 'DATA'), help=argparse.SUPPRESS)
    parser.add_argument('recognizers', nargs='*', metavar='vosk:DIR|whisper:NAME')
    args = parser.parse_args()
    if args.child:
        child(*args.child)
        return

    with tempfile.TemporaryDirectory() as directory:
        clips = synthesize(directory)
        data = os.path.join(directory, 'clips.json')
        json.dump([(sentence, pcm.hex()) for sentence, pcm in clips], open(data, 'w'))
        for spec in args.recognizers:
            python = args.whisper_python if spec.startswith('whisper:') else sys.executable
            run = subprocess.run([python, os.path.abspath(__file__), '--child', spec, data],
                                 capture_output=True, text=True)
            if run.returncode:
                print(f'{spec}: FAILED (exit {run.returncode}) {run.stderr.strip()[-300:]}')
                continue
            result = json.loads(run.stdout.strip().splitlines()[-1])
            errors = words = 0
            misses = []
            for (sentence, _pcm), (text, _seconds) in zip(clips, result['results']):
                reference, hypothesis = normalize(sentence), normalize(text)
                errors += distance(reference, hypothesis)
                words += len(reference)
                if reference != hypothesis:
                    misses.append(f'{sentence!r} -> {text.strip()!r}')
            latencies = [seconds for _text, seconds in result['results']]
            print(f"{spec}: WER {errors / words * 100:.1f} %  after release "
                  f"mean {sum(latencies) / len(latencies):.2f} s max {max(latencies):.2f} s  "
                  f"load {result['load']:.1f} s  peak RSS {result['rss']:.0f} MiB  "
                  f"({len(clips)} clips)", flush=True)
            for miss in misses:
                print('   ', miss)


if __name__ == '__main__':
    main()
