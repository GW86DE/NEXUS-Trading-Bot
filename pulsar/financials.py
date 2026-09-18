"""Choose one financial-report source; do not double count SEC and FMP."""
from datetime import datetime, timezone
import math
from .evidence import valid_financials


def annual_valid(facts, symbol, observed, now):
    try:
        if (facts.get('schema_version') not in {'FMP-ANNUAL-1', 'FMP-ANNUAL-2'} or facts.get('available') is not True
            or facts.get('symbol')!=symbol or facts.get('symbol_identity_verified') is not True
            or int(facts.get('cik') or 0)<=0 or facts.get('period')!='FY' or facts.get('currency')!='USD'
            or facts.get('source_family')!='ISSUER_FINANCIAL_REPORT' or not 0<=now-float(observed)<=2*86400):return False
        latest=facts['latest']
        end=datetime.fromisoformat(latest['end']).replace(tzinfo=timezone.utc).timestamp()
        filed=datetime.fromisoformat(latest['filed']).replace(tzinfo=timezone.utc).timestamp()
        return (latest.get('period')=='FY' and latest.get('currency')=='USD' and end<=filed<=now
                and now-end<=550*86400 and now-filed<=550*86400
                and all(type(latest[k]) in (float,int) and math.isfinite(latest[k]) for k in ('net_income','operating_cashflow')))
    except (KeyError,TypeError,ValueError,OverflowError):return False


def choose_financials(sources,symbol,now,sec):
    sec_ok,reason=valid_financials(sec,symbol,now)
    annual=[s['data'] for s in sources if s.get('provider')=='FMP' and s.get('kind')=='annual_financials'
            and annual_valid(s.get('data') or {},symbol,s.get('observed_at'),now)]
    fmp=annual[0] if len(annual)==1 else None
    if fmp and sec_ok:
        if int(fmp['cik'])!=int(sec['cik']):return None,'Finanzbelege: FMP und SEC widersprechen sich bei der Unternehmenskennung'
        latest=fmp['latest'];sec_profit=sec['net_income'];sec_cash=sec['operating_cashflow']
        # Only compare like-for-like annual periods, never a quarter with FY.
        duration=(datetime.fromisoformat(sec_profit['end'])-datetime.fromisoformat(sec_profit['start'])).days
        if sec_profit['end']==latest['end'] and 330<=duration<=371:
            if any(not math.isclose(a,b,rel_tol=.005,abs_tol=1.) for a,b in
                    ((latest['net_income'],sec_profit['value']),(latest['operating_cashflow'],sec_cash['value']))):
                return None,'Finanzbelege: FMP und SEC widersprechen sich in derselben Jahresperiode'
    if sec_ok:
        return {'profit':sec['net_income']['value'],'cashflow':sec['operating_cashflow']['value']},reason + ('; FMP-Jahresdaten als Kontext, keine Doppelwertung' if fmp else '')
    if fmp:
        r=fmp['latest']
        from fmp_service import record_use
        record_use('PULSAR_FMP_FINANZWERTUNG',symbol,r)
        return {'profit':r['net_income'],'cashflow':r['operating_cashflow']},'FMP: gepruefte USD-Jahresbelege bis '+r['end']+'; Jahresdaten, keine Quartalsaktualitaet'
    return None,reason+'; passende aktuelle FMP-Jahresbelege ebenfalls nicht verfuegbar'
