"""10.2.0: Gebuehren-Snapshots offener eToro-Positionen (Vorarbeit).

Kein Test erzeugt eine Broker-, Netzwerk- oder Telegram-Aktion.
"""
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class _Broker:
    paper = True

    def account_fingerprint(self):
        return "konto-abc"

    @staticmethod
    def _all_positions(pnl):
        cp = pnl.get("clientPortfolio") or {}
        return list(cp.get("positions") or [])


def _pnl(positions):
    return {"clientPortfolio": {"positions": positions},
            "_snapshot_id": "etoro:demo:7",
            "_snapshot_at": "2026-09-17T10:00:00+00:00"}


class FeeSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def test_gebuehrenfelder_werden_dauerhaft_gesichert(self):
        import etoro_fee_snapshot as fs
        n = fs.capture_pnl(_Broker(), _pnl([
            {"positionId": 71, "instrumentId": 5, "openRate": 500.0,
             "units": 30.0, "isBuy": True, "totalFees": 1.25, "fees": 1.0},
        ]))
        self.assertEqual(n, 1)
        beleg = fs.letzter_beleg("konto-abc", "DEMO", "71")
        self.assertIsNotNone(beleg)
        self.assertEqual(beleg["status"], "FELDER_ERFASST")
        self.assertEqual(beleg["gebuehren"]["totalFees"], 1.25)
        self.assertEqual(beleg["identitaet"]["positionId"], 71)
        self.assertEqual(beleg["snapshot_id"], "etoro:demo:7")

    def test_fehlende_felder_bleiben_ehrlich_unbekannt(self):
        import etoro_fee_snapshot as fs
        fs.capture_pnl(_Broker(), _pnl([
            {"positionId": 72, "instrumentId": 5, "openRate": 10.0, "units": 1.0},
        ]))
        beleg = fs.letzter_beleg("konto-abc", "DEMO", "72")
        self.assertEqual(beleg["status"], "KEINE_GEBUEHRENFELDER_GELIEFERT")
        self.assertEqual(beleg["gebuehren"], {})

    def test_erstsichtung_bleibt_bei_updates_erhalten(self):
        import etoro_fee_snapshot as fs
        fs.capture_pnl(_Broker(), _pnl([{"positionId": 73, "fees": 0.5}]))
        zweiter = _pnl([{"positionId": 73, "fees": 0.9}])
        zweiter["_snapshot_at"] = "2026-09-17T12:00:00+00:00"
        fs.capture_pnl(_Broker(), zweiter)
        beleg = fs.letzter_beleg("konto-abc", "DEMO", "73")
        self.assertEqual(beleg["erstmals_gesehen_utc"], "2026-09-17T10:00:00+00:00")
        self.assertEqual(beleg["zuletzt_gesehen_utc"], "2026-09-17T12:00:00+00:00")
        self.assertEqual(beleg["gebuehren"]["fees"], 0.9)

    def test_fehler_wirft_niemals_in_den_handelspfad(self):
        import etoro_fee_snapshot as fs

        class KaputterBroker:
            def account_fingerprint(self):
                raise RuntimeError("kaputt")

        self.assertEqual(fs.capture_pnl(KaputterBroker(), {}), 0)

    def test_hook_im_broker_verankert(self):
        source = (ROOT / "broker" / "etoro.py").read_text(encoding="utf-8")
        self.assertIn("from etoro_fee_snapshot import capture_pnl as capture_fees", source)


if __name__ == "__main__":
    unittest.main()
