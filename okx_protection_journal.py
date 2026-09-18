"""Durable reservation of a protection POST until a broker response proves it.

Uses the existing decision database so normal backups and updates preserve it.
A missing pending-list entry is never proof that a submitted algo was rejected.
"""
from datetime import datetime, timezone
from contextlib import closing
import json
import sqlite3

from broker.base import BrokerFehler


def _connect():
    from decision_analytics import _connect as connect
    con = connect()
    try:
        con.execute('PRAGMA synchronous=FULL')
        con.execute('''CREATE TABLE IF NOT EXISTS okx_protection_submissions (
            account TEXT NOT NULL, environment TEXT NOT NULL,
            instrument TEXT NOT NULL, client_id TEXT NOT NULL,
            state TEXT NOT NULL, algo_id TEXT NOT NULL DEFAULT '',
            request_json TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(account, environment, instrument, client_id))''')
        con.execute('''CREATE TABLE IF NOT EXISTS okx_protection_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,account TEXT NOT NULL,environment TEXT NOT NULL,
            instrument TEXT NOT NULL,client_id TEXT NOT NULL,state TEXT NOT NULL,algo_id TEXT NOT NULL,
            request_json TEXT NOT NULL,updated_at TEXT NOT NULL,
            UNIQUE(account,environment,instrument,client_id,state,algo_id,request_json,updated_at))''')
        # Existing evidence is preserved even before the next replacement attempt.
        con.execute('''INSERT OR IGNORE INTO okx_protection_history
            (account,environment,instrument,client_id,state,algo_id,request_json,updated_at)
            SELECT account,environment,instrument,client_id,state,algo_id,request_json,updated_at
            FROM okx_protection_submissions''')
        con.commit()
        return con
    except BaseException:
        con.close()
        raise


def _scope(account, environment, instrument, client_id):
    scope = tuple(str(x or '').strip() for x in (account, environment, instrument, client_id))
    if not all(scope) or scope[1] not in {'DEMO', 'LIVE'}:
        raise BrokerFehler('Schutzauftrag ohne eindeutige Konto-/Orderzuordnung blockiert')
    return scope


def begin(*, account, environment, instrument, client_id, request):
    """Atomic claim. Return an unresolved previous submission instead of sending."""
    scope = _scope(account, environment, instrument, client_id)
    try:
        with closing(_connect()) as con, con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('''SELECT state,algo_id,request_json FROM okx_protection_submissions
                WHERE account=? AND environment=? AND instrument=? AND client_id=?''', scope).fetchone()
            if row and row['state'] not in {'REJECTED', 'CONFIRMED'}:
                return dict(row)
            con.execute('''INSERT INTO okx_protection_submissions VALUES(?,?,?,?,?,'',?,?)
                ON CONFLICT(account,environment,instrument,client_id) DO UPDATE SET
                state=excluded.state,algo_id='',request_json=excluded.request_json,
                updated_at=excluded.updated_at''', (*scope, 'SUBMITTING',
                    json.dumps(request, sort_keys=True), datetime.now(timezone.utc).isoformat()))
            con.execute('''INSERT OR IGNORE INTO okx_protection_history
                (account,environment,instrument,client_id,state,algo_id,request_json,updated_at)
                SELECT account,environment,instrument,client_id,state,algo_id,request_json,updated_at
                FROM okx_protection_submissions WHERE account=? AND environment=? AND instrument=? AND client_id=?''',scope)
            return None
    except sqlite3.Error as exc:
        raise BrokerFehler(f'Schutzauftrag nicht dauerhaft reservierbar ({type(exc).__name__})') from None


def update(*, account, environment, instrument, client_id, state, algo_id=''):
    scope = _scope(account, environment, instrument, client_id)
    if state not in {'ACCEPTED', 'REJECTED', 'CONFIRMED'}:
        raise ValueError('Unsupported protection evidence state')
    try:
        with closing(_connect()) as con, con:
            con.execute('''UPDATE okx_protection_submissions SET state=?,
                algo_id=CASE WHEN ?<>'' THEN ? ELSE algo_id END,updated_at=?
                WHERE account=? AND environment=? AND instrument=? AND client_id=?''',
                (state, str(algo_id), str(algo_id), datetime.now(timezone.utc).isoformat(), *scope))
            con.execute('''INSERT OR IGNORE INTO okx_protection_history
                (account,environment,instrument,client_id,state,algo_id,request_json,updated_at)
                SELECT account,environment,instrument,client_id,state,algo_id,request_json,updated_at
                FROM okx_protection_submissions WHERE account=? AND environment=? AND instrument=? AND client_id=?''',scope)
    except sqlite3.Error as exc:
        raise BrokerFehler(f'Schutzbeleg nicht dauerhaft gespeichert ({type(exc).__name__})') from None
