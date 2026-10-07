#!/usr/bin/env python3
"""Apply the tested, reversible Servitor audio configuration on this Pi."""
from pathlib import Path
import os
import shutil
import subprocess

snapshot = Path('/home/obivan/pi-diagnostics/20261007-182511')
development = snapshot / 'latency-development'
updates = {
    '/etc/pi-ptt.env': {'PTT_MEMORY_MODE': 'hybrid', 'TTS_PLAYBACK_MODE': 'buffered', 'TTS_DSP_MODE': 'buffered',
                      'TTS_SERVITOR_AURA': 'reference'},
    '/etc/pi-voice-assistant.env': {
        'TTS_SERVITOR_MODEL': '/opt/pi-voice-assistant/tts/servitor-external/emotional.onnx',
        'TTS_PIPER_SPEAKER_ID': '4', 'TTS_PIPER_LENGTH_SCALE': '1.10',
        'TTS_PIPER_NOISE_SCALE': '0.30', 'TTS_PIPER_NOISE_W_SCALE': '0.25',
        'TTS_PIPER_SENTENCE_SILENCE': '0.32', 'OPENBLAS_NUM_THREADS': '1'},
}
backup = snapshot / 'pre-latency-deployed.tar.gz'
if backup.exists():
    raise SystemExit('Latency backup exists; inspect it before reapplying')
subprocess.run(['tar', '-czf', str(backup), '-C', '/',
                'opt/pi-voice-assistant/src', 'etc/pi-ptt.env',
                'etc/pi-voice-assistant.env'], check=True)
os.chmod(backup, 0o600)
subprocess.run(['systemctl', 'stop', 'pi-ptt.service'], check=True)
try:
    model_dir = Path('/opt/pi-voice-assistant/tts/servitor-external')
    model_dir.mkdir(parents=True, exist_ok=True)
    for name in ('emotional.onnx', 'emotional.weights', 'emotional.onnx.json', 'emotional.verification.json'):
        shutil.copyfile(snapshot / 'external' / name, model_dir / name)
        os.chmod(model_dir / name, 0o644)
    for name in ('ptt.py', 'transcribe.py', 'voice_controls.py', 'runtime_metrics.py', 'speak.py', 'voice_effects.py', 'piper_worker.py'):
        shutil.copyfile(development / 'src' / name, Path('/opt/pi-voice-assistant/src') / name)
    for filename, values in updates.items():
        target = Path(filename)
        metadata = target.stat()
        lines = target.read_text().splitlines()
        result = []
        seen = set()
        for line in lines:
            key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
            if key in values:
                if key not in seen:
                    result.append(f'{key}={values[key]}')
                    seen.add(key)
            else:
                result.append(line)
        result.extend(f'{key}={value}' for key, value in values.items() if key not in seen)
        temporary = target.with_name(target.name + '.servitor.tmp')
        temporary.write_text('\n'.join(result) + '\n')
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.chmod(temporary, metadata.st_mode & 0o777)
        temporary.replace(target)
except BaseException:
    subprocess.run(['tar', '-xzf', str(backup), '-C', '/'], check=True)
    raise
finally:
    subprocess.run(['systemctl', 'start', 'pi-ptt.service'], check=True)
print('Tested Servitor latency changes applied; pre-latency backup retained.')
