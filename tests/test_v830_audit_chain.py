"""Regressionen fuer die lueckenlose NEXUS-8.3-Auditkette."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def _db(monkeypatch, tmp_path):
    import decision_analytics as da
    path = tmp_path / "decision_history.sqlite"
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(da, "DB_PATH", path)
    return da, path


def test_snapshot_enthaelt_id_zahlen_und_explizite_gate_zustaende(monkeypatch, tmp_path):
    da, path = _db(monkeypatch, tmp_path)
    did = da.record({
        "decision_id": 83001, "symbol": "BTC", "asset_type": "crypto",
        "broker": "okx", "status": "BLOCKED", "blocked_by": "net_edge",
        "price": 100.5, "bid": 100.4, "ask": 100.6, "spread_pct": 0.00199,
        "rsi_15m": 48.2, "cash_before": 5000.0, "equity_before": 5100.0,
        "qty": 1.25, "stop": 98.0, "take": 106.0,
        "evaluated_filters": [{"name": "market_quality", "status": "PASS"},
                              {"name": "net_edge", "status": "BLOCKED",
                               "actual": 0.01, "limit": 0.02, "unit": "ratio"}],
        "not_evaluated_filters": ["candidate_gate", "broker_submit"],
    })
    assert did == 83001
    with sqlite3.connect(path) as con:
        raw = con.execute("SELECT payload_json FROM decisions WHERE id=?", (did,)).fetchone()[0]
    payload = json.loads(raw)
    snap = payload["decision_snapshot"]
    assert payload["decision_id"] == snap["decision_id"] == did
    assert isinstance(snap["market"]["spread_pct"], float)
    assert isinstance(snap["technical"]["rsi_15m"], float)
    assert isinstance(snap["risk_account"]["cash"], float)
    states = {g["name"]: g["status"] for g in snap["gates"]}
    assert states == {"market_quality": "PASS", "net_edge": "BLOCKED",
                      "candidate_gate": "NOT_EVALUATED", "broker_submit": "NOT_EVALUATED"}


def test_system_error_wird_als_entscheidung_persistiert(monkeypatch, tmp_path):
    da, path = _db(monkeypatch, tmp_path)
    import decision_journal as journal
    monkeypatch.setattr(journal, "PATH", tmp_path / "decision_journal.jsonl")
    try:
        raise TypeError("entry_allowed erhielt unerwarteten Parameter")
    except TypeError as exc:
        did = da.record_system_error(
            decision_id=83002, symbol="MSFT", asset_type="stock", broker="etoro",
            gate="market_session", exc=exc, paper=True)
    with sqlite3.connect(path) as con:
        row = con.execute(
            "SELECT status,blocked_by,payload_json FROM decisions WHERE id=?", (did,)
        ).fetchone()
    assert row[0:2] == ("SYSTEM_ERROR", "market_session")
    payload = json.loads(row[2])
    assert payload["decision_id"] == did
    assert payload["decision_snapshot"]["gates"][0]["status"] == "SYSTEM_ERROR"
    mirror = json.loads(journal.PATH.read_text(encoding="utf-8").splitlines()[-1])
    sources = json.loads((tmp_path / "decision_sources.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])
    assert mirror["decision_id"] == sources["decision_id"] == did


@dataclass
class Result:
    order_ids: list[str]
    filled_quantity: float
    avg_fill_price: float
    reference_id: str = "ref-1"
    stop_order_platziert: bool = True
    paper: bool = True


def test_partial_fill_und_storno_vermischen_schutzorder_nicht(monkeypatch, tmp_path):
    da, path = _db(monkeypatch, tmp_path)
    did = da.record({"decision_id": 83003, "symbol": "SOL", "asset_type": "crypto",
                     "broker": "okx", "status": "APPROVED", "paper": True,
                     "price": 100.0})
    da.record_order_result(did, Result(["entry-1", "protect-1"], 4.0, 100.1),
                           broker="okx", symbol="SOL", requested_qty=10.0,
                           requested_price=100.0, currency="EUR")
    da.record_order_state(did, broker="okx", broker_order_id="entry-1", role="ENTRY",
                          status="CANCELED", symbol="SOL", filled_qty=4.0,
                          remaining_qty=6.0)
    with sqlite3.connect(path) as con:
        rows = con.execute(
            "SELECT role,status,filled_qty,remaining_qty FROM decision_orders "
            "WHERE decision_id=? ORDER BY role", (did,)).fetchall()
    assert rows == [("ENTRY", "CANCELED", 4.0, 6.0),
                    ("PROTECTIVE", "ACTIVE", None, None)]


def test_trade_braucht_decision_oder_explizite_externe_herkunft(monkeypatch, tmp_path):
    da, path = _db(monkeypatch, tmp_path)
    import trade_ledger as ledger
    did = da.record({"decision_id": 83004, "symbol": "AAPL", "asset_type": "stock",
                     "broker": "etoro", "status": "APPROVED", "price": 200.0})
    trade_id = ledger.trade_open(
        broker="etoro", symbol="AAPL", menge=2, einstieg_preis=200,
        decision_id=did, enter_tag="pullback_rsi_1h", asset_type="stock", waehrung="USD")
    rejected_id = ledger.trade_open(
        broker="okx", symbol="ALT", menge=1, einstieg_preis=10,
        asset_type="crypto", waehrung="EUR")
    external_id = ledger.trade_open(
        broker="okx", symbol="MANUELL", menge=1, einstieg_preis=10,
        asset_type="crypto", waehrung="EUR", external=True)
    with sqlite3.connect(path) as con:
        linked = con.execute(
            "SELECT decision_id,link_status,enter_tag FROM trades WHERE trade_id=?",
            (trade_id,)).fetchone()
        external = con.execute(
            "SELECT decision_id,link_status FROM trades WHERE trade_id=?", (external_id,)).fetchone()
    assert linked == (did, "LINKED", "pullback_rsi_1h")
    assert rejected_id is None
    assert external == (None, "EXTERNAL")


def test_aktien_final_gate_ruft_entry_allowed_ohne_fremdparameter_auf():
    source = (Path(__file__).resolve().parent.parent / "live_trader.py").read_text(encoding="utf-8")
    marker = "final_market_open, _final_session_reason = entry_allowed("
    block = source[source.index(marker):source.index(marker) + 260]
    assert "asset_type=" not in block
    assert "regular_hours_only=" in block


def test_jsonl_spiegel_enthaelt_dieselbe_decision_id(monkeypatch, tmp_path):
    da, _ = _db(monkeypatch, tmp_path)
    import decision_journal as journal
    monkeypatch.setattr(journal, "PATH", tmp_path / "decision_journal.jsonl")
    did = journal.record_decision(
        decision_id=83005, symbol="QCOM", asset_type="stock", broker="etoro",
        status="BLOCKED", blocked_by="market_quality", price=150.0)
    payload = json.loads(journal.PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert did == payload["decision_id"] == payload["decision_snapshot"]["decision_id"]


def test_migration_v822_ist_idempotent_und_behaelt_altdaten(monkeypatch, tmp_path):
    da, path = _db(monkeypatch, tmp_path)
    da.init_db()
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER,
                broker TEXT NOT NULL, asset_type TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL, waehrung TEXT NOT NULL DEFAULT '',
                strategie_version TEXT NOT NULL DEFAULT '', marktphase TEXT NOT NULL DEFAULT '',
                paper INTEGER NOT NULL DEFAULT 1, eingestiegen_am TEXT NOT NULL,
                einstieg_preis REAL, einstieg_referenz REAL, menge REAL,
                einstieg_gebuehr REAL, ausgestiegen_am TEXT, ausstieg_preis REAL,
                ausstieg_referenz REAL, exit_grund TEXT, brutto_pnl REAL,
                gebuehren REAL, slippage_geschaetzt REAL, netto_pnl REAL,
                haltedauer_minuten REAL, mfe_pct REAL, mae_pct REAL,
                notiz TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO trades
              (decision_id,broker,asset_type,symbol,waehrung,paper,eingestiegen_am,
               einstieg_preis,menge,notiz)
            VALUES (NULL,'okx','crypto','SOL','EUR',1,'2026-08-25T10:00:00+00:00',
                    120.0,2.0,'historisch ungeklärt');
        """)
    import trade_ledger
    trade_ledger.init_ledger()
    trade_ledger.init_ledger()
    da.init_db()
    da.init_db()
    with sqlite3.connect(path) as con:
        old = con.execute(
            "SELECT symbol,decision_id,link_status,enter_tag,notiz FROM trades"
        ).fetchone()
        order_tables = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='decision_orders'"
        ).fetchone()[0]
    assert old == ("SOL", None, "LEGACY_UNLINKED", "", "historisch ungeklärt")
    assert order_tables == 1
