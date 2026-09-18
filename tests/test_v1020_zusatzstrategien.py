"""10.2.0: Zusatzstrategien -- Signal-Identitaet, Snapshots, Modusschalter, WebUI-Verdrahtung.

Die sechs Zusatzstrategien muessen im Bot exakt dieselben Signale erzeugen wie
im Backtest V6, ihre Snapshots muessen nachrechenbar sein, und beide
Laufzeitschalter muessen dieselbe konservative Politik verfolgen wie der
Freqtrade-Modus (nur neue Einstiege, ausdrueckliche Bestaetigung).
"""
import ast
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

try:
    import pandas as pd
    import numpy as np
    PANDAS = True
except Exception:
    PANDAS = False


def _trendreihe(perioden=420, drift=0.002):
    idx = pd.date_range("2024-01-01", periods=perioden, freq="1D", tz="UTC")
    x = np.arange(float(perioden))
    close = pd.Series(100.0 * np.exp(drift * x + 0.01 * np.sin(x / 9.0)), index=idx)
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * 1.006, "low": close * 0.994,
        "close": close, "volume": 1000.0,
    })
    # Letzte Kerze weg: im Test zaehlt nur Abgeschlossenes, unabhaengig von der Uhr.
    return df.iloc[:-1]


@unittest.skipUnless(PANDAS, "pandas erforderlich; laeuft im Pi-Volltest")
class SignalIdentitaetMitBacktestV6(unittest.TestCase):
    """Bot und Backtest V6 muessen dieselbe Signal-Mathematik besitzen."""

    @classmethod
    def setUpClass(cls):
        import zusatz_strategien
        cls.z = zusatz_strategien
        sh = (ROOT / "NEXUS_Universum_Backtest_V6.sh").read_text(encoding="utf-8")
        py = sh.split("<<'PY'", 1)[1].rsplit("\nPY\n", 1)[0]
        tree = ast.parse(py)
        wanted = {"wilder_rsi", "true_range_atr", "normalize_frame",
                  "rsi2_signals", "high52w_signals", "golden_cross_signals",
                  "tsmom_signals", "keltner_signals", "macd_trend_signals"}
        picked = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        cls.assertTrue(len(picked) == len(wanted),
                        f"Backtest V6 fehlt Signalfunktionen: {wanted - {n.name for n in picked}}")
        modul = ast.Module(body=picked, type_ignores=[])
        cls.backtest_ns = {"pd": pd, "np": np}
        exec(compile(ast.fix_missing_locations(modul), "<v6>", "exec"), cls.backtest_ns)

    @classmethod
    def assertTrue(cls, cond, msg=""):  # setUpClass-Helfer
        if not cond:
            raise AssertionError(msg)

    def test_signale_identisch_auf_gemeinsamen_daten(self):
        df = _trendreihe()
        paare = [
            ("RSI2_MEAN_REVERSION", "rsi2_signals"),
            ("HIGH_52W_MOMENTUM", "high52w_signals"),
            ("GOLDEN_CROSS_TREND", "golden_cross_signals"),
            ("TSMOM_LONG_FLAT", "tsmom_signals"),
            ("KELTNER_BREAKOUT", "keltner_signals"),
            ("MACD_TREND_CRYPTO", "macd_trend_signals"),
        ]
        for name, fn in paare:
            with self.subTest(strategie=name):
                bot = self.z.STRATEGIEN[name]["builder"](df)
                backtest = self.backtest_ns[fn](df)
                for spalte in ("enter_signal", "exit_signal"):
                    self.assertTrue(
                        bot[spalte].fillna(False).equals(backtest[spalte].fillna(False)),
                        f"{name}.{spalte} weicht vom Backtest V6 ab")


@unittest.skipUnless(PANDAS, "pandas erforderlich; laeuft im Pi-Volltest")
class SnapshotUndBewertung(unittest.TestCase):
    def setUp(self):
        import zusatz_strategien
        self.z = zusatz_strategien

    def test_sechs_strategien_mit_brokerbindung(self):
        self.assertEqual(len(self.z.ALLE_STRATEGIEN), 6)
        for name in self.z.ETORO_STRATEGIEN:
            self.assertEqual(self.z.STRATEGIEN[name]["broker"], "etoro")
        for name in self.z.OKX_STRATEGIEN:
            self.assertEqual(self.z.STRATEGIEN[name]["broker"], "okx")

    def test_snapshot_nachrechenbar_und_stabil(self):
        for name in self.z.ALLE_STRATEGIEN:
            a = self.z.parameter_snapshot(name)
            b = self.z.parameter_snapshot(name)
            self.assertEqual(a["parameter_hash"], b["parameter_hash"])
            self.assertEqual(len(a["parameter_hash"]), 64)
            self.assertTrue(a["strategy_version"].startswith("NEXUS-ZUSATZ-"))
            self.assertEqual(a["timeframe"], "1 day")

    def test_hashes_unterscheiden_strategien(self):
        hashes = {self.z.parameter_hash(n) for n in self.z.ALLE_STRATEGIEN}
        self.assertEqual(len(hashes), 6)

    def test_bewerte_liefert_signale_und_atr(self):
        df = _trendreihe()
        # TSMOM: die 90-Tage-Drift (+0,2 %/Tag) dominiert die Sinuswelle sicher.
        b = self.z.bewerte("TSMOM_LONG_FLAT", df)
        self.assertTrue(b.entry)
        self.assertFalse(b.exit)
        self.assertGreater(b.atr, 0.0)
        self.assertIn("mom_entry", b.indicators)
        m = self.z.bewerte("MACD_TREND_CRYPTO", df)
        self.assertIn("macd", m.indicators)
        self.assertIsInstance(m.entry, bool)

    def test_bewerte_verlangt_mindestkerzen(self):
        df = _trendreihe(150)
        with self.assertRaises(ValueError):
            self.z.bewerte("RSI2_MEAN_REVERSION", df)

    def test_laufende_tageskerze_wird_verworfen(self):
        from datetime import datetime, timezone
        df = _trendreihe(80)
        heute = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
        laufend = pd.DataFrame({"open": [1e9], "high": [2e9], "low": [1e9],
                                 "close": [2e9], "volume": [1.0]}, index=[heute])
        mit_laufender = pd.concat([df, laufend])
        b = self.z.bewerte("KELTNER_BREAKOUT", mit_laufender)
        self.assertLess(b.close, 1e6, "laufende Tageskerze darf nicht bewertet werden")


class Modusschalter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def test_okx_zusatzmodi_registriert(self):
        import crypto_strategy_mode as csm
        for mode in ("TSMOM_LONG_FLAT", "KELTNER_BREAKOUT", "MACD_TREND_CRYPTO"):
            self.assertIn(mode, csm.VALID_MODES)
            self.assertIn(mode, csm.ZUSATZ_MODES)
        # Bestehende Modi unveraendert.
        for mode in ("NEXUS_STANDARD", "FREQTRADE_SAMPLE", "CRYPTO_PAUSED"):
            self.assertIn(mode, csm.VALID_MODES)

    def test_okx_snapshot_und_timeframe(self):
        import crypto_strategy_mode as csm
        csm.set_mode("KELTNER_BREAKOUT", source="test", notify=False)
        try:
            snap = csm.entry_snapshot()
            self.assertEqual(snap["entry_strategy_mode"], "KELTNER_BREAKOUT")
            self.assertTrue(snap["parameter_hash"])
            self.assertTrue(csm.strategy_is_resolved(snap))
            self.assertEqual(csm.signal_timeframe(), "1 day")
        finally:
            csm.set_mode("NEXUS_STANDARD", source="test", notify=False)

    def test_etoro_schalter_faellt_konservativ_zurueck(self):
        import etoro_strategy_mode as esm
        self.assertEqual(esm.current_mode(), "NEXUS_STANDARD")
        esm.set_mode("GOLDEN_CROSS_TREND", source="test", notify=False)
        self.assertEqual(esm.current_mode(), "GOLDEN_CROSS_TREND")
        # Beschaedigte Datei -> NEXUS Standard, niemals eine schaerfere Strategie.
        esm.path().write_text("{kaputt", encoding="utf-8")
        self.assertEqual(esm.current_mode(), "NEXUS_STANDARD")
        self.assertIn("error", esm.status())

    def test_etoro_schalter_lehnt_fremde_modi_ab(self):
        import etoro_strategy_mode as esm
        for schlecht in ("CRYPTO_PAUSED", "FREQTRADE_SAMPLE", "TSMOM_LONG_FLAT", "PULSAR", ""):
            with self.assertRaises(ValueError):
                esm.set_mode(schlecht, source="test", notify=False)

    def test_settings_store_verlangt_bestaetigung(self):
        from webui.settings_store import set_crypto_strategy_mode, set_etoro_strategy_mode
        with self.assertRaises(ValueError):
            set_crypto_strategy_mode("TSMOM_LONG_FLAT", source="test", confirm="")
        with self.assertRaises(ValueError):
            set_etoro_strategy_mode("RSI2_MEAN_REVERSION", source="test", confirm="falsch")


class WebUiVerdrahtung(unittest.TestCase):
    def test_settings_template_kennt_alle_strategien(self):
        html = (ROOT / "webui" / "templates" / "settings.html").read_text(encoding="utf-8")
        for kennung in ("crypto-tsmom", "crypto-keltner", "crypto-macd",
                        "etoro-strategy-standard", "etoro-strategy-rsi2",
                        "etoro-strategy-high52", "etoro-strategy-goldencross",
                        "STRATEGIE AKTIVIEREN"):
            self.assertIn(kennung, html)

    def test_settings_js_bedient_beide_schalter(self):
        js = (ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
        for kennung in ("setEtoroStrategy", "/api/etoro-strategy",
                        "TSMOM_LONG_FLAT", "GOLDEN_CROSS_TREND",
                        "STRATEGIE AKTIVIEREN", "etoro-strategy-state"):
            self.assertIn(kennung, js)

    def test_app_route_vorhanden(self):
        text = (ROOT / "webui" / "app.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/etoro-strategy")', text)
        self.assertIn("set_etoro_strategy_mode", text)

    def test_engine_verdrahtung(self):
        engine = (ROOT / "crypto_engine.py").read_text(encoding="utf-8")
        for kennung in ("_zusatz_cursor", "_zusatz_exit", "ZUSATZ_MODES",
                        "Tageskerze bereits verarbeitet"):
            self.assertIn(kennung, engine)
        trader = (ROOT / "live_trader.py").read_text(encoding="utf-8")
        for kennung in ("etoro_strategy_mode", "etoro_zusatz_mode",
                        "zusatz_strategien", "ist_zusatzstrategie"):
            self.assertIn(kennung, trader)


if __name__ == "__main__":
    unittest.main()
