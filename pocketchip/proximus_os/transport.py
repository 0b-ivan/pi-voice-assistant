"""Private HTTPS text protocol; never fall back to the voice endpoint."""
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from .core import read_private

MAX_RESPONSE = 32768


def validate_url(url):
    if not isinstance(url, str) or any(c.isspace() for c in url):
        raise ValueError('Ungültige Serveradresse.')
    parsed = urllib.parse.urlsplit(url)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or
            parsed.fragment or parsed.path not in ('', '/') or
            parsed.scheme not in ('https', 'http')):
        raise ValueError('Nur eine Server-Basisadresse ohne Pfad ist erlaubt.')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('HTTP nur über lokalen Tunnel; im Netz HTTPS verwenden.')
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError('Ungültiger Port.') from exc
    return url.rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Serverumleitung abgelehnt.')


def json_request(url, token='', payload=None, timeout=15):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if raw is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=raw, headers=headers)
    # Disable environment proxies: a private LAN token must not follow arbitrary proxy settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        if response.headers.get_content_type() != 'application/json':
            raise ValueError('Server liefert kein JSON.')
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise ValueError('Serverantwort zu groß.')
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError('Ungültige Serverantwort.')
        return data


@dataclass(frozen=True)
class ClientConfig:
    url: str
    token: str

    @classmethod
    def load(cls, path):
        data = read_private(Path(path), limit=8192)
        if not isinstance(data, dict) or set(data) != {'url', 'token'}:
            raise ValueError('Konfiguration benötigt genau url und token.')
        token = data['token']
        if (not isinstance(token, str) or not 32 <= len(token) <= 256 or
                any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise ValueError('Token benötigt 32–256 druckbare ASCII-Zeichen ohne Leerzeichen.')
        return cls(validate_url(data['url']), token)


class TextClient:
    def __init__(self, config):
        self.config = config

    def ask(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise ValueError('Nachricht benötigt 1–2000 Zeichen.')
        answer = json_request(self.config.url + '/v1/text', self.config.token,
                              {'text': text.strip()}, timeout=20)
        if (answer.get('mode') != 'text' or not isinstance(answer.get('reply'), str) or
                not 1 <= len(answer['reply']) <= 8000 or 'audio' in answer):
            raise ValueError('Server unterstützt keinen gültigen Textmodus.')
        return answer['reply']

    def status(self):
        answer = json_request(self.config.url + '/v1/status', self.config.token, timeout=4)
        if answer.get('mode') != 'text' or answer.get('ok') is not True:
            raise ValueError('Textdienst nicht bereit.')
        return 'CT107-Textdienst erreichbar. Backend-Inferenz erst bei einer Nachricht geprüft.'
