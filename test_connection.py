"""Read-only Verbindungstest fuer den einzigen Broker eToro."""
from __future__ import annotations
from broker import get_broker


def main():
    broker=None
    try:
        broker=get_broker("etoro")
        print("Teste eToro", "DEMO" if broker.ist_paper() else "LIVE", "...")
        broker.connect()
        if hasattr(broker,"diagnose_credentials"):
            diag=broker.diagnose_credentials()
            for step in diag.get("steps",[]):
                print(f"  {'OK' if step.get('ok') else 'HINWEIS'} {step.get('step')}: {step.get('detail','')}")
        healthy=bool(broker.health_check(force=True)) if hasattr(broker,"health_check") else bool(broker.is_connected())
        if not healthy:
            print("Verbindungstest fehlgeschlagen: eToro antwortet nicht gesund."); return 1
        print("Verbunden.")
        print(f"Kontowert : {broker.kontowert():,.2f} {broker.kontowaehrung()}")
        print(f"Positionen: {len(broker.positionen())}")
        print("Kosten werden fuer konkrete Kaufkandidaten ueber eToro What-if geladen.")
        return 0
    except Exception as exc:
        print(f"eToro-Verbindung fehlgeschlagen: {exc}")
        print("Public-API-Key, User-Key, Internetverbindung und DEMO/LIVE-Modus pruefen.")
        return 1
    finally:
        try:
            if broker is not None: broker.disconnect()
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

if __name__=="__main__": raise SystemExit(main())
