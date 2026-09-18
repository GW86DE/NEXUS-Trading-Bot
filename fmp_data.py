"""Typed FMP research facts. Dates, currency and identity remain explicit."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import math
import time

ANNUAL_SCHEMA = 'FMP-ANNUAL-2'


def annual_context(facts):
    """Versioned research view; old derived trends must be revalidated first."""
    if facts.get('schema_version') != ANNUAL_SCHEMA:
        return {'available': False, 'schema_version': ANNUAL_SCHEMA,
                'reason': 'Jahresableitungen warten auf erneute Validierung',
                'errors': ['DERIVED_SCHEMA_REVALIDATION_REQUIRED']}
    return {k: facts.get(k) for k in ('schema_version', 'symbol', 'available',
        'currency', 'latest', 'trends', 'trend_period', 'metrics', 'reason', 'errors', 'status')}


def number(value):
    if isinstance(value,bool):return None
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None


def day(value):
    return datetime.fromisoformat(str(value).replace('Z','+00:00')).date()


def history(client,symbol,count,*,purpose='automatic'):
    symbol=str(symbol).strip().upper()
    today=datetime.now(ZoneInfo('America/New_York')).date()
    key='history:'+symbol
    # One symbol lock also coalesces NEXUS's 20-bar and PULSAR's 95-bar requests.
    from state_lock import critical_state_lock
    import hashlib
    lock=client.store.path.with_name('fmp_history_'+hashlib.sha256((client.store.scope+symbol).encode()).hexdigest()+'.lock')
    with critical_state_lock(lock):
        prior=client.store.cached(key,stale=True)
        data=(prior or {}).get('data',{})
        rows=data.get('rows',[])
        paid=client.starter()
        full=not rows or paid and (not data.get('five_years') or time.time()-data.get('rebuilt',0)>90*86400)
        if (prior and prior['expires']>time.time() and not full):
            client.store.metric('history_cache_hits')
            return rows[:max(1,int(count))]
        start=today-timedelta(days=1826 if paid else max(150,min(730,int(count)*2)))
        if not full and rows:
            start=max(start,day(rows[0]['date'])-timedelta(days=14))
        params={'symbol':symbol,'from':start.isoformat(),'to':today.isoformat()}
        raw=client._get('/historical-price-eod/full',params,purpose=purpose)
        raw=raw if isinstance(raw,list) else raw.get('historical',[])
        valid={}
        for r in raw:
            if not isinstance(r,dict) or r.get('symbol',symbol)!=symbol:continue
            try:date=day(r['date'])
            except (ValueError,KeyError,TypeError):continue
            values=[number(r.get(k)) for k in ('open','high','low','close','volume')]
            if any(v is None for v in values):continue
            o,h,l,c,v=values
            if not (date<today and min(o,h,l,c)>0 and v>=0 and h>=max(o,l,c) and l<=min(o,c)):continue
            valid[date.isoformat()]={**r,'date':date.isoformat(),'symbol':symbol}
        if not valid:raise RuntimeError('FMP: keine abgeschlossenen passenden OHLCV-Tageskerzen')
        from market_calendar import ist_handelstag
        expected=today-timedelta(days=1)
        while not ist_handelstag(expected)[0]:expected-=timedelta(days=1)
        if max(valid)<expected.isoformat():
            raise RuntimeError('FMP: letzte abgeschlossene US-Handelssitzung fehlt')
        # A newly applied split must not mix rebased overlapping bars with an
        # older price scale. Rebuild before making such a series usable.
        previous={r['date']:r for r in rows}
        adjusted=any(k in previous and not math.isclose(float(v['close']),float(previous[k]['close']),rel_tol=1e-6) for k,v in valid.items())
        if adjusted and not full:
            client.store.save(key,{**data,'rebuilt':0,'five_years':False,'rows':[]},0)
            raise RuntimeError('FMP: korrigierte historische Kurse; vollstaendiger Neuaufbau im naechsten Lauf')
        merged={} if full else previous
        merged.update(valid)
        cutoff=(today-timedelta(days=1827)).isoformat()
        result=sorted([r for d,r in merged.items() if d>=cutoff],key=lambda r:r['date'],reverse=True)
        # End-of-day values change once a day. An incomplete current session is
        # excluded; next day's first lookup then fetches its completed bar.
        expiry=datetime.combine(today+timedelta(days=1),datetime.min.time(),ZoneInfo('America/New_York')).timestamp()
        client.store.save(key,{'rows':result,'five_years':paid if full else data.get('five_years',False),
            'rebuilt':time.time() if full else data.get('rebuilt',0)},max(60,expiry-time.time()))
        client.store.metric('history_updates')
        return result[:max(1,int(count))]


def daily_metrics(rows):
    today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    rows=sorted([r for r in rows if isinstance(r,dict) and str(r.get('date',''))<today and number(r.get('close')) and number(r.get('volume')) is not None],key=lambda r:r['date'])
    if len(rows)<20:return {'available':False,'reason':'Weniger als 20 abgeschlossene Tageskerzen'}
    closes=[float(r['close']) for r in rows];vol=[float(r['volume']) for r in rows]
    spans=[]
    for p,r in zip(rows[-15:-1],rows[-14:]):
        h,l=number(r.get('high')),number(r.get('low'))
        if h is not None and l is not None and h>=l:spans.append(max(h-l,abs(h-float(p['close'])),abs(l-float(p['close']))))
    changes=[b-a for a,b in zip(closes[-15:-1],closes[-14:])]
    gain=sum(max(c,0) for c in changes);loss=sum(max(-c,0) for c in changes)
    return {'available':True,'as_of':rows[-1]['date'],'basis':'DAILY_COMPLETED',
        'bars':len(rows),'sma20':sum(closes[-20:])/20,'sma200':sum(closes[-200:])/200 if len(rows)>=200 else None,
        'atr14':sum(spans)/14 if len(spans)==14 else None,
        'rsi14_simple':100-100/(1+gain/loss) if loss else (100 if gain else 50),
        'relative_volume':vol[-1]/(sum(vol[-21:-1])/20) if len(vol)>=21 and sum(vol[-21:-1])>0 else None,
        'drawdown_252':closes[-1]/max(closes[-252:])-1 if len(closes)>=252 else None}


def _identity(row,profile,symbol):
    try:
        return (isinstance(row,dict) and row.get('symbol')==symbol and profile.get('symbol')==symbol
            and int(profile.get('cik') or 0)>0 and int(row.get('cik') or 0)==int(profile['cik'])
            and row.get('reportedCurrency')==profile.get('currency')=='USD'
            and row.get('period')=='FY')
    except (ValueError,TypeError):return False


def normalize_financials(symbol,profile,responses,*,now=None):
    now=time.time() if now is None else now
    today=datetime.fromtimestamp(now,timezone.utc).date()
    matched={};errors=[]
    for name in ('income','cashflow','balance'):
        grouped={}
        for row in responses.get(name,[]):
            if not _identity(row,profile,symbol):continue
            try:
                end=day(row['date']); filed=day(row.get('filingDate') or row.get('fillingDate') or row.get('acceptedDate'))
                if not end<=filed<=today or not 0<=(today-end).days<=6*366:continue
            except (ValueError,KeyError,TypeError):continue
            k=(end.isoformat(),str(row.get('fiscalYear') or row.get('calendarYear') or ''))
            if not k[1]:continue
            # Ambiguous duplicate periods are not quietly selected.
            if k in grouped and grouped[k]!=row:grouped[k]=None
            elif k not in grouped:grouped[k]=row
        matched[name]=grouped
    years=[]
    for key in sorted(set(matched['income']) & set(matched['cashflow']) & set(matched['balance']),reverse=True):
        i,c,b=[matched[n][key] for n in ('income','cashflow','balance')]
        if not all((i,c,b)):continue
        profit,cash=number(i.get('netIncome')),number(c.get('operatingCashFlow'))
        if profit is None or cash is None:continue
        # Cross-check the same net income in the cashflow statement when supplied.
        cashprofit=number(c.get('netIncome'))
        if cashprofit is not None and not math.isclose(profit,cashprofit,rel_tol=0.005,abs_tol=1):
            errors.append('Widerspruch beim Jahresgewinn '+key[0]);continue
        years.append({'end':key[0],'fiscal_year':key[1],'period':'FY','currency':'USD',
            'filed':max(str(r.get('filingDate') or r.get('fillingDate') or r.get('acceptedDate'))[:10] for r in (i,c,b)),
            'net_income':profit,'operating_cashflow':cash,'revenue':number(i.get('revenue')),
            'free_cashflow':number(c.get('freeCashFlow')),'total_debt':number(b.get('totalDebt')),
            'cash':number(b.get('cashAndCashEquivalents')),'diluted_shares':number(i.get('weightedAverageShsOutDil')),
            'equity':number(b.get('totalStockholdersEquity'))})
    latest=years[0] if years else {}
    incomplete_newer=bool(latest and any(key[0]>latest['end'] for key in matched['income']))
    if incomplete_newer:errors.append('Neuere Jahresperiode vorhanden, aber noch nicht vollstaendig widerspruchsfrei belegt')
    fresh=bool(latest and not incomplete_newer and 0<=(today-day(latest['end'])).days<=550 and 0<=(today-day(latest['filed'])).days<=550)
    metrics=[]
    for name in ('ratios','metrics'):
        for row in responses.get(name,[]):
            # FMP ratio endpoints can omit CIK: identity is anchored by the
            # three statements, but symbol/currency/FY/date must still match.
            if (not isinstance(row,dict) or not latest or row.get('symbol')!=symbol
                    or row.get('period')!='FY' or row.get('date')!=latest['end']
                    or row.get('reportedCurrency') not in (None, 'USD')):continue
            values={k:number(row.get(k)) for k in
                ('grossProfitMargin','operatingProfitMargin','netProfitMargin','debtToEquityRatio','currentRatio','returnOnEquity','returnOnAssets','freeCashFlowYield') if k in row}
            aliases={'ev_to_ebitda': ('evToEBITDA', 'enterpriseValueMultiple', 'enterpriseValueOverEBITDA'),
                'net_debt_to_ebitda': ('netDebtToEBITDA',), 'interest_coverage': ('interestCoverageRatio',),
                'quick_ratio': ('quickRatio',), 'roic': ('returnOnInvestedCapital',),
                'cashflow_conversion': ('freeCashFlowOperatingCashFlowRatio',)}
            for label, fields in aliases.items():
                supplied=[number(row[f]) for f in fields if f in row]
                if not supplied:continue
                good=[v for v in supplied if v is not None]
                if len(good)!=len(supplied) or not good or any(not math.isclose(good[0], v, rel_tol=1e-6, abs_tol=1e-9) for v in good[1:]):
                    values[label]=None
                    errors.append('METRIC_ALIAS_CONFLICT:'+label)
                else:
                    # Negative valuation multiples are not useful as a cheapness signal.
                    values[label]=good[0] if label != 'ev_to_ebitda' or good[0]>0 else None
            metrics.append({'kind':name,'date':row['date'],'period':'FY','currency':'USD',
                'basis':'HISTORICAL_ANNUAL_NOT_CURRENT_VALUATION','values':values})
    trends={}
    trend_period={'from': None, 'to': latest.get('end'), 'comparable': False,
                  'reason': 'PREVIOUS_COMPARABLE_YEAR_MISSING'}
    if len(years)>1:
        recent,previous=years[:2]
        elapsed=(day(recent['end'])-day(previous['end'])).days
        try: consecutive=int(recent['fiscal_year'])-int(previous['fiscal_year'])==1
        except (ValueError,TypeError): consecutive=False
        comparable=consecutive and 330<=elapsed<=400
        trend_period={'from':previous['end'],'to':recent['end'],'days':elapsed,
            'comparable':comparable,'reason':'CONSECUTIVE_FISCAL_YEARS' if comparable else 'PERIOD_GAP'}
        if not comparable:errors.append('PERIOD_GAP:'+previous['end']+'..'+recent['end'])
        if comparable:
            for field in ('revenue','operating_cashflow','diluted_shares'):
                new,old=recent.get(field),previous.get(field)
                if new is not None and old is not None and old>0:trends[field+'_change']=new/old-1
    return {'schema_version':ANNUAL_SCHEMA,'symbol':symbol,'cik':profile.get('cik'),
        'symbol_identity_verified':bool(years),'available':fresh,'period':'FY','currency':'USD',
        'latest':latest,'years':years[:5],'trends':trends,'trend_period':trend_period,'metrics':metrics,'errors':errors,
        'reason':'FMP-Jahresbelege mit gemeinsamer USD-Periode' if fresh else 'Passende aktuelle FMP-Jahresabschluesse fehlen',
        'source_family':'ISSUER_FINANCIAL_REPORT','annual_only':True}


def financials(client,symbol,*,purpose='automatic'):
    profile=client.profil(symbol,purpose=purpose)
    if profile.get('symbol')==symbol and (profile.get('isEtf') is True or profile.get('isFund') is True):
        return {'schema_version':ANNUAL_SCHEMA,'symbol':symbol,'available':False,
            'reason':'NOT_APPLICABLE: ETF/Fonds hat keine Unternehmensjahresabschluesse',
            'status':'NOT_APPLICABLE','errors':[],'latest':{},'years':[],'trends':{},'metrics':[]}
    responses={};errors=[]
    for name,path in (('income','/income-statement'),('cashflow','/cash-flow-statement'),('balance','/balance-sheet-statement'),('ratios','/ratios'),('metrics','/key-metrics')):
        if not client.erlaubt(path):continue
        try:
            rows=client._get(path,{'symbol':symbol,'period':'annual','limit':5},purpose=purpose)
            responses[name]=rows if isinstance(rows,list) else []
        except RuntimeError as exc:errors.append(str(exc))
    result=normalize_financials(symbol,profile,responses)
    result['errors'].extend(errors)
    hits=[client.cached_source(p, {'symbol':symbol,'period':'annual','limit':5}) for p in
        ('/income-statement','/cash-flow-statement','/balance-sheet-statement')]
    if all(hits):
        stamp=min(h['saved'] for h in hits)
        client.store.save('annual:'+symbol,result,86400,saved=stamp)
    return result


def research_context(symbol):
    """Cache-only context for an existing NEXUS GPT review; never extra calls."""
    import fmp_reference
    from fmp_service import settings
    client=fmp_reference.client()
    if not client.konfiguriert:return {}
    symbol=str(symbol).strip().upper()
    out={'role':'OPTIONAL_RESEARCH_ONLY','detail':'FMP-Referenzdaten; keine Brokerkurse oder Ausfuehrungs-/Buchungsbelege'}
    history=client.store.cached('history:'+symbol)
    if history:out['daily']=daily_metrics(history['data'].get('rows',[]))
    mode,declared=settings()
    if mode=='STARTER' or mode=='AUTO' and declared=='STARTER':
        annual=client.store.cached('annual:'+symbol)
        if annual:
            out['annual']=annual_context(annual['data'])
            out['annual']['fetched_at']=annual['saved']
        macro=client.store.cached('market_context')
        if macro:out['market_context']=macro['data']
    return out


def refresh_market_context(client, *, now=None):
    """Hourly secondary FX/crypto context; no rates enter accounting."""
    now=time.time() if now is None else float(now)
    if not client.starter():return
    hit=client.store.cached('market_context',stale=True)
    if hit and hit['expires']>now:return hit['expires']
    from fmp_service import consumer
    result={'role':'REFERENCE_ONLY','quotes':[],'news':[],'errors':[],
            'observed_at':now,'next_refresh_at':now+3600,'components':{}}
    with consumer('market_context'):
        for symbol in ('EURUSD','BTCUSD'):
            try:
                row=client.quote(symbol)
                # The quote may be timestamped while HTTP is in flight.  Check
                # freshness at receipt; ``now`` above only anchors scheduling.
                received_at=time.time()
                price=number(row.get('price'));stamp=number(row.get('timestamp'))
                if price is None or price<=0 or stamp is None or not 0<=received_at-stamp<=900:
                    raise RuntimeError('Aktueller datierter Referenzkurs fehlt')
                result['quotes'].append({'symbol':symbol,'price':price,'timestamp':stamp,'provider':'FMP'})
                result['components'][symbol]={'status':'OK','data_at':stamp}
            except RuntimeError as exc:
                result['errors'].append(symbol+': '+str(exc))
                result['components'][symbol]={'status':'UNAVAILABLE','reason':str(exc)}
        for market in ('crypto','forex'):
            path='/news/'+market+'-latest'
            if not client.erlaubt(path):continue
            try:
                rows=client.nachrichten(market=market)
                for row in rows[:5]:
                    try:
                        dt=datetime.fromisoformat(str(row.get('publishedDate') or '').replace('Z','+00:00'))
                        if dt.tzinfo is None:dt=dt.replace(tzinfo=ZoneInfo('America/New_York'))
                        if not 0<=time.time()-dt.timestamp()<=48*3600:continue
                    except (TypeError,ValueError):continue
                    result['news'].append({k:row.get(k) for k in ('symbol','title','url','publishedDate')})
            except RuntimeError as exc:result['errors'].append(str(exc))
    client.store.save('market_context',result,3600,saved=now)
    return now+3600
