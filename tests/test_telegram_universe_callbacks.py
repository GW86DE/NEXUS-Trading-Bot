from unittest.mock import patch

import config
from telegram_steuerung import TelegramSteuerung
from universe_proposals import create_proposals, get
from universe_telegram import handle_callback


def _candidate():
    return {"symbol":"XYZ","company":"Example Corp","sector":"Industrie","rationale":"Research",
            "sources":[{"title":"A","url":"https://a.example/x"},{"title":"B","url":"https://b.example/y"}]}


def test_authorized_callback_can_only_request_review(monkeypatch,tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR",str(tmp_path))
    monkeypatch.setattr(config,"TELEGRAM_CHAT_ID","123")
    monkeypatch.setattr(config,"TELEGRAM_ALLOWED_USER_ID","123")
    p=create_proposals([_candidate()])[0]
    ctl=TelegramSteuerung(lambda *_:"",callback_handler=handle_callback)
    sent=[]
    update={"callback_query":{"id":"cb1","from":{"id":123},"message":{"chat":{"id":123}},"data":f"univ:review:{p['id']}"}}
    with patch("telegram_steuerung.answer_callback_query",return_value=True):
        result=ctl.process_update(update,send_func=lambda x:sent.append(x) or True,send_buttons_func=lambda *a,**k:True)
    assert result=="callback"
    assert get(p["id"])["status"]=="REVIEW_REQUESTED"
    assert sent and "deterministischen eToro" in sent[0]


def test_callback_requires_authorized_user_even_in_authorized_chat(monkeypatch,tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR",str(tmp_path))
    monkeypatch.setattr(config,"TELEGRAM_CHAT_ID","123")
    monkeypatch.setattr(config,"TELEGRAM_ALLOWED_USER_ID","123")
    p=create_proposals([_candidate()])[0]
    ctl=TelegramSteuerung(lambda *_:"",callback_handler=handle_callback)
    update={"callback_query":{"id":"cb2","from":{"id":999},"message":{"chat":{"id":123}},"data":f"univ:review:{p['id']}"}}
    with patch("telegram_steuerung.answer_callback_query",return_value=True):
        result=ctl.process_update(update,send_func=lambda x:True,send_buttons_func=lambda *a,**k:True)
    assert result=="unauthorized"
    assert get(p["id"])["status"]=="PROPOSED"
