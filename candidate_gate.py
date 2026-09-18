"""Reine, deterministische letzte Kaufpruefung vor dem eToro-Submit.

Die Funktion kennt keine KI und keine Netzwerkzugriffe. Alle Eingaben stammen
bereits aus den vorherigen Filtern. Dadurch ist die Geldpfad-Entscheidung ohne
Broker, Telegram oder OpenAI reproduzierbar testbar.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Mapping
import math


@dataclass(frozen=True)
class CandidateDecision:
    approved: bool
    blocked_by: str = ""
    reason: str = ""
    checks: Mapping[str, bool] = field(default_factory=dict)


def bewerte_kandidat(*, state_allows_buy: bool, broker_online: bool,
                      instrument_identity_ok: bool, market_open: bool,
                      position_already_open: bool, duplicate_open_order: bool,
                      risk_allows_buy: bool, portfolio_allows_buy: bool,
                      cash_allows_buy: bool, market_quality_ok: bool,
                      cost_quote_ok: bool, net_edge_ok: bool,
                      quantity: float, price: float, stop: float,
                      take_profit: float) -> CandidateDecision:
    checks = {
        "state": bool(state_allows_buy),
        "broker_online": bool(broker_online),
        "instrument_identity": bool(instrument_identity_ok),
        "market_session": bool(market_open),
        "existing_position": not bool(position_already_open),
        "duplicate_open_order": not bool(duplicate_open_order),
        "risk_manager": bool(risk_allows_buy),
        "portfolio_guard": bool(portfolio_allows_buy),
        "cash_reserve": bool(cash_allows_buy),
        "market_quality": bool(market_quality_ok),
        "cost_quote": bool(cost_quote_ok),
        "net_edge": bool(net_edge_ok),
    }
    order = [
        ("state", "Botzustand erlaubt keine neuen Kaeufe"),
        ("broker_online", "eToro ist nicht sicher online/synchronisiert"),
        ("instrument_identity", "Instrumentidentitaet ist nicht eindeutig"),
        ("market_session", "Marktzeit/Handelbarkeit blockiert den Einstieg"),
        ("existing_position", "Position bereits vorhanden"),
        ("duplicate_open_order", "Offene Kauforder bereits vorhanden"),
        ("risk_manager", "Risikolimit blockiert den Einstieg"),
        ("portfolio_guard", "Portfolio-/Klumpenregel blockiert den Einstieg"),
        ("cash_reserve", "Cash-Reserve reicht nicht"),
        ("market_quality", "Marktqualitaet/Spread unzureichend"),
        ("cost_quote", "eToro-Kostenabfrage nicht bestaetigt"),
        ("net_edge", "Netto-Edge nach Kosten unzureichend"),
    ]
    for key, reason in order:
        if not checks[key]:
            return CandidateDecision(False, key, reason, checks)

    nums = [quantity, price, stop, take_profit]
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in nums):
        return CandidateDecision(False, "order_geometry", "Menge/Preis/Schutzwerte ungueltig", checks)
    if not (float(stop) < float(price) < float(take_profit)):
        return CandidateDecision(False, "order_geometry", "Stop/Preis/Take-Profit nicht in Long-Reihenfolge", checks)
    return CandidateDecision(True, "", "freigegeben", checks)
