from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def test_webui_live_mode_requires_phrase_and_disarms(monkeypatch, tmp_path):
    import webui.settings_store as store
    from broker_live_arming import arm, status
    from credential_store import save_credentials
    monkeypatch.setattr(store, "ROOT", tmp_path)
    save_credentials(tmp_path / "okx_credentials.json", {
        "live_api_key": "key", "live_api_secret": "secret", "live_passphrase": "phrase",
    })
    arm("okx", root=tmp_path)
    with pytest.raises(ValueError):
        store.set_broker_mode("okx", "live", "")
    assert status("okx", root=tmp_path)[0] is True
    store.set_broker_mode("okx", "live", "OKX LIVE AUSWAHL")
    assert status("okx", root=tmp_path)[0] is False
    from credential_store import load_credentials
    assert load_credentials(tmp_path / "okx_credentials.json", {})["live_trading"] is True


def test_demo_mode_is_always_disarmed(monkeypatch, tmp_path):
    import webui.settings_store as store
    from broker_live_arming import arm, status
    monkeypatch.setattr(store, "ROOT", tmp_path)
    arm("etoro", root=tmp_path)
    store.set_broker_mode("etoro", "demo")
    assert (tmp_path / "handelsmodus.txt").read_text(encoding="utf-8").strip() == "paper"
    assert status("etoro", root=tmp_path)[0] is False


def test_offensive_profile_needs_second_confirmation(monkeypatch, tmp_path):
    import risk_profile_control
    import webui.settings_store as store
    monkeypatch.setattr(risk_profile_control, "PROFILE_FILE", tmp_path / "aktives_profil.txt")
    with pytest.raises(ValueError):
        store.set_risk_profile("offensiv")
    store.set_risk_profile("offensiv", "OFFENSIV AKTIVIEREN")
    assert (tmp_path / "aktives_profil.txt").read_text(encoding="utf-8").strip() == "offensiv"


def _decision_db(path: Path, rows: int) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE decisions(id INTEGER PRIMARY KEY, symbol TEXT)")
    for i in range(rows):
        con.execute("INSERT INTO decisions(symbol) VALUES (?)", (f"S{i}",))
    con.commit(); con.close()


def test_migration_replaces_empty_dashboard_db_but_preserves_source(tmp_path):
    import settings_migration as migration
    source = tmp_path / "TradingBot_v6"
    target = tmp_path / "TradingBot_v81"
    source.mkdir(); target.mkdir()
    _decision_db(source / "decision_history.sqlite", 2)
    _decision_db(target / "decision_history.sqlite", 0)
    (source / "favorites.json").write_text('{"favorites":["AAPL"]}', encoding="utf-8")
    before = (source / "decision_history.sqlite").read_bytes()
    copied, _ = migration.migrate_from(source, target)
    assert "decision_history.sqlite" in copied
    con = sqlite3.connect(target / "decision_history.sqlite")
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2
    con.close()
    assert (source / "decision_history.sqlite").read_bytes() == before
    assert (target / "handelsmodus.txt").read_text(encoding="utf-8").strip() == "paper"


def test_okx_healthcheck_is_authenticated_and_throttled(monkeypatch):
    import config
    from broker.okx import OKXBroker
    class Client:
        calls = 0
        def server_time_ms(self): return 1
        def account_config(self): self.calls += 1; return {"perm": "read_only"}
        def last_contact(self): return "now"
    client = Client()
    broker = OKXBroker(client=client, demo=True)
    broker._connected = True
    monkeypatch.setattr(config, "BROKER_HEALTHCHECK_SECONDS", 30)
    assert broker.health_check(force=True)
    assert broker.health_check(force=False)
    assert client.calls == 1


def test_dashboard_template_exposes_new_control_areas():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui/templates/dashboard.html").read_text(encoding="utf-8")
    settings = (root / "webui/templates/settings.html").read_text(encoding="utf-8")
    for text in ("Letzte Entscheidungen", "Raspberry Pi", "Buchungsabgleich"):
        assert text in html or text in (root / "webui/static/dashboard.js").read_text(encoding="utf-8")
    for text in ("Demo/LIVE", "Risikoprofil", "Benachrichtigungsmodus"):
        assert text in settings


def test_telegram_resume_is_session_bound_confirmed_and_idempotent(monkeypatch, tmp_path):
    import config
    import telegram_steuerung as tg
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_USER_ID", "123")
    monkeypatch.setattr(tg, "STATE", tmp_path / "control.json")
    monkeypatch.setattr(tg, "AUDIT", tmp_path / "audit.jsonl")
    class Executor:
        calls = 0
        def ausfuehren(self, command, **_):
            self.calls += 1
            return f"{command} OK"
    executor = Executor()
    control = tg.TelegramSteuerung(executor)
    buttons = []
    update = {"update_id": 77, "message": {"chat": {"id": 123}, "from": {"id": 123}, "text": "/resume"}}
    result = control.process_update(
        update, send_func=lambda _x: True,
        send_buttons_func=lambda text, keyboard, **kwargs: buttons.append(keyboard) or True,
    )
    assert result == "confirm_required" and executor.calls == 0
    callback_data = buttons[0][0][0]["callback_data"]
    callback = {"update_id": 78, "callback_query": {
        "id": "cb", "from": {"id": 123},
        "message": {"message_id": 1, "chat": {"id": 123}}, "data": callback_data,
    }}
    monkeypatch.setattr(tg, "answer_callback_query", lambda *_: True)
    monkeypatch.setattr(tg, "edit_telegram_message", lambda *_: False)
    assert control.process_update(callback, send_func=lambda _x: True) == "callback"
    assert executor.calls == 1
    assert control.process_update(callback, send_func=lambda _x: True) == "duplicate"
    assert executor.calls == 1


def test_telegram_old_session_callback_is_rejected(monkeypatch, tmp_path):
    import config
    import telegram_steuerung as tg
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_USER_ID", "123")
    monkeypatch.setattr(tg, "STATE", tmp_path / "control.json")
    monkeypatch.setattr(tg, "AUDIT", tmp_path / "audit.jsonl")
    control = tg.TelegramSteuerung(lambda *_: "never")
    replies = []
    monkeypatch.setattr(tg, "answer_callback_query", lambda *_: True)
    callback = {"update_id": 1, "callback_query": {
        "id": "cb", "from": {"id": 123}, "message": {"chat": {"id": 123}},
        "data": "menu:oldsession:STATUS",
    }}
    control.process_update(callback, send_func=lambda text: replies.append(text) or True)
    assert "alten Bot-Sitzung" in replies[0]
