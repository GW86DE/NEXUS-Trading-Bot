"""Telegram-Meldeschicht (v8.1.4).

Anlass: ``nexus_start._melder()`` importierte eine Klasse ``Notifier``, die es
in ``notifier.py`` nie gab. Der ImportError wurde verschluckt, die Funktion
gab ``None`` zurueck -- und damit hatte die gesamte Kryptomaschine keinen
Meldekanal. Am 25.08.2026 gab es vier Kaeufe und drei Verkaeufe, ohne dass
eine einzige Telegram-Nachricht ankam.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Der kaputte Kanal
# ---------------------------------------------------------------------------
def test_melder_liefert_niemals_none():
    """Der Kernfehler: _melder() gab None zurueck."""
    import nexus_start
    melder = nexus_start._melder("KRYPTO")
    assert melder is not None, "Ohne Melder meldet die Kryptoseite gar nichts"
    assert callable(melder)


def test_melder_funktioniert_auch_ohne_telegram(monkeypatch):
    """Ohne Zugangsdaten wird protokolliert, nicht abgestuerzt."""
    import config
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "", raising=False)
    import nexus_start
    melder = nexus_start._melder("KRYPTO")
    assert melder("Testmeldung", wichtig=True) is False


def test_kein_import_der_nicht_existierenden_klasse():
    quelle = (WURZEL / "nexus_start.py").read_text(encoding="utf-8")
    import ast
    baum = ast.parse(quelle)
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom) and knoten.module == "notifier":
            namen = {a.name for a in knoten.names}
            assert "Notifier" not in namen, \
                "notifier.Notifier existiert nicht -- genau das war der Fehler"


def test_notifier_hat_wirklich_keine_klasse_notifier():
    """Falls jemand sie spaeter anlegt, soll dieser Test daran erinnern."""
    import notifier
    assert not hasattr(notifier, "Notifier"), \
        "Wenn es die Klasse jetzt gibt, muss _melder() darauf geprueft werden"


# ---------------------------------------------------------------------------
# Meldungsklassen
# ---------------------------------------------------------------------------
def test_stille_meldungen_gehen_nur_ins_protokoll(monkeypatch):
    import meldungen
    gesendet = []
    monkeypatch.setattr(meldungen, "_telegram_bereit", lambda: True)
    monkeypatch.setattr("notifier.send_telegram",
                        lambda *a, **k: gesendet.append(a) or True)
    assert meldungen.melde("nur Protokoll", klasse=meldungen.STILL) is False
    assert gesendet == []


def test_kritische_meldungen_werden_nie_gedrosselt(monkeypatch):
    import meldungen
    gesendet = []
    monkeypatch.setattr(meldungen, "_telegram_bereit", lambda: True)
    monkeypatch.setattr("notifier.send_telegram",
                        lambda text, **k: gesendet.append(text) or True)
    for _ in range(3):
        meldungen.melde("Verlustbremse ausgeloest", klasse=meldungen.KRITISCH)
    assert len(gesendet) == 3, "Ein Sicherheitsereignis darf nie unterdrueckt werden"


def test_info_meldungen_werden_gedrosselt(monkeypatch):
    import meldungen
    meldungen._zuletzt.clear()
    gesendet = []
    monkeypatch.setattr(meldungen, "_telegram_bereit", lambda: True)
    monkeypatch.setattr("notifier.send_telegram",
                        lambda text, **k: gesendet.append(text) or True)
    for _ in range(3):
        meldungen.melde("Universum unveraendert", klasse=meldungen.INFO)
    assert len(gesendet) == 1, "Dieselbe INFO darf nicht dreimal kommen"


# ---------------------------------------------------------------------------
# Meldungstexte
# ---------------------------------------------------------------------------
def test_kaufmeldung_enthaelt_alles_wichtige():
    import meldungen
    text = meldungen.kauf(broker="okx", symbol="SOL", menge=15.8577, preis=99.9544,
                          waehrung="USDC", gebuehr=5.55, gebuehr_pct=0.0035,
                          stop=99.38, ziel=101.09, grund="Ruecksetzer, RSI 31->37",
                          geplant=47.53513)
    for teil in ("KAUF", "OKX", "SOL", "15.8577", "99.9544", "Gebuehr",
                 "Stop", "Ziel", "Ruecksetzer"):
        assert teil in text, f"{teil} fehlt in der Kaufmeldung"
    assert "47.53513 geplant" in text, "Die Teilausfuehrung muss benannt werden"


def test_kaufmeldung_warnt_ohne_broker_schutz():
    import meldungen
    text = meldungen.kauf(broker="okx", symbol="BTC", menge=1.0, preis=100.0,
                          waehrung="EUR", schutz=False)
    assert "keine Broker-Schutzorder" in text


def test_verkaufsmeldung_nennt_teilausfuehrung_und_rest():
    """Der Fall vom 25.08.: 1,67 von 15,86 verkauft, 14,19 blieben liegen."""
    import meldungen
    text = meldungen.verkauf(broker="okx", symbol="SOL", menge=1.66715,
                             geplant=15.85775, preis=99.1898, waehrung="USDC",
                             gebuehr=0.58, ergebnis=-1.85,
                             grund="Stop-Loss erreicht", haltedauer="1 min",
                             rest_gefuehrt=True)
    assert "1.66715 von 15.85775 ausgefuehrt" in text
    assert "14.1906 bleiben gefuehrt und geschuetzt" in text
    assert "Ergebnis -1,85 USDC" in text.replace(".", ",", 1) or "-1.85" in text
    assert "Haltedauer 1 min" in text


def test_verkaufsmeldung_bei_vollausfuehrung():
    import meldungen
    text = meldungen.verkauf(broker="okx", symbol="BTC", menge=1.0, geplant=1.0,
                             preis=100.0, waehrung="EUR", grund="Gewinnziel")
    assert "vollstaendig ausgefuehrt" in text
    assert "von" not in text.split("\n")[1]


def test_sicherheitsmeldung_nennt_die_handlung():
    import meldungen
    text = meldungen.sicherheit("Fremdbestand erkannt", "0,94 BTC mehr im Konto",
                                handlung="Bestand pruefen und zuordnen")
    assert "SICHERHEIT" in text
    assert "Was jetzt:" in text


# ---------------------------------------------------------------------------
# OKX im Status
# ---------------------------------------------------------------------------
def test_status_enthaelt_okx_abschnitt(tmp_path, monkeypatch):
    """Bis 8.1.3 stand ueber OKX im Telegram-Status kein Wort."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import json
    from datetime import datetime, timezone
    (tmp_path / "runtime_status_okx.json").write_text(json.dumps({
        "online": True, "modus": "DEMO",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "handelbares_kapital": 9600.0, "gebuehrensatz_pct": 0.35,
        "guthaben": {"EUR": {"gesamt": 60798.06, "frei": 60798.06},
                     "SOL": {"gesamt": 14.1908, "frei": 14.1908}},
        "positionen": [{"symbol": "BTC", "menge": 0.126632, "einstieg": 67975.1,
                        "stop": 67386.2, "broker_schutz": True,
                        "verwaltung": "AUTO", "ownership_verified": True,
                        "order_id": "order-1", "client_order_id": "TBN-test",
                        "fill_ids": ["trade-1"]}],
        "universum": {"aktiv": 3, "kern": 3, "dynamisch": 0},
    }), encoding="utf-8")

    import importlib
    import okx_status
    importlib.reload(okx_status)
    text = okx_status.text()

    assert "OKX · SPOT" in text
    assert "DEMO" in text
    assert "60 798.06 EUR" in text, "Das Guthaben muss im Status stehen"
    assert "14.190800 SOL" in text
    assert "BTC" in text and "Stop 67386.2" in text
    assert "Bestätigte Bot-Positionen: 1" in text
    assert "0.35 %" in text, "Der Gebuehrensatz gehoert in den Status"


def test_status_meldet_fehlenden_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import importlib
    import okx_status
    importlib.reload(okx_status)
    text = okx_status.text()
    assert "Kein Heartbeat" in text, "Ein fehlender Heartbeat darf nicht wie 'alles gut' aussehen"


def test_status_erkennt_veralteten_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import json
    from datetime import datetime, timedelta, timezone
    alt = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    (tmp_path / "runtime_status_okx.json").write_text(
        json.dumps({"online": True, "modus": "DEMO", "last_heartbeat": alt}),
        encoding="utf-8")
    import importlib
    import okx_status
    importlib.reload(okx_status)
    text = okx_status.text()
    assert "VERALTET" in text
    assert "30 min" in text


def test_berichte_bindet_okx_ein():
    quelle = (WURZEL / "berichte.py").read_text(encoding="utf-8")
    assert "okx_status" in quelle, "Der Statusbericht muss den OKX-Block einbinden"
