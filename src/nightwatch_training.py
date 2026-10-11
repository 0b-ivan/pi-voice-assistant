"""Local dataset planning and review gates; never train or activate a model."""
import hashlib
import json
import math
from pathlib import PurePath
import re


def dataset_plan(records):
    """Split by recording session before augmentation; identical PCM is unique.
    Session hashing remains stable when new recordings are added. Unknown
    sessions cannot serve as evidence of an independent validation/test set."""
    unique, duplicates = {}, 0
    for record in records:
        digest = record.get('sha256', '')
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('invalid sample hash')
        if digest in unique:
            duplicates += 1
            continue
        name = record.get('file', '')
        if not isinstance(name, str) or PurePath(name).name != name or not name.endswith('.wav'):
            raise ValueError('invalid sample file')
        match = re.fullmatch(r'(\d{8}-\d{6})-\d{2}\.wav', name)
        session = match[1] if match else 'unknown'
        bucket = int(hashlib.sha256(session.encode()).hexdigest()[:8], 16) % 10
        split = ('train' if bucket < 6 else 'validation' if bucket < 8 else 'test') \
            if match else 'unassigned'
        unique[digest] = dict(record, session=session, split=split)
    rows = sorted(unique.values(), key=lambda row: row['sha256'])
    counts = {split: sum(row['split'] == split for row in rows)
              for split in ('train', 'validation', 'test', 'unassigned')}
    sessions = len({row['session'] for row in rows if row['session'] != 'unknown'})
    ready = len(rows) >= 20 and counts['train'] >= 10 and counts['validation'] >= 5 \
        and counts['test'] >= 5 and sessions >= 3
    identity = [dict(sha256=row['sha256'], session=row['session'], split=row['split']) for row in rows]
    return dict(version=1, valid=len(rows), duplicates=duplicates, sessions=sessions,
                split_counts=counts, positive_split_ready=ready, manifest=rows,
                dataset_id=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
                limits=['Positive Aufnahmen allein belegen keine Fehlaktivierungsrate.',
                        'Aufnahmesitzung trennt Daten, ersetzt aber keine verschiedenen Räume und Tage.'])


def assess_candidate(baseline, candidate, plan):
    """Check measured, independently produced reports, not model-generated claims.
    Passing permits human review only. No file is promoted to the running model."""
    reasons = []
    test = sorted(row['sha256'] for row in plan.get('manifest', []) if row.get('split') == 'test')
    identity = plan.get('dataset_id')
    for label, report in (('Bestandsmodell', baseline), ('Kandidat', candidate)):
        if not isinstance(report, dict) or report.get('dataset_id') != identity \
                or report.get('test_sha256') != test or not test \
                or report.get('measured_on') != 'pi-zero-2-w' \
                or report.get('selection_used_test') is not False:
            reasons.append(f'{label}: gleicher unabhängiger Testbestand und Pi-Messung nicht belegt')
            continue
        if type(report.get('positive_count')) is not int or report['positive_count'] != len(test) \
                or type(report.get('false_activations')) is not int or report['false_activations'] < 0:
            reasons.append(f'{label}: Anzahl der Testfälle ungültig')
        hits = report.get('true_positives')
        recall = report.get('recall')
        if type(hits) is not int or not 0 <= hits <= len(test) or type(recall) not in (int, float) \
                or not math.isclose(recall, hits / len(test), abs_tol=0.0005):
            reasons.append(f'{label}: Trefferquote passt nicht zur Zahl tatsächlich erkannter Clips')
        for key, low, high in (('recall', 0, 1), ('negative_hours', 10, 100000),
                              ('latency_p95_ms', 0.01, 80), ('threshold', 0, 1)):
            value = report.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                reasons.append(f'{label}: {key} fehlt oder liegt außerhalb der Prüfgrenze')
        if not re.fullmatch('[a-f0-9]{64}', str(report.get('model_sha256', ''))):
            reasons.append(f'{label}: Modellhash fehlt')
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity) \
            or len(test) < 30 or not plan.get('positive_split_ready'):
        reasons.append('Mindestens 30 zurückgehaltene Positivclips und ein gültiger Datensplit nötig')
    if not reasons:
        if baseline.get('negative_dataset_sha256') != candidate.get('negative_dataset_sha256') \
                or not re.fullmatch('[a-f0-9]{64}', str(baseline.get('negative_dataset_sha256', ''))) \
                or baseline['negative_hours'] != candidate['negative_hours']:
            reasons.append('Negativ-Testbestand muss identisch und nachgewiesen sein')
        old_rate = baseline['false_activations'] / baseline['negative_hours']
        new_rate = candidate['false_activations'] / candidate['negative_hours']
        if baseline['model_sha256'] == candidate['model_sha256']:
            reasons.append('Kandidat und Bestandsmodell sind dieselbe Datei')
        if candidate['recall'] < 0.9 or candidate['recall'] < baseline['recall'] - 0.01:
            reasons.append('Trefferquote zu niedrig oder gegenüber dem Bestand verschlechtert')
        if new_rate > 0.5 or new_rate > old_rate:
            reasons.append('Fehlaktivierungen zu häufig oder gegenüber dem Bestand verschlechtert')
        if candidate['latency_p95_ms'] > baseline['latency_p95_ms'] * 1.1:
            reasons.append('Pi-Latenz um mehr als zehn Prozent verschlechtert')
        if candidate['recall'] < baseline['recall'] + 0.02 and new_rate > old_rate - 0.05:
            reasons.append('Kein ausreichend messbarer Vorteil im Vergleich')
    return dict(review_ready=not reasons, activated=False, reasons=reasons,
                limits=['Prüft Messberichte; führt selbst keine ONNX-Messung aus.',
                        'Kleine Testmengen beweisen keine allgemeine Verbesserung.',
                        'Aktivierung erfordert getrennte Freigabe und Rückfallmöglichkeit.'])
