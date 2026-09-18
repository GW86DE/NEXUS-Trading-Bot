from __future__ import annotations
import json, threading
from pathlib import Path
from datetime import datetime, timezone
from safe_persistence import best_effort_json

PATH=Path(__file__).resolve().parent/'automation_status.json'
LOCK=threading.RLock()
def _now(): return datetime.now(timezone.utc).isoformat()
def load():
    try: return json.loads(PATH.read_text(encoding='utf-8')) if PATH.exists() else {}
    except Exception: return {}
def mark(name,status='ok',detail='',**extra):
    with LOCK:
        d=load(); d[name]={'time':_now(),'status':status,'detail':detail,**extra}
        return best_effort_json(PATH,d,label='Automation-Status')
def entry(name): return load().get(name,{})
