"""10.1.10: FX-robuste Equity-Bremse, Marktkursbewertung, Waehrungsfreigabe,
Entscheidungs-Retention und Diagnose-Sicherung.

Kein Test erzeugt eine Broker-, Netzwerk- oder Telegram-Aktion.
"""
import os
from pathlib import Path
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeEquityGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name
        import risk_manager
        self.state = risk_manager.RiskState()
        self.state.save(Path(self._tmp.name) / "risk_state_test.json")

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def test_fx_only_drawdown_does_not_halt_when_native_series_is_flat(self):
        key = "handelbares_kapital:v3:okx:EUR"
        self.state.update_equity_guard(87000.0, basis_key=key,
                                       native_equity=100000.0, native_ccy="USDC")
        # EUR faellt um 3.4 % (reiner Kursverfall USDC/EUR), nativ unveraendert.
        halted = self.state.update_equity_guard(84000.0, basis_key=key,
                                                native_equity=100000.0, native_ccy="USDC")
        self.assertFalse(halted)
        self.assertFalse(self.state.equity_guard_halted)
        self.assertLess(self.state.equity_drawdown_pct, -0.03)
        self.assertAlmostEqual(self.state.native_drawdown_pct, 0.0, places=9)
        self.assertLess(self.state.fx_drawdown_pct, -0.03)

    def test_native_trading_loss_halts_even_if_fx_masks_it(self):
        key = "handelbares_kapital:v3:okx:EUR"
        self.state.update_equity_guard(87000.0, basis_key=key,
                                       native_equity=100000.0, native_ccy="USDC")
        # EUR-Wert stabil (FX gestiegen), aber nativ -4 % Handelsverlust.
        halted = self.state.update_equity_guard(87000.0, basis_key=key,
                                                native_equity=96000.0, native_ccy="USDC")
        self.assertTrue(halted)
        self.assertTrue(self.state.equity_guard_halted)
        self.assertLess(self.state.native_drawdown_pct, -0.03)

    def test_without_native_series_the_basis_drawdown_still_halts(self):
        key = "handelbares_kapital:v3:etoro:USD"
        self.state.update_equity_guard(10000.0, basis_key=key)
        halted = self.state.update_equity_guard(9600.0, basis_key=key)
        self.assertTrue(halted)

    def test_funded_currency_change_restarts_native_day_basis(self):
        key = "handelbares_kapital:v3:okx:EUR"
        self.state.update_equity_guard(87000.0, basis_key=key,
                                       native_equity=100000.0, native_ccy="USDC")
        self.state.update_equity_guard(87000.0, basis_key=key,
                                       native_equity=100000.0, native_ccy="USD")
        self.assertEqual(self.state.native_equity_ccy, "USD")
        self.assertAlmostEqual(self.state.day_start_native_equity, 100000.0)
        self.assertFalse(self.state.equity_guard_halted)


class MarketPriceValuation(unittest.TestCase):
    def test_positions_are_no_longer_valued_at_trailing_high(self):
        source = (ROOT / "crypto_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("preis = position.hoechstkurs or position.einstieg", source)
        self.assertIn("preis = self._letzter_kurs(position) or position.einstieg", source)


class CurrencyRelease(unittest.TestCase):
    def test_config_permits_usd_usdg_only_via_explicit_list(self):
        source = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn('{"EUR", "USDC", "USD", "USDG"}', source)
        self.assertIn("WAEHRUNGEN FREIGEBEN", source)

    def test_settings_store_requires_confirmation_phrase(self):
        source = (ROOT / "webui" / "settings_store.py").read_text(encoding="utf-8")
        self.assertIn("'WAEHRUNGEN FREIGEBEN'", source)
        self.assertIn("confirm_currencies", source)

    def test_entry_routing_uses_allowed_quotes_dynamically(self):
        source = (ROOT / "okx_entry_routing.py").read_text(encoding="utf-8")
        self.assertNotIn('for lane in ("EUR", "USDC")', source)
        self.assertIn("for lane in broker.allowed_quotes", source)

    def test_routes_accept_a_released_usd_lane(self):
        import importlib.util
        if importlib.util.find_spec("pandas") is None:
            self.skipTest("pandas fehlt in dieser Pruefumgebung (broker.base-Import)")
        import okx_entry_routing as routing
        meta = types.SimpleNamespace(ist_live=True, base_ccy="SOL", quote_ccy="USD",
                                     trade_quote_ccy_list=("USD",), inst_id="SOL-USD",
                                     min_size=0.001)
        client = types.SimpleNamespace(
            hat_zugangsdaten=True,
            balances=lambda: {"USD": {"cash": 5000.0}, "EUR": {"cash": 0.0}},
            instruments=lambda: {"SOL-USD": meta})
        broker = types.SimpleNamespace(client=client, quote_ccy="EUR",
                                       allowed_quotes=("EUR", "USDC", "USD"))
        lanes = routing.routes(broker, "SOL")
        self.assertEqual([(r[1].inst_id, r[2]) for r in lanes], [("SOL-USD", "USD")])
        without_release = types.SimpleNamespace(client=client, quote_ccy="EUR",
                                                allowed_quotes=("EUR", "USDC"))
        self.assertEqual(routing.routes(without_release, "SOL"), [])


class HistoryRetentionAndSnapshot(unittest.TestCase):
    def test_prune_removes_only_unlinked_old_decisions(self):
        from datetime import datetime, timedelta, timezone
        import gc
        import shutil
        tmp = tempfile.mkdtemp()
        if True:
            os.environ["TRADINGBOT_TEST_STATE_DIR"] = tmp
            try:
                import decision_analytics as da
                da.init_db()
                now = datetime.now(timezone.utc)
                old = (now - timedelta(days=120)).isoformat()
                fresh = (now - timedelta(days=1)).isoformat()
                con = da._connect()
                try:
                    def add(created):
                        return con.execute(
                            "INSERT INTO decisions(created_at_utc,local_day,symbol,status,"
                            "paper,payload_json) VALUES(?,?,?,?,1,'{}')",
                            (created, created[:10], "TST", "BLOCKED")).lastrowid
                    old_plain = add(old)
                    old_with_order = add(old)
                    fresh_plain = add(fresh)
                    con.execute(
                        "INSERT INTO decision_orders(decision_id,broker,broker_order_id,role,"
                        "status,created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?,?)",
                        (old_with_order, "okx", "o1", "entry", "FILLED", old, old))
                    con.execute(
                        "INSERT INTO heartbeat_events(created_at_utc,broker,state)"
                        " VALUES(?,?,?)", (old, "okx", "FRESH"))
                    con.commit()
                finally:
                    con.close()
                result = da.prune_history(now=now)
                self.assertEqual(result["removed_decisions"], 1)
                self.assertEqual(result["removed_heartbeats"], 1)
                self.assertFalse(result["economic_receipts_touched"])
                self.assertGreater(old_plain, 0)
                con = da._connect()
                try:
                    kept = {r[0] for r in con.execute("SELECT id FROM decisions")}
                finally:
                    con.close()
                self.assertEqual(kept, {old_with_order, fresh_plain})
            finally:
                os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
                gc.collect()
                shutil.rmtree(tmp, ignore_errors=True)

    def test_diagnosis_snapshot_uses_single_step_backup_for_wal(self):
        source = (ROOT / "NEXUS_10_Diagnose.py").read_text(encoding="utf-8")
        self.assertIn("source.backup(destination, pages=-1)", source)
        self.assertIn("SNAPSHOT_SECONDS = 60", source)


class SettlementNotification(unittest.TestCase):
    def test_new_unknown_sale_result_triggers_one_notification(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRADINGBOT_TEST_STATE_DIR"] = tmp
            try:
                import risk_manager
                state = risk_manager.RiskState()
                state.save(Path(tmp) / "risk_state_etoro.json")
                calls = []
                state._notify_settlement_needed = lambda tid: calls.append(str(tid))
                self.assertTrue(state.register_unknown_pnl_trade("ledger:71"))
                self.assertFalse(state.register_unknown_pnl_trade("ledger:71"))
                self.assertEqual(calls, ["ledger:71"])
            finally:
                os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)


if __name__ == "__main__":
    unittest.main()
