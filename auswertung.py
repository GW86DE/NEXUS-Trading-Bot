"""Lokale Auswertung von Entscheidungen, Ausfuehrungen und Filterwirkung."""
from __future__ import annotations
from collections import Counter
from decision_analytics import latest
from ai_attention import AIAttentionPrioritizer


def main():
    rows=latest(10000)
    print("="*78); print("TRADINGBOT 8.1.1 NEXUS – AUSWERTUNG"); print("="*78)
    if not rows:
        print("Noch keine Entscheidungen in decision_history.sqlite."); return 0
    status=Counter(str(r.get("status") or "?") for r in rows)
    execs=Counter(str(r.get("execution_status") or "-") for r in rows)
    reasons=Counter(str(r.get("blocked_by") or "") for r in rows if r.get("blocked_by"))
    print("\nEntscheidungsstatus:")
    for k,n in status.most_common(): print(f"  {k:<24}{n:>7}")
    print("\nAusfuehrungsstatus:")
    for k,n in execs.most_common(): print(f"  {k:<24}{n:>7}")
    print("\nHaeufigste Blockaden:")
    for k,n in reasons.most_common(12): print(f"  {k:<30}{n:>7}")
    b=AIAttentionPrioritizer().budget_status()
    print("\nKI-Aufmerksamkeit (keine Handelsentscheidung):")
    print(f"  Priorisierungen heute: {b.get('calls',0)}/{b.get('max_calls_per_day',0)}")
    print(f"  Websuchen: {b.get('web_searches',0)}")
    return 0

if __name__=="__main__": raise SystemExit(main())
