"""WebUI/CLI-Einstieg fuer die orderfreie OKX-Freqtrade-Walk-Forward-Pruefung."""
from crypto_analysis import crypto_walkforward, print_report


if __name__ == "__main__":
    print_report(crypto_walkforward())
