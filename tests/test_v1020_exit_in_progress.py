"""10.2.0: EXIT_IN_PROGRESS -- der DOGE-Fall vom 17.09.2026.

Ein teilerfuellter bot-eigener Schutz-Exit (OCO-Take-Profit) reduzierte den
Bestand; der Snapshot-Guard meldete BROKER_STATE_UNKNOWN und sperrte damit
ALLE OKX-Neueinstiege. Fachlich ist der Zustand erklaert: Die Order gehoert
nachweislich dem Bot, fehlende Menge == bereits verkaufte Menge. 10.2.0
klassifiziert das als EXIT_IN_PROGRESS (Domaene handelbar, nur der betroffene
Wert gesperrt). Jeder Zweifel behaelt die alte konservative Sperre.
"""
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class _Position:
    def __init__(self, symbol, menge, *, algo_id="", inst_id="", account="konto-1"):
        self.symbol = symbol
        self.menge = menge
        self.ist_bewiesene_botposition = True
        self.account_fingerprint = account
        self.paper = True
        self.protection_algo_id = algo_id
        self.inst_id = inst_id


class _Client:
    def __init__(self, orders):
        self._orders = orders
        self.raise_on_status = False

    def order_status(self, inst_id, *, ord_id="", cl_ord_id=""):
        if self.raise_on_status:
            raise RuntimeError("Orderauskunft nicht erreichbar")
        return dict(self._orders.get(ord_id, {}))


class _Broker:
    def __init__(self, balances, orders, exit_ids):
        self.demo = True
        self._balances = balances
        self.client = _Client(orders)
        self._exit_ids = exit_ids

    def account_fingerprint(self):
        return "konto-1"

    def guthaben_schnappschuss(self):
        return dict(self._balances)

    def protection_exit_order_ids(self, algo_id):
        return set(self._exit_ids.get(str(algo_id), set()))


def _doge_broker(*, fills=1575.48, side="sell", state="partially_filled"):
    balances = {"DOGE": {"cash": 5758.70, "frozen": 0.0, "gesamt": 5758.70}}
    orders = {"ord-tp-1": {"side": side, "state": state,
                            "accFillSz": str(fills), "sz": "7334.18"}}
    return _Broker(balances, orders, {"algo-doge-1": {"ord-tp-1"}})


class ExitInProgress(unittest.TestCase):
    def setUp(self):
        # ignore_cleanup_errors: Windows haelt die Ledger-SQLite noch offen;
        # auf dem Pi (POSIX) raeumt das Verzeichnis normal ab.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def _positionen(self):
        return [_Position("DOGE", 7334.18, algo_id="algo-doge-1", inst_id="DOGE-USDC")]

    def test_doge_fall_wird_exit_in_progress(self):
        from okx_snapshot_guard import validate
        result = validate(_doge_broker(), self._positionen())
        self.assertEqual(result["missing"], [])
        self.assertEqual(len(result["exit_in_progress"]), 1)
        eintrag = result["exit_in_progress"][0]
        self.assertEqual(eintrag["symbol"], "DOGE")
        self.assertAlmostEqual(eintrag["eigene_exit_fills"], 1575.48, places=2)
        self.assertIn("EXIT_IN_PROGRESS", result["detail"])
        # Die Domaene bleibt handelbar (sofern die Buchhaltung vollstaendig ist).
        self.assertTrue(result["valid"] or "EXIT_IN_PROGRESS" not in result["detail"],
                        f"unerwartet: {result['detail']}")

    def test_unerklaerte_fehlmenge_sperrt_weiter_alles(self):
        from okx_snapshot_guard import validate
        # Kein eigener Fill: die fehlende Menge ist NICHT erklaert.
        result = validate(_doge_broker(fills=0.0), self._positionen())
        self.assertIn("DOGE", result["missing"])
        # 10.7.0: unerklaerter Bestand sperrt DOGE, nicht die Domaene.
        self.assertTrue(result["valid"])
        self.assertIn("DOGE", result["blocked_symbols"])
        self.assertIn("Bestand fehlt oder reduziert", result["detail"])

    def test_kauforder_erklaert_nichts(self):
        from okx_snapshot_guard import validate
        result = validate(_doge_broker(side="buy"), self._positionen())
        self.assertIn("DOGE", result["missing"])
        # 10.7.0: unerklaerter Bestand sperrt DOGE, nicht die Domaene.
        self.assertTrue(result["valid"])
        self.assertIn("DOGE", result["blocked_symbols"])

    def test_zweifel_behaelt_die_sperre(self):
        from okx_snapshot_guard import validate
        broker = _doge_broker()
        broker.client.raise_on_status = True
        result = validate(broker, self._positionen())
        self.assertIn("DOGE", result["missing"])
        # 10.7.0: unerklaerter Bestand sperrt DOGE, nicht die Domaene.
        self.assertTrue(result["valid"])
        self.assertIn("DOGE", result["blocked_symbols"])

    def test_teilfill_der_die_menge_nicht_deckt_sperrt(self):
        from okx_snapshot_guard import validate
        # Beobachtet 5758,70 + eigene Fills 100 << 98 % von 7334,18.
        result = validate(_doge_broker(fills=100.0), self._positionen())
        self.assertIn("DOGE", result["missing"])
        # 10.7.0: unerklaerter Bestand sperrt DOGE, nicht die Domaene.
        self.assertTrue(result["valid"])
        self.assertIn("DOGE", result["blocked_symbols"])


class EngineVerdrahtung(unittest.TestCase):
    def test_engine_sperrt_exit_in_progress_nur_positionsbezogen(self):
        engine = (ROOT / "crypto_engine.py").read_text(encoding="utf-8")
        self.assertIn("_exit_in_progress_symbole", engine)
        self.assertIn("EXIT_IN_PROGRESS", engine)
        self.assertIn("kein Neueinstieg bis zur Abrechnung", engine)


if __name__ == "__main__":
    unittest.main()
