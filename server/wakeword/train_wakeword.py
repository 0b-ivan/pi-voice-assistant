#!/usr/bin/env python3
"""Train a custom wake word for src/wakeword.py from synthetic speech (CT 107).

The Pi keeps openWakeWord's frozen front end (melspectrogram.onnx and
embedding_model.onnx); only the small classifier on 16 x 96 embeddings is
trained here, so the result is a drop-in <word>.onnx for PTT_WAKE_WORD.

  python train_wakeword.py build    # synthesize, augment, extract features
  python train_wakeword.py train    # fit the classifier, report, export ONNX

Data comes from download.sh (Piper voices, openWakeWord ACAV100M negative
features, validation set, MIT impulse responses). Some voices and speakers
are held out for testing. Nothing here touches the running services; run it
with nice so Proximus keeps answering.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np

ROOT = Path('/opt/servitor-voice/train')  # --root overrides (e.g. smoke tests)
RATE = 16000
WINDOW = 2 * RATE            # 2 s -> exactly 16 embeddings
WORD = 'proximus'
POSITIVE_TEXTS = ('Proximus', 'Proximus.', 'Proximus!', 'Proximus?', 'Proximus,')
# Sound-alikes the model must reject; Proxmox matters most on this server.
ADVERSARIAL_TEXTS = (
    'Proxmox', 'Proxmox.', 'der Proxmox Server', 'Prozess', 'Prozesse', 'Maximus',
    'Proxy', 'Primus', 'Proximo', 'Proximal', 'Proxima', 'Proxima Centauri',
    'Prognose', 'Proviant', 'Professor', 'Prospekt', 'Prokurist', 'Promis', 'Prozent',
    'Krokus', 'Opossum', 'Proximität', 'Praxis', 'Prinzip', 'Minimus', 'Optimus',
    'Promille', 'Positiv', 'Provinz', 'Box im Bus', 'Pro Minus', 'Brox', 'Toxikum',
    'Proxima mus', 'Pokémon', 'Mikrokosmos', 'Proxys',
)
SENTENCES = (
    'Ich habe den Proxmox Server heute neu gestartet.', 'Wie wird das Wetter morgen?',
    'Kannst du bitte das Fenster schließen?', 'Der Prozess läuft seit drei Stunden.',
    'Wir treffen uns um acht Uhr am Bahnhof.', 'Das Essen ist gleich fertig.',
    'Hast du meine Schlüssel gesehen?', 'Der Container braucht mehr Speicher.',
    'Morgen muss ich früh aufstehen.', 'Die Prognose für nächste Woche ist gut.',
    'Ich gehe noch schnell einkaufen.', 'Mach bitte die Musik etwas leiser.',
    'Der Film fängt um neun an.', 'Wo ist eigentlich die Fernbedienung?',
    'Im Büro war heute viel los.', 'Kannst du mir den Proxy erklären?',
    'Wir sollten das Backup prüfen.', 'Der Hund will raus.', 'Danke, das reicht.',
    'Maximus hat das Rennen gewonnen.', 'Die Praxis hat heute geschlossen.',
    'Das kostet zwanzig Prozent mehr.', 'Ruf mich später an.', 'Gute Nacht.',
)
TRAIN_VOICES = {   # file stem: (speaker range or None, weight)
    'de_DE-mls-medium': (range(0, 200), 6), 'de_DE-thorsten_emotional-medium': (range(0, 8), 2),
    'de_DE-eva_k-x_low': (None, 1), 'de_DE-kerstin-low': (None, 1),
    'de_DE-pavoque-low': (None, 1), 'de_DE-thorsten-medium': (None, 1),
    'en_US-libritts_r-medium': (range(0, 800), 3), 'en_GB-vctk-medium': (range(0, 100), 1),
    'nl_NL-mls-medium': (range(0, 45), 2),
}
TEST_VOICES = {
    'de_DE-mls-medium': (range(200, 236), 3), 'de_DE-karlsson-low': (None, 1),
    'de_DE-ramona-low': (None, 1), 'en_US-libritts_r-medium': (range(800, 904), 1),
    'en_GB-vctk-medium': (range(100, 109), 1), 'nl_NL-mls-medium': (range(45, 52), 1),
}


def voice_path(stem):
    for folder in (ROOT / 'voices', Path('/opt/servitor-voice/tts')):
        if (folder / f'{stem}.onnx').is_file():
            return folder / f'{stem}.onnx'
    raise FileNotFoundError(stem)


def resample(audio, rate):
    """Band-limited FFT resampling to 16 kHz (no scipy needed)."""
    if rate == RATE:
        return audio.astype(np.float32)
    n = int(round(len(audio) * RATE / rate))
    spectrum = np.fft.rfft(audio.astype(np.float32))
    keep = n // 2 + 1
    spectrum = spectrum[:keep] if len(spectrum) >= keep else np.pad(spectrum, (0, keep - len(spectrum)))
    return np.fft.irfft(spectrum, n) * (n / len(audio))


class Synth:
    """One Piper voice in memory at a time (all ten together needed ~1.5 GB
    and got the training, and once llama-server, OOM-killed on CT 107)."""

    def __init__(self, voices, rng):
        self.rng = rng
        self.voices = voices
        self.current = (None, None)

    def plan(self, count):
        """How many clips each voice contributes, by weight."""
        weights = np.array([w for _s, w in self.voices.values()], dtype=np.float64)
        shares = np.floor(weights / weights.sum() * count).astype(int)
        shares[0] += count - shares.sum()
        return dict(zip(self.voices, shares.tolist()))

    def load(self, stem):
        from piper import PiperVoice
        if self.current[0] != stem:
            self.current = (None, None)  # free the previous voice first
            self.current = (stem, PiperVoice.load(str(voice_path(stem))))
        return self.current[1]

    def say(self, stem, text):
        from piper.config import SynthesisConfig
        voice = self.load(stem)
        speakers = self.voices[stem][0]
        config = SynthesisConfig(
            speaker_id=int(self.rng.choice(list(speakers))) if speakers else None,
            length_scale=float(self.rng.uniform(0.75, 1.35)),
            noise_scale=float(self.rng.uniform(0.4, 0.95)),
            noise_w_scale=float(self.rng.uniform(0.4, 1.1)))
        pcm = b''.join(chunk.audio_int16_bytes for chunk in voice.synthesize(text, syn_config=config))
        audio = np.frombuffer(pcm, dtype=np.int16)
        return resample(audio, voice.config.sample_rate)


def trim(audio, threshold=0.02):
    level = np.abs(audio)
    loud = np.nonzero(level > threshold * max(level.max(), 1.0))[0]
    return audio[loud[0]:loud[-1] + 1] if len(loud) else audio


def read_wav(path):
    """Mono float samples of a PCM (16/24/32 bit) or IEEE-float WAV, including
    WAVE_FORMAT_EXTENSIBLE (the MIT impulse responses), plus its rate."""
    import struct
    data = Path(path).read_bytes()
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('not a WAV file')
    pos, fmt, frames = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack('<I', data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b'fmt ':
            tag, channels, rate = struct.unpack('<HHI', body[:8])
            bits = struct.unpack('<H', body[14:16])[0]
            if tag == 0xFFFE and len(body) >= 26:  # extensible: real tag in the GUID
                tag = struct.unpack('<H', body[24:26])[0]
            fmt = (tag, channels, rate, bits)
        elif cid == b'data':
            frames = body
        pos += 8 + size + (size & 1)
    if fmt is None or frames is None:
        raise ValueError('missing fmt or data chunk')
    tag, channels, rate, bits = fmt
    if tag == 3 and bits == 32:
        samples = np.frombuffer(frames, dtype='<f4').astype(np.float32)
    elif tag == 1 and bits == 16:
        samples = np.frombuffer(frames, dtype='<i2').astype(np.float32) / 32768
    elif tag == 1 and bits == 24:
        raw = np.frombuffer(frames[:len(frames) // 3 * 3], dtype=np.uint8).reshape(-1, 3)
        ints = (raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8)
                | (raw[:, 2].astype(np.int32) << 16))
        samples = (np.where(ints & 0x800000, ints - 0x1000000, ints) / 8388608).astype(np.float32)
    elif tag == 1 and bits == 32:
        samples = np.frombuffer(frames, dtype='<i4').astype(np.float32) / 2147483648
    else:
        raise ValueError(f'unsupported WAV format tag {tag} with {bits} bits')
    return samples[::channels], rate


def load_rirs():
    rirs = []
    for path in sorted((ROOT / 'rir').glob('*.wav')):
        try:
            data, rate = read_wav(path)
        except (OSError, ValueError):
            continue
        if rate != RATE or not len(data):
            continue
        rirs.append(data / (np.abs(data).max() or 1.0))
    return rirs


def colored_noise(n, rng):
    white = rng.normal(0, 1, n)
    kind = rng.integers(3)
    if kind == 0:
        return white
    spectrum = np.fft.rfft(white)
    freqs = np.maximum(np.fft.rfftfreq(n), 1e-3)
    spectrum /= np.sqrt(freqs) if kind == 1 else freqs  # pink / brown
    return np.fft.irfft(spectrum, n)


def augment(clip, rng, rirs, babble, positive):
    clip = trim(clip) * 10 ** (rng.uniform(-12, 4) / 20)
    if rirs and rng.random() < 0.5:
        rir = rirs[rng.integers(len(rirs))]
        clip = np.convolve(clip, rir)[:len(clip) + RATE // 4]
    window = np.zeros(WINDOW, dtype=np.float32)
    if positive:
        # The word must end shortly before the window end, as at detection time.
        end = WINDOW - int(rng.uniform(0.05, 0.35) * RATE)
        start = end - len(clip)
        if start < 0:
            clip, start = clip[-start:], 0
        window[start:start + len(clip)] = clip[:WINDOW - start]
    else:
        if len(clip) >= WINDOW:
            offset = rng.integers(len(clip) - WINDOW + 1)
            window[:] = clip[offset:offset + WINDOW]
        else:
            start = rng.integers(WINDOW - len(clip) + 1)
            window[start:start + len(clip)] = clip
    signal = np.sqrt(np.mean(window ** 2)) or 1.0
    if rng.random() < 0.8:
        noise = colored_noise(WINDOW, rng)
        snr = rng.uniform(5, 30)
        window += noise / (np.sqrt(np.mean(noise ** 2)) or 1.0) * signal / 10 ** (snr / 20)
    if babble and rng.random() < 0.3:
        other = babble[rng.integers(len(babble))]
        start = rng.integers(max(1, WINDOW - len(other) + 1))
        part = other[:WINDOW - start] * signal / (np.sqrt(np.mean(other ** 2)) or 1.0)
        window[start:start + len(part)] += part * 10 ** (rng.uniform(-20, -8) / 20)
    return np.clip(window, -32768, 32767).astype(np.int16)


class Features:
    """Same frozen front end as the Pi (src/wakeword.py), batched."""

    def __init__(self, threads=2):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        models = ROOT / 'models'
        self.mel = ort.InferenceSession(str(models / 'melspectrogram.onnx'), options)
        self.emb = ort.InferenceSession(str(models / 'embedding_model.onnx'), options)

    def __call__(self, window):
        mel = self.mel.run(None, {self.mel.get_inputs()[0].name:
                                  window[None, :].astype(np.float32)})[0]
        mel = np.squeeze(mel) / 10 + 2
        starts = range(0, mel.shape[0] - 76 + 1, 8)
        batch = np.stack([mel[i:i + 76] for i in starts])[-16:, :, :, None].astype(np.float32)
        emb = self.emb.run(None, {self.emb.get_inputs()[0].name: batch})[0]
        return np.squeeze(emb).reshape(16, 96).astype(np.float16)


def build_set(name, texts, voices, count, variants, positive, rng, rirs, babble, features):
    target = ROOT / 'features' / f'{name}.npy'
    if target.is_file():
        print(f'{name}: exists, skipped', flush=True)
        return
    synth = Synth(voices, rng)
    out = np.zeros((count * variants, 16, 96), dtype=np.float16)
    started = time.monotonic()
    i = 0
    for stem, share in synth.plan(count).items():
        for _ in range(share):
            clip = synth.say(stem, texts[rng.integers(len(texts))])
            if not positive and len(babble) < 300:
                babble.append(clip)
            for j in range(variants):
                out[i * variants + j] = features(augment(clip, rng, rirs, babble, positive))
            i += 1
            if i % 500 == 0:
                rate = i / (time.monotonic() - started)
                print(f'{name}: {i}/{count} clips ({stem}), {rate:.1f}/s', flush=True)
    # Voices come in blocks; shuffle so train batches mix them.
    out = out[rng.permutation(len(out))]
    target.parent.mkdir(exist_ok=True)
    np.save(target, out)
    print(f'{name}: saved {out.shape}', flush=True)


def build(args):
    rng = np.random.default_rng(args.seed)
    rirs = load_rirs()
    print(f'{len(rirs)} impulse responses', flush=True)
    features = Features()
    babble = []
    build_set('adversarial', ADVERSARIAL_TEXTS + SENTENCES, TRAIN_VOICES, args.negatives, 2,
              False, rng, rirs, babble, features)
    build_set('positive_train', POSITIVE_TEXTS, TRAIN_VOICES, args.positives, 2,
              True, rng, rirs, babble, features)
    build_set('positive_test', POSITIVE_TEXTS, TEST_VOICES, args.positives // 8, 1,
              True, rng, rirs, babble, features)
    build_set('adversarial_test', ADVERSARIAL_TEXTS + SENTENCES, TEST_VOICES,
              args.negatives // 8, 1, False, rng, rirs, babble, features)


def make_model(torch):
    nn = torch.nn
    return nn.Sequential(nn.Flatten(), nn.Linear(16 * 96, 64), nn.LayerNorm(64), nn.ReLU(),
                         nn.Linear(64, 64), nn.LayerNorm(64), nn.ReLU(), nn.Linear(64, 1))


def false_positives_per_hour(scores, threshold, patience=2, cooldown_blocks=25):
    """Detections as on the Pi (Detector): N consecutive 80 ms blocks >= threshold."""
    streak, quiet, hits = 0, 0, 0
    for score in scores:
        if quiet:
            quiet -= 1
            streak = 0
            continue
        streak = streak + 1 if score >= threshold else 0
        if streak >= patience:
            hits, streak, quiet = hits + 1, 0, cooldown_blocks
    return hits / (len(scores) * 0.08 / 3600)


def train(args):
    import torch
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    feat = ROOT / 'features'
    pos = torch.from_numpy(np.load(feat / 'positive_train.npy').astype(np.float32))
    adv = torch.from_numpy(np.load(feat / 'adversarial.npy').astype(np.float32))
    pos_test = torch.from_numpy(np.load(feat / 'positive_test.npy').astype(np.float32))
    adv_test = torch.from_numpy(np.load(feat / 'adversarial_test.npy').astype(np.float32))
    acav = np.load(ROOT / 'data' / 'openwakeword_features_ACAV100M_2000_hrs_16bit.npy', mmap_mode='r')
    validation = np.load(ROOT / 'data' / 'validation_set_features.npy', mmap_mode='r')
    print(f'positives {len(pos)}, adversarial {len(adv)}, ACAV {acav.shape}, '
          f'validation {validation.shape} ({validation.shape[0] * 0.08 / 3600:.1f} h)', flush=True)

    model = make_model(torch)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.steps)
    loss_fn = torch.nn.BCEWithLogitsLoss(reduction='none')

    def scores(x):
        model.eval()
        with torch.no_grad():
            out = torch.cat([torch.sigmoid(model(x[i:i + 4096])).squeeze(1)
                             for i in range(0, len(x), 4096)])
        model.train()
        return out.numpy()

    def validation_scores():
        """The validation set is one embedding per 80 ms; slide 16-frame windows
        over it exactly like the Pi does in streaming mode."""
        chunks = []
        step = 65536
        for i in range(0, validation.shape[0] - 15, step):
            block = np.asarray(validation[i:i + step + 15], dtype=np.float32)
            windows = np.lib.stride_tricks.sliding_window_view(block, 16, axis=0)
            chunks.append(scores(torch.from_numpy(np.ascontiguousarray(
                windows.transpose(0, 2, 1)))))
        return np.concatenate(chunks)

    best = None
    third = args.batch // 3
    for step in range(1, args.steps + 1):
        start = rng.integers(0, acav.shape[0] - third)
        general = torch.from_numpy(np.asarray(acav[start:start + third], dtype=np.float32))
        p = pos[torch.randint(len(pos), (third,))]
        a = adv[torch.randint(len(adv), (third,))]
        x = torch.cat([p, a, general])
        y = torch.cat([torch.ones(third), torch.zeros(2 * third)])
        # Ramp the cost of false alarms up over training (as openWakeWord does).
        neg_weight = 1 + (args.max_negative_weight - 1) * min(1.0, step / (0.6 * args.steps))
        w = torch.cat([torch.ones(third), torch.full((2 * third,), neg_weight)])
        loss = (loss_fn(model(x).squeeze(1), y) * w).mean()
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        scheduler.step()
        if step % args.eval_every == 0 or step == args.steps:
            recall = float((scores(pos_test) >= 0.5).mean())
            adv_rate = float((scores(adv_test) >= 0.5).mean())
            val = validation_scores()
            fph = {t: round(false_positives_per_hour(val, t), 2) for t in (0.5, 0.7, 0.9)}
            report = dict(step=step, loss=round(float(loss), 4), recall_at_0_5=round(recall, 3),
                          adversarial_hit_rate=round(adv_rate, 3), false_positives_per_hour=fph)
            print(json.dumps(report), flush=True)
            # Best: highest recall while staying under the false-alarm budget.
            if fph[0.5] <= args.max_fph and (best is None or recall > best[0]):
                best = (recall, report)
                torch.save(model.state_dict(), ROOT / f'{WORD}.pt')
    if best is None:
        print('no checkpoint met the false-alarm budget; saving the last one', flush=True)
        torch.save(model.state_dict(), ROOT / f'{WORD}.pt')
    else:
        print('best:', json.dumps(best[1]), flush=True)
    model.load_state_dict(torch.load(ROOT / f'{WORD}.pt'))
    model.eval()
    exported = torch.nn.Sequential(model, torch.nn.Sigmoid())
    torch.onnx.export(exported, torch.zeros(1, 16, 96), str(ROOT / f'{WORD}.onnx'),
                      input_names=['x'], output_names=['score'],
                      dynamic_axes={'x': {0: 'batch'}, 'score': {0: 'batch'}}, opset_version=17,
                      dynamo=False)  # classic exporter: needs only the onnx package
    print(f'exported {ROOT / f"{WORD}.onnx"}', flush=True)


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('step', choices=('build', 'train'))
    parser.add_argument('--positives', type=int, default=12000)
    parser.add_argument('--negatives', type=int, default=10000)
    parser.add_argument('--steps', type=int, default=30000)
    parser.add_argument('--batch', type=int, default=768)
    parser.add_argument('--eval-every', type=int, default=3000)
    parser.add_argument('--max-negative-weight', type=float, default=30.0)
    parser.add_argument('--max-fph', type=float, default=0.5)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--root', type=Path, default=ROOT,
                        help='working directory; data/voices/rir/models are taken from the default root')
    args = parser.parse_args()
    data_root = ROOT
    ROOT = args.root
    for sub in ('data', 'voices', 'rir', 'models'):
        link = ROOT / sub
        if ROOT != data_root and not link.exists():
            ROOT.mkdir(parents=True, exist_ok=True)
            link.symlink_to(data_root / sub)
    build(args) if args.step == 'build' else train(args)


if __name__ == '__main__':
    main()
