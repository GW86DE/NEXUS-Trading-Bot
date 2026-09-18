"""eToro-Kontodiagnose ohne Orderausfuehrung."""
from __future__ import annotations
from broker import get_broker
from _verbindungshinweis import freundlich

@freundlich
def main():
    broker=get_broker("etoro")
    print("Broker:", broker.name)
    print("Modus:", "DEMO" if broker.ist_paper() else "LIVE")
    print("Kontowert:", broker.kontowert(), broker.kontowaehrung())
    print("Positionen:", len(broker.positionen()))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
