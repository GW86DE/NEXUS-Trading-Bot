"""Independent ledger buy gate and durable unexplained balance reductions.

The money gate reads the real ledger at every reservation/submission. It does
not trust a previous JSON counter or a successful connection as accounting proof.
Protection and SELL paths are deliberately not gated here.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import json
import hashlib
from ledger_result import confirmed_net
from okx_receipt_math import EvidenceError, number, same_quantity


def _fail_closed(detail):
    return dict(complete=False, gaps=[], blocking=[], blocked_symbols=[], expired=[], detail=detail)


def status_on_connection(con, account, environment):
    if not account or environment not in {'DEMO', 'LIVE'}:
        return _fail_closed('OKX-Kontodomaene fehlt')
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    gaps = []
    if 'trades' in names:
        columns = {r[1] for r in con.execute('PRAGMA table_info(trades)')}
        required={'trade_id','broker','paper','broker_account_fingerprint','ausgestiegen_am','fee_quality','netto_pnl'}
        if not required <= columns:
            return _fail_closed('OKX-Ledgerschema unvollstaendig')
        rows = [dict(r) for r in con.execute('''SELECT * FROM trades WHERE broker='okx' AND paper=?
            AND (broker_account_fingerprint=? OR COALESCE(broker_account_fingerprint,'')='')''',
            (int(environment=='DEMO'), account))]
        from okx_account_context import strict_account
        strict = strict_account(account, environment)
        for row in rows:
            if strict and row.get('broker_account_fingerprint') != account:
                continue
            if row.get('superseded_by') is not None or row.get('reconciliation_status') in {'DISMISSED','ACCOUNT_ASSET_CONFIRMED','LEGACY_AUDIT'}:
                continue
            if row.get('reconciliation_status') == 'BROKER_STATE_UNKNOWN':
                gaps.append(dict(trade_id=row['trade_id'], symbol=row.get('symbol'), kind='BROKER_STATE_UNKNOWN'))
            if row.get('accounting_kind') == 'RESIDUAL':
                # A label alone is not enough; the matching immutable proof must exist.
                proof = None
                if 'okx_residual_inventory' in names:
                    proof = con.execute('''SELECT * FROM okx_residual_inventory WHERE trade_id=? AND account=? AND environment=?
                        AND separate_row=1''', (row['trade_id'],account,environment)).fetchone()
                if (proof and same_quantity(proof['quantity'], row['menge'])
                        and proof['instrument']==row.get('broker_position_id')
                        and proof['entry_order_id']==row.get('entry_order_id')
                        and hashlib.sha256(proof['proof_json'].encode()).hexdigest()==proof['proof_hash']):
                    continue
                gaps.append(dict(trade_id=row['trade_id'],symbol=row.get('symbol'),kind='RESIDUAL_PROOF_MISSING'))
                continue
            # Revalidate an existing reference even after risk already consumed
            # it. A changed proof/row must block, including natively closed trades.
            try:
                from okx_reference_valuation import load_on
                valued = load_on(con,row)
            except (ValueError,KeyError,TypeError,ArithmeticError):
                gaps.append(dict(trade_id=row['trade_id'],symbol=row.get('symbol'),kind='FX_EVIDENCE_INVALID'))
                continue
            if row.get('ausgestiegen_am') and not confirmed_net(row):
                if valued and valued['cross_currency']:
                    continue
                # Explicitly unbound legacy observations are NOT assigned to this account.
                # New/owned/linkable unbound money, however, cannot silently disappear.
                bound = bool(row.get('broker_account_fingerprint'))
                relevant = bound or bool(row.get('entry_order_id') or row.get('decision_id'))
                if relevant:
                    gaps.append(dict(trade_id=row['trade_id'],symbol=row.get('symbol'),
                        kind='RESULT' if bound else 'ACCOUNT_UNKNOWN',currency=row.get('waehrung'),
                        closed_at=row.get('ausgestiegen_am')))
    if 'okx_balance_gaps' in names:
        for row in con.execute("SELECT * FROM okx_balance_gaps WHERE account=? AND environment=? AND status='PENDING'", (account,environment)):
            gaps.append(dict(trade_id=row['trade_id'],symbol=row['instrument'],kind='BALANCE_REDUCTION'))
    if 'okx_accounting_incidents' in names:
        for row in con.execute("SELECT * FROM okx_accounting_incidents WHERE environment=? AND (account=? OR account='') AND status='PENDING'", (environment,account)):
            from okx_account_context import strict_account
            if not row['account'] and strict_account(account, environment):
                continue
            gaps.append(dict(trade_id=None,symbol=row['instrument'],kind='LEDGER_ANCHOR_MISSING'))
    return classify_gaps(gaps)


# 10.7.0 (Entscheidung Georg, 18.09.2026): Jede Luecke traegt Reichweite und
# Ablauf. Bis 10.6.0 sperrte JEDE Luecke die ganze Kontodomaene, unbegrenzt --
# neun Sperrfaelle in zehn Tagen, fast alle wegen eines einzelnen Coins oder
# eines Ergebnisses von vor Tagen. Jetzt gilt:
#   SYMBOL  -> nur dieser Coin ist fuer Neueinstiege gesperrt (Schutzzweck:
#              kein Kauf auf ungeklaertem Bestand DESSELBEN Werts)
#   DOMAIN  -> ganze Kontodomaene (Geld ohne Kontozuordnung)
#   TAGESRESET -> ein unbekanntes Verkaufsergebnis sperrt nur am Verkaufstag
GAP_SCOPE = {
    'BROKER_STATE_UNKNOWN':   ('SYMBOL', 'BELEG'),
    'BALANCE_REDUCTION':      ('SYMBOL', 'BELEG'),
    'RESIDUAL_PROOF_MISSING': ('SYMBOL', 'BELEG'),
    'LEDGER_ANCHOR_MISSING':  ('SYMBOL', 'BELEG'),
    'FX_EVIDENCE_INVALID':    ('SYMBOL', 'BELEG'),
    'RESULT':                 ('DOMAIN', 'TAGESRESET'),
    'ACCOUNT_UNKNOWN':        ('DOMAIN', 'BELEG'),
}
GAP_RESOLUTION = {
    'BROKER_STATE_UNKNOWN':   'Bestand in zwei bestaetigten Messungen wieder da oder Verkaufsbeleg',
    'BALANCE_REDUCTION':      'Verkaufsbeleg (auch mit Lot-Rest) oder Bestand wieder da',
    'RESIDUAL_PROOF_MISSING': 'Restbeleg aus Original-Fills (okx_residual_inventory)',
    'LEDGER_ANCHOR_MISSING':  'belegbasierte Reparatur des Ledgerankers',
    'FX_EVIDENCE_INVALID':    'gueltige EUR-Referenzbewertung',
    'RESULT':                 'Nettoergebnis (EUR-Referenzbewertung) oder Tageswechsel',
    'ACCOUNT_UNKNOWN':        'Kontozuordnung des Geldes belegen',
}


def _base_symbol(value):
    text = str(value or '').upper()
    return text.split('-', 1)[0] if text else ''


def _heute_lokal():
    from zoneinfo import ZoneInfo
    import config
    zone = ZoneInfo(str(getattr(config, 'LOCAL_TIMEZONE', 'Europe/Berlin')))
    return datetime.now(timezone.utc).astimezone(zone).date()


def classify_gaps(gaps, *, today=None):
    """Reichweite und Ablauf je Luecke; eine einzige Entscheidung fuer alle Leser."""
    today = today or _heute_lokal()
    blocking_domain, blocked_symbols, expired = [], set(), []
    for gap in gaps:
        scope, ablauf = GAP_SCOPE.get(gap.get('kind'), ('DOMAIN', 'BELEG'))
        gap['scope'] = scope
        gap['ablauf'] = ablauf
        gap['aufloesung'] = GAP_RESOLUTION.get(gap.get('kind'), 'Beleg')
        gap['base'] = _base_symbol(gap.get('symbol'))
        if ablauf == 'TAGESRESET':
            closed = str(gap.get('closed_at') or '')
            try:
                from zoneinfo import ZoneInfo
                import config
                zone = ZoneInfo(str(getattr(config, 'LOCAL_TIMEZONE', 'Europe/Berlin')))
                tag = datetime.fromisoformat(closed.replace('Z', '+00:00')).astimezone(zone).date()
            except (ValueError, TypeError):
                tag = None
            if tag is not None and tag < today:
                gap['sperrt'] = False
                expired.append(gap)
                continue
        gap['sperrt'] = True
        if scope == 'SYMBOL' and gap['base']:
            blocked_symbols.add(gap['base'])
        else:
            blocking_domain.append(gap)
    symbole = sorted(blocked_symbols)
    if blocking_domain:
        detail = (f"{len(blocking_domain)} OKX-Buchungsbeleg(e) offen; neue Kaeufe dieser Kontodomaene gesperrt"
                  + (f"; zusaetzlich {len(symbole)} Coin(s) einzeln gesperrt: {', '.join(symbole)}" if symbole else ''))
    elif symbole:
        detail = (f"{len(symbole)} Coin(s) mit offenem Bestandsbeleg fuer Neueinstiege gesperrt: "
                  f"{', '.join(symbole)}; alle anderen Kaeufe frei")
    else:
        detail = 'Kontogebundene OKX-Abschluesse und Bestandsabgaenge belegt'
        if expired:
            detail += f' ({len(expired)} aeltere Ergebnisbelege offen, nicht mehr sperrend)'
    return dict(complete=not blocking_domain, gaps=gaps, blocking=blocking_domain,
                blocked_symbols=symbole, expired=expired, detail=detail)


def status(account, environment):
    import trade_ledger as tl
    try:
        tl.init_ledger()
        with closing(tl._connect()) as con, con:
            return status_on_connection(con, account, environment)
    except Exception as exc:
        return _fail_closed('OKX-Buchung nicht pruefbar: '+type(exc).__name__)


def require_complete(con, account, environment):
    from broker.base import BrokerFehler
    try:
        result=status_on_connection(con,account,environment)
    except Exception as exc:
        raise BrokerFehler('OKX-Buchung nicht pruefbar: '+type(exc).__name__) from exc
    if not result['complete']:
        raise BrokerFehler(result['detail'])


def require_tradable(con, account, environment, instrument):
    """Domaenensperre ODER Symbolsperre fuer genau diesen Coin (10.7.0)."""
    from broker.base import BrokerFehler
    try:
        result=status_on_connection(con,account,environment)
    except Exception as exc:
        raise BrokerFehler('OKX-Buchung nicht pruefbar: '+type(exc).__name__) from exc
    if not result['complete']:
        raise BrokerFehler(result['detail'])
    base = _base_symbol(instrument)
    if base and base in set(result.get('blocked_symbols') or []):
        gruende = sorted({g['kind'] for g in result['gaps'] if g.get('sperrt') and g.get('base') == base})
        raise BrokerFehler(f"{base}: offener Bestandsbeleg ({', '.join(gruende)}); "
                           "kein Neueinstieg in diesen Coin, andere Kaeufe frei")


def mark_balance_gap(row, observed, tracked):
    import trade_ledger as tl
    from okx_residual_inventory import scope
    ctx=scope(row);observed=number(observed);tracked=number(tracked,positive=True)
    if observed < 0 or observed >= tracked:
        raise EvidenceError('Kein bestaetigter Mengenrueckgang')
    tl.init_ledger();now=datetime.now(timezone.utc).isoformat()
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        current=con.execute('SELECT * FROM trades WHERE trade_id=?',(row['trade_id'],)).fetchone()
        if not current or current['entry_order_id']!=ctx['oid'] or current['broker_account_fingerprint']!=ctx['account'] or int(current['paper'])!=int(ctx['environment']=='DEMO'):
            raise EvidenceError('Bestandsluecke ohne passende Ledgerzeile')
        con.execute('''INSERT INTO okx_balance_gaps VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trade_id) DO UPDATE SET observed_balance=excluded.observed_balance,
            status='PENDING',last_seen=excluded.last_seen,resolution='' ''',
            (row['trade_id'],ctx['account'],ctx['environment'],ctx['inst'],ctx['oid'],str(tracked),str(observed),'PENDING',now,now,''))


def resolve_balance_gap_on(con, *, trade_id, account, environment, sold, residual=0,
                           exit_ids=(), reason='EXACT_EXIT_FILLS'):
    """Die EINE Aufloesung einer Bestandsluecke durch Verkauf (10.7.0).

    Bis 10.6.0 rechneten drei Stellen dieselbe Formel getrennt und alle drei
    ohne den Lot-Rest (trade_ledger.trade_close, okx_external_settlement,
    okx_closed_reconciliation). Jetzt gibt es genau diese Funktion; die
    Mengenfrage beantwortet ``okx_receipt_math.quantity_explained``.

    ``residual`` ist der Rest, der nach dem Verkauf als eigene Ledgerzeile
    weiterlaeuft. Er ist erklaert, nicht verschwunden. Rueckgabe: ob eine
    PENDING-Luecke geschlossen wurde.
    """
    from okx_receipt_math import quantity_explained
    gap = con.execute(
        "SELECT * FROM okx_balance_gaps WHERE trade_id=? AND account=? AND environment=? AND status='PENDING'",
        (int(trade_id), str(account), str(environment))).fetchone()
    if not gap:
        return False
    unexplained = number(gap['tracked_quantity']) - number(gap['observed_balance'])
    if not quantity_explained(unexplained, sold, residual):
        return False
    now = datetime.now(timezone.utc).isoformat()
    marker = f"{reason}:{json.dumps(sorted(str(x) for x in exit_ids))}"
    if residual not in (None, '', 0) and number(residual) > 0:
        marker += f":residual={number(residual)}"
    con.execute("UPDATE okx_balance_gaps SET status='RESOLVED',resolution=?,last_seen=? WHERE trade_id=?",
                (marker, now, int(trade_id)))
    return True


def _ist_rest_split(row):
    """Offene Zeile derselben Einstiegskette, die einen weitergefuehrten Rest darstellt.

    Erkennung an der Struktur: keine eigenen Einstiegs-Fills (die gehoeren der
    Kopfzeile) ODER Notiz 'Rest nach Teilverkauf' ODER als Staub/Restbestand
    klassifiziert (RESIDUAL_EXPOSURE, accounting_kind RESIDUAL). Eine offene
    Zeile MIT eigenen Fills ist eine eigene Position und nie ein Rest.
    """
    try:
        fills = json.loads(row.get('entry_fill_ids_json') or '[]')
    except (TypeError, ValueError):
        fills = ['unlesbar']
    eigene_fills = bool(fills) or bool(str(row.get('entry_fill_id') or '').strip())
    if not eigene_fills:
        return True
    if str(row.get('notiz') or '').startswith('Rest nach Teilverkauf'):
        return True
    return (str(row.get('accounting_kind') or 'TRADE').upper() == 'RESIDUAL'
            or str(row.get('reconciliation_status') or '').upper() == 'RESIDUAL_EXPOSURE')


def repair_gaps_explained_by_lineage(con, account, environment):
    """Schliesst PENDING-Luecken, deren Trade laengst verkauft und verbucht ist.

    Fall vom 18.09.2026: Die Luecken von XRP/BTC/ETH entstanden um 02:34 UTC
    aus zwei unvollstaendigen Schnappschuessen. Um 13:47-13:51 UTC verkauften
    die Take-Profit-Orders die Positionen; die Trades wurden mit exakten Fills
    geschlossen, die Lot-Reste liefen als Zeilen 84-86 weiter. Die Luecken
    blieben trotzdem PENDING, weil die Aufloesung den Rest nicht kannte.

    Beweis hier: Die Summe der Mengen aller Zeilen derselben Einstiegskette
    (Verkauf + weitergefuehrte Reste) deckt den gemeldeten Abgang. Eine noch
    offene Zeile zaehlt nur, wenn sie ein Rest-Split dieser Kette ist oder
    selbst geschlossen ist.

    10.7.1: Der Rest-Split wird an seiner STRUKTUR erkannt, nicht am Notiztext.
    ``trade_close`` legt den Rest ohne eigene Einstiegs-Fills an (die Fills
    gehoeren der Kopfzeile) und schreibt 'Rest nach Teilverkauf'; der
    Positionsabgleich benennt dieselbe Zeile spaeter in RESIDUAL_EXPOSURE um
    ("Coin-Guthaben vorhanden, aber kein Eintrag im OKX-Positionsbuch"), und
    ein Staubrest kann als eigene RESIDUAL-Zeile gefuehrt werden. Am 18.09.2026
    trugen die Reste 84-86 genau diese Umbenennung -- 10.7.0 hielt sie fuer
    fremde offene Zeilen und liess die Luecken 81-83 stehen.
    Rueckgabe: Liste der geschlossenen trade_ids.
    """
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'okx_balance_gaps' not in names or 'trades' not in names:
        return []
    repaired = []
    gaps = [dict(r) for r in con.execute(
        "SELECT * FROM okx_balance_gaps WHERE account=? AND environment=? AND status='PENDING'",
        (str(account), str(environment)))]
    for gap in gaps:
        head = con.execute("SELECT * FROM trades WHERE trade_id=?", (gap['trade_id'],)).fetchone()
        if not head or not head['ausgestiegen_am']:
            continue                      # nie verkauft: das ist keine Lot-Frage
        try:
            exit_ids = json.loads(head['exit_fill_ids_json'] or '[]')
        except (TypeError, ValueError):
            exit_ids = []
        if not exit_ids and not head['exit_order_id']:
            continue                      # geschlossen ohne Verkaufsbeleg: bleibt offen
        lineage = [dict(r) for r in con.execute(
            """SELECT * FROM trades WHERE broker='okx' AND broker_position_id=? AND entry_order_id=?
               AND broker_account_fingerprint=? AND paper=? AND superseded_by IS NULL""",
            (gap['instrument'], gap['entry_order_id'], str(account), int(environment == 'DEMO')))]
        sold = Decimal(0); residual = Decimal(0); ok = True
        for row in lineage:
            try:
                menge = number(row['menge'])
            except EvidenceError:
                ok = False; break
            if row['ausgestiegen_am']:
                sold += menge
            elif _ist_rest_split(row):
                residual += menge
            elif int(row['trade_id']) != int(gap['trade_id']):
                ok = False; break         # offene Zeile mit eigenen Fills: kein Rest, kein Beweis
        if not ok:
            continue
        if resolve_balance_gap_on(con, trade_id=gap['trade_id'], account=account, environment=environment,
                                  sold=sold, residual=residual, exit_ids=exit_ids,
                                  reason='LOT_RESIDUAL_LINEAGE'):
            repaired.append(int(gap['trade_id']))
    return repaired


def repair_explained_gaps(account, environment):
    """Schreibender Reparaturlauf fuer den Bot-Prozess (nicht fuer WebUI/Diagnose)."""
    import trade_ledger as tl
    if not account or environment not in {'DEMO', 'LIVE'}:
        return []
    tl.init_ledger()
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        return repair_gaps_explained_by_lineage(con, account, environment)


def resolve_balance_gap_restored(row, observed, *, detail=''):
    """Close a balance gap because the holding itself is measurably back.

    WARUM ES DIESEN WEG GEBEN MUSS (10.6.0)
    ---------------------------------------
    Bis 10.5.0 fuehrten alle drei Aufloesungswege einer Bestandsluecke ueber
    einen VERKAUF: exakte Exit-Fills (trade_ledger), die Abrechnung extern
    geschlossener Positionen (okx_closed_reconciliation) und die manuelle
    Aussenbuchung (okx_external_settlement). Fuer den Fall "der Bestand war
    nie weg, nur zwei Guthaben-Schnappschuesse waren unvollstaendig" gab es
    keinen Rueckweg.

    Am 18.09.2026 hat genau das drei OKX-Positionen (XRP, BTC, ETH)
    dauerhaft gesperrt: zwei unvollstaendige Schnappschuesse im Abstand von
    neun Sekunden erzeugten drei Bestandsluecken, obwohl Guthaben und
    Schutzorders die Mengen unveraendert auswiesen. Sechs offene Belege
    sperrten danach jeden weiteren OKX-Kauf.

    Hier wird KEIN Verkauf erfunden. Der Beleg ist der erneut gemessene
    Bestand: Er muss die gebuchte Menge wieder erreichen, und Menge sowie
    Zeitpunkt der Messung bleiben in ``resolution`` nachlesbar.
    """
    import trade_ledger as tl
    from okx_residual_inventory import scope
    ctx = scope(row)
    observed = number(observed, positive=True)
    tracked = number(row['menge'], positive=True)
    if observed < tracked:
        raise EvidenceError('Bestand ist nicht wieder vollstaendig')
    tl.init_ledger()
    now = datetime.now(timezone.utc).isoformat()
    resolution = f'BALANCE_RESTORED:{observed}@{now}'
    if detail:
        resolution = f'{resolution}:{str(detail)[:160]}'
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        current = con.execute('SELECT * FROM trades WHERE trade_id=?',
                              (row['trade_id'],)).fetchone()
        if (not current or current['entry_order_id'] != ctx['oid']
                or current['broker_account_fingerprint'] != ctx['account']
                or int(current['paper']) != int(ctx['environment'] == 'DEMO')):
            raise EvidenceError('Bestandsrueckkehr ohne passende Ledgerzeile')
        if current['ausgestiegen_am']:
            # Eine geschlossene Zeile wird nie ueber diesen Weg wiederbelebt.
            raise EvidenceError('Trade ist bereits ausgestiegen')
        cur = con.execute(
            """UPDATE okx_balance_gaps SET status='RESOLVED',resolution=?,
               observed_balance=?,last_seen=?
               WHERE trade_id=? AND account=? AND environment=? AND status='PENDING'""",
            (resolution, str(observed), now, row['trade_id'], ctx['account'],
             ctx['environment']))
        return bool(cur.rowcount)


def sync_unknown_results(state, broker):
    """Catch missing risk receipts after restart; never invent a historical FX rate."""
    import trade_ledger as tl
    account=str(broker.account_fingerprint() or '')
    env='DEMO' if broker.demo else 'LIVE'
    result=status(account,env)
    for gap in result['gaps']:
        if gap['kind'] != 'RESULT':
            continue
        row=tl.trade_detail(gap['trade_id'])
        if row and row.get('broker_account_fingerprint')==account and bool(row['paper'])==bool(broker.demo):
            state.register_unknown_pnl_at('ledger:'+str(row['trade_id']),row['ausgestiegen_am'])
    return result


def broker_status(broker):
    """Missing adapter identity is UNKNOWN, never implicit LIVE or complete."""
    try:
        account = str(broker.account_fingerprint() or '')
        demo = broker.demo
        if not isinstance(demo, bool):
            raise EvidenceError('Brokerumgebung fehlt')
        return status(account, 'DEMO' if demo else 'LIVE')
    except Exception as exc:
        return _fail_closed('OKX-Buchungsdomaene nicht pruefbar: '+type(exc).__name__)


def mark_unanchored(position, observed):
    """Quarantine unaccounted inventory without guessing an owner or a sale.

    This persistent incident intentionally requires an exact receipt-based repair;
    an unrelated later balance/connection cannot clear it.
    """
    import trade_ledger as tl
    ctx=dict(account=str(position.account_fingerprint or ''),
        environment='DEMO' if position.paper else 'LIVE',instrument=str(position.inst_id),
        entry_order_id=str(position.order_id or ''))
    identity=json.dumps(ctx,sort_keys=True)
    payload=dict(**ctx,tracked=str(number(position.menge,positive=True)),
        observed=(str(number(observed)) if observed is not None else None),client_order_id=str(position.client_order_id or ''),
        reason='Mengenabgang ohne eindeutigen Ledgeranker; keine Verkaufsbuchung')
    tid=hashlib.sha256(identity.encode()).hexdigest()
    tl.init_ledger();now=datetime.now(timezone.utc).isoformat()
    with tl._LOCK,closing(tl._connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        con.execute("""INSERT INTO okx_accounting_incidents VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(incident_id) DO UPDATE SET status='PENDING',last_seen=excluded.last_seen""",
            (tid,ctx['account'],ctx['environment'],ctx['instrument'],ctx['entry_order_id'],
             'PENDING',json.dumps(payload,sort_keys=True),now,now))
    return tid
