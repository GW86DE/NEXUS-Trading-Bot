"""Instrument-Risikoklassifizierung fuer TradingBot v5.8.

Die kuratierten Underdogs und die nicht kuratierten Broad-Aktien sind bewusst
getrennte Kategorien. Beide erhalten konservativere Markt-/Kostenannahmen,
aber nur die kuratierten Underdogs laufen durch das bestehende fundamentale
Underdog-Screening. So werden Broad-Werte nicht versehentlich als geprueft
behandelt und zugleich nicht mit liquiden Standardaktien gleichgesetzt.
"""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class InstrumentRiskClass:
    curated_underdog: bool
    broad: bool

    @property
    def conservative(self) -> bool:
        """Strengere News-/Edge-/Slippage-Annahmen verwenden."""
        return bool(self.curated_underdog or self.broad)


def classify_instrument(inst) -> InstrumentRiskClass:
    """Klassifiziert ein Instrument ohne Netz-/Brokerzugriff.

    ``broad`` wird primaer aus dem Instrument-Metadatum gelesen. Der
    UNCLASSIFIED-Fallback schuetzt alte/migrierte Instrumentobjekte, bei denen
    das ``broad``-Attribut fehlen koennte.
    """
    asset_type = str(getattr(inst, "asset_type", "") or "").lower()
    curated = bool(getattr(inst, "underdog", False)) if asset_type == "stock" else False
    broad = False
    if asset_type == "stock":
        broad = bool(getattr(inst, "broad", False))
        if not broad:
            group = str(getattr(inst, "gruppe", "") or "").strip().lower()
            broad = group == "broad"
        if not broad:
            sector = str(getattr(inst, "sector", "") or "").strip().upper()
            broad = sector == "UNCLASSIFIED"
    return InstrumentRiskClass(curated_underdog=curated, broad=broad)


def broad_liquidity_check(df) -> tuple[bool, float, float]:
    """Prueft die bereits geladene Historie eines Broad-Wertes.

    Rueckgabe: ``(allowed, average_dollar_volume_per_bar, minimum)``.
    Bei fehlenden/ungueltigen Daten gilt fail-closed.
    """
    minimum = float(getattr(config, "BROAD_MIN_AVG_DOLLAR_VOLUME", 2_000_000.0))
    try:
        dv = (df["close"].astype(float) * df["volume"].astype(float)).tail(20).dropna()
        average = float(dv.mean()) if len(dv) else 0.0
    except Exception:
        average = 0.0
    return bool(average >= minimum), average, minimum
