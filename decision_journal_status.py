"""Konsolenansicht des TradingBot-8.1.1-NEXUS-Entscheidungsjournals."""
from decision_analytics import latest, summary, filter_performance, db_pfad



def main():
    print('='*100)
    print('TRADINGBOT 8.1.1 NEXUS – ENTSCHEIDUNGEN / NACHBEWERTUNG')
    print('='*100)
    s=summary()
    print(f"Geprueft/gespeichert: {s.get('total',0)} | freigegeben: {s.get('approved',0)} | abgelehnt: {s.get('blocked',0)} | Fill: {s.get('filled',0)}")
    if s.get('reasons'):
        print('Haeufigste Ablehnungsgruende:', ', '.join(f'{k}={v}' for k,v in s['reasons'].most_common(8)))

    rows=latest(40)
    if not rows:
        print('\nNoch keine Entscheidungen gespeichert.')
    else:
        print('\nLetzte Entscheidungen:')
        for r in rows:
            ai=r.get('ai') or {}; out=r.get('outcomes') or {}
            def ret(h):
                try:return f"{float(out[h]['return_pct']):+.2f}%"
                except Exception:return '-'
            print(f"{r.get('created_at_local','')} | {r.get('symbol','?'):8s} | {r.get('status','?'):8s} | Exec {r.get('execution_status','-'):9s} | Grund {r.get('blocked_by') or '-':18s} | GPT {ai.get('decision','-')} {ai.get('confidence','-')} | 1d {ret('1d')} | 5d {ret('5d')}")

    perf=filter_performance(10)
    if perf:
        print('\nFilter-Nachbewertung (nur vorhandene Outcomes):')
        for p in perf[:20]:
            note='SICHERHEITSFILTER – nicht auf entgangenen Gewinn optimieren' if p.get('category')=='safety' else 'Qualitaets-/Alpha-Filter'
            print(f"{p.get('blocked_by','?'):20s} {p.get('horizon','?'):3s} n={p.get('n',0):3d} avg={float(p.get('avg_return_pct') or 0):+.2f}% | {note}")
    print(f"\nDatenbank: {db_pfad()}")



if __name__ == "__main__":
    main()
