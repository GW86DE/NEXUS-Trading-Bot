"""Real persistence/entry/exit methods; only the exchange transport is synthetic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS

import pytest

from order_ownership import OrderOwnershipRegistry, RegistryIdentityConflict


def meta(account='A', env='DEMO', role='ENTRY', client='buy', oid='100', **values):
    return dict(broker='okx', asset_type='crypto', symbol='SUI', inst_id='SUI-USDC',
                account_fingerprint=account, environment=env, role=role,
                cl_ord_id=client, client_order_id=client, ord_id=oid,
                decision_id=123, zustand='SUBMITTING', **values)


@pytest.fixture
def registry(tmp_path):
    return OrderOwnershipRegistry(tmp_path/'bot_order_registry.json')


@pytest.mark.parametrize('account,env', [('A','DEMO'),('A','LIVE'),('B','DEMO')])
def test_scoped_clear_removes_exact_key_and_preserves_other_accounts(registry, account, env):
    for a,e in [('A','DEMO'),('A','LIVE'),('B','DEMO')]:
        registry.register_pending('SUI',meta(a,e),'crypto')
    assert registry.clear_pending('SUI','crypto',broker='okx',environment=env,account_fingerprint=account,decision_id=123)
    assert len(registry.pending)==2
    assert not any(x['account_fingerprint']==account and x['environment']==env for x in registry.pending.values())


def test_ambiguous_symbol_clear_and_wrong_account_do_not_release(registry):
    for a in ('A','B'): registry.register_pending('SUI',meta(a),'crypto')
    assert not registry.clear_pending('SUI','crypto')
    assert not registry.clear_pending('SUI','crypto',account_fingerprint='missing')
    assert len(registry.pending)==2


def test_state_update_uses_existing_scoped_storage_key(registry):
    registry.register_pending('SUI',meta(),'crypto')
    assert registry.setze_zustand('SUI','FILLED','crypto',broker='okx',account_fingerprint='A',environment='DEMO',decision_id=123)
    assert not registry.nicht_terminale()


def test_orders_of_same_decision_are_never_aliases(registry):
    buy={**meta(), 'zustand':'FILLED'}
    registry.register_orders(['100'],'SUI',buy,asset_type='crypto')
    registry.register_orders(['okx:A:SUI-USDC:100:98'],'SUI',{**buy,'identifier_type':'FILL'},asset_type='crypto')
    original=deepcopy(registry.orders)
    for i in range(1,4):
        registry.register_orders([str(200+i)],'SUI',meta(role='EXIT',client=f'sell{i}',oid=str(200+i)),asset_type='crypto')
        registry.update_order(str(200+i),zustand='CANCELED')
    for k,v in original.items(): assert registry.orders[k]==v
    assert [registry.orders[str(200+i)]['client_order_id'] for i in range(1,4)]==['sell1','sell2','sell3']
    assert registry.orders['100']['role']=='ENTRY'
    assert registry.orders['100']['identifier_type'] != 'FILL' if 'identifier_type' in registry.orders['100'] else True


@pytest.mark.parametrize('change',[{'role':'EXIT'},{'account_fingerprint':'B'},{'environment':'LIVE'},{'inst_id':'SUI-EUR'}])
def test_identity_conflict_keeps_disk_and_memory(registry,change):
    registry.register_orders(['100'],'SUI',meta(),asset_type='crypto')
    before=registry.path.read_bytes(); memory=deepcopy(registry.orders)
    with pytest.raises(RegistryIdentityConflict): registry.update_order('100',**change)
    assert registry.path.read_bytes()==before
    assert registry.orders==memory


def test_etoro_explicit_order_reference_aliases_still_work(registry):
    data=dict(broker='etoro',asset_type='stock',symbol='KO',reference_id='ref',decision_id=12,zustand='SUBMITTED')
    registry.register_orders(['order','ref'],'KO',data)
    registry.update_order('order',zustand='FILLED',position_ids=['position'])
    assert registry.orders['ref']['zustand']=='FILLED'
    assert registry.orders['ref']['position_ids']==['position']


def test_two_registry_instances_do_not_lose_updates(tmp_path):
    a=OrderOwnershipRegistry(tmp_path/'r.json'); b=OrderOwnershipRegistry(a.path)
    a.register_orders(['100'],'SUI',meta(),asset_type='crypto')
    b.register_orders(['200'],'SUI',meta(role='EXIT',client='sell',oid='200'),asset_type='crypto')
    assert set(json.loads(a.path.read_text())['orders'])=={'100','200'}


def result(qty=125.21, price=.7843, fee=.438235, fid='98'):
    from broker.base import OrderErgebnis
    return OrderErgebnis(order_ids=['100'],status='FILLED',raw_status='filled',
        filled_quantity=qty-fee,gross_filled_quantity=qty,requested_quantity=qty,
        avg_fill_price=price,terminal=True,paper=True,fill_evidence_complete=True,
        client_order_id='buy',reference_id='buy',trade_quote_ccy='USDC',
        account_fingerprint='A',broker_environment='DEMO',
        fills=[dict(tradeId=fid,ordId='100',clOrdId='buy',instId='SUI-USDC',side='buy',
                    fillSz=str(qty),fillPx=str(price),fee=str(-fee),feeCcy='SUI',ts='1788796806482')],
        fill_ids=[f'okx:A:SUI-USDC:100:{fid}'],fees={'SUI':fee},fees_quote=fee*price)


@pytest.fixture
def engine(monkeypatch,tmp_path):
    import config,decision_analytics as da
    from crypto_engine import CryptoEngine,KryptoPositionsbuch
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    monkeypatch.setattr(da,'DB_PATH',tmp_path/'decision_history.sqlite')
    da.init_db()
    assert da.record(dict(decision_id=123,symbol='SUI',asset_type='crypto',broker='okx',paper=True,status='APPROVED'))==123
    broker=NS(demo=True,account_fingerprint=lambda:'A',reconcile_position_protection=lambda *a,**k:
              dict(checked=True,protection_confirmed=True,algo_id='protect',algo_client_id='Pbuy'))
    pot=NS(setze_offene_positionen=lambda n:None)
    e=CryptoEngine.__new__(CryptoEngine)
    e.cfg=NS(BOT_ORDER_REGISTRY_FILE='bot_order_registry.json',OKX_EXIT_RETRY_MINUTES=5,
             OKX_EXIT_RETRY_MAX_MINUTES=60,OKX_EXIT_ESKALATION_VERSUCHE=5)
    e.hub=NS(broker=lambda _:broker); e.risiko=NS(topf=lambda _:pot)
    e.buch=KryptoPositionsbuch(tmp_path/'crypto_positions.json')
    e._instrument=lambda *a:NS(name=a[0],asset_type='crypto')
    e._taker_satz=lambda:.0035
    e.bereitschaft=NS(melde=lambda *a:None)
    e.messages=[];e._melde=lambda text,**kw:e.messages.append(str(text))
    e._ledger_schutz_synchronisieren=lambda *a:None
    return e


def intent():
    return {**meta(), 'qty':125.21,'signal_price':.8049,'stop':.72441,'take':.85,
            'trade_quote_ccy':'USDC','strategy_mode':'NEXUS_STANDARD',
            'strategy_identity':{'strategy_version':'TEST-STANDARD'}}


def persist(e,r=None):
    return e._persistiere_okx_entry(symbol='SUI',decision_id=123,ergebnis=r or result(),intent=intent(),nachtraeglich=True)


def close_ledger(p,tid,qty):
    import trade_ledger as tl
    return tl.trade_close(broker='okx',symbol='SUI',ausstieg_preis=.83,menge=qty,gebuehr=0,
        waehrung='USDC',paper=True,trade_id=tid,broker_position_id='SUI-USDC',
        broker_account_fingerprint='A',entry_order_id='100',exit_order_id='200',
        exit_fill_ids=['okx:A:SUI-USDC:200:1'],critical=True)


def test_entry_replay_preserves_retry_and_every_position_control(engine):
    e=engine;e._order_registry().register_pending('SUI',intent(),'crypto')
    p,tid=persist(e); assert tid
    p.exit_state='RETRY_WAIT';p.exit_fehlversuche=3
    p.exit_retry_after=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat()
    p.exit_attempt_id='old-attempt';p.exit_client_order_id='old-sell';p.hoechstkurs=1.2
    p.manual_changed_by='owner';e.buch.setze(p)
    before=p.als_dict();e._order_registry().register_pending('SUI',intent(),'crypto')
    q,two=persist(e)
    assert q is p and two==tid and q.als_dict()==before
    assert e._order_registry().nicht_terminale()=={}
    assert e._schliesse(q,.82,'target')=='COOLDOWN'
    assert e.buch.hole('SUI').als_dict()==before


def test_closed_entry_replay_does_not_recreate_position(engine):
    p,tid=persist(engine);close_ledger(p,tid,p.menge)
    engine.buch.positionen.clear();engine.buch.speichern()
    q,two=persist(engine)
    assert q is None and two==tid and not engine.buch.alle()


def test_partial_exit_replay_does_not_restore_sold_quantity(engine):
    p,tid=persist(engine);close_ledger(p,tid,25)
    p.menge-=25;p.exit_state='RETRY_WAIT';p.exit_fehlversuche=2;engine.buch.setze(p)
    before=p.als_dict();q,_=persist(engine)
    assert q is p and q.als_dict()==before


def test_new_late_entry_after_exit_never_changes_financial_rows(engine):
    import trade_ledger as tl,decision_analytics as da
    p,tid=persist(engine);close_ledger(p,tid,25)
    with sqlite3.connect(da.DB_PATH) as c: before=c.execute('SELECT * FROM trades ORDER BY trade_id').fetchall()
    q,_=persist(engine,result(5,.8,.01,'new'))
    assert q is None
    with sqlite3.connect(da.DB_PATH) as c: assert c.execute('SELECT * FROM trades ORDER BY trade_id').fetchall()==before


def test_real_entry_merge_separates_gross_net_and_base_fee(engine):
    import trade_ledger as tl
    p,tid=persist(engine,result(4,100,.04,'1'))
    q,two=persist(engine,result(6,110,.06,'2'))
    assert q is p and tid==two
    assert q.menge==pytest.approx(9.9) and q.einstieg==pytest.approx(106)
    assert q.entry_fee_quote==pytest.approx(10.6)
    assert q.entry_fee_by_currency['SUI']==pytest.approx(.1)


@pytest.mark.parametrize('closed',[False,True])
def test_same_fill_id_with_changed_amount_is_not_silently_accepted(engine,closed):
    import decision_analytics as da
    p,tid=persist(engine)
    if closed: close_ledger(p,tid,p.menge)
    with sqlite3.connect(da.DB_PATH) as c: before=c.execute('SELECT * FROM trades').fetchall()
    r=result();r.fills[0]['fillSz']='999'
    q,_=persist(engine,r); assert q is None
    with sqlite3.connect(da.DB_PATH) as c: assert c.execute('SELECT * FROM trades').fetchall()==before


@pytest.mark.parametrize('state',['SUBMITTING','UNCLEAR','ACCOUNTING_PENDING','MANUAL_EXIT_PENDING'])
def test_manual_exit_cannot_bypass_unresolved_order(engine,state):
    p,_=persist(engine);p.exit_state=state
    calls=[];engine.broker.schliesse_position=lambda *a,**k:calls.append(k)
    assert engine._schliesse(p,.82,'manual',allow_manual=True,explicit_manual=True)=='UNCLEAR'
    assert not calls


@pytest.mark.parametrize('account,env',[('B',True),('A',False),('',True)])
def test_exit_account_bound_before_exchange_call(engine,account,env):
    p,_=persist(engine);p.account_fingerprint=account;p.paper=env
    calls=[];engine.broker.schliesse_position=lambda *a,**k:calls.append(k)
    assert engine._schliesse(p,.82,'target')=='BLOCKED' and not calls


def test_terminal_no_fill_retries_keep_counter_and_use_new_client_id(engine):
    from broker.base import OrderErgebnis
    p,tid=persist(engine);calls=[]
    def sell(*a,**kw):
        calls.append(kw)
        return OrderErgebnis(status='canceled',terminal=True,fill_evidence_complete=True,
            order_ids=[str(200+len(calls))],client_order_id=kw['client_order_id'],
            paper=True,hinweis='FOK no fill')
    engine.broker.schliesse_position=sell
    assert engine._schliesse(p,.82,'target')=='FAILED'
    assert p.exit_fehlversuche==1 and p.exit_state=='RETRY_WAIT'
    first=p.exit_client_order_id
    assert engine._schliesse(p,.82,'target')=='COOLDOWN'
    # Entry replay must not shorten this wait.
    persist(engine);assert p.exit_fehlversuche==1 and p.exit_client_order_id==first
    p.exit_retry_after=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    assert engine._schliesse(p,.82,'target')=='FAILED'
    assert p.exit_fehlversuche==2 and p.exit_client_order_id!=first and len(calls)==2
    wait=(datetime.fromisoformat(p.exit_retry_after)-datetime.now(timezone.utc)).total_seconds()
    assert 10 <= wait <= 60  # Only terminal zero fills get the short retry.
    assert engine._order_registry().orders['100']['role']=='ENTRY'
    assert engine._order_registry().orders['201']['client_order_id']==first
    assert engine._order_registry().orders['202']['client_order_id']==p.exit_client_order_id


@pytest.mark.parametrize('terminal,complete',[(False,True),(True,False),(False,False)])
def test_zero_without_complete_terminal_proof_never_retries(engine,terminal,complete):
    from broker.base import OrderErgebnis
    p,_=persist(engine)
    engine.broker.schliesse_position=lambda *a,**k:OrderErgebnis(order_ids=['200'],status='canceled',
        terminal=terminal,fill_evidence_complete=complete)
    assert engine._schliesse(p,.82,'target')=='UNCLEAR'
    assert p.exit_state=='UNCLEAR' and p.exit_fehlversuche==0


def test_protection_failure_message_does_not_claim_confirmation(engine):
    p,_=persist(engine);p.broker_schutz=False;p.protection_status='MISSING'
    engine._exit_fehlversuch(p,'No fill')
    assert 'nicht bestaetigt' in engine.messages[-1].lower() or 'fehlt' in engine.messages[-1].lower()
    assert 'wurde sofort neu abgeglichen' not in engine.messages[-1]


def test_order_remainder_is_not_base_currency_fee(engine):
    import decision_analytics as da
    r=result();da.record_order_result(123,r,broker='okx',symbol='SUI',requested_qty=125.21,currency='USDC')
    with sqlite3.connect(da.DB_PATH) as c:
        row=c.execute("SELECT filled_qty,remaining_qty,raw_json FROM decision_orders WHERE broker_order_id='100'").fetchone()
    assert row[:2]==pytest.approx((125.21,0))
    assert json.loads(row[2])['net_filled_quantity']==pytest.approx(124.771765)
    assert r.execution_status=='FILLED'


def test_state_repair_is_proof_bound_and_idempotent(engine,tmp_path):
    import decision_analytics as da
    from repair_okx_state import repair
    p,tid=persist(engine)
    r=result();da.record_order_result(123,r,broker='okx',symbol='SUI',requested_qty=125.21,currency='USDC')
    with sqlite3.connect(da.DB_PATH) as c:
        c.execute("UPDATE decision_orders SET filled_qty=?,remaining_qty=?",(p.menge,.438235))
        financial=c.execute('SELECT * FROM trades').fetchall()
    path=tmp_path/'bot_order_registry.json';reg=json.loads(path.read_text())
    reg['orders']['100'].update(role='EXIT',zustand='CANCELED',client_order_id='old-sell')
    reg['pending']['crypto:SUI|okx|DEMO|A']=intent();path.write_text(json.dumps(reg))
    before=path.read_bytes()
    plan=repair(tmp_path);assert plan['changed'] and path.read_bytes()==before
    first=repair(tmp_path,apply=True);assert first['applied']
    assert repair(tmp_path,apply=True)['changed']==0
    fixed=json.loads(path.read_text());assert fixed['orders']['100']['role']=='ENTRY' and not fixed['pending']
    with sqlite3.connect(da.DB_PATH) as c:
        assert c.execute('SELECT * FROM trades').fetchall()==financial
        assert c.execute('SELECT filled_qty,remaining_qty FROM decision_orders').fetchone()==pytest.approx((125.21,0))
    assert p.als_dict()==engine.buch.hole('SUI').als_dict()


def test_repair_does_not_adopt_other_account_pending(engine,tmp_path):
    from repair_okx_state import repair
    persist(engine);reg=engine._order_registry();reg.register_pending('SUI',meta('B'),'crypto')
    repair(tmp_path,apply=True)
    assert list(engine._order_registry().pending.values())[0]['account_fingerprint']=='B'

@pytest.mark.parametrize('terminal,complete',[(False,True),(True,False),(False,False)])
def test_entry_not_booked_without_complete_terminal_evidence(engine,terminal,complete):
    r=result();r.terminal=terminal;r.fill_evidence_complete=complete
    assert persist(engine,r)==(None,None) and not engine.buch.alle()


def test_unified_usd_position_preserves_selected_settlement_lane(engine):
    from crypto_engine import KryptoPosition
    p=KryptoPosition('SUI','SUI-USD',1,1,.9,1.1,trade_quote_ccy='USDC')
    assert engine._quote_waehrung(p)=='USDC'


def test_plain_multiline_logs_remain_with_original_broker(monkeypatch):
    from webui import state
    rows=['2026-09-08 10:00:00 INFO crypto_engine: SUI',
          'Gewinnziel erreicht: keine Ausfuehrung','Was jetzt: Schutz nicht bestaetigt',
          '2026-09-08 10:00:01 ERROR live_trader: KO','freie Folgezeile',
          '2026-09-08 10:00:02 INFO main: allgemein','weitere Zeile']
    monkeypatch.setattr(state,'system_log',lambda **_:rows)
    assert [r['broker'] for r in state.system_log_scoped()]==['okx']*3+['etoro']*2+['system']*2


def test_filled_order_cannot_be_downgraded_by_delayed_poll(registry):
    registry.register_orders(['100'],'SUI',{**meta(),'zustand':'FILLED'},asset_type='crypto')
    registry.update_order('100',zustand='CANCELED')
    assert registry.orders['100']['zustand']=='FILLED'


@pytest.mark.parametrize('terminal,complete',[(False,False),(False,True),(True,False),(True,True)])
def test_legacy_recovery_marks_zero_only_with_complete_terminal_proof(engine,terminal,complete):
    e=engine;data={**intent(),'identity_schema':1,'zustand':'CANCELED'}
    reg=e._order_registry();reg.register_orders(['100'],'SUI',data,asset_type='crypto')
    b=e.broker
    b.reconcile_order_evidence=lambda _:NS(terminal=terminal,fill_evidence_complete=complete,
                                          gross_filled_quantity=0,filled_quantity=0)
    e._recover_terminal_okx_entries(b)
    row=e._order_registry().orders['100']
    assert bool(row.get('v950_recovery_checked')) == (terminal and complete)
    if terminal and complete:assert row['v950_recovery_result']=='NO_FILL_CONFIRMED'


def test_legacy_recovery_cannot_label_failed_booked_positive_fill_as_no_fill(engine,monkeypatch):
    e=engine;reg=e._order_registry();reg.register_orders(['100'],'SUI',
           {**intent(),'identity_schema':1,'zustand':'CANCELED'},asset_type='crypto')
    e.broker.reconcile_order_evidence=lambda _:result()
    monkeypatch.setattr(e,'_persistiere_okx_entry',lambda **_: (None,None))
    e._recover_terminal_okx_entries(e.broker)
    assert not e._order_registry().orders['100'].get('v950_recovery_checked')
