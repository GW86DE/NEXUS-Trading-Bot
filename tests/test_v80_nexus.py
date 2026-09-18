"""v8-Regressions: EEA, Live-Arming, Migration, Human-Gate und WebUI-Auth."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from broker.base import BrokerFehler
from broker.okx import OKX_BASE_URL, OKXBroker, OKXClient
from broker.okx_stream import (
    OKXPrivateStream, OKX_EEA_DEMO_PRIVATE_WS, OKX_EEA_PRIVATE_WS,
)
from broker_live_arming import arm, status
from settings_migration import _force_paper
from universe.manager import UniverseManager
from universe.modelle import UniverseKandidat, UniverseScore, UniverseZustand
from webui.auth import hash_password, verify_password


class Response:
    status_code = 200
    content = b"x"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class Session:
    def __init__(self, payload):
        self.payload = payload

    def request(self, *args, **kwargs):
        return Response(self.payload)


def test_okx_uses_eea_endpoint_and_detects_nested_order_error():
    assert OKX_BASE_URL == "https://eea.okx.com"
    client = OKXClient("key", "secret", "phrase", demo=True)
    client.session = Session({"code": "0", "msg": "", "data": [
        {"sCode": "51008", "sMsg": "Insufficient balance"}
    ]})
    with pytest.raises(BrokerFehler, match="51008"):
        client.place_order({"instId": "BTC-EUR"})


def test_okx_live_entry_needs_short_lived_arm(monkeypatch, tmp_path):
    monkeypatch.setattr("broker_live_arming.ROOT", tmp_path)
    broker = OKXBroker(demo=False, client=object())
    with pytest.raises(BrokerFehler, match="LIVE-Einstieg blockiert"):
        broker.kaufe_mit_absicherung("BTC-EUR", 0.01, 50_000, 45_000, 60_000)
    arm("okx", minutes=1, root=tmp_path)
    assert status("okx", root=tmp_path)[0] is True


def test_migration_forces_both_brokers_back_to_paper(tmp_path):
    (tmp_path / "handelsmodus.txt").write_text("live\n", encoding="utf-8")
    (tmp_path / "okx_credentials.json").write_text(
        json.dumps({"live_trading": True, "live_api_key": "kept"}), encoding="utf-8")
    (tmp_path / "okx_live_arm.json").write_text("{}", encoding="utf-8")
    _force_paper(tmp_path)
    assert (tmp_path / "handelsmodus.txt").read_text(encoding="utf-8").strip() == "paper"
    data = json.loads((tmp_path / "okx_credentials.json").read_text(encoding="utf-8"))
    assert data["live_trading"] is False and data["live_api_key"] == "kept"
    assert not (tmp_path / "okx_live_arm.json").exists()


def test_stock_favorite_does_not_skip_probation(tmp_path):
    """GEAENDERT IN v8.1.5: Aktien werden autonom aufgenommen.

    Vorher hiess dieser Test ``..._still_requires_human_gate`` und pruefte,
    dass auch ein Favorit die Telegram-Freigabe braucht. Die Freigabe ist auf
    Georgs Vorgabe entfallen. Was BLEIBT und hier geprueft wird: ein Favorit
    ist weiterhin nur ein Prioritaetsmarker und ueberspringt die Bewaehrung
    NICHT -- sonst waere "Favorit" eine Hintertuer ins handelbare Universum.
    """
    from universe.modelle import AKTIV, BEOBACHTUNG

    class Cfg:
        STOCK_UNIVERSE_ACTIVE_LIMIT = 100
        STOCK_UNIVERSE_FOCUS_LIMIT = 15
        STOCK_UNIVERSE_REMOVAL_RANK = 150
        STOCK_UNIVERSE_MIN_RESIDENCE_HOURS = 24
        STOCK_UNIVERSE_REMOVAL_CONFIRMATIONS = 3
        STOCK_UNIVERSE_FAVORITE_SLOTS = 5
        STOCK_UNIVERSE_MAX_CHANGES_PER_RUN = 5
        STOCK_UNIVERSE_PROBATION_HOURS = 4.0
        STOCK_CORE_SYMBOLS = ()          # kein fester Kern in diesem Test
        STOCK_CORE_LIMIT = 0

    manager = UniverseManager(UniverseZustand(tmp_path / "universe.json"), cfg=Cfg())
    candidate = UniverseKandidat("AAPL", "etoro", asset_type="stock", inst_id="AAPL")
    result = {"broker": "etoro", "rangliste": [{
        "kandidat": candidate, "score": UniverseScore(0.9), "rang": 1,
        "tier": "ETABLIERT", "tier_begruendung": "Test",
    }]}
    diff = manager.lauf(result, favoriten=["AAPL"])

    mitglied = manager.zustand.hole("etoro", "AAPL")
    assert mitglied is not None, "Seit v8.1.5 wird ohne Rueckfrage aufgenommen"
    assert mitglied.zustand == BEOBACHTUNG
    assert mitglied.zustand != AKTIV, "Ein Favorit ueberspringt keine Bewaehrung"
    assert "AAPL" not in diff.aufgenommen


def test_crypto_favorite_does_not_skip_probation(tmp_path):
    class Cfg:
        CRYPTO_UNIVERSE_ACTIVE_LIMIT = 50
        CRYPTO_UNIVERSE_FOCUS_LIMIT = 12
        CRYPTO_UNIVERSE_REMOVAL_RANK = 75
        CRYPTO_UNIVERSE_MIN_RESIDENCE_HOURS = 6
        CRYPTO_UNIVERSE_REMOVAL_CONFIRMATIONS = 3
        CRYPTO_UNIVERSE_PROBATION_HOURS = 24
        CRYPTO_UNIVERSE_FAVORITE_SLOTS = 5
        CRYPTO_UNIVERSE_MAX_CHANGES_PER_RUN = 8

    manager = UniverseManager(UniverseZustand(tmp_path / "universe.json"), cfg=Cfg())
    candidate = UniverseKandidat("NEU", "okx", asset_type="crypto", inst_id="NEU-EUR")
    result = {"broker": "okx", "rangliste": [{
        "kandidat": candidate, "score": UniverseScore(0.9), "rang": 1,
        "tier": "KANDIDAT", "tier_begruendung": "noch nicht etabliert",
    }]}
    manager.lauf(result, favoriten=["NEU"])
    member = manager.zustand.hole("okx", "NEU")
    assert member is not None and not member.handelbar and member.favorit


def test_webui_password_is_salted_and_verifiable():
    first = hash_password("Sehr-langes-Testpasswort-123")
    second = hash_password("Sehr-langes-Testpasswort-123")
    assert first != second
    assert verify_password("Sehr-langes-Testpasswort-123", first)
    assert not verify_password("falsch", first)


def test_okx_eea_private_stream_login_subscription_and_balance_snapshot():
    class App:
        def __init__(self):
            self.sent = []

        def send(self, value):
            self.sent.append(json.loads(value))

        def close(self):
            pass

    app = App()
    stream = OKXPrivateStream("KEY", "SECRET", "PHRASE", demo=True)
    assert stream.endpoint == OKX_EEA_DEMO_PRIVATE_WS
    assert OKXPrivateStream("K", "S", "P", demo=False).endpoint == OKX_EEA_PRIVATE_WS
    stream._on_open(app)
    assert app.sent[0]["op"] == "login"
    assert app.sent[0]["args"][0]["apiKey"] == "KEY"
    assert app.sent[0]["args"][0]["sign"]
    stream._on_message(app, json.dumps({"event": "login", "code": "0"}))
    assert app.sent[1]["op"] == "subscribe"
    assert {x["channel"] for x in app.sent[1]["args"]} == {"account", "orders"}
    # Ab v8.1.2 muss die Subscription-ID rein alphanumerisch sein; der
    # frueher benutzte Bindestrich hat OKX-Code 60033 ausgeloest.
    assert app.sent[1]["id"].isalnum() and len(app.sent[1]["id"]) <= 32

    # Daten gelten erst als brauchbar, wenn OKX BEIDE Kanaele bestaetigt hat.
    assert stream.balances() is None, "ohne subscribe-ACK darf es keine Daten geben"
    for kanal in ("account", "orders"):
        stream._on_message(app, json.dumps({"event": "subscribe", "arg": {"channel": kanal}}))
    assert stream.status().zustand == "SUBSCRIBED"

    stream._on_message(app, json.dumps({
        "arg": {"channel": "account"},
        "data": [{"details": [{
            "ccy": "EUR", "availBal": "120", "cashBal": "125", "frozenBal": "5",
        }]}],
    }))
    assert stream.balances()["EUR"]["cash"] == pytest.approx(120)
