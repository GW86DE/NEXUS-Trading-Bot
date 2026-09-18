import json
from unittest.mock import patch

import config
import notifier


def test_inline_keyboard_is_persisted_with_message(monkeypatch,tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR",str(tmp_path))
    monkeypatch.setattr(config,"NOTIFY_TELEGRAM",True)
    monkeypatch.setattr(config,"TELEGRAM_BOT_TOKEN","")
    monkeypatch.setattr(config,"TELEGRAM_CHAT_ID","")
    kb=[[{"text":"Pruefen","callback_data":"univ:review:U-1"}]]
    ok=notifier.send_telegram_buttons("Vorschlag",kb)
    assert not ok
    state=json.loads((tmp_path/config.TELEGRAM_QUEUE_FILE).read_text(encoding="utf-8"))
    assert state["queue"][0]["reply_markup"]["inline_keyboard"]==kb
