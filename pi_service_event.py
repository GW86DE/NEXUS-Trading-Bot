"""Persistiert das Ergebnis des letzten systemd-Service-Laufs fuer Neustartdiagnose."""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from safe_persistence import best_effort_json

ROOT=Path(__file__).resolve().parent
PATH=ROOT/'last_service_exit.json'

def record(result='', exit_code='', exit_status=''):
    best_effort_json(PATH, {
        'service_result': str(result or ''),
        'exit_code': str(exit_code or ''),
        'exit_status': str(exit_status or ''),
        'recorded_at': datetime.now(timezone.utc).isoformat(),
    }, label='systemd-exit')

def consume_problem():
    try:
        if not PATH.exists(): return None
        d=json.loads(PATH.read_text(encoding='utf-8'))
        PATH.unlink(missing_ok=True)
        result=str(d.get('service_result','')).lower()
        if result in {'success',''}: return None
        return d
    except Exception:
        return None

if __name__=='__main__':
    record(*(sys.argv[1:4]+['','',''])[:3])
