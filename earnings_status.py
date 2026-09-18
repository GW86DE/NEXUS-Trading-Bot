from earnings_engine import EarningsClient
from watchlist import US_KERN, UNDERDOG_KANDIDATEN



def main():
    c=EarningsClient()
    print("="*74)
    print("EARNINGS INTELLIGENCE – NAECHSTE QUARTALSZAHLEN")
    print("="*74)
    print("Datenbasis:", ("SEC EDGAR kostenlos" + (" + Alpha Vantage Kalender/Estimates" if c.key else "")) if c.enabled() else "NICHT EINGERICHTET")
    if not c.enabled():
        print("Bitte unter 'Kostenlose Research-Quellen' eine SEC Kontakt-E-Mail eintragen (kein API-Key).")
        return
    if not c.key:
        print("Hinweis: SEC liefert veroeffentlichte Quartals-/Filing-Daten, aber keinen verlaesslichen Zukunftskalender. Fuer die Liste kommender Termine ist der optionale Alpha-Vantage-Key erforderlich.")

    c.calendar()
    from earnings_calendar_status import status
    health = status()
    print(health["detail"])
    if not health["available"]:
        return

    symbols=[]
    for s,_ in US_KERN:
        if s not in symbols: symbols.append(s)
    for s,_ in UNDERDOG_KANDIDATEN:
        if s not in symbols: symbols.append(s)

    hits=[]
    for s in symbols:
        days=c.days_to_earnings(s)
        if days is not None and days<=21:
            hits.append((days,s,"UNDERDOG" if any(s==u for u,_ in UNDERDOG_KANDIDATEN) else "KERN"))

    if not hits:
        print("Keine Termine in den naechsten 21 Tagen aus dem US-Universum gefunden.")
    else:
        for days,s,kind in sorted(hits):
            print(f"{s:7s} | {kind:8s} | Zahlen in {days:2d} Tag(en)")
    print("\nHinweis: Der Kalender liefert meist ein Datum, nicht immer eine exakte Uhrzeit.")



if __name__ == "__main__":
    main()
