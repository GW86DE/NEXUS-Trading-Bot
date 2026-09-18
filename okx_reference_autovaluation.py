"""10.2.1: EUR-Referenzbewertung fuer zusammengesetzte OKX-Abschluesse, automatisch.

Bislang entstand eine EUR-Referenzbewertung (okx_reference_valuation) nur aus
manuell gelieferten, verifizierten Kursexporten. Ein nativ geschlossener
Cross-Currency-Trade blieb deshalb UNBEZIFFERT und hielt als RESULT-Beleg die
Kaufdomaene gesperrt, obwohl saemtliche Ausfuehrungsbelege vorlagen.

Dieses Modul holt die fehlenden KURSbelege selbst -- mit exakt denselben
oeffentlichen, kontofreien Abrufen (USDC/EUR-Minutenkerzen von eea.okx.com,
EZB-Tagesreferenz nur fuer USD-Legs), speichert die ROHEN Antworten mit
Pruefsumme und laesst die bestehende strenge ``Rates``-Validierung entscheiden.
Es beziffert nichts selbst: gelingt ein Beleg nicht, bleibt das Ergebnis
unbekannt und die Sperre bestehen (UNKNOWN bleibt UNKNOWN).

Begrenzt: hoechstens EIN Trade je Aufruf, je Trade ein Wiederholungs-Backoff.
"""
from __future__ import annotations

from contextlib import closing
import json
import logging
import time

logger = logging.getLogger(__name__)

_BACKOFF: dict[int, float] = {}
_BACKOFF_SECONDS = 600.0


def _kandidat(con, account: str, environment: str):
    rows = con.execute("""SELECT * FROM trades WHERE broker='okx'
        AND broker_account_fingerprint=? AND paper=? AND superseded_by IS NULL
        AND ausgestiegen_am IS NOT NULL AND netto_pnl IS NULL
        AND exit_native_json IS NOT NULL AND exit_native_json != ''
        ORDER BY ausgestiegen_am DESC LIMIT 20""",
        (account, int(environment == 'DEMO'))).fetchall()
    for raw in rows:
        row = dict(raw)
        try:
            receipt = json.loads(row.get('exit_native_json') or '{}').get('receipt') or {}
        except ValueError:
            continue
        if receipt.get('beweisart') != 'ZUSAMMENGESETZTER_VERKAUF':
            continue
        saved = con.execute('SELECT 1 FROM okx_reference_results WHERE trade_id=?',
                            (row['trade_id'],)).fetchone() if con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='okx_reference_results'"
        ).fetchone() else None
        if saved:
            continue
        if time.monotonic() - _BACKOFF.get(row['trade_id'], -1e12) < _BACKOFF_SECONDS:
            continue
        return row, receipt
    return None, None


def _proofs(broker, row, receipt):
    """Entry- und Sale-Beweise in prove_order-Form aus Broker-Originaldaten."""
    from okx_receipt_math import prove_order
    inst = str(row['broker_position_id'])
    base = inst.split('-')[0]
    entry_oid = str(row['entry_order_id'])
    entry_status = broker.client.order_status(inst, ord_id=entry_oid)
    if not entry_status:
        entry_status = broker.client.order_history_match(inst, ord_id=entry_oid, side='buy')
    entry_fills = broker.order_fills(inst, entry_oid)
    entry = prove_order(entry_status, entry_fills, inst=inst, oid=entry_oid,
                        side='buy', base=base, currency=str(row['waehrung']),
                        account=str(row['broker_account_fingerprint']))
    sales = []
    gruppen: dict[str, list] = {}
    for fill in receipt.get('fills') or []:
        gruppen.setdefault(str(fill['ordId']), []).append(dict(fill))
    for oid, fills in sorted(gruppen.items()):
        order = (receipt.get('orders') or {}).get(oid) or {}
        status = dict(order.get('status') or {})
        sale_inst = str(order.get('inst_id') or status.get('instId') or '')
        quote = str(order.get('quote_ccy') or '').upper()
        sales.append(prove_order(status, fills, inst=sale_inst, oid=oid,
                                 side='sell', base=base, currency=quote,
                                 account=str(row['broker_account_fingerprint'])))
    return entry, sales


def run_one(broker) -> bool:
    """Genau einen offenen zusammengesetzten Abschluss beziffern (best effort)."""
    import trade_ledger as tl
    from okx_reference_valuation import Rates, calculate_composite, store_on_composite
    account = str(broker.account_fingerprint() or '')
    environment = 'DEMO' if broker.demo else 'LIVE'
    if not account:
        return False
    tl.init_ledger()
    with tl._LOCK, closing(tl._connect()) as con, con:
        row, receipt = _kandidat(con, account, environment)
    if not row:
        return False
    tid = int(row['trade_id'])
    _BACKOFF[tid] = time.monotonic()
    try:
        entry, sales = _proofs(broker, row, receipt)
        fill_times = []
        currencies = set()
        for proof in [entry, *sales]:
            currencies.add(proof['currency'])
            if proof['currency'] not in {'EUR'}:
                for fill in proof['fills']:
                    fill_times.append(str(fill.get('fillTime') or fill.get('ts') or ''))
        document = broker.fx_followup_document(fill_times, currencies)
        rates = Rates(document)
        valuation = calculate_composite(entry, sales, rates,
                                        account=account, environment=environment)
        with tl._LOCK, closing(tl._connect()) as con, con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute('SELECT * FROM trades WHERE trade_id=?', (tid,)).fetchone()
            stored = store_on_composite(con, dict(current), valuation, rates)
        logger.info('EUR-Referenzbewertung fuer Trade %s %s: netto %s EUR (%s)',
                    tid, 'gespeichert' if stored else 'bereits vorhanden',
                    valuation['net_eur'], valuation['method'])
        return True
    except Exception as exc:
        logger.warning('EUR-Referenzbewertung fuer Trade %s noch nicht moeglich: %s',
                       tid, exc)
        return False


__all__ = ['run_one']
