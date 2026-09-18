from unittest.mock import patch

import config
from berichte import Befehlsverarbeitung
from telegram_steuerung import TelegramSteuerung


class _State:
    def zustand(self):
        return "aktiv"


def _authorized(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_USER_ID", "123")
    monkeypatch.setattr(config, "TELEGRAM_RISK3_CONFIRM_TTL_SECONDS", 60)


def _risk3_request():
    return {"message": {"chat": {"id": 123}, "from": {"id": 123}, "text": "/risk3"}}


def test_risk3_command_only_creates_confirmation_and_does_not_execute(monkeypatch):
    _authorized(monkeypatch)

    class _Exec:
        calls = 0
        def ausfuehren(self, *args, **kwargs):
            self.calls += 1
            return "should not run"

    executor = _Exec()
    ctl = TelegramSteuerung(executor)
    sent_buttons = []
    result = ctl.process_update(
        _risk3_request(),
        send_func=lambda _x: True,
        send_buttons_func=lambda text, keyboard, **kwargs: sent_buttons.append((text, keyboard, kwargs)) or True,
    )

    assert result == "confirm_required"
    assert executor.calls == 0
    assert len(sent_buttons) == 1
    text, keyboard, kwargs = sent_buttons[0]
    assert "ändert noch NICHTS" in text
    assert kwargs.get("priority") == "high"
    assert keyboard[0][0]["callback_data"].startswith("risk3:confirm:")
    assert keyboard[0][1]["callback_data"].startswith("risk3:cancel:")


def test_risk3_confirmation_is_one_time_and_activates_only_after_callback(monkeypatch):
    _authorized(monkeypatch)
    ctl = TelegramSteuerung(lambda *_: "")
    sent_buttons = []
    ctl.process_update(
        _risk3_request(),
        send_func=lambda _x: True,
        send_buttons_func=lambda text, keyboard, **kwargs: sent_buttons.append((text, keyboard, kwargs)) or True,
    )
    callback_data = sent_buttons[0][1][0][0]["callback_data"]
    callback = {"callback_query": {
        "id": "risk-cb-1",
        "from": {"id": 123},
        "message": {"chat": {"id": 123}},
        "data": callback_data,
    }}
    replies = []
    with patch("telegram_steuerung.answer_callback_query", return_value=True), \
         patch("telegram_steuerung.activate_risk_profile", return_value="offensiv") as activate:
        first = ctl.process_update(callback, send_func=lambda text: replies.append(text) or True)
        second = ctl.process_update(callback, send_func=lambda text: replies.append(text) or True)

    assert first == "callback"
    assert second == "callback"
    activate.assert_called_once_with("offensiv")
    assert "zweiten Bestätigung" in replies[0]
    assert "bereits benutzt" in replies[1]


def test_risk3_expired_confirmation_cannot_activate(monkeypatch):
    _authorized(monkeypatch)
    ctl = TelegramSteuerung(lambda *_: "")
    sent_buttons = []
    ctl.process_update(
        _risk3_request(),
        send_func=lambda _x: True,
        send_buttons_func=lambda text, keyboard, **kwargs: sent_buttons.append((text, keyboard, kwargs)) or True,
    )
    callback_data = sent_buttons[0][1][0][0]["callback_data"]
    token = callback_data.rsplit(":", 1)[-1]
    ctl._risk3_confirmations[token] = -1.0
    callback = {"callback_query": {
        "id": "risk-cb-2",
        "from": {"id": 123},
        "message": {"chat": {"id": 123}},
        "data": callback_data,
    }}
    replies = []
    with patch("telegram_steuerung.answer_callback_query", return_value=True), \
         patch("telegram_steuerung.activate_risk_profile") as activate:
        result = ctl.process_update(callback, send_func=lambda text: replies.append(text) or True)

    assert result == "callback"
    activate.assert_not_called()
    assert "abgelaufen" in replies[0]


def test_risk3_callback_still_requires_authorized_user(monkeypatch):
    _authorized(monkeypatch)
    ctl = TelegramSteuerung(lambda *_: "")
    token, _ = ctl._new_risk3_confirmation()
    callback = {"callback_query": {
        "id": "risk-cb-3",
        "from": {"id": 999},
        "message": {"chat": {"id": 123}},
        "data": f"risk3:confirm:{token}",
    }}
    with patch("telegram_steuerung.answer_callback_query", return_value=True), \
         patch("telegram_steuerung.activate_risk_profile") as activate:
        result = ctl.process_update(callback, send_func=lambda _text: True)

    assert result == "unauthorized"
    activate.assert_not_called()
    assert token in ctl._risk3_confirmations


def test_generic_risk3_execution_is_defense_in_depth_blocked():
    commands = Befehlsverarbeitung(_State())
    with patch("risk_profile_control.activate_risk_profile") as activate:
        text = commands.ausfuehren("RISK3", quelle="telegram")
    activate.assert_not_called()
    assert "NICHT aktiviert" in text
    assert "zweistufige" in text


def test_risk3_cancel_consumes_token_without_activation(monkeypatch):
    _authorized(monkeypatch)
    ctl = TelegramSteuerung(lambda *_: "")
    token, _ = ctl._new_risk3_confirmation()
    callback = {"callback_query": {
        "id": "risk-cb-cancel",
        "from": {"id": 123},
        "message": {"chat": {"id": 123}},
        "data": f"risk3:cancel:{token}",
    }}
    replies = []
    with patch("telegram_steuerung.answer_callback_query", return_value=True), \
         patch("telegram_steuerung.activate_risk_profile") as activate:
        result = ctl.process_update(callback, send_func=lambda text: replies.append(text) or True)

    assert result == "callback"
    activate.assert_not_called()
    assert token not in ctl._risk3_confirmations
    assert "nicht aktiviert" in replies[0]


def test_risk3_ja_cannot_bypass_button_confirmation(monkeypatch):
    _authorized(monkeypatch)

    class _Exec:
        calls = 0
        def ausfuehren(self, *args, **kwargs):
            self.calls += 1
            return "should not run"

    executor = _Exec()
    ctl = TelegramSteuerung(executor)
    sent_buttons = []
    request = {"message": {"chat": {"id": 123}, "from": {"id": 123}, "text": "/risk3 JA"}}
    result = ctl.process_update(
        request,
        send_func=lambda _x: True,
        send_buttons_func=lambda text, keyboard, **kwargs: sent_buttons.append((text, keyboard, kwargs)) or True,
    )

    assert result == "confirm_required"
    assert executor.calls == 0
    assert sent_buttons
