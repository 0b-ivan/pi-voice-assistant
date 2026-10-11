#!/usr/bin/env python3
"""Small same-origin web gateway for the existing Servitor server.

The browser never receives SERVITOR_API_TOKEN. Expose this gateway only
behind an authenticated reverse proxy (e.g. Cloudflare Access). By default
it listens on loopback; it has no user authentication of its own.
"""
import http.client
import http.server
import json
import os
from pathlib import Path
import urllib.parse

ROOT = Path(__file__).resolve().parent
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
MAX_REQUEST_BYTES = 8192
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "connect-src 'self'; img-src 'self'; media-src 'self' blob:; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


class Gateway(http.server.BaseHTTPRequestHandler):
    server_version = 'ProximusWeb/1'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def log_message(self, fmt, *args):
        # Never log user-supplied chat bodies or tokens.
        super().log_message(fmt, *args)

    def _headers(self, kind, length=None):
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        if length is not None:
            self.send_header('Content-Length', str(length))

    def _json(self, code, payload):
        data = (json.dumps(payload) + '\n').encode('utf-8')
        self.send_response(code)
        self._headers('application/json; charset=utf-8', len(data))
        self.end_headers()
        self.wfile.write(data)

    def _host_allowed(self):
        host = self.headers.get('Host', '').split(':', 1)[0].lower().rstrip('.')
        return host in self.server.allowed_hosts

    def do_GET(self):
        if not self._host_allowed():
            return self._json(403, {'error': 'unknown host'})
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/health':
            try:
                upstream = http.client.HTTPConnection(self.server.upstream_host,
                                                       self.server.upstream_port, timeout=5)
                try:
                    upstream.request('GET', '/health')
                    response = upstream.getresponse()
                    data = response.read(4096)
                    self.send_response(response.status)
                    self._headers('application/json; charset=utf-8', len(data))
                    self.end_headers()
                    self.wfile.write(data)
                finally:
                    upstream.close()
            except (OSError, TimeoutError):
                self._json(502, {'ok': False, 'ready': False})
            return
        if path not in STATIC:
            return self._json(404, {'error': 'not found'})
        filename, mime = STATIC[path]
        content = (ROOT / filename).read_bytes()
        self.send_response(200)
        self._headers(mime, len(content))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        if not self._host_allowed():
            return self._json(403, {'error': 'unknown host'})
        if self.path != '/api/chat':
            return self._json(404, {'error': 'not found'})
        # Cross-origin requests must not use an Access-authenticated session.
        origin = self.headers.get('Origin', '')
        parsed = urllib.parse.urlsplit(origin)
        if (self.headers.get('Sec-Fetch-Site') == 'cross-site' or
                parsed.scheme not in ('https', 'http') or
                parsed.netloc.lower() != self.headers.get('Host', '').lower()):
            return self._json(403, {'error': 'invalid origin'})
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self._json(415, {'error': 'application/json required'})
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self._json(411, {'error': 'length required'})
        if not 0 < length <= MAX_REQUEST_BYTES:
            return self._json(413, {'error': 'request too large'})
        try:
            data = json.loads(self.rfile.read(length))
            message = data['text']
        except (ValueError, KeyError, TypeError):
            return self._json(400, {'error': 'invalid JSON chat request'})
        if not isinstance(message, str) or not message.strip() or len(message) > 1200:
            return self._json(400, {'error': 'text must contain 1-1200 characters'})
        payload = json.dumps({'text': message.strip()}, ensure_ascii=False).encode('utf-8')
        connection = http.client.HTTPConnection(self.server.upstream_host,
                                                self.server.upstream_port, timeout=90)
        try:
            connection.request('POST', '/v1/chat?format=wav', body=payload,
                               headers={'Authorization': 'Bearer ' + self.server.api_token,
                                        'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.send_response(response.status)
            self._headers('application/x-ndjson' if response.status == 200
                          else 'application/json; charset=utf-8')
            self.end_headers()
            while True:
                part = response.read(16384)
                if not part:
                    break
                self.wfile.write(part)
        except (OSError, TimeoutError):
            # Network failure before response headers: reply with 502. After
            # streaming began, close instead (headers cannot be resent).
            if not self.wfile.closed and not getattr(self, '_headers_sent', False):
                try:
                    self._json(502, {'error': 'Proximus server unavailable'})
                except OSError:
                    pass
        finally:
            connection.close()


def make_server(env=None):
    env = os.environ if env is None else env
    token = env.get('SERVITOR_API_TOKEN', '').strip()
    if len(token) < 32:
        raise ValueError('SERVITOR_API_TOKEN must contain at least 32 characters')
    bind = env.get('PROXIMUS_WEB_BIND', '127.0.0.1')
    port = int(env.get('PROXIMUS_WEB_PORT', '8780'))
    server = http.server.ThreadingHTTPServer((bind, port), Gateway)
    server.daemon_threads = True
    server.api_token = token
    server.upstream_host = env.get('PROXIMUS_UPSTREAM_HOST', '127.0.0.1')
    server.upstream_port = int(env.get('PROXIMUS_UPSTREAM_PORT', '8765'))
    server.allowed_hosts = {v.strip().lower() for v in
                            env.get('PROXIMUS_WEB_HOSTS', 'localhost,127.0.0.1').split(',')
                            if v.strip()}
    return server


if __name__ == '__main__':
    with make_server() as httpd:
        print(f'Proximus web gateway on {httpd.server_address}', flush=True)
        httpd.serve_forever()
