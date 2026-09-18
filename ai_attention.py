"""Kostenbewusste KI-Priorisierung eines bereits geprueften Aktienuniversums.

Die Anfrage laeuft ueber den zentralen Luna/Terra-Router. Das Modell darf nur
die Scanreihenfolge aendern; unbekannte Instrumente und Handelssignale werden
technisch verworfen. Ohne KI gilt unveraendert die deterministische Rotation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import config
from ai_router import AIRouter
from instrument_identity import canonical_key

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "ordered_keys": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "reason": {"type": "string"},
    },
    "required": ["ordered_keys", "reason"],
    "additionalProperties": False,
}


@dataclass
class AttentionResult:
    ordered_keys: list[str]
    reason: str = ""
    model: str = ""
    web_searches: int = 0


class AIAttentionPrioritizer:
    def __init__(self, router: AIRouter | None = None):
        self.router = router or AIRouter(config)
        self.enabled = bool(getattr(config, "AI_ATTENTION_ENABLED", False) and self.router.aktiv)
        self.model = self.router.modellname("luna")
        self.last_error = ""

    def budget_status(self) -> dict:
        status = self.router.status().get("budget", {})
        luna = status.get("luna", {})
        terra = status.get("terra", {})
        return {
            "calls": int(luna.get("anfragen", 0) or 0) + int(terra.get("anfragen", 0) or 0),
            "max_calls_per_day": int(luna.get("grenze", 0) or 0)
                                     + int(terra.get("grenze", 0) or 0),
            "input_tokens": int(luna.get("input_tokens", 0) or 0)
                            + int(terra.get("input_tokens", 0) or 0),
            "output_tokens": int(luna.get("output_tokens", 0) or 0)
                             + int(terra.get("output_tokens", 0) or 0),
            "cost_usd": float(status.get("kosten_gesamt", 0.0) or 0.0),
            "router": "Luna/Terra",
        }

    @staticmethod
    def _ai_control_allows() -> bool:
        try:
            from ai_control import read_mode
            return read_mode() != "OFF"
        except Exception:
            logger.debug("AI-Control-Status nicht lesbar", exc_info=True)
            return True

    def prioritize(self, instruments: Iterable, *, max_items: int | None = None) -> AttentionResult:
        self.last_error = ""
        rows, allowed = [], {}
        for inst in instruments:
            try:
                key = canonical_key(inst.name, inst.asset_type)
            except Exception:
                continue
            allowed[key] = inst
            rows.append({
                "key": key,
                "symbol": str(inst.name),
                "asset_type": str(inst.asset_type),
                "sector": str(getattr(inst, "sector", "") or ""),
            })
        if not rows or not self.enabled or not self._ai_control_allows():
            return AttentionResult([])

        maximum = max(1, min(
            int(max_items or getattr(config, "AI_ATTENTION_MAX_PRIORITY", 48)), len(rows)
        ))
        answer = self.router.frage(
            "focus_ranking",
            {"instrumente": rows, "max_items": maximum,
             "symbole": [x["symbol"] for x in rows]},
            SCHEMA,
            anweisung=(
                "Du priorisierst ausschliesslich die uebergebenen, bereits von eToro "
                "qualifizierten Instrumente. Gib hoechstens max_items vorhandene keys "
                "zurueck. Keine Kauf-/Verkaufssignale, keine Freigabe, keine neuen Ticker."
            ),
        )
        if not answer.ok:
            self.last_error = answer.grund
            logger.warning(
                "KI-Aufmerksamkeitspriorisierung ausgefallen; deterministische Rotation bleibt aktiv: %s",
                answer.grund,
            )
            return AttentionResult([])

        selected, seen = [], set()
        for raw in answer.daten.get("ordered_keys", []) or []:
            lowered = str(raw).strip().lower()
            match = next((key for key in allowed if key.lower() == lowered), None)
            if match and match not in seen:
                selected.append(match)
                seen.add(match)
            if len(selected) >= maximum:
                break
        return AttentionResult(
            selected, str(answer.daten.get("reason") or "")[:500], answer.modell, 0
        )


__all__ = ["AIAttentionPrioritizer", "AttentionResult", "SCHEMA"]
