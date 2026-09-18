"""Exercise actual REST parsing, including rejection and secret-bearing replies."""
import json
from types import SimpleNamespace as NS
from urllib.parse import urlsplit, parse_qs
import pytest
from broker.okx import OKXClient
from broker.base import BrokerFehler
from okx_account_switch import SwitchError, inspect_new, activate
from okx_precheck_diagnostics import instrument_client


def rest_client(monkeypatch, *, fail_path=None, code='51000', transport=False):
    c=OKXClient('secret_key', 'secret_secret', 'secret_phrase', demo=True)
    monkeypatch.setattr(c._private_limit,'acquire',lambda **kw:True)
    monkeypatch.setattr(c._public_limit,'acquire',lambda **kw:True)
    calls=[]
    def send(method,url,**kw):
        path=urlsplit(url).path.removeprefix('/api/v5');q=parse_qs(urlsplit(url).query)
        calls.append((method,path,q))
        assert method=='GET' and kw.get('data') is None
        if path != '/public/time':assert kw['headers']['x-simulated-trading']=='1'
        if transport:
            import requests
            raise requests.ConnectionError('secret_key secret_secret secret_phrase')
        payload={'code':'0','data':[]}
        if path=='/public/time':payload['data']=[{'ts':'1789500000000'}]
        if path=='/account/config':payload['data']=[{'uid':'synthetic-new'}]
        if path=='/account/balance':payload['data']=[{'details':[]}]
        if path=='/trade/orders-algo-pending':
            if q.get('ordType',[''])[0] not in {'oco','conditional','trigger','move_order_stop'}:
                payload={'code':'51000','msg':'Invalid ordType','data':[]}
        if path==fail_path:payload={'code':code,'msg':'secret_key secret_secret secret_phrase','data':[]}
        response=NS(status_code=200,json=lambda:payload)
        for hook in c.session.hooks.get('response',[]):hook(response)
        return response
    monkeypatch.setattr(c.session,'request',send)
    instrument_client(c,SwitchError)
    return c,calls


def test_precheck_actual_rest_uses_accepted_trailing_type(monkeypatch):
    c,calls=rest_client(monkeypatch)
    assert inspect_new(c)['environment']=='DEMO'
    assert ('GET','/trade/orders-algo-pending',{'ordType':['move_order_stop']}) in calls
    assert len(calls)==24


@pytest.mark.parametrize('path',['/public/time','/account/config','/trade/orders-algo-pending','/trade/fills-history','/account/balance'])
def test_failure_reports_path_and_code_but_no_credentials(monkeypatch,path):
    c,_=rest_client(monkeypatch,fail_path=path)
    with pytest.raises(SwitchError) as caught:inspect_new(c)
    message=str(caught.value)
    assert path in message and 'HTTP=200' in message and 'OKX-Code=51000' in message
    assert 'secret_' not in message


def test_transport_failure_cannot_reuse_previous_response(monkeypatch):
    c,_=rest_client(monkeypatch)
    c.server_time_ms()
    def fail(*a,**kw):
        import requests
        raise requests.ConnectionError('secret_key')
    monkeypatch.setattr(c.session,'request',fail)
    with pytest.raises(SwitchError,match='HTTP=unbekannt, OKX-Code=unbekannt'):c.account_config()


@pytest.mark.parametrize('method,path,kw',[
    ('POST','/trade/order',{}),('GET','/account/set-account-switch-precheck',{}),
    ('GET','/trade/orders-pending',{'params':{'secret_key':'secret_secret'}}),
    ('GET','/trade/orders-pending',{'body':{}}),
])
def test_diagnosis_cannot_write_or_echo_unknown_input(monkeypatch,method,path,kw):
    c,calls=rest_client(monkeypatch)
    with pytest.raises(SwitchError) as caught:c.request(method,path,**kw)
    assert calls==[] and 'secret_' not in str(caught.value)


def test_precheck_failure_leaves_existing_money_state(monkeypatch,tmp_path):
    original=b'{"positionen": [{"symbol": "BTC", "menge": 1}]}'
    (tmp_path/'crypto_positions.json').write_bytes(original)
    c,_=rest_client(monkeypatch,fail_path='/trade/orders-algo-pending')
    with pytest.raises(SwitchError):activate(tmp_path,c)
    assert (tmp_path/'crypto_positions.json').read_bytes()==original
    assert not (tmp_path/'okx_account_context.json').exists()
    assert not (tmp_path/'okx_credentials.json').exists()
    assert not list(tmp_path.glob('okx_account_archive*'))


def test_inspect_only_cli_does_not_activate(monkeypatch,tmp_path,capsys):
    import sys,config,getpass,broker.okx,okx_account_switch as switch
    c,_=rest_client(monkeypatch)
    # Already instrumented in the fixture; main can safely wrap a second time.
    monkeypatch.setattr(broker.okx,'OKXClient',lambda *a,**kw:c)
    monkeypatch.setattr(getpass,'getpass',lambda *a:'unused-local-secret')
    monkeypatch.setattr(switch,'activate',lambda *a,**kw:pytest.fail('activation invoked'))
    monkeypatch.setattr(sys,'argv',['switch','--okx-neues-konto','--nur-pruefen','--state-dir',str(tmp_path)])
    switch.main()
    assert 'Kein Kontowechsel' in capsys.readouterr().out
    assert not (tmp_path/'okx_credentials.json').exists()


def test_precheck_reports_account_mode_and_unsupported_funding_without_guessing(monkeypatch):
    import config
    monkeypatch.setattr(config,'OKX_ALLOWED_QUOTE_CCY',('EUR','USDC'))
    c=NS(demo=True,base_url='https://eea.okx.com',server_time_ms=lambda:1,
         account_config=lambda:{'uid':'fresh','acctLv':'2','posMode':'net_mode'},
         request=lambda method,path,**kw:[],
         balances=lambda:{'USD':{'cash':500.0,'gesamt':500.0}})
    result=inspect_new(c)
    assert result['account_mode']=={'acctLv':'2','posMode':'net_mode'}
    assert result['funding']['state']=='UNSUPPORTED_FUNDS_ONLY'
    assert result['funding']['funded_supported_currencies']==[]
    assert result['funding']['funded_unsupported_currencies']==['USD']
