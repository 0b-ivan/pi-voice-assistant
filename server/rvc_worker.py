#!/usr/bin/env python3
"""Billy's own voice on CT 107: RVC voice conversion as a loopback service.

The voice service (servitor_server.py) synthesizes Billy's answer with Piper
Thorsten and, when ``SERVITOR_RVC_URL`` is set, posts the WAV here. This
process keeps an RVC v2 model (rvc-python, PyTorch on the CPU) resident and
answers with the converted WAV. It runs in its own Python 3.10 venv
(server/install-rvc.sh) because rvc-python pins fairseq 0.12.2 and numpy 1.23.

Protocol (HTTP/1.1, loopback peers only, no token):

* ``POST /v1/convert`` with a WAV body, answered with a WAV
  (``X-RVC-Seconds``: conversion time). Optional query parameters override
  the configured settings for this request only: ``pitch``, ``f0``,
  ``index_rate``, ``protect``, ``rms_mix`` (server/bench-rvc.py uses them).
* ``GET /health``: ``{"ok": true, "ready": bool, "model": ..., "rss_mb": ...}``.

The model itself (e.g. an RVC v2 model of B.J. Blazkowicz) is never part of
the repository; install-rvc.sh copies it from a local file. Private use only.

Settings (``/etc/servitor-rvc.env``): ``SERVITOR_RVC_MODEL`` (.pth),
``SERVITOR_RVC_INDEX`` (.index, optional), ``SERVITOR_RVC_VERSION`` (v2|v1), ``SERVITOR_RVC_PITCH`` (semitones),
``SERVITOR_RVC_F0_METHOD`` (rmvpe|pm|harvest|crepe), ``SERVITOR_RVC_INDEX_RATE``,
``SERVITOR_RVC_PROTECT``, ``SERVITOR_RVC_RMS_MIX``, ``SERVITOR_RVC_THREADS``,
``SERVITOR_RVC_UNSAFE_LOAD`` (1: full pickle load of a trusted model),
``SERVITOR_RVC_BIND``/``SERVITOR_RVC_PORT`` (127.0.0.1:8767),
``SERVITOR_RVC_WORKDIR``.
"""
import functools
import http.server
import ipaddress
import json
import math
import os
import pickle
import socketserver
import struct
import tempfile
import threading
import time
import urllib.parse
import wave

MAX_BYTES = 16 * 1024 * 1024   # ~6 min of 22 kHz mono; answers are far shorter
F0_METHODS = ('rmvpe', 'pm', 'harvest', 'crepe')
# Query parameter -> (rvc-python parameter, type, minimum, maximum)
OVERRIDES = {
    'pitch': ('f0up_key', int, -24, 24),
    'index_rate': ('index_rate', float, 0.0, 1.0),
    'protect': ('protect', float, 0.0, 0.5),
    'rms_mix': ('rms_mix_rate', float, 0.0, 1.0),
}


class Settings:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.model = env.get('SERVITOR_RVC_MODEL', '').strip()
        if not self.model:
            raise ValueError('SERVITOR_RVC_MODEL must name the RVC .pth file')
        self.index = env.get('SERVITOR_RVC_INDEX', '').strip()
        self.version = env.get('SERVITOR_RVC_VERSION', 'v2').strip().lower()
        if self.version not in ('v1', 'v2'):
            raise ValueError('SERVITOR_RVC_VERSION must be v1 or v2')
        self.params = dict(
            f0up_key=int(env.get('SERVITOR_RVC_PITCH', '0')),
            f0method=env.get('SERVITOR_RVC_F0_METHOD', 'rmvpe').strip().lower(),
            index_rate=float(env.get('SERVITOR_RVC_INDEX_RATE', '0.5')),
            protect=float(env.get('SERVITOR_RVC_PROTECT', '0.33')),
            rms_mix_rate=float(env.get('SERVITOR_RVC_RMS_MIX', '0.25')),
        )
        if self.params['f0method'] not in F0_METHODS:
            raise ValueError(f'SERVITOR_RVC_F0_METHOD must be one of {", ".join(F0_METHODS)}')
        self.unsafe_load = env.get('SERVITOR_RVC_UNSAFE_LOAD') == '1'
        self.threads = int(env.get('SERVITOR_RVC_THREADS', '4'))
        self.bind = env.get('SERVITOR_RVC_BIND', '127.0.0.1')
        self.port = int(env.get('SERVITOR_RVC_PORT', '8767'))
        self.workdir = env.get('SERVITOR_RVC_WORKDIR') or tempfile.gettempdir()


def overrides(query):
    """Per-request parameters from the query string; ValueError when invalid."""
    values = urllib.parse.parse_qs(query)
    params = {}
    for name, (key, kind, low, high) in OVERRIDES.items():
        if name in values:
            value = kind(values[name][0])
            if not low <= value <= high:
                raise ValueError(f'{name} must be between {low} and {high}')
            params[key] = value
    if 'f0' in values:
        method = values['f0'][0].strip().lower()
        if method not in F0_METHODS:
            raise ValueError(f'f0 must be one of {", ".join(F0_METHODS)}')
        params['f0method'] = method
    return params


def rss_mb():
    try:
        with open('/proc/self/status') as status:
            for line in status:
                if line.startswith('VmRSS:'):
                    return round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return None


def write_tone(path, seconds=1.5, rate=16000):
    """A short voiced tone with a vowel-like overtone for the warm-up turn."""
    frames = bytearray()
    for i in range(int(seconds * rate)):
        t = i / rate
        value = 0.25 * math.sin(2 * math.pi * 120 * t) + 0.1 * math.sin(2 * math.pi * 720 * t)
        frames += struct.pack('<h', int(value * 32767))
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))


class RvcEngine:
    """rvc-python's RVCInference, adapted to a read-only system service."""

    def __init__(self, settings):
        self.settings = settings
        self.rvc = None

    def load(self):
        import torch
        torch.set_num_threads(self.settings.threads)
        import rvc_python.infer as infer
        from rvc_python.configs import config as rvc_config
        # Base models (hubert_base.pt, rmvpe.pt) come from install-rvc.sh; the
        # library would otherwise try to download them on every start.
        infer.download_rvc_models = lambda lib_dir: None
        # Config.use_fp32_config rewrites files inside site-packages, which is
        # read-only under systemd. Only the in-memory flag matters for inference.
        config_class = next(cell.cell_contents for cell in rvc_config.Config.__closure__
                            if isinstance(cell.cell_contents, type))

        def use_fp32_config(config):
            for value in config.json_config.values():
                value['train']['fp16_run'] = False
        config_class.use_fp32_config = use_fp32_config
        # 'cpu', not 'cpu:0': rvc-python enables half precision for any other name.
        self.rvc = infer.RVCInference(models_dir=self.settings.workdir, device='cpu')
        index = self.settings.index if self.settings.index and \
            os.path.isfile(self.settings.index) else ''
        # An RVC .pth is a pickle: loaded normally, a model from the internet
        # could run code. Weights only, unless the operator trusts the file.
        original = torch.load
        if not self.settings.unsafe_load:
            torch.load = functools.partial(original, weights_only=True)
        try:
            self.rvc.load_model(self.settings.model, version=self.settings.version,
                                index_path=index)
        except pickle.UnpicklingError as exc:
            raise RuntimeError(f'{self.model} holds more than weights; load it only if you '
                               f'trust it (SERVITOR_RVC_UNSAFE_LOAD=1): {exc}') from exc
        finally:
            torch.load = original
        self.rvc.set_params(**self.settings.params)

    @property
    def model(self):
        return os.path.basename(self.settings.model)

    def convert(self, source, target, params=None):
        """Convert one WAV; params override the settings for this call only."""
        import numpy
        from scipy.io import wavfile
        vc = self.rvc.vc
        current = dict(self.settings.params, **(params or {}))
        info = self.rvc.models[self.rvc.current_model]
        result = vc.vc_single(
            sid=0, input_audio_path=str(source), f0_up_key=current['f0up_key'],
            f0_file='', f0_method=current['f0method'], file_index=info.get('index') or '',
            file_index2='', index_rate=current['index_rate'], filter_radius=3,
            resample_sr=0, rms_mix_rate=current['rms_mix_rate'], protect=current['protect'])
        # vc_single returns (traceback, (None, None)) instead of raising.
        if not isinstance(result, numpy.ndarray):
            message = result[0] if isinstance(result, tuple) else repr(result)
            raise RuntimeError(str(message).strip().splitlines()[-1])
        wavfile.write(str(target), vc.tgt_sr, result)


class Worker:
    def __init__(self, settings, engine):
        self.settings = settings
        self.engine = engine
        self.ready = False
        self.lock = threading.Lock()

    def warm_up(self):
        """Loads HuBERT and the pitch model, which rvc-python does lazily on
        the first conversion; otherwise Billy's first answer waits for them."""
        source = self._temporary('warm-')
        target = self._temporary('warm-out-')
        try:
            write_tone(source)
            self.engine.convert(source, target)
        finally:
            os.unlink(source)
            os.unlink(target)

    def _temporary(self, prefix):
        fd, name = tempfile.mkstemp(prefix=prefix, suffix='.wav', dir=self.settings.workdir)
        os.close(fd)
        return name

    def convert(self, data, params=None):
        source = self._temporary('in-')
        target = self._temporary('out-')
        try:
            with open(source, 'wb') as out:
                out.write(data)
            with self.lock:
                started = time.monotonic()
                self.engine.convert(source, target, params)
                seconds = time.monotonic() - started
            with open(target, 'rb') as result:
                return result.read(), seconds
        finally:
            os.unlink(source)
            os.unlink(target)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'ServitorRVC/1'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _reply(self, status, body, content_type='application/json', extra=None):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def do_GET(self):
        worker = self.server.worker
        if urllib.parse.urlsplit(self.path).path != '/health':
            return self._reply(404, {'error': 'not_found'})
        return self._reply(200, {'ok': True, 'ready': worker.ready,
                                 'model': worker.engine.model, 'rss_mb': rss_mb()})

    def do_POST(self):
        worker = self.server.worker
        url = urllib.parse.urlsplit(self.path)
        if not self._local():
            return self._reply(403, {'error': 'loopback_only'})
        if url.path != '/v1/convert':
            return self._reply(404, {'error': 'not_found'})
        if not worker.ready:
            return self._reply(503, {'error': 'loading'}, extra={'Retry-After': '10'})
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self._reply(411, {'error': 'length_required'})
        if not 44 < length <= MAX_BYTES:
            return self._reply(413, {'error': 'bad_size'})
        try:
            params = overrides(url.query)
        except ValueError as exc:
            return self._reply(400, {'error': 'bad_parameter', 'message': str(exc)})
        data = self.rfile.read(length)
        try:
            audio, seconds = worker.convert(data, params)
        except Exception as exc:
            print(json.dumps(dict(event='convert_failed', error=str(exc))), flush=True)
            return self._reply(500, {'error': 'convert_failed', 'message': str(exc)})
        return self._reply(200, audio, 'audio/wav', {'X-RVC-Seconds': f'{seconds:.3f}'})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    settings = Settings()
    worker = Worker(settings, RvcEngine(settings))
    server = Server((settings.bind, settings.port), Handler)
    server.worker = worker

    def load():
        started = time.monotonic()
        try:
            worker.engine.load()
            worker.warm_up()
        except BaseException as exc:
            print(json.dumps(dict(event='load_failed', error=str(exc))), flush=True)
            os._exit(1)  # let systemd restart the service
        worker.ready = True
        print(json.dumps(dict(event='ready', model=worker.engine.model, rss_mb=rss_mb(),
                              load_seconds=round(time.monotonic() - started, 3))), flush=True)

    threading.Thread(target=load, daemon=True).start()
    print(json.dumps(dict(event='listening', bind=settings.bind, port=settings.port)),
          flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
