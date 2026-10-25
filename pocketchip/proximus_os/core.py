"""Bounded local utilities and private persistent notes; no audio or shell eval."""
import ast
import datetime
import decimal
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

MAX_NOTES = 80
MAX_NOTE = 4000
MAX_STORE = 400_000


def read_private(path, limit=MAX_STORE):
    """Reject symlinks, other owners and group/world-readable credentials/data."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Datei muss dem Benutzer gehören und Modus 0600 haben.')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Datei überschreitet das Größenlimit.')
        return json.loads(data)


class Archive:
    def __init__(self, directory):
        self.directory = Path(directory)
        if self.directory.is_symlink():
            raise ValueError('Archivverzeichnis darf kein Symlink sein.')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Archivverzeichnis benötigt Modus 0700 und eigenen Benutzer.')
        self.path = self.directory / 'notes.json'

    def notes(self):
        try:
            data = read_private(self.path)
        except FileNotFoundError:
            return []
        if (not isinstance(data, list) or len(data) > MAX_NOTES or any(
                not isinstance(n, dict) or set(n) != {'time', 'text'} or
                not isinstance(n['time'], str) or len(n['time']) > 40 or
                not isinstance(n['text'], str) or len(n['text']) > MAX_NOTE for n in data)):
            raise ValueError('Archiv ist beschädigt; Originaldatei bleibt erhalten.')
        return data

    def add(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_NOTE:
            raise ValueError(f'Notiz benötigt 1–{MAX_NOTE} Zeichen.')
        notes = self.notes()
        if len(notes) >= MAX_NOTES:
            raise ValueError('Archiv voll. Vor weiteren Notizen extern sichern und aufräumen.')
        notes.append({'time': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                      'text': text.strip()})
        raw = json.dumps(notes, ensure_ascii=False).encode('utf-8')
        if len(raw) > MAX_STORE:
            raise ValueError('Archiv voll.')
        fd, temporary = tempfile.mkstemp(prefix='.notes-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def calculate(expression):
    if len(expression) > 80:
        raise ValueError('Rechnung zu lang.')
    tree = ast.parse(expression.replace(',', '.'), mode='eval')
    if len(list(ast.walk(tree))) > 32:
        raise ValueError('Rechnung zu komplex.')

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = decimal.Decimal(str(node.value))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            else:
                result = left / right
        else:
            raise ValueError('Nur Zahlen, Klammern und + − * / sind erlaubt.')
        if not result.is_finite() or abs(result) > decimal.Decimal('1e12'):
            raise ValueError('Zahl außerhalb des Wertebereichs.')
        return result

    with decimal.localcontext() as context:
        context.prec = 18
        return format(evaluate(tree.body).normalize(), 'f')


def read_value(path):
    try:
        return Path(path).read_text(encoding='utf-8').strip()[:100]
    except OSError:
        return 'unbekannt'


def system_status(sysroot=Path('/sys'), procroot=Path('/proc')):
    batteries = []
    for item in sorted((sysroot / 'class/power_supply').glob('*')):
        if read_value(item / 'type') == 'Battery':
            capacity = read_value(item / 'capacity')
            if capacity.isdigit() and 0 <= int(capacity) <= 100:
                capacity += ' %'
            else:
                capacity = 'unbekannt'
            batteries.append(f'Akku: {capacity} · {read_value(item / "status")}')
    network = ', '.join(f'{p.name}: {read_value(p / "operstate")}'
                        for p in sorted((sysroot / 'class/net').glob('*')) if p.name != 'lo')
    memory = read_value(procroot / 'meminfo')
    # meminfo can exceed 100 characters; report only total/available from a full read.
    try:
        entries = (procroot / 'meminfo').read_text().splitlines()
        memory = ' · '.join(line for line in entries if line.startswith(('MemTotal:', 'MemAvailable:')))
    except OSError:
        pass
    return '\n'.join(batteries + ([] if batteries else ['Akku: unbekannt (keine Messwerte)']) +
                     [f'Netz: {network or "unbekannt"}', memory,
                      'NAND/PMIC: Hardwareprüfung ausstehend'])


def local_command(text):
    command = text.strip().lower()
    if command in ('hilfe', ''):
        return 'Lokal: uhr, datum, status, rechne (12 + 3) / 2. Keine freien Systembefehle.'
    if command == 'uhr':
        return datetime.datetime.now().astimezone().strftime('%H:%M:%S %Z')
    if command == 'datum':
        return datetime.datetime.now().astimezone().strftime('%d.%m.%Y')
    if command == 'status':
        return system_status()
    if command.startswith('rechne '):
        try:
            return calculate(command[7:].strip())
        except (ValueError, SyntaxError, decimal.DecimalException):
            return 'Ungültige Rechnung. Erlaubt: Zahlen, Klammern, + − * / (bis 10¹²).'
    return 'Unbekannter lokaler Befehl. „hilfe“ zeigt die Möglichkeiten.'


def wifi_status():
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device'],
                                capture_output=True, text=True, timeout=3, check=False)
        return (result.stdout[:2000].strip() if result.returncode == 0 else
                'NetworkManager nicht verfügbar.')
    except (OSError, subprocess.TimeoutExpired):
        return 'WLAN-Status nicht verfügbar (offline nutzbar).'
