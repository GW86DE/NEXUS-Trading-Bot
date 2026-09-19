"""Decimal, order-scoped spot evidence. No network, state changes or inferred FX.

Cash is the amount actually paid/received in the explicitly evidenced quote
currency. A fee paid in base changes inventory; it is not a second cash debit.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Iterable, Mapping


class EvidenceError(ValueError):
    """Missing or conflicting evidence; leave accounting pending."""


def number(value, *, positive=False) -> Decimal:
    if value is None or value == '' or isinstance(value, bool):
        raise EvidenceError('Zahlenbeleg fehlt')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise EvidenceError('Ungueltiger Zahlenbeleg') from None
    if not result.is_finite() or (positive and result <= 0):
        raise EvidenceError('Nicht endlicher/positiver Zahlenbeleg')
    return result


def tolerance(value) -> Decimal:
    """Only the stored SQLite REAL error, never exchange lot/percentage slack."""
    d = number(value)
    f = float(d)
    if not math.isfinite(f):
        raise EvidenceError('Zahlenbeleg ausserhalb des Speicherbereichs')
    return max(Decimal('1e-15'), Decimal(str(math.ulp(f))) * 4)


def same_quantity(actual, expected) -> bool:
    return abs(number(actual) - number(expected)) <= tolerance(expected)


def quantity_explained(unexplained, sold, residual=0) -> bool:
    """Deckt Verkauf PLUS weitergefuehrter Rest den beobachteten Abgang?

    WARUM ES DIESE FUNKTION GIBT (10.7.0)
    -------------------------------------
    ``same_quantity``/``tolerance`` sind absichtlich hart: nur der
    Fliesskommafehler, nie Boersen-Lot oder Prozent. Fuer den Vergleich
    "Fill gegen Fill" ist das richtig. Fuer die Frage "ist der Abgang aus
    dem Konto erklaert?" war es dreimal falsch:

      * 10.2.1 Rev 2: 0,00847 DOGE Lot-Rest liess den Composite-Beleg scheitern
      * 10.3.0:       Demo-Startguthaben wurde einer Staubzeile zugeordnet
      * 18.09.2026:   XRP/BTC/ETH per Take-Profit verkauft, Trades geschlossen
                      und bestaetigt -- die Bestandsluecken blieben PENDING,
                      weil 18,3284 (Lot) < 18,3284532 (gebucht). Die 0,0000532
                      lagen als eigene Ledgerzeile 85 vor. Drei Belege sperrten
                      jeden OKX-Kauf.

    Die Boerse verkauft in Lot-Schritten. Was unter dem Lot bleibt, fuehrt
    ``trade_close`` als eigene Zeile ("Rest nach Teilverkauf") weiter. Dieser
    Rest ist nicht verschwunden, er ist verbucht -- und genau deshalb zaehlt er
    hier mit. Jede Stelle, die einen Bestandsabgang gegen Verkaeufe rechnet,
    ruft DIESE Funktion und nicht mehr ``tolerance`` direkt.
    """
    todo = number(unexplained)
    covered = number(sold) + (number(residual) if residual not in (None, '') else Decimal(0))
    if covered < 0:
        raise EvidenceError('Negative Deckungsmenge')
    return covered + tolerance(todo) >= todo


def stamp(raw) -> datetime:
    millis = number(raw.get('fillTime') or raw.get('ts'), positive=True)
    try:
        dt = datetime.fromtimestamp(float(millis / 1000), timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise EvidenceError('Ungueltiger Ausfuehrungszeitpunkt') from None
    if dt.timestamp() > datetime.now(timezone.utc).timestamp() + 60:
        raise EvidenceError('Ausfuehrungszeitpunkt liegt in der Zukunft')
    return dt


def identity(raw: Mapping, account: str) -> str:
    return f"okx:{account}:{raw['instId']}:{raw['ordId']}:{raw['tradeId']}"


def unique_fills(rows: Iterable[Mapping], *, inst: str, oid: str, side: str,
                 currency: str, account: str, order_currency='') -> list[dict]:
    if not all((inst, oid, side, currency, account)) or side not in {'buy', 'sell'}:
        raise EvidenceError('Ausfuehrungsbeleg ohne Identitaet')
    if order_currency and order_currency != currency:
        raise EvidenceError('Abrechnungswaehrung der Order widerspricht dem Trade')
    unique = {}
    for value in rows:
        if not isinstance(value, Mapping):
            raise EvidenceError('Fill ist kein Objekt')
        raw = dict(value)
        if any(str(raw.get(k) or '') != v for k, v in (
                ('instId', inst), ('ordId', oid), ('side', side))):
            raise EvidenceError('Fill-Identitaet widerspricht der exakten Order')
        if not str(raw.get('tradeId') or ''):
            raise EvidenceError('Fill ohne tradeId')
        quote = str(raw.get('tradeQuoteCcy') or order_currency)
        if quote != currency:
            raise EvidenceError('Fill-Abrechnungswaehrung fehlt oder widerspricht')
        q, px, fee = number(raw.get('fillSz'), positive=True), number(raw.get('fillPx'), positive=True), number(raw.get('fee'))
        fc = str(raw.get('feeCcy') or '')
        if not fc:
            raise EvidenceError('Gebuehrenwaehrung fehlt')
        moment = stamp(raw)
        # A second observation's ts/billId may differ; execution time may not.
        sig = (inst, oid, side, quote, q, px, fee, fc, moment.isoformat())
        fid = identity(raw, account)
        if fid in unique and unique[fid][0] != sig:
            raise EvidenceError('Widerspruechlicher doppelter Fill')
        unique.setdefault(fid, (sig, raw))
    if not unique:
        raise EvidenceError('Ausfuehrungsfills fehlen')
    return [v[1] for _, v in sorted(unique.items())]


def summarize(rows, *, inst: str, oid: str, side: str, base: str, currency: str,
              account: str, order_currency='') -> dict:
    if not base or base == currency:
        raise EvidenceError('Instrument-Basiswaehrung fehlt/widerspricht')
    fills = unique_fills(rows, inst=inst, oid=oid, side=side, currency=currency,
                         account=account, order_currency=order_currency)
    gross_qty = value = base_fee = quote_fee = fee_value = Decimal(0)
    fees = {}
    for f in fills:
        q, px, charge = number(f['fillSz']), number(f['fillPx']), -number(f['fee'])
        fc = f['feeCcy']
        if fc not in {base, currency}:
            raise EvidenceError('Drittwaehrungsgebuehr ohne historischen Bewertungsbeleg')
        gross_qty += q
        value += q * px
        fees[fc] = fees.get(fc, Decimal(0)) + charge
        if fc == base:
            base_fee += charge
            fee_value += charge * px
        else:
            quote_fee += charge
            fee_value += charge
    base_qty = gross_qty - base_fee if side == 'buy' else gross_qty + base_fee
    cash = value + quote_fee if side == 'buy' else value - quote_fee
    if base_qty <= 0 or cash <= 0:
        raise EvidenceError('Nicht positive Nettomenge/Geldbewegung')
    times = [stamp(f) for f in fills]
    return dict(inst_id=inst, order_id=oid, side=side, base=base, currency=currency,
        account=account, gross_quantity=str(gross_qty), quantity=str(base_qty),
        value=str(value), cash=str(cash), price=str(value / gross_qty),
        fees_quote=str(fee_value), base_fee=str(base_fee), quote_fee=str(quote_fee),
        fees={k: str(v) for k, v in sorted(fees.items())},
        first_at=min(times).isoformat(), last_at=max(times).isoformat(),
        fills=fills, fill_ids=[identity(f, account) for f in fills])


def prove_order(status, fills, *, inst: str, oid: str, side: str, base: str,
                currency: str, account: str) -> dict:
    if not isinstance(status, Mapping) or any(str(status.get(k) or '') != v for k, v in (
            ('instId', inst), ('ordId', oid), ('side', side))):
        raise EvidenceError('Exakter primaerer Orderstatus fehlt oder widerspricht der Identitaet')
    if status.get('state') not in {'filled', 'canceled', 'mmp_canceled'}:
        raise EvidenceError('Kein terminaler primaerer Orderstatus')
    result = summarize(fills, inst=inst, oid=oid, side=side, base=base,
        currency=currency, account=account, order_currency=str(status.get('tradeQuoteCcy') or ''))
    if not same_quantity(result['gross_quantity'], number(status.get('accFillSz'), positive=True)):
        raise EvidenceError('Ordermenge nicht vollstaendig durch Einzel-Fills belegt')
    for field in ('sz',):
        # sz may be quote-denominated for market buys; accFillSz is always base.
        if side == 'sell' and status.get(field) not in (None, '') and number(status[field]) < number(result['gross_quantity']) - tolerance(result['gross_quantity']):
            raise EvidenceError('Ausfuehrung groesser als Verkaufsauftrag')
    if status.get('avgPx') not in (None, '', '0'):
        if abs(number(status['avgPx']) - number(result['price'])) > max(Decimal('1e-10'), abs(number(result['price'])) * Decimal('1e-8')):
            raise EvidenceError('Order-Durchschnittspreis widerspricht Fills')
    if status.get('fee') not in (None, ''):
        fc = str(status.get('feeCcy') or '')
        if not fc or fc not in result['fees'] or abs(-number(status['fee']) - number(result['fees'][fc])) > Decimal('1e-10'):
            raise EvidenceError('Order-Gebuehr widerspricht Einzel-Fills')
    # A separately reported non-zero rebate needs its own consistent proof.
    if status.get('rebate') not in (None, '', '0') and number(status['rebate']) != 0:
        raise EvidenceError('Separater Order-Rebate verlangt eigenen Belegabgleich')
    result['status'] = dict(status)
    return result
