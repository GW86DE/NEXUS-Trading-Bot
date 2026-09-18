"""Offene Trades muessen einen Versionswechsel unbeschadet ueberstehen (v9.2).

Der Vorfall vom 31.08.2026: Nach dem Update auf 9.1 galten drei bewiesene
Krypto-Positionen ploetzlich als "Externer Bestand - ohne identische
ID-Kette". Ursache war kein Brokerproblem. In 9.1 wurde
``STARTUP_CANDLES`` von 200 auf 30 gesenkt. Das aenderte den
PARAMETER_HASH, der Hashvergleich schlug fehl, die Position wurde pausiert
-- und weil Eigentum an der Verwaltung haengte, verlor sie zugleich ihren
Eigentumsnachweis in der Anzeige.

Diese Tests halten die Trennung fest:

* Eigentum   -- ausschliesslich brokerseitig vergebene IDs
* Abgleich   -- Ledger gegen Broker
* Verwaltung -- welche Ausstiegslogik laufen darf

Ein Softwareupdate darf nur den DRITTEN Zustand beruehren.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent

# Der echte Hash der ausgelieferten 9.0.15-Strategie. Aus ihm entstand der
# Fehler; er muss als Vorgaenger erkannt werden.
HASH_9_0_15 = "c8c1883383675aafd52da451a1d4cdcf924677be3da53ee66c958f11821f3f37"
HASH_9_1 = "5611109f87491637ddaa6727534b3ce09284788be194a632d257f8fc8d98d5a2"


def bestandsposition(ce, symbol="BNB", *, version, hash_wert):
    """Eine Position, wie sie 9.0.15 auf die Platte geschrieben hat."""
    return ce.KryptoPosition(
        symbol=symbol, inst_id=f"{symbol}-USDT", menge=1.5,
        einstieg=612.4, stop=601.2, take_profit=634.9, broker_schutz=True,
        order_id="2100000000000001", referenz="NEXUS-BNB-0001",
        client_order_id="NEXUS-BNB-0001", order_tag="NEXUS",
        fill_ids=["9900000000000001", "9900000000000002"],
        ownership_verified=True,
        strategy_version=version, strategy_parameter_hash=hash_wert,
        entry_strategy_mode="FREQTRADE")


# ---------------------------------------------------------------------------
# T1 -- der eigentliche Vorfall
# ---------------------------------------------------------------------------
def test_bestand_aus_9_0_15_bleibt_nach_update_eine_botposition():
    """Der Kern des Auftrags von Georg: offene Trades richtig zuordnen."""
    import crypto_engine as ce

    p = bestandsposition(ce, version="NEXUS-FT-SAMPLE-V1", hash_wert=HASH_9_0_15)

    assert p.ownership_chain_complete, (
        "orderId, clOrdId und echte tradeIds sind vorhanden -- das ist "
        "Eigentum, unabhaengig von jeder Strategieversion")
    assert p.blocks_reentry, "Ein zweiter Kauf desselben Werts bleibt gesperrt"
    assert p.safety_monitoring_enabled, "Stop und Menge werden weiter geprueft"


def test_eigentum_haengt_an_keinem_wert_den_ein_update_aendern_kann():
    """DIE REGEL, maschinell geprueft.

    Es wird jeder Wert veraendert, den ein Softwareupdate anfassen kann.
    Das Eigentum darf sich dabei nicht bewegen. Faellt hier ein neues Feld
    in die Eigentumsentscheidung, schlaegt dieser Test fehl.
    """
    import crypto_engine as ce

    p = bestandsposition(ce, version="NEXUS-FT-SAMPLE-V1", hash_wert=HASH_9_0_15)
    vorher = p.ownership_chain_complete
    assert vorher is True

    for feld, wert in (
            ("strategy_version", "NEXUS-FT-SAMPLE-V2"),
            ("strategy_parameter_hash", "0" * 64),
            ("strategy_parameter_hash", ""),
            ("entry_strategy_mode", "STANDARD"),
            ("verwaltung", "BEOBACHTEN"),
            ("verwaltung", "MANUELL"),
            ("verwaltungsnotiz", "Strategie nicht reproduzierbar"),
            ("management_status", "STRATEGY_MIGRATION_REQUIRED"),
            ("broker_schutz", False),
    ):
        alt = getattr(p, feld)
        setattr(p, feld, wert)
        assert p.ownership_chain_complete is vorher, (
            f"{feld} darf das Eigentum nicht beeinflussen")
        assert p.blocks_reentry is vorher, (
            f"{feld} darf die Wiedereinstiegssperre nicht aufheben")
        setattr(p, feld, alt)


def test_nur_fehlende_broker_ids_nehmen_das_eigentum():
    """Die Gegenprobe: ohne Brokerbeweis gibt es kein Eigentum."""
    import crypto_engine as ce

    for feld, wert in (("order_id", ""), ("client_order_id", ""),
                       ("fill_ids", []), ("ownership_verified", False)):
        p = bestandsposition(ce, version="NEXUS-FT-SAMPLE-V1",
                             hash_wert=HASH_9_0_15)
        setattr(p, feld, wert)
        if feld == "client_order_id":
            p.referenz = ""
        assert not p.ownership_chain_complete, (
            f"ohne {feld} darf keine Botposition behauptet werden")


# ---------------------------------------------------------------------------
# T2 -- Hashabweichung
# ---------------------------------------------------------------------------
def test_hashabweichung_pausiert_nur_die_automatik():
    import crypto_engine as ce

    p = bestandsposition(ce, version="NEXUS-FT-SAMPLE-V1", hash_wert=HASH_9_0_15)
    p.pausiere("Strategie-Snapshot nicht reproduzierbar",
               status="STRATEGY_MIGRATION_REQUIRED")

    assert p.ownership_chain_complete
    assert p.blocks_reentry
    assert p.safety_monitoring_enabled
    assert not p.strategy_execution_enabled, "Automatische Ausstiege ruhen"
    assert p.management_status == "STRATEGY_MIGRATION_REQUIRED"


def test_bekannter_vorgaenger_wird_migriert_und_protokolliert():
    import crypto_engine as ce
    import strategy_migration
    import freqtrade_sample_strategy as sample

    p = bestandsposition(ce, version="NEXUS-FT-SAMPLE-V1", hash_wert=HASH_9_0_15)
    erlaubt, status, grund = strategy_migration.pruefe(p)
    assert erlaubt, (status, grund)

    ergebnis = strategy_migration.migriere(p, actor="test")
    assert ergebnis["migriert"] is True, ergebnis

    assert p.strategy_version == sample.STRATEGY_VERSION
    assert p.strategy_parameter_hash == sample.PARAMETER_HASH
    assert p.strategy_migrated_from_version == "NEXUS-FT-SAMPLE-V1"
    assert p.strategy_migrated_from_hash == HASH_9_0_15
    assert p.strategy_migrated_at, "Der Zeitpunkt wird protokolliert"
    assert p.strategy_migration_actor == "test"
    # Menge, Einstand und IDs bleiben unberuehrt.
    assert p.menge == 1.5 and p.einstieg == 612.4
    assert p.order_id == "2100000000000001"


def test_migration_ist_idempotent():
    import crypto_engine as ce
    import strategy_migration
    import freqtrade_sample_strategy as sample

    p = bestandsposition(ce, version="NEXUS-FT-SAMPLE-V1", hash_wert=HASH_9_1)
    assert strategy_migration.migriere(p, actor="test")["migriert"] is True
    ersterlauf = p.strategy_migrated_at
    assert p.strategy_version == sample.STRATEGY_VERSION

    zweitlauf = strategy_migration.migriere(p, actor="test")
    assert zweitlauf["migriert"] is False, "Ein zweiter Aufruf tut nichts"
    assert p.strategy_migrated_at == ersterlauf


def test_unbekannter_hash_wird_nicht_stillschweigend_uebernommen():
    """Eine fremde Strategie darf nicht per Migration eingemeindet werden."""
    import crypto_engine as ce
    import strategy_migration
    import freqtrade_sample_strategy as sample

    p = bestandsposition(ce, version="FREMD-STRATEGIE", hash_wert="f" * 64)
    erlaubt, status, grund = strategy_migration.pruefe(p)
    assert not erlaubt
    assert status == strategy_migration.STATUS_MIGRATION_NOETIG
    assert "Unbekannte Strategieidentitaet" in grund
    # Und die Position behaelt ihr Eigentum trotzdem.
    assert p.ownership_chain_complete and p.blocks_reentry


def test_fremdes_konto_wird_nie_uebernommen():
    import crypto_engine as ce
    import strategy_migration
    import freqtrade_sample_strategy as sample

    p = bestandsposition(ce, version="NEXUS-FT-SAMPLE-V1", hash_wert=HASH_9_0_15)
    p.account_fingerprint = "konto-A"
    erlaubt, status, _ = strategy_migration.pruefe(p, account_fingerprint="konto-B")
    assert not erlaubt
    assert status == strategy_migration.STATUS_KONTO_ABWEICHUNG
    ergebnis = strategy_migration.migriere(p, account_fingerprint="konto-B")
    assert ergebnis["migriert"] is False
    assert p.strategy_version == "NEXUS-FT-SAMPLE-V1", "nichts angefasst"


# ---------------------------------------------------------------------------
# Strategie-Identitaet
# ---------------------------------------------------------------------------
def test_parameter_hash_ist_aus_dem_snapshot_nachrechenbar():
    """In 9.1 war er das NICHT -- der Hash lief ueber Werte, die danach
    ueberschrieben wurden. Genau das machte den Snapshot unpruefbar."""
    import freqtrade_sample_strategy as sample

    snapshot = sample.parameter_snapshot()
    daten = {feld: snapshot[feld] for feld in sample.SEMANTIK_FELDER}
    roh = json.dumps(daten, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(roh.encode("utf-8")).hexdigest() == sample.PARAMETER_HASH


def test_ausfuehrungsdetails_aendern_den_hash_nicht():
    """Nur Semantik zaehlt. Sonst wiederholt sich der Vorfall bei jeder
    Feinjustierung der Kerzenanzahl."""
    import freqtrade_sample_strategy as sample

    assert "startup_candles" not in sample.SEMANTIK_FELDER
    assert "minimal_roi" in sample.SEMANTIK_FELDER
    assert "stoploss" in sample.SEMANTIK_FELDER
    assert "exit_order" in sample.SEMANTIK_FELDER


def test_version_wurde_mit_der_semantik_erhoeht():
    import freqtrade_sample_strategy as sample

    assert sample.STRATEGY_VERSION == "NEXUS-FT-SAMPLE-V3", (
        "Wer die Hashberechnung aendert, muss die Version mitziehen -- "
        "sonst stehen zwei verschiedene Hashes unter demselben Namen")


# ---------------------------------------------------------------------------
# T7 -- Rasterlauf ueber eine Kerzengrenze
# ---------------------------------------------------------------------------
def test_scan_meldet_den_kerzenschluss_des_rasterpunkts():
    """Freqtrade entscheidet am Kerzenschluss. Zieht sich ein Scan ueber die
    Grenze, muss die Kerze des Rasterpunkts gelten, nicht die zufaellige
    Uhrzeit am Ende der Schleife."""
    import scheduler_v7

    takt = scheduler_v7.Taktgeber()
    token = takt.rasterlauf_beginnen("crypto", "scan", raster_sekunden=300.0)
    assert token["raster_sekunden"] == 300.0
    assert token.get("kerzenschluss_utc"), "Der Kerzenschluss wird festgehalten"
    fest = token["kerzenschluss_utc"]
    takt.rasterlauf_abschliessen("crypto", "scan", token)
    assert token["kerzenschluss_utc"] == fest, (
        "Der Kerzenschluss darf sich waehrend des Laufs nicht verschieben")


# ---------------------------------------------------------------------------
# Oberflaeche
# ---------------------------------------------------------------------------
def test_webui_zeigt_eigentum_abgleich_und_verwaltung_getrennt():
    quelle = (WURZEL / "webui" / "static" / "trades.js").read_text(encoding="utf-8")
    assert "Zuordnung" in quelle
    assert "eigentum_status" in quelle
    assert "abgleich_status" in quelle
    assert "verwaltung_status" in quelle


def test_eigentum_status_der_webui_kennt_keine_strategiefelder():
    """Auch die Anzeige darf Eigentum nicht aus Versionsfeldern ableiten."""
    import webui.state as state

    zeile = {"entry_order_id": "2100000000000001",
             "client_order_id": "NEXUS-BNB-0001",
             "entry_fill_ids_json": json.dumps(["9900000000000001"]),
             "strategie_version": "NEXUS-FT-SAMPLE-V1",
             "strategy_parameter_hash": HASH_9_0_15}
    assert state._eigentum_status(zeile) == "BEWIESEN"
    zeile["strategie_version"] = "NEXUS-FT-SAMPLE-V2"
    zeile["strategy_parameter_hash"] = "0" * 64
    assert state._eigentum_status(zeile) == "BEWIESEN"


def test_kuenstliche_ersatz_id_gilt_nicht_als_fill():
    import webui.state as state

    zeile = {"entry_order_id": "2100000000000001",
             "client_order_id": "NEXUS-BNB-0001",
             "entry_fill_ids_json": json.dumps(["okx-entry:2100000000000001"])}
    assert state._eigentum_status(zeile) != "BEWIESEN"


def test_recheck_fuehrt_einen_echten_abgleich_aus():
    """Der Knopf hat bis 9.1 nur einen Text geschrieben."""
    import okx_reconciliation_actions as ora

    quelle = (WURZEL / "okx_reconciliation_actions.py").read_text(encoding="utf-8")
    assert "_echter_abgleich" in quelle
    assert hasattr(ora, "_echter_abgleich")

    gerufen = {}

    class FakeBestand:
        symbol = "BNB"

    class FakeBroker:
        demo = True
        def account_fingerprint(self): return "fixture-A"
        def positionen(self):
            return [FakeBestand()]

    class FakeBuch:
        def hole(self, symbol):
            return None

    class FakeEngine:
        buch = FakeBuch()

        def _offene_ledger_abgleichen(self, bestaende):
            gerufen["bestaende"] = bestaende
            return {"ok": True}

    text = ora._echter_abgleich(FakeEngine(), FakeBroker(), "BNB", 0)
    assert "bestaende" in gerufen, "Der Ledgerabgleich muss wirklich laufen"
    assert isinstance(text, str) and text
