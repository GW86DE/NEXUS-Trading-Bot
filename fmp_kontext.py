"""FMP-Zusatzkontext und Nutzungsnachweis ueber die gemeinsame Instanz (10.8.0, Schritt 2).

Schichten der FMP-Module, von unten nach oben:
    fmp_service    Speicher, Budget, Abruf
    fmp_data       Endpunkte und Kennzahlen (nutzt fmp_service)
    fmp_reference  gemeinsame Instanz ``client()`` (nutzt beide)
    fmp_kontext    Auswertung ueber die Instanz (dieses Modul)

Bis 10.7.1 lag ``research_context`` in fmp_data und ``record_use`` in fmp_service --
beide griffen nach oben auf ``fmp_reference.client()``: zwei Import-Zyklen.
Verhalten unveraendert; Aufrufer (Zweitmeinung, PULSAR) importieren jetzt von hier.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def research_context(symbol):
    """Cache-only context for an existing NEXUS GPT review; never extra calls."""
    import fmp_reference
    from fmp_service import settings
    from fmp_data import daily_metrics, annual_context
    client = fmp_reference.client()
    if not client.konfiguriert:
        return {}
    symbol = str(symbol).strip().upper()
    out = {'role': 'OPTIONAL_RESEARCH_ONLY',
           'detail': 'FMP-Referenzdaten; keine Brokerkurse oder Ausfuehrungs-/Buchungsbelege'}
    history = client.store.cached('history:' + symbol)
    if history:
        out['daily'] = daily_metrics(history['data'].get('rows', []))
    mode, declared = settings()
    if mode == 'STARTER' or mode == 'AUTO' and declared == 'STARTER':
        annual = client.store.cached('annual:' + symbol)
        if annual:
            out['annual'] = annual_context(annual['data'])
            out['annual']['fetched_at'] = annual['saved']
        macro = client.store.cached('market_context')
        if macro:
            out['market_context'] = macro['data']
    return out


def record_use(kind, symbol, facts):
    """Optional effectiveness log; a reporting failure never changes a decision."""
    try:
        from fmp_reference import client
        ref = client()
        if ref.konfiguriert:
            ref.store.record_use(kind, symbol, facts)
    except Exception as exc:
        logger.info('FMP-Nutzungsnachweis nicht gespeichert (%s)', type(exc).__name__)
