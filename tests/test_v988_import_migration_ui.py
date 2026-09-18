"""Offline imports, staged migration and actual SVG execution (no remote browser).

The fixture follows the read-only exporter schema but contains invented account
and order IDs. Production receipts never enter the distributed test fixtures.
"""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import sqlite3
import subprocess
import zipfile
import pytest
import trade_ledger as tl
import okx_closed_reconciliation as cr
from okx_receipt_math import EvidenceError
from okx_receipt_import import read_bundle,extract,import_bundle
from test_v975_execution_and_repair import engine
from test_v987_closed_results import closed
from test_v988_accounting import add_quote,split


def wrapper(endpoint,params,rows):
    return dict(method='GET',endpoint=endpoint,params=params,environment='DEMO',http_status=200,
        ok=True,response={'code':'0','data':rows})


@pytest.fixture
def bundle(closed,tmp_path):
    x=closed;add_quote(x)
    # An almost fully sold entry, with a truly tiny remainder; not an invented zero.
    x['sell'][0]['fillSz']='124.771765';x['sell'][0]['fee']='-.1'
    tl.set_protection(x['tid'],algo_id='protect',client_order_id='Pbuy',status='UNKNOWN')
    x['row']=tl.trade_detail(x['tid'])
    buy=x['broker'].client.order_status('SUI-USDC',ord_id='100');buy['tradeQuoteCcy']=''
    sell=x['broker'].client.order_status('SUI-USDC',ord_id='200');sell.update(tradeQuoteCcy='',algoId='protect',algoClOrdId='Pbuy',source='7')
    algo={**x['algo'],'algoClOrdId':'Pbuy','actualSide':'sl'}
    data=dict(schema='nexus-doge-sale-readonly-v1',read_only=True,automatic_booking=False,
        expected_account_fingerprint='A',environment='DEMO',instrument='SUI-USDC',
        account_check={'matched':True,'ok':True,'fingerprint':'A','api_code':'0'},user_supplied_sell_order='200',
        orders={'purchase':wrapper('/api/v5/trade/order',{'instId':'SUI-USDC','ordId':'100'},[buy]),
                'sale':wrapper('/api/v5/trade/order',{'instId':'SUI-USDC','ordId':'200'},[sell])},
        historical_algo=wrapper('/api/v5/trade/order-algo',{'algoId':'protect'},[algo]),
        sale_fills={ep:{'pages':[wrapper(ep,{'instType':'SPOT','instId':'SUI-USDC','ordId':'200','limit':'100'},deepcopy(x['sell']))]}
            for ep in ['/api/v5/trade/fills','/api/v5/trade/fills-history']})
    path=tmp_path/'receipt.json';path.write_text(json.dumps(data))
    return dict(**x,data=data,path=path,root=tmp_path)


def test_readonly_preview_does_not_book_money_and_apply_is_idempotent(bundle):
    x=bundle;before=tl.trade_detail(x['tid'])
    assert import_bundle(x['path'])['status']=='READY' and tl.trade_detail(x['tid'])==before
    out=import_bundle(x['path'],apply=True);assert out['status']=='APPLIED'
    row=tl.trade_detail(x['tid']);assert row['exit_grund']=='Broker-Stop-Loss (Ausfuehrung belegt)'
    assert import_bundle(x['path'],apply=True)['status']=='ALREADY_APPLIED'
    assert row==tl.trade_detail(x['tid'])
    with tl._connect() as con:
        assert con.execute('SELECT count(*) FROM okx_receipt_imports').fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM trade_native_exit_fills').fetchone()[0]==1


def test_already_recovered_receipt_is_verified_without_rebooking_or_overwriting(bundle):
    x=bundle
    cr.apply(x['row'],cr.prepare(x['row'],x['broker']))
    before=tl.trade_detail(x['tid'])
    result=import_bundle(x['path'],apply=True)
    assert result['status']=='ALREADY_VERIFIED' and tl.trade_detail(x['tid'])==before


@pytest.mark.parametrize('key,value',[('environment','LIVE'),('expected_account_fingerprint','B'),
    ('user_supplied_sell_order','201'),('instrument','SUI-EUR'),('read_only',False),
    ('automatic_booking',True),('schema','something-else')])
def test_incompatible_export_cannot_change_money(bundle,key,value):
    x=bundle;before=tl.trade_detail(x['tid']);data=deepcopy(x['data']);data[key]=value
    x['path'].write_text(json.dumps(data))
    with pytest.raises(EvidenceError):import_bundle(x['path'],apply=True)
    assert tl.trade_detail(x['tid'])==before


@pytest.mark.parametrize('part,field,value',[('sale','tradeQuoteCcy','EUR'),('sale','algoId','wrong'),
    ('sale','algoClOrdId','other-client'),('sale','state','live'),('sale','accFillSz','124'),
    ('purchase','clOrdId','wrong'),('purchase','tradeQuoteCcy','USD')])
def test_order_identity_currency_terminal_and_child_conflicts_remain_blocked(bundle,part,field,value):
    x=bundle;data=deepcopy(x['data']);data['orders'][part]['response']['data'][0][field]=value
    x['path'].write_text(json.dumps(data))
    with pytest.raises(EvidenceError):import_bundle(x['path'],apply=True)
    assert tl.trade_detail(x['tid'])['netto_pnl'] is None


def test_full_sale_id_without_local_protection_anchor_is_not_enough(bundle):
    x=bundle;tl.set_protection(x['tid'],algo_id='',client_order_id='')
    with pytest.raises(EvidenceError,match='Anker'):import_bundle(x['path'],apply=True)


def test_duplicate_json_keys_rejected(tmp_path):
    p=tmp_path/'bad.json';p.write_text('{"read_only":true,"read_only":false}')
    with pytest.raises(EvidenceError,match='Doppelter'):read_bundle(p)


@pytest.mark.parametrize('name',['../DOGE_Belege.json','/DOGE_Belege.json','x\\DOGE_Belege.json'])
def test_unsafe_receipt_archive_rejected(tmp_path,name):
    p=tmp_path/'bad.zip'
    with zipfile.ZipFile(p,'w') as z:z.writestr(name,'{}')
    with pytest.raises(EvidenceError,match='Unsicher'):read_bundle(p)


def test_zip_receipt_and_plain_json_have_same_source_hash(bundle,tmp_path):
    p=tmp_path/'safe.zip'
    with zipfile.ZipFile(p,'w') as z:z.writestr('DOGE_Belege.json',bundle['path'].read_bytes())
    assert read_bundle(p)==read_bundle(bundle['path'])


def test_missing_fill_and_successful_but_wrong_request_are_not_proof(bundle):
    x=bundle;data=deepcopy(x['data'])
    for part in data['sale_fills'].values():part['pages'][0]['params']['ordId']='999'
    x['path'].write_text(json.dumps(data))
    with pytest.raises(EvidenceError,match='orderbezogen'):import_bundle(x['path'],apply=True)


def test_migration_preview_uses_sqlite_backup_and_leaves_source_bytes_unchanged(bundle):
    from repair_okx_accounting import repair
    import decision_analytics as da
    x=bundle
    with tl._connect() as con:con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db=da.db_pfad();before=db.read_bytes()
    result=repair(db.parent,receipts=x['path'])
    assert result['preview'] and not result['applied'] and result['receipt_import']['status']=='APPLIED'
    assert db.read_bytes()==before and tl.trade_detail(x['tid'])['netto_pnl'] is None


def test_migration_requires_stopped_writers_and_preserves_a_backup(bundle):
    from repair_okx_accounting import repair,AccountingRepairError,REPORT_NAME
    import decision_analytics as da
    x=bundle;root=da.db_pfad().parent
    with pytest.raises(AccountingRepairError,match='gestoppten'):
        repair(root,receipts=x['path'],apply=True)
    result=repair(root,receipts=x['path'],apply=True,workers_stopped=True)
    assert result['applied'] and (root/REPORT_NAME).is_file()
    backup=Path(result['backup'])/'decision_history.sqlite'
    assert backup.is_file() and backup.stat().st_mode&0o777==0o600
    with sqlite3.connect(backup) as c:
        assert c.execute('SELECT netto_pnl FROM trades WHERE trade_id=?',(x['tid'],)).fetchone()[0] is None
    second=repair(root,receipts=x['path'],apply=True,workers_stopped=True)
    assert second['receipt_import']['status']=='ALREADY_APPLIED'


def test_invalid_receipts_do_not_reclassify_other_residuals(split,tmp_path):
    from repair_okx_accounting import repair
    import decision_analytics as da
    path=tmp_path/'wrong.json';path.write_text('{}')
    before=tl.trade_detail(split['rest_id'])
    with pytest.raises(EvidenceError):repair(da.db_pfad().parent,receipts=path,apply=True,workers_stopped=True)
    assert tl.trade_detail(split['rest_id'])==before


def test_readonly_source_zip_cannot_be_symlink(bundle,tmp_path):
    p=tmp_path/'link';p.symlink_to(bundle['path'])
    with pytest.raises(EvidenceError):read_bundle(p)


def test_preview_sees_committed_wal_not_only_the_main_database(bundle):
    from repair_okx_accounting import backup_state
    import decision_analytics as da
    root=da.db_pfad().parent
    with tl._connect() as con:
        con.execute('CREATE TABLE wal_probe (value TEXT)');con.execute("INSERT INTO wal_probe VALUES('committed-in-wal')");con.commit()
        dest=root/'backup';backup_state(root,dest)
        with sqlite3.connect(dest/'decision_history.sqlite') as check:
            assert check.execute('SELECT value FROM wal_probe').fetchone()[0]=='committed-in-wal'


def test_new_repair_report_is_runtime_not_immutable_source():
    from settings_migration import PERSISTENT_FILES
    assert 'okx_accounting_repair_report.json' in PERSISTENT_FILES


NODE_RENDER = r"""
const fs=require('fs'),vm=require('vm');const input=JSON.parse(fs.readFileSync(0,'utf8'));
const ids=['group','mode','unit','days','chart','metrics','table','method','status'];const nodes={};
for(const id of ids)nodes['performance-'+id]={value:'',innerHTML:'',textContent:'',clientWidth:input.width||840,
  addEventListener(){},replaceChildren(){this.innerHTML='';}};
nodes['performance-group'].value=input.data.groups[0]?.group_key||'';
nodes['performance-mode'].value=input.mode;nodes['performance-unit'].value=input.unit;
nodes['performance-days'].value='1';
// Load the production helper dependency, including the real bounded scheduler.
// The timer stub prevents periodic retries; the initial rendering runs normally.
const ctx={document:{readyState:'loading',hidden:false,addEventListener(){},getElementById:id=>nodes[id]},
  window:{addEventListener(){}},setTimeout(){},clearTimeout(){},
  api:async()=>input.data,console};
vm.runInNewContext(fs.readFileSync(require('path').join(require('path').dirname(process.argv[1]),'common.js'),'utf8'),ctx,{timeout:5000});
ctx.api=async()=>input.data; // deterministic data fixture, no HTTP request
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx,{timeout:5000});
setImmediate(()=>console.log(JSON.stringify({chart:nodes['performance-chart'].innerHTML,table:nodes['performance-table'].innerHTML,metrics:nodes['performance-metrics'].innerHTML})));
"""


def render(data,mode='daily',unit='net',width=390):
    script=Path(__file__).resolve().parents[1]/'webui/static/performance.js'
    r=subprocess.run(['node','-e',NODE_RENDER,str(script)],input=json.dumps(dict(data=data,mode=mode,unit=unit,width=width)),
        capture_output=True,text=True,encoding="utf-8",errors="strict",check=True,timeout=15)
    return json.loads(r.stdout)


@pytest.mark.parametrize('mode',['daily','cumulative'])
@pytest.mark.parametrize('unit',['net','percent'])
def test_actual_javascript_renders_confirmed_partial_sum_with_unknown_day(mode,unit):
    from trade_performance import aggregate
    from test_v981_performance import row,NOW
    data=aggregate([row(1,netto_pnl=26.15),row(2,netto_pnl=None)],days=1,now=NOW)
    out=render(data,mode,unit)
    assert '<svg' in out['chart'] and 'bestätigten Teil' in out['chart']
    assert ('performance-partial' if mode=='daily' else 'performance-partial-point') in out['chart']
    assert ('26,15' if unit=='net' else '25,89') in out['chart']
    assert '26,15' in out['metrics'] and '(Teil)' in out['table']


def test_unknown_only_day_is_not_drawn_as_zero_profit():
    from trade_performance import aggregate
    from test_v981_performance import row,NOW
    out=render(aggregate([row(netto_pnl=None)],days=1,now=NOW))
    assert '<svg' not in out['chart'] and 'fehlen bestätigte' in out['chart']
    assert 'nicht bekannt' in out['table']


def test_residual_excluded_without_excluding_the_confirmed_sale():
    from trade_performance import aggregate
    from test_v981_performance import row,NOW
    data=aggregate([row(1,netto_pnl=26.15),row(2,netto_pnl=None,accounting_kind='RESIDUAL')],days=1,now=NOW)
    g=data['groups'][0];assert g['today']['net']==26.15 and g['today']['unknown']==0
    assert '26,15' in render(data)['chart']


def test_percentage_uses_proven_allocated_entry_capital_not_full_sibling_fee():
    from trade_performance import aggregate
    from test_v981_performance import row,NOW
    data=aggregate([row(netto_pnl=10,einstieg_gebuehr=500,entry_cost_basis=200)],days=1,now=NOW)
    assert data['groups'][0]['today']['percent']==5
