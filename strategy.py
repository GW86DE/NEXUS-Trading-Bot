"""
Signal-Logik: technische Regeln + optionaler ML-Filter.

WARUM DIESE UEBERARBEITUNG NOETIG WAR
-------------------------------------
Die erste Fassung verlangte, dass ALLE Bedingungen auf EINEM EINZIGEN
Kursbalken gleichzeitig zutreffen:
  - genau in diesem Balken kreuzt der schnelle ueber den langsamen Schnitt
  - UND RSI unter 70
  - UND ML-Wahrscheinlichkeit >= 0.55

Eine SMA-Kreuzung passiert in zwei Jahren vielleicht 20-30 Mal. Dass
ausgerechnet auf diesem einen Balken auch noch die beiden anderen Filter
passen, ist selten -- Ergebnis: 2 Trades in 2 Jahren. Aus 2 Trades laesst
sich statistisch nichts ableiten.

WAS JETZT ANDERS IST
--------------------
Zwei waehlbare Einstiegs-Modi (config.ENTRY_MODE):

  "crossover" -- wie bisher, aber mit Toleranzfenster: die Kreuzung darf
                 bis zu CROSS_LOOKBACK Balken zurueckliegen. Die Filter
                 muessen also nicht exakt im Kreuzungsbalken passen.

  "trend"     -- (Standard) Es zaehlt der Trend-ZUSTAND, nicht der exakte
                 Kreuzungsmoment: Wenn der Aufwaertstrend intakt ist und
                 der Kurs nach einem Ruecksetzer wieder dreht, wird
                 eingestiegen. Erzeugt deutlich mehr Gelegenheiten.

Ausserdem neu:
- Ausstieg auch bei Trendwechsel, nicht nur bei Stop-Loss/Take-Profit.
  Vorher konnte eine Position im Abwaertstrend "haengen", bis der Stop
  irgendwann griff.
- Der ML-Filter laesst sich per config.USE_ML_FILTER komplett abschalten.
  Sinnvoll, wenn das Modell (wie im Training ausgewiesen) keinen Vorsprung
  gegenueber Raten hat -- dann filtert es nur zufaellig Signale weg.
"""

from dataclasses import dataclass

import config
from indicators import add_all_indicators
from ml_model import predict_up_probability, FEATURE_COLUMNS


@dataclass
class Signal:
    action: str          # "BUY", "SELL", "HOLD"
    reason: str
    ml_probability: float
    price: float
    atr: float = float("nan")   # fuer volatilitaetsabhaengige Stops


def prepare(raw_df):
    """Indikatoren berechnen und unvollstaendige Zeilen entfernen."""
    df = add_all_indicators(
        raw_df,
        sma_fast=config.SMA_FAST,
        sma_slow=config.SMA_SLOW,
        rsi_period=config.RSI_PERIOD,
    )
    needed = ["sma_fast", "sma_slow", "rsi", "atr"] + FEATURE_COLUMNS
    return df.dropna(subset=needed)


def entry_conditions(df, i, ml_prob):
    """
    Prueft, ob an Position i ein Einstiegssignal vorliegt.
    Gibt (True/False, Begruendung) zurueck.
    """
    curr = df.iloc[i]

    uptrend = curr["sma_fast"] > curr["sma_slow"]
    if not uptrend:
        return False, "kein Aufwaertstrend"

    if curr["rsi"] >= config.RSI_OVERBOUGHT:
        return False, f"ueberkauft (RSI {curr['rsi']:.0f})"

    if config.USE_ML_FILTER and ml_prob < config.ML_CONFIRM_THRESHOLD:
        return False, f"ML-Filter blockt (P={ml_prob:.2f})"

    if config.ENTRY_MODE == "crossover":
        # Kreuzung darf bis zu CROSS_LOOKBACK Balken zurueckliegen
        start = max(1, i - config.CROSS_LOOKBACK)
        for j in range(start, i + 1):
            prev_j, curr_j = df.iloc[j - 1], df.iloc[j]
            if prev_j["sma_fast"] <= prev_j["sma_slow"] and curr_j["sma_fast"] > curr_j["sma_slow"]:
                return True, f"SMA-Kreuzung vor {i - j} Balken, RSI {curr['rsi']:.0f}"
        return False, "keine frische Kreuzung"

    # ENTRY_MODE == "trend": Ruecksetzer im intakten Aufwaertstrend
    prev = df.iloc[i - 1]
    rsi_turning_up = prev["rsi"] < curr["rsi"]
    was_pulled_back = prev["rsi"] < config.RSI_PULLBACK_LEVEL

    if was_pulled_back and rsi_turning_up:
        return True, f"Ruecksetzer im Aufwaertstrend, RSI dreht ({prev['rsi']:.0f}->{curr['rsi']:.0f})"

    return False, "kein Einstiegs-Trigger"


def exit_conditions(df, i):
    """Prueft, ob eine offene Long-Position geschlossen werden soll."""
    curr = df.iloc[i]
    prev = df.iloc[i - 1]

    # Trendwechsel nach unten
    if prev["sma_fast"] >= prev["sma_slow"] and curr["sma_fast"] < curr["sma_slow"]:
        return True, "Trendwechsel (SMA-Kreuzung abwaerts)"

    # Stark ueberkauft -> Gewinnmitnahme
    if curr["rsi"] > config.RSI_EXIT_OVERBOUGHT:
        return True, f"stark ueberkauft (RSI {curr['rsi']:.0f})"

    return False, ""


def generate_signal(raw_df, model) -> Signal:
    """
    Fuer den Live-Betrieb: erzeugt ein Signal fuer den JUENGSTEN Balken.
    """
    df = prepare(raw_df)

    if len(df) < config.SMA_SLOW + 5:
        return Signal("HOLD", "Nicht genug Daten fuer ein Signal.", 0.5, float("nan"))

    i = len(df) - 1
    curr = df.iloc[i]
    price = float(curr["close"])
    atr_value = float(curr.get("atr", float("nan")))
    ml_prob = predict_up_probability(model, curr) if config.USE_ML_FILTER else 0.5

    should_exit, exit_reason = exit_conditions(df, i)
    if should_exit:
        return Signal("SELL", exit_reason, ml_prob, price, atr_value)

    should_enter, enter_reason = entry_conditions(df, i, ml_prob)
    if should_enter:
        return Signal("BUY", enter_reason, ml_prob, price, atr_value)

    return Signal("HOLD", enter_reason, ml_prob, price, atr_value)
