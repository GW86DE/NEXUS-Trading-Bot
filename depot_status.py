"""Zeigt den aktuellen eToro-Depotstatus; optional Telegram-Versand."""
from __future__ import annotations
from broker import get_broker
from notifier import notify
from _verbindungshinweis import freundlich

@freundlich
def main():
    b=get_broker("etoro"); b.connect()
    try:
        rows=list(b.positionen() or [])
        lines=["AKTUELLER ETORO-DEPOTSTATUS", f"Positionen: {len(rows)}"]
        for p in rows:
            lines.append(f"{getattr(p,'symbol','?')} · {getattr(p,'quantity',0)} @ {getattr(p,'avg_price',getattr(p,'avg_cost',0))}")
        text="\n".join(lines); print(text)
        if input("Per Telegram senden? (j/n): ").strip().lower() in {"j","ja","y","yes"}:
            notify("DEPOT-STATUS",text); print("Telegram-Nachricht angefordert.")
        return 0
    finally:
        b.disconnect()

if __name__=="__main__": raise SystemExit(main())
