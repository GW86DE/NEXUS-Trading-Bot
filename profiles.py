"""
Risiko-Profile.

WAS EIN PROFIL AENDERT -- UND WAS NICHT
========================================
Ein Profil aendert AUSSCHLIESSLICH, wie viel Kapital pro Signal eingesetzt
wird und wie schnell ausgestiegen wird. Es aendert NICHT, wie gut die
Signale sind.

Das ist der entscheidende Punkt, den Werbung fuer "offensive Strategien"
gerne verschweigt: Positionsgroesse ist ein VERSTAERKER, kein Ertragsmotor.

  - Hat eine Strategie einen echten Vorteil, macht mehr Einsatz aus
    kleinem Gewinn einen groesseren.
  - Hat sie keinen, macht mehr Einsatz aus kleinem Verlust einen
    groesseren -- und zwar schneller.

Der Erwartungswert pro eingesetztem Euro bleibt in beiden Faellen gleich.
Mehr Risiko erhoeht die Schwankungsbreite in BEIDE Richtungen, nicht die
Gewinnwahrscheinlichkeit.

STAND DIESER STRATEGIE (aus den Walk-Forward-Tests)
---------------------------------------------------
Die Strategie schlug simples Kaufen-und-Halten in der Mehrheit der
getesteten Zeitabschnitte NICHT. Solange sich das nicht aendert, ist
"OFFENSIV" nicht der schnellere Weg zum Gewinn, sondern der schnellere
Weg zum Verlust. Bitte die Profile im Paper-Modus vergleichen, bevor
irgendwo echtes Geld im Spiel ist.

MATHEMATISCHER NEBENEFFEKT: VERLUSTE WIEGEN SCHWERER ALS GEWINNE
-----------------------------------------------------------------
  -10 % Verlust  ->  +11,1 % noetig, um wieder bei null zu sein
  -25 % Verlust  ->  +33,3 % noetig
  -50 % Verlust  ->  +100 % noetig
  -75 % Verlust  ->  +300 % noetig

Deshalb ist die Begrenzung des Drawdowns kein Angsthasen-Thema, sondern
mathematisch der wirksamste Hebel fuer langfristiges Wachstum.

KRYPTO WIRD SEPARAT GESTEUERT
------------------------------
Krypto hat eigene, durchgehend niedrigere Werte als Aktien -- aus drei
sachlichen Gruenden:
  1. deutlich hoehere Schwankungsbreite (oft 3-5x die einer Aktie)
  2. Handel rund um die Uhr, auch wenn niemand hinschaut
  3. kein boersenseitiger Stop-Loss moeglich (siehe README) -- der Bot
     muss selbst pruefen, und das geht nur im Takt seiner Zyklen

Zusaetzlich begrenzt MAX_CRYPTO_PORTFOLIO_PCT den GESAMTEN Krypto-Anteil,
nicht nur die Einzelposition. Ohne diese Grenze koennten sich sonst viele
kleine Krypto-Positionen zu einem grossen Gesamtrisiko summieren.
"""

PROFILES = {
    # -----------------------------------------------------------------
    "konservativ": {
        "beschreibung": (
            "Kleine Positionen, enge Verlustgrenzen, wenige gleichzeitige "
            "Trades. Schwankt wenig, waechst langsam. Geeignet, um den "
            "Bot ueber Wochen zu beobachten, ohne dass einzelne Fehltrades "
            "das Bild dominieren."
        ),
        "erwartung": (
            "Kleine Gewinne UND kleine Verluste. Wenn die Strategie nichts "
            "taugt, verlierst du hier langsam genug, um es zu merken."
        ),
        "werte": {
            "RISK_PER_TRADE_PCT": 0.005,      # 0,5 % Risiko je Trade
            "MAX_POSITION_PCT": 0.03,         # max 3 % Kapital je Position
            "MAX_OPEN_POSITIONS": 2,
            "MAX_DAILY_LOSS_PCT": 0.02,       # Handelsstopp ab -2 % am Tag
            "ATR_STOP_MULTIPLIER": 2.5,       # weiterer Stop = weniger Fehlausloesungen
            "ATR_TAKE_MULTIPLIER": 3.5,
            "RSI_OVERBOUGHT": 65,             # vorsichtiger Einstieg
            "RSI_PULLBACK_LEVEL": 40,
            "CRYPTO_RISK_PER_TRADE_PCT": 0.002,   # 0,2 % -- Krypto schwankt staerker
            "CRYPTO_MAX_POSITION_PCT": 0.015,     # max 1,5 % je Coin
            "MAX_CRYPTO_PORTFOLIO_PCT": 0.05,     # max 5 % Krypto insgesamt
        },
    },

    # -----------------------------------------------------------------
    "ausgewogen": {
        "beschreibung": (
            "Die getesteten Standardwerte. Mittlere Positionsgroessen, "
            "uebliche Stop-Abstaende. Das ist die Einstellung, mit der "
            "die Backtests und Walk-Forward-Laeufe durchgefuehrt wurden."
        ),
        "erwartung": (
            "Referenz-Einstellung. Am ehesten vergleichbar mit den "
            "Testergebnissen aus run_walkforward.py."
        ),
        "werte": {
            "RISK_PER_TRADE_PCT": 0.01,       # 1 %
            "MAX_POSITION_PCT": 0.05,         # 5 %
            "MAX_OPEN_POSITIONS": 5,
            "MAX_DAILY_LOSS_PCT": 0.03,       # -3 %
            "ATR_STOP_MULTIPLIER": 2.0,
            "ATR_TAKE_MULTIPLIER": 3.0,
            "RSI_OVERBOUGHT": 70,
            "RSI_PULLBACK_LEVEL": 45,
            "CRYPTO_RISK_PER_TRADE_PCT": 0.003,   # 0,3 %
            "CRYPTO_MAX_POSITION_PCT": 0.03,      # max 3 % je Coin
            "MAX_CRYPTO_PORTFOLIO_PCT": 0.10,     # max 10 % Krypto insgesamt
        },
    },

    # -----------------------------------------------------------------
    "offensiv": {
        "beschreibung": (
            "Groessere Positionen, engere Stops, mehr gleichzeitige Trades. "
            "ACHTUNG: Das erhoeht die Schwankungsbreite in BEIDE Richtungen. "
            "Engere Stops bedeuten ausserdem MEHR Fehlausloesungen -- man "
            "wird oefter vom normalen Marktrauschen ausgestoppt, obwohl die "
            "Richtung stimmte."
        ),
        "erwartung": (
            "Groessere Gewinne UND groessere Verluste, plus mehr Trades und "
            "damit mehr Gebuehren. Kein hoeherer Erwartungswert -- nur mehr "
            "Ausschlag. Bei einer Strategie ohne nachgewiesenen Vorteil "
            "beschleunigt dieses Profil vor allem den Kapitalverzehr."
        ),
        "werte": {
            "RISK_PER_TRADE_PCT": 0.02,       # 2 % Risiko je Trade
            "MAX_POSITION_PCT": 0.15,         # bis 15 % Kapital je Position
            "MAX_OPEN_POSITIONS": 8,
            "MAX_DAILY_LOSS_PCT": 0.06,       # Handelsstopp erst ab -6 %
            "ATR_STOP_MULTIPLIER": 1.5,       # enger -> oefter ausgestoppt
            "ATR_TAKE_MULTIPLIER": 3.0,
            "RSI_OVERBOUGHT": 75,             # steigt auch spaeter noch ein
            "RSI_PULLBACK_LEVEL": 50,
            "CRYPTO_RISK_PER_TRADE_PCT": 0.006,   # 0,6 %
            "CRYPTO_MAX_POSITION_PCT": 0.06,      # max 6 % je Coin
            "MAX_CRYPTO_PORTFOLIO_PCT": 0.20,     # max 20 % Krypto insgesamt
        },
    },
}

DEFAULT_PROFILE = "ausgewogen"


def get_profile(name: str) -> dict:
    key = (name or "").strip().lower()
    if key not in PROFILES:
        raise KeyError(
            f"Unbekanntes Profil {name!r}. Verfuegbar: {', '.join(PROFILES)}"
        )
    return PROFILES[key]


def apply_profile(config_module, name: str) -> dict:
    """
    Setzt die Profil-Werte auf dem uebergebenen config-Modul.
    Gibt das angewandte Profil zurueck.
    """
    profile = get_profile(name)
    for key, value in profile["werte"].items():
        setattr(config_module, key, value)
    setattr(config_module, "ACTIVE_PROFILE", name.strip().lower())
    return profile


def describe(name: str) -> str:
    """Menschenlesbare Zusammenfassung eines Profils."""
    p = get_profile(name)
    w = p["werte"]
    lines = [
        f"Profil: {name.upper()}",
        "",
        p["beschreibung"],
        "",
        f"  Risiko je Trade:        {w['RISK_PER_TRADE_PCT'] * 100:.1f} % des Kontos",
        f"  Max. Kapital/Position:  {w['MAX_POSITION_PCT'] * 100:.0f} %",
        f"  Max. offene Positionen: {w['MAX_OPEN_POSITIONS']}",
        f"  Tages-Verlustlimit:     {w['MAX_DAILY_LOSS_PCT'] * 100:.0f} %",
        f"  Stop-Abstand:           {w['ATR_STOP_MULTIPLIER']} x ATR",
        f"  Gewinnziel:             {w['ATR_TAKE_MULTIPLIER']} x ATR",
        "",
        f"  Erwartung: {p['erwartung']}",
    ]
    return "\n".join(lines)
