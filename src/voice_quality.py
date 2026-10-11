"""One audio path for enrollment and everyday turns: format, speech, quality.

Concept stimm-einlernen v2, section 5 (Etappe 2). Everything that judges a
voice recording goes through here, on the Pi and on CT 107, so enrollment
clips and turns are measured and trimmed the same way:

- read_wav(): mono 16-bit PCM at 16 kHz; stereo is mixed down, other rates
  are really resampled (low-pass + interpolation), other sample widths and
  broken files are refused (AudioFormatError).
- speech_mask(): speech frames of 20 ms. WebRTC VAD when the module is
  installed (``webrtcvad``, mode SERVITOR_VAD_MODE, default 2), otherwise an
  energy estimate. A frame below SPEECH_FLOOR is never speech.
- trim(): without the quiet before the first and after the last speech
  frame, MARGIN_SECONDS kept on both sides; pauses inside stay.
- analyze(): numbers only (seconds, speech share, longest pause, clipping,
  level in dBFS, SNR with validity flag). problems() turns them into codes,
  the dominant one first; HINTS has one sentence per code.

The limits are the concept's start values for enrollment clips, to be
checked at the real microphone. SNR is an estimate for steady noise from
the quiet frames before the speech; without enough of them it is missing,
and missing is not passed.
"""
import math
import os
import sys
import wave
from array import array

RATE = 16000
FRAME_SECONDS = 0.02
MARGIN_SECONDS = 0.15
FULL_SCALE = 32768.0
SPEECH_FLOOR = 200.0     # RMS (~ -44 dBFS): below this a frame is never speech
NOISE_CAP = 600.0        # a "noise floor" above this is taken to be continuous speech
NOISE_MIN_SECONDS = 0.2  # quiet before the speech needed for an SNR estimate

# Start values for enrollment clips (concept, section 5).
SPEECH_MIN, SPEECH_MAX = 2.0, 8.0
SPEECH_RATIO_MIN = 0.65
PAUSE_MAX = 1.2
CLIP_LEVEL = 0.99 * 32767
CLIP_RATIO_MAX = 0.001
CLIP_RUN_MS_MAX = 5.0
LEVEL_MIN_DBFS = -35.0
SNR_MIN_DB = 12.0

HINTS = {
    'format': "Die Aufnahme hat ein unbrauchbares Format.",
    'too_short': "Zu kurz. Sprich den ganzen Satz in gewöhnlichem Tempo.",
    'too_long': "Zu lang. Sprich nur den angezeigten Satz.",
    'clipping': "Das Mikrofon übersteuert. Gehe etwas zurück oder senke den Aufnahmepegel.",
    'too_quiet': "Zu leise. Sprich etwas deutlicher oder gehe näher heran.",
    'noisy': ("Zu viel Hintergrundgeräusch. Schalte die Geräuschquelle aus und versuche "
              "es erneut."),
    'pauses': "Zu viele Pausen. Sprich den Satz als zusammenhängende Äußerung.",
    'snr_missing': "Kein Raumgeräusch gemessen. Warte nach dem Signalton kurz.",
}
ORDER = ('format', 'too_short', 'clipping', 'too_quiet', 'noisy', 'pauses', 'too_long',
         'snr_missing')


class AudioFormatError(ValueError):
    pass


def samples_of(pcm):
    """int16 little-endian bytes -> array('h'), on any host byte order."""
    values = array('h', pcm[:len(pcm) - len(pcm) % 2])
    if sys.byteorder == 'big':
        values.byteswap()
    return values


def pcm_of_samples(values):
    out = array('h', values)
    if sys.byteorder == 'big':
        out.byteswap()
    return out.tobytes()


def resample(pcm, rate_in, rate_out=RATE):
    """Real resampling of int16 mono PCM: low-pass below the new Nyquist
    frequency, then linear interpolation at the new sample times."""
    if rate_in == rate_out or not pcm:
        return pcm
    source = samples_of(pcm)
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        data = np.asarray(source, dtype=np.float64)
        if rate_out < rate_in:
            data = np.convolve(data, _lowpass(rate_out / rate_in), mode='same')
        count = int(len(source) * rate_out / rate_in)
        positions = np.arange(count) * (rate_in / rate_out)
        out = np.interp(positions, np.arange(len(data)), data)
        return pcm_of_samples(int(v) for v in np.clip(np.rint(out), -32768, 32767))
    data = [float(v) for v in source]
    if rate_out < rate_in:
        taps = _lowpass(rate_out / rate_in)
        half = len(taps) // 2
        data = [sum(taps[k] * data[i + k - half] for k in range(len(taps))
                    if 0 <= i + k - half < len(data)) for i in range(len(data))]
    count = int(len(data) * rate_out / rate_in)
    out = []
    for n in range(count):
        position = n * rate_in / rate_out
        i = int(position)
        frac = position - i
        nxt = data[i + 1] if i + 1 < len(data) else data[i]
        out.append(max(-32768, min(32767, round(data[i] * (1 - frac) + nxt * frac))))
    return pcm_of_samples(out)


def _lowpass(ratio, taps=31):
    """Windowed-sinc low-pass with cutoff at ``ratio`` x the old Nyquist."""
    half = taps // 2
    cutoff = 0.5 * ratio * 0.9
    weights = []
    for k in range(taps):
        n = k - half
        ideal = 2 * cutoff if n == 0 else math.sin(2 * math.pi * cutoff * n) / (math.pi * n)
        weights.append(ideal * (0.54 - 0.46 * math.cos(2 * math.pi * k / (taps - 1))))
    total = sum(weights)
    return [w / total for w in weights]


def read_wav(source):
    """WAV file (path or file object) -> mono 16 kHz int16 PCM."""
    try:
        with wave.open(source if hasattr(source, 'read') else str(source), 'rb') as clip:
            channels, width, rate = clip.getnchannels(), clip.getsampwidth(), clip.getframerate()
            raw = clip.readframes(clip.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioFormatError(f'no readable WAV: {exc}') from None
    if width != 2:
        raise AudioFormatError(f'{width * 8}-bit samples, expected 16-bit')
    if channels not in (1, 2):
        raise AudioFormatError(f'{channels} channels, expected mono or stereo')
    if not 8000 <= rate <= 48000:
        raise AudioFormatError(f'{rate} Hz outside 8-48 kHz')
    if channels == 2:
        values = samples_of(raw)
        raw = pcm_of_samples((values[i] + values[i + 1]) // 2
                             for i in range(0, len(values) - 1, 2))
    return resample(raw, rate)


def levels(pcm, rate=RATE, frame_seconds=FRAME_SECONDS):
    """RMS per frame of int16 mono PCM."""
    values = samples_of(pcm)
    size = max(1, int(rate * frame_seconds))
    return [math.sqrt(sum(s * s for s in values[i:i + size]) / size)
            for i in range(0, len(values) - size + 1, size)]


def _webrtc():
    try:
        import webrtcvad
    except ImportError:
        return None
    try:
        mode = int(os.environ.get('SERVITOR_VAD_MODE', '2'))
    except ValueError:
        mode = 2
    return webrtcvad.Vad(max(0, min(3, mode)))


def speech_mask(pcm, rate=RATE, frame_seconds=FRAME_SECONDS, vad=None):
    """([is speech] per frame, method). ``vad``: 'energy' forces the estimate."""
    frame_levels = levels(pcm, rate, frame_seconds)
    detector = None if vad == 'energy' else _webrtc()
    if detector is not None and rate in (8000, 16000, 32000, 48000):
        size = int(rate * frame_seconds) * 2
        mask = []
        for index, level in enumerate(frame_levels):
            frame = pcm[index * size:(index + 1) * size]
            mask.append(level >= SPEECH_FLOOR / 2 and detector.is_speech(frame, rate))
        return mask, 'webrtc'
    if not frame_levels:
        return [], 'energy'
    # A frame counts when it is twice (6 dB) as loud as the quiet tenth of
    # the take; the cap keeps a take without pauses from measuring itself
    # as noise.
    floor = min(sorted(frame_levels)[len(frame_levels) // 10], NOISE_CAP)
    limit = max(SPEECH_FLOOR, floor * 2)
    return [level >= limit for level in frame_levels], 'energy'


def speech_seconds(pcm, rate=RATE, vad=None):
    mask, _ = speech_mask(pcm, rate, vad=vad)
    return round(sum(mask) * FRAME_SECONDS, 3)


def _span(mask, margin_frames):
    speech = [i for i, flag in enumerate(mask) if flag]
    if not speech:
        return None
    return max(0, speech[0] - margin_frames), min(len(mask), speech[-1] + 1 + margin_frames)


def trim(pcm, rate=RATE, margin=MARGIN_SECONDS, vad=None):
    """Speech from the first to the last speech frame plus ``margin`` on both
    sides; b'' without any speech."""
    mask, _ = speech_mask(pcm, rate, vad=vad)
    span = _span(mask, int(round(margin / FRAME_SECONDS)))
    if span is None:
        return b''
    size = int(rate * FRAME_SECONDS) * 2
    end = len(pcm) - len(pcm) % 2 if span[1] == len(mask) else span[1] * size
    return pcm[span[0] * size:end]


def _dbfs(power):
    return round(10 * math.log10(power / FULL_SCALE ** 2), 1) if power > 0 else None


def analyze(pcm, rate=RATE, noise=None, vad=None):
    """Numbers of one recording (no content). ``noise``: separate PCM of the
    room right before, else the quiet frames before the first speech."""
    values = samples_of(pcm)
    mask, method = speech_mask(pcm, rate, vad=vad)
    frame_levels = levels(pcm, rate)
    margin = int(round(MARGIN_SECONDS / FRAME_SECONDS))
    span = _span(mask, margin)
    speech_frames = sum(mask)
    result = dict(seconds=round(len(values) / rate, 3), vad=method,
                  speech_seconds=round(speech_frames * FRAME_SECONDS, 3),
                  trimmed_seconds=0.0, speech_ratio=0.0, longest_pause=0.0,
                  clipping_ratio=0.0, clipped_run_ms=0.0, rms_dbfs=None,
                  snr_db=None, snr_valid=False)
    if values:
        clipped = run = longest = 0
        for value in values:
            if abs(value) >= CLIP_LEVEL:
                clipped += 1
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        result['clipping_ratio'] = round(clipped / len(values), 5)
        result['clipped_run_ms'] = round(longest * 1000 / rate, 2)
    if span is None:
        return result
    inner = mask[span[0]:span[1]]
    result['trimmed_seconds'] = round(len(inner) * FRAME_SECONDS, 3)
    result['speech_ratio'] = round(speech_frames / len(inner), 3) if inner else 0.0
    pause = longest_pause = 0
    for flag in mask[span[0] + margin:span[1] - margin] or inner:
        pause = 0 if flag else pause + 1
        longest_pause = max(longest_pause, pause)
    result['longest_pause'] = round(longest_pause * FRAME_SECONDS, 3)
    speech_power = (sum(frame_levels[i] ** 2 for i, flag in enumerate(mask) if flag)
                    / speech_frames)
    result['rms_dbfs'] = _dbfs(speech_power)
    if noise is not None:
        quiet = levels(noise, rate)
    else:
        first = next(i for i, flag in enumerate(mask) if flag)
        # Leave a frame of margin before the speech: onset is not noise.
        quiet = frame_levels[:max(0, first - 1)]
    if len(quiet) * FRAME_SECONDS >= NOISE_MIN_SECONDS:
        noise_power = sum(level ** 2 for level in quiet) / len(quiet)
        epsilon = 1.0
        result['snr_db'] = round(10 * math.log10(max(speech_power - noise_power, epsilon)
                                                 / max(noise_power, epsilon)), 1)
        result['snr_valid'] = True
    return result


def problems(quality, speech_min=SPEECH_MIN, speech_max=SPEECH_MAX):
    """Problem codes of an enrollment clip, the dominant one first; [] passes."""
    found = set()
    if quality['speech_seconds'] < speech_min:
        found.add('too_short')
    if quality['speech_seconds'] > speech_max:
        found.add('too_long')
    if (quality['clipping_ratio'] >= CLIP_RATIO_MAX
            or quality['clipped_run_ms'] >= CLIP_RUN_MS_MAX):
        found.add('clipping')
    if quality['rms_dbfs'] is None or quality['rms_dbfs'] < LEVEL_MIN_DBFS:
        found.add('too_quiet')
    if quality['speech_seconds'] and (quality['speech_ratio'] < SPEECH_RATIO_MIN
                                      or quality['longest_pause'] > PAUSE_MAX):
        found.add('pauses')
    if not quality['snr_valid']:
        found.add('snr_missing')
    elif quality['snr_db'] < SNR_MIN_DB:
        found.add('noisy')
    return [code for code in ORDER if code in found]
