"""10.7.1 -- Die Sperren vom 18.09.2026 loesen sich, und nachfolgende Trades laufen.

BEFUND (Diagnose 18:28 UTC, 10.7.0 Rev 2 installiert)
=====================================================
eToro, CSCO (Trade 88):
  * Der Risikozustand kannte Trade 88 nicht (kein ``ledger:88``). Die Position
    wurde 1,5 s nach dem Kauf vom Broker geschlossen, bevor der Bot sie
    bestaetigt hatte; der Abgleich verbuchte den Verkauf, meldete ihn aber nie
    an den Risikozustand. Der Abrechnungs-Worker (Intervall, Erwartungswert)
    arbeitet nur registrierte Belege ab -> nichts geschah.
  * Der Einstiegs-Gebuehrenbeleg (1,00 USD) wurde 324x pro Stunde abgelehnt:
    "Einstiegs-Fillregister enthaelt widersprechenden Beleg". ``trade_open``
    speicherte die Gebuehr mit leerer Waehrung (nur ``feeCcy`` gelesen, eToro
    liefert ``fee_currency``); der Abgleich las die leere Waehrung als
    Widerspruch. Ohne bestaetigten Einstieg kein Erwartungswert (Vorbedingung),
    kein Intervall, und der Ledgerabgleich blieb ACTIVE -> Domaene gesperrt,
    ohne Tagesreset.
OKX, BTC/ETH/XRP (Luecken 81-83):
  * Die Lot-Rest-Reparatur erkannte Rest-Zeilen nur an der Notiz
    'Rest nach Teilverkauf'. Der Positionsabgleich hatte die Zeilen 84-86
    laengst in RESIDUAL_EXPOSURE umbenannt -> "fremde offene Zeile" -> keine
    Reparatur -> drei Coins gesperrt, und genau auf diese drei kamen alle
    Kaufsignale.
  * Die Sperrliste nannte die drei zusaetzlich "BESTAND_FEHLT_UNBESTAETIGT".
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import json
from pathlib import Path

import pytest

import trade_ledger as ledger
from ledger_result import usable_net
from test_v975_execution_and_repair import engine, persist, close_ledger  # noqa: F401
from test_v1070_gebuehrenbeleg import _basis, _bestaetigte_abrechnungen

ROOT = Path(__file__).resolve().parents[1]
RESIDUAL_NOTE = ("Coin-Guthaben vorhanden, aber kein Eintrag im OKX-Positionsbuch; "
                 "wird nicht automatisch verkauft")


# ---------------------------------------------------------------------------
# OKX: Reparatur erkennt den Rest an der Struktur
# ---------------------------------------------------------------------------
def _gap(tid):
    with closing(ledger._connect()) as con:
        return con.execute("SELECT * FROM okx_balance_gaps WHERE trade_id=?", (tid,)).fetchone()


def _rest_zeile(tid):
    head = ledger.trade_detail(tid)
    reste = [r for r in ledger.entry_lineage("okx", head["broker_position_id"], head["entry_order_id"],
                                            head["broker_account_fingerprint"], paper=bool(head["paper"]))
             if not r.get("ausgestiegen_am")]
    assert len(reste) == 1
    return reste[0]


def _altlast_mit_rest(engine):
    """Luecke PENDING, Trade per TP verkauft, Rest laeuft als eigene Zeile."""
    import okx_accounting as buchung
    position, tid = persist(engine)
    row = ledger.trade_detail(tid)
    buchung.mark_balance_gap(row, 0.0, row["menge"])
    close_ledger(position, tid, 124.77)
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("UPDATE okx_balance_gaps SET status='PENDING',resolution='' WHERE trade_id=?", (tid,))
    return tid, _rest_zeile(tid)


def test_reparatur_erkennt_den_vom_positionsabgleich_umbenannten_rest(engine):
    """Der Zustand vom 18.09.: Rest-Zeile heisst RESIDUAL_EXPOSURE, Notiz ersetzt."""
    import okx_accounting as buchung
    tid, rest = _altlast_mit_rest(engine)
    assert ledger.set_reconciliation_status(int(rest["trade_id"]), "RESIDUAL_EXPOSURE", notiz=RESIDUAL_NOTE)
    rest = ledger.trade_detail(int(rest["trade_id"]))
    assert rest["reconciliation_status"] == "RESIDUAL_EXPOSURE" and rest["notiz"] == RESIDUAL_NOTE
    assert "SUI" in buchung.status("A", "DEMO")["blocked_symbols"]

    assert buchung.repair_explained_gaps("A", "DEMO") == [tid]

    gap = _gap(tid)
    assert gap["status"] == "RESOLVED" and gap["resolution"].startswith("LOT_RESIDUAL_LINEAGE:")
    assert "residual=0.001765" in gap["resolution"]
    assert not buchung.status("A", "DEMO")["blocked_symbols"]


def test_reparatur_erkennt_den_rest_auch_ohne_jede_notiz(engine):
    """Nur die Struktur zaehlt: gleiche Einstiegskette, offen, keine eigenen Fills."""
    import okx_accounting as buchung
    tid, rest = _altlast_mit_rest(engine)
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("UPDATE trades SET notiz='', reconciliation_status='CONFIRMED_OPEN' WHERE trade_id=?",
                    (int(rest["trade_id"]),))
    assert buchung.repair_explained_gaps("A", "DEMO") == [tid]
    assert _gap(tid)["status"] == "RESOLVED"


def test_offene_zeile_mit_eigenen_fills_ist_kein_rest(engine):
    """Eine echte zweite Position derselben Kette (mit Fills) beweist nichts."""
    import okx_accounting as buchung
    tid, rest = _altlast_mit_rest(engine)
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("""UPDATE trades SET notiz='', reconciliation_status='CONFIRMED_OPEN',
                       entry_fill_id='eigener-fill', entry_fill_ids_json='["eigener-fill"]'
                       WHERE trade_id=?""", (int(rest["trade_id"]),))
    assert buchung.repair_explained_gaps("A", "DEMO") == []
    assert _gap(tid)["status"] == "PENDING"


def test_nach_der_reparatur_laeuft_der_naechste_trade_des_coins_sauber(engine):
    """Coin frei -> neuer Kauf -> Vollverkauf -> keine neue Luecke, keine Sperre."""
    import okx_accounting as buchung
    tid, rest = _altlast_mit_rest(engine)
    ledger.set_reconciliation_status(int(rest["trade_id"]), "RESIDUAL_EXPOSURE", notiz=RESIDUAL_NOTE)
    assert buchung.repair_explained_gaps("A", "DEMO") == [tid]
    with closing(ledger._connect()) as con:
        buchung.require_tradable(con, "A", "DEMO", "SUI-USDC")       # frei, wirft nicht

    position, tid2 = persist(engine)
    close_ledger(position, tid2, ledger.trade_detail(tid2)["menge"])
    zustand = buchung.status("A", "DEMO")
    assert zustand["complete"] and not zustand["blocked_symbols"]
    assert _gap(tid2) is None or _gap(tid2)["status"] == "RESOLVED"
    assert buchung.repair_explained_gaps("A", "DEMO") == []


# ---------------------------------------------------------------------------
# OKX: Sperrliste ohne Doppelnennung
# ---------------------------------------------------------------------------
def test_sperrliste_nennt_buchungsbeleg_nicht_als_fehlbestand():
    from handelsfreigabe import okx_sperren
    accounting = dict(complete=True, gaps=[dict(kind="BALANCE_REDUCTION", scope="SYMBOL", ablauf="BELEG",
                                                aufloesung="Verkaufsbeleg", trade_id=82, base="BTC",
                                                symbol="BTC-EUR", sperrt=True)],
                      blocked_symbols=["BTC"], expired=[], detail="")
    guard = dict(valid=True, detail="", missing=[], exit_in_progress=[])
    sperren = okx_sperren(None, None, guard=guard, accounting=accounting)
    assert [(s.grund, s.symbol) for s in sperren] == [("BALANCE_REDUCTION", "BTC")]
    engine_src = (ROOT / "crypto_engine.py").read_text(encoding="utf-8")
    assert 'self._fehlende_symbole = {str(s).upper() for s in (guard.get("missing") or [])}' in engine_src
    assert '"missing": sorted(set(getattr(self, "_fehlende_symbole", set()) or set())' in engine_src


# ---------------------------------------------------------------------------
# eToro: Gebuehrenwaehrung wird gespeichert und leer nicht als Widerspruch gelesen
# ---------------------------------------------------------------------------
CSCO_FILL = "etoro-entry:382256862:3601191853:2026-09-18T14:15:11.123Z"


def _csco_offen(tmp_path, monkeypatch, *, fill_currency_key="fee_currency"):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as da
    monkeypatch.setattr(da, "DB_PATH", tmp_path / "decision_history.sqlite")
    ledger.init_ledger()
    fill = {"fill_id": CSCO_FILL, "ordId": "382256862", "quantity": 136.0, "price": 109.15,
            "filled_at": "2026-09-18T14:15:11.123Z", "fee": 1.0, fill_currency_key: "USD",
            "fee_source": "ETORO_V2_OPENING_DATA"}
    tid = ledger.trade_open(broker="etoro", symbol="CSCO", menge=136, einstieg_preis=109.15,
                            asset_type="stock", waehrung="USD", paper=True,
                            zeit="2026-09-18T14:15:11.123Z", broker_position_id="3601191853",
                            entry_order_id="382256862", entry_fill_id=CSCO_FILL,
                            entry_fill_ids=[CSCO_FILL], entry_fills=[fill], gebuehr=1.0,
                            broker_account_fingerprint="664e7dfbc13e0cd60e493592", decision_id=3345,
                            ownership_status="BOT_VERIFIED", reconciliation_status="CONFIRMED_OPEN",
                            critical=True)
    assert tid
    return tid


def _gespeicherter_fill(tid):
    with closing(ledger._connect()) as con:
        return con.execute("SELECT * FROM trade_entry_fills WHERE trade_id=?", (tid,)).fetchone()


def test_trade_open_speichert_die_etoro_gebuehrenwaehrung(tmp_path, monkeypatch):
    tid = _csco_offen(tmp_path, monkeypatch)
    f = _gespeicherter_fill(tid)
    assert f["fee"] == 1.0 and f["fee_currency"] == "USD"


def test_trade_open_liest_weiter_das_okx_feld(tmp_path, monkeypatch):
    tid = _csco_offen(tmp_path, monkeypatch, fill_currency_key="feeCcy")
    assert _gespeicherter_fill(tid)["fee_currency"] == "USD"


def _abgleich(entry_fee=1.0):
    return dict(broker="etoro", account="664e7dfbc13e0cd60e493592", paper=True,
                position_id="3601191853", entry_order_id="382256862",
                entry_fills=[{"fill_id": CSCO_FILL, "quantity": 136.0, "price": 109.15, "fee": entry_fee,
                              "fee_currency": "USD", "filled_at": "2026-09-18T14:15:11.123Z"}])


def test_kostenabgleich_traegt_die_fehlende_waehrung_nach(tmp_path, monkeypatch):
    """Genau der Datenbestand von Trade 88 auf dem Pi: Gebuehr 1,00, Waehrung leer."""
    tid = _csco_offen(tmp_path, monkeypatch)
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("UPDATE trade_entry_fills SET fee_currency='' WHERE trade_id=?", (tid,))
        con.execute("UPDATE trades SET entry_fee_quality='UNKNOWN', fee_quality='UNKNOWN' WHERE trade_id=?", (tid,))
    assert _gespeicherter_fill(tid)["fee_currency"] == ""

    ergebnis = ledger.reconcile_entry_fees_exact(**_abgleich())

    assert ergebnis["updated"] == 1 and ergebnis["entry_fee"] == 1.0
    assert _gespeicherter_fill(tid)["fee_currency"] == "USD"
    row = ledger.trade_detail(tid)
    assert row["entry_fee_quality"] == "CONFIRMED" and row["einstieg_gebuehr"] == 1.0


def test_kostenabgleich_lehnt_andere_waehrung_und_andere_gebuehr_weiter_ab(tmp_path, monkeypatch):
    tid = _csco_offen(tmp_path, monkeypatch)
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("UPDATE trade_entry_fills SET fee_currency='EUR' WHERE trade_id=?", (tid,))
    with pytest.raises(ledger.LedgerZuordnungUnklar, match="widersprechenden Beleg"):
        ledger.reconcile_entry_fees_exact(**_abgleich())
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("UPDATE trade_entry_fills SET fee_currency='' WHERE trade_id=?", (tid,))
    # Eine andere Gebuehr scheitert schon an den bestaetigten Kosten der Zeile
    # -- oder am Fillregister; in beiden Faellen bleibt es beim Widerspruch.
    with pytest.raises(ledger.LedgerZuordnungUnklar, match="widersprech"):
        ledger.reconcile_entry_fees_exact(**_abgleich(entry_fee=2.0))
    assert _gespeicherter_fill(tid)["fee_currency"] == "", "Abgelehnter Beleg aendert nichts"


# ---------------------------------------------------------------------------
# eToro: Der Risikozustand lernt vom Abgleich verbuchte Verkaeufe -- und rechnet ab
# ---------------------------------------------------------------------------
def _broker(args):
    return NS(name="etoro", demo=True, ist_paper=lambda: True,
              account_fingerprint=lambda: args["account"], kontowaehrung=lambda: "USD")


def _state(tmp_path):
    from risk_manager import RiskState
    return RiskState.load(tmp_path / "risk_state_etoro.json")


def _brutto_setzen(tid, wert):
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("UPDATE trades SET brutto_pnl=? WHERE trade_id=?", (wert, tid))


def test_abgleich_verbuchter_verkauf_wird_registriert_und_abgerechnet(monkeypatch, tmp_path):
    """Trade geschlossen, kein Risikobeleg, kein Netto -- ein Abgleichlauf reicht."""
    from risk_result_recovery import reconcile
    from risk_manager import kaufsperre_grund, offene_ergebnisse_heute
    tid, args = _basis(monkeypatch, tmp_path)          # 4 St. zu 100 gekauft (+1), zu 105 verkauft
    _brutto_setzen(tid, 20.0)
    _bestaetigte_abrechnungen(args, 3)
    state = _state(tmp_path)
    key = f"ledger:{tid}"
    assert key not in state.realized_receipts and not usable_net(ledger.trade_detail(tid))
    monkeypatch.setattr("risk_result_recovery._LAST", {})

    done = reconcile(state, _broker(args), account_equity=100000.0)

    assert key in done
    beleg = state.realized_receipts[key]
    assert beleg["status"] == "CONFIRMED" and beleg["pnl"] == 18.0
    assert beleg["settlement_evidence"]["quality"] == "EXPECTED_UNVERIFIED"
    assert beleg["settlement_evidence"]["source"] == "ETORO_EXPECTED_FEE_MODEL"
    row = ledger.trade_detail(tid)
    assert usable_net(row) and row["fee_quality"] == "EXPECTED_UNVERIFIED" and row["netto_pnl"] == 18
    assert kaufsperre_grund(state) == "" and offene_ergebnisse_heute(state) == 0


def _abgleich_verkauf(args, *, symbol, pid, oid, decision, minuten_alt, exit_fill):
    """Ein vom Abgleich verbuchter Verkauf: Zeile geschlossen, kein Netto, kein Risikobeleg.

    Genau die Datenlage von Trade 88: Einstieg bestaetigt (1,00 USD), Exit-Fill
    aus dem Abgleich, brutto bekannt, Gebuehren/Netto unbekannt."""
    tid = ledger.trade_open(broker="etoro", symbol=symbol, menge=2, einstieg_preis=50, asset_type="stock",
                            waehrung="USD", paper=True, zeit="2026-09-15T10:00:00+00:00",
                            broker_position_id=pid, entry_order_id=oid,
                            broker_account_fingerprint=args["account"], decision_id=decision,
                            ownership_status="BOT_VERIFIED", gebuehr=1.0, critical=True)
    wann = (datetime.now(timezone.utc) - timedelta(minutes=minuten_alt)).isoformat()
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("""UPDATE trades SET ausgestiegen_am=?, ausstieg_preis=60, brutto_pnl=20,
                       einstieg_gebuehr=1, entry_fee_quality='CONFIRMED', fee_quality='UNKNOWN',
                       exit_fill_ids_json=?, reconciliation_status='CLOSED',
                       notiz='eToro-Close ueber exakte Reconciliation-Kette verknuepft'
                       WHERE trade_id=?""", (wann, json.dumps([exit_fill]), tid))
    # Der Historienbeleg des Brokers (netProfit ohne Abschlusskosten), wie ihn der
    # Kostennachlauf nach dem bestaetigten Einstieg aufzeichnet.
    import etoro_history_accounting as history
    snapshot = dict(complete=True, account_fingerprint=args["account"], environment="DEMO",
                    snapshot_at=datetime.now(timezone.utc).isoformat(),
                    rows=[dict(positionId=int(pid), orderId=int(oid), instrumentId=1013, units=2,
                               openRate=50, closeRate=60, openTimestamp="2026-09-15T10:00:00.000Z",
                               closeTimestamp=wann, isBuy=True, leverage=1, fees=0, netProfit=20)])
    history.record_history(account=args["account"], paper=True, position_id=pid,
                           entry_order_id=oid, snapshot=snapshot)
    return tid


def test_heutiger_abgleichverkauf_sperrt_nur_bis_zur_abrechnung_im_selben_lauf(monkeypatch, tmp_path):
    """CSCO-Muster am Verkaufstag: registriert (heute, sperrend) und im selben
    Lauf per Erwartungswert beziffert -- die Kaufsperre erscheint nie."""
    from risk_result_recovery import reconcile
    from risk_manager import kaufsperre_grund, offene_ergebnisse_heute
    _, args = _basis(monkeypatch, tmp_path)
    _bestaetigte_abrechnungen(args, 3)
    tid = _abgleich_verkauf(args, symbol="CSCO", pid="3601191853", oid="382256862", decision=3345,
                            minuten_alt=20, exit_fill="etoro-close:history:3601191853:heute")
    state = _state(tmp_path)
    monkeypatch.setattr("risk_result_recovery._LAST", {})

    reconcile(state, _broker(args), account_equity=100000.0)

    beleg = state.realized_receipts[f"ledger:{tid}"]
    assert beleg["status"] == "CONFIRMED" and beleg["booked_day"] == str(state.current_date)
    assert beleg["settlement_evidence"]["quality"] == "EXPECTED_UNVERIFIED"
    assert state.realized_pnl_today == pytest.approx(18.0)
    assert offene_ergebnisse_heute(state) == 0 and kaufsperre_grund(state) == ""


def test_registrierung_ueberspringt_manuelle_und_bezifferte_zeilen(monkeypatch, tmp_path):
    from risk_result_recovery import register_closed_etoro_rows
    tid, args = _basis(monkeypatch, tmp_path)
    manuell = ledger.trade_open(broker="etoro", symbol="KO", menge=1, einstieg_preis=10, asset_type="stock",
                                waehrung="USD", paper=True, zeit="2026-09-10T10:00:00+00:00",
                                broker_position_id="91", entry_order_id="81",
                                broker_account_fingerprint=args["account"], decision_id=9101,
                                ownership_status="MANUAL", gebuehr=1.0, critical=True)
    beziffert = ledger.trade_open(broker="etoro", symbol="PEP", menge=1, einstieg_preis=10, asset_type="stock",
                                  waehrung="USD", paper=True, zeit="2026-09-11T10:00:00+00:00",
                                  broker_position_id="92", entry_order_id="82",
                                  broker_account_fingerprint=args["account"], decision_id=9102,
                                  ownership_status="BOT_VERIFIED", gebuehr=1.0, critical=True)
    with ledger._LOCK, closing(ledger._connect()) as con, con:
        con.execute("UPDATE trades SET ausgestiegen_am='2026-09-10T12:00:00+00:00', ausstieg_preis=12 WHERE trade_id=?",
                    (manuell,))
        con.execute("""UPDATE trades SET ausgestiegen_am='2026-09-11T12:00:00+00:00', ausstieg_preis=12,
                       gebuehren=2, netto_pnl=0, fee_quality='CASH_DELTA_CONFIRMED' WHERE trade_id=?""", (beziffert,))
    state = _state(tmp_path)
    vorher = state.lifetime_unknown_pnl_trades

    neu = register_closed_etoro_rows(state, args["account"], True)

    assert neu == [f"ledger:{tid}"]
    assert f"ledger:{manuell}" not in state.realized_receipts, "Fremde Position: kein Bot-Ergebnis"
    assert f"ledger:{beziffert}" not in state.realized_receipts, "Bereits beziffert: nicht doppelt zaehlen"
    assert state.lifetime_unknown_pnl_trades == vorher + 1
    assert register_closed_etoro_rows(state, args["account"], True) == [], "Zweiter Lauf tut nichts"
    assert register_closed_etoro_rows(state, "anderes-konto", True) == []


def test_registrierung_uebernimmt_bekannten_fill_schluessel_als_alias(monkeypatch, tmp_path):
    """Ein alter Beleg unter dem Fill-Schluessel wird uebernommen, nicht verdoppelt."""
    from risk_result_recovery import register_closed_etoro_rows
    tid, args = _basis(monkeypatch, tmp_path)
    row = ledger.trade_detail(tid)
    fills = json.loads(row["exit_fill_ids_json"] or "[]")
    assert fills, "Testbasis ohne Exit-Fill"
    state = _state(tmp_path)
    state.register_unknown_pnl_trade(trade_id=f"etoro:{fills[0]}")
    vorher = state.lifetime_unknown_pnl_trades

    assert register_closed_etoro_rows(state, args["account"], True) == []
    assert state.realized_receipts[f"ledger:{tid}"]["status"] == "UNKNOWN"
    assert state.realized_receipts[f"etoro:{fills[0]}"]["status"] == "ALIAS"
    assert state.lifetime_unknown_pnl_trades == vorher


def test_nachfolgender_verkauf_wird_im_naechsten_lauf_genauso_abgerechnet(monkeypatch, tmp_path):
    """Zwei Laeufe, zwei Trades: der zweite laeuft wie der erste, nichts bleibt haengen."""
    from risk_result_recovery import reconcile
    from risk_manager import kaufsperre_grund, offene_ergebnisse_heute
    tid, args = _basis(monkeypatch, tmp_path)
    _brutto_setzen(tid, 20.0)
    _bestaetigte_abrechnungen(args, 3)
    state = _state(tmp_path)
    monkeypatch.setattr("risk_result_recovery._LAST", {})
    assert f"ledger:{tid}" in reconcile(state, _broker(args), account_equity=100000.0)

    zweiter = _abgleich_verkauf(args, symbol="AMD", pid="555", oid="444", decision=4242,
                                minuten_alt=16, exit_fill="etoro-close:history:555:heute")
    monkeypatch.setattr("risk_result_recovery._LAST", {})

    done = reconcile(state, _broker(args), account_equity=100000.0)

    assert f"ledger:{zweiter}" in done
    assert state.realized_receipts[f"ledger:{zweiter}"]["pnl"] == 18.0
    assert state.realized_pnl_today == pytest.approx(18.0)
    assert offene_ergebnisse_heute(state) == 0 and kaufsperre_grund(state) == ""
    assert all(v.get("status") in ("CONFIRMED", "ALIAS") for v in state.realized_receipts.values())


# ---------------------------------------------------------------------------
# Vertrag: die Verdrahtung bleibt
# ---------------------------------------------------------------------------
def test_verdrahtung_der_vier_fixe():
    recovery = (ROOT / "risk_result_recovery.py").read_text(encoding="utf-8")
    assert "register_closed_etoro_rows(state, account, paper)" in recovery
    tl = (ROOT / "trade_ledger.py").read_text(encoding="utf-8")
    assert '(row or {}).get("feeCcy") or (row or {}).get("fee_currency")' in tl
    assert '(r["fee_currency"] and r["fee_currency"] != "USD")' in tl
    assert 'if r["fee"] is None or not r["fee_currency"]:' in tl
    accounting = (ROOT / "okx_accounting.py").read_text(encoding="utf-8")
    assert "def _ist_rest_split(row)" in accounting and "elif _ist_rest_split(row):" in accounting
    import handelsfreigabe as _hf  # 10.8.0: liegt in nexus/application/, Alias an der Wurzel
    freigabe = Path(_hf.__file__).read_text(encoding="utf-8")
    assert "LEDGERABGLEICH_OFFEN" in freigabe and "from etoro_reconciliation import buchungsluecken" in freigabe
