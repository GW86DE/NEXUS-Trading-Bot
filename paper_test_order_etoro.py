"""Sicherer eToro-DEMO-Ende-zu-Ende-Test.

Es wird niemals im LIVE-Modus gehandelt. Vor einer Demo-Order zeigt das Skript
Eligibility, aktuellen Kurs und die von eToro dynamisch gelieferten What-if-Kosten.
Eine Order erfordert die exakte Bestätigung ``TESTORDER ETORO``.
"""
from __future__ import annotations

from types import SimpleNamespace

import config
from broker.etoro import EtoroBroker


def _pick_pct(cfg, minimum_key, maximum_key, preferred):
    try: mn = float(cfg.get(minimum_key) if cfg.get(minimum_key) is not None else 0)
    except Exception: mn = 0.0
    try: mx = float(cfg.get(maximum_key) if cfg.get(maximum_key) is not None else 0)
    except Exception: mx = 0.0
    value = max(float(preferred), mn)
    if mx > 0:
        value = min(value, mx)
    if value <= 0 or (mx > 0 and value < mn - 1e-9):
        raise RuntimeError("Keine gueltige SL/TP-Distanz aus eToro-Eligibility ableitbar")
    return value


def main():
    if not bool(getattr(config, "PAPER_TRADING", True)):
        print("ABBRUCH: eToro-Testorder ist ausschliesslich im PAPER/DEMO-Modus erlaubt.")
        return 2
    if str(getattr(config, "BROKER", "")).lower() != "etoro":
        print("ABBRUCH: Bitte zuerst eToro als aktiven Broker waehlen.")
        return 2

    b = EtoroBroker(paper=True)
    try:
        b.connect()
        print("eToro DEMO verbunden.")
        print(f"Kontowert: {b.kontowert():,.2f} {b.kontowaehrung()} | Cash: {b.verfuegbares_cash():,.2f}")

        symbol = (input("Testsymbol [AAPL]: ").strip().upper() or "AAPL")
        inst = SimpleNamespace(name=symbol, asset_type="stock", currency="USD")
        ok, reason = b.instrument_handelbar(inst)
        if not ok:
            print("Nicht handelbar:", reason)
            return 2
        rate = b.latest_bid_ask(inst) or {}
        ask = float(rate.get("ask") or rate.get("last") or 0)
        if ask <= 0:
            print("Kein belastbarer aktueller Preis. Keine Order gesendet.")
            return 2

        settlement = b._settlement(inst)
        eligibility = b._eligibility_for(inst)
        chosen = None
        for c in eligibility.get("leverageConfigs") or []:
            vals=[]
            for v in c.get("leverageValues") or []:
                try: vals.append(int(v))
                except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            if str(c.get("settlementType") or "").lower() == settlement.lower() and str(c.get("direction") or "").lower() in ("long","buy","") and 1 in vals:
                chosen=c; break
        chosen = chosen or {}
        sl_pct = _pick_pct(chosen, "minStopLossPercentage", "maxStopLossPercentage", 5.0)
        tp_pct = _pick_pct(chosen, "minTakeProfitPercentage", "maxTakeProfitPercentage", 10.0)
        stop = ask * (1 - sl_pct/100.0)
        tp = ask * (1 + tp_pct/100.0)
        qty = max(0.000001, 100.0 / ask)  # ca. 100 USD Demo-Exposition

        costs = b.dynamic_cost_quote(inst, qty, ask)
        print(f"\n{symbol}: Ask ~ {ask:.6f} USD | Testmenge {qty:.8f} | Settlement {settlement}")
        print(f"Stop {stop:.6f} (-{sl_pct:.2f}%) | Take-Profit {tp:.6f} (+{tp_pct:.2f}%)")
        print("Aktuelle eToro What-if-Kosten:")
        total=0.0
        for k,v in sorted((costs.get("components") or {}).items()):
            print(f"  {k:18s}: {float(v):.6f} {costs.get('currency','USD')}")
            total += abs(float(v or 0))
        print(f"  Summe offene Seite: {total:.6f} {costs.get('currency','USD')} | Stand {costs.get('last_updated','?')}")
        print("\nNoch KEINE Order gesendet.")
        if input("Zum Senden der DEMO-Order exakt 'TESTORDER ETORO' eingeben: ").strip() != "TESTORDER ETORO":
            print("Abgebrochen.")
            return 0
        result = b.kaufe_mit_absicherung(inst, qty, ask, stop, tp)
        print("DEMO-Order Ergebnis:", result)
        print("Pruefe die Position anschliessend auch im eToro-Demo-Konto.")
        return 0
    except Exception as exc:
        print("eToro DEMO-Test fehlgeschlagen:", exc)
        return 1
    finally:
        try: b.disconnect()
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)


if __name__ == "__main__":
    raise SystemExit(main())
