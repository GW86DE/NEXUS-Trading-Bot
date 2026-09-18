"""9.5.7 -- Der HYPE-Fall vom 03.09.2026, mit den echten Zahlen.

BEFUND aus der Kontodiagnose:

    Konto gesamt      : 31.6721581 HYPE
    davon frei        :  0.0000581
    davon eingefroren : 31.6721000
    Schutzorder       : algoId 3889992275388477441 | HYPE-EUR | Menge 31.6721
                        SL 63.18 | TP 73.52 | Kennung TBP91b0d068a351f42f98e207079eb3b

Die Position WAR also geschuetzt. NEXUS hat die eigene Order nur nicht
wiedererkannt, weil 9.5.6 die Uebernahme an die Uebereinstimmung von SL/TP
band -- und die passten nicht:

    im Konto:       SL 63,18        TP 73,52
    NEXUS erwartet: SL 63,27786405  TP 73,63563475

Beide um denselben Faktor daneben, weil 63,18 / 0,9 = 70,20: die Schutzorder
wurde mit einem vorlaeufigen Einstiegspreis gesetzt, bevor der echte VWAP von
70,30873783 feststand.

Der Beweis lag die ganze Zeit vor: die Kennung beginnt mit "TBP9" -- das ist
NEXUS' eigenes Praefix. Ob SL und TP noch passen, sagt nichts darueber, WEM
die Order gehoert.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Echte Zahlen vom 03.09.2026
MENGE_BUCH = 31.67215810
EINSTIEG = 70.30873783
SL_ERWARTET = 63.27786405          # = Einstieg x 0,90
TP_ERWARTET = 73.63563475
SL_GESETZT = 63.18                 # = 70,20 x 0,90 (vorlaeufiger Einstieg)
TP_GESETZT = 73.52
ALGO_ID = "3889992275388477441"
KENNUNG = "TBP91b0d068a351f42f98e207079eb3b"


class HypeClient:
    hat_zugangsdaten = True
    clock_offset_seconds = 0.0

    def __init__(self, algos, frei=0.0000581, gesamt=31.6721581):
        from broker.okx import OKXInstrument
        self.meta = OKXInstrument("HYPE-EUR", "HYPE", "EUR", "live",
                                  "0.01", "0.0001", "0.1")
        self._algos = list(algos)
        self._frei = frei
        self._gesamt = gesamt
        self.gesendete_schutzorders = []
        self.geaenderte_schutzorders = []

    def instrument(self, _i): return self.meta
    def pending_orders(self, *_a, **_k): return []
    def pending_algo_orders(self, inst_id="", ord_type="oco"):
        return list(self._algos) if ord_type == "oco" else []

    def balances(self):
        return {"EUR": {"cash": 72901.38, "gesamt": 72901.38},
                "HYPE": {"cash": self._frei, "gesamt": self._gesamt,
                         "frozen": self._gesamt - self._frei}}

    def place_algo_order(self, body):
        from broker.base import BrokerFehler
        self.gesendete_schutzorders.append(dict(body))
        raise BrokerFehler(
            "OKX lehnt die Order ab (51008): Order failed. Your available HYPE "
            "balance is insufficient, and your available margin (in USD) is too "
            "low for borrowing.")

    def amend_algo_order(self, body):
        self.geaenderte_schutzorders.append(dict(body))
        # OKX fuehrt die Aenderung aus -- die Order zeigt danach die neuen Werte.
        for row in self._algos:
            if str(row.get("algoId")) == str(body.get("algoId")):
                row["slTriggerPx"] = body["newSlTriggerPx"]
                row["tpTriggerPx"] = body["newTpTriggerPx"]
        return {"algoId": body.get("algoId")}

    def cancel_algo_orders(self, _rows): return []
    def algo_order_details(self, _a): return {}


def liegende_order(algo_id=ALGO_ID, kennung=KENNUNG, sz=31.6721,
                   sl=SL_GESETZT, tp=TP_GESETZT):
    return {"algoId": algo_id, "algoClOrdId": kennung, "instId": "HYPE-EUR",
            "side": "sell", "sz": str(sz), "slTriggerPx": str(sl),
            "tpTriggerPx": str(tp), "state": "live", "ordType": "oco"}


def broker(algos, **kw):
    from broker.okx import OKXBroker
    b = OKXBroker(client=HypeClient(algos, **kw), quote_ccy="EUR")
    b.account_fingerprint = lambda: "testkonto"
    return b


def hype():
    return SimpleNamespace(name="HYPE", asset_type="crypto",
                           contract=SimpleNamespace(localSymbol="HYPE-EUR"))


def abgleich(b, **kw):
    return b.reconcile_position_protection(
        hype(), MENGE_BUCH, SL_ERWARTET, TP_ERWARTET,
        protection_algo_id=kw.get("algo_id", ""),
        protection_client_id=kw.get("client_id", ""))


# ---------------------------------------------------------------------------
# Der Kern
# ---------------------------------------------------------------------------
def test_eigene_order_wird_an_der_kennung_erkannt():
    """Nicht an SL/TP -- die sagen nichts ueber das Eigentum."""
    b = broker([liegende_order()])

    zustand = abgleich(b)

    assert zustand["protection_confirmed"] is True, (
        "Die Position IST geschuetzt -- das muss NEXUS erkennen")
    assert zustand["algo_id"] == ALGO_ID
    assert b.client.gesendete_schutzorders == [], (
        "Keine zweite Schutzorder -- genau daran haengt die 51008-Schleife")


def test_abweichende_werte_werden_geaendert_nicht_dupliziert():
    """Der Stop lag 0,10 EUR zu tief. Das gehoert korrigiert, nicht ignoriert."""
    b = broker([liegende_order()])

    zustand = abgleich(b)

    assert b.client.geaenderte_schutzorders, "Die Order muss geaendert werden"
    aenderung = b.client.geaenderte_schutzorders[0]
    assert aenderung["algoId"] == ALGO_ID
    assert float(aenderung["newSlTriggerPx"]) == pytest.approx(SL_ERWARTET, abs=0.01)
    assert float(aenderung["newTpTriggerPx"]) == pytest.approx(TP_ERWARTET, abs=0.01)
    assert "geaendert" in zustand["detail"]


def test_passende_werte_loesen_keine_aenderung_aus():
    """Kein Brokerverkehr im Zyklustakt, wenn alles stimmt."""
    b = broker([liegende_order(sl=SL_ERWARTET, tp=TP_ERWARTET)])

    zustand = abgleich(b)

    assert zustand["protection_confirmed"] is True
    assert b.client.geaenderte_schutzorders == []
    assert b.client.gesendete_schutzorders == []


def test_zweiter_takt_findet_die_order_ueber_die_kennung():
    """Nach der Uebernahme laeuft es ueber den strengen Filter -- ohne Umweg."""
    b = broker([liegende_order(sl=SL_ERWARTET, tp=TP_ERWARTET)])

    zustand = b.reconcile_position_protection(
        hype(), MENGE_BUCH, SL_ERWARTET, TP_ERWARTET,
        protection_algo_id=ALGO_ID, protection_client_id="")

    assert zustand["protection_confirmed"] is True
    assert not zustand.get("uebernommen"), (
        "Ueber die bekannte algoId gefunden ist keine Uebernahme")


# ---------------------------------------------------------------------------
# Der strukturelle Riegel
# ---------------------------------------------------------------------------
def test_keine_order_die_am_guthaben_scheitern_muss():
    """Fremde Order haelt das Guthaben: senden waere von vornherein zwecklos."""
    fremd = liegende_order(kennung="", sl=50.0, tp=90.0)   # ohne NEXUS-Kennung
    b = broker([fremd])

    zustand = abgleich(b)

    assert zustand["protection_confirmed"] is False
    assert b.client.gesendete_schutzorders == [], (
        "31,6721 sind gebunden, frei sind 0,0000581 -- die Order MUESSTE "
        "scheitern und wird deshalb nicht gesendet")
    assert "frei verfuegbar" in zustand["detail"]


def test_ohne_algo_order_wird_weiterhin_gesendet():
    """Der Riegel darf keinen Schutz verhindern, wo keiner liegt.

    Ist ueberhaupt keine Algo-Order da, ist ein knappes Guthaben etwas anderes
    (Verrechnung, Transfer). Dann soll OKX klar antworten, statt dass NEXUS
    still auf den Schutz verzichtet.
    """
    b = broker([], frei=0.0, gesamt=31.6721581)

    zustand = abgleich(b)

    assert b.client.gesendete_schutzorders, (
        "Ohne liegende Algo-Order muss der Versuch weiterhin stattfinden")
    assert zustand["protection_confirmed"] is False


def test_fehlende_guthabenangabe_blockiert_nicht():
    """Eine fehlende Messung darf keinen Schutz verhindern.

    Direkt nach einem Kauf kann die Basiswaehrung in der Guthabenantwort noch
    fehlen. Das als "null frei" zu lesen wuerde den Schutz im denkbar
    schlechtesten Moment blockieren.
    """
    b = broker([liegende_order(kennung="", sl=50.0, tp=90.0)])
    b.client.balances = lambda: {"EUR": {"cash": 100.0, "gesamt": 100.0}}

    zustand = abgleich(b)

    assert b.client.gesendete_schutzorders, (
        "Ohne Guthabenangabe wird nicht geblockt, sondern versucht")
    assert zustand["protection_confirmed"] is False


# ---------------------------------------------------------------------------
# Befunde aus der gegnerischen Pruefung der ersten 9.5.7-Fassung.
# Jeder davon war ein echter Fehler in meinem eigenen Entwurf.
# ---------------------------------------------------------------------------
def test_teildeckung_wird_aufgefuellt_statt_aufgegeben():
    """BEFUND 2 (kritisch): der Rest blieb dauerhaft ungeschuetzt.

    Meine erste Fassung hat bei einer eigenen Order mit ABWEICHENDER Menge gar
    nichts mehr gesendet -- mit der Begruendung, das Guthaben sei gebunden. Das
    gilt aber nur, wenn die Order MEHR bindet als frei ist. Deckt sie WENIGER
    ab, ist der Rest frei und gehoert geschuetzt.
    """
    # Eigene Order deckt 20 von 31,6721 -- 11,67 sind frei und ungeschuetzt.
    b = broker([liegende_order(sz=20.0)], frei=11.6721581, gesamt=31.6721581)

    zustand = abgleich(b)

    assert b.client.gesendete_schutzorders, (
        "Die fehlenden 11,67 muessen nachgeschuetzt werden")
    gesendet = float(b.client.gesendete_schutzorders[0]["sz"])
    assert gesendet == pytest.approx(11.6721, abs=0.001), (
        f"Nachgeschuetzt werden muss die Differenz, gesendet wurde {gesendet}")


def test_bekannte_algo_id_geht_nie_verloren():
    """BEFUND 2: eine weggeworfene algoId macht die Position unverkaeuflich.

    schliesse_position storniert vor dem Verkauf die eigene Schutzorder. Ohne
    ihre Kennung scheitert das -- die Position liesse sich nicht mehr
    verkaufen. Jede Rueckgabe, die eine eigene Order kennt, muss ihre Kennung
    mitgeben.
    """
    b = broker([liegende_order(sz=20.0)], frei=0.0, gesamt=31.6721581)

    zustand = abgleich(b)

    assert zustand.get("algo_id") == ALGO_ID, (
        "Die bekannte algoId darf nicht verworfen werden")


def test_ausgeloester_schutz_wird_nicht_als_fehlend_gemeldet():
    """BEFUND 1 (kritisch): ein bewiesener Schutz wurde ueberschrieben.

    Hat die eigene Schutzorder ausgeloest, laeuft sie als normale SELL-Order
    weiter. Meine erste Fassung meldete trotzdem "kein Schutz", sobald
    zusaetzlich eine aeltere eigene Algo-Order herumlag -- die exakte
    Spiegelung des Fehlers, den 9.5.7 beheben sollte.
    """
    b = broker([liegende_order(sz=20.0)], frei=0.0, gesamt=31.6721581)
    # Die ausgeloeste Order laeuft als Standard-Verkauf ueber die volle Menge.
    b.client.pending_orders = lambda *_a, **_k: [
        {"ordId": "OID9", "side": "sell", "sz": str(MENGE_BUCH), "accFillSz": "0"}]
    b.protection_exit_order_ids = lambda _a: {"OID9"}

    zustand = b.reconcile_position_protection(
        hype(), MENGE_BUCH, SL_ERWARTET, TP_ERWARTET,
        protection_algo_id="alt-1", protection_client_id="")

    assert zustand["protection_confirmed"] is True, (
        "Der Schutz laeuft als ausgeloeste Verkaufsorder -- das ist Deckung")


def test_ohne_stop_und_take_wird_nichts_bestaetigt():
    """BEFUND 5: all() ueber eine leere Bedingung ist wahr.

    Ohne gesetzten Stop UND ohne Take-Profit haette meine erste Fassung jede
    beliebige Order als "SL/TP passen exakt" bestaetigt, ohne einen einzigen
    Preis zu vergleichen.
    """
    b = broker([liegende_order()])

    zustand = b.reconcile_position_protection(
        hype(), MENGE_BUCH, 0.0, 0.0,
        protection_algo_id="", protection_client_id="")

    assert "passen exakt" not in str(zustand.get("detail") or ""), (
        "Ohne Preise darf nicht behauptet werden, sie passten")


def test_unterdeckung_gilt_nicht_als_voller_schutz():
    """BEFUND 6: 0,1 Prozent Toleranz liessen echte Luecken durch.

    Bei 100.000 DOGE waren das 100 Stueck, gemeldet als voll geschuetzt.
    Deckung gilt jetzt erst, wenn hoechstens ein Lot fehlt.
    """
    # 0,6721 zu wenig -- deutlich ueber der Mindestmenge 0,1, also schuetzbar.
    b = broker([liegende_order(sz=31.0)], frei=0.6721581, gesamt=31.6721581)

    zustand = abgleich(b)

    assert b.client.gesendete_schutzorders, (
        "Die Luecke ist schuetzbar und muss geschlossen werden")
    assert float(b.client.gesendete_schutzorders[0]["sz"]) == pytest.approx(
        0.6721, abs=0.001)


def test_rest_unter_der_mindestmenge_gilt_als_geschuetzt():
    """Gegenprobe: was OKX gar nicht schuetzen KANN, ist kein Alarm wert.

    Fehlen 0,03 HYPE bei einer Mindestmenge von 0,1, laesst sich dafuer keine
    Order stellen. Das als "ungeschuetzt" zu melden waere ein Daueralarm ohne
    Handlungsmoeglichkeit.
    """
    b = broker([liegende_order(sz=31.6421)], frei=0.03, gesamt=31.6721581)

    zustand = abgleich(b)

    assert zustand["protection_confirmed"] is True
    assert "Mindestmenge" in zustand["detail"]
    assert b.client.gesendete_schutzorders == []


def test_unbestaetigte_aenderung_ist_nicht_bestaetigt_fehlend():
    """BEFUND 8: OrderStatusUnklar wurde als 'sicher ungeschuetzt' verbucht.

    OKX hat die Aenderung angenommen, nur das Ruecklesen scheiterte. Das ist
    etwas anderes als 'kein Schutz vorhanden' -- checked muss False bleiben.
    """
    from broker.base import OrderStatusUnklar
    b = broker([liegende_order()])

    def unklar(_body):
        raise OrderStatusUnklar("Aenderung angenommen, Ruecklesen fehlgeschlagen")
    b.client.amend_algo_order = unklar

    zustand = abgleich(b)

    assert zustand["checked"] is False, (
        "Nicht rueckgelesen ist NICHT dasselbe wie bestaetigt fehlend")
    assert zustand["algo_id"] == ALGO_ID


def test_fehlgeschlagene_aenderung_erzeugt_keine_schleife():
    """BEFUND 3: jeder Takt ein neues amend -- dieselbe Minutentakt-Schleife.

    Die Order LIEGT und schuetzt, nur mit anderen Werten. Sie als fehlend zu
    melden waere die groessere Unwahrheit und wuerde den naechsten Takt erneut
    aendern lassen.
    """
    from broker.base import BrokerFehler
    b = broker([liegende_order()])

    def scheitert(_body):
        raise BrokerFehler("OKX lehnt die Aenderung ab")
    b.client.amend_algo_order = scheitert

    zustand = abgleich(b)

    assert zustand["protection_confirmed"] is True, (
        "Der bestehende Schutz bleibt aktiv -- das ist die Tatsache")
    assert zustand["algo_id"] == ALGO_ID
    assert "nicht auf SL" in zustand["detail"]
