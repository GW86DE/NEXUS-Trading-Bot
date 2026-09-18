"""Zeigt offene eToro-Orders und letzte Fills."""
from __future__ import annotations
from broker import get_broker
from _verbindungshinweis import freundlich

@freundlich
def main():
    b=get_broker("etoro")
    print("eToro offene Orders:")
    for row in b.offene_orders() or []:
        print(row)
    print("\neToro letzte Fills:")
    for row in (b.fills() or [])[-20:]:
        print(row)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
