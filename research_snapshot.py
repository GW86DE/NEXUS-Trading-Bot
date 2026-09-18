"""Read-only Research-Snapshot fuer einen einzelnen US-Ticker.

Keine Order, keine Universumsfreigabe und keine OpenAI-Abfrage.
"""
from __future__ import annotations
import argparse
from console_io import configure_utf8_console
from news_sources import MultiSourceNews
from sec_fundamentals import SecFundamentals

configure_utf8_console()


def run(symbol: str) -> int:
    symbol=str(symbol or "").strip().upper()
    if not symbol:
        print("Kein Symbol eingegeben.")
        return 2
    print("\nGRATIS-RESEARCH",symbol,flush=True)
    print("="*72,flush=True)
    n=MultiSourceNews()
    try:
        b=n.fetch_symbol(symbol,hours=48)
        print(f"News: {len(b.items)} Meldungen | Quellen OK: {', '.join(b.sources_ok) or 'keine'}")
        for x in b.items[:10]:
            ts=x.published_at.isoformat() if x.published_at else "?"
            print(f"- [{x.source}] {ts[:19]} | {x.headline[:120]}")
            if x.url: print("  ",x.url)
        if b.sources_failed: print("Fehler:",b.sources_failed)
        if n.provider_configuration().get("FMP"):
            try:
                fd=n.fmp_diagnostic(symbol)
                print("\nFMP STABLE DIAGNOSE")
                print(f"Symbolsuche: {'OK' if fd.get('resolved') else 'kein exakter Treffer'}")
                if fd.get('company'): print("Unternehmen:",fd.get('company'))
                if fd.get('exchange'): print("Boerse:",fd.get('exchange'))
                print("Gezielte Stock-News (7 Tage):",fd.get('news',0))
            except Exception as exc:
                print("FMP-Test fehlgeschlagen:",exc)
    except Exception as exc:
        print("News-Test fehlgeschlagen:",exc)

    print("\nSEC COMPANY FACTS")
    sec=SecFundamentals()
    if not sec.enabled():
        print("Nicht aktiv: SEC Kontakt-E-Mail unter 'Kostenlose Research-Quellen' eintragen (kein API-Key).")
    else:
        try:
            snap=sec.snapshot(symbol)
            if not snap.get('available'):
                print("Keine SEC-Daten:",snap.get('reason','unbekannt'))
            else:
                print("Unternehmen:",snap.get('company'))
                for key,label in [('revenue','Umsatz'),('net_income','Nettoergebnis'),('eps_diluted','EPS verw.'),('operating_cashflow','Operativer Cashflow'),('assets','Assets'),('liabilities','Verbindlichkeiten')]:
                    row=snap.get(key)
                    if row: print(f"{label:22s}: {row.get('value')} {row.get('unit','')} | {row.get('form')} | Periode {row.get('end')} | Filed {row.get('filed')}")
        except Exception as exc:
            print("SEC-Test fehlgeschlagen:",exc)
    print("\nHinweis: Dieser Test ruft GPT NICHT auf und verursacht keine OpenAI-Kosten.")
    return 0


def main(argv=None):
    ap=argparse.ArgumentParser(add_help=True)
    ap.add_argument("--symbol", default="")
    args=ap.parse_args(argv)
    symbol=args.symbol.strip().upper()
    if not symbol:
        print("US-Ticker (z.B. AAPL): ",end="",flush=True)
        try:
            symbol=input().strip().upper()
        except EOFError:
            print("\nKeine Eingabe verfuegbar.")
            return 2
    return run(symbol)

if __name__=='__main__': raise SystemExit(main())
