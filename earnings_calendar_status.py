"""Future earnings dates: explicit availability, shared cache, no inferred dates."""
from datetime import datetime, timezone
from pathlib import Path
import csv
import io
import json
import os
import time
import requests
from provider_safety import redact
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock


def _path():
    return Path(os.getenv('TRADINGBOT_TEST_STATE_DIR') or Path(__file__).parent) / 'earnings_calendar_status.json'


def _read():
    try:
        data = json.loads(_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def status():
    from live_settings import alpha_vantage_key
    row = _read()
    if not alpha_vantage_key():
        return {'available': False, 'state': 'disabled',
                'detail': 'Zukunftskalender nicht eingerichtet. SEC liefert bereits veroeffentlichte Zahlen, keine kommenden Termine.'}
    age = time.time() - float(row.get('checked_epoch') or 0)
    fresh = 0 <= age <= float(row.get('ttl_seconds') or 43200)
    return {'available': bool(row.get('ok') and fresh),
            'state': 'ok' if row.get('ok') and fresh else 'error' if row and not row.get('ok') else 'unknown',
            'last_checked_at': row.get('last_checked_at', ''),
            'detail': redact(row.get('detail') or 'Kommende Termine noch nicht geprueft.')}


def calendar(key, horizon='3month'):
    if not key:
        return []
    import config
    ttl = max(60, float(getattr(config, 'EARNINGS_CALENDAR_CACHE_HOURS', 12)) * 3600)
    with critical_state_lock(_path(), timeout_seconds=3):
        row = _read()
        age = time.time() - float(row.get('checked_epoch') or 0)
        if row.get('horizon') == horizon and 0 <= age < (ttl if row.get('ok') else 1800):
            return row.get('rows', []) if row.get('ok') else []
        result = {'last_checked_at': datetime.now(timezone.utc).isoformat(),
                  'checked_epoch': time.time(), 'horizon': horizon, 'ttl_seconds': ttl}
        try:
            r = requests.get('https://www.alphavantage.co/query',
                params={'function': 'EARNINGS_CALENDAR', 'horizon': horizon, 'apikey': key}, timeout=10)
            r.raise_for_status()
            text = r.text.lstrip('\ufeff \r\n')
            if text.startswith(('{', '[')):
                raise ValueError('Anbieter liefert eine Status-/Fehlermeldung statt Kalenderdaten: ' + text[:300])
            reader = csv.DictReader(io.StringIO(text))
            if not {'symbol', 'reportDate'}.issubset(set(reader.fieldnames or [])):
                raise ValueError('Erwartete Kalenderfelder fehlen')
            rows = list(reader)
            if any(not x.get('symbol') or not x.get('reportDate') for x in rows):
                raise ValueError('Unvollstaendige Kalenderzeile')
            for item in rows:
                datetime.strptime(item['reportDate'], '%Y-%m-%d')
            result.update(ok=True, rows=rows, detail=f'Kalender erfolgreich geprueft: {len(rows)} Termine.')
        except Exception as exc:
            result.update(ok=False, rows=[], detail='Zukunftskalender nicht verfuegbar: ' + redact(exc, [key]))
        atomic_write_json(_path(), result)
        return result['rows']
