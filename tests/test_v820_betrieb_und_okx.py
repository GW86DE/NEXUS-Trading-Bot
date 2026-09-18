"""Regressionen fuer NEXUS 8.2: Betriebsmeldungen, OKX-EEA und Klarheit."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")


class Uhr:
    def __init__(self):
        self.wert = 1000.0

    def __call__(self):
        return self.wert


class SofortBereit:
    STARTUP_TRADING_GRACE_MINUTES = 0.0


def _alles_gruen(ready):
    for name in list(ready.bedingungen):
        ready.melde(name, True, "Test")


def test_handelsbereit_meldung_ueberlebt_neustart_und_meldet_echte_rueckkehr(tmp_path, monkeypatch):
    """Ein Neustart ist keine Zustandsänderung, ein echter Ausfall schon."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from trading_ready import Handelsbereitschaft

    erste = []
    bereit1 = Handelsbereitschaft("okx", cfg=SofortBereit(), jetzt=Uhr(), melder=erste.append)
    _alles_gruen(bereit1)
    assert bereit1.darf_kaufen()[0] is True
    assert len(erste) == 1 and "🟢 OKX" in erste[0]

    zweite = []
    bereit2 = Handelsbereitschaft("okx", cfg=SofortBereit(), jetzt=Uhr(), melder=zweite.append)
    _alles_gruen(bereit2)
    assert bereit2.darf_kaufen()[0] is True
    assert zweite == [], "Ein sauberer Neustart darf keine zweite Bereitmeldung senden"

    bereit2.melde("broker_verbunden", False, "Testausfall")
    assert bereit2.darf_kaufen()[0] is False
    bereit2.melde("broker_verbunden", True, "wieder da")
    assert bereit2.darf_kaufen()[0] is True
    assert len(zweite) == 1 and "wieder handelsbereit" in zweite[0]


class Antwort:
    def __init__(self, daten):
        self._daten = daten
        self.status_code = 200

    def json(self):
        return self._daten


class Session:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "data": data})
        if "/account/config" in url:
            data = [{"acctLv": "1", "posMode": "net_mode", "perm": "read_only,trade"}]
        elif "/account/balance" in url:
            data = [{"totalEq": "123.4", "details": [{"ccy": "EUR", "availBal": "100", "cashBal": "100", "frozenBal": "0", "eqUsd": "108"}]}]
        elif "/account/trade-fee" in url:
            data = [{"maker": "-0.001", "taker": "-0.002", "level": "Lv1"}]
        elif "/trade/fills" in url:
            data = [{"instId": "BTC-EUR", "fillSz": "0.01"}]
        elif "/trade/orders-pending" in url or "/trade/orders-algo-pending" in url:
            data = []
        else:
            data = [{"ordId": "1"}]
        return Antwort({"code": "0", "data": data})


def test_okx_order_hat_ablauffrist_und_vollsnapshot_liest_alle_schutzdaten():
    from broker.okx import OKXClient

    client = OKXClient("key", "secret", "passphrase", demo=True)
    session = Session()
    client.session = session
    client.place_order({"instId": "BTC-EUR", "tdMode": "cash", "side": "buy", "ordType": "market", "sz": "0.01"})
    header = session.calls[-1]["headers"]
    assert header.get("x-simulated-trading") == "1"
    assert str(header.get("expTime", "")).isdigit(), "OKX-Order braucht ein Ablaufdatum"

    snap = client.account_snapshot(fills_limit=5)
    assert snap["demo"] is True
    assert snap["guthaben"]["EUR"]["cash"] == pytest.approx(100.0)
    assert snap["gebuehren"]["taker"] == pytest.approx(0.002)
    assert "schutzorders_oco" in snap and "schutzorders_conditional" in snap
    assert len(snap["letzte_fills"]) == 1


def test_finanzen_net_ist_weder_schaltbar_noch_im_newsablauf():
    import live_settings
    from news_sources import MultiSourceNews

    assert live_settings.quelle_aktiv("finanzen_net") is False
    assert "finanzen.net" not in MultiSourceNews().provider_configuration()
    assert "_finanzen" not in MultiSourceNews.__dict__


def test_webui_erklaert_okx_demo_startguthaben_und_botpositionen_getrennt():
    root = Path(__file__).resolve().parent.parent
    template = (root / "webui/templates/trades.html").read_text(encoding="utf-8")
    script = (root / "webui/static/positions.js").read_text(encoding="utf-8")
    assert "OKX · Guthaben und Restbestände" in template
    assert "Offene Botpositionen" in template
    assert "Demo-Startguthaben" in script
    assert "EXTERNAL_HOLDING" in script and "BOT_MANAGED" in script


def test_telegram_meldungen_haben_kleine_bedeutungsvolle_symbole():
    import meldungen

    assert meldungen.kauf(broker="okx", symbol="BTC", menge=1, preis=1, waehrung="EUR").startswith("🟢")
    assert meldungen.verkauf(broker="okx", symbol="BTC", menge=1, geplant=1, preis=1, waehrung="EUR").startswith("💰")
    assert meldungen.sicherheit("Test", "Details").startswith("🛡")


def test_ki_kritisch_wird_nur_zum_menschlichen_gate():
    root = Path(__file__).resolve().parent.parent
    quelle = (root / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("def _second_opinion")
    ende = quelle.index("def _melde_bereitschaft", start)
    block = quelle[start:ende]
    assert "warntext" in block
    assert "stelle_zurueck" in block
    assert "NUTZER" in block
    assert "grundlage_noch_gueltig" in block


def test_telegram_kaufen_befehl_loest_keine_sofortorder_aus():
    """Freigeben bedeutet nur: beim naechsten Scan erneut voll pruefen."""
    from berichte import Befehlsverarbeitung

    antwort = Befehlsverarbeitung(None).ausfuehren("KAUFEN", args=["BTC"])
    assert "keine gültige wartende Kaufentscheidung" in antwort
    assert "gekauft" not in antwort.lower()
