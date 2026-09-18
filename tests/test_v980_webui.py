from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]

class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__();self.tags=[];self.feed(text)
    def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))


def test_all_settings_controls_survive_the_redesign():
    expected='''etoro.allow_cfd etoro.demo_api_key etoro.demo_user_key etoro.live_api_key etoro.live_user_key etoro.require_cost_quote handel.crypto_ticker_max_age_seconds handel.market_session_quote_max_age_seconds news.alpha_api_key news.enabled.alpha_vantage news.enabled.finnhub news.enabled.fmp news.enabled.gdelt news.enabled.google_news news.enabled.nasdaq_halts news.enabled.sec_edgar news.enabled.yahoo_finance news.finnhub_api_key news.fmp_api_key news.massive_api_key news.massive_daily_limit news.massive_enabled news.sec_contact_email okx.demo_api_key okx.demo_api_secret okx.demo_passphrase okx.enabled okx.live_api_key okx.live_api_secret okx.live_passphrase okx.quote_ccy openai.api_key openai.enabled openai.luna_model openai.max_cost_per_day_usd openai.model openai.research_enabled openai.terra_model second_opinion.cooldown_minutes second_opinion.critical_requires_approval second_opinion.daily_limit second_opinion.mode second_opinion.timeout_seconds telegram.bot_token telegram.chat_id telegram.enabled telegram.notification_mode telegram.user_id webui.bind_host webui.cookie_secure webui.port'''.split()
    expected += "news.enabled.federal_reserve news_rules.NEWS_BLOCK_SCORE news_rules.NEWS_BLOCK_SCORE_UNDERDOG news_rules.NEWS_EXIT_THRESHOLD news_rules.NEWS_MARKET_THRESHOLD news_rules.NEWS_MAX_SIGNAL_REPETITIONS".split()
    expected.remove("okx.quote_ccy")  # EUR is fixed; USDC is a separate opt-in.
    expected.append("okx.allow_usdc")
    # 10.1.10: USD/USDG sind eigene Opt-ins mit Bestaetigung 'WAEHRUNGEN FREIGEBEN'.
    expected += ["okx.allow_usd", "okx.allow_usdg"]
    expected += ["news.fmp_plan", "news.fmp_subscription", "news.fmp_daily_limit"]
    html=(ROOT/'webui/templates/settings.html').read_text()
    fields=[a['data-field'] for _,a in Tags(html).tags if 'data-field' in a]
    assert Counter(fields)==Counter(expected)
    for action in ('saveAll()',"setMode('etoro','live')","setMode('okx','live')",'setCryptoStrategy(', 'setEtoroStrategy(', 'setTelegramRuntime(', 'testAI()'):
        assert action in html
    assert 'settings-search' in html
    # 10.6.0: Der Einsatz je Trade ist eine eigene Entscheidung je Broker und
    # bekommt eine eigene, sichtbare Flaeche statt einer Zeile im Profilblock.
    # Die Karten entstehen wie die Profilkarten erst im Skript; hier steht nur
    # der Anker, den settings.js fuellt.
    assert 'id="risk-levels"' in html
    skript=(ROOT/'webui/static/settings.js').read_text(encoding='utf-8')
    for teil in ('function renderRiskLevels(', 'async function setRiskLevel(',
                 "api('/api/risk-level'", 'EINSATZ ERHOEHEN'):
        assert teil in skript
    # 10.2.0: +1 Panel "eToro Aktien-Strategiemodus". 10.6.0: +1 "Einsatz je Trade".
    assert len([t for t,a in Tags(html).tags if t=='details'])==14


@pytest.mark.parametrize('name',['dashboard','trades','settings','analysis','universe','logbook','pulsar','sources','diagnosis'])
def test_pages_have_unique_ids_local_assets_and_consistent_main_destinations(name):
    html=(ROOT/f'webui/templates/{name}.html').read_text()
    tags=Tags(html).tags
    ids=[a['id'] for _,a in tags if 'id' in a]
    assert len(ids)==len(set(ids))
    nav=html.split('<nav ',1)[1].split('</nav>',1)[0]
    links=[a['href'] for t,a in Tags('<nav '+nav+'</nav>').tags if t=='a']
    assert links==['/','/trades','/analysis','/universe','/underdogs','/pulsar','/sources','/diagnosis','/settings','/logbook']
    for t,a in tags:
        if t=='script' and a.get('src'):assert (ROOT/'webui'/a['src'].lstrip('/')).is_file()
        if t=='link' and a.get('rel')=='stylesheet':assert (ROOT/'webui'/a['href'].lstrip('/')).is_file()
    assert 'name="viewport"' in html


def test_unified_trade_tabs_have_accessible_panels_and_candles():
    tags=Tags((ROOT/'webui/templates/trades.html').read_text()).tags
    tabs=[a for t,a in tags if a.get('role')=='tab']
    panels={a['id']:a for _,a in tags if a.get('role')=='tabpanel'}
    assert len(tabs)==len(panels)==4
    assert sum(a['aria-selected']=='true' for a in tabs)==1
    for tab in tabs:assert panels[tab['aria-controls']]['aria-labelledby']==tab['id']
    assert any(a.get('id')=='trade-chart' for _,a in tags)
    assert not (ROOT/'webui/templates/positions.html').exists()


def test_positions_bookmark_redirect_and_execution_api_scope(monkeypatch):
    import importlib
    module=importlib.import_module('webui.app')
    import execution_lifecycle as life
    with TestClient(module.app) as client:
        assert client.get('/api/executions').status_code==401
        assert client.get('/positions',follow_redirects=False).headers['location']=='/login'
        monkeypatch.setattr(module,'_session',lambda *a,**k:{'u':'test','csrf':'test-token'})
        r=client.get('/positions',follow_redirects=False)
        assert r.status_code==303 and r.headers['location']=='/trades#bestand'
        life.reserve(broker='okx',account='A',environment='DEMO',instrument='SUI-USDC',
            side='SELL',client_id='sell',quantity=1,request={'private-diagnostic':'not-for-ui'})
        rows=client.get('/api/executions?broker=okx').json()['orders']
        assert len(rows)==1 and rows[0]['state']=='SUBMITTING'
        assert 'request_json' not in rows[0] and 'evidence_json' not in rows[0]
        assert client.get('/api/executions?broker=etoro').json()['orders']==[]
        assert client.get('/api/executions?broker=bad').status_code==400
        assert client.post('/api/manual-trades/1',json={'action':'SELL'}).status_code==403
        for route in ('/','/trades','/settings','/analysis','/universe','/logbook'):
            response=client.get(route)
            assert response.status_code==200 and '{{' not in response.text


def test_open_positions_never_disappear_under_history_filters():
    import trade_ledger as ledger
    from datetime import datetime,timezone,timedelta
    old=(datetime.now(timezone.utc)-timedelta(days=500)).isoformat()
    recent=datetime.now(timezone.utc).isoformat()
    first=ledger.trade_open(broker='okx',symbol='SUI',menge=1,einstieg_preis=1,
        gebuehr=0,decision_id=1,zeit=old,entry_order_id='old',broker_position_id='SUI-USDC')
    second=ledger.trade_open(broker='okx',symbol='BTC',menge=1,einstieg_preis=1,
        gebuehr=0,decision_id=2,zeit=old,entry_order_id='older',broker_position_id='BTC-USDC')
    ledger.trade_close(broker='okx',symbol='BTC',menge=1,ausstieg_preis=2,
        gebuehr=0,trade_id=second,zeit=recent,exit_order_id='sold',critical=True)
    rows=ledger.trade_liste(tage=1,limit=1)
    assert {r['trade_id'] for r in rows}=={first,second}


def test_execution_snapshot_does_not_create_database_or_migrate_legacy_schema(tmp_path,monkeypatch):
    import sqlite3
    import decision_analytics
    from execution_lifecycle import snapshot
    path=tmp_path/'readonly.sqlite'
    monkeypatch.setattr(decision_analytics,'db_pfad',lambda:path)
    assert snapshot()==[] and not path.exists()
    with sqlite3.connect(path) as con:con.execute('CREATE TABLE legacy_receipts(id INTEGER)')
    before=path.read_bytes()
    assert snapshot()==[]
    assert path.read_bytes()==before
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('legacy_receipts',)]
