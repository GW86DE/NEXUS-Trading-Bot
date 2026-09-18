"""Die zwei Testluecken aus dem Aenderungsantrag (CR-07, CR-09).

Beide Einwaende trafen zu:

* CR-07 verlangt einen Upgrade-Test mit einer ECHTEN Positionsdatei aus
  9.0.15 bzw. 9.1, nicht nur mit synthetisch gebauten Objekten. Ein
  konstruiertes Objekt beweist nicht, dass eine auf der Platte liegende
  Datei nach dem Update noch richtig gelesen wird.
* CR-09 verlangt einen Rasterlauf-Test mit SIMULIERTER Uhr ueber eine
  5-Minuten-Grenze. Der Rasterpunkt wird seit 9.2 vor dem Lauf festgelegt,
  aber die Grenzueberschreitung war nicht abgedeckt.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

# Der echte Hash der ausgelieferten 9.0.15-Strategie.
HASH_9_0_15 = "c8c1883383675aafd52da451a1d4cdcf924677be3da53ee66c958f11821f3f37"

# Eine crypto_positions.json, wie 9.0.15 sie geschrieben hat: die alte
# Strategieversion, kein management_status, keine Migrationsfelder, kein
# account_fingerprint -- aber eine lueckenlose Broker-ID-Kette.
BESTAND_9_0_15 = {
    "updated_at": "2026-08-30T22:14:03+02:00",
    "positionen": [
        {
            "symbol": "BNB", "inst_id": "BNB-USDT", "menge": 1.5,
            "einstieg": 612.4, "stop": 601.2, "take_profit": 634.9,
            "broker_schutz": True, "hoechstkurs": 618.0, "paper": False,
            "herkunft": "BOT", "verwaltung": "AUTO", "verwaltungsnotiz": "",
            "decision_id": 8801,
            "strategy_name": "NexusFreqtradeSample",
            "strategy_version": "NEXUS-FT-SAMPLE-V1",
            "strategy_parameter_hash": HASH_9_0_15,
            "entry_strategy_mode": "FREQTRADE",
            "order_id": "2100000000000001", "referenz": "NEXUS-BNB-0001",
            "client_order_id": "NEXUS-BNB-0001", "order_tag": "NEXUS",
            "fill_ids": ["9900000000000001", "9900000000000002"],
            "ownership_verified": True,
            "trade_quote_ccy": "USDT",
            "eroeffnet_am": "2026-08-29T09:12:44+02:00",
        },
        {
            "symbol": "LINK", "inst_id": "LINK-USDT", "menge": 42.0,
            "einstieg": 14.88, "stop": 14.31, "take_profit": 15.62,
            "broker_schutz": True, "hoechstkurs": 15.02, "paper": False,
            "herkunft": "BOT", "verwaltung": "AUTO", "verwaltungsnotiz": "",
            "decision_id": 8802,
            "strategy_name": "NexusFreqtradeSample",
            "strategy_version": "NEXUS-FT-SAMPLE-V1",
            "strategy_parameter_hash": HASH_9_0_15,
            "entry_strategy_mode": "FREQTRADE",
            "order_id": "2100000000000002", "referenz": "NEXUS-LINK-0001",
            "client_order_id": "NEXUS-LINK-0001", "order_tag": "NEXUS",
            "fill_ids": ["9900000000000003"],
            "ownership_verified": True,
            "trade_quote_ccy": "USDT",
            "eroeffnet_am": "2026-08-29T11:41:02+02:00",
        },
        {
            "symbol": "ONDO", "inst_id": "ONDO-USDT", "menge": 900.0,
            "einstieg": 0.8412, "stop": 0.8093, "take_profit": 0.8833,
            "broker_schutz": True, "hoechstkurs": 0.8501, "paper": False,
            "herkunft": "BOT", "verwaltung": "AUTO", "verwaltungsnotiz": "",
            "decision_id": 8803,
            "strategy_name": "NexusFreqtradeSample",
            "strategy_version": "NEXUS-FT-SAMPLE-V1",
            "strategy_parameter_hash": HASH_9_0_15,
            "entry_strategy_mode": "FREQTRADE",
            "order_id": "2100000000000003", "referenz": "NEXUS-ONDO-0001",
            "client_order_id": "NEXUS-ONDO-0001", "order_tag": "NEXUS",
            "fill_ids": ["9900000000000004"],
            "ownership_verified": True,
            "trade_quote_ccy": "USDT",
            "eroeffnet_am": "2026-08-30T08:05:19+02:00",
        },
    ],
}


# ---------------------------------------------------------------------------
# CR-07 -- Upgrade mit einer echten Positionsdatei
# ---------------------------------------------------------------------------
@pytest.fixture()
def bestandsbuch(tmp_path):
    import crypto_engine as ce

    datei = tmp_path / "crypto_positions.json"
    datei.write_text(json.dumps(BESTAND_9_0_15, indent=2), encoding="utf-8")
    return ce.KryptoPositionsbuch(datei)


def test_die_drei_positionen_ueberleben_das_update(bestandsbuch):
    """Georgs eigentliche Anforderung, an der echten Datei geprueft."""
    for symbol, menge in (("BNB", 1.5), ("LINK", 42.0), ("ONDO", 900.0)):
        p = bestandsbuch.hole(symbol)
        assert p is not None, f"{symbol} ging beim Laden verloren"
        assert p.menge == menge
        assert p.ownership_chain_complete, (
            f"{symbol} hat eine lueckenlose Broker-ID-Kette und bleibt "
            "eine Botposition -- auch nach einem Versionswechsel")
        assert p.blocks_reentry, f"{symbol} sperrt weiter den Wiedereinstieg"
        assert p.safety_monitoring_enabled, f"{symbol} wird weiter ueberwacht"


def test_unbekannte_felder_einer_neueren_version_kippen_den_start_nicht(tmp_path):
    """Ein Rueckschritt auf eine aeltere Version darf die Datei nicht
    unlesbar machen."""
    import crypto_engine as ce

    daten = json.loads(json.dumps(BESTAND_9_0_15))
    daten["positionen"][0]["ein_feld_aus_der_zukunft"] = {"x": 1}
    datei = tmp_path / "crypto_positions.json"
    datei.write_text(json.dumps(daten), encoding="utf-8")

    buch = ce.KryptoPositionsbuch(datei)
    assert buch.hole("BNB") is not None
    assert buch.hole("BNB").ownership_chain_complete


def test_der_alte_bestand_wird_nicht_still_migriert(bestandsbuch):
    """Migriert wird nur ausdruecklich und protokolliert."""
    p = bestandsbuch.hole("BNB")
    assert p.strategy_version == "NEXUS-FT-SAMPLE-V1"
    assert p.strategy_parameter_hash == HASH_9_0_15
    assert not p.strategy_migrated_at, "Das blosse Laden migriert nichts"


def test_die_bekannte_vorgaengeridentitaet_ist_migrierbar(bestandsbuch):
    import strategy_migration
    import freqtrade_sample_strategy as sample

    p = bestandsbuch.hole("BNB")
    erlaubt, status, grund = strategy_migration.pruefe(p)
    assert erlaubt, (status, grund)

    assert strategy_migration.migriere(p, actor="upgrade")["migriert"] is True
    assert p.strategy_version == sample.STRATEGY_VERSION
    assert p.strategy_migrated_from_hash == HASH_9_0_15
    assert p.menge == 1.5, "Eine Migration fasst keine Menge an"
    assert p.order_id == "2100000000000001"
    assert p.ownership_chain_complete


def test_ein_9_2_stand_mit_migrationsfeldern_laedt_ebenfalls(tmp_path):
    import crypto_engine as ce

    daten = json.loads(json.dumps(BESTAND_9_0_15))
    daten["positionen"][0].update({
        "management_status": "", "strategy_migrated_at": "2026-08-31T12:10:00+00:00",
        "strategy_migrated_from_version": "NEXUS-FT-SAMPLE-V1",
        "strategy_migrated_from_hash": HASH_9_0_15,
        "strategy_migration_reason": "bekannter Vorgaenger",
        "strategy_migration_actor": "auto",
        "account_fingerprint": "konto-A",
    })
    datei = tmp_path / "crypto_positions.json"
    datei.write_text(json.dumps(daten), encoding="utf-8")

    p = ce.KryptoPositionsbuch(datei).hole("BNB")
    assert p.strategy_migrated_from_hash == HASH_9_0_15
    assert p.account_fingerprint == "konto-A"
    assert p.ownership_chain_complete


# ---------------------------------------------------------------------------
# CR-09 -- Rasterlauf ueber die Kerzengrenze, mit simulierter Uhr
# ---------------------------------------------------------------------------
def test_scan_ueber_die_fuenfminutengrenze_behaelt_seinen_rasterpunkt(monkeypatch):
    """Der Pflichtfall aus dem Antrag:

        Scanstart 12:04:58
        Scanende  12:05:07
        bewerteter Rasterpunkt bleibt der beim Start festgelegte Punkt
        naechster 5m-Rasterlauf wird nicht uebersprungen
    """
    import scheduler_v7

    # 2026-08-31 12:04:58 UTC
    start_wand = 1788609898.0
    assert start_wand % 300 == 298, "Der Startzeitpunkt liegt 2 s vor der Grenze"

    uhr = {"wand": start_wand, "monoton": 10_000.0}
    monkeypatch.setattr(scheduler_v7.time, "time", lambda: uhr["wand"])
    monkeypatch.setattr(scheduler_v7.time, "monotonic", lambda: uhr["monoton"])

    takt = scheduler_v7.Taktgeber()
    token = takt.rasterlauf_beginnen("crypto", "scan", raster_sekunden=300.0)
    rasterpunkt = token["rasterpunkt"]
    kerze = token["kerzenschluss_utc"]
    assert rasterpunkt == start_wand - 298

    # Der Scan dauert 9 Sekunden und ueberschreitet dabei 12:05:00.
    uhr["wand"] = start_wand + 9
    uhr["monoton"] = 10_009.0
    assert uhr["wand"] % 300 == 7, "Das Ende liegt hinter der Grenze"

    takt.rasterlauf_abschliessen("crypto", "scan", token)

    assert token["rasterpunkt"] == rasterpunkt, (
        "Der Lauf gehoert zu der Kerze, die bei seinem Start abgeschlossen war")
    assert token["kerzenschluss_utc"] == kerze

    # Der entscheidende Punkt: WORAUF der Vermerk gesetzt wurde.
    vermerk = takt._letzter_lauf[("crypto", "scan")]
    assert vermerk == 10_000.0 - 298, (
        "Der Lauf wird dem Rasterpunkt bei seinem START zugerechnet")

    # Damit ist 12:05 (monoton 10_002) nach 300 s wieder faellig -- die Kerze
    # wird also nicht uebersprungen.
    assert (uhr["monoton"] - vermerk) >= 300.0, (
        "12:05 ist eine neue Kerze und muss laufen koennen")

    # Gegenprobe mit dem alten Verhalten: haette man am ENDE des Laufs
    # gestempelt, stuende der Vermerk auf monoton 10_009.
    naechster_lauf_mit_raster = vermerk + 300.0            # 10_002 = 12:05:00
    naechster_lauf_ohne_raster = uhr["monoton"] + 300.0    # 10_309 = 12:10:07
    kerze_12_05 = 10_002.0

    assert naechster_lauf_mit_raster <= kerze_12_05, (
        "Mit Rasterpunkt wird die 12:05-Kerze bewertet")
    assert naechster_lauf_ohne_raster > kerze_12_05 + 300.0, (
        "Ohne Rasterpunkt waere der naechste Lauf erst 12:10:07 -- die "
        "12:05-Kerze fiele ersatzlos aus. Genau das war der Fehler.")


def test_ohne_raster_bleibt_der_alte_freie_takt(monkeypatch):
    """NEXUS_STANDARD behaelt seinen freien Takt. Georgs Vorgabe: die
    anderen Modi arbeiten unveraendert."""
    import scheduler_v7

    takt = scheduler_v7.Taktgeber()
    token = takt.rasterlauf_beginnen("crypto", "scan", raster_sekunden=0.0)
    assert token["raster_sekunden"] == 0.0
    assert token["kerzenschluss_utc"] == ""
    takt.rasterlauf_abschliessen("crypto", "scan", token)
