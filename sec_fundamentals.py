"""Kostenlose SEC-EDGAR-Unternehmensdaten ohne API-Key.

Es wird ausschliesslich data.sec.gov genutzt. Fuer faire Nutzung verlangt die
SEC einen identifizierenden User-Agent mit Kontakt-E-Mail; kein API-Key noetig.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json, time, requests, os
from safe_persistence import best_effort_json
import config

ROOT=Path(__file__).resolve().parent
CACHE=Path(os.environ.get("TRADINGBOT_TEST_STATE_DIR") or ROOT)/"sec_fundamentals_cache.json"
TICKERS="https://www.sec.gov/files/company_tickers.json"
FACTS="https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

class SecFundamentals:
    def __init__(self):
        self.email=str(getattr(config,"SEC_USER_AGENT_EMAIL","") or "").strip()
        self.timeout=float(getattr(config,"NEWS_SOURCE_TIMEOUT_SECONDS",10))
        self.ttl=int(getattr(config,"SEC_FUNDAMENTALS_CACHE_SECONDS",21600))
        self.s=requests.Session()
        self.s.headers.update({"User-Agent":f"TradingBot/5.6 private research {self.email}","Accept-Encoding":"gzip, deflate"})
        self._tickers=None
        try:self.cache=json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        except Exception:self.cache={}
    def enabled(self): return bool(self.email and "@" in self.email)
    def _save(self):
        best_effort_json(CACHE, self.cache, label='SEC-Fundamentals-Cache')
    def _map(self):
        if self._tickers is not None:return self._tickers
        if not self.enabled(): return {}
        r=self.s.get(TICKERS,timeout=self.timeout); r.raise_for_status(); raw=r.json()
        self._tickers={str(v.get('ticker','')).upper():v for v in raw.values()}
        return self._tickers
    @staticmethod
    def _rows(facts, names):
        """Preserve the unit of the selected row, not the first unit key."""
        today = datetime.now(timezone.utc).date().isoformat()
        rows = []
        for name in names:
            for unit, values in ((facts.get(name) or {}).get("units") or {}).items():
                for row in values or []:
                    if not isinstance(row, dict):
                        continue
                    filed, end = str(row.get("filed") or ""), str(row.get("end") or "")
                    try:
                        datetime.strptime(filed, "%Y-%m-%d")
                        datetime.strptime(end, "%Y-%m-%d")
                    except ValueError:
                        continue
                    if (row.get("form") not in ("10-Q", "10-K", "20-F", "40-F")
                            or row.get("val") is None or filed > today or end > today):
                        continue
                    rows.append({"value": row["val"], "unit": unit,
                        "start": row.get("start"), "end": end, "filed": filed,
                        "form": row["form"], "concept": name, "accession": row.get("accn")})
        return sorted(rows, key=lambda row: (row["end"], row["filed"], str(row["start"] or "")), reverse=True)

    @staticmethod
    def _latest(facts, names):
        rows = SecFundamentals._rows(facts, names)
        return rows[0] if rows else None

    @staticmethod
    def _matched_results(facts):
        """Income and cash flow must refer to the same duration and unit.

        A quarterly profit must not be compared to a YTD cash flow solely
        because both appeared in the latest filing.
        """
        profits = SecFundamentals._rows(facts, ["NetIncomeLoss"])
        cashflows = SecFundamentals._rows(facts, ["NetCashProvidedByUsedInOperatingActivities"])
        for profit in profits:
            if not profit.get("start"):
                continue
            for cashflow in cashflows:
                if all(profit.get(key) == cashflow.get(key) for key in ("start", "end", "unit")):
                    return profit, cashflow
        return (profits[0] if profits else None, cashflows[0] if cashflows else None)

    def snapshot(self,symbol):
        symbol=str(symbol).upper()
        old=self.cache.get(symbol,{})
        if old and old.get("schema_version") == 2 and time.time()-float(old.get("_ts",0))<self.ttl:return old
        if not self.enabled(): return {"available":False,"reason":"SEC Kontakt-E-Mail nicht eingerichtet"}
        row=self._map().get(symbol)
        if not row:return {"available":False,"reason":"Ticker bei SEC nicht gefunden"}
        cik=int(row.get("cik_str") or 0)
        r=self.s.get(FACTS.format(cik=cik),timeout=self.timeout); r.raise_for_status(); raw=r.json()
        try:
            identity_ok = cik > 0 and int(raw.get("cik") or 0) == cik
        except (TypeError, ValueError):
            identity_ok = False
        if not identity_ok:
            return {"available": False, "symbol": symbol, "reason": "SEC-Ticker-/CIK-Beleg widerspruechlich"}
        f=((raw.get("facts") or {}).get("us-gaap") or {})
        profit, cashflow = self._matched_results(f)
        out={"available":True,"schema_version":2,"symbol_identity_verified":True,"symbol":symbol,"company":raw.get("entityName"),"cik":cik,
             "revenue":self._latest(f,["RevenueFromContractWithCustomerExcludingAssessedTax","Revenues","SalesRevenueNet"]),
             "net_income":profit,
             "eps_diluted":self._latest(f,["EarningsPerShareDiluted"]),
             "operating_cashflow":cashflow,
             "assets":self._latest(f,["Assets"]),"liabilities":self._latest(f,["Liabilities"]),
             "source":"SEC EDGAR / Company Facts","fetched_at":datetime.now(timezone.utc).isoformat(),"_ts":time.time()}
        self.cache[symbol]=out; self._save(); return out
