"""WebUI/CLI-Einstieg fuer den orderfreien OKX-Freqtrade-Backtest."""
from crypto_analysis import crypto_backtest, print_report


if __name__ == "__main__":
    print_report(crypto_backtest())
