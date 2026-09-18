"""Quartalszahlen-Intelligence auf Basis der Alpha-Vantage-Earnings-API.

Optional: Ohne API-Key bleibt der bestehende Bot voll funktionsfaehig, nur die
Earnings-Sonderlogik ist deaktiviert. Der Key kann ueber alpha_vantage_setup.py
lokal gespeichert werden.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,date,timedelta,timezone
from pathlib import Path
import csv, io, json, os, time, requests
import config
from sec_fundamentals import SecFundamentals

@dataclass
class EarningsSnapshot:
    symbol:str; checked:bool=False; report_date:str=""; fiscal_end:str=""; reported_eps:float|None=None
    estimated_eps:float|None=None; surprise_pct:float|None=None; yoy_eps_growth_pct:float|None=None
    revenue:float|None=None; estimated_revenue:float|None=None; revenue_surprise_pct:float|None=None
    yoy_revenue_growth_pct:float|None=None
    gross_margin_pct:float|None=None; operating_margin_pct:float|None=None
    yoy_gross_margin_delta_pp:float|None=None; free_cash_flow:float|None=None
    fcf_margin_pct:float|None=None; yoy_fcf_growth_pct:float|None=None
    score:int=0; grade:str="NO_DATA"; reason:str=""; recent:bool=False

class EarningsClient:
    BASE="https://www.alphavantage.co/query"
    def __init__(self):
        from live_settings import alpha_vantage_key
        self.key=alpha_vantage_key()
        self.sec=SecFundamentals()
        self.cache={}; self.calendar_cache=(0,[])

    @staticmethod
    def _f(v):
        try:
            if v in (None,"","None","null"): return None
            return float(v)
        except Exception:return None

    def enabled(self): return bool(self.key or self.sec.enabled())


    def _sec_snapshot(self, symbol):
        """Kostenloser, neutraler Quartals-/Filing-Fallback aus SEC Company Facts.

        SEC liefert harte berichtete Fakten, aber keine Analystenschaetzungen.
        Daher wird absichtlich KEIN Beat/Miss erfunden und der Score bleibt
        neutral. Die Daten sind trotzdem fuer Eventkontext und GPT-Fakten wertvoll.
        """
        if not self.sec.enabled():
            return EarningsSnapshot(symbol, reason="Kein Alpha-Vantage-Key; SEC Kontakt-E-Mail fehlt")
        try:
            d=self.sec.snapshot(symbol)
            if not d.get("available"):
                return EarningsSnapshot(symbol, checked=True, reason="SEC: "+str(d.get("reason","keine Daten")))
            rev=d.get("revenue") or {}; eps=d.get("eps_diluted") or {}; ni=d.get("net_income") or {}; ocf=d.get("operating_cashflow") or {}
            filed=str((rev or eps or ni or ocf).get("filed") or "")
            fiscal=str((rev or eps or ni or ocf).get("end") or "")
            recent=False
            try:
                drift=max(3,int(getattr(config,"EARNINGS_POST_DRIFT_DAYS",3)))
                age=(date.today()-date.fromisoformat(filed[:10])).days
                recent=0 <= age <= drift
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            def val(row):
                try:return float(row.get("value")) if row and row.get("value") is not None else None
                except Exception:return None
            reason=(f"SEC Company Facts · Filing {filed or 'n/a'} · "
                    f"Umsatz {val(rev) if rev else 'n/a'} · Nettoergebnis {val(ni) if ni else 'n/a'} · "
                    f"EPS {val(eps) if eps else 'n/a'} · keine Analysten-Surprise ohne optionale Datenquelle")
            return EarningsSnapshot(symbol=symbol, checked=True, report_date=filed, fiscal_end=fiscal,
                                    reported_eps=val(eps), revenue=val(rev), score=50, grade="SEC_NEUTRAL",
                                    reason=reason, recent=recent)
        except Exception as exc:
            return EarningsSnapshot(symbol, False, reason=__import__("provider_safety").redact(f"SEC-Fallback fehlgeschlagen: {exc}"))

    def calendar(self,horizon="3month"):
        from earnings_calendar_status import calendar
        return calendar(self.key, horizon)

    def days_to_earnings(self,symbol):
        try:
            today=date.today(); dates=[]
            for row in self.calendar():
                if str(row.get("symbol","")).upper()!=symbol.upper():continue
                raw=row.get("reportDate") or row.get("report_date") or row.get("date")
                if not raw:continue
                d=date.fromisoformat(raw[:10]);
                if d>=today:dates.append((d-today).days)
            return min(dates) if dates else None
        except Exception:return None

    def history(self,symbol):
        if not self.key:return {}
        key=(symbol.upper(),"hist"); now=time.time()
        cached=self.cache.get(key)
        if cached and now-cached[0]<float(getattr(config,"EARNINGS_HISTORY_CACHE_HOURS",12))*3600:return cached[1]
        r=requests.get(self.BASE,params={"function":"EARNINGS","symbol":symbol,"apikey":self.key},timeout=20)
        r.raise_for_status(); data=r.json(); self.cache[key]=(now,data); return data

    def income_statement(self,symbol):
        """Quartalsumsatz fuer echte YoY-Umsatzentwicklung (optional).

        Alpha Vantage liefert mit INCOME_STATEMENT quarterlyReports. Der Call
        wird genauso gecacht wie Earnings, damit die externe API nicht bei
        jedem Scan belastet wird. Fehler werden vom Snapshot abgefangen.
        """
        if not self.key:return {}
        key=(symbol.upper(),"income"); now=time.time()
        cached=self.cache.get(key)
        if cached and now-cached[0]<float(getattr(config,"EARNINGS_HISTORY_CACHE_HOURS",12))*3600:return cached[1]
        r=requests.get(self.BASE,params={"function":"INCOME_STATEMENT","symbol":symbol,"apikey":self.key},timeout=20)
        r.raise_for_status(); data=r.json(); self.cache[key]=(now,data); return data

    def cash_flow(self,symbol):
        """Quartals-Cashflow fuer Free-Cash-Flow-Qualitaet (optional)."""
        if not self.key:return {}
        key=(symbol.upper(),"cashflow"); now=time.time()
        cached=self.cache.get(key)
        if cached and now-cached[0]<float(getattr(config,"EARNINGS_HISTORY_CACHE_HOURS",12))*3600:return cached[1]
        r=requests.get(self.BASE,params={"function":"CASH_FLOW","symbol":symbol,"apikey":self.key},timeout=20)
        r.raise_for_status(); data=r.json(); self.cache[key]=(now,data); return data

    def estimates(self,symbol):
        """Analysten-EPS/Umsatzschaetzungen (optional, je API-Plan verfuegbar).

        Der Alpha-Vantage-Endpunkt EARNINGS_ESTIMATES liefert laut offizieller
        Dokumentation quartalsweise EPS- und Umsatzschaetzungen samt Analysten-
        Anzahl/Revisionen. Da Feldnamen zwischen API-Versionen variieren
        koennen, wertet ``_find_revenue_estimate`` mehrere gaengige Varianten
        defensiv aus. Fehlen Daten, bleibt Revenue Surprise einfach ``None``.
        """
        if not self.key:return {}
        key=(symbol.upper(),"estimates"); now=time.time()
        cached=self.cache.get(key)
        if cached and now-cached[0]<float(getattr(config,"EARNINGS_HISTORY_CACHE_HOURS",12))*3600:return cached[1]
        r=requests.get(self.BASE,params={"function":"EARNINGS_ESTIMATES","symbol":symbol,"apikey":self.key},timeout=20)
        r.raise_for_status(); data=r.json(); self.cache[key]=(now,data); return data

    @staticmethod
    def _iter_dicts(obj):
        if isinstance(obj,dict):
            yield obj
            for v in obj.values():
                yield from EarningsClient._iter_dicts(v)
        elif isinstance(obj,list):
            for v in obj:
                yield from EarningsClient._iter_dicts(v)

    def _find_revenue_estimate(self,data,fiscal_end=""):
        revenue_keys=(
            "revenue_estimate_average","revenueEstimateAverage","revenue_estimate_avg",
            "revenueEstimateAvg","revenueEstimate","estimatedRevenue","estimated_revenue",
            "revenueEstimateMean","revenue_estimate_mean",
        )
        date_keys=("fiscalDateEnding","fiscal_date_ending","date","reportDate","report_date")
        candidates=[]
        target=None
        try: target=date.fromisoformat(str(fiscal_end)[:10]) if fiscal_end else None
        except Exception: target=None
        for row in self._iter_dicts(data):
            val=None
            for k in revenue_keys:
                if k in row:
                    val=self._f(row.get(k));
                    if val is not None: break
            if val is None or val<=0: continue
            row_date=None
            for k in date_keys:
                raw=row.get(k)
                if raw:
                    try: row_date=date.fromisoformat(str(raw)[:10]); break
                    except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            distance=abs((row_date-target).days) if row_date and target else 9999
            candidates.append((distance,val,row))
        if not candidates:return None
        candidates.sort(key=lambda x:x[0])
        # Nur eine halbwegs passende Periode verwenden; ohne Datum nehmen wir
        # den ersten Wert nur, wenn es genau einen Kandidaten gibt.
        if target and candidates[0][0] <= 60:return candidates[0][1]
        if len(candidates)==1:return candidates[0][1]
        return None

    def snapshot(self,symbol):
        if not self.key:
            return self._sec_snapshot(symbol)
        try:
            data=self.history(symbol); q=data.get("quarterlyEarnings") or []
            if not q:return EarningsSnapshot(symbol,checked=True,reason="keine Quartalsdaten")
            latest=q[0]; prev_year=q[4] if len(q)>4 else None
            rep=self._f(latest.get("reportedEPS")); est=self._f(latest.get("estimatedEPS"));
            sp=self._f(latest.get("surprisePercentage"))
            if sp is None and rep is not None and est not in (None,0):sp=(rep/est-1)*100
            yoy=None
            if prev_year:
                old=self._f(prev_year.get("reportedEPS"))
                if old not in (None,0) and rep is not None:yoy=(rep/abs(old)-1)*100
            revenue=yoy_revenue=None
            gross_margin=operating_margin=yoy_gross_margin_delta=None
            prior_revenue=None; prior_period=None; cur_period=None
            try:
                inc=self.income_statement(symbol)
                qr=inc.get("quarterlyReports") or []
                if qr:
                    # Moeglichst den gleichen Fiskalzeitraum wie beim Earnings-Datensatz verwenden.
                    fiscal=str(latest.get("fiscalDateEnding") or "")[:10]
                    cur=next((x for x in qr if str(x.get("fiscalDateEnding") or "")[:10]==fiscal),qr[0])
                    cur_period=cur
                    revenue=self._f(cur.get("totalRevenue"))
                    gross=self._f(cur.get("grossProfit"))
                    opinc=self._f(cur.get("operatingIncome"))
                    if revenue not in (None,0):
                        if gross is not None:gross_margin=gross/revenue*100
                        if opinc is not None:operating_margin=opinc/revenue*100
                    cur_date=str(cur.get("fiscalDateEnding") or "")[:10]
                    if revenue is not None and cur_date:
                        try:
                            cur_d=date.fromisoformat(cur_date)
                            # Quartal des Vorjahres: naechster Bericht mit etwa 1 Jahr Abstand.
                            prev=min((x for x in qr[1:] if x.get("fiscalDateEnding")),
                                     key=lambda x: abs((cur_d-date.fromisoformat(str(x.get("fiscalDateEnding"))[:10])).days-365),
                                     default=None)
                            prior_period=prev
                            if prev:
                                prior_revenue=self._f(prev.get("totalRevenue"))
                                if prior_revenue not in (None,0):yoy_revenue=(revenue/abs(prior_revenue)-1)*100
                                old_gross=self._f(prev.get("grossProfit"))
                                if old_gross is not None and prior_revenue not in (None,0) and gross_margin is not None:
                                    yoy_gross_margin_delta=gross_margin-(old_gross/prior_revenue*100)
                        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            except Exception:
                # Umsatz-/Margendaten sind Zusatzinformation; ein Rate-Limit darf die EPS-Analyse nicht ausschalten.
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

            free_cash_flow=fcf_margin=yoy_fcf_growth=None
            try:
                cf=self.cash_flow(symbol)
                cqr=cf.get("quarterlyReports") or []
                if cqr:
                    fiscal=str(latest.get("fiscalDateEnding") or "")[:10]
                    ccur=next((x for x in cqr if str(x.get("fiscalDateEnding") or "")[:10]==fiscal),cqr[0])
                    ocf=self._f(ccur.get("operatingCashflow"))
                    capex=self._f(ccur.get("capitalExpenditures"))
                    if ocf is not None:
                        # Alpha Vantage kann CapEx positiv oder negativ liefern; wirtschaftlich
                        # ist FCF = OCF - |CapEx| robust gegen das Vorzeichenformat.
                        free_cash_flow=ocf-abs(capex or 0.0)
                        if revenue not in (None,0):fcf_margin=free_cash_flow/revenue*100
                    cdate=str(ccur.get("fiscalDateEnding") or "")[:10]
                    if free_cash_flow is not None and cdate:
                        cd=date.fromisoformat(cdate)
                        cprev=min((x for x in cqr[1:] if x.get("fiscalDateEnding")),
                                  key=lambda x: abs((cd-date.fromisoformat(str(x.get("fiscalDateEnding"))[:10])).days-365),
                                  default=None)
                        if cprev:
                            old_ocf=self._f(cprev.get("operatingCashflow")); old_capex=self._f(cprev.get("capitalExpenditures"))
                            if old_ocf is not None:
                                old_fcf=old_ocf-abs(old_capex or 0.0)
                                if abs(old_fcf)>1e-9:yoy_fcf_growth=(free_cash_flow/abs(old_fcf)-1)*100
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

            estimated_revenue=revenue_surprise=None
            try:
                estimates=self.estimates(symbol)
                estimated_revenue=self._find_revenue_estimate(estimates,str(latest.get("fiscalDateEnding") or ""))
                if revenue not in (None,0) and estimated_revenue not in (None,0):
                    revenue_surprise=(revenue/estimated_revenue-1)*100
            except Exception:
                # Estimates koennen plan-/rate-limit-abhaengig sein. Kein harter Fehler.
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

            score=50
            if sp is not None:
                score += 25 if sp>=15 else 18 if sp>=8 else 10 if sp>=3 else -20 if sp<=-10 else -10 if sp<0 else 4
            if yoy is not None:
                score += 10 if yoy>=25 else 6 if yoy>=10 else -8 if yoy<-10 else 0
            if revenue_surprise is not None:
                score += 15 if revenue_surprise>=8 else 10 if revenue_surprise>=4 else 6 if revenue_surprise>=1 else -14 if revenue_surprise<=-5 else -7 if revenue_surprise<0 else 2
            if yoy_revenue is not None:
                score += 10 if yoy_revenue>=25 else 6 if yoy_revenue>=10 else 3 if yoy_revenue>=3 else -10 if yoy_revenue<-10 else -4 if yoy_revenue<0 else 0
            # Qualitaet des Ergebnisses: Margen und Cashflow verhindern, dass ein
            # reiner Umsatz-/EPS-Beat mit schlechter Ergebnisqualitaet zu hoch bewertet wird.
            if gross_margin is not None:
                score += 4 if gross_margin>=50 else 2 if gross_margin>=30 else -3 if gross_margin<10 else 0
            if yoy_gross_margin_delta is not None:
                score += 5 if yoy_gross_margin_delta>=2 else 2 if yoy_gross_margin_delta>=0.5 else -6 if yoy_gross_margin_delta<=-2 else -2 if yoy_gross_margin_delta<0 else 0
            if operating_margin is not None:
                score += 4 if operating_margin>=20 else 2 if operating_margin>=10 else -5 if operating_margin<0 else 0
            if fcf_margin is not None:
                score += 6 if fcf_margin>=15 else 3 if fcf_margin>=5 else -7 if fcf_margin<0 else 0
            if yoy_fcf_growth is not None:
                score += 4 if yoy_fcf_growth>=20 else 2 if yoy_fcf_growth>=5 else -4 if yoy_fcf_growth<-20 else 0
            score=max(0,min(100,int(round(score))))
            strong_threshold=int(getattr(config,"EARNINGS_STRONG_SCORE",80))
            grade="STRONG" if score>=strong_threshold else "POSITIVE" if score>=65 else "NEUTRAL" if score>=45 else "WEAK"
            rd=str(latest.get("reportedDate") or "")
            recent=False
            try:
                drift_days=int(getattr(config,"EARNINGS_POST_DRIFT_DAYS",getattr(config,"EARNINGS_RECENT_DAYS",3)))
                age_days=(date.today()-date.fromisoformat(rd[:10])).days
                recent=0 <= age_days <= drift_days
            except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            reason=(f"EPS Surprise {sp if sp is not None else 'n/a'}%, "
                    f"Umsatz-Surprise {revenue_surprise if revenue_surprise is not None else 'n/a'}%, "
                    f"YoY EPS {yoy if yoy is not None else 'n/a'}%, "
                    f"YoY Umsatz {yoy_revenue if yoy_revenue is not None else 'n/a'}%, "
                    f"Bruttomarge {gross_margin if gross_margin is not None else 'n/a'}%, "
                    f"Operative Marge {operating_margin if operating_margin is not None else 'n/a'}%, "
                    f"FCF-Marge {fcf_margin if fcf_margin is not None else 'n/a'}%")
            return EarningsSnapshot(
                symbol=symbol,checked=True,report_date=rd,fiscal_end=str(latest.get("fiscalDateEnding") or ""),
                reported_eps=rep,estimated_eps=est,surprise_pct=sp,yoy_eps_growth_pct=yoy,
                revenue=revenue,estimated_revenue=estimated_revenue,revenue_surprise_pct=revenue_surprise,
                yoy_revenue_growth_pct=yoy_revenue,gross_margin_pct=gross_margin,
                operating_margin_pct=operating_margin,yoy_gross_margin_delta_pp=yoy_gross_margin_delta,
                free_cash_flow=free_cash_flow,fcf_margin_pct=fcf_margin,yoy_fcf_growth_pct=yoy_fcf_growth,
                score=score,grade=grade,reason=reason,recent=recent
            )
        except Exception as exc:
            # Ein Ausfall des optionalen Key-Providers darf die kostenlose
            # SEC-Grundlage nicht ausschalten.
            sec=self._sec_snapshot(symbol)
            if sec.checked:
                sec.reason=f"Alpha Vantage nicht verfuegbar ({exc}); "+sec.reason
                return sec
            return EarningsSnapshot(symbol,False,reason=str(exc))
