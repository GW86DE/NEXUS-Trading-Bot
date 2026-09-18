"""Status der deterministischen Event-/Kosten-/Research-Schichten."""
from __future__ import annotations
from types import SimpleNamespace
import config
from market_regime import MarketRegimeClient
from earnings_engine import EarningsClient
from cost_engine import estimate_roundtrip
from edge_estimator import estimate_plausible_move
from news_sources import configured_sources



def main():
    print("="*74); print("EVENT / COST / RESEARCH STATUS"); print("="*74)
    r=MarketRegimeClient().get(); print(f"Marktregime: {r.name} | Score {r.score} | VIX {r.vix:.1f} | {r.details}")
    e=EarningsClient(); print("Earnings-Daten:","SEC kostenlos"+(" + Alpha Vantage optional" if e.key else "") if e.enabled() else "nicht eingerichtet")
    sources=configured_sources(); active=[k for k,v in sources.items() if v]
    print(f"Newsquellen: {len(active)}/{len(sources)} konfiguriert -> "+(", ".join(active) if active else "keine"))
    print("Positive Event-Trades: min.",getattr(config,"NEWS_MIN_DIVERSE_SOURCES_FOR_EVENT",2),"unabhaengige Belege inkl. Earnings")
    print("Broker: ETORO (einziger unterstuetzter Broker)")
    b=estimate_roundtrip(50,100,"stock","USD",broker="etoro")
    print("\neToro Offline-Fallback-Beispiel Roundtrip 5.000 USD US-Aktie:"); print(b.as_text("USD"))
    print("Live-Neukaeufe verlangen zusaetzlich aktuelle eToro What-if-Kosten; Ausfall => Blockade.")
    print("\nSchutz:")
    print("Max Tagesverlust:",config.MAX_DAILY_LOSS_PCT*100,"%")
    print("Equity-Neukaufsperre:",getattr(config,"MAX_UNREALIZED_DAILY_LOSS_PCT",.03)*100,"%")
    print("Sektorlimit:",getattr(config,"MAX_SECTOR_POSITIONS",3),"Positionen /",getattr(config,"MAX_SECTOR_EXPOSURE_PCT",.25)*100,"%")
    print("Max. Trades/Tag:",getattr(config,"MAX_TRADES_PER_DAY",20))
    _demo=estimate_plausible_move(100,115,atr_value=1.2,event=SimpleNamespace(score=82,event_buy=True,earnings_score=85),ml_probability=.6,regime=SimpleNamespace(name="NEUTRAL"))
    print(f"Plausible-Edge-Demo: Ziel 15,00 % -> Modell {_demo.gross_move_pct*100:.2f} % | {_demo.reason}")
    print("KI-Aufmerksamkeit ist nicht Teil dieser Kauf-/Verkaufsentscheidungskette.")



if __name__ == "__main__":
    main()
