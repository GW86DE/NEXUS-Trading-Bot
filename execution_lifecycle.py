"""Durable primary-order lifecycle shared by OKX and eToro.

Client identity is committed before submission. Status, received fills and
local accounting are separate facts. All legacy position/strategy modules
remain consumers; none may release a possibly submitted order by timeout.
No network or credentials are owned by this module.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from datetime import datetime, timezone

from broker.base import BrokerFehler, OrderStatusUnklar


class ExecutionConflict(BrokerFehler):
    """Contradictory immutable order or execution evidence."""


POSITION_CLOSED_ORDER_UNPROVEN = "POSITION_CLOSED_ORDER_UNPROVEN"
ACTIVE_SQL = "(terminal=0 OR evidence_complete=0 OR (CAST(filled AS REAL)>0 AND accounted=0)) AND state<>'POSITION_CLOSED_ORDER_UNPROVEN'"


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ExecutionConflict("Ungueltiger Ausfuehrungswert") from None
    if not result.is_finite():
        raise ExecutionConflict("Nicht endlicher Ausfuehrungswert")
    return result


def result_state(result, requested=None):
    """A partial execution is never relabelled as a fully filled order."""
    raw = str(getattr(result, "raw_status", "") or getattr(result, "status", "") or "").upper().replace(" ", "_")
    gross = getattr(result, "gross_filled_quantity", None)
    if gross in (None, 0, 0.0):
        gross = getattr(result, "filled_quantity", 0)
    qty = number(gross or 0)
    target = number(requested if requested is not None else getattr(result, "requested_quantity", 0) or 0)
    remaining = number(getattr(result, "remaining_quantity", 0) or 0)
    if min(qty, target, remaining) < 0:
        raise ExecutionConflict("Negative Ordermenge")
    terminal = bool(getattr(result, "terminal", False))
    if qty > 0:
        partial = remaining > Decimal('0.000000000001') or (target > 0 and qty < target - max(Decimal('0.000000000001'), target * Decimal('0.000000001')))
        if partial or "PARTIAL" in raw:
            return "PARTIALLY_FILLED_CANCELED" if terminal else "PARTIALLY_FILLED"
        return "FILLED" if terminal else "AWAITING_TERMINAL"
    if terminal:
        if raw in {"CANCELED", "CANCELLED", "MMP_CANCELED", "EXPIRED", "NICHT_AUSGEFUEHRT"}:
            return "CANCELED"
        if raw in {"REJECTED", "FAILED"}:
            return "REJECTED"
        # 'filled' with zero units is contradictory, not a zero-fill proof.
        return "UNCLEAR"
    return "OPEN" if raw in {"LIVE", "OPEN", "ACCEPTED", "SUBMITTED"} else "UNCLEAR"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from decision_analytics import _connect as connect
    con = connect()
    con.execute("PRAGMA synchronous=FULL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS execution_orders (
            order_key TEXT PRIMARY KEY, broker TEXT NOT NULL,
            account TEXT NOT NULL, environment TEXT NOT NULL,
            instrument TEXT NOT NULL, side TEXT NOT NULL,
            position_id TEXT NOT NULL DEFAULT '', client_id TEXT NOT NULL,
            order_id TEXT NOT NULL DEFAULT '', requested TEXT NOT NULL,
            state TEXT NOT NULL, terminal INTEGER NOT NULL DEFAULT 0,
            evidence_complete INTEGER NOT NULL DEFAULT 0,
            filled TEXT NOT NULL DEFAULT '0', net_filled TEXT NOT NULL DEFAULT '0',
            accounted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            request_json TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(broker,account,environment,client_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS execution_exchange_id
            ON execution_orders(broker,account,environment,instrument,order_id)
            WHERE order_id<>'';
        CREATE TABLE IF NOT EXISTS execution_fills (
            order_key TEXT NOT NULL REFERENCES execution_orders(order_key),
            fill_id TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL,
            fee TEXT, fee_currency TEXT NOT NULL, raw_json TEXT NOT NULL,
            PRIMARY KEY(order_key,fill_id)
        );
        CREATE TABLE IF NOT EXISTS execution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_key TEXT NOT NULL,
            kind TEXT NOT NULL, created_at TEXT NOT NULL, detail_json TEXT NOT NULL
        );
    """)
    # Schema checks and ALTER must share the same write transaction across
    # processes; otherwise two simultaneous starts both add the same column.
    try:
        con.execute('BEGIN IMMEDIATE')
        columns = {r[1] for r in con.execute('PRAGMA table_info(execution_orders)')}
        for column in ('last_checked_at', 'check_detail'):
            if column not in columns:
                con.execute(f"ALTER TABLE execution_orders ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        con.commit()
    except BaseException:
        con.rollback()
        con.close()
        raise
    return con


def _event(con, key, kind, detail):
    con.execute("INSERT INTO execution_events(order_key,kind,created_at,detail_json) VALUES(?,?,?,?)",
                (key, kind, _now(), json.dumps(detail, sort_keys=True, default=str)))


def reserve(*, broker, account, environment, instrument, side, client_id,
            quantity, request, position_id="", prepared=False):
    """Reserve exactly one operation; any existing identity requires lookup."""
    broker, environment, side = str(broker).lower(), str(environment).upper(), str(side).upper()
    if broker not in {'okx', 'etoro'} or environment not in {'DEMO', 'LIVE'} or side not in {'BUY', 'SELL'}:
        raise ExecutionConflict("Broker-/Umgebungs-/Orderseite nicht eindeutig")
    if not all(str(x).strip() for x in (account, instrument, client_id)) or number(quantity) <= 0:
        raise ExecutionConflict("Order ohne vollstaendige Identitaet oder Menge blockiert")
    if broker == 'okx':
        from okx_account_context import require_active
        require_active(str(account), environment)
    domain = (broker, str(account), environment)
    key = hashlib.sha256(json.dumps([*domain, str(client_id)]).encode()).hexdigest()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if broker == 'okx' and side == 'BUY':
            # 10.7.0: Domaenensperre ODER Symbolsperre genau dieses Coins.
            from okx_accounting import require_tradable
            require_tradable(con, str(account), environment, str(instrument))
        row = con.execute("SELECT * FROM execution_orders WHERE order_key=?", (key,)).fetchone()
        if row is not None:
            if (row['instrument'] != str(instrument) or row['side'] != side
                    or number(row['requested']) != number(quantity)
                    or row['position_id'] != str(position_id)):
                raise ExecutionConflict("Client-ID wird fuer einen anderen Auftrag wiederverwendet")
        else:
            # BUY waits for all unresolved operations of this instrument.
            # SELL is position-specific on eToro, instrument-specific on OKX.
            rows = con.execute("""SELECT * FROM execution_orders WHERE broker=? AND account=?
                AND environment=? AND instrument=? AND
                ((terminal=0 OR evidence_complete=0 OR (CAST(filled AS REAL)>0 AND accounted=0))
                 OR (broker='etoro' AND state='CANCELED'))""",
                (*domain, str(instrument))).fetchall()
            row = next((r for r in rows if
                        (broker != 'etoro' or r['state'] not in {POSITION_CLOSED_ORDER_UNPROVEN, 'CANCELED'} or
                         (side == 'SELL' and r['position_id'] == str(position_id))) and
                        (broker == 'okx' or side == 'BUY' or
                        (r['side'] == side and (broker == 'okx' or r['position_id'] == str(position_id))))), None)
        if row is not None:
            raise OrderStatusUnklar("Bestehender Auftrag muss zuerst abgeglichen werden; kein zweiter POST.",
                reference_id=row['client_id'], order_ids=[row['order_id']] if row['order_id'] else [], accepted=True)
        now = _now()
        con.execute("""INSERT INTO execution_orders(order_key,broker,account,environment,instrument,
            side,position_id,client_id,requested,state,created_at,updated_at,request_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, *domain, str(instrument), side, str(position_id), str(client_id),
             str(number(quantity)), 'PREPARED' if prepared else 'SUBMITTING',
             now, now, json.dumps(request, sort_keys=True, default=str)))
        _event(con, key, 'PREPARED' if prepared else 'SUBMITTING', {})
    return key


def begin_submission(key, request):
    """Commit the final payload exactly once before POST; a stale owner cannot send."""
    with _connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM execution_orders WHERE order_key=?', (key,)).fetchone()
        if not row or row['state'] != 'PREPARED' or row['terminal']:
            raise ExecutionConflict('Vorbereiteter Auftrag ist nicht mehr zur Uebermittlung frei')
        if row['broker'] == 'okx':
            from okx_account_context import require_active
            require_active(row['account'], row['environment'])
        if row['broker'] == 'okx' and row['side'] == 'BUY':
            from okx_accounting import require_tradable
            require_tradable(con, row['account'], row['environment'], row['instrument'])
        if (request.get('instId') != row['instrument']
                or str(request.get('side', '')).upper() != row['side']
                or request.get('clOrdId') != row['client_id']
                or not 0 < number(request.get('sz')) <= number(row['requested'])):
            raise ExecutionConflict('Endgueltiger Auftrag widerspricht der Reservierung')
        con.execute("UPDATE execution_orders SET state='SUBMITTING',requested=?,request_json=?,updated_at=? WHERE order_key=?",
                    (str(number(request['sz'])), json.dumps(request, sort_keys=True), _now(), key))
        _event(con, key, 'SUBMITTING', {'final_payload_committed': True})


def abandon_prepared(key):
    """Only PREPARED proves no POST could occur. Never release SUBMITTING."""
    with _connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT state FROM execution_orders WHERE order_key=?', (key,)).fetchone()
        if not row or row['state'] != 'PREPARED':
            return False
        con.execute("UPDATE execution_orders SET state='REJECTED',terminal=1,evidence_complete=1,updated_at=? WHERE order_key=?",
                    (_now(), key))
        _event(con, key, 'NOT_SUBMITTED', {'proof': 'PREPARED; no SUBMITTING transition'})
        return True


def lookup(*, broker, account, environment, client_id):
    with _connect() as con:
        row = con.execute('SELECT * FROM execution_orders WHERE broker=? AND account=? AND environment=? AND client_id=?',
                          (str(broker).lower(), str(account), str(environment), str(client_id))).fetchone()
        return dict(row) if row else None


def accepted(key, order_id=""):
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM execution_orders WHERE order_key=?", (key,)).fetchone()
        if not row:
            raise ExecutionConflict("Orderreservierung fehlt")
        if row['order_id'] and order_id and row['order_id'] != str(order_id):
            raise ExecutionConflict("Bestaetigung nennt eine andere Order-ID")
        if not row['terminal']:
            # An ACK can arrive after a partial execution. It is transport
            # evidence, not permission to erase the execution projection.
            con.execute("UPDATE execution_orders SET state=CASE WHEN state IN ('SUBMITTING','UNCLEAR') THEN 'OPEN' ELSE state END,order_id=CASE WHEN ?<>'' THEN ? ELSE order_id END,updated_at=? WHERE order_key=?",
                        (str(order_id), str(order_id), _now(), key))
            _event(con, key, 'ACCEPTED', {'order_id': str(order_id)})


def rejected(key, *, proven=False, evidence=None):
    with _connect() as con:
        state = 'REJECTED' if proven else 'UNCLEAR'
        con.execute("UPDATE execution_orders SET state=?,terminal=?,evidence_complete=?,updated_at=? WHERE order_key=? AND terminal=0 AND CAST(filled AS REAL)=0",
                    (state, int(proven), int(proven), _now(), key))
        _event(con, key, state, {k: v for k, v in (evidence or {}).items() if k in {'broker_code', 'reason_code', 'action_required'}})


def observe(result, *, broker, instrument=""):
    """Merge an exact order result. Missing identities never create ownership."""
    account = str(getattr(result, 'account_fingerprint', '') or '')
    env = str(getattr(result, 'broker_environment', '') or '')
    client = str(getattr(result, 'client_order_id', '') or getattr(result, 'reference_id', '') or '')
    ids = [str(x) for x in getattr(result, 'order_ids', []) or [] if x]
    if not account or env not in {'DEMO', 'LIVE'}:
        return 0
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute("SELECT * FROM execution_orders WHERE broker=? AND account=? AND environment=?",
                           (str(broker).lower(), account, env)).fetchall()
        matches = [r for r in rows if (client and r['client_id'] == client) or (r['order_id'] and r['order_id'] in ids)]
        if not matches:
            return 0  # Existing pre-upgrade executions remain in their original ledger.
        if len(matches) != 1:
            raise ExecutionConflict("Orderergebnis passt zu mehreren reservierten Auftraegen")
        row = matches[0]; key = row['order_key']
        if client and client != row['client_id']:
            raise ExecutionConflict("Orderergebnis mit widerspruechlicher Client-ID")
        if instrument and str(instrument) != row['instrument']:
            raise ExecutionConflict("Orderergebnis mit widerspruechlichem Instrument")
        if len(set(ids)) > 1:
            raise ExecutionConflict("Ein Auftragsbeleg enthaelt mehrere primaere Order-IDs")
        order_id = ids[0] if ids else row['order_id']
        if row['order_id'] and order_id != row['order_id']:
            raise ExecutionConflict("Orderergebnis mit widerspruechlicher Order-ID")
        for raw in getattr(result, 'fills', []) or []:
            fid = str(raw.get('tradeId') or raw.get('fill_id') or '')
            if not fid:
                raise ExecutionConflict("Ausfuehrungsbeleg ohne Fill-ID")
            for field, expected in (('ordId', order_id), ('instId', row['instrument']), ('side', row['side'].lower())):
                if raw.get(field) not in (None, '', expected):
                    raise ExecutionConflict("Fill widerspricht der Orderidentitaet: " + field)
            q = number(raw.get('fillSz', raw.get('quantity')))
            px = number(raw.get('fillPx', raw.get('price')))
            if q <= 0 or px <= 0:
                raise ExecutionConflict("Fill ohne positive Menge/Preis")
            fee = None if raw.get('fee') in (None, '') else str(number(raw['fee']))
            ccy = str(raw.get('feeCcy') or '')
            previous = con.execute("SELECT * FROM execution_fills WHERE order_key=? AND fill_id=?", (key, fid)).fetchone()
            if previous:
                if (number(previous['quantity']) != q or number(previous['price']) != px
                        or (previous['fee'] is not None and fee is not None and number(previous['fee']) != number(fee))
                        or (previous['fee_currency'] and ccy and previous['fee_currency'] != ccy)):
                    raise ExecutionConflict("Wiederholter Fill widerspricht dem gespeicherten Beleg")
                # A late fee may enrich, never overwrite, existing evidence.
                con.execute("UPDATE execution_fills SET fee=COALESCE(fee,?),fee_currency=CASE WHEN fee_currency='' THEN ? ELSE fee_currency END WHERE order_key=? AND fill_id=?",
                            (fee, ccy, key, fid))
            else:
                con.execute("INSERT INTO execution_fills VALUES(?,?,?,?,?,?,?)",
                            (key, fid, str(q), str(px), fee, ccy, json.dumps(raw, sort_keys=True, default=str)))
        filled = number(getattr(result, 'gross_filled_quantity', 0) or getattr(result, 'filled_quantity', 0) or 0)
        net = number(getattr(result, 'filled_quantity', 0) or 0)
        requested = number(row['requested'])
        if filled < 0 or net < 0 or filled > requested + max(Decimal('1e-12'), requested*Decimal('1e-9')):
            raise ExecutionConflict("Ausgefuehrte Bruttomenge widerspricht dem reservierten Auftrag")
        previous_filled = number(row['filled'])
        total = sum((number(r[0]) for r in con.execute("SELECT quantity FROM execution_fills WHERE order_key=?", (key,))), Decimal(0))
        tolerance = max(Decimal('1e-12'), max(filled, previous_filled) * Decimal('1e-9'))
        if total > max(filled, previous_filled) + tolerance:
            raise ExecutionConflict("Native Fillbelege uebersteigen die kumulierte Ordermenge")
        if filled < previous_filled:
            _event(con, key, 'STALE_RESULT_IGNORED', {'filled': str(filled)})
            return 1
        state = result_state(result, row['requested'])
        terminal = bool(getattr(result, 'terminal', False)) and state != 'UNCLEAR'
        complete = bool(getattr(result, 'fill_evidence_complete', False))
        if complete and filled > 0 and abs(total-filled) > max(Decimal('1e-12'),filled*Decimal('1e-9')):
            raise ExecutionConflict("Ordermenge und einzelne Fillbelege widersprechen sich")
        if row['terminal']:
            if filled == previous_filled:
                # Repeated REST/WS summaries can omit native fills and fees.
                # Retain the verified projection only for the SAME quantity;
                # a complete, validated fee correction may change net quantity.
                terminal = True
                state = row['state']
            else:
                # A delayed native fill may complete a previously canceled
                # remainder. Cancel never erases executions; no timer or retry
                # counter can take this path. A rejected order is a conflict.
                if row['state'] == 'REJECTED' or abs(total-filled) > tolerance:
                    raise ExecutionConflict("Spaeter Fill ohne passenden terminalen Orderbeleg")
                terminal = True
                state = ('FILLED' if abs(filled-requested) <= tolerance
                         else 'PARTIALLY_FILLED_CANCELED')
        preserve_evidence = bool(row['evidence_complete'] and filled == previous_filled
                                 and abs(total-filled) <= tolerance and not complete)
        if preserve_evidence:
            # An incomplete snapshot does not prove a correction of base fees.
            # Store the observation in the event journal, keep the last proven
            # net quantity and accounting receipt until full evidence arrives.
            net = number(row['net_filled'])
            complete = True
        evidence_json = (row['evidence_json'] if preserve_evidence else
                         json.dumps(getattr(result,'execution_evidence',{}) or {},
                                    sort_keys=True,default=str))
        accounted = (int(row['accounted']) if filled == previous_filled
                     and net == number(row['net_filled']) else 0)
        con.execute("""UPDATE execution_orders SET order_id=?,state=?,terminal=?,evidence_complete=?,
            filled=?,net_filled=?,accounted=?,
            updated_at=?,evidence_json=? WHERE order_key=?""",
            (order_id,state,int(terminal),int(complete),str(filled),str(net),accounted,_now(),
             evidence_json,key))
        _event(con, key, state, {'filled': str(filled), 'terminal': terminal,
            'evidence_complete': complete, 'preserved_stronger_evidence': preserve_evidence,
            'observed_evidence_complete': bool(getattr(result, 'fill_evidence_complete', False))})
        return 1


def confirm_accounted(*, broker, account, environment, order_id, client_id=""):
    """Confirm from an existing durable receipt, e.g. during restart recovery."""
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        confirm_accounted_on(con, broker=broker, account=account,
            environment=environment, order_id=order_id, client_id=client_id)


def confirm_accounted_on(con, *, broker, account, environment, order_id, client_id=""):
    """Project an exact ledger receipt in the CALLER'S SQLite transaction.

    No commit, new connection, schema change or network access is performed.
    A legacy ledger without an execution table remains a legacy ledger.
    """
    if not con.in_transaction:
        raise ExecutionConflict("Lifecyclequittung braucht eine Ledgertransaktion")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_orders'").fetchone():
        return
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'").fetchone():
        return
    rows = con.execute("SELECT * FROM execution_orders WHERE broker=? AND account=? AND environment=? AND ((order_id<>'' AND order_id=?) OR (client_id<>'' AND client_id=?))",
                (str(broker).lower(),str(account),str(environment),str(order_id),str(client_id))).fetchall()
    for row in rows:
        column = 'entry_order_id' if row['side']=='BUY' else 'exit_order_id'
        identity = row['order_id']
        if not identity:
            # Empty broker IDs are not a shared order identity. For entries
            # the ledger also persists the exact client ID; exits need their
            # own broker order ID before a receipt can release the reservation.
            if row['side'] != 'BUY' or not row['client_id']:
                continue
            column, identity = 'client_order_id', row['client_id']
        entries = con.execute(f"SELECT menge FROM trades WHERE broker=? AND broker_account_fingerprint=? AND paper=? AND {column}=? AND (broker_position_id=? OR ?=1) AND superseded_by IS NULL" +
            (" AND ausgestiegen_am IS NOT NULL" if row['side']=='SELL' else ''),
            (row['broker'],row['account'],int(row['environment']=='DEMO'),identity,row['instrument'] if row['broker']=='okx' else row['position_id'],int(row['broker']=='etoro' and row['side']=='BUY'))).fetchall()
        total = sum((number(r[0]) for r in entries),Decimal(0))
        expected = number(row['net_filled'])
        if row['broker']=='etoro' and row['side']=='SELL' and total > 0:
            # eToro exits enter accounting through exact position executions.
            # Full requested quantity proves completion; a partial quantity
            # alone cannot release the remaining live order.
            complete = abs(total-number(row['requested'])) <= max(Decimal('1e-9'),total*Decimal('1e-8'))
            if complete:
                con.execute("UPDATE execution_orders SET filled=?,net_filled=?,terminal=1,evidence_complete=1,state='FILLED' WHERE order_key=?",
                            (str(total),str(total),row['order_key']))
                expected=total
        if expected > 0 and abs(total-expected) <= max(Decimal('1e-9'),expected*Decimal('1e-8')):
            con.execute("UPDATE execution_orders SET accounted=1,updated_at=? WHERE order_key=? AND terminal=1 AND evidence_complete=1",
                        (_now(),row['order_key']))


def assert_clear(*, broker, account, environment, instrument, side, position_id=""):
    if broker == 'etoro' and side == 'SELL':
        # Closed IDs and explicitly cancelled operations cannot be re-posted
        # automatically under a new client ID, even though new BUYs may run.
        for row in snapshot(broker='etoro'):
            if (row['account'] == str(account) and row['environment'] == environment
                    and row['instrument'] == str(instrument) and row['position_id'] == str(position_id)
                    and row['state'] in {POSITION_CLOSED_ORDER_UNPROVEN, 'CANCELED'}):
                raise OrderStatusUnklar("Geschlossene Position oder stornierter Schliessauftrag; kein automatischer Wiederholungsauftrag.", reference_id=row['client_id'], order_ids=[row['order_id']] if row['order_id'] else [], accepted=True)
    for row in snapshot(broker=broker, active_only=True):
        if (row['account']==str(account) and row['environment']==environment
                and row['instrument']==str(instrument) and
                (broker=='okx' or side=='BUY' or (row['side']==side and (broker=='okx' or row['position_id']==str(position_id))))):
            raise OrderStatusUnklar("Auftrag oder lokale Buchung noch unklar; kein zweiter POST.",
                reference_id=row['client_id'],order_ids=[row['order_id']] if row['order_id'] else [],accepted=True)


def snapshot(*, broker="", active_only=False):
    """Read without creating a database or changing an older schema."""
    import sqlite3
    from contextlib import closing
    from decision_analytics import db_pfad
    path = db_pfad()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=5)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_orders'").fetchone():
            return []
        sql = "SELECT * FROM execution_orders WHERE 1=1"; args=[]
        if broker:
            sql += " AND broker=?"; args.append(str(broker).lower())
        if active_only:
            sql += " AND (" + ACTIVE_SQL + ")"
        sql += " ORDER BY created_at DESC" + ("" if active_only else " LIMIT 500")
        return [dict(r) for r in con.execute(sql,args)]


def observe_etoro_entry(record, evidence):
    """Bridge the existing, position-verified eToro recovery to the order book."""
    from broker.base import OrderErgebnis
    client = str(record.get('reference_id') or '')
    matching = [r for r in snapshot(broker='etoro',active_only=True) if r['client_id']==client
        and r['account']==str(record.get('account_fingerprint') or '')
        and r['environment']==('DEMO' if record.get('paper') else 'LIVE')]
    if not matching:
        return
    row=matching[0]
    oid=str((record.get('order_ids') or [row['order_id']])[0])
    raw=[]
    for f in record.get('fills') or []:
        raw.append({'fill_id':f"etoro:{row['account']}:open:{oid or client}:{f['position_id']}:{f.get('execution_time')}",
            'ordId':oid,'instId':row['instrument'],'side':'buy',
            'fillSz':f['quantity'],'fillPx':f['price'],
            'position_id':f['position_id'],'filled_at':f.get('execution_time')})
    filled=sum((number(f['fillSz']) for f in raw),Decimal(0))
    execution=str(record.get('execution_state') or '').upper()
    terminal=execution in {'FILLED','REJECTED','CANCELLED','CANCELED','EXPIRED','REJECTED_PARTIALLY_FILLED','PARTIALLY_FILLED_CANCELED','CANCELED_PARTIALLY_FILLED'}
    terminal = terminal and record.get('order_terminal') is not False
    result=OrderErgebnis(order_ids=[oid] if oid else [],client_order_id=client,
        account_fingerprint=row['account'],broker_environment=row['environment'],
        status=execution,raw_status=execution,terminal=terminal,
        filled_quantity=float(filled),gross_filled_quantity=float(filled),
        requested_quantity=float(row['requested']),remaining_quantity=max(0,float(number(row['requested'])-filled)),
        fills=raw,fill_evidence_complete=terminal and (filled>0 or execution in {'REJECTED','CANCELLED','CANCELED','EXPIRED'}))
    observe(result,broker='etoro',instrument=row['instrument'])
    confirm_accounted(broker='etoro',account=row['account'],environment=row['environment'],order_id=oid)


def _checked(key, detail):
    with _connect() as con:
        con.execute('UPDATE execution_orders SET last_checked_at=?,check_detail=? WHERE order_key=?',
                    (_now(), detail, key))


def recover_okx(broker):
    """Read-only reconciliation; a missing response never becomes a zero fill."""
    account=str(broker.account_fingerprint() or '')
    env='DEMO' if broker.demo else 'LIVE'; results=[]
    for row in snapshot(broker='okx',active_only=True):
        if row['account']!=account or row['environment']!=env:
            continue
        try:
            if row['state'] == 'PREPARED':
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row['updated_at'])).total_seconds()
                if age > 120 and abandon_prepared(row['order_key']):
                    results.append({'client_id': row['client_id'], 'state': 'NOT_SUBMITTED'})
                continue
            if hasattr(broker, 'read_primary_order_status'):
                status = broker.read_primary_order_status(row['instrument'], order_id=row['order_id'],
                    client_id=row['client_id'], side=row['side'].lower())
            else:
                status=broker.client.order_status(row['instrument'],ord_id=row['order_id'],
                    cl_ord_id='' if row['order_id'] else row['client_id'])
            if not status:
                _checked(row['order_key'], 'Kein eindeutiger Abschlussbeleg. Status/Archiv werden erneut geprueft; keine neue Order.')
                continue
            for name,expected in (('instId',row['instrument']),('side',row['side'].lower()),('clOrdId',row['client_id'])):
                if status.get(name) not in (None,'',expected):
                    raise ExecutionConflict('Recoveryantwort mit fremder Identitaet')
            if row['order_id'] and status.get('ordId')!=row['order_id']:
                raise ExecutionConflict('Recoveryantwort mit fremder Order-ID')
            status.setdefault('side',row['side'].lower())
            meta=broker.client.instrument(row['instrument'])
            if meta is None:
                continue
            broker._order_result_from_evidence(meta=meta,status=status,cl_ord_id=row['client_id'],
                requested_qty=float(row['requested']),reference_price=0)
            confirm_accounted(broker='okx',account=account,environment=env,
                order_id=str(status.get('ordId') or ''),client_id=row['client_id'])
            _checked(row['order_key'], 'Orderstatus und Ausfuehrungsbelege abgefragt. Eine offene Mengenbuchung bleibt gesperrt.')
            results.append({'client_id':row['client_id'],'checked':True})
        except Exception as exc:
            _checked(row['order_key'], 'Abgleich fehlgeschlagen: ' + type(exc).__name__ + '. Sperre bleibt bis zum Beleg bestehen.')
            results.append({'client_id':row['client_id'],'checked':False,'error_type':type(exc).__name__})
    return results


def reject_reference(*, broker, account, environment, client_id):
    """Release only an exact locally reserved request after explicit rejection."""
    with _connect() as con:
        row = con.execute("SELECT order_key FROM execution_orders WHERE broker=? AND account=? AND environment=? AND client_id=?",
                          (broker, account, environment, client_id)).fetchone()
    if row:
        rejected(row['order_key'], proven=True)


def observe_etoro_exit(fill, *, environment, instrument):
    """Persist a position-scoped close execution before its local consumer."""
    from broker.base import OrderErgebnis
    account = str(fill.account_fingerprint or '')
    with _connect() as con:
        row = con.execute("SELECT * FROM execution_orders WHERE broker='etoro' AND account=? AND environment=? AND instrument=? AND position_id=? AND side='SELL' AND order_id<>'' AND order_id=?",
            (account, environment, str(instrument), str(fill.broker_id), str(fill.order_id))).fetchone()
        if not row:
            return  # External fills are kept by the original position ledger.
        saved = [json.loads(r[0]) for r in con.execute('SELECT raw_json FROM execution_fills WHERE order_key=?', (row['order_key'],))]
    raw = {'fill_id':str(fill.fill_id), 'ordId':str(fill.order_id),
           'instId':str(instrument), 'side':'sell', 'fillSz':fill.quantity,
           'fillPx':fill.price, 'fee':-fill.explicit_fees if fill.explicit_fees is not None else None,
           'feeCcy':fill.currency, 'filled_at':str(fill.timestamp or '')}
    # Replays go through the immutable-evidence validator, even if already saved.
    raw_fills = [r for r in saved if r['fill_id'] != raw['fill_id']] + [raw]
    total = sum((number(r['fillSz']) for r in raw_fills), Decimal(0))
    target = number(row['requested'])
    if total > target + max(Decimal('1e-9'),target*Decimal('1e-8')):
        raise ExecutionConflict('eToro-Close uebersteigt die reservierte Positionsmenge')
    terminal = abs(total-target) <= max(Decimal('1e-9'),target*Decimal('1e-8'))
    result=OrderErgebnis(order_ids=[row['order_id']],client_order_id=row['client_id'],
        account_fingerprint=account,broker_environment=environment,
        status='filled' if terminal else 'partially_filled',terminal=terminal,
        filled_quantity=float(total),gross_filled_quantity=float(total),
        requested_quantity=float(target),remaining_quantity=float(max(Decimal(0),target-total)),
        fills=raw_fills,fill_evidence_complete=True)
    observe(result,broker='etoro',instrument=str(instrument))
    confirm_accounted(broker='etoro',account=account,environment=environment,order_id=row['order_id'])


def observe_etoro_position_closed(proof):
    """Project exact position closure while the original order stays unproven.

    No synthetic execution, cancellation, terminal-order proof or accounting
    receipt is created. A later native exact-order execution may still settle
    the order normally through observe_etoro_exit().
    """
    account = str(proof.get('account_fingerprint') or '')
    environment = str(proof.get('environment') or '')
    if (proof.get('schema') != 1 or proof.get('position_closed') is not True
            or proof.get('order_outcome') != 'UNPROVEN' or not account
            or environment not in {'DEMO', 'LIVE'} or not proof.get('event_key')
            or not proof.get('history_rows') or not proof.get('snapshot_id')
            or number(proof.get('position_closed_quantity', 0)) <= 0):
        raise ExecutionConflict('Positionsabschluss ohne vollstaendigen Beleg')
    with _connect() as con:
        con.execute('BEGIN IMMEDIATE')
        rows = con.execute("""SELECT * FROM execution_orders WHERE broker='etoro'
            AND account=? AND environment=? AND instrument=? AND position_id=? AND side='SELL'
            AND ((order_id<>'' AND order_id=?) OR (client_id<>'' AND client_id=?))""",
            (account, environment, str(proof.get('instrument_id') or ''),
             str(proof.get('position_id') or ''), str(proof.get('broker_order_id') or ''),
             str(proof.get('client_order_id') or ''))).fetchall()
        for row in rows:
            if ((proof.get('broker_order_id') and row['order_id'] and proof['broker_order_id'] != row['order_id'])
                    or (proof.get('client_order_id') and proof['client_order_id'] != row['client_id'])):
                raise ExecutionConflict('Positionsabschluss widerspricht der Auftragsidentitaet')
            if row['state'] in {'CANCELED', 'REJECTED', 'EXPIRED'}:
                continue  # Keep stronger explicit terminal-order evidence.
            native = sum((number(r[0]) for r in con.execute('SELECT quantity FROM execution_fills WHERE order_key=?', (row['order_key'],))), Decimal(0))
            if row['state'] == 'FILLED' and row['terminal'] and row['evidence_complete'] and native == number(row['requested']):
                continue
            previous_events = con.execute("SELECT detail_json FROM execution_events WHERE order_key=? AND kind=?", (row['order_key'], POSITION_CLOSED_ORDER_UNPROVEN)).fetchall()
            if any(json.loads(r[0]).get('event_key') == proof['event_key'] for r in previous_events):
                continue
            _event(con, row['order_key'], POSITION_CLOSED_ORDER_UNPROVEN,
                   {'event_key': proof['event_key'], 'previous_order': dict(row),
                    'position_closed': True, 'order_outcome': 'UNPROVEN',
                    'detail': proof['reason'], 'proof': proof})
            con.execute("""UPDATE execution_orders SET state=?,terminal=0,evidence_complete=0,
                filled=?,net_filled=?,accounted=?,last_checked_at=?,check_detail=?,updated_at=?,evidence_json=?
                WHERE order_key=?""", (POSITION_CLOSED_ORDER_UNPROVEN, str(native), str(native),
                int(row['accounted']) if native > 0 and number(row['filled']) == native else 0,
                _now(), proof['reason'], _now(), json.dumps(proof, sort_keys=True, default=str), row['order_key']))


def observe_etoro_cancelled(*, account, environment, instrument, position_id, client_id, order_id, detail):
    """The adapter has validated explicit account/order-bound cancel evidence."""
    from broker.base import OrderErgebnis
    with _connect() as con:
        row = con.execute("SELECT * FROM execution_orders WHERE broker='etoro' AND account=? AND environment=? AND instrument=? AND position_id=? AND client_id=? AND order_id=?", (account, environment, str(instrument), str(position_id), client_id, order_id)).fetchone()
    if not row:
        return
    if row['state'] == 'CANCELED' and row['terminal'] and row['evidence_complete']:
        return
    result = OrderErgebnis(order_ids=[order_id], client_order_id=client_id,
        account_fingerprint=account, broker_environment=environment,
        status='CANCELED', raw_status='CANCELED', terminal=True,
        requested_quantity=float(row['requested']), filled_quantity=0,
        fill_evidence_complete=True, execution_evidence=detail)
    observe(result, broker='etoro', instrument=str(instrument))
