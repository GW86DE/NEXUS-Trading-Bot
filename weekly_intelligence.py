"""Woechentliche, strikt nicht-handelnde Intelligence-Jobs."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import config
from ai_control import read_mode
from notifier import send_document, send_telegram, send_telegram_buttons
from safe_persistence import atomic_write_json
from strategy_analyst import StrategyAnalyst
from universe_research import UniverseResearchAssistant, proposal_keyboard, proposal_message

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parent


def _root(): return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR","").strip() or ROOT)
def _path(): return _root()/"weekly_intelligence_state.json"
def _load():
    try:
        if _path().exists(): return json.loads(_path().read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Weekly-Intelligence-State konnte nicht gelesen werden: %s",exc)
    return {"version":1}
def _save(d): atomic_write_json(_path(),d)

def _target(now: datetime, weekday: int, hour: int) -> datetime:
    """Letzter faelliger Wochentermin; verpasste Sonntage werden nachgeholt."""
    wd=max(0,min(6,int(weekday))); h=max(0,min(23,int(hour)))
    days_since=(now.weekday()-wd)%7
    target=(now-timedelta(days=days_since)).replace(hour=h,minute=0,second=0,microsecond=0)
    if target>now: target-=timedelta(days=7)
    return target


def _due(state: dict,key: str,target: datetime,now: datetime) -> bool:
    try:
        last=datetime.fromisoformat(str(state.get(f"{key}_target") or ""))
        if last.tzinfo is None: last=last.replace(tzinfo=now.tzinfo)
        if last>=target: return False
    except Exception as exc:
        logger.debug("Weekly target status nicht lesbar: %s",exc)
    try:
        attempt=datetime.fromisoformat(str(state.get(f"{key}_attempt") or ""))
        if attempt.tzinfo is None: attempt=attempt.replace(tzinfo=now.tzinfo)
        retry=float(getattr(config,"WEEKLY_INTELLIGENCE_RETRY_HOURS",6))*3600.0
        if (now-attempt).total_seconds()<retry: return False
    except Exception as exc:
        logger.debug("Weekly attempt status nicht lesbar: %s",exc)
    return True


class WeeklyIntelligence:
    def __init__(self): self.last_error=""

    def check_once(self) -> dict:
        now=datetime.now().astimezone(); state=_load(); result={"research":None,"strategy":None}
        mode=read_mode()
        research_target=_target(now,getattr(config,"AI_RESEARCH_WEEKDAY",6),getattr(config,"AI_RESEARCH_HOUR_LOCAL",11))
        from market_calendar import darf_arbeiten as aktienmarkt_darf_arbeiten
        stock_window, stock_window_reason = aktienmarkt_darf_arbeiten()
        if (bool(getattr(config,"AI_RESEARCH_ENABLED",True)) and mode!="OFF"
                and stock_window and _due(state,"research",research_target,now)):
            state["research_attempt"]=now.isoformat(); _save(state)
            assistant=UniverseResearchAssistant()
            proposals=assistant.generate(list(getattr(config,"STOCK_SYMBOLS",[]) or []))
            if proposals:
                for p in proposals:
                    send_telegram_buttons(proposal_message(p),proposal_keyboard(p.get("id","")),priority="normal")
                send_telegram(f"🧠 WÖCHENTLICHES RESEARCH\n{len(proposals)} neue Vorschläge wurden erstellt. Es wurde NICHTS automatisch ins Universum aufgenommen.\nMit /vorschlaege siehst du den Status.",priority="normal")
                state["research_target"]=research_target.isoformat(); state["research_last_ok"]=now.isoformat(); state["research_last_error"]=""; _save(state)
                result["research"]={"proposals":len(proposals)}
            else:
                state["research_last_error"]=assistant.last_error or "keine gueltigen Vorschlaege"; _save(state)
                result["research"]={"error":state["research_last_error"]}
        elif (bool(getattr(config,"AI_RESEARCH_ENABLED",True)) and mode!="OFF"
              and _due(state,"research",research_target,now) and not stock_window):
            result["research"]={"deferred": True, "reason": stock_window_reason}

        strategy_target=_target(now,getattr(config,"STRATEGY_ANALYST_WEEKDAY",6),getattr(config,"STRATEGY_ANALYST_HOUR_LOCAL",18))
        if bool(getattr(config,"STRATEGY_ANALYST_ENABLED",True)) and _due(state,"strategy",strategy_target,now):
            state["strategy_attempt"]=now.isoformat(); _save(state)
            analyst=StrategyAnalyst()
            # /ai off deaktiviert auch die KI-Auslegung, nicht aber die rein
            # deterministische Statistik. So bleibt der Datensatz lesbar, ohne
            # einen OpenAI-Aufruf zu machen.
            if mode=="OFF": analyst.ai_enabled=False
            try:
                report,meta=analyst.run()
                sent=send_document(report,caption="TradingBot Strategy Analyst · nur Auswertung, keine automatische Regeländerung")
                stats=meta.get("stats",{})
                send_telegram(
                    f"📊 STRATEGY ANALYST\n{stats.get('decision_count',0)} Entscheidungen aus {stats.get('lookback_days',0)} Tagen ausgewertet. "
                    f"KI-Auslegung: {'ja' if meta.get('interpretation') else 'nein'}.\nDer Bericht ändert keine Filter und keine Handelsparameter." +
                    ("\nDokumentversand war nicht möglich; Datei bleibt lokal gespeichert." if not sent else ""),
                    priority="normal",
                )
                state["strategy_target"]=strategy_target.isoformat(); state["strategy_last_ok"]=now.isoformat(); state["strategy_last_report"]=str(report); state["strategy_last_error"]=""; _save(state)
                result["strategy"]={"report":str(report),"ai":bool(meta.get("interpretation"))}
            except Exception as exc:
                logger.exception("Strategy Analyst fehlgeschlagen: %s",exc)
                state["strategy_last_error"]=str(exc); _save(state); result["strategy"]={"error":str(exc)}
        return result
