"""10.1.7: real local state, synthetic GET-only exchange; no trading/network."""
from types import SimpleNamespace as NS
import json
import sqlite3
import zipfile
import hashlib
import pytest
from test_v975_execution_and_repair import engine, persist


def client(uid='new'):
    calls=[]
    def request(method,path,**kw):
        assert method=='GET'
        calls.append((path,kw))
        return []
    return NS(demo=True,base_url='https://eea.okx.com',server_time_ms=lambda:1,
        account_config=lambda:{'uid':uid},request=request,balances=lambda:{'EUR':{'cash':1000}},calls=calls)


def test_unknown_position_and_ledger_survive_repeated_snapshots_and_reload(engine,monkeypatch):
    import trade_ledger as tl
    from crypto_engine import KryptoPositionsbuch
    from risk_manager import RiskState
    p,tid=persist(engine);p.protection_status='ACTIVE';p.broker_schutz=True;p.protection_algo_id='original'
    engine.topf.state=RiskState();engine.bereitschaft=NS(melde=lambda *a:None)
    monkeypatch.setattr(engine,'_externer_verkaufsbeweis',lambda *a:None)
    for _ in range(4): engine._position_verschwunden(p,{})
    loaded=KryptoPositionsbuch(engine.buch.datei).hole('SUI')
    assert loaded.menge==p.menge and loaded.stop==p.stop and loaded.take_profit==p.take_profit
    assert loaded.broker_state=='BROKER_STATE_UNKNOWN'
    assert loaded.protection_status=='UNKNOWN_BROKER_STATE'
    assert loaded.last_confirmed_protection['status']=='ACTIVE'
    assert loaded.protection_algo_id=='original' and loaded.ist_bewiesene_botposition
    assert not loaded.darf_schutz_ausfuehren and not loaded.darf_automatisch_verkaufen
    assert tl.trade_detail(tid)['ausgestiegen_am'] is None
    assert tl.trade_detail(tid)['reconciliation_status']=='BROKER_STATE_UNKNOWN'
    assert not engine.topf.state.realized_receipts


@pytest.mark.parametrize('balance',[0,1,50,float('nan'),float('inf')])
def test_missing_and_invalid_snapshot_rejected_before_equity(engine,balance):
    from okx_snapshot_guard import validate
    import math
    p,_=persist(engine)
    engine.broker.guthaben_schnappschuss=lambda:{'SUI':{'gesamt':balance}}
    r=validate(engine.broker,[p])
    if math.isfinite(balance):
        # 10.7.0: fehlender/reduzierter Bestand sperrt SUI, nicht die Domaene.
        assert r['valid'] and 'SUI' in r['missing'] and 'SUI' in r['blocked_symbols']
    else:
        assert not r['valid']      # unbrauchbare Messung: nichts ist belegt


def test_consistent_snapshot_and_ledger_only_gap(engine):
    from okx_snapshot_guard import validate
    p,_=persist(engine)
    engine.broker.guthaben_schnappschuss=lambda:{'SUI':{'gesamt':p.menge},'EUR':{'cash':1000}}
    assert validate(engine.broker,[p])['valid']
    engine.broker.guthaben_schnappschuss=lambda:{}
    assert validate(engine.broker,[])['missing']==['SUI']


def test_invalid_snapshot_keeps_risk_after_restart(tmp_path):
    from risk_pots import RiskPotManager
    manager=RiskPotManager(['okx'])
    pot=manager.topf('okx');pot.setze_kontowert(76751)
    before=pot.state.last_equity
    broker=NS(_nexus_snapshot_valid=False,handelbares_kapital=lambda *_:7291)
    pot._snapshot_error='BROKER_STATE_UNKNOWN'
    assert manager.aktualisiere_kontowerte(NS(broker=lambda _:broker))['okx']==76751
    assert pot.state.last_equity==before and not pot.state.equity_guard_halted
    assert not pot.darf_kaufen()[0]
    restarted=RiskPotManager(['okx'])
    assert restarted.aktualisiere_kontowerte(NS(broker=lambda _:broker))['okx']==76751


def test_ledger_only_missing_never_closed(engine,monkeypatch):
    import trade_ledger as tl
    p,tid=persist(engine);engine.buch.entferne(p.symbol)
    engine._fehlender_ledger_bestand={}
    engine.broker.historical_fills=lambda *a,**kw:[]
    # 10.8.1: Auch eine Ledgerzeile ohne Position braucht zwei Messungen
    # (Mindestabstand hier 0 s). Die erste Messung allein bucht nichts.
    engine.cfg.OKX_POSITION_MISSING_CONFIRM_SECONDS=0
    engine._offene_ledger_abgleichen({})
    assert tl.trade_detail(tid)['reconciliation_status']!='BROKER_STATE_UNKNOWN'
    for _ in range(2): engine._offene_ledger_abgleichen({})
    row=tl.trade_detail(tid)
    assert row['ausgestiegen_am'] is None and row['reconciliation_status']=='BROKER_STATE_UNKNOWN'


def test_switch_archives_database_preserves_other_state_and_54092_scope(engine,tmp_path):
    from okx_account_switch import activate
    from okx_account_context import bind_verified
    import trade_ledger as tl
    import okx_account_action as action
    p,tid=persist(engine)
    bind_verified('A','DEMO');action.record('A','DEMO',instrument='SUI')
    (tmp_path/'risk_state_okx.json').write_text(json.dumps({'last_equity':76751}))
    for name in ('stock_positions.json','risk_state_etoro.json','ai_router_usage.json','crypto_strategy_mode.json'):
        (tmp_path/name).write_text(json.dumps({'keep':name}))
    preserved={n:(tmp_path/n).read_bytes() for n in ('stock_positions.json','risk_state_etoro.json','ai_router_usage.json','crypto_strategy_mode.json')}
    result=activate(tmp_path,client())
    for n,v in preserved.items():assert (tmp_path/n).read_bytes()==v
    assert json.loads((tmp_path/'crypto_positions.json').read_text())['positionen']==[]
    assert json.loads((tmp_path/'risk_state_okx.json').read_text())=={}
    assert tl.trade_detail(tid)['ausgestiegen_am'] is None
    assert tl.offene_trades('okx')==[]
    assert not action.status(result['account'],'DEMO')['blocked']
    assert action.status('A','DEMO')['blocked']
    with zipfile.ZipFile(tmp_path/result['archive']) as z:
        info=json.loads(z.read('ACCOUNT_ARCHIVE.json'))
        assert json.loads(z.read('crypto_positions.json'))['positionen'][0]['symbol']=='SUI'
        assert 'decision_history.sqlite' in z.namelist()
        for n,h in info['sha256'].items():assert hashlib.sha256(z.read(n)).hexdigest()==h
    bind_verified(result['account'],'DEMO')
    with pytest.raises(Exception,match='CONTEXT_MISMATCH'):bind_verified('A','DEMO')


@pytest.mark.parametrize('fault',['orders','fills','api_error','bad_format','no_uid','live'])
def test_precheck_failure_never_resets_state(tmp_path,fault):
    from okx_account_switch import activate
    c=client();p=tmp_path/'crypto_positions.json';p.write_text('{"positionen":[]}')
    if fault=='no_uid':c.account_config=lambda:{}
    elif fault=='live':c.demo=False
    else:
        def request(method,path,**kw):
            assert method=='GET'
            if fault=='api_error':raise OSError('offline')
            if fault=='bad_format':return None
            return [{'order':'existing'}] if (fault=='orders' or 'fills' in path) else []
        c.request=request
    with pytest.raises(Exception):activate(tmp_path,c)
    assert p.read_text()=='{"positionen":[]}'
    assert not (tmp_path/'okx_account_context.json').exists()


def test_interrupted_commit_blocks_start(tmp_path,monkeypatch):
    import okx_account_switch as sw
    from okx_account_context import bind_verified
    original=sw.atomic_write_json
    def fail(p,v):
        if p.name=='risk_state_okx.json':raise OSError('power loss')
        original(p,v)
    monkeypatch.setattr(sw,'atomic_write_json',fail)
    with pytest.raises(OSError):sw.activate(tmp_path,client())
    assert (tmp_path/'okx_account_switch_pending.json').exists()
    with pytest.raises(Exception,match='SWITCH_INCOMPLETE'):bind_verified('new','DEMO')


def test_reactivation_is_refused_even_if_old_position_book_empty(tmp_path):
    from okx_account_switch import activate,inspect_new
    c=client();old=inspect_new(c)['account']
    (tmp_path/'okx_account_context.json').write_text(json.dumps(dict(account='current',environment='DEMO',strict=True,retired_accounts=[old])))
    (tmp_path/'crypto_positions.json').write_text('{"positionen":[]}')
    with pytest.raises(Exception,match='Reaktivierung'):activate(tmp_path,c)


@pytest.mark.parametrize('environment',['DEMO','LIVE'])
def test_account_mismatch_refuses_new_order_reservations(tmp_path,environment):
    from okx_account_context import bind_verified
    import execution_lifecycle as life
    bind_verified('new','DEMO')
    for side in ('BUY','SELL'):
        with pytest.raises(Exception,match='CONTEXT_MISMATCH'):
            life.reserve(broker='okx',account='old',environment=environment,instrument='BTC-EUR',side=side,client_id='x',quantity=1,request={})


def test_same_account_cannot_reset_its_risk(tmp_path):
    from okx_account_switch import activate
    c=client();activate(tmp_path,c)
    (tmp_path/'risk_state_okx.json').write_text('{"last_equity":123}')
    with pytest.raises(Exception,match='bekannt'):activate(tmp_path,c)
    assert (tmp_path/'risk_state_okx.json').read_text()=='{"last_equity":123}'


def test_empty_balance_does_not_cancel_any_protective_order():
    from broker.okx import OKXBroker
    def forbidden(*a,**kw):pytest.fail('Leerer Kontostand ist keine Stornoerlaubnis')
    c=NS(balances=lambda:{},cancel_algo_orders=forbidden,cancel_order=forbidden)
    b=OKXBroker(client=c,demo=True)
    assert b.verwaiste_orders_aufraeumen()==0


def test_new_account_excludes_unbound_legacy_accounting(tmp_path,engine):
    import trade_ledger as tl
    import okx_accounting as gate
    from okx_account_switch import activate
    p,tid=persist(engine)
    with tl._connect() as con:
        con.execute("UPDATE trades SET broker_account_fingerprint='',ausgestiegen_am='2026-09-15T01:00:00Z',netto_pnl=NULL WHERE trade_id=?",(tid,))
    result=activate(tmp_path,client())
    assert gate.status(result['account'],'DEMO')['complete']
    assert tl.trade_detail(tid)['broker_account_fingerprint']==''


def test_archives_survive_next_version_migration(tmp_path,monkeypatch):
    from okx_account_switch import activate
    from settings_migration import migrate_from
    src=tmp_path/'old';dst=tmp_path/'next';src.mkdir();dst.mkdir()
    result=activate(src,client())
    migrate_from(src,dst)
    assert (dst/result['archive']).read_bytes()==(src/result['archive']).read_bytes()
    assert json.loads((dst/'okx_account_context.json').read_text())['account']==result['account']


@pytest.mark.parametrize('qty',[float('nan'),float('inf'),-1])
def test_invalid_cash_alone_cannot_become_risk_basis(tmp_path,qty):
    from okx_snapshot_guard import validate
    b=NS(demo=True,account_fingerprint=lambda:'A',guthaben_schnappschuss=lambda:{'EUR':{'cash':qty}})
    assert not validate(b,[])['valid']


def test_old_missing_position_is_archived_as_candidate_not_recreated(engine,tmp_path):
    import trade_ledger as tl
    from okx_accounting import mark_balance_gap
    from okx_account_switch import activate
    p,tid=persist(engine)
    mark_balance_gap(tl.trade_detail(tid),0,p.menge)
    engine.buch.entferne(p.symbol)
    result=activate(tmp_path,client())
    with zipfile.ZipFile(tmp_path/result['archive']) as z:
        info=json.loads(z.read('ACCOUNT_ARCHIVE.json'))
        candidate=info['reactivation_candidates'][0]
        assert candidate['balance_gap']['trade_id']==tid
        assert candidate['trade_snapshot']['symbol']=='SUI'
        assert candidate['position_restore_authorized'] is False
    assert json.loads((tmp_path/'crypto_positions.json').read_text())['positionen']==[]


def test_updater_new_account_runs_before_state_promotion(tmp_path,monkeypatch):
    import decision_analytics as da
    import trade_ledger as tl
    from test_v975_installer import make_root,FakeHost
    from nexus_update import Installer
    from settings_migration import migrate_from
    from okx_account_switch import activate
    source=make_root(tmp_path/'old');target=make_root(tmp_path/'new')
    monkeypatch.setattr(da,'DB_PATH',source/'decision_history.sqlite');da.init_db();tl.init_ledger()
    for name in ('etoro_reconciliation.json','fill_progress.json'):(source/name).write_text('{}')
    (source/'bot_order_registry.json').write_text('{"orders":{},"pending":{}}')
    (source/'okx_credentials.json').write_text('{"live_trading":false}')
    (source/'handelsmodus.txt').write_text('paper')
    host=FakeHost(source);calls=[]
    def command(args,**kw):
        calls.append(str(args[1]))
        if args[1]=='-c' and len(args)==5:
            migrate_from(args[3],args[4],strict=True)
        elif args[1]=='okx_account_switch.py':
            assert not (target/'crypto_positions.json').exists()
            activate(args[-1],client())
        elif args[1]=='-c':return json.dumps(dict(etoro=True,okx=True,paper=True,okx_live=False))
        return ''
    host.command=command
    installer=Installer(target,host,okx_neues_konto=True)
    installer.migrate(source)
    assert 'okx_account_switch.py' in calls
    assert calls.index('okx_account_switch.py')<calls.index('volltest.py')
    assert json.loads((target/'crypto_positions.json').read_text())['positionen']==[]
    assert (target/'okx_account_context.json').exists()
    assert list(target.glob('okx_account_archive_*.zip'))


def test_tradeable_capital_values_allowed_usdc_in_primary_currency(monkeypatch):
    from broker.okx import OKXBroker
    b=OKXBroker(client=NS(),demo=True,quote_ccy='EUR',allowed_quotes=('EUR','USDC'))
    monkeypatch.setattr(b,'_balances_stream_or_rest',lambda:{
        'EUR':{'cash':0.0,'gesamt':0.0},
        'USDC':{'cash':1000.0,'gesamt':1000.0},
        'USD':{'cash':75.0,'gesamt':75.0},
    })
    monkeypatch.setattr(b,'quote_conversion_rate',lambda source,target: 0.92 if (source,target)==('USDC','EUR') else None)
    assert b.handelbares_kapital()==920.0
    evidence=b.capital_evidence()
    assert evidence['basis_currency']=='EUR'
    assert evidence['funded_allowed_lanes']==['USDC']
    assert evidence['unsupported_positive_balance_currencies']==['USD']
    assert evidence['total_in_basis']==920.0


def test_tradeable_capital_never_assumes_stablecoin_parity(monkeypatch):
    from broker.okx import OKXBroker
    from broker.base import BrokerFehler
    b=OKXBroker(client=NS(),demo=True,quote_ccy='EUR',allowed_quotes=('EUR','USDC'))
    monkeypatch.setattr(b,'_balances_stream_or_rest',lambda:{'USDC':{'cash':1000.0,'gesamt':1000.0}})
    monkeypatch.setattr(b,'quote_conversion_rate',lambda *a:None)
    with pytest.raises(BrokerFehler,match='RISK_CAPITAL_FX_UNKNOWN'):
        b.handelbares_kapital()
    assert b.capital_evidence()['unconverted'][0]['currency']=='USDC'


def test_unsupported_usd_cash_is_visible_but_not_tradeable(monkeypatch):
    from broker.okx import OKXBroker
    b=OKXBroker(client=NS(),demo=True,quote_ccy='EUR',allowed_quotes=('EUR','USDC'))
    monkeypatch.setattr(b,'_balances_stream_or_rest',lambda:{'USD':{'cash':500.0,'gesamt':500.0}})
    assert b.handelbares_kapital()==0.0
    evidence=b.capital_evidence()
    assert evidence['unsupported_positive_balance_currencies']==['USD']
    assert evidence['funded_allowed_lanes']==[]
