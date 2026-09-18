"""Regressionen der in 9.0.12 gemeldeten OKX-/WebUI-Fehler."""
from __future__ import annotations

import logging
import inspect
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_small_positive_volume_is_ranked_instead_of_hard_blocked():
    from broker.okx import OKXInstrument, OKXTicker
    from universe.crypto_selector import CryptoUniverseSelector

    cfg = SimpleNamespace(
        OKX_QUOTE_CCY="EUR", OKX_ALLOWED_QUOTE_CCY=("EUR", "USD", "USDC"),
        OKX_REQUIRE_FUNDED_TRADE_QUOTE=True,
        CRYPTO_UNIVERSE_MIN_AGE_DAYS=30.0,
        CRYPTO_UNIVERSE_MIN_QUOTE_VOLUME=0.0,
        CRYPTO_UNIVERSE_MAX_SPREAD_PCT=0.012,
        CRYPTO_UNIVERSE_PRESELECTION=100, CRYPTO_UNIVERSE_BLOCKLIST=(),
        CRYPTO_ESTABLISHED_MIN_AGE_DAYS=365.0,
        CRYPTO_ESTABLISHED_MIN_QUOTE_VOLUME=50_000_000.0,
        CRYPTO_ESTABLISHED_MAX_SPREAD_PCT=0.0015,
        CRYPTO_CORE_SYMBOLS=(),
    )
    selector = CryptoUniverseSelector(SimpleNamespace(), cfg=cfg)
    selector._base_age_days = {"ATOM": 1000.0}
    selector._quote_cash = {"EUR": 1000.0, "USD": 0.0, "USDC": 0.0}
    selector._funded_quotes = {"EUR"}
    meta = OKXInstrument("ATOM-EUR", "ATOM", "EUR", "live", "0.001", "0.01", "0.01",
                         trade_quote_ccy_list=("EUR",))
    ticker = OKXTicker("ATOM-EUR", last=5.0, bid=4.99, ask=5.01,
                       vol_24h_quote=14_548.0)
    assert selector._harter_filter(meta, ticker, quote_rate=1.0) == ""


def test_delete_unproven_entry_removes_local_position_but_never_broker_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    monkeypatch.setattr(config, "DECISION_DB_FILE", "decision_history.sqlite")

    import trade_ledger
    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="SOL", menge=14.1906, einstieg_preis=99.95,
        asset_type="crypto", waehrung="USD", external=True,
        broker_account_fingerprint="fixture-A", broker_position_id="SOL-USD",
        entry_order_id="3863766848127053825",
        entry_fill_id="okx-entry:3863766848127053825",
        reconciliation_status="EXTERNAL_OBSERVE")
    assert trade_id

    from crypto_engine import KryptoPosition, KryptoPositionsbuch
    book = KryptoPositionsbuch(tmp_path / "crypto_positions.json")
    book.setze(KryptoPosition(
        symbol="SOL", inst_id="SOL-USD", menge=14.19086594,
        einstieg=99.95, stop=0.0, take_profit=0.0,
        order_id="3863766848127053825", referenz="TBNlegacy",
        verwaltung="BEOBACHTEN", ownership_verified=False))
    engine = SimpleNamespace(
        buch=book,
        _offene_order_symbole=lambda: set(),
    )

    import okx_reconciliation_actions as actions
    actions.anfordern(trade_id, actions.DELETE_LOCAL, symbol="SOL", actor="test")
    result = actions.verarbeite(engine, SimpleNamespace(account_fingerprint=lambda:"fixture-A", demo=True))
    assert result[0]["status"] == "DONE"
    assert book.hole("SOL") is None
    row = trade_ledger.trade_detail(trade_id)
    assert row["reconciliation_status"] == "DISMISSED"
    assert not row["ausgestiegen_am"], "lokales Ausblenden darf keinen Verkauf erfinden"
    assert trade_ledger.offener_trade("okx", "SOL") is None
    assert "OKX-Guthaben unverändert" in result[0]["detail"]
    assert list((tmp_path / "state_backups").glob("*/crypto_positions.json"))


def test_confirmed_trade_cannot_be_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    monkeypatch.setattr(config, "DECISION_DB_FILE", "decision_history.sqlite")
    import trade_ledger
    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="ETH", menge=1.0, einstieg_preis=2000,
        decision_id=42, entry_order_id="order-1", client_order_id="TBN42",
        entry_fill_id="real-trade-id", entry_fill_ids=["real-trade-id"],
        ownership_status="VERIFIED", reconciliation_status="CONFIRMED_OPEN")
    import okx_reconciliation_actions as actions
    try:
        actions.anfordern(trade_id, actions.DELETE_LOCAL, symbol="ETH", actor="test")
    except ValueError as exc:
        assert "bestätigte offene Botposition" in str(exc)
    else:
        raise AssertionError("bestätigter Trade durfte gelöscht werden")


def test_log_formatter_is_always_europe_berlin():
    from log_hygiene import BerlinFormatter
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "x", (), None)
    record.created = 0.0
    assert BerlinFormatter("%(asctime)s", "%Y-%m-%d %H:%M:%S").format(record).startswith(
        "1970-01-01 01:00:00")


def test_logbook_page_size_dropdown_and_api_fallback_preserve_server_local_time():
    root = Path(__file__).resolve().parents[1]
    js = (root / "webui/static/logbook.js").read_text(encoding="utf-8")
    state = (root / "webui/state.py").read_text(encoding="utf-8")
    from webui.app import api_decisions
    assert inspect.signature(api_decisions).parameters["per_page"].default == 15
    assert "created_at_local" in js and "updated_at_local" in js
    assert 'ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE"' in state

    class PageOptions(HTMLParser):
        active = False
        selected = None

        def __init__(self):
            super().__init__()
            self.values = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "select":
                self.active = values.get("id") == "perPage"
            if tag == "option" and self.active:
                self.values.append(values["value"])
                if "selected" in values:
                    self.selected = values["value"]

        def handle_endtag(self, tag):
            if tag == "select":
                self.active = False

    parsed = PageOptions()
    parsed.feed((root / "webui/templates/logbook.html").read_text(encoding="utf-8"))
    assert parsed.values == ["15", "50", "100"] and parsed.selected == "15"
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required to exercise actual dropdown request behavior")
    harness = r'''
const fs=require('fs'),vm=require('vm');
const ids=new Map(),values=JSON.parse(process.argv[2]),selected=process.argv[3];
const get=id=>{if(!ids.has(id))ids.set(id,{value:id==='perPage'?selected:''});return ids.get(id)};
const ctx={document:{getElementById:get,querySelector:()=>null},URLSearchParams,api:()=>new Promise(()=>{})};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
if(ctx.qs().get('per_page')!==selected)throw Error('Selected page size not sent');
for(const value of values){get('perPage').value=value;if(ctx.qs().get('per_page')!==value)throw Error('Dropdown ignored: '+value);}
ids.set('perPage',null);
if(ctx.qs().get('per_page')!=='15')throw Error('Old-page fallback changed');
'''
    result = subprocess.run([node, "-e", harness, str(root / "webui/static/logbook.js"),
        json.dumps(parsed.values), parsed.selected], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_telegram_decisions_do_not_repeat_blocked(monkeypatch):
    import decision_analytics
    monkeypatch.setattr(decision_analytics, "latest", lambda _limit: [{
        "created_at_utc": "2026-08-29T00:00:00+00:00",
        "symbol": "ETH", "status": "BLOCKED", "execution_status": "BLOCKED",
        "reason": "kein Kaufsignal",
    }])
    import berichte
    text = berichte.decisions_text()
    assert "ABGELEHNT · ABGELEHNT" not in text
    assert text.count("ABGELEHNT") == 1
    assert "29.08 02:00" in text


def test_okx_status_separates_account_assets_from_bot_positions():
    import okx_status
    data = {
        "online": True, "modus": "DEMO",
        "exposure": {"nach_klasse": {"ACCOUNT_ASSET": [
            {"waehrung": "ETH", "menge": 2.03972528},
        ]}},
        "positionen": [{
            "symbol": "SOL", "menge": 14.19, "einstieg": 100,
            "verwaltung": "BEOBACHTEN", "ownership_verified": False,
        }],
    }
    text = okx_status.text(data)
    assert "Konto-Assets (keine Bottrades):" in text
    assert "2.03972528 ETH" in text
    assert "Bestätigte Bot-Positionen: 0" in text
