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
    "[0:a]aresample=24000,"
    "asetrate=sample_rate=22200,aresample=24000,atempo=1.081081,"
    "asplit=6[main0][metal0][choir0][tracer0][sawphase0][machine0];"

    "[main0]"
    "equalizer=f=180:t=q:w=1:g=3,"
    "equalizer=f=2200:t=q:w=1.2:g=3,"
    "equalizer=f=3500:t=q:w=1.2:g=3,"
    "volume=0.62[main];"

    "[metal0]"
    "highpass=f=220,lowpass=f=5400,"
    "equalizer=f=900:t=q:w=0.65:g=14,"
    "equalizer=f=1650:t=q:w=0.58:g=18,"
    "equalizer=f=3000:t=q:w=0.68:g=15,"
    "flanger=delay=6:depth=6:regen=38:width=100:speed=0.85:"
    "shape=sinusoidal:phase=45:interp=linear,"
    "aecho=0.8:0.42:6|13|24:0.24|0.14|0.08,"
    "volume=2.35[metal];"

    "[choir0]"
    "chorus=0.42:0.52:18|31|49:"
    "0.72|0.58|0.44:"
    "0.52|0.74|0.96:"
    "1.2|1.8|2.4,"
    "aecho=0.8:0.22:19|37:0.11|0.06,"
    "volume=2.35[choir];"

    "[tracer0]"
    "highpass=f=750,lowpass=f=5200,"
    "tremolo=f=17:d=0.82,"
    "aecho=0.8:0.50:34|68|102:0.38|0.21|0.11,"
    "volume=0.92[tracer];"

    "[sawphase0]"
    "highpass=f=650,lowpass=f=4700,"
    "aeval=val(0)*(0.48+0.52*(t*6-floor(t*6))),"
    "aphaser=in_gain=0.34:out_gain=0.54:delay=4:decay=0.72:"
    "speed=0.52:type=triangular,"
    "aecho=0.8:0.18:21|42:0.08|0.04,"
    "volume=0.82[sawphase];"

    "[machine0]"
    "aeval=0.060*(2*(t*220-floor(t*220))-1)"
    "+0.030*(2*(t*440-floor(t*440))-1),"
    "highpass=f=170,lowpass=f=3600,"
    "tremolo=f=5.5:d=0.34,"
    "aphaser=in_gain=0.30:out_gain=0.56:delay=4:decay=0.78:"
    "speed=0.46:type=triangular,"
    "aecho=0.8:0.18:23|47:0.09|0.045,"
    "volume=1.02[machinehum];"

    "[main][metal][choir][tracer][sawphase][machinehum]"
    "amix=inputs=6:duration=first:dropout_transition=0:normalize=0,"
    "volume=4.0,"
    "aecho=0.8:0.08:72|145:0.035|0.015,"
    "alimiter=level_in=2.2:level_out=1:limit=0.97:attack=5:release=60:level=0,"
    "aresample=48000"
    "[out]"
)


# PR #26's free-running aura is only used with fully buffered file rendering.
# Live playback keeps its PCM-clocked source to avoid independent-source stalls.
SERVITOR_REFERENCE_FILTER_GRAPH = SERVITOR_FILTER_GRAPH.replace(
    "asplit=6[main0][metal0][choir0][tracer0][sawphase0][machine0];",
    "asplit=5[main0][metal0][choir0][tracer0][sawphase0];",
).replace(
    "[machine0]aeval=0.060*(2*(t*220-floor(t*220))-1)"
    "+0.030*(2*(t*440-floor(t*440))-1),",
    "aevalsrc=0.060*(2*(t*220-floor(t*220))-1)"
    "+0.030*(2*(t*440-floor(t*440))-1):s=24000,",
)


# Billy's voice (menu "Stimmeffekt: natürlich"): the same Piper speaker, only
# a little lower and warmer, without the machine layers. Light enough for the Pi.
NATURAL_FILTER_GRAPH = (
    "[0:a]aresample=24000,"
    "asetrate=sample_rate=22800,aresample=24000,atempo=1.052632,"
    "equalizer=f=140:t=q:w=1:g=2.5,"
    "equalizer=f=2800:t=q:w=1.4:g=1.5,"
    "alimiter=level_in=1.2:level_out=1:limit=0.97:attack=5:release=60:level=0,"
    "aresample=48000"
    "[out]"
)
# CT 107 with Piper thorsten-high (SERVITOR_HIGH_PIPER_MODEL, calm settings) for
# both voices; chosen by ear on 10.10.2026. Not menu values: the server picks
# them when that model is loaded, the Pi's local fallback keeps the graphs above.
# Billy: the voice is already right, only the limiter and the playback rate remain.
BILLY_FILTER_GRAPH = (
    "[0:a]alimiter=level_in=1:level_out=1:limit=0.95:attack=5:release=60:level=0,"
    "aresample=48000"
    "[out]"
)
# Proximus: thorsten-high 3 semitones lower with the formants (a bigger chest),
# warmer, lightly saturated, then the machine DSP on top as before.
SERVITOR_HIGH_BASE_CHAIN = (
    "asetrate=sample_rate=19670,aresample=22050,atempo=1.121,"
    "equalizer=f=130:t=q:w=1:g=3,equalizer=f=2500:t=q:w=1.2:g=2,"
    "asoftclip=type=tanh:threshold=0.6,lowpass=f=7000,alimiter=limit=0.95,"
)


def _with_base_chain(graph):
    return graph.replace("[0:a]", "[0:a]" + SERVITOR_HIGH_BASE_CHAIN, 1)


VOICE_EFFECTS = ("servitor", "natural")
DEFAULT_VOICE_EFFECT = "servitor"


def voice_effect(value=None):
    """Menu voice effect: 'servitor' (machine DSP) or 'natural' (Billy)."""
    value = str(value or DEFAULT_VOICE_EFFECT).strip().lower()
    return value if value in VOICE_EFFECTS else DEFAULT_VOICE_EFFECT


def filter_graph(effect=None):
    return NATURAL_FILTER_GRAPH if voice_effect(effect) == "natural" else SERVITOR_FILTER_GRAPH


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


def _ffmpeg_output_args(audio_device, effect=None):
    return [
        "-filter_complex",
        filter_graph(effect),
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
    effect=None,
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
        *_ffmpeg_output_args(audio_device, effect),
    ]


def build_stream_playback_command(
    sample_rate,
    channels,
    audio_device,
    profile="servitor",
    ffmpeg_bin=None,
    effect=None,
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
        # PCM format is known. Default stream analysis can wait for seconds of
        # audio (or EOF), defeating sentence-at-a-time Piper synthesis.
        "-probesize",
        "32",
        "-analyzeduration",
        "1",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        "pipe:0",
        *_ffmpeg_output_args(audio_device, effect),
    ]


def build_render_command(source, target, ffmpeg_bin=None, effect=None):
    """Finish Servitor DSP before ALSA playback; optionally restore PR #26 aura.
    The natural effect has no aura variant."""
    aura = os.environ.get("TTS_SERVITOR_AURA", "pcm").strip().lower()
    if aura not in ("pcm", "reference"):
        raise ValueError("TTS_SERVITOR_AURA must be pcm or reference")
    if effect == "billy":
        graph = BILLY_FILTER_GRAPH
    elif voice_effect(effect) == "natural":
        graph = NATURAL_FILTER_GRAPH
    else:
        graph = SERVITOR_REFERENCE_FILTER_GRAPH if aura == "reference" else SERVITOR_FILTER_GRAPH
        if effect == "servitor-high":
            graph = _with_base_chain(graph)
    executable = ffmpeg_bin or os.environ.get("TTS_FFMPEG_BIN", DEFAULT_FFMPEG_BIN)
    return [executable, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-filter_complex_threads", "1", "-i", str(source),
            "-filter_complex", graph, "-map", "[out]",
            "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", "-y", str(target)]
