"""10.2.0: Mehrlagen-native Equity-Bremse (USDC+USD).

Mit ZWEI finanzierten Waehrungs-Lanes lieferte die 10.1.10-Reihe nichts (Design
nur fuer genau eine Waehrung) -- der FX-Schutz war genau dann inaktiv, als
Georg USD freigab. 10.2.0 fuehrt die native Reihe als Summe der Lanes mit am
Tagesstart eingefrorenen Umrechnungskursen; ein Lane-Set-Wechsel ist ein
Basis-Ereignis und startet die Tagesbasis neu.

Kein Test erzeugt eine Broker-, Netzwerk- oder Telegram-Aktion.
"""
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
KEY = "handelbares_kapital:v3:okx:EUR"


def _beitraege(usdc=100000.0, usd=100000.0, *, usdc_rate=0.85, usd_rate=0.87):
    return {"USDC": {"native": usdc, "rate_to_basis": usdc_rate},
            "USD": {"native": usd, "rate_to_basis": usd_rate}}


class MultiLaneNativeGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name
        import risk_manager
        self.state = risk_manager.RiskState()
        self.state.save(Path(self._tmp.name) / "risk_state_test.json")

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def test_reiner_fx_verfall_haelt_nicht(self):
        self.state.update_equity_guard(172000.0, basis_key=KEY,
                                       native_beitraege=_beitraege())
        self.assertEqual(self.state.native_equity_ccy, "USD+USDC")
        # Beide Kurse fallen um ~4 %, native Mengen unveraendert.
        halted = self.state.update_equity_guard(165000.0, basis_key=KEY,
            native_beitraege=_beitraege(usdc_rate=0.816, usd_rate=0.835))
        self.assertFalse(halted)
        self.assertFalse(self.state.equity_guard_halted)
        self.assertAlmostEqual(self.state.native_drawdown_pct, 0.0, places=9)
        self.assertLess(self.state.fx_drawdown_pct, -0.03)

    def test_nativer_handelsverlust_haelt_auch_bei_fx_maskierung(self):
        self.state.update_equity_guard(172000.0, basis_key=KEY,
                                       native_beitraege=_beitraege())
        # Basiswert stabil (FX gestiegen), aber USDC-Lane -8 % nativ
        # (Gesamtnativ ca. -4 %).
        halted = self.state.update_equity_guard(172000.0, basis_key=KEY,
            native_beitraege=_beitraege(usdc=92000.0, usdc_rate=0.885, usd_rate=0.905))
        self.assertTrue(halted)
        self.assertTrue(self.state.equity_guard_halted)
        self.assertLess(self.state.native_drawdown_pct, -0.03)

    def test_kurse_bleiben_am_tagesstart_eingefroren(self):
        self.state.update_equity_guard(172000.0, basis_key=KEY,
                                       native_beitraege=_beitraege())
        start = dict(self.state.native_fx_day_start)
        self.state.update_equity_guard(171000.0, basis_key=KEY,
            native_beitraege=_beitraege(usdc_rate=0.80, usd_rate=0.82))
        self.assertEqual(self.state.native_fx_day_start, start)

    def test_lane_set_wechsel_startet_tagesbasis_neu(self):
        # Erst eine Lane (10.1.10-Pfad), dann Freigabe der zweiten untertaegig.
        self.state.update_equity_guard(85000.0, basis_key=KEY,
                                       native_equity=100000.0, native_ccy="USDC")
        halted = self.state.update_equity_guard(172000.0, basis_key=KEY,
                                                native_beitraege=_beitraege())
        self.assertFalse(halted)
        self.assertFalse(self.state.equity_guard_halted)
        self.assertEqual(self.state.native_equity_ccy, "USD+USDC")
        # Neue Basis == aktueller synthetischer Wert, kein Phantom-Drawdown/-Gewinn.
        self.assertAlmostEqual(self.state.native_drawdown_pct, 0.0, places=9)

    def test_fehlender_kurs_liefert_keine_native_reihe(self):
        beitraege = _beitraege()
        beitraege["USD"]["rate_to_basis"] = None
        self.state.update_equity_guard(172000.0, basis_key=KEY,
                                       native_beitraege=beitraege)
        self.assertEqual(self.state.native_equity_ccy, "")
        self.assertEqual(self.state.day_start_native_equity, 0.0)

    def test_einzellane_verhalten_unveraendert(self):
        # Der 10.1.10-Pfad (genau eine Waehrung) bleibt bit-identisch nutzbar.
        self.state.update_equity_guard(87000.0, basis_key=KEY,
                                       native_equity=100000.0, native_ccy="USDC")
        halted = self.state.update_equity_guard(87000.0, basis_key=KEY,
                                                native_equity=96000.0, native_ccy="USDC")
        self.assertTrue(halted)
        self.assertLess(self.state.native_drawdown_pct, -0.03)


class AltCheckpointsBleibenGueltig(unittest.TestCase):
    def test_neues_zustandsfeld_ist_optional_klassifiziert(self):
        import risk_basis_review
        self.assertIn("native_fx_day_start", risk_basis_review.OPTIONAL_FIELDS_10_2_0)
        self.assertIn("native_fx_day_start", risk_basis_review.OPTIONAL_FIELDS)

    def test_altzustand_ohne_neues_feld_ist_ladbar(self):
        import json
        import risk_manager
        state = risk_manager.RiskState()
        payload = json.loads(json.dumps(state.__dict__, default=str))
        payload.pop("native_fx_day_start", None)
        decoded = risk_manager.RiskState._decode_payload(dict(payload))
        self.assertNotIn("native_fx_day_start", decoded)  # Default greift

    def test_kaputte_tagesstartkurse_werden_abgelehnt(self):
        import json
        import risk_manager
        state = risk_manager.RiskState()
        payload = json.loads(json.dumps(state.__dict__, default=str))
        payload["native_fx_day_start"] = {"USDC": -1.0}
        with self.assertRaises(ValueError):
            risk_manager.RiskState._decode_payload(payload)


class RiskPotsVerdrahtung(unittest.TestCase):
    def test_mehrlagen_beitraege_werden_gebaut_und_durchgereicht(self):
        source = (ROOT / "risk_pots.py").read_text(encoding="utf-8")
        self.assertIn("native_beitraege", source)
        self.assertIn("elif len(waehrungen) >= 2:", source)
        self.assertIn("keine erfundenen Paritaeten", source)


if __name__ == "__main__":
    unittest.main()
