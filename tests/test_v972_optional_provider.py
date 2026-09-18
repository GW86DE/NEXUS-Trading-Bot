import builtins
import pytest


def test_missing_historical_provider_is_explicit(monkeypatch):
    import data
    original=builtins.__import__
    def without_yahoo(name,*a,**k):
        if name=='yfinance':raise ModuleNotFoundError('yfinance intentionally unavailable')
        return original(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',without_yahoo)
    with pytest.raises(RuntimeError,match='yfinance fehlt'):
        data.fetch_history_yfinance('ABC')


def test_missing_regime_provider_never_returns_checked(monkeypatch):
    import market_regime
    original=builtins.__import__
    def without_yahoo(name,*a,**k):
        if name=='yfinance':raise ModuleNotFoundError('yfinance unavailable')
        return original(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',without_yahoo)
    result=market_regime.MarketRegimeClient().get()
    assert not result.checked and result.name=='UNKNOWN' and 'yfinance' in result.details
