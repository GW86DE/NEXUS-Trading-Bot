from types import SimpleNamespace as NS
import pandas as pd
import numpy as np
import pytest

import freqtrade_candles as candles
import freqtrade_sample_strategy as strategy
import freqtrade_sample_backtest as backtest


def bars(n=650, start="2026-09-10", price=100.):
    return pd.DataFrame({"open":price,"high":price+1,"low":price-1,"close":price,"volume":100.},
                        index=pd.date_range(start, periods=n, freq="5min", tz="UTC"))


def test_official_startup_and_recursive_window_are_in_semantic_hash():
    params = strategy.parameter_snapshot()
    assert params["startup_candle_count"] == 200
    assert params["live_candle_window"] == 500
    assert "startup_candle_count" in params["parameter_hash_fields"]
    with pytest.raises(ValueError, match="200"):
        strategy.evaluate(bars(199))
    strategy.evaluate(bars(200))


def test_history_paginates_deduplicates_and_survives_restart(tmp_path):
    data = bars()
    calls = []
    class Client:
        demo=True
        base_url="https://test.okx.invalid"
        def candles(self, inst, **kw):
            calls.append(("latest",kw["limit"]))
            return data.tail(kw["limit"])
        def historical_candles(self, inst, **kw):
            calls.append(("archive",kw["end_ms"]))
            # Include overlap deliberately: cursors must still advance.
            return data.loc[data.index <= pd.Timestamp(kw["end_ms"],unit="ms",tz="UTC")].tail(100)
    client=Client()
    end=data.index[-1]+pd.Timedelta(minutes=5)
    path=tmp_path/'candles.sqlite'
    first=candles.History(client,path).load("BTC-USD",cutoff=end)
    assert len(first)==500 and first.index.is_unique
    pd.testing.assert_frame_equal(first[candles.COLUMNS],data.tail(500),check_freq=False)
    count=len(calls)
    again=candles.History(client,path).load("BTC-USD",cutoff=end)
    assert len(calls)==count
    pd.testing.assert_frame_equal(first,again)
    assert len(candles.History(client,path).load("BTC-EUR",cutoff=end,cached_only=True))==0
    client.demo=False
    assert len(candles.History(client,path).load("BTC-USD",cutoff=end,cached_only=True))==0


def test_gaps_and_duplicate_aggregation_use_reference_rules_without_future_fill():
    data=bars(5)
    data.loc[data.index[0],["open","high","close"]]=[100,104,103]
    gap=data.index[1]
    duplicate=data.iloc[[2]].copy()
    duplicate["high"]=105
    duplicate["volume"]=120
    raw=pd.concat([data.drop(gap),duplicate])
    df=candles.normalize(raw)
    assert len(df)==5 and df.index.is_unique
    assert df.loc[gap,"synthetic_gap"]
    assert df.loc[gap,"close"]==103 and df.loc[gap,"volume"]==0
    assert df.iloc[2]["high"]==105 and df.iloc[2]["volume"]==120
    assert len(candles.normalize(raw,cutoff=data.index[-1]))==4


def test_boundary_offset_and_final_signal_deadline():
    boundary=pd.Timestamp("2026-09-13T12:05:00Z").timestamp()
    assert candles.candle_cutoff(boundary+.5).hour==12
    assert candles.candle_cutoff(boundary+.5).minute==0
    assert candles.candle_cutoff(boundary+1).minute==5
    context={"strategy_mode":"FREQTRADE_SAMPLE","signal_instrument":"BTC-USD","signal_candle":"2026-09-13T12:00:00Z"}
    candles.require_signal_current(context,"BTC-USD",now=boundary+1)
    with pytest.raises(ValueError,match="Instrument"):
        candles.require_signal_current(context,"BTC-EUR",now=boundary+1)
    with pytest.raises(ValueError,match="veraltet"):
        candles.require_signal_current(context,"BTC-USD",now=boundary+601)
    assert not candles.latest_signal_valid(bars(),now=boundary+600)


def test_per_instrument_cursor_is_persistent_scoped_and_monotone():
    c=candles.ScanCursor("okx:demo:account:strategy3")
    stamp="2026-09-13T12:00:00Z"
    assert not c.seen("BTC",stamp)
    c.mark("BTC",stamp,17)
    assert candles.ScanCursor(c.domain).seen("BTC",stamp)
    assert not c.seen("ETH",stamp)
    assert not candles.ScanCursor("okx:live:account:strategy3").seen("BTC",stamp)
    assert not candles.ScanCursor("okx:demo:account:strategy2").seen("BTC",stamp)
    c.mark("BTC","2026-09-13T11:55:00Z",16)
    assert c.seen("BTC",stamp)


def test_signal_precedes_stop_and_roi_but_missing_history_never_blocks_price_exits(monkeypatch):
    evaluation=NS(exit=True,entry=False,audit_values=lambda:{})
    monkeypatch.setattr(strategy,"evaluate",lambda _:evaluation)
    assert strategy.exit_decision(bars(),entry_price=100,current_price=89,elapsed_minutes=0)[1]=="freqtrade_exit_signal"
    assert strategy.exit_decision(bars(),entry_price=100,current_price=105,elapsed_minutes=0)[1]=="freqtrade_exit_signal"
    def unavailable(_):raise ValueError("no data")
    monkeypatch.setattr(strategy,"evaluate",unavailable)
    assert strategy.exit_decision(None,entry_price=100,current_price=89,elapsed_minutes=0)[1]=="freqtrade_stop_loss"
    assert not strategy.exit_decision(None,entry_price=100,current_price=104,elapsed_minutes=0)[0]
    assert strategy.exit_decision(None,entry_price=100,current_price=104.001,elapsed_minutes=0)[0]


@pytest.mark.parametrize("scenario,expected",[("signal",102),("roi_clip",105),("new_roi",103)])
def test_backtest_exit_at_attainable_reference_price(monkeypatch,scenario,expected):
    df=bars(216)
    df["enter_long"]=False;df["exit_long"]=False
    df.loc[df.index[200],"enter_long"]=True
    if scenario=="signal":
        df.loc[df.index[201],"exit_long"]=True
        df.loc[df.index[202],["open","high","low","close"]]=[102,103,89,101]
    elif scenario=="roi_clip":
        df.loc[df.index[202],["open","high","low","close"]]=[106,107,105,106]
    else:
        df.loc[df.index[207],["open","high","low","close"]]=[103,103.5,102,103]
    monkeypatch.setattr(backtest,"_signals",lambda raw:df)
    result=backtest.run_backtest(df,fee_pct=0,slippage_pct=0)
    trade=result["trade_rows"][0]
    assert trade["exit_price"]==expected
    assert trade["entry_time"]==str(df.index[201])


def test_backtest_uses_actual_elapsed_minutes_not_row_count(monkeypatch):
    df=bars(204)
    idx=list(df.index);idx[202]=idx[201]+pd.Timedelta(minutes=65);idx[203]=idx[202]+pd.Timedelta(minutes=5)
    df.index=pd.DatetimeIndex(idx)
    df["enter_long"]=False;df["exit_long"]=False
    df.loc[df.index[200],"enter_long"]=True
    df.loc[df.index[202],["open","high","low","close"]]=[101.5,102,101.1,101.5]
    monkeypatch.setattr(backtest,"_signals",lambda raw:df)
    result=backtest.run_backtest(df,fee_pct=0,slippage_pct=0)
    assert result["trade_rows"][0]["exit_reason"]=="roi_1pct"


def test_thin_listing_does_not_repeat_archive_fetch_each_poll(tmp_path):
    data=bars(210);calls=[]
    class Client:
        demo=True;base_url='history.test'
        def candles(self,inst,**kw):calls.append(('latest',kw['limit']));return data.tail(kw['limit'])
        def historical_candles(self,inst,**kw):calls.append(('archive',kw['limit']));return data.iloc[:0]
    client=Client();path=tmp_path/'history.sqlite'
    cutoff=data.index[-1]+pd.Timedelta(minutes=5)
    first=candles.History(client,path).load('NEW-USD',cutoff=cutoff)
    assert len(first)==210
    calls.clear()
    candles.History(client,path).load('NEW-USD',cutoff=cutoff)
    assert not calls
    extra=data.iloc[[-1]].copy();extra.index=extra.index+pd.Timedelta(minutes=5)
    data=pd.concat([data,extra]);cutoff+=pd.Timedelta(minutes=5)
    later=candles.History(client,path).load('NEW-USD',cutoff=cutoff)
    assert len(later)==211 and calls==[('latest',5)]
