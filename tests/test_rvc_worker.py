import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'server'))
import rvc_worker as rw  # noqa: E402


class FakeEngine:
    """Stands in for rvc-python: copies the input and records the parameters."""
    model = 'billy.pth'

    def __init__(self):
        self.calls = []
        self.fail = None

    def convert(self, source, target, params=None):
        self.calls.append(params)
        if self.fail:
            raise RuntimeError(self.fail)
        Path(target).write_bytes(Path(source).read_bytes())


class SettingsTest(unittest.TestCase):
    def test_defaults_and_validation(self):
        settings = rw.Settings({'SERVITOR_RVC_MODEL': '/m/billy.pth'})
        self.assertEqual((settings.bind, settings.port, settings.version), ('127.0.0.1', 8767, 'v2'))
        self.assertEqual(settings.params['f0method'], 'rmvpe')
        with self.assertRaises(ValueError):
            rw.Settings({})
        with self.assertRaises(ValueError):
            rw.Settings({'SERVITOR_RVC_MODEL': 'x.pth', 'SERVITOR_RVC_F0_METHOD': 'magic'})
        with self.assertRaises(ValueError):
            rw.Settings({'SERVITOR_RVC_MODEL': 'x.pth', 'SERVITOR_RVC_VERSION': 'v3'})

    def test_query_overrides(self):
        self.assertEqual(rw.overrides('pitch=-3&f0=PM&protect=0.4'),
                         {'f0up_key': -3, 'f0method': 'pm', 'protect': 0.4})
        self.assertEqual(rw.overrides(''), {})
        for bad in ('pitch=40', 'f0=magic', 'index_rate=2', 'pitch=tief'):
            with self.assertRaises(ValueError):
                rw.overrides(bad)

    def test_warm_up_tone_is_a_valid_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tone.wav'
            rw.write_tone(path, seconds=0.5)
            with wave.open(str(path)) as audio:
                self.assertEqual((audio.getframerate(), audio.getnframes()), (16000, 8000))


class WorkerServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = rw.Settings({'SERVITOR_RVC_MODEL': 'billy.pth',
                                'SERVITOR_RVC_WORKDIR': self.tmp.name})
        self.engine = FakeEngine()
        self.worker = rw.Worker(settings, self.engine)
        self.worker.ready = True
        self.server = rw.Server(('127.0.0.1', 0), rw.Handler)
        self.server.worker = self.worker
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.wav = Path(self.tmp.name) / 'answer.wav'
        rw.write_tone(self.wav, seconds=0.2)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request(method, path, body=body)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def test_convert_returns_the_engine_output(self):
        body = self.wav.read_bytes()
        response, data = self.request('POST', '/v1/convert?pitch=-2', body)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Type'), 'audio/wav')
        self.assertGreaterEqual(float(response.getheader('X-RVC-Seconds')), 0)
        self.assertEqual(data, body)
        self.assertEqual(self.engine.calls, [{'f0up_key': -2}])
        self.assertEqual(sorted(Path(self.tmp.name).iterdir()), [self.wav])  # temp files gone

    def test_health_reports_model_and_readiness(self):
        response, data = self.request('GET', '/health')
        health = json.loads(data)
        self.assertEqual((response.status, health['ready'], health['model']),
                         (200, True, 'billy.pth'))

    def test_errors(self):
        self.worker.ready = False
        response, _ = self.request('POST', '/v1/convert', self.wav.read_bytes())
        self.assertEqual((response.status, response.getheader('Retry-After')), (503, '10'))
        self.worker.ready = True
        response, _ = self.request('POST', '/v1/convert', b'')
        self.assertEqual(response.status, 413)
        response, _ = self.request('POST', '/v1/convert?f0=magic', self.wav.read_bytes())
        self.assertEqual(response.status, 400)
        response, _ = self.request('POST', '/v1/other', self.wav.read_bytes())
        self.assertEqual(response.status, 404)
        self.engine.fail = 'Invalid data found'
        response, data = self.request('POST', '/v1/convert', self.wav.read_bytes())
        self.assertEqual((response.status, json.loads(data)['error']), (500, 'convert_failed'))
        self.assertEqual(sorted(Path(self.tmp.name).iterdir()), [self.wav])

    def test_only_loopback_peers_may_convert(self):
        handler = rw.Handler.__new__(rw.Handler)
        for address, allowed in (('127.0.0.1', True), ('::1', True), ('172.22.2.100', False),
                                 ('not-an-ip', False)):
            handler.client_address = (address, 5000)
            self.assertEqual(handler._local(), allowed, address)


if __name__ == '__main__':
    unittest.main()
