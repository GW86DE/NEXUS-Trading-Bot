"""Tests fuer die Neuerungen in 6.0: Boersenkalender, Sitzungsmeldungen,
Quellenrollen und Einstellungsuebernahme."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NY = ZoneInfo("America/New_York")


class BoersenkalenderTest(unittest.TestCase):
    """Der Kalender ersetzt eine API -- er muss exakt stimmen."""

    # Offizielle NYSE-Feiertage zur Gegenprobe
    OFFIZIELL = {
        2025: ["2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
               "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25"],
        2026: ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
               "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"],
        2027: ["2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
               "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"],
    }

    def test_feiertage_stimmen_mit_nyse_kalender(self):
        from market_calendar import feiertage
        for jahr, soll in self.OFFIZIELL.items():
            with self.subTest(jahr=jahr):
                ist = sorted(d.isoformat() for d in feiertage(jahr))
                self.assertEqual(ist, sorted(soll))

    def test_wochenende_ist_kein_handelstag(self):
        from market_calendar import ist_handelstag
        self.assertFalse(ist_handelstag(date(2026, 8, 22))[0])   # Samstag
        self.assertFalse(ist_handelstag(date(2026, 8, 23))[0])   # Sonntag
        self.assertTrue(ist_handelstag(date(2026, 8, 21))[0])    # Freitag

    def test_verkuerzte_tage_werden_erkannt(self):
        from market_calendar import verkuerzte_tage, schlusszeit
        kurz = verkuerzte_tage(2025)
        self.assertIn(date(2025, 11, 28), kurz)      # Tag nach Thanksgiving
        self.assertIn(date(2025, 12, 24), kurz)      # Heiligabend
        zeit, grund = schlusszeit(date(2025, 11, 28))
        self.assertEqual(zeit.hour, 13)
        self.assertTrue(grund)

    def test_kein_arbeitszyklus_an_feiertagen(self):
        """Der eigentliche Zweck: keine KI-/Newsabfragen an geschlossenen Tagen."""
        from market_calendar import darf_arbeiten
        # Thanksgiving 2026, mitten am Tag
        ok, grund = darf_arbeiten(datetime(2026, 11, 26, 11, 0, tzinfo=NY))
        self.assertFalse(ok)
        self.assertIn("Thanksgiving", grund)

    def test_arbeitszyklus_waehrend_handelszeit(self):
        from market_calendar import darf_arbeiten
        ok, _ = darf_arbeiten(datetime(2026, 8, 21, 11, 0, tzinfo=NY))
        self.assertTrue(ok)

    def test_vorlauf_vor_handelsbeginn(self):
        from market_calendar import darf_arbeiten
        # 10 Minuten vor Eroeffnung: arbeiten erlaubt (Kerzen vorbereiten)
        ok, _ = darf_arbeiten(datetime(2026, 8, 21, 9, 20, tzinfo=NY), puffer_minuten=20)
        self.assertTrue(ok)
        # 2 Stunden vorher: nicht
        ok, _ = darf_arbeiten(datetime(2026, 8, 21, 7, 30, tzinfo=NY), puffer_minuten=20)
        self.assertFalse(ok)


class SitzungsmeldungTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._dir
        for m in ("market_notifier",):
            sys.modules.pop(m, None)

    def test_erststart_meldet_nicht(self):
        """Sonst kaeme nach jeder Neuinstallation eine sinnlose Meldung."""
        import market_notifier as mn
        mn.STATE = Path(self._dir) / "s.json"
        gesendet = []
        r = mn.pruefe_und_melde(lambda s, t: gesendet.append(s),
                                jetzt=datetime(2026, 8, 21, 11, 0, tzinfo=NY))
        self.assertIsNone(r)
        self.assertEqual(gesendet, [])

    def test_oeffnung_und_schluss_je_einmal(self):
        import market_notifier as mn
        mn.STATE = Path(self._dir) / "s2.json"
        gesendet = []
        f = lambda s, t: gesendet.append(s)
        mn.pruefe_und_melde(f, jetzt=datetime(2026, 8, 21, 11, 0, tzinfo=NY))   # Erststart
        self.assertEqual(mn.pruefe_und_melde(f, jetzt=datetime(2026, 8, 21, 16, 30, tzinfo=NY)), "CLOSE")
        self.assertIsNone(mn.pruefe_und_melde(f, jetzt=datetime(2026, 8, 21, 18, 0, tzinfo=NY)))
        self.assertEqual(mn.pruefe_und_melde(f, jetzt=datetime(2026, 8, 24, 9, 45, tzinfo=NY)), "OPEN")
        self.assertIsNone(mn.pruefe_und_melde(f, jetzt=datetime(2026, 8, 24, 12, 0, tzinfo=NY)))
        self.assertEqual(gesendet, ["BOERSE GESCHLOSSEN", "BOERSE GEOEFFNET"])


class QuellenrollenTest(unittest.TestCase):
    def test_dauerhafte_fehler_werden_lange_pausiert(self):
        """HTTP 402/401 behebt sich nicht durch Warten."""
        from news_sources import MultiSourceNews
        c = MultiSourceNews()
        for text in ("FMP HTTP 402: Payment Required",
                     "401 Client Error: Unauthorized",
                     "This endpoint is not available under your current subscription"):
            with self.subTest(text=text[:30]):
                self.assertGreaterEqual(c._failure_backoff("FMP", RuntimeError(text)), 86400)

    def test_voruebergehende_fehler_kurz_pausiert(self):
        from news_sources import MultiSourceNews
        c = MultiSourceNews()
        self.assertLess(c._failure_backoff("Yahoo Finance", RuntimeError("Connection timeout")), 3600)

    def test_gdelt_bekommt_laengere_sperre_als_andere(self):
        from news_sources import MultiSourceNews
        c = MultiSourceNews()
        gdelt = c._failure_backoff("GDELT", RuntimeError("429 Too Many Requests"))
        yahoo = c._failure_backoff("Yahoo Finance", RuntimeError("429 Too Many Requests"))
        self.assertGreater(gdelt, yahoo)


class MigrationTest(unittest.TestCase):
    def test_neue_zustandsdateien_werden_uebernommen(self):
        """Ohne diese Eintraege begaenne 6.0 wieder bei null."""
        import settings_migration as sm
        for name in ("market_session_state.json", "approved_universe.json",
                     "universe_proposals.json", "news_source_status.json",
                     "telegram_queue.json"):
            self.assertIn(name, sm.PERSISTENT_FILES, f"{name} fehlt in der Uebernahme")

    def test_zugangsdaten_werden_uebernommen(self):
        import settings_migration as sm
        for name in ("etoro_credentials.json", "telegram_credentials.json",
                     "openai_ai_settings.json"):
            self.assertIn(name, sm.PERSISTENT_FILES)


if __name__ == "__main__":
    unittest.main()
