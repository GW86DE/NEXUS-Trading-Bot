"""Stock readiness: exchange time, actual quote proof and signal history differ."""
from datetime import datetime, timezone
import math
import time
import pandas as pd
from broker.history_safety import bar_seconds
from market_session import market_session_status


def market(ready, status, *, regular_only=True):
    """Pure calendar observation; closed never means authenticated account banned."""
    data={k:(v.isoformat() if isinstance(v,datetime) else v) for k,v in status.items()}
    ready.market_status=data
    if regular_only:
        ready.melde('marktsitzung', status.get('offen') is True,
            'US-Regelhandel '+str(status.get('phase') or 'unbekannt')+
            '; naechste Oeffnung '+str(data.get('naechste_oeffnung') or 'unbekannt'))
        ready.bedingungen['marktsitzung'].beschreibung='US-Regelhandel geoeffnet'


def quote(ready, session):
    age=session.quote_age_seconds
    limit=session.quote_max_age_seconds
    valid=(session.quote_fresh is True and type(age) in (int,float) and type(limit) in (int,float)
           and limit > 0
           and math.isfinite(float(age)) and math.isfinite(float(limit))
           and 0 <= float(age) <= float(limit))
    ready.melde('kursdaten',valid,
        f'{session.symbol}: Kursalter {age} s; geprueft {session.checked_at_utc}',
        gueltig_fuer=max(0.001,float(limit)-float(age)) if valid else None)


def probe(ready,broker,universe,*,now=None):
    """At most one actual rates probe/minute, independently of BUY signals."""
    stamp=time.monotonic() if now is None else float(now)
    if stamp-getattr(ready,'_quote_probe_at',-1e9)<60:
        return
    ready._quote_probe_at=stamp
    if getattr(ready,'market_status',{}).get('offen') is not True:
        return  # No expected quote updates outside the configured RTH scan.
    sample=next((i for i in universe if getattr(i,'asset_type','')=='stock'),None)
    if sample is None:
        ready.melde('kursdaten',False,'Kein aufloesbares Aktieninstrument fuer Kurspruefung')
        return
    try:
        import config
        session=market_session_status(broker,sample,
            quote_max_age_seconds=float(getattr(config,'MARKET_SESSION_QUOTE_MAX_AGE_SECONDS',180)))
        quote(ready,session)
    except Exception as exc:
        ready.melde('kursdaten',False,'Kurspruefung fehlgeschlagen: '+type(exc).__name__)


def history_fresh(df,bar_size,*,now=None):
    """Do not turn thirty old rows into current signal evidence."""
    if df is None or len(df)<30 or 'close' not in df:
        return False,'Mindestens 30 verwertbare Kerzen fehlen'
    seconds=bar_seconds(bar_size)
    if not seconds or seconds<=0:
        return False,'Kerzendauer nicht eindeutig'
    try:
        if (not isinstance(df.index,pd.DatetimeIndex) or df.index.tz is None
                or not df.index.is_monotonic_increasing or not df.index.is_unique):
            return False,'Kerzenzeit oder Zeitzone fehlt'
        end=df.index[-1].tz_convert('UTC')+pd.Timedelta(seconds=seconds)
        stamp=pd.Timestamp(now or datetime.now(timezone.utc))
        if stamp.tzinfo is None:
            return False,'Pruefuhr ohne Zeitzone'
        age=(stamp.tz_convert('UTC')-end).total_seconds()
        prices=pd.to_numeric(df['close'],errors='coerce')
        if not all(math.isfinite(float(p)) and p>0 for p in prices):
            return False,'Ungueltige Schlusskurse in der Signalhistorie'
        price=float(df['close'].iloc[-1])
        valid=math.isfinite(price) and price>0 and 0<=age<=seconds+180
        return valid,f'Letzter Kerzenschluss {end.isoformat()}; Alter {age:.0f} s'
    except (ValueError,TypeError,OverflowError):
        return False,'Kerzenfrische nicht pruefbar'
