import io
import math
import random
import sys
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import speaker  # noqa: E402
import voice_quality as vq  # noqa: E402


def tone(*parts, rate=16000, freq=440.0, noise=0):
    """(seconds, amplitude) pieces -> int16 PCM; ``noise``: added white noise."""
    generator = random.Random(7)
    samples = array("h")
    for seconds, amplitude in parts:
        for i in range(int(seconds * rate)):
            value = amplitude * math.sin(2 * math.pi * freq * i / rate)
            value += generator.uniform(-noise, noise) if noise else 0
            samples.append(max(-32768, min(32767, int(value))))
    return vq.pcm_of_samples(samples)


def wav_bytes(pcm, rate=16000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(pcm)
    buffer.seek(0)
    return buffer


def dominant_frequency(pcm, rate=16000):
    """Zero crossings per second / 2: good enough for one clean tone."""
    values = vq.samples_of(pcm)
    crossings = sum(1 for a, b in zip(values, values[1:]) if (a < 0) != (b < 0))
    return crossings / 2 / (len(values) / rate)


def rms(pcm):
    values = vq.samples_of(pcm)
    return math.sqrt(sum(v * v for v in values) / len(values))


class FormatTests(unittest.TestCase):
    def test_int16_little_endian_round_trip(self):
        raw = b"\x01\x00\xff\x7f\x00\x80"          # 1, 32767, -32768 little endian
        self.assertEqual(list(vq.samples_of(raw)), [1, 32767, -32768])
        self.assertEqual(vq.pcm_of_samples([1, 32767, -32768]), raw)
        self.assertEqual(list(vq.samples_of(raw + b"\x05")), [1, 32767, -32768])  # odd byte

    def test_resampling_keeps_pitch_and_length(self):
        for numpy in (True, False):
            with self.subTest(numpy=numpy), patch.dict(sys.modules,
                                                       {} if numpy else {"numpy": None}):
                source = tone((1.0, 8000), rate=48000, freq=440.0)
                out = vq.resample(source, 48000)
                self.assertEqual(len(out) // 2, 16000)
                self.assertAlmostEqual(dominant_frequency(out), 440.0, delta=5)
                self.assertGreater(rms(out), 0.6 * rms(source))

    def test_downsampling_filters_what_cannot_be_represented(self):
        # 12 kHz is above the new Nyquist frequency (8 kHz): it must not come
        # back as a loud 4 kHz alias.
        source = tone((0.5, 8000), rate=48000, freq=12000.0)
        self.assertLess(rms(vq.resample(source, 48000)), 0.1 * rms(source))

    def test_read_wav_mixes_stereo_resamples_and_refuses_bad_formats(self):
        mono = tone((1.0, 8000), rate=48000)
        values = vq.samples_of(mono)
        stereo = vq.pcm_of_samples(v for value in values for v in (value, value))
        pcm = vq.read_wav(wav_bytes(stereo, rate=48000, channels=2))
        self.assertEqual(len(pcm) // 2, 16000)
        self.assertEqual(vq.read_wav(wav_bytes(tone((0.5, 100)))), tone((0.5, 100)))
        with self.assertRaises(vq.AudioFormatError):
            vq.read_wav(wav_bytes(bytes(16000), width=1))
        with self.assertRaises(vq.AudioFormatError):
            vq.read_wav(wav_bytes(bytes(4000), rate=4000))
        with self.assertRaises(vq.AudioFormatError):
            vq.read_wav(io.BytesIO(b"RIFF-not-really"))


class SpeechTests(unittest.TestCase):
    def test_speech_seconds_and_trim(self):
        take = tone((1.0, 0), (2.0, 8000), (1.0, 0))
        self.assertAlmostEqual(vq.speech_seconds(take, vad="energy"), 2.0, delta=0.05)
        self.assertAlmostEqual(len(vq.trim(take, vad="energy")) / 32000, 2.3, delta=0.05)
        self.assertEqual(vq.trim(tone((2.0, 0)), vad="energy"), b"")
        self.assertEqual(vq.speech_seconds(tone((2.0, 150)), vad="energy"), 0.0)  # below floor

    def test_webrtc_vad_is_used_when_installed(self):
        class Vad:
            def __init__(self, mode):
                self.mode = mode

            def is_speech(self, frame, rate):
                return len(frame) == 640 and rate == 16000

        fake = type(sys)("webrtcvad")
        fake.Vad = Vad
        with patch.dict(sys.modules, {"webrtcvad": fake}):
            mask, method = vq.speech_mask(tone((0.5, 0), (1.0, 8000)))
        self.assertEqual(method, "webrtc")
        self.assertEqual(sum(mask), 50)              # the quiet half is below the floor
        with patch.dict(sys.modules, {"webrtcvad": None}):
            self.assertEqual(vq.speech_mask(tone((1.0, 8000)))[1], "energy")


class QualityTests(unittest.TestCase):
    def analyze(self, pcm, **kwargs):
        quality = vq.analyze(pcm, vad="energy", **kwargs)
        return quality, vq.problems(quality)

    def test_a_good_clip_passes_with_numbers(self):
        quality, problems = self.analyze(tone((0.5, 0), (3.0, 8000), (0.5, 0), noise=60))
        self.assertEqual(problems, [])
        self.assertAlmostEqual(quality["speech_seconds"], 3.0, delta=0.05)
        self.assertAlmostEqual(quality["rms_dbfs"], 20 * math.log10(8000 / math.sqrt(2) / 32768),
                               delta=0.5)
        self.assertTrue(quality["snr_valid"])
        self.assertGreater(quality["snr_db"], 30)
        self.assertEqual(quality["clipping_ratio"], 0.0)

    def test_silence(self):
        quality, problems = self.analyze(tone((4.0, 0)))
        self.assertEqual(quality["speech_seconds"], 0.0)
        self.assertIsNone(quality["rms_dbfs"])
        self.assertEqual(problems[0], "too_short")

    def test_many_pauses(self):
        take = tone((0.5, 0), (0.8, 8000), (1.5, 0), (0.8, 8000), (1.5, 0), (0.8, 8000), (0.5, 0))
        quality, problems = self.analyze(take)
        self.assertAlmostEqual(quality["longest_pause"], 1.5, delta=0.05)
        self.assertIn("pauses", problems)

    def test_clipping(self):
        quality, problems = self.analyze(tone((0.5, 0), (3.0, 40000), (0.5, 0)))
        self.assertGreater(quality["clipping_ratio"], vq.CLIP_RATIO_MAX)
        self.assertGreaterEqual(quality["clipped_run_ms"], 0.0)
        self.assertEqual(problems[0], "clipping")

    def test_low_level(self):
        quality, problems = self.analyze(tone((0.5, 0), (3.0, 500), (0.5, 0)))
        self.assertLess(quality["rms_dbfs"], vq.LEVEL_MIN_DBFS)
        self.assertEqual(problems, ["too_quiet"])

    def test_snr_missing_and_noisy(self):
        # Speech from the first frame: no room noise to measure, and missing
        # is not passed.
        quality, problems = self.analyze(tone((3.0, 8000)))
        self.assertEqual((quality["snr_db"], quality["snr_valid"]), (None, False))
        self.assertEqual(problems, ["snr_missing"])
        noise = tone((1.0, 0), noise=2500)
        quality, problems = self.analyze(tone((3.0, 4000)), noise=noise)
        self.assertTrue(quality["snr_valid"])
        self.assertLess(quality["snr_db"], vq.SNR_MIN_DB)
        self.assertIn("noisy", problems)

    def test_room_calibration_ignores_startup_click_and_later_bump(self):
        # 3s of a quiet room with a loud microphone startup transient and a
        # second short handling noise must not poison the SNR of clear speech.
        room = tone((0.04, 32000), (0.96, 30), (0.04, 32000), (1.96, 30))
        speech = tone((0.4, 30), (3.0, 8000), (0.6, 30))
        quality, problems = self.analyze(speech, noise=room)
        self.assertEqual(problems, [])
        self.assertGreater(quality["snr_db"], 25)
        self.assertLess(quality["noise_dbfs"], -55)
        self.assertGreater(quality["noise_peak_dbfs"], -5)
        self.assertGreater(quality["noise_spikes"], 0)
        self.assertAlmostEqual(quality["noise_seconds"], 2.7, delta=0.02)

    def test_noise_louder_than_speech_has_bounded_snr_and_stays_rejected(self):
        room = tone((3.0, 22000))
        quality, problems = self.analyze(tone((0.4, 30), (3.0, 8000), (0.6, 30)),
                                         noise=room)
        self.assertTrue(quality["snr_valid"])
        self.assertEqual(quality["snr_db"], 0.0)
        self.assertIn("noisy", problems)

    def test_room_recording_with_only_startup_guard_is_not_accepted(self):
        room = tone((0.35, 30))
        quality, problems = self.analyze(tone((0.4, 30), (3.0, 8000)), noise=room)
        self.assertFalse(quality["snr_valid"])
        self.assertIn("snr_missing", problems)

    def test_every_problem_has_a_hint(self):
        self.assertEqual(set(vq.ORDER), set(vq.HINTS))


class VectorTests(unittest.TestCase):
    def test_int8_round_trip_change_is_measured(self):
        generator = random.Random(3)
        worst = 1.0
        for _ in range(20):
            vector = speaker.normalize([generator.gauss(0, 1) for _ in range(512)])
            worst = min(worst, speaker.cosine(vector, speaker.decode(speaker.encode(vector))))
        # Measured: the stored int8 print changes a random 512-value embedding
        # by up to ~0.0014 cosine. Calibration therefore uses the stored and
        # reloaded vector (concept, section 6), never the float original.
        self.assertGreater(worst, 0.998)

    @unittest.skipUnless(speaker.MODEL.is_file(), "speaker model not installed")
    def test_model_compares_a_take_with_itself(self):
        embedder = speaker.Embedder()
        take = tone((0.3, 0), (2.0, 8000), (0.3, 0))
        a = embedder.embed(speaker.trim_silence(take))
        b = embedder.embed(speaker.trim_silence(take))
        self.assertGreater(speaker.cosine(a, b), 0.999)


if __name__ == "__main__":
    unittest.main()
