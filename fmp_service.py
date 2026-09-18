"""Shared FMP transport, persistent entitlements, caches and account-wide limits.

Plan is a user declaration, never inferred from one successful endpoint. AUTO
uses that declaration and reduces to Free after two distinct paid capabilities
are refused. Endpoint refusals are independent of transient transport failures.
No credentials, broker receipts or execution prices are stored here.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from contextvars import ContextVar
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

import requests
from state_lock import critical_state_lock

CONSUMER = ContextVar('fmp_consumer', default='nexus')


@contextmanager
def consumer(name):
    token = CONSUMER.set(name)
    try: yield
    finally: CONSUMER.reset(token)


BASE = 'https://financialmodelingprep.com/stable'
# (capability, cache seconds); no unsupported intraday/calendar probes.
ENDPOINTS = {
    '/search-symbol': ('reference', 86400), '/quote': ('quote', 900),
    '/profile': ('reference', 86400), '/historical-price-eod/full': ('history', 3600),
    '/income-statement': ('annual_income', 86400),
    '/balance-sheet-statement': ('annual_balance', 86400),
    '/cash-flow-statement': ('annual_cashflow', 86400),
    '/ratios': ('annual_ratios', 86400), '/key-metrics': ('annual_metrics', 86400),
    '/news/stock': ('news', 900), '/news/stock-latest': ('news', 900),
    '/news/general-latest': ('news', 1800), '/news/crypto': ('crypto_news', 1800),
    '/news/crypto-latest': ('crypto_news', 1800), '/news/forex': ('forex_news', 1800),
    '/news/forex-latest': ('forex_news', 1800),
    '/news/press-releases': ('press_releases', 3600),
    '/news/press-releases-latest': ('press_releases', 3600),
}
FREE = {'reference', 'quote', 'history'}
MAX_BYTES = 8 * 1024 * 1024


class FMPTarifFehlt(RuntimeError):
    pass


class FMPPaused(RuntimeError):
    pass


def root():
    return Path(os.getenv('TRADINGBOT_TEST_STATE_DIR', '').strip() or Path(__file__).resolve().parent)


def settings():
    import live_settings
    raw = live_settings.lies('news_sources_credentials.json')
    mode = str(raw.get('fmp_plan', 'AUTO')).upper()
    declared = str(raw.get('fmp_subscription', 'FREE')).upper()
    return (mode if mode in {'AUTO', 'FREE', 'STARTER'} else 'FREE',
            declared if declared in {'FREE', 'STARTER'} else 'FREE')


def encoded(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, api_key='', base=BASE, daily_limit=0):
        self.path = root() / 'fmp_service.sqlite'
        self.base = base
        self.scope = hashlib.sha256((base+'\0'+api_key).encode()).hexdigest()
        self.daily_limit = min(250, max(0, int(daily_limit)))

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        self.path.chmod(0o600)
        con.row_factory = sqlite3.Row
        try:
            con.execute('PRAGMA synchronous=FULL')
            con.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, at REAL NOT NULL,
              automatic INTEGER NOT NULL, origin TEXT NOT NULL, capability TEXT NOT NULL,
              bytes INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS fmp_call_at ON calls(at);
            CREATE TABLE IF NOT EXISTS capabilities(scope TEXT, path TEXT, ok INTEGER,
              checked REAL, retry REAL, detail TEXT, PRIMARY KEY(scope,path));
            CREATE TABLE IF NOT EXISTS cache(scope TEXT, key TEXT, saved REAL, expires REAL,
              payload TEXT, PRIMARY KEY(scope,key));
            CREATE TABLE IF NOT EXISTS metrics(day TEXT, name TEXT, value INTEGER,
              PRIMARY KEY(day,name));
            CREATE TABLE IF NOT EXISTS evidence_uses(kind TEXT, symbol TEXT, hash TEXT, at REAL, facts TEXT,
              PRIMARY KEY(kind,symbol,hash));
            ''')
            con.execute('BEGIN IMMEDIATE')
            if not con.execute("SELECT 1 FROM meta WHERE key='legacy_import'").fetchone():
                # Import once. A key/tariff switch never resets usage. Keep a
                # legacy local-day remainder through both possible day ends.
                p = root()/'api_daily_budgets.json'; old = None
                if p.exists():
                    with critical_state_lock(p):
                        raw = json.loads(p.read_text(encoding='utf-8'))
                    old = (raw.get('budgets') or {}).get('fmp')
                now = time.time(); utc = datetime.fromtimestamp(now, timezone.utc)
                local = datetime.fromtimestamp(now).astimezone()
                tomorrow = utc.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
                local_end = local.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
                valid = bool(old and old.get('day') in {utc.date().isoformat(),local.date().isoformat()})
                legacy = {'used': max(0,int(old.get('used',0))) if valid else 0,
                    'auto': max(0,int(old.get('automatic_used',0))) if valid else (80 if old is None and utc.hour>=3 else 0),
                    'until': max(tomorrow.timestamp(),local_end.timestamp()),
                    'blocked': float(old.get('blocked_until',0)) if valid else 0}
                con.execute('INSERT INTO meta VALUES(?,?)', ('legacy_import',encoded(legacy)))
            yield con
            con.commit()
        except BaseException:
            con.rollback(); raise
        finally:
            con.close()

    @staticmethod
    def _meta(con, key, default=None):
        row=con.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _put(con,key,value):
        con.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,encoded(value)))

    def _policy(self, con, now):
        mode, declared = settings()
        plan = 'STARTER' if mode=='STARTER' or mode=='AUTO' and declared=='STARTER' else 'FREE'
        reason = 'Tarifvorgabe des Nutzers; Datenberechtigungen werden einzeln geprueft'
        quota = self._meta(con,'free_quota_until',0)
        if plan=='STARTER':
            rows=con.execute('SELECT path,ok,checked FROM capabilities WHERE scope=? AND checked>?',
                             (self.scope,now-86400)).fetchall()
            refused={ENDPOINTS[r['path']][0] for r in rows if not r['ok'] and r['path'] in ENDPOINTS and ENDPOINTS[r['path']][0] not in FREE}
            latest_no=max((r['checked'] for r in rows if not r['ok']),default=0)
            latest_yes=max((r['checked'] for r in rows if r['ok'] and r['path'] in ENDPOINTS and ENDPOINTS[r['path']][0] not in FREE),default=0)
            if (mode=='AUTO' and {'news','annual_income'} <= refused and latest_no>=latest_yes) or quota>now:
                plan='FREE'
                reason='Free-Rueckfall: mehrere Zusatzberechtigungen abgelehnt' if quota<=now else 'FMP meldet ein Tageskontingent; Free-Grenze aktiv'
        return {'mode':mode,'declared':declared,'effective':plan,'reason':reason,
                'limit': (self.daily_limit or min(250,max(1,__import__('live_settings').fmp_tageslimit()))) if plan=='FREE' else 0,
                'auto_limit':min(80,max(1,int(getattr(__import__('config'),'FMP_AUTOMATIC_DAILY_LIMIT',80)))) if plan=='FREE' else 0,
                'minute_limit':30 if plan=='FREE' else 300,
                'bandwidth_limit':500_000_000 if plan=='FREE' else 20_000_000_000}

    def _status(self, con, now):
        p=self._policy(con,now)
        start=datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        daily=con.execute('SELECT COUNT(*),COALESCE(SUM(automatic),0) FROM calls WHERE at>=?',(start,)).fetchone()
        minute=con.execute('SELECT COUNT(*) FROM calls WHERE at>?',(now-60,)).fetchone()[0]
        bandwidth=con.execute('SELECT COALESCE(SUM(bytes),0) FROM calls WHERE at>?',(now-30*86400,)).fetchone()[0]
        legacy=self._meta(con,'legacy_import',{})
        used,auto=daily
        if legacy.get('until',0)>now:
            used+=legacy.get('used',0);auto+=legacy.get('auto',0)
        block=self._meta(con,'block',{})
        until=max(block.get('until',0),legacy.get('blocked',0))
        return {**p,'verbraucht':used,'automatic_used':auto,'minute_used':minute,
            'bandwidth_bytes':bandwidth,'rest':max(0,p['limit']-used) if p['limit'] else None,
            'automatic_limit':p['auto_limit'],'gesperrt':until>now,
            'sperrgrund':block.get('reason','') if until>now else '',
            'blocked_until':until,'tag':datetime.fromtimestamp(now,timezone.utc).date().isoformat(),
            'reset_zone':'UTC','bandwidth_scope':'NEXUS/PULSAR seit Einfuehrung; externe Abrufe nicht messbar'}

    @staticmethod
    def _allowed(s,purpose):
        if s['gesperrt']:return False,s['sperrgrund'] or 'FMP in Abrufpause'
        if s['limit'] and s['verbraucht']>=s['limit']:return False,'FMP-Tagesbudget aufgebraucht'
        if purpose not in {'position','manual'} and s['automatic_limit'] and s['automatic_used']>=s['automatic_limit']:
            return False,'FMP-Automatikreserve erreicht; Reserve fuer Positionen/Diagnose'
        if s['minute_used']>=s['minute_limit']:return False,'FMP-Minutenlimit erreicht'
        if s['bandwidth_bytes']+MAX_BYTES>s['bandwidth_limit']:return False,'FMP-Datenvolumen fuer 30 Tage ausgeschöpft'
        return True,''

    def reserve(self, purpose, capability, origin):
        now=time.time()
        with self.db() as con:
            allowed,reason=self._allowed(self._status(con,now),purpose)
            if not allowed:raise FMPPaused('FMP pausiert: '+reason)
            # A bounded number of in-flight calls prevents bursts/large Pi memory use.
            if con.execute('SELECT COUNT(*) FROM calls WHERE done=0 AND at>?',(now-60,)).fetchone()[0]>=2:
                raise FMPPaused('FMP: zwei Abrufe laufen bereits')
            row=con.execute('INSERT INTO calls(at,automatic,origin,capability,bytes) VALUES(?,?,?,?,?)',
                            (now,int(purpose not in {'position','manual'}),origin,capability,MAX_BYTES))
            return row.lastrowid

    def finish(self, token, size=None):
        with self.db() as con:
            con.execute('UPDATE calls SET done=1,bytes=COALESCE(?,bytes) WHERE id=?', (size,token))

    def mark(self, path, ok, detail, retry=0):
        with self.db() as con:
            con.execute('INSERT INTO capabilities VALUES(?,?,?,?,?,?) ON CONFLICT(scope,path) DO UPDATE SET '
                        'ok=excluded.ok,checked=excluded.checked,retry=excluded.retry,detail=excluded.detail',
                        (self.scope,path,int(ok),time.time(),retry,detail))

    def capability(self,path):
        with self.db() as con:
            row=con.execute('SELECT * FROM capabilities WHERE scope=? AND path=?',(self.scope,path)).fetchone()
            return dict(row) if row else {}

    def permits(self,path,*,probe=False):
        if path not in ENDPOINTS:return False
        cap=ENDPOINTS[path][0]
        mode,declared=settings()
        # Direct manual diagnostics may explicitly test a paid capability under AUTO.
        if cap not in FREE and (mode=='FREE' or mode=='AUTO' and declared!='STARTER' and not probe):return False
        row=self.capability(path)
        return not row or bool(row['ok']) or row['retry']<=time.time()

    def cached(self,key,*,stale=False):
        with self.db() as con:
            row=con.execute('SELECT * FROM cache WHERE scope=? AND key=?',(self.scope,key)).fetchone()
            if row and (stale or row['expires']>time.time()):
                return {'saved':row['saved'],'expires':row['expires'],'data':json.loads(row['payload'])}
        return None

    def save(self,key,payload,ttl,*,saved=None):
        stamp=time.time() if saved is None else saved
        with self.db() as con:
            con.execute('INSERT INTO cache VALUES(?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET '
                        'saved=excluded.saved,expires=excluded.expires,payload=excluded.payload',
                        (self.scope,key,stamp,stamp+ttl,encoded(payload)))

    def metric(self,name,value=1):
        with self.db() as con:
            day=datetime.now(timezone.utc).date().isoformat()
            con.execute('INSERT INTO metrics VALUES(?,?,?) ON CONFLICT(day,name) DO UPDATE SET value=value+excluded.value',
                        (day,name,int(value)))

    def record_use(self,kind,symbol,facts):
        payload=encoded(facts)
        digest=hashlib.sha256(payload.encode()).hexdigest()
        with self.db() as con:
            con.execute('INSERT OR IGNORE INTO evidence_uses VALUES(?,?,?,?,?)',
                        (kind,str(symbol),digest,time.time(),payload))

    def block(self,seconds,reason):
        with self.db() as con:
            old=self._meta(con,'block',{})
            self._put(con,'block',{'until':max(old.get('until',0),time.time()+seconds),'reason':reason})

    def status(self):
        with self.db() as con:
            s=self._status(con,time.time())
            s['capabilities']=[{k:r[k] for k in ('path','ok','checked','retry','detail')} for r in con.execute(
                'SELECT * FROM capabilities WHERE scope=? ORDER BY path',(self.scope,))]
            s['last_success']=con.execute('SELECT MAX(checked) FROM capabilities WHERE scope=? AND ok=1',(self.scope,)).fetchone()[0]
            s['month_usage']=[dict(r) for r in con.execute('SELECT origin,capability,COUNT(*) calls,SUM(bytes) bytes FROM calls WHERE at>? GROUP BY origin,capability',(time.time()-30*86400,))]
            s['month_evidence_uses']={r[0]:r[1] for r in con.execute('SELECT kind,COUNT(*) FROM evidence_uses WHERE at>? GROUP BY kind',(time.time()-30*86400,))}
            s['month_metrics']={r[0]:r[1] for r in con.execute('SELECT name,SUM(value) FROM metrics WHERE day>=? GROUP BY name',((datetime.now(timezone.utc)-timedelta(days=30)).date().isoformat(),))}
            return s

    def maintain(self):
        with self.db() as con:
            con.execute('DELETE FROM calls WHERE at<?',(time.time()-31*86400,))
            con.execute('DELETE FROM cache WHERE expires<? AND key NOT LIKE ?', (time.time()-35*86400,'history:%'))
            con.execute('DELETE FROM evidence_uses WHERE at<?',(time.time()-400*86400,))
            con.execute('DELETE FROM metrics WHERE day<?',((datetime.now(timezone.utc)-timedelta(days=400)).date().isoformat(),))


class Budget:
    """Compatibility facade; all callers use the same SQLite reservation."""
    def __init__(self,store):self.store=store
    @property
    def limit(self):return self.als_dict()['limit']
    @property
    def verbraucht(self):return self.als_dict()['verbraucht']
    def als_dict(self):return self.store.status()
    def frei(self,*,purpose='automatic'):
        with self.store.db() as con:return self.store._allowed(self.store._status(con,time.time()),purpose)
    def sperren(self,sekunden,grund):self.store.block(sekunden,grund)


def _retry(response,default=60):
    value=getattr(response,'headers',{}).get('Retry-After','')
    try:seconds=float(value)
    except (TypeError,ValueError):
        try:seconds=parsedate_to_datetime(value).timestamp()-time.time()
        except (TypeError,ValueError,OverflowError):seconds=default
    return max(1,min(86400,seconds)) if math.isfinite(seconds) else default


def request(store, session, key, path, params=None, *, timeout=12, purpose='automatic', origin=None, cache=True):
    if not key:raise RuntimeError('FMP API-Key fehlt')
    query=dict(params or {})
    if 'apikey' in query or path not in ENDPOINTS:raise FMPTarifFehlt('FMP: Datenart im Free-/Starter-Profil nicht freigegeben')
    cachekey=encoded([path,query])
    lock=store.path.with_name('fmp_fetch_'+hashlib.sha256((store.scope+cachekey).encode()).hexdigest()+'.lock')
    with critical_state_lock(lock,timeout_seconds=15):
        # Check plan BEFORE cache: Free does not quietly keep using paid news.
        if not store.permits(path,probe=purpose=='manual'):
            raise FMPTarifFehlt('FMP '+path+': im Tarif nicht verfuegbar oder Berechtigungspruefung pausiert')
        hit=store.cached(cachekey) if cache else None
        if hit:
            store.metric('cache_hits');return hit['data']
        cap,ttl=ENDPOINTS[path]
        token=store.reserve(purpose,cap,origin or CONSUMER.get())
        size=None;response=None
        try:
            # Header authentication prevents keys appearing in URLs and errors.
            started=time.monotonic()
            response=session.get(store.base+path,params=query,headers={'apikey':key},
                                 timeout=min(20,max(1,timeout)),stream=True,allow_redirects=False)
            if hasattr(response,'iter_content'):
                body=bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if time.monotonic()-started>max(1,timeout):
                        raise requests.Timeout('FMP wall timeout')
                    body.extend(chunk)
                    if len(body)>MAX_BYTES:
                        size=len(body)
                        raise RuntimeError('FMP-Antwort groesser als erlaubte 8 MiB')
                size=len(body)
                try:payload=json.loads(body)
                except (ValueError,UnicodeError):payload=None
            else:
                body=getattr(response,'content',b'')
                size=len(body)
                if size>MAX_BYTES:raise RuntimeError('FMP-Antwort groesser als erlaubte 8 MiB')
                try:payload=response.json()
                except ValueError:payload=None
            # Never persist or expose server-supplied error text (may echo keys).
            message=str((payload.get('Error Message') or payload.get('error') or payload.get('message') or '') if isinstance(payload,dict) else '').lower()
            status=int(response.status_code)
            if status in {403,429} and 'bandwidth' in message:
                store.block(max(3600,_retry(response,86400)),'FMP-Datenvolumen beim Anbieter ausgeschöpft; vorhandener Cache bleibt nutzbar')
                raise FMPPaused('FMP: Datenvolumen beim Anbieter ausgeschöpft')
            if status==401 or status==403 and ('invalid api' in message or 'invalid key' in message):
                store.block(3600,'FMP-Zugang abgelehnt; Schluessel pruefen')
                raise RuntimeError('FMP HTTP '+str(status)+': Zugang abgelehnt')
            if status in {402,403} or ('premium' in message or 'subscription' in message) and status==200:
                store.mark(path,False,'Berechtigung abgelehnt (HTTP '+str(status)+')',time.time()+86400)
                store.metric('entitlement_refusals')
                raise FMPTarifFehlt('FMP '+path+': im Tarif nicht enthalten (HTTP '+str(status)+')')
            if status==429:
                seconds=_retry(response)
                if '250' in message and ('day' in message or 'daily' in message):
                    seconds=max(seconds,3600)
                    with store.db() as con:store._put(con,'free_quota_until',time.time()+86400)
                store.block(seconds,'FMP HTTP 429: Abruflimit erreicht; erneuter Versuch nach Pause')
                raise FMPPaused('FMP HTTP 429: Abruflimit erreicht')
            if status>=500:
                store.block(_retry(response,120),'FMP voruebergehend nicht erreichbar')
            if status>=300 or message:raise RuntimeError('FMP: API-Antwort abgelehnt (HTTP '+str(status)+')')
            if not isinstance(payload,(dict,list)):raise RuntimeError('FMP-Antwort nicht lesbar')
            # JSON doubles as validation against NaN/Infinity before persistence.
            encoded(payload)
            store.mark(path,True,'Abruf erfolgreich')
            if cache:store.save(cachekey,payload,ttl)
            store.metric('network_successes')
            return payload
        except requests.RequestException as exc:
            store.block(60,'FMP voruebergehend nicht erreichbar')
            raise RuntimeError('FMP nicht erreichbar ('+type(exc).__name__+')') from None
        finally:
            store.finish(token,size)
            if response is not None and hasattr(response,'close'):response.close()


def record_use(kind,symbol,facts):
    """Optional effectiveness log; a reporting failure never changes a decision."""
    try:
        from fmp_reference import client
        ref=client()
        if ref.konfiguriert:ref.store.record_use(kind,symbol,facts)
    except Exception as exc:
        __import__('logging').getLogger(__name__).info('FMP-Nutzungsnachweis nicht gespeichert (%s)',type(exc).__name__)
