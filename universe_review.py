"""Deterministische Aufnahmepruefung fuer menschlich angeforderte Research-Ideen.

Diese Pruefung erzeugt KEINE Order. Sie darf lediglich einen Vorschlag in den
Zustand TECHNICALLY_APPROVED versetzen. Die finale Aufnahme braucht danach
noch einen zweiten menschlichen Telegram-Klick.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import config
from contracts import Instrument, SimpleContract
from cost_engine import estimate_roundtrip_with_broker, CostQuoteUnavailable
from instrument_identity import canonical_key, normalize_user_symbol

_EXCLUDE_TOKENS = (
    "spac", "acquisition corp", "acquisition co", "blank check", "warrant",
    "rights", " units", "unit ", "depositary warrant",
)


@dataclass
class ReviewResult:
    passed: bool
    symbol: str
    etoro_symbol_full: str = ""
    instrument_id: int | None = None
    company: str = ""
    live_quote_ok: bool = False
    history_bars: int = 0
    avg_dollar_volume: float = 0.0
    median_dollar_volume: float = 0.0
    spread_pct: float = 0.0
    cost_pct: float = 0.0
    required_edge_pct: float = 0.0
    cost_source: str = ""
    summary: str = ""
    checks: list | None = None

    def to_dict(self):
        return asdict(self)


def _fail(symbol: str, checks: list, reason: str, **kw) -> ReviewResult:
    checks.append({"name":"result","status":"BLOCKED","detail":reason})
    return ReviewResult(False,symbol,summary=reason,checks=checks,**kw)


def review_proposal(broker, proposal: dict, configured_keys: set[str]) -> ReviewResult:
    checks=[]
    try:
        symbol=normalize_user_symbol(proposal.get("symbol", ""), "stock")
    except Exception as exc:
        return _fail("?",checks,f"ungueltiges Aktiensymbol: {exc}")
    key=canonical_key(symbol,"stock")
    if key in set(configured_keys or set()):
        return _fail(symbol,checks,"Wert ist bereits im aktiven/geprueften Universum")
    checks.append({"name":"not_already_configured","status":"PASS"})

    if not getattr(broker,"is_connected",lambda:False)():
        return _fail(symbol,checks,"eToro ist derzeit nicht verbunden")

    inst=Instrument(symbol,SimpleContract(symbol),"stock","USD",str(proposal.get("sector") or "UNCLASSIFIED"),"ETORO",True,False)
    try:
        meta=broker.instrument_metadata(inst)
    except Exception as exc:
        return _fail(symbol,checks,f"eToro-Instrument nicht eindeutig aufloesbar: {exc}")
    full=str(meta.get("symbol_full") or "").upper()
    iid=meta.get("instrument_id")
    detected=str(meta.get("asset_type") or "")
    company=str(meta.get("display_name") or proposal.get("company") or "")
    kind_text=" ".join(str(meta.get(k) or "") for k in ("instrument_type","display_name")).lower()
    if detected != "stock" or not full:
        return _fail(symbol,checks,"eToro-Metadaten bestaetigen keine eindeutige Aktie",etoro_symbol_full=full,instrument_id=iid,company=company)
    if any(t in kind_text for t in _EXCLUDE_TOKENS):
        return _fail(symbol,checks,"SPAC/Warrant/Unit-Ausschluss durch eToro-Metadaten",etoro_symbol_full=full,instrument_id=iid,company=company)
    checks.append({"name":"asset_and_structure","status":"PASS","detail":f"{full} / {detected}"})

    try:
        tradable,reason=broker.instrument_handelbar(inst)
    except Exception as exc:
        return _fail(symbol,checks,f"eToro-Eligibility nicht pruefbar: {exc}",etoro_symbol_full=full,instrument_id=iid,company=company)
    if not tradable:
        return _fail(symbol,checks,f"eToro REAL/Hebel-1 nicht handelbar: {reason}",etoro_symbol_full=full,instrument_id=iid,company=company)
    checks.append({"name":"etoro_eligibility","status":"PASS"})

    try:
        quote=broker.latest_bid_ask(inst) or {}
        bid=float(quote.get("bid") or 0); ask=float(quote.get("ask") or 0)
    except Exception as exc:
        return _fail(symbol,checks,f"Live-Kurs nicht abrufbar: {exc}",etoro_symbol_full=full,instrument_id=iid,company=company)
    if bid <= 0 or ask <= bid:
        return _fail(symbol,checks,"gueltiger eToro Live-Bid/Ask fehlt",etoro_symbol_full=full,instrument_id=iid,company=company)
    mid=(bid+ask)/2.0; spread=(ask-bid)/mid
    max_spread=float(getattr(config,"UNIVERSE_APPROVAL_MAX_SPREAD_PCT",0.008))
    if spread > max_spread:
        return _fail(symbol,checks,f"Spread {spread*100:.3f}% > Aufnahmegrenze {max_spread*100:.3f}%",etoro_symbol_full=full,instrument_id=iid,company=company,live_quote_ok=True,spread_pct=spread)
    checks.append({"name":"live_quote_and_spread","status":"PASS","detail":f"{spread*100:.3f}%"})

    try:
        hist=broker.historie(inst,getattr(config,"UNIVERSE_APPROVAL_HISTORY_DURATION","260 D"),"1 day",nur_handelszeiten=True)
    except Exception as exc:
        return _fail(symbol,checks,f"Historie nicht abrufbar: {exc}",etoro_symbol_full=full,instrument_id=iid,company=company,live_quote_ok=True,spread_pct=spread)
    min_bars=int(getattr(config,"UNIVERSE_APPROVAL_MIN_HISTORY_BARS",190))
    bars=int(len(hist) if hist is not None else 0)
    if bars < min_bars:
        return _fail(symbol,checks,f"nur {bars} Tageskerzen; frische IPOs/zu kurze Historie werden ausgeschlossen (Minimum {min_bars})",etoro_symbol_full=full,instrument_id=iid,company=company,live_quote_ok=True,spread_pct=spread,history_bars=bars)
    try:
        recent=hist.tail(min(60,bars)).copy()
        dollars=(recent["close"].astype(float)*recent["volume"].astype(float)).replace([math.inf,-math.inf],float("nan")).dropna()
        avg=float(dollars.mean()) if len(dollars) else 0.0
        med=float(dollars.median()) if len(dollars) else 0.0
    except Exception:
        avg=med=0.0
    min_avg=float(getattr(config,"UNIVERSE_APPROVAL_MIN_AVG_DOLLAR_VOLUME",20_000_000.0))
    min_med=float(getattr(config,"UNIVERSE_APPROVAL_MIN_MEDIAN_DOLLAR_VOLUME",10_000_000.0))
    if avg < min_avg or med < min_med:
        return _fail(symbol,checks,f"Liquiditaet zu niedrig: Ø {avg:,.0f} USD / Median {med:,.0f} USD",etoro_symbol_full=full,instrument_id=iid,company=company,live_quote_ok=True,spread_pct=spread,history_bars=bars,avg_dollar_volume=avg,median_dollar_volume=med)
    checks.append({"name":"history_and_liquidity","status":"PASS","detail":f"{bars} Bars · Ø {avg:,.0f} USD · Median {med:,.0f} USD"})

    notional=float(getattr(config,"UNIVERSE_APPROVAL_COST_NOTIONAL",3000.0))
    qty=max(notional/mid,1e-8)
    try:
        costs=estimate_roundtrip_with_broker(broker,qty,mid,"stock","USD",bid=bid,ask=ask,broad=True,instrument=inst)
    except CostQuoteUnavailable as exc:
        return _fail(symbol,checks,f"konservative eToro-Kostenklasse nicht pruefbar: {exc}",etoro_symbol_full=full,instrument_id=iid,company=company,live_quote_ok=True,spread_pct=spread,history_bars=bars,avg_dollar_volume=avg,median_dollar_volume=med)
    except Exception as exc:
        return _fail(symbol,checks,f"Kostenpruefung fehlgeschlagen: {exc}",etoro_symbol_full=full,instrument_id=iid,company=company,live_quote_ok=True,spread_pct=spread,history_bars=bars,avg_dollar_volume=avg,median_dollar_volume=med)
    max_cost=float(getattr(config,"UNIVERSE_APPROVAL_MAX_ROUNDTRIP_COST_PCT",0.015))
    if not str(getattr(costs,"source","")).startswith("etoro_what_if") or float(costs.total_cost_pct) > max_cost:
        return _fail(symbol,checks,f"Kostenklasse nicht konservativ genug: {costs.total_cost_pct*100:.3f}%",etoro_symbol_full=full,instrument_id=iid,company=company,live_quote_ok=True,spread_pct=spread,history_bars=bars,avg_dollar_volume=avg,median_dollar_volume=med,cost_pct=float(costs.total_cost_pct),required_edge_pct=float(costs.required_edge_pct),cost_source=str(costs.source))
    checks.append({"name":"etoro_cost_class","status":"PASS","detail":f"{costs.total_cost_pct*100:.3f}% · {costs.source}"})

    summary=(f"PASS · {full} · {bars} Tageskerzen · Ø-Volumen {avg:,.0f} USD · "
             f"Spread {spread*100:.3f}% · Kosten {costs.total_cost_pct*100:.3f}%")
    return ReviewResult(True,symbol,full,iid,company,True,bars,avg,med,spread,float(costs.total_cost_pct),float(costs.required_edge_pct),str(costs.source),summary,checks)
