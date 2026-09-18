from news_sources import MultiSourceNews
from sec_fundamentals import SecFundamentals


def main():
    print('KOSTENLOSE RESEARCH-SCHICHT')
    print('='*60)
    m=MultiSourceNews(); conf=m.provider_configuration(); health=m.health_snapshot()
    for name in ('GDELT','SEC EDGAR','Nasdaq Halts','Yahoo Finance','Google News','Alpha Vantage','Finnhub','FMP','MASSIVE'):
        c=conf.get(name,False); h=health.get(name,{})
        print(f'{name:15s} konfiguriert={"JA" if c else "NEIN":4s} status={h.get("state","unbekannt")} detail={h.get("detail","")}')
    s=SecFundamentals(); print('\nSEC Company Facts:', 'bereit' if s.enabled() else 'Kontakt-E-Mail fehlt (kein API-Key erforderlich)')



if __name__ == "__main__":
    main()
