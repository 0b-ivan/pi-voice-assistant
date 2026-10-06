"""Offline voice playback profiles implemented with FFmpeg."""

import os
from pathlib import Path


DEFAULT_VOICE_PROFILE = "normal"
SUPPORTED_VOICE_PROFILES = ("normal", "servitor")
DEFAULT_FFMPEG_BIN = "/usr/bin/ffmpeg"
DEFAULT_APLAY_BIN = "/usr/bin/aplay"

# Tuned on the Pi Zero 2 W + WM8960. The dry voice stays intelligible while
# parallel branches add metal resonance, a strong flanger, chorus, stutter,
# phaser/doppler motion and a restrained >20 Hz tremolo ring-mod texture.
# The final limiter is intentional: the branch gains are character controls,
# not a request to clip the WM8960 output.
SERVITOR_FILTER_GRAPH = (
    "[0:a]aresample=48000,"
    "asetrate=sample_rate=40320,aresample=48000,atempo=1.190476,"
    "asplit=8[main0][metal0][flange0][choir0][stutter0][aura0][doppler0][ring0];"

    "[main0]"
    "equalizer=f=180:t=q:w=1:g=3,"
    "equalizer=f=2200:t=q:w=1.2:g=3,"
    "equalizer=f=3500:t=q:w=1.2:g=3,"
    "volume=0.48[main];"

    "[metal0]"
    "highpass=f=220,lowpass=f=5400,"
    "equalizer=f=900:t=q:w=0.7:g=10,"
    "equalizer=f=1650:t=q:w=0.65:g=15,"
    "equalizer=f=3000:t=q:w=0.75:g=12,"
    "flanger=delay=3:depth=2:regen=18:width=95:speed=0.65:"
    "shape=sinusoidal:phase=25:interp=linear,"
    "aecho=0.8:0.68:4|8|13|19|27:0.40|0.31|0.23|0.16|0.10,"
    "volume=1.65[metal];"

    "[flange0]"
    "highpass=f=180,lowpass=f=5000,"
    "flanger=delay=8:depth=8:regen=48:width=100:speed=1.0:"
    "shape=sinusoidal:phase=50:interp=linear,"
    "aecho=0.8:0.35:14|29:0.14|0.08,"
    "volume=3.80[flange];"

    "[choir0]"
    "chorus=0.45:1.0:22|31|43|58:"
    "0.80|0.70|0.60|0.55:"
    "0.45|0.60|0.72|0.82:"
    "0.7|1.0|1.4|1.9,"
    "volume=1.85[choir];"

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
    "volume=0.45[aura];"

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

    "[main][metal][flange][choir][stutter][aura][doppler][ring]"
    "amix=inputs=8:duration=first:dropout_transition=0:normalize=0,"
    "volume=4.4,"
    "aecho=0.8:0.16:85|170:0.055|0.025,"
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


def build_playback_command(
    source,
    audio_device,
    profile=None,
    ffmpeg_bin=None,
    aplay_bin=None,
):
    """Build direct playback command; Servitor DSP never renders an effect WAV."""
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
