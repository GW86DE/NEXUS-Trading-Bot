"""Offline-Selbsttest. Keine echte eToro-Verbindung notwendig."""
import logging
from pathlib import Path
import numpy as np, pandas as pd
import config

ROOT = Path(__file__).resolve().parent
from indicators import add_all_indicators
from strategy import generate_signal
from risk_manager import size_new_position, calculate_stop_take
from position_manager import PositionManager
from trade_messages import buy_message, sell_message

def main():
    log_level = getattr(logging, getattr(config, "LOG_LEVEL", "INFO"), logging.INFO)
    assert isinstance(log_level, int)
    # Version nicht fest verdrahten: eine feste Nummer laesst den Selbsttest bei
    # jedem Versionssprung zwangslaeufig fehlschlagen, ohne dass etwas kaputt
    # ist. Geprueft wird stattdessen, dass config und VERSION.txt zusammenpassen.
    _version = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() if (ROOT / "VERSION.txt").exists() else ""
    assert getattr(config, "VERSION_NEXUS", "") == _version, (
        f"config.VERSION_NEXUS={getattr(config, 'VERSION_NEXUS', '')!r} passt nicht "
        f"zu VERSION.txt={_version!r}")
    assert getattr(config, "OKX_BASE_URL", "") == "https://eea.okx.com"
    assert tuple(getattr(config, "OKX_ALLOWED_QUOTE_CCY", ())) == (
        "EUR", "USDC")
    _core = tuple(getattr(config, "CRYPTO_CORE_SYMBOLS", ()))
    assert len(_core) == 20 and len(set(_core)) == 20
    assert _core[:3] == ("BTC", "ETH", "SOL")
    assert int(getattr(config, "CRYPTO_UNIVERSE_DYNAMIC_LIMIT", 0)) == 30
    assert int(getattr(config, "CRYPTO_UNIVERSE_ACTIVE_LIMIT", 0)) == 50
    assert (Path(__file__).with_name("webui").joinpath("templates", "dashboard.html").exists())
    assert (Path(__file__).with_name("webui").joinpath("templates", "trades.html").exists())

    n=250
    x=np.linspace(100,130,n)+np.sin(np.arange(n)/5)*2
    df=pd.DataFrame({"open":x-0.5,"high":x+1,"low":x-1,"close":x,"volume":1000},
                    index=pd.date_range("2026-01-01",periods=n,freq="h"))
    d=add_all_indicators(df, sma_fast=config.SMA_FAST, sma_slow=config.SMA_SLOW, rsi_period=config.RSI_PERIOD)
    assert len(d.dropna())>100
    stop,take=calculate_stop_take(100,"BUY",2.0,"stock")
    assert stop<100<take
    assert size_new_position(10000,100,stop,asset_type="stock")>=1
    assert size_new_position(10000,100000,stop,asset_type="crypto")>=0
    sig=generate_signal(df,None)
    assert sig.action in {"BUY","SELL","HOLD"}

    buy = buy_message(
        symbol="TEST", asset_type="stock", qty=1, fill_price=100, currency="EUR",
        position_value=100, account_equity=10000, stop=95, take=110,
        risk_amount=5, risk_pct=0.0005, rr=2.0, reason="Test",
        ml_probability=0.5, profile="ausgewogen",
    )
    sell = sell_message(
        symbol="TEST", asset_type="stock", qty=1, entry_price=100, exit_price=110,
        currency="EUR", invested_value=100, proceeds=110, pnl=10, pnl_pct=0.1,
        reason="Test", holding_text="1 h 0 min", account_equity=10010,
        daily_realized=10, stop=95, take=110, order_label="TAKE-PROFIT",
    )
    assert "Risiko bis Stop" in buy and "Chance/Risiko" in buy
    assert "Gewinn/Verlust" in sell and "Haltedauer" in sell

    print("SELF TEST OK")

if __name__=="__main__": main()
