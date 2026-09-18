"""9.5.8 -- Guthabenfreigabe, ungeklaerte Orders und die Reichweite der Sperren.

Drei Befunde aus dem Betrieb, alle am Verhalten geprueft:

1. XLM, 03.09.2026, 17:14:24 -- die Schutzorder wurde mit 51008 abgelehnt,
   Sekunden nach einem stornierten FOK. Der Verkaufspfad wartet seit dem
   LINK/ONDO-Vorfall auf die tatsaechliche Guthabenfreigabe (OKX quittiert die
   Stornierung VOR der Freigabe); der Schutzpfad las genau einmal.

2. APT, ab 04.09.2026 -- "Kaufzustand UNKLAR", im Minutentakt. OKX antwortet
   auf eine unbekannte Order mit 51603. Das ist eine ANTWORT, wurde aber wie
   ein Netzausfall behandelt: der Registereintrag blieb, verfaellt per TTL nie,
   und ein ungeklaerter Eintrag sperrte ALLE Kryptoeinstiege.

3. Ein einziger RESIDUAL-Posten legte den kompletten Kryptoscan stumm, obwohl
   die Klassifizierung genau wusste, um welche Waehrung es geht.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ===========================================================================
# 1. Die Schutzorder wartet auf die Guthabenfreigabe
# ===========================================================================
class FreigabeClient:
    """Gibt das Guthaben erst nach N Abfragen frei -- wie OKX es tut."""
    hat_zugangsdaten = True
    clock_offset_seconds = 0.0

    def __init__(self, *, frei_spaeter: float, verzoegerung: int, gesamt: float):
        from broker.okx import OKXInstrument
        self.meta = OKXInstrument("XLM-USDC", "XLM", "USDC", "live",
                                  "0.00001", "0.000001", "1",
                                  trade_quote_ccy_list=("USDC",))
        self._frei_spaeter = float(frei_spaeter)
        self._verzoegerung = int(verzoegerung)
        self._gesamt = float(gesamt)
        self.abfragen = 0
        self.gesendete_schutzorders: list[dict] = []

    def instrument(self, _i):
        return self.meta

    def balances(self):
        self.abfragen += 1
        frei = (self._frei_spaeter if self.abfragen > self._verzoegerung
                else 0.0000581)
        return {"USDC": {"cash": 500.0, "gesamt": 500.0},
                "XLM": {"cash": frei, "gesamt": self._gesamt,
                        "frozen": self._gesamt - frei}}

    def pending_algo_orders(self, inst_id="", ord_type="oco"):
        return []

    def pending_orders(self, *_a, **_k):
        return []

    def place_algo_order(self, body):
        self.gesendete_schutzorders.append(dict(body))
        return {"algoId": "ALGO-NEU"}

    def algo_order_details(self, _a):
        return {"algoId": "ALGO-NEU", "state": "live", "sz": "560.88",
                "slTriggerPx": "0.16", "tpTriggerPx": "0.20",
                "instId": "XLM-USDC", "side": "sell"}

    def cancel_algo_orders(self, _rows):
        return []


def _xlm():
    return SimpleNamespace(name="XLM", asset_type="crypto",
                           contract=SimpleNamespace(localSymbol="XLM-USDC"))


def _broker(client):
    from broker.okx import OKXBroker
    b = OKXBroker(client=client, quote_ccy="USDC",
                  allowed_quotes=("USDC", "USD", "EUR"))
    b.account_fingerprint = lambda: "testkonto"
    return b


def test_schutz_wartet_auf_die_freigabe(monkeypatch):
    """Nach einem Storno darf nicht sofort gelesen und gesendet werden."""
    import config
    monkeypatch.setattr(config, "OKX_CANCEL_RELEASE_TIMEOUT", 5.0, raising=False)
    client = FreigabeClient(frei_spaeter=560.88, verzoegerung=2, gesamt=560.88)
    b = _broker(client)
    # Freigabe steht an -- genau die Lage nach einem stornierten FOK.
    b._freigabe_vormerken("XLM-USDC")

    frei, gedeckt = b.warte_auf_guthabenfreigabe(
        "XLM", 560.88, inst_id="XLM-USDC", lot_size=0.000001)
    assert gedeckt, "Die Freigabe wurde nicht abgewartet"
    assert frei == pytest.approx(560.88)
    assert client.abfragen > 1, "Es wurde nur ein einziges Mal gelesen"


def test_ohne_anstehende_freigabe_wird_nicht_gewartet(monkeypatch):
    """Sonst kostet jeder Zyklus fuer jede Position Sekunden."""
    import config
    monkeypatch.setattr(config, "OKX_CANCEL_RELEASE_TIMEOUT", 5.0, raising=False)
    client = FreigabeClient(frei_spaeter=560.88, verzoegerung=99, gesamt=560.88)
    b = _broker(client)
    start = time.monotonic()
    frei, gedeckt = b.warte_auf_guthabenfreigabe(
        "XLM", 560.88, inst_id="XLM-USDC", lot_size=0.000001)
    dauer = time.monotonic() - start
    assert not gedeckt
    assert client.abfragen == 1, "Ohne anstehende Freigabe wurde gepollt"
    assert dauer < 1.0, f"Der Aufruf hat {dauer:.1f}s gebraucht"


def test_fehlende_waehrung_heisst_keine_aussage(monkeypatch):
    """Ein fehlender Eintrag ist nicht "null frei" -- sonst blockiert der
    Schutz direkt nach einem Kauf (Korrektur aus 9.5.4, muss erhalten bleiben)."""
    class OhneEintrag(FreigabeClient):
        def balances(self):
            self.abfragen += 1
            return {"USDC": {"cash": 500.0, "gesamt": 500.0}}

    b = _broker(OhneEintrag(frei_spaeter=0.0, verzoegerung=0, gesamt=0.0))
    frei, gedeckt = b.warte_auf_guthabenfreigabe("XLM", 560.88,
                                                 inst_id="XLM-USDC")
    assert frei is None, "Ein fehlender Eintrag wurde als 0 gelesen"
    assert not gedeckt


def test_storno_merkt_die_freigabe_vor():
    """Nach einer erfolgreichen Stornierung muss der naechste Leser warten duerfen."""
    class StornoClient(FreigabeClient):
        def algo_order_details(self, algo_id):
            return {"algoId": algo_id, "instId": "XLM-USDC", "ordIdList": []}
        def pending_algo_orders(self, inst_id="", ord_type="oco"):
            if ord_type != "oco":
                return []
            return [{"algoId": "ALGO-ALT", "algoClOrdId": "TBP9test",
                     "instId": "XLM-USDC", "side": "sell", "sz": "560.88",
                     "state": "live", "ordType": "oco"}]

    b = _broker(StornoClient(frei_spaeter=0.0, verzoegerung=0, gesamt=560.88))
    anzahl = b.storniere_offene_orders(_xlm(), protection_algo_id="ALGO-ALT")
    assert anzahl >= 1
    assert "XLM-USDC" in b._freigabe_erwartet, (
        "Die Stornierung hat die anstehende Freigabe nicht vermerkt")


# ===========================================================================
# 2. "OKX kennt die Order nicht" ist ein Endzustand
# ===========================================================================
class UnbekannteOrderClient:
    hat_zugangsdaten = True
    clock_offset_seconds = 0.0

    def __init__(self, *, code="51603", archiv=None):
        from broker.okx import OKXInstrument
        self.meta = OKXInstrument("APT-USDC", "APT", "USDC", "live",
                                  "0.001", "0.01", "0.1",
                                  trade_quote_ccy_list=("USDC",))
        self._code = code
        self._archiv = list(archiv or [])

    def instrument(self, _i):
        return self.meta

    def order_status(self, _inst_id, ord_id="", cl_ord_id=""):
        from broker.base import BrokerFehler
        # Wortlaut wie in broker/okx.py request(): "OKX-Fehler <code>: <text>".
        # Der Code wird an genau dieser Stelle gelesen, nicht irgendwo in der
        # Meldung -- sonst wuerde eine Ziffernfolge in einer ordId reichen.
        raise BrokerFehler(
            f"OKX-Fehler {self._code}: Die angesprochene Order existiert nicht.")

    def fills(self, _inst_id, limit=100, *, ord_id=""):
        return list(self._archiv)

    def fills_history_paginated(self, _inst_id, max_pages=3, *, ord_id=""):
        return list(self._archiv)

    def balances(self):
        return {"USDC": {"cash": 500.0, "gesamt": 500.0},
                "APT": {"cash": 12.0, "gesamt": 12.0, "frozen": 0.0}}

    def pending_algo_orders(self, inst_id="", ord_type="oco"):
        return []

    def pending_orders(self, *_a, **_k):
        return []

    def place_algo_order(self, body):
        return {"algoId": "ALGO-APT"}

    def algo_order_details(self, _a):
        return {"algoId": "ALGO-APT", "state": "live", "sz": "12",
                "slTriggerPx": "7.6", "tpTriggerPx": "9.4",
                "instId": "APT-USDC", "side": "sell"}

    def cancel_algo_orders(self, _rows):
        return []


def _apt_broker(client):
    from broker.okx import OKXBroker
    b = OKXBroker(client=client, quote_ccy="USDC",
                  allowed_quotes=("USDC", "USD", "EUR"))
    b.account_fingerprint = lambda: "testkonto"
    b.historical_fills = lambda base, since="": list(client._archiv)
    return b


def _alter_intent(minuten=30):
    return {
        "inst_id": "APT-USDC", "symbol": "APT", "asset_type": "crypto",
        "cl_ord_id": "N91962747665766744576", "ord_id": "", "qty": 12.0,
        "created_at": (datetime.now(timezone.utc)
                       - timedelta(minutes=minuten)).isoformat(),
    }


def test_alter_und_leere_begrenzte_history_beweisen_keine_nullausfuehrung():
    """Fehlende Auskunft darf einen moeglich uebermittelten Auftrag nie freigeben."""
    b = _apt_broker(UnbekannteOrderClient())
    assert b.reconcile_order_evidence(_alter_intent()) is None


def test_frische_order_wird_nicht_vorschnell_verworfen():
    """Eine gerade abgesendete Order ist sekundenlang nicht abfragbar."""
    b = _apt_broker(UnbekannteOrderClient())
    assert b.reconcile_order_evidence(_alter_intent(minuten=0)) is None


def test_ohne_zeitstempel_wird_nicht_geraten():
    b = _apt_broker(UnbekannteOrderClient())
    intent = _alter_intent()
    intent.pop("created_at")
    assert b.reconcile_order_evidence(intent) is None


def test_transportfehler_bleibt_keine_aussage():
    """Ein Netzausfall darf niemals "nicht ausgefuehrt" bedeuten."""
    b = _apt_broker(UnbekannteOrderClient(code="50001"))
    assert b.reconcile_order_evidence(_alter_intent()) is None


def test_archivierter_fill_schlaegt_die_unbekannt_meldung():
    """Gibt es einen echten Fill, wurde sehr wohl gekauft."""
    archiv = [{"instId": "APT-USDC", "ordId": "OID-9", "tradeId": "T-9",
               "side": "buy", "clOrdId": "N91962747665766744576",
               "fillSz": "12.0", "fillPx": "8.5", "fee": "-0.01",
               "feeCcy": "USDC", "ts": "1756900000000"}]
    b = _apt_broker(UnbekannteOrderClient(archiv=archiv))
    ergebnis = b.reconcile_order_evidence(_alter_intent())
    assert ergebnis is not None
    assert ergebnis.gross_filled_quantity == pytest.approx(12.0), (
        "Der archivierte Fill wurde uebersehen -- ein echter Kauf waere als "
        "'nie ausgefuehrt' verbucht worden")


# ===========================================================================
# 3. Eine Sperre trifft nur den betroffenen Wert
# ===========================================================================
def test_klassifizierung_benennt_die_gesperrten_waehrungen():
    import exposure_klassifizierung as ek
    buch = [SimpleNamespace(symbol="XLM", menge=560.88, inst_id="XLM-USDC",
                            herkunft="BOT", verwaltung="AUTO",
                            darf_automatisch_verkaufen=True)]
    ergebnis = ek.klassifiziere(
        {"XLM": {"cash": 100.0, "gesamt": 100.0},
         "USDC": {"cash": 500.0, "gesamt": 500.0}},
        positionsbuch=buch, offene_orders=[], schutzorders=[],
        ledger_trades=[], preise={"XLM": 0.18}, cfg=None)
    assert ergebnis["einstiege_gesperrt"]
    assert ergebnis["gesperrte_waehrungen"] == ["XLM"], (
        "Ohne die benannte Waehrung kann der Aufrufer nur global sperren")


def test_unlesbares_register_sperrt_weiterhin_global(monkeypatch):
    """Wenn gar keine Aussage moeglich ist, muss der Riegel breit bleiben."""
    import crypto_engine

    class Register:
        storage_error = ""

        @staticmethod
        def pending_for(**_kw):
            raise TimeoutError("Zustandsschloss nicht bekommen")

    engine = crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    engine._order_registry = lambda: Register()
    engine.hub = SimpleNamespace(broker=lambda _n: SimpleNamespace(demo=True))
    engine._konto_fingerprint = lambda: "testkonto"
    offen = engine._offene_order_symbole()
    assert offen == ["<REGISTER-UNLESBAR>"], (
        "Ein unlesbares Register meldete wieder 'nichts offen' -- die Sperre "
        "wuerde sporadisch gruen")


def test_ziffernfolge_in_der_meldung_taeuscht_nicht():
    """"51603" irgendwo im Text ist keine Brokerauskunft."""
    class OrdIdMitZiffern(UnbekannteOrderClient):
        def order_status(self, _inst_id, ord_id="", cl_ord_id=""):
            from broker.base import BrokerFehler
            raise BrokerFehler(
                "OKX-Ueberlast 50011: Zu viele Anfragen "
                "(ordId 516031234567890).")

    b = _apt_broker(OrdIdMitZiffern())
    assert b.reconcile_order_evidence(_alter_intent()) is None, (
        "Eine Ziffernfolge in der ordId wurde als 'Order unbekannt' gelesen")


def test_unlesbares_archiv_ist_kein_beweis():
    """Eine Ratenbegrenzung darf keinen echten Kauf verschwinden lassen."""
    from broker.base import BrokerFehler, VerbindungVerloren

    b = _apt_broker(UnbekannteOrderClient())

    def archiv_kaputt(_base, since=""):
        raise VerbindungVerloren("OKX-Ueberlast 50011: Zu viele Anfragen")

    b.historical_fills = archiv_kaputt
    assert b.reconcile_order_evidence(_alter_intent()) is None, (
        "Ein nicht erreichbares Fill-Archiv wurde als 'kein Fill' gewertet -- "
        "ein gefuellter Kauf laege danach ohne Buch und ohne Stop im Konto")


def test_mehrdeutiges_archiv_ist_kein_beweis():
    """Fills ohne eindeutige ordId beweisen weder das eine noch das andere."""
    archiv = [
        {"instId": "APT-USDC", "ordId": "OID-9", "tradeId": "T-9", "side": "buy",
         "clOrdId": "N91962747665766744576", "fillSz": "6.0", "fillPx": "8.5",
         "ts": "1756900000000"},
        {"instId": "APT-USDC", "ordId": "OID-10", "tradeId": "T-10", "side": "buy",
         "clOrdId": "N91962747665766744576", "fillSz": "6.0", "fillPx": "8.6",
         "ts": "1756900001000"},
    ]
    b = _apt_broker(UnbekannteOrderClient(archiv=archiv))
    assert b.reconcile_order_evidence(_alter_intent()) is None
