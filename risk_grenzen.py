"""Grenzwerte eines Risikotopfes, rein aus der Konfiguration (10.8.0, Schritt 2).

``TopfGrenzen`` lag bis 10.7.1 in ``risk_pots``. ``risk_levels`` (Einsatzstufen)
las die Basiswerte von dort, und ``risk_pots`` las die Stufe aus ``risk_levels``:
Import-Zyklus. Die Grenzwerte haengen nur an ``config``; hier unten koennen beide
sie lesen. ``risk_pots`` exportiert ``TopfGrenzen`` weiter (dieselbe Klasse), damit
Aufrufer und Tests unveraendert bleiben.
"""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass
class TopfGrenzen:
    """Alle Grenzwerte eines einzelnen Risikotopfes."""
    risiko_pro_trade_pct: float = 0.005
    max_position_pct: float = 0.04
    max_offene_positionen: int = 8
    max_tagesverlust_pct: float = 0.02
    max_trades_pro_tag: int = 20
    mengen_schritt: float = 0.0
    min_positionswert: float = 0.0

    @classmethod
    def fuer_broker(cls, broker: str, cfg=None) -> "TopfGrenzen":
        """Liest die Grenzwerte des Topfes aus der Konfiguration.

        Namensschema: <BROKER>_MAX_POSITION_PCT usw. Fehlt ein Wert, gilt
        der globale Standard aus v6. So bleibt eine alte Konfiguration
        gueltig und der Nutzer muss nichts neu eintragen.
        """
        cfg = cfg or config
        prefix = str(broker or "").strip().upper()

        def hole(name: str, standard):
            spezifisch = getattr(cfg, f"{prefix}_{name}", None)
            if spezifisch is not None:
                return spezifisch
            return getattr(cfg, name, standard)

        return cls(
            risiko_pro_trade_pct=float(hole("RISK_PER_TRADE_PCT", 0.005)),
            max_position_pct=float(hole("MAX_POSITION_PCT", 0.04)),
            max_offene_positionen=int(hole("MAX_OPEN_POSITIONS", 8)),
            max_tagesverlust_pct=float(hole("MAX_DAILY_LOSS_PCT", 0.02)),
            max_trades_pro_tag=int(hole("MAX_TRADES_PER_DAY", 20)),
            mengen_schritt=float(getattr(cfg, f"{prefix}_QUANTITY_STEP", 0.0) or 0.0),
            min_positionswert=float(getattr(cfg, f"{prefix}_MIN_POSITION_VALUE", 0.0) or 0.0),
        )
