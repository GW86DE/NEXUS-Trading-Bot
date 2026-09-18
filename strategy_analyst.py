"""Offline-first Strategieanalyst fuer die eigene Decision-History.

Python/SQLite rechnet. Die KI interpretiert nur bereits aggregierte Zahlen,
verwendet KEINE Websuche und kann keinerlei Tradingparameter schreiben.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import config
from ai_router import AIRouter
from decision_analytics import strategy_snapshot, trade_snapshot
from safe_persistence import atomic_write_text

logger=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parent
SCHEMA={
    "type":"object",
    "properties":{
        "assessment":{"type":"string"},
        "findings":{"type":"array","maxItems":8,"items":{"type":"object","properties":{
            "title":{"type":"string"},"evidence":{"type":"string"},"interpretation":{"type":"string"},"sample_quality":{"type":"string"}},
            "required":["title","evidence","interpretation","sample_quality"],"additionalProperties":False}},
        "limitations":{"type":"array","items":{"type":"string"},"maxItems":8},
        "next_questions":{"type":"array","items":{"type":"string"},"maxItems":8},
    },
    "required":["assessment","findings","limitations","next_questions"],"additionalProperties":False,
}


def _handelsblock(trades: dict) -> str:
    """Die realisierten Kennzahlen als lesbarer Block.

    Bewusst mit Stichprobengroesse in jeder Zeile: eine Trefferquote aus
    drei Trades sieht sonst aus wie ein Ergebnis.
    """
    if not trades or trades.get("fehler"):
        return f"nicht verfuegbar ({trades.get('fehler','unbekannt') if trades else 'unbekannt'})"
    g=trades.get("gesamt",{}) or {}
    if not int(g.get("trades",0) or 0):
        return ("Noch keine geschlossenen Trades aufgezeichnet. Das Trade-Ledger laeuft "
                "seit v8.1.3 mit; belastbare Kennzahlen brauchen einige Wochen Handel.")
    zeilen=[
        f"Trades {g.get('trades')} (bewertbar {g.get('bewertbar')}, "
        f"ohne bekanntes Ergebnis {g.get('ohne_ergebnis')}) [{g.get('sample_quality')}]",
        f"Trefferquote {g.get('trefferquote_pct')} % · Erwartungswert {g.get('erwartungswert')} "
        f"je Trade · Profitfaktor {g.get('profitfaktor')}",
        f"Summe netto {g.get('summe_netto')} · max. Drawdown {g.get('max_drawdown')} · "
        f"Gebuehren {g.get('gebuehren_summe')} ({g.get('gebuehren_quote_pct')} % vom Brutto)",
        f"Slippage im Mittel {g.get('slippage_mittel_pct')} % · Haltedauer "
        f"{g.get('haltedauer_minuten_mittel')} min · offene Positionen "
        f"{trades.get('offene_positionen')}",
        f"Aktuelle Strategieversion: {trades.get('aktuelle_strategieversion') or 'unbekannt'}",
    ]
    for titel,schluessel in (("je Broker","je_broker"),("je Exit-Grund","je_exit_grund"),
                             ("je Marktphase","je_marktphase"),
                             ("je Strategieversion","je_strategieversion")):
        eintraege=[x for x in (trades.get(schluessel) or []) if int(x.get("bewertbar",0) or 0)]
        if not eintraege:
            continue
        zeilen.append(f"  {titel}:")
        for x in eintraege[:12]:
            zeilen.append(
                f"    {str(x['wert'])[:24]:24s} n={x['bewertbar']:4d} "
                f"treffer={x.get('trefferquote_pct')}% ew={x.get('erwartungswert')} "
                f"[{x.get('sample_quality')}]")
    zeilen.append(f"  Methodik: {trades.get('methodenhinweis','')}")
    return "\n".join(zeilen)


def _report_dir() -> Path:
    root=Path(os.getenv("TRADINGBOT_TEST_STATE_DIR","").strip() or ROOT)
    p=root/"strategy_reports"; p.mkdir(parents=True,exist_ok=True); return p


class StrategyAnalyst:
    def __init__(self, router: AIRouter | None = None):
        self.router = router or AIRouter(config)
        self.enabled=bool(getattr(config,"STRATEGY_ANALYST_ENABLED",True))
        self.ai_enabled=bool(self.enabled and getattr(config,"STRATEGY_ANALYST_USE_AI",True)
                             and self.router.aktiv)
        self.model=""
        self.last_error=""

    def run(self) -> tuple[Path,dict]:
        lookback=int(getattr(config,"STRATEGY_ANALYST_LOOKBACK_DAYS",90))
        stats=strategy_snapshot(lookback)
        # v8.1.3 (Etappe A): dazu die REALISIERTEN Handelsergebnisse. Bis
        # hierher kannte der Bericht nur Forward-Performance -- also "haette
        # sich der Einstieg gelohnt", nicht "was ist tatsaechlich passiert".
        try:
            trades=trade_snapshot(tage=lookback)
        except Exception as exc:
            logger.warning("Trade-Kennzahlen nicht verfuegbar: %s",exc)
            trades={"fehler":str(exc)}
        interpretation=None
        if self.ai_enabled:
            try:
                answer = self.router.frage(
                    "strategie_auswertung",
                    {"statistik": stats, "handelsergebnisse": trades}, SCHEMA,
                    anweisung=(
                        "Analysiere ausschliesslich die vorab berechneten TradingBot-Statistiken. "
                        "Keine Websuche, keine Orders und keine automatische Parameter- oder "
                        "Filteraenderung. Trenne Korrelation von Kausalitaet. Nenne immer "
                        "Stichprobengroessen und respektiere sample_quality. Ein primaerer "
                        "Ablehnungsgrund ist nicht automatisch kausal, weil nachfolgende Filter "
                        "nach einem Block nicht mehr liefen. Die Handelsergebnisse enthalten "
                        "nur geschlossene Trades; Eintraege unter 'ohne_ergebnis' haben keinen "
                        "bekannten Einstand und duerfen nicht mitgerechnet werden. Schlage nichts "
                        "vor, was Sicherheitsfilter, Universum oder Risikogrenzen beruehrt."
                    ),
                )
                if not answer.ok:
                    raise RuntimeError(answer.grund)
                self.model = answer.modell
                interpretation=answer.daten
            except Exception as exc:
                self.last_error=str(exc); logger.warning("Strategy-Analyst KI-Auslegung fehlgeschlagen; deterministischer Bericht bleibt verfuegbar: %s",exc)

        stamp=datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        p=_report_dir()/f"strategy_report_{stamp}.txt"
        lines=[
            "TRADINGBOT STRATEGY ANALYST",
            "="*72,
            f"Erstellt: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"Lookback: {stats.get('lookback_days')} Tage · Entscheidungen: {stats.get('decision_count')}",
            "",
            "WICHTIG: Dieser Bericht darf keinerlei Tradingparameter automatisch aendern.",
            "Primaere Ablehnungsgruende sind deskriptiv, nicht kausal; downstream Filter wurden nach einem Block nicht weiter ausgefuehrt.",
            "",
            "REALISIERTE HANDELSERGEBNISSE (geschlossene Trades)",
            _handelsblock(trades),
            "",
            "STATUSCOUNTS",
            json.dumps(stats.get("status_counts",{}),ensure_ascii=False,indent=2),
            "",
            "PRIMAERE ABLEHNUNGSGRUENDE",
            json.dumps(stats.get("primary_rejection_counts",{}),ensure_ascii=False,indent=2),
            "",
            "FORWARD-PERFORMANCE",
        ]
        for row in stats.get("forward_performance",[]) or []:
            lines.append(
                f"{row['group']:24s} {row['horizon']:3s} n={row['n']:4d} "
                f"mean={row['mean_return_pct']:+.3f}% median={row['median_return_pct']:+.3f}% "
                f"pos={row['positive_rate_pct']:.1f}% missed>=1%={row['missed_winner_rate_pct']:.1f}% "
                f"avoided<=-1%={row['avoided_loser_rate_pct']:.1f}% [{row['sample_quality']}]"
            )
        lines += ["", "SEGMENTE – ABLEHNUNGSANZAHL", json.dumps({
            "sector":stats.get("rejections_by_sector",{}),"regime":stats.get("rejections_by_regime",{}),
            "local_hour":stats.get("rejections_by_local_hour",{})},ensure_ascii=False,indent=2),
            "", "SEGMENTE – FORWARD-PERFORMANCE"]
        for row in stats.get("segment_forward_performance",[]) or []:
            if int(row.get("n",0) or 0) < 5:
                continue
            lines.append(
                f"{row['dimension']:10s} {str(row['value'])[:20]:20s} {row['horizon']:3s} n={row['n']:4d} "
                f"median={row['median_return_pct']:+.3f}% pos={row['positive_rate_pct']:.1f}% "
                f"missed>=1%={row['missed_winner_rate_pct']:.1f}% [{row['sample_quality']}]"
            )
        if interpretation:
            lines += ["", "KI-AUSLEGUNG (nur Interpretation, kein Steuerrecht)", "-"*72, interpretation.get("assessment","")]
            for f in interpretation.get("findings",[]) or []:
                lines += ["",f"• {f.get('title','')}",f"  Evidenz: {f.get('evidence','')}",f"  Einordnung: {f.get('interpretation','')}",f"  Datenlage: {f.get('sample_quality','')}"]
            if interpretation.get("limitations"):
                lines += ["", "Grenzen:"]+[f"- {x}" for x in interpretation.get("limitations",[])]
            if interpretation.get("next_questions"):
                lines += ["", "Naechste Fragen:"]+[f"- {x}" for x in interpretation.get("next_questions",[])]
        elif self.last_error:
            lines += ["",f"KI-Auslegung nicht verfuegbar: {self.last_error}","Der deterministische Statistikteil ist davon nicht betroffen."]
        atomic_write_text(p,"\n".join(lines)+"\n")
        return p,{"stats":stats,"interpretation":interpretation,"ai_error":self.last_error}
