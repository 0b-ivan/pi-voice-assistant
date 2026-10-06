"""Offline voice playback profiles implemented with FFmpeg."""

import os
from pathlib import Path


DEFAULT_VOICE_PROFILE = "normal"
SUPPORTED_VOICE_PROFILES = ("normal", "servitor")
DEFAULT_FFMPEG_BIN = "/usr/bin/ffmpeg"
DEFAULT_APLAY_BIN = "/usr/bin/aplay"

# Tuned on the Pi Zero 2 W + WM8960. The base pitch is only moderately lowered
# so the voice stays authoritative without becoming unnaturally bass-heavy.
# Do not add whole-stream reverse/fade filters here: they buffer the complete
# utterance and destroy time-to-first-audio for the resident streaming path.
SERVITOR_FILTER_GRAPH = (
    "[0:a]aresample=48000,"
    "asetrate=sample_rate=44400,aresample=48000,atempo=1.081081,"
    "asplit=9[main0][metal0][flange0][choir0][stutter0][aura0][doppler0][ring0][tracer0];"

    "[main0]"
    "equalizer=f=180:t=q:w=1:g=3,"
    "equalizer=f=2200:t=q:w=1.2:g=3,"
    "equalizer=f=3500:t=q:w=1.2:g=3,"
    "volume=0.54[main];"

    "[metal0]"
    "highpass=f=220,lowpass=f=5400,"
    "equalizer=f=900:t=q:w=0.65:g=14,"
    "equalizer=f=1650:t=q:w=0.58:g=18,"
    "equalizer=f=3000:t=q:w=0.68:g=15,"
    "flanger=delay=3:depth=2:regen=18:width=95:speed=0.65:"
    "shape=sinusoidal:phase=25:interp=linear,"
    "aecho=0.8:0.68:4|8|13|19|27:0.40|0.31|0.23|0.16|0.10,"
    "volume=2.15[metal];"

    "[flange0]"
    "highpass=f=180,lowpass=f=5000,"
    "flanger=delay=8:depth=8:regen=48:width=100:speed=1.0:"
    "shape=sinusoidal:phase=50:interp=linear,"
    "aecho=0.8:0.35:14|29:0.14|0.08,"
    "volume=3.45[flange];"

    "[choir0]"
    "chorus=0.52:0.92:18|27|39|54:"
    "0.86|0.78|0.68|0.58:"
    "0.52|0.68|0.86|1.08:"
    "1.2|1.7|2.2|2.8,"
    "aecho=0.8:0.30:17|33:0.16|0.09,"
    "volume=2.35[choir];"

    "[stutter0]"
    "highpass=f=450,lowpass=f=4600,"
    "tremolo=f=13:d=0.94,"
    "aecho=0.8:0.45:22|47:0.22|0.12,"
    "volume=0.90[stutter];"

    "[aura0]"
    "highpass=f=500,"
    "tremolo=f=31:d=0.36,"
    "aphaser=in_gain=0.8:out_gain=0.8:delay=3:decay=0.4:"
    "speed=1.6:type=triangular,"
    "aecho=0.8:0.30:31|63:0.13|0.07,"
    "volume=0.38[aura];"

    "[doppler0]"
    "flanger=delay=10:depth=7:regen=15:width=90:speed=0.28:"
    "shape=sinusoidal:phase=25:interp=linear,"
    "volume=0.45[doppler];"

    "[ring0]"
    "highpass=f=700,lowpass=f=5000,"
    "tremolo=f=42:d=0.55,"
    "flanger=delay=2:depth=1:regen=10:width=80:speed=1.4:"
    "shape=triangular:phase=25:interp=linear,"
    "volume=0.55[ring];"

    "[tracer0]"
    "highpass=f=900,lowpass=f=5200,"
    "tremolo=f=17:d=0.82,"
    "aecho=0.8:0.62:34|68|102:0.52|0.30|0.16,"
    "volume=0.82[tracer];"

    "[main][metal][flange][choir][stutter][aura][doppler][ring][tracer]"
    "amix=inputs=9:duration=first:dropout_transition=0:normalize=0,"
    "volume=4.4,"
    "aecho=0.8:0.10:72|145:0.040|0.018,"
    "alimiter=level_in=2.5:level_out=1:limit=0.97:attack=5:release=60:level=0"
    "[out]"
)


def resolve_voice_profile(profile=None):
    value = (
        os.environ.get("TTS_VOICE_PROFILE", DEFAULT_VOICE_PROFILE)
        if profile is None else profile
    )
    value = str(value).strip().lower()
    if value not in SUPPORTED_VOICE_PROFILES:
        supported = ", ".join(SUPPORTED_VOICE_PROFILES)
        raise ValueError(
            f"unsupported TTS_VOICE_PROFILE {value!r}; expected one of: {supported}"
        )
    return value


def _ffmpeg_output_args(audio_device):
    return [
        "-filter_complex",
        SERVITOR_FILTER_GRAPH,
        "-map",
        "[out]",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-f",
        "alsa",
        audio_device,
    ]


def build_playback_command(
    source,
    audio_device,
    profile=None,
    ffmpeg_bin=None,
    aplay_bin=None,
):
    """Build file playback command for normal mode and the CLI fallback."""
    profile = resolve_voice_profile(profile)
    source = str(Path(source))
    if profile == "normal":
        return [
            aplay_bin or DEFAULT_APLAY_BIN,
            "-q",
            "-D",
            audio_device,
            source,
        ]

    executable = ffmpeg_bin or os.environ.get("TTS_FFMPEG_BIN", DEFAULT_FFMPEG_BIN)
    return [
        executable,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-i",
        source,
        *_ffmpeg_output_args(audio_device),
    ]


def build_stream_playback_command(
    sample_rate,
    channels,
    audio_device,
    profile="servitor",
    ffmpeg_bin=None,
):
    """Build FFmpeg raw-PCM stdin -> live DSP -> ALSA command."""
    if resolve_voice_profile(profile) != "servitor":
        raise ValueError("stream playback is only defined for the servitor profile")
    sample_rate = int(sample_rate)
    channels = int(channels)
    if sample_rate <= 0 or channels <= 0:
        raise ValueError("sample_rate and channels must be positive")

    executable = ffmpeg_bin or os.environ.get("TTS_FFMPEG_BIN", DEFAULT_FFMPEG_BIN)
    return [
        executable,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        "pipe:0",
        *_ffmpeg_output_args(audio_device),
    ]
