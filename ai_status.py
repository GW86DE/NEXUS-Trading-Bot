"""Status des strikt nicht-handelnden Luna/Terra-Routers."""
from __future__ import annotations
import json

import config
from ai_control import read_mode
from ai_router import AIRouter


def main():
    router=AIRouter()
    status=router.status()
    print("="*76)
    print("TRADINGBOT 8.1.1 NEXUS – KI-ROUTER")
    print("="*76)
    print("Globale Steuerung    :", read_mode())
    print("API-Key vorhanden    :", bool(getattr(config,"OPENAI_API_KEY","")))
    print("Router aktiviert     :",status["aktiv"])
    print("Handelsrechte        : KEINE")
    print()
    print("1) Luna – haeufig und guenstig")
    print("   Modell             :",status["luna_modell"])
    print("   Aufgaben           : Fokus-Ranking, Batch-Einstufung, News-Relevanz, Duplikate")
    print("   Tageslimit         :",getattr(config,"AI_LUNA_MAX_CALLS_PER_DAY",40),"Aufrufe")
    print()
    print("2) Terra – selten und anspruchsvoll")
    print("   Modell             :",status["terra_modell"])
    print("   Aufgaben           : Anomalien, Widersprueche, Krypto-Events, Research, Strategie")
    print("   Tageslimit         :",getattr(config,"AI_TERRA_MAX_CALLS_PER_DAY",8),"Aufrufe")
    print()
    print("3) Woechentliches Aktien-Research")
    print("   Aktiviert          :", bool(getattr(config,"AI_RESEARCH_ENABLED",False)))
    print("   Rolle              : nur Vorschlaege; zweistufige Human-Freigabe")
    print("   Termin             : Wochentag",getattr(config,"AI_RESEARCH_WEEKDAY",6),"um",getattr(config,"AI_RESEARCH_HOUR_LOCAL",11),"Uhr lokal")
    print("   Vorschlaege        :",getattr(config,"AI_RESEARCH_MIN_PROPOSALS",5),"bis",getattr(config,"AI_RESEARCH_MAX_PROPOSALS",10))
    print()
    print("4) Strategy Analyst")
    print("   Aktiviert          :", bool(getattr(config,"STRATEGY_ANALYST_ENABLED",True)))
    print("   KI-Auslegung       :", bool(getattr(config,"STRATEGY_ANALYST_USE_AI",True)))
    print("   Websuche           : NEIN")
    print("   Rolle              : lokale Statistik + optionale Interpretation; keine Parameteraenderung")
    print("   Termin             : Wochentag",getattr(config,"STRATEGY_ANALYST_WEEKDAY",6),"um",getattr(config,"STRATEGY_ANALYST_HOUR_LOCAL",18),"Uhr lokal")
    print("\nBudget/Audit         :",json.dumps(status["budget"],ensure_ascii=False))
    if status["letzter_fehler"]:
        print("Letzter Router-Fehler:",status["letzter_fehler"])
    if status["degraded"]:
        print("Degraded             :",status["degraded_grund"])
    print("\n/ai off sperrt alle OpenAI-Aufrufe; deterministische Funktionen bleiben aktiv.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
