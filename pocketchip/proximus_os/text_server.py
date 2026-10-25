"""Optional CT107 companion: JSON -> local LLM -> JSON. No voice imports."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
from .transport import json_request, validate_url

PROMPT = '''Du bist Proximus, ein sachlicher deutschsprachiger Textassistent für Proximus OS.
Antworte lesbar, präzise und direkt als Text. Markdown-Listen sind erlaubt.
Das Handheld ist ein original PocketCHIP: Allwinner R8 ARMv7, 512 MB RAM,
4 GB physisches NAND, 480x272 Display. Die Inferenz läuft auf CT107.
Du hast hier keine Messwerte, keine gespeicherten persönlichen Daten und keine
Werkzeuge oder Berechtigung zur Gerätesteuerung. Behaupte keine ausgeführten Aktionen.
Jede Anfrage ist unabhängig; frage bei fehlendem Kontext nach. Keine Sprachausgabe.'''


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, token, backend):
        if (not isinstance(token, str) or not 32 <= len(token) <= 256 or
                any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise ValueError('PROXIMUS_TEXT_TOKEN benötigt 32–256 ASCII-Zeichen ohne Leerzeichen.')
        self.token = token
        self.backend = backend
        self.inference = threading.BoundedSemaphore(1)
        self.connections = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ProximusText/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # no prompts, credentials, request paths or personal text in access logs

    def send_json(self, code, data):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self):
        supplied = self.headers.get('Authorization', '')
        expected = 'Bearer ' + self.server.token
        if not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('ascii')):
            self.send_json(401, {'error': 'Nicht autorisiert.'})
            return False
        return True

    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'ok': True})
        elif not self.authorized():
            return
        elif self.path == '/v1/status':
            self.send_json(200, {'ok': True, 'mode': 'text', 'backend': 'not_probed'})
        else:
            self.send_json(404, {'error': 'Unbekannter Pfad.'})

    def do_POST(self):
        if not self.authorized():
            return
        if self.path != '/v1/text':
            self.send_json(404, {'error': 'Unbekannter Pfad.'})
            return
        try:
            lengths = self.headers.get_all('Content-Length') or []
            if len(lengths) != 1 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Content-Length erforderlich.')
            length = int(lengths[0])
            if not 1 <= length <= 8192:
                self.send_json(413, {'error': 'Nachricht zu groß oder leer.'})
                return
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('JSON erforderlich.')
            data = json.loads(self.rfile.read(length))
            if (not isinstance(data, dict) or set(data) != {'text'} or
                    not isinstance(data['text'], str) or not 1 <= len(data['text'].strip()) <= 2000):
                raise ValueError('Genau ein Textfeld mit 1–2000 Zeichen erforderlich.')
        except (ValueError, UnicodeError, TimeoutError):
            self.send_json(400, {'error': 'Ungültige Textanfrage.'})
            return
        if not self.server.inference.acquire(blocking=False):
            self.send_json(503, {'error': 'Textdienst belegt; später erneut senden.'})
            return
        try:
            reply = self.server.backend(data['text'].strip())
            if not isinstance(reply, str) or not reply.strip() or len(reply) > 8000:
                raise ValueError('Ungültige Backendantwort.')
            self.send_json(200, {'mode': 'text', 'reply': reply})
        except Exception:
            self.send_json(502, {'error': 'Textkern nicht erreichbar. Lokale Module bleiben verfügbar.'})
        finally:
            self.server.inference.release()


def make_backend(url, model):
    url = validate_url(url)

    def complete(text):
        data = json_request(url + '/v1/chat/completions', payload={
            'model': model, 'messages': [{'role': 'system', 'content': PROMPT},
                                        {'role': 'user', 'content': text}],
            'max_tokens': 512, 'temperature': 0.4, 'stream': False}, timeout=15)
        return data['choices'][0]['message']['content']
    return complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8770)
    args = parser.parse_args()
    backend = make_backend(os.environ.get('PROXIMUS_LLM_URL', 'http://127.0.0.1:8766'),
                           os.environ.get('PROXIMUS_LLM_MODEL', 'local'))
    # Always loopback. Expose only through an authenticated TLS proxy or SSH tunnel.
    with Server(('127.0.0.1', args.port), os.environ.get('PROXIMUS_TEXT_TOKEN', ''), backend) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
