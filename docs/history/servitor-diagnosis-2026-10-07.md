> **Historisches Protokoll.** Dieser Text dokumentiert die damaligen Messungen oder Entscheidungen; für den aktuellen Betrieb siehe [README](../../README.md), [Architektur](../architecture.md) und [Roadmap](../roadmap.md).

# Servitor diagnosis on pi-assistent, 2026-10-07

Repository and origin/main: d3a7dff. PR #26 reference: 6d95499.
The installed /opt code differs from main and already implements isolated
Vosk/Piper workers, phase timing, a longer live-recognizer drain deadline,
and protection against restarting a still-draining recognizer.
This change makes that existing memory mode reproducible from the repository.

The baseline is saved on the Pi at:
/home/obivan/pi-diagnostics/20261007-182511
It includes a Git bundle, deployed-code archive, service logs, and a private
configuration/ALSA archive. Configuration archives contain credentials and
must stay private. No credentials were copied into this repository.

## Findings

- 415 MiB physical memory is exposed by the OS. Idle available memory was
  approximately 247 MiB; swap was already in use.
- No kernel OOM event appeared in the current boot logs. This does not rule
  out earlier OOM events. CPU throttling was 0x0; temperature was about 44 C.
- The installed voice was thorsten-low, speaker 0. Its startup took
  17.160 s in a short baseline test, with 0.811 s synthesis.
- All/basic/disabled graph optimization, a fixed single thread, an ORT model
  cache, and disabled weight prepacking did not remove startup latency.
  Experimental model caches were not shipped; final hybrid thread settings are documented below.
- An isolated ONNX Runtime 1.23.2 comparison took 13.470 s to load the
  emotional model. The installed 1.30.0 runtime remains unchanged.
- The PCM-clocked and PR #26 aura graphs produced different waveforms from
  identical raw speech. This is an objective difference, not a listening verdict.
- The reference rendering graph was checked for exact equality with the
  SERVITOR_FILTER_GRAPH in commit 6d95499.
- The memory cgroup controller is unavailable on this kernel. systemd
  MemoryMax does not provide an effective limit here; process-level RSS and
  swap timings are the useful measurements. No kernel settings were changed.

## Tested audio path

Use PTT_MEMORY_MODE=isolated to reap the Vosk child before starting Piper.
This mode records 16 kHz mono to a WAV and does not run a live Vosk pump.
The live-mode code retains capture ownership if native recognition drains
past its deadline, and rejects a restart while that thread still owns the file.

For the reference sound, set:

```
TTS_VOICE_PROFILE=servitor
TTS_SERVITOR_MODEL=/opt/pi-voice-assistant/tts/de_DE-thorsten_emotional-medium.onnx
TTS_PIPER_SPEAKER_ID=4
TTS_PIPER_LENGTH_SCALE=1.10
TTS_PIPER_NOISE_SCALE=0.30
TTS_PIPER_NOISE_W_SCALE=0.25
TTS_PIPER_SENTENCE_SILENCE=0.32
PTT_MEMORY_MODE=isolated
TTS_DSP_MODE=buffered
TTS_SERVITOR_AURA=reference
OPENBLAS_NUM_THREADS=1
```

Piper exits before FFmpeg starts. FFmpeg renders a completed WAV with the
PR #26 aura; aplay then plays that file with a 500 ms requested buffer.
Live playback always keeps the PCM-clocked graph. Rendering has a 120 s
bound and removes temporary files if it fails; failed rendering never starts
hardware playback.

## Validation

The existing suite plus initial isolation/audio tests passed: 116 tests.
Two additional reference-path tests passed with the four isolation tests.
The final combined suite passed: 118 tests in 23.417 s.

A local hardware loop used the saved microphone capture, Vosk recognition,
and a fixed response, avoiding any external transmission. It completed with
exit status 0, with no xrun, OOM, or live-pump error in this test.
Measured phases:

| Phase | Time |
| --- | ---: |
| Vosk import | 1.592 s |
| Vosk model load | 7.712 s |
| Vosk recognition | 4.452 s |
| Piper import | 1.774 s |
| Piper model load | 17.320 s |
| Piper synthesis | 3.268 s |
| DSP rendering | 4.785 s |
| Hardware playback | 3.559 s |
| Local loop total | 46.678 s |

Vosk reported 211436 KiB RSS and 13032 KiB swap after recognition.
Piper reported 197520 KiB RSS and zero swap after synthesis. The models
were not simultaneously resident. This is not a claim of zero system swap.

Latency remains unacceptable for interactive use. Buffered effects increase
first-audio delay in exchange for making playback independent of DSP load.
The quality/audio path is tested; startup latency is unresolved. Physical
button interaction, a listening verdict, repeated long utterances and the
external LLM round trip remain separate acceptance checks.

Development is staged in /home/obivan/pi-voice-assistant-servitor-fix on
branch fix/servitor-buffered-memory. At diagnosis time no commit was created because the
Pi had no Git author identity configured. The changes were subsequently
prepared for publication from a clean local checkout using the configured
Git author identity.

## Latency follow-up

The user authorized production installation. The reference-sound configuration
was installed first. The latency version uses PTT_MEMORY_MODE=hybrid:
Piper stays resident with losslessly externalized ONNX weights, its CPU arena
is disabled, and synthesis scratch allocations are released after each reply.
Vosk loads in an isolated standby process during idle time and receives live
16 kHz PCM through a bounded writer queue. It exits before speech synthesis.
Cancellation terminates/reaps the child; canceled native synthesis must finish
before starting another capture or preparing another Vosk model.

The converter verified all 418 initializer values, with 2749 graph nodes.
Model files live under /opt/pi-voice-assistant/tts/servitor-external so that
systemd ProtectHome does not prevent access. Original model files are retained.
No quantization or alternate ONNX Runtime is installed in production.

Repeated hardware tests with fixed local replies measured about 17.8 seconds
from recording release through completed playback, versus 46.678 seconds for
the original isolated reference path. The Vosk and Piper loads move into idle
and initial service startup; they are not made computationally free. These
measurements have different recording/recognition scheduling. First sound
remains around 14.5 seconds in that test, and is not yet satisfactory.

A synthetic question was also recognized and sent to the configured LLM,
which returned Paris. Real saved microphone transcripts were not sent.
Short synthetic full loops measured 14–17 seconds including variable LLM time.
FFmpeg preparation, memory locking and single-thread synthesis were investigated
but are not shipped: they did not reliably improve the complete response time.
The tested four-thread, non-spinning session is retained. Swap remains in use.
Tests saw no OOM, ALSA xrun or live-pump shutdown error; long-term hardware
acceptance and the physical button/listening test remain outstanding.

Automated suite: 127 tests passed in 27.398 seconds on the Pi. A repeat during model startup failed the existing profiler test's one-second child-start timing assertion; the final four-thread configuration passed all 127 tests in 23.633 seconds with the service temporarily stopped.
Production installation retains a second private backup,
pre-latency-deployed.tar.gz, containing the immediately previous code/config.
The install script rolls back this backup if copying/configuration fails.

Final installed-code hardware check completed successfully: 1.393 s live
recognition finalization, 6.985 s synthesis, 4.984 s DSP, 3.424 s playback.
First hardware audio was observed at 13.700 s after recording release;
completion at 17.077 s. This fixed-reply local test excludes LLM time.
Minimum available memory was 206704 KiB; controller swap peaked at 41436 KiB.
