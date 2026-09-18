from pathlib import Path
import hashlib
import runpy
import sqlite3
import pytest

inspect=runpy.run_path(str(Path(__file__).resolve().parents[1]/'NEXUS_9_8_7_Statuspruefung.py'))['inspect']


def test_read_only_inventory_separates_protection_results_modes_currencies(tmp_path):
    p=tmp_path/'state.sqlite';con=sqlite3.connect(p)
    con.execute('CREATE TABLE trades(trade_id,broker,paper,broker_account_fingerprint,symbol,waehrung,ausgestiegen_am,einstieg_preis,ausstieg_preis,netto_pnl,fee_quality,protection_status)')
    con.executemany('INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[
        (1,'okx',1,'A','DOGE','USD','closed',.1,None,None,'UNKNOWN','ACTIVE'),
        (2,'etoro',1,'B','PEP','USD',None,100,None,None,'UNKNOWN','UNKNOWN'),
        (3,'etoro',0,'B','PEP','EUR','closed',100,110,10,'CONFIRMED','CLOSED')])
    con.commit();con.close();before=hashlib.sha256(p.read_bytes()).hexdigest()
    report=inspect(p)
    assert report['total_trades']==3 and len(report['domains'])==3 and len(report['items'])==2
    assert report['items'][0]['missing']==['BROKER_PROTECTION_UNCONFIRMED']
    assert 'EXIT_PRICE_MISSING' in report['items'][1]['missing']
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before


def test_missing_database_is_not_created(tmp_path):
    p=tmp_path/'not-here.sqlite'
    with pytest.raises(FileNotFoundError):inspect(p)
    assert not p.exists()


def test_wal_commits_are_seen_without_immutable_stale_snapshot(tmp_path):
    p=tmp_path/'wal.sqlite';con=sqlite3.connect(p)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE trades(trade_id,broker,paper,ausgestiegen_am)');con.commit()
    con.execute("INSERT INTO trades VALUES(1,'okx',1,NULL)");con.commit()
    assert inspect(p)['total_trades']==1
    con.close()
