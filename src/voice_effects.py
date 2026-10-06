"""Lightweight offline voice profiles implemented with SoX."""

import os
from pathlib import Path
import subprocess


DEFAULT_VOICE_PROFILE = "normal"
SUPPORTED_VOICE_PROFILES = ("normal", "servitor")
DEFAULT_SOX_BIN = "/usr/bin/sox"

# Inspired by marmalade-tts' lightweight SoX character presets. Its ringmod
# effect maps to SoX tremolo at the carrier frequency; we keep the mix lower
# here so German status speech remains intelligible on the small WM8960 speakers.
SERVITOR_EFFECTS = (
    "gain", "-3",
    "pitch", "-300",
    "highpass", "180",
    "lowpass", "4000",
    "compand", "0.05,0.2", "-60,-60,-20,-12,-6,-4",
    "overdrive", "8", "15",
    "tremolo", "60", "35",
    "reverb", "18", "70", "25", "20", "8", "-8",
    "gain", "-n", "-1",
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


def build_sox_command(source, output, profile=None, sox_bin=None):
    profile = resolve_voice_profile(profile)
    if profile == "normal":
        return None
    if profile != "servitor":
        raise AssertionError(f"unhandled voice profile: {profile}")
    executable = sox_bin or os.environ.get("TTS_SOX_BIN", DEFAULT_SOX_BIN)
    return [
        executable,
        str(Path(source)),
        str(Path(output)),
        *SERVITOR_EFFECTS,
    ]


def apply_voice_profile(
    source,
    output,
    profile=None,
    runner=subprocess.run,
    sox_bin=None,
):
    """Render a configured profile and return the WAV path to play."""
    command = build_sox_command(source, output, profile=profile, sox_bin=sox_bin)
    if command is None:
        return Path(source)
    runner(
        command,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return Path(output)
