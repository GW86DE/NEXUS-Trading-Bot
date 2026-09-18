"""Konservative Schaetzung einer plausiblen Kursbewegung fuer die Netto-Edge.

WICHTIG: Das ist keine Renditeprognose und kein Versprechen. Die Funktion
verhindert gerade, dass das Take-Profit-Ziel faelschlich als 'erwartete'
Bewegung behandelt wird. Sie kombiniert stattdessen:
- aktuelle Volatilitaet (ATR / Preis)
- technischen Zielabstand als harte Obergrenze
- Event-/Earnings-Staerke
- optionale ML-Wahrscheinlichkeit
- Marktregime

Die Schaetzung wird ausschliesslich als Mindesthuerde gegen Handelskosten
verwendet. Backtest/Walk-Forward entscheiden weiterhin, ob die Regeln einen
belastbaren Vorteil haben.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import config


def _finite(value, default=0.0):
    try:
        v=float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default


@dataclass
class MoveEstimate:
    gross_move_pct: float
    target_distance_pct: float
    atr_pct: float
    technical_component_pct: float
    event_component_pct: float
    ml_component_pct: float
    regime_factor: float
    confidence: str
    reason: str


def estimate_plausible_move(price, take_profit, atr_value=None, event=None,
                            ml_probability=0.5, regime=None, underdog=False):
    """Liefert eine konservative plausible Bruttobewegung als Dezimalzahl.

    Der Take-Profit-Abstand ist NUR die Obergrenze. Dadurch kann ein weit
    gesetztes Ziel nicht mehr automatisch einen wirtschaftlich sinnvollen
    Trade vortaeuschen.
    """
    price=max(_finite(price),1e-12)
    target=max(0.0,(_finite(take_profit)-price)/price)
    atr_pct=max(0.0,_finite(atr_value)/price)

    # Ohne ATR wird nur ein Teil des Zielabstands als plausibel angesetzt.
    fallback_fraction=float(getattr(config,'EDGE_TARGET_REALIZATION_FALLBACK',0.40))
    technical=min(target, atr_pct*float(getattr(config,'EDGE_ATR_MULTIPLIER',1.35))) if atr_pct>0 else target*fallback_fraction

    event_score=int(getattr(event,'score',0) or 0) if event is not None else 0
    event_buy=bool(getattr(event,'event_buy',False)) if event is not None else False
    earnings_score=int(getattr(event,'earnings_score',0) or 0) if event is not None else 0
    # Event-Bonus bleibt volatilitaetsgebunden; Schlagzeilen allein erzeugen
    # keine beliebig grosse erwartete Bewegung.
    event_strength=max(0.0,(event_score-60)/40.0)
    earnings_strength=max(0.0,(earnings_score-60)/40.0)
    event_component=0.0
    if event_buy or event_score>=int(getattr(config,'EVENT_BUY_SCORE',78)):
        base_vol=atr_pct if atr_pct>0 else max(target*0.20,0.002)
        event_component=base_vol*(0.75*event_strength+0.35*earnings_strength)

    ml=float(_finite(ml_probability,0.5))
    ml_component=0.0
    if bool(getattr(config,'USE_ML_FILTER',False)) and ml>0.5:
        base_vol=atr_pct if atr_pct>0 else max(target*0.15,0.001)
        ml_component=base_vol*min(1.0,(ml-0.5)/0.20)*float(getattr(config,'EDGE_ML_MAX_ATR_BONUS',0.35))

    regime_name=str(getattr(regime,'name','NEUTRAL') or 'NEUTRAL').upper() if regime is not None else 'NEUTRAL'
    if regime_name=='RISK_OFF':
        regime_factor=float(getattr(config,'EDGE_RISK_OFF_FACTOR',0.75))
    elif regime_name=='RISK_ON':
        regime_factor=float(getattr(config,'EDGE_RISK_ON_FACTOR',1.05))
    else:
        regime_factor=1.0

    if underdog:
        regime_factor*=float(getattr(config,'EDGE_UNDERDOG_CONFIDENCE_FACTOR',0.90))

    plausible=max(0.0,(technical+event_component+ml_component)*regime_factor)
    plausible=min(target,plausible) if target>0 else plausible

    # Absolute Plausibilitaetsgrenze verhindert, dass Event-Heuristiken bei
    # extremen ATR-Werten absurde Prozentwerte erzeugen.
    plausible=min(plausible,float(getattr(config,'EDGE_MAX_PLAUSIBLE_MOVE_PCT',0.12)))

    confidence='HOCH' if event_buy and atr_pct>0 else 'MITTEL' if atr_pct>0 else 'NIEDRIG'
    reason=(f"ATR {atr_pct*100:.2f}% | technisch {technical*100:.2f}% | "
            f"Event {event_component*100:.2f}% | ML {ml_component*100:.2f}% | "
            f"Regime {regime_name} x{regime_factor:.2f} | Ziel-Cap {target*100:.2f}%")
    return MoveEstimate(plausible,target,atr_pct,technical,event_component,ml_component,regime_factor,confidence,reason)
