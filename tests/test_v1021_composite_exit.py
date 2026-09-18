"""10.2.1: Zusammengesetzter Verkaufsbeweis, Gap-Aufloesung, EUR-Autobewertung.

Kernszenario ist der reale DOGE-Fall vom 17.09.2026: eigene OCO-TP-Order
teilerfuellt (1.575,48 via DOGE-USD, Abrechnung USDC) plus zwei manuelle
Market-Orders ueber DOGE-EUR (1.280,1 teilgefuellt-storniert + 4.478,6
ausgefuehrt) decken zusammen exakt die Buchmenge 7.334,18847.

Kein Test erzeugt eine Broker-, Netzwerk- oder Telegram-Aktion.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

MENGE = 7334.18847
TP_QTY = 1575.48
EUR1_QTY = 1280.1
EUR2_QTY = MENGE - TP_QTY - EUR1_QTY  # 4478.60847 -> exakt aufgehend


def _fill(oid, inst, trade_id, qty, px, fee, fee_ccy, ts, quote):
    return {"instId": inst, "ordId": oid, "tradeId": str(trade_id), "side": "sell",
            "fillSz": str(qty), "fillPx": str(px), "fee": str(-fee),
            "feeCcy": fee_ccy, "fillTime": str(ts), "ts": str(ts),
            "tradeQuoteCcy": quote}


def _doge_gruppen():
    tp = dict(
        status={"instId": "DOGE-USD", "ordId": "tp-1", "side": "sell",
                 "state": "canceled", "accFillSz": str(TP_QTY),
                 "tradeQuoteCcy": "USDC"},
        fills=[_fill("tp-1", "DOGE-USD", 501, TP_QTY, 0.0831, 0.13, "USDC",
                     1758100000000, "USDC")])
    eur1 = dict(
        status={"instId": "DOGE-EUR", "ordId": "eur-1", "side": "sell",
                 "state": "canceled", "accFillSz": str(EUR1_QTY)},
        fills=[_fill("eur-1", "DOGE-EUR", 502, EUR1_QTY, 0.126, 0.1613, "EUR",
                     1758105817000, "EUR")])
    eur2 = dict(
        status={"instId": "DOGE-EUR", "ordId": "eur-2", "side": "sell",
                 "state": "filled", "accFillSz": str(EUR2_QTY)},
        fills=[_fill("eur-2", "DOGE-EUR", 503, EUR2_QTY, 0.11837, 0.5301, "EUR",
                     1758105849000, "EUR")])
    return [tp, eur1, eur2]


class CompositeReceipt(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def test_doge_mischfall_wird_vollstaendig_belegt(self):
        from okx_external_settlement import composite_receipt
        r = composite_receipt(_doge_gruppen(), account="konto-1", environment="DEMO",
                              entry_instrument="DOGE-USDC", expected_quantity=MENGE,
                              lot_size=0.0001, own_order_ids={"tp-1"})
        self.assertTrue(r["confirmed"])
        self.assertEqual(r["beweisart"], "ZUSAMMENGESETZTER_VERKAUF")
        self.assertAlmostEqual(r["quantity"], MENGE, places=6)
        self.assertEqual(sorted(r["legs"]), ["EUR", "USDC"])
        self.assertEqual(r["own_order_ids"], ["tp-1"])
        self.assertTrue(r["manual_confirmed"])  # Nutzerorders enthalten
        self.assertEqual(len(r["fill_ids"]), 3)
        self.assertIsNone(r["native_currency"])  # zwei Waehrungen -> kein Mischkurs

    def test_teilgefuellt_stornierte_order_ist_terminal_belegbar(self):
        from okx_external_settlement import composite_receipt
        gruppen = _doge_gruppen()
        self.assertEqual(gruppen[1]["status"]["state"], "canceled")
        r = composite_receipt(gruppen, account="konto-1", environment="DEMO",
                              entry_instrument="DOGE-USDC", expected_quantity=MENGE,
                              lot_size=0.0001)
        self.assertTrue(r["confirmed"])

    def test_unterdeckung_und_ueberdeckung_brechen_ab(self):
        from okx_external_settlement import composite_receipt
        with self.assertRaises(ValueError):
            composite_receipt(_doge_gruppen()[:2], account="konto-1", environment="DEMO",
                              entry_instrument="DOGE-USDC", expected_quantity=MENGE,
                              lot_size=0.0001)
        with self.assertRaises(ValueError):
            composite_receipt(_doge_gruppen(), account="konto-1", environment="DEMO",
                              entry_instrument="DOGE-USDC",
                              expected_quantity=MENGE - 100.0, lot_size=0.0001)

    def test_ordermenge_muss_durch_fills_belegt_sein(self):
        from okx_external_settlement import composite_receipt
        gruppen = _doge_gruppen()
        gruppen[2]["status"]["accFillSz"] = str(EUR2_QTY + 1.0)
        with self.assertRaises(ValueError):
            composite_receipt(gruppen, account="konto-1", environment="DEMO",
                              entry_instrument="DOGE-USDC", expected_quantity=MENGE,
                              lot_size=0.0001)

    def test_widerspruechlicher_doppel_fill_bricht_ab(self):
        from okx_external_settlement import composite_receipt
        gruppen = _doge_gruppen()
        dup = dict(gruppen[2]["fills"][0]); dup["fillPx"] = "0.99"
        gruppen[2]["fills"].append(dup)
        with self.assertRaises(ValueError):
            composite_receipt(gruppen, account="konto-1", environment="DEMO",
                              entry_instrument="DOGE-USDC", expected_quantity=MENGE,
                              lot_size=0.0001)

    def test_fehlende_gebuehrenwaehrung_bricht_ab(self):
        from okx_external_settlement import composite_receipt
        gruppen = _doge_gruppen()
        gruppen[0]["fills"][0]["feeCcy"] = ""
        with self.assertRaises(ValueError):
            composite_receipt(gruppen, account="konto-1", environment="DEMO",
                              entry_instrument="DOGE-USDC", expected_quantity=MENGE,
                              lot_size=0.0001)


class LedgerVerbuchung(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def _lege_offenen_trade_an(self):
        import trade_ledger as tl
        tl.init_ledger()
        with tl._LOCK, tl._connect() as con:
            con.execute("""INSERT INTO trades(broker,symbol,asset_type,waehrung,menge,
                einstieg_preis,eingestiegen_am,paper,broker_position_id,entry_order_id,
                broker_account_fingerprint,ownership_status,accounting_kind,
                reconciliation_status)
                VALUES('okx','DOGE','crypto','USDC',?, 0.07952986639,
                '2026-09-16T08:57:02+00:00',1,'DOGE-USDC','entry-1','konto-1',
                'VERIFIED','TRADE','BROKER_STATE_UNKNOWN')""", (MENGE,))
            tid = con.execute("SELECT trade_id FROM trades WHERE entry_order_id='entry-1'").fetchone()[0]
            con.execute("""INSERT INTO okx_balance_gaps VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, 'konto-1', 'DEMO', 'DOGE-USDC', 'entry-1', str(MENGE),
                 '0.0', 'PENDING', 'x', 'x', ''))
            con.commit()
        return tid

    def test_record_composite_exit_schliesst_und_loest_bestandsbeleg(self):
        from okx_external_settlement import composite_receipt, record_composite_exit
        import trade_ledger as tl
        tid = self._lege_offenen_trade_an()
        evidence = composite_receipt(_doge_gruppen(), account="konto-1",
            environment="DEMO", entry_instrument="DOGE-USDC",
            expected_quantity=MENGE, lot_size=0.0001, own_order_ids={"tp-1"})
        out = record_composite_exit(entry_order_id="entry-1",
            entry_instrument="DOGE-USDC", account="konto-1", paper=True,
            evidence=evidence, residual=evidence["residual"])
        self.assertEqual(out, tid)
        with tl._connect() as con:
            row = dict(con.execute("SELECT * FROM trades WHERE trade_id=?", (tid,)).fetchone())
            gap = dict(con.execute("SELECT * FROM okx_balance_gaps WHERE trade_id=?", (tid,)).fetchone())
        self.assertEqual(row["reconciliation_status"], "CLOSED")
        self.assertIsNotNone(row["ausgestiegen_am"])
        self.assertIsNone(row["netto_pnl"])  # keine FX-Erfindung
        self.assertEqual(gap["status"], "RESOLVED")
        doc = json.loads(row["exit_native_json"])
        self.assertEqual(doc["receipt"]["beweisart"], "ZUSAMMENGESETZTER_VERKAUF")
        self.assertIn("EUR", doc["receipt"]["legs"])
        # Wiederholung mit identischem Beleg ist idempotent.
        self.assertEqual(record_composite_exit(entry_order_id="entry-1",
            entry_instrument="DOGE-USDC", account="konto-1", paper=True,
            evidence=evidence, residual=evidence["residual"]), tid)

    def test_beziffertes_ergebnis_wird_nie_ueberschrieben(self):
        from okx_external_settlement import composite_receipt, record_composite_exit
        import trade_ledger as tl
        tid = self._lege_offenen_trade_an()
        with tl._LOCK, tl._connect() as con:
            con.execute("UPDATE trades SET netto_pnl=1.23 WHERE trade_id=?", (tid,))
            con.commit()
        evidence = composite_receipt(_doge_gruppen(), account="konto-1",
            environment="DEMO", entry_instrument="DOGE-USDC",
            expected_quantity=MENGE, lot_size=0.0001)
        with self.assertRaises(ValueError):
            record_composite_exit(entry_order_id="entry-1",
                entry_instrument="DOGE-USDC", account="konto-1", paper=True,
                evidence=evidence, residual=evidence["residual"])


class EurAutobewertung(unittest.TestCase):
    def test_calculate_composite_summiert_legs_in_eur(self):
        from okx_reference_valuation import calculate_composite, METHOD_COMPOSITE

        class FakeRates:
            document = {"source_account": "konto-1", "source_environment": "DEMO"}
            source_hash = "quelle"

            def for_fill(self, currency, fill):
                if currency == "EUR":
                    return dict(currency="EUR", eur_per_unit="1", quality="IDENTITY",
                                fill_time_ms=str(fill.get("fillTime")))
                return dict(currency=currency, eur_per_unit="0.85",
                            quality="LIVE_MARKET_PRECEDING_TRADED_MINUTE",
                            fill_time_ms=str(fill.get("fillTime")))

        def proof(oid, inst, ccy, qty, px, fee, ts, side="sell"):
            fills = [{"instId": inst, "ordId": oid, "tradeId": oid + "-t", "side": side,
                      "fillSz": str(qty), "fillPx": str(px), "fee": str(-fee),
                      "feeCcy": ccy, "fillTime": str(ts), "ts": str(ts)}]
            return dict(order_id=oid, inst_id=inst, side=side, base="DOGE",
                        currency=ccy, quantity=str(qty), gross_quantity=str(qty),
                        cash=str(qty * px + (fee if side == "sell" else -fee) * -1),
                        fill_ids=[oid + "-f"], fills=fills,
                        status={"instId": inst, "ordId": oid, "side": side,
                                 "state": "filled", "accFillSz": str(qty)})

        import okx_reference_valuation as orv
        # original_proof erneut ableiten wuerde echte prove_order-Eingaben
        # verlangen; fuer den Summentest ersetzen wir es kontrolliert.
        alt = orv.original_proof
        orv.original_proof = lambda p, account: p
        try:
            entry = proof("e", "DOGE-USDC", "USDC", 100.0, 0.08, 0.08, 1000, side="buy")
            s1 = proof("s1", "DOGE-USDC", "USDC", 40.0, 0.09, 0.036, 2000)
            s2 = proof("s2", "DOGE-EUR", "EUR", 60.0, 0.10, 0.06, 3000)
            r = calculate_composite(entry, [s1, s2], FakeRates(),
                                    account="konto-1", environment="DEMO")
        finally:
            orv.original_proof = alt
        self.assertEqual(r["method"], METHOD_COMPOSITE)
        self.assertTrue(r["cross_currency"])
        self.assertEqual(r["exit_order_ids"], ["s1", "s2"])
        # Erloese: 40*0.09*0.85 (USDC->EUR) + 60*0.10*1 abzgl. Gebuehren.
        self.assertAlmostEqual(float(r["proceeds_eur"]),
                               (40 * 0.09 - 0.036) * 0.85 + (60 * 0.10 - 0.06), places=9)
        self.assertEqual(len(r["flows"]), 3)

    def test_verkauf_vor_kauf_bricht_ab(self):
        from okx_reference_valuation import calculate_composite
        import okx_reference_valuation as orv

        class FakeRates:
            document = {"source_account": "k", "source_environment": "DEMO"}
            source_hash = "q"

            def for_fill(self, currency, fill):
                return dict(currency="EUR", eur_per_unit="1", quality="IDENTITY",
                            fill_time_ms=str(fill.get("fillTime")))

        alt = orv.original_proof
        orv.original_proof = lambda p, account: p
        try:
            entry = dict(order_id="e", inst_id="X-EUR", side="buy", base="X",
                currency="EUR", quantity="10", gross_quantity="10", cash="10",
                fill_ids=["e-f"], fills=[{"instId": "X-EUR", "ordId": "e",
                    "tradeId": "t1", "side": "buy", "fillSz": "10", "fillPx": "1",
                    "fee": "0", "feeCcy": "EUR", "fillTime": "5000", "ts": "5000"}])
            sale = dict(order_id="s", inst_id="X-EUR", side="sell", base="X",
                currency="EUR", quantity="10", gross_quantity="10", cash="10",
                fill_ids=["s-f"], fills=[{"instId": "X-EUR", "ordId": "s",
                    "tradeId": "t2", "side": "sell", "fillSz": "10", "fillPx": "1",
                    "fee": "0", "feeCcy": "EUR", "fillTime": "1000", "ts": "1000"}])
            with self.assertRaises(Exception):
                calculate_composite(entry, [sale], FakeRates(),
                                    account="k", environment="DEMO")
        finally:
            orv.original_proof = alt


class LotStaubKonsistenz(unittest.TestCase):
    """Rev 2: Der reale Pi-Befund vom 17.09. 11:08-Diagnose.

    Buchmenge 7334,18847, verkauft 7334,18 (TP 1575,48 USDC + 1280,1 + 4478,6
    EUR), 0,00847 DOGE bleiben als Lot-Staub. Rev 1 wies die EUR-Bewertung
    deshalb dauerhaft mit 'EUR-Beleg passt nicht zur aktuellen abgeschlossenen
    Handelszeile' ab; der dokumentierte Staub muss eingerechnet werden.
    """

    STAUB = 0.00847
    VERKAUFT = MENGE - 0.00847  # 7334.18 exakt

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def _pi_gruppen(self):
        tp = dict(
            status={"instId": "DOGE-USDC", "ordId": "3928938723920969728",
                     "side": "sell", "state": "canceled", "accFillSz": "1575.48",
                     "tradeQuoteCcy": "USDC"},
            fills=[_fill("3928938723920969728", "DOGE-USDC", 601, 1575.48,
                          0.0831, 0.1047379104, "USDC", 1758079000000, "USDC")])
        eur1 = dict(
            status={"instId": "DOGE-EUR", "ordId": "3929660794661634048",
                     "side": "sell", "state": "canceled", "accFillSz": "1280.1"},
            fills=[_fill("3929660794661634048", "DOGE-EUR", 602, 1280.1,
                          0.126, 0.1612926, "EUR", 1758079417000, "EUR")])
        eur2 = dict(
            status={"instId": "DOGE-EUR", "ordId": "3929661840486170624",
                     "side": "sell", "state": "filled", "accFillSz": "4478.6"},
            fills=[_fill("3929661840486170624", "DOGE-EUR", 603, 4478.6,
                          0.11837, 0.53009806, "EUR", 1758079449044, "EUR")])
        return [tp, eur1, eur2]

    def test_eur_bewertung_akzeptiert_dokumentierten_lot_staub(self):
        from okx_external_settlement import composite_receipt, record_composite_exit
        import okx_reference_valuation as orv
        import okx_residual_inventory
        import trade_ledger as tl
        # 1. Offener Trade + Verbuchung exakt wie auf dem Pi.
        tl.init_ledger()
        with tl._LOCK, tl._connect() as con:
            con.execute("""INSERT INTO trades(broker,symbol,asset_type,waehrung,menge,
                einstieg_preis,eingestiegen_am,paper,broker_position_id,entry_order_id,
                broker_account_fingerprint,ownership_status,accounting_kind,
                reconciliation_status)
                VALUES('okx','DOGE','crypto','USDC',?,0.07952986639,
                '2026-09-16T08:57:02+00:00',1,'DOGE-USDC','3927432930331594752',
                'konto-1','VERIFIED_BROKER_FILL_CHAIN','TRADE','BROKER_STATE_UNKNOWN')""",
                (MENGE,))
            con.commit()
        evidence = composite_receipt(self._pi_gruppen(), account="konto-1",
            environment="DEMO", entry_instrument="DOGE-USDC",
            expected_quantity=MENGE, lot_size=0.01,
            own_order_ids={"3928938723920969728"})
        self.assertAlmostEqual(float(evidence["residual"]), self.STAUB, places=9)
        tid = record_composite_exit(entry_order_id="3927432930331594752",
            entry_instrument="DOGE-USDC", account="konto-1", paper=True,
            evidence=evidence, residual=evidence["residual"])
        # 2. EUR-Bewertungs-Receipt (Rates/Proofs kontrolliert ersetzt).
        with tl._connect() as con:
            row = dict(con.execute("SELECT * FROM trades WHERE trade_id=?", (tid,)).fetchone())

        def kerzen_request(fill_ms):
            """Echte, formatgueltige USDC-EUR-Kerzenantwort fuer diesen Fill."""
            t = (fill_ms // 60000) * 60000 - 120000  # abgeschlossene Minute davor
            body = json.dumps({"code": "0", "msg": "", "data": [
                [str(t), "0.85", "0.851", "0.849", "0.85", "1200", "1020",
                 "1020", "1"]]})
            return dict(environment="LIVE_REFERENCE",
                url=(f"https://eea.okx.com/api/v5/market/history-candles"
                     f"?instId=USDC-EUR&bar=1m&after={fill_ms}&limit=5"),
                http_status=200, body_utf8=body,
                body_sha256=orv.sha(body), body_bytes=len(body.encode()))

        dokument = dict(schema="nexus-public-fx-followup-v1",
            source_account="konto-1", source_environment="DEMO",
            requests=[kerzen_request(1758013022000),   # Entry-Fill (USDC)
                      kerzen_request(1758079000000)])  # TP-Fill (USDC)
        rates = orv.Rates(dokument)

        def proof_aus_gruppe(g, side="sell"):
            f = g["fills"][0]
            qty = float(f["fillSz"])
            fee = -float(f["fee"])
            cash = qty * float(f["fillPx"]) - fee
            ccy = str(g["status"].get("tradeQuoteCcy") or
                      g["status"]["instId"].split("-")[-1])
            fid = f"okx:konto-1:{f['instId']}:{f['ordId']}:{f['tradeId']}"
            return dict(order_id=str(f["ordId"]), inst_id=f["instId"], side=side,
                        base="DOGE", currency=ccy, quantity=str(qty),
                        gross_quantity=str(qty), cash=str(cash),
                        fill_ids=[fid], fills=[dict(f)], status=dict(g["status"]))

        entry = dict(order_id="3927432930331594752", inst_id="DOGE-USDC",
                     side="buy", base="DOGE", currency="USDC",
                     quantity=str(MENGE), gross_quantity=str(MENGE),
                     cash=str(MENGE * 0.07952986639),
                     fill_ids=["okx:konto-1:DOGE-USDC:3927432930331594752:100"],
                     fills=[{"instId": "DOGE-USDC", "ordId": "3927432930331594752",
                              "tradeId": "100", "side": "buy", "fillSz": str(MENGE),
                              "fillPx": "0.07952986639", "fee": "0", "feeCcy": "USDC",
                              "fillTime": "1758013022000", "ts": "1758013022000"}])
        sales = [proof_aus_gruppe(g) for g in self._pi_gruppen()]
        alt_proof = orv.original_proof
        alt_entry = okx_residual_inventory.entry_from_ledger
        orv.original_proof = lambda p, account: p
        okx_residual_inventory.entry_from_ledger = (
            lambda con, row, proven_order_currency: (
                dict(fill_ids=entry["fill_ids"], cash=entry["cash"]), None))
        try:
            receipt = orv.calculate_composite(entry, sales, rates,
                                              account="konto-1", environment="DEMO")
            with tl._LOCK, tl._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                # Rev-1-Fehler trat genau hier auf; jetzt muss es durchgehen.
                gespeichert = orv.store_on_composite(con, row, receipt, rates)
                con.commit()
            self.assertTrue(gespeichert)
            with tl._connect() as con:
                geladen = orv.load_on(con, row)
            self.assertIsNotNone(geladen)
            self.assertTrue(geladen["cross_currency"])
        finally:
            orv.original_proof = alt_proof
            okx_residual_inventory.entry_from_ledger = alt_entry

    def test_abweichung_ueber_dem_dokumentierten_staub_bleibt_fehler(self):
        import okx_reference_valuation as orv
        row = dict(broker="okx", broker_account_fingerprint="konto-1", paper=1,
                   superseded_by=None, accounting_kind="TRADE",
                   ausgestiegen_am="2026-09-17T03:24:09+00:00",
                   ownership_status="VERIFIED", entry_order_id="e",
                   exit_order_id="s", waehrung="USDC",
                   broker_position_id="DOGE-USDC", menge=MENGE, trade_id=1,
                   exit_fill_ids_json="[]",
                   exit_native_json=json.dumps({"receipt": {
                       "beweisart": "ZUSAMMENGESETZTER_VERKAUF",
                       "residual": "0.00847", "fill_ids": [], "legs": {}}}))
        receipt = dict(account="konto-1", environment="DEMO",
                       entry_proof=dict(order_id="e", inst_id="DOGE-USDC",
                                         currency="USDC", fill_ids=[], cash="0"),
                       exit_proofs=[dict(order_id="s", currency="USDC",
                                          fill_ids=[], cash="0")],
                       exit_order_ids=["s"],
                       quantity=str(MENGE - 5.0),  # 5 DOGE fehlen unerklaert
                       closed_at="2026-09-17T03:24:09+00:00")
        with self.assertRaises(Exception):
            orv._native_consistency_composite(None, row, receipt)


class Verdrahtung(unittest.TestCase):
    def test_engine_nutzt_composite_und_teilfill_pfad(self):
        engine = (ROOT / "crypto_engine.py").read_text(encoding="utf-8")
        for kennung in ("composite_exit_evidence", "_position_extern_zusammengesetzt",
                        "_verbuche_eigenen_schutz_teilfill",
                        "okx_reference_autovaluation", "checks % 100 == 0"):
            self.assertIn(kennung, engine)

    def test_broker_liefert_composite_und_fx_belege(self):
        broker = (ROOT / "broker" / "okx.py").read_text(encoding="utf-8")
        for kennung in ("def composite_exit_evidence", "def fx_followup_document",
                        "nexus-public-fx-followup-v1"):
            self.assertIn(kennung, broker)

    def test_record_native_exit_loest_bestandsbeleg(self):
        text = (ROOT / "okx_external_settlement.py").read_text(encoding="utf-8")
        self.assertIn("_resolve_balance_gap", text)
        # sowohl im nativen als auch im zusammengesetzten Pfad
        self.assertGreaterEqual(text.count("_resolve_balance_gap(con"), 2)


if __name__ == "__main__":
    unittest.main()
