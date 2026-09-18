"""Regression for the observed fresh-database WAL initialization race."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import subprocess
import sys
from pathlib import Path
import threading
import importlib
from types import SimpleNamespace as NS

import pytest

import execution_lifecycle as lifecycle
from broker.base import OrderStatusUnklar


@pytest.fixture
def da():
    # Older tests deliberately reload modules. Bind the same current module
    # used by execution_lifecycle, not a stale collection-time reference.
    return importlib.import_module('decision_analytics')


@pytest.mark.parametrize('round_number',range(10))
def test_fresh_parallel_reservations_have_one_winner(tmp_path,monkeypatch,round_number,da):
    monkeypatch.setattr(da,'DB_PATH',tmp_path/f'fresh-{round_number}.sqlite')
    barrier=threading.Barrier(16)
    def attempt(n):
        barrier.wait(timeout=10)
        try:
            return lifecycle.reserve(broker='okx',account='test',environment='DEMO',
                instrument='BTC-USDC',side='SELL',client_id=f'C{n}',quantity='1',request={})
        except OrderStatusUnklar:return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        answers=list(pool.map(attempt,range(16)))
    assert sum(x is not None for x in answers)==1
    assert len(lifecycle.snapshot())==1
    with da._connect() as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'


def test_connection_retries_only_temporary_lock_and_closes_failed_handle(tmp_path,monkeypatch,da):
    monkeypatch.setattr(da,'DB_PATH',tmp_path/'lock.sqlite')
    connect=sqlite3.connect;calls=[];closed=[]
    error=sqlite3.OperationalError('database is locked');error.sqlite_errorcode=sqlite3.SQLITE_BUSY
    def execute(sql):
        if sql.startswith('PRAGMA journal_mode'):raise error
    def opening(*a,**kw):
        calls.append(1)
        return NS(execute=execute,close=lambda:closed.append(1)) if len(calls)==1 else connect(*a,**kw)
    monkeypatch.setattr(da.sqlite3,'connect',opening)
    connection=da._connect()
    assert connection.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    connection.close()
    assert len(calls)==2 and closed==[1]


def test_disk_error_is_not_retried_or_swallowed(monkeypatch,tmp_path,da):
    monkeypatch.setattr(da,'DB_PATH',tmp_path/'broken.sqlite')
    error=sqlite3.OperationalError('disk I/O error');error.sqlite_errorcode=sqlite3.SQLITE_IOERR
    closed=[];opened=[]
    def execute(sql):raise error
    def connect(*a,**kw):
        opened.append(1);return NS(execute=execute,close=lambda:closed.append(1))
    monkeypatch.setattr(da.sqlite3,'connect',connect)
    with pytest.raises(sqlite3.OperationalError,match='disk I/O'):
        da._connect()
    assert closed==[1] and opened==[1]


def test_persistent_lock_has_a_deadline_and_closes_connection(monkeypatch,tmp_path,da):
    monkeypatch.setattr(da,'DB_PATH',tmp_path/'locked.sqlite')
    error=sqlite3.OperationalError('database is locked');error.sqlite_errorcode=sqlite3.SQLITE_BUSY
    closed=[]
    def execute(sql):raise error
    monkeypatch.setattr(da.sqlite3,'connect',lambda *a,**kw:NS(execute=execute,close=lambda:closed.append(1)))
    clock=iter([0,11]);monkeypatch.setattr(da,'time',NS(monotonic=lambda:next(clock),sleep=lambda _:None))
    with pytest.raises(sqlite3.OperationalError):da._connect()
    assert closed==[1]


def test_independent_processes_also_share_one_durable_reservation(tmp_path,monkeypatch,da):
    db=tmp_path/'processes.sqlite';monkeypatch.setattr(da,'DB_PATH',db)
    code="""
import sys
from pathlib import Path
import decision_analytics as da
da.DB_PATH=Path(sys.argv[1])
from execution_lifecycle import reserve
from broker.base import OrderStatusUnklar
try:
    reserve(broker='okx',account='test',environment='DEMO',instrument='BTC-USDC',
        side='SELL',client_id=sys.argv[2],quantity='1',request={})
    print('RESERVED')
except OrderStatusUnklar:
    print('BLOCKED')
"""
    children=[subprocess.Popen([sys.executable,'-c',code,str(db),f'C{i}'],
        cwd=Path(__file__).resolve().parents[1],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        for i in range(6)]
    outcomes=[]
    try:
        for child in children:
            out,err=child.communicate(timeout=25)
            assert child.returncode==0,err
            outcomes.append(out.strip())
    finally:
        for child in children:
            if child.poll() is None:child.kill();child.wait(timeout=5)
    assert outcomes.count('RESERVED')==1 and outcomes.count('BLOCKED')==5
    assert len(lifecycle.snapshot())==1
