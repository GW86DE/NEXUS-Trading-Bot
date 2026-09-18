"""Shared cache for public, keyless feeds; serialises competing Core/UI reads."""
import base64
import hashlib
import json
import time
import requests
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock


def get(session, url, *, root, ttl, timeout, params=None, headers=None):
    key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
    path = root / ('public_news_' + key + '.json')
    with critical_state_lock(path, timeout_seconds=min(15, timeout + 2)):
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            row = {}
        age = time.time() - float(row.get('time', 0))
        if 0 <= age < max(60, ttl) and row.get('body'):
            r = requests.Response()
            r.status_code = 200
            r._content = base64.b64decode(row['body'], validate=True)
            r.headers.update(row.get('headers', {}))
            r.url = url
            return r
        r = session.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        if len(r.content) > 2 * 1024 * 1024:
            raise ValueError('Oeffentlicher Feed ueberschreitet Groessenlimit')
        atomic_write_json(path, {'time': time.time(), 'body': base64.b64encode(r.content).decode(),
                                'headers': {k: v for k, v in r.headers.items()
                                            if k.lower() in {'date', 'age', 'content-type'}}}, durable=False)
        return r
