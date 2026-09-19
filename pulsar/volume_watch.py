"""Relatives Volumen als fruehester Hype-Beleg (10.5.0).

Die frueheste Spur eines Hypes ist ungewoehnliches Handelsvolumen, nicht ein
Beitrag in einem Forum. Zwei Datenwege, beide ohne zusaetzlichen Abruf:

1. FMP-Quote (je Karte ohnehin abgerufen): ``volume`` (Tagesvolumen bis jetzt)
   gegen ``avgVolume`` (Anbieter-Tagesdurchschnitt), zeitanteilig zur NY-Sitzung.
2. Stundenkerzen des eToro-Kerzenspeichers (der Handelskern sichert sie bei
   jedem Scan): heutiges kumuliertes Volumen bis zur gleichen Stunde gegen den
   Mittelwert derselben Stunde der letzten 20 Sitzungen.
3. 10.8.0: 15-Minuten-Kerzen von eToro fuer AKTIVE Karten (Ausloeser /
   Hype-Kandidat). Der Kern ruft sie ab (etoro_chart_store.ergaenze_fuer_karten),
   PULSAR liest sie (``intraday_from_store``). Dieselbe Rechnung wie bei den
   Stundenkerzen, nur alle 15 Minuten statt einmal je Stunde -- der FMP-Quote
   ist dann nur noch der Rueckfall.

Grundsaetze: kein relatives Volumen ohne Vergleichsbasis (UNKNOWN), keine
Bewertung vor Handelsbeginn, keine Kursprognose. Schwellen: Ausloeser >= 3,0x,
Bestaetigung >= 2,0x, jeweils nur bei positivem Tageskurs.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math
import time

NY = ZoneInfo("America/New_York")
SESSION_OPEN = (9, 30)
SESSION_CLOSE = (16, 0)
SESSION_MINUTES = 390
TRIGGER_MULTIPLE = 3.0
CONFIRM_MULTIPLE = 2.0
MIN_SESSION_FRACTION = 0.10
QUOTE_MAX_AGE = 900
LOOKBACK_SESSIONS = 20
MIN_SESSIONS = 10


def _number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 0 else None
    except (TypeError, ValueError):
        return None


def session_fraction(stamp):
    """Anteil der NY-Handelssitzung, der zum Zeitpunkt ``stamp`` vergangen ist."""
    local = datetime.fromtimestamp(stamp, NY)
    if local.weekday() >= 5:
        return None
    start = local.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1], second=0, microsecond=0)
    end = local.replace(hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1], second=0, microsecond=0)
    if local < start:
        return 0.0
    if local >= end:
        return 1.0
    return (local - start).total_seconds() / (SESSION_MINUTES * 60)


def from_quote(quote, *, now=None):
    """Relatives Volumen aus dem FMP-Quote; UNKNOWN ohne Basis oder vor Handelsbeginn."""
    now = time.time() if now is None else now
    if not isinstance(quote, dict):
        return {"status": "UNKNOWN", "rvol": None, "detail": "Kein Quote"}
    volume, average = _number(quote.get("volume")), _number(quote.get("avgVolume"))
    stamp = quote.get("timestamp")
    fresh = type(stamp) in (int, float) and math.isfinite(stamp) and -60 <= now - stamp <= QUOTE_MAX_AGE
    if not fresh:
        return {"status": "STALE", "rvol": None, "detail": "Quote aelter als 15 Minuten oder ohne Zeitstempel"}
    if volume is None or not average:
        return {"status": "UNKNOWN", "rvol": None, "detail": "Quote ohne Volumen oder Durchschnittsvolumen"}
    fraction = session_fraction(stamp)
    if fraction is None or fraction <= 0:
        return {"status": "NOT_IN_SESSION", "rvol": None, "detail": "Vor Handelsbeginn oder Wochenende; kein Vergleich"}
    expected = average * max(MIN_SESSION_FRACTION, fraction)
    rvol = volume / expected
    price, prev = _number(quote.get("price")), _number(quote.get("previousClose"))
    gain = (price / prev - 1) if price and prev else None
    return {"status": "OK", "rvol": rvol, "session_fraction": fraction, "volume": volume,
            "average_daily_volume": average, "gain": gain, "method": "FMP_QUOTE_VS_AVGVOLUME",
            "observed_at": stamp,
            "detail": f"{rvol:.1f}x des zeitanteiligen Durchschnittsvolumens nach {fraction*100:.0f} % der Sitzung"
                      + (f", Kurs {gain*100:+.1f} % zum Vortag" if gain is not None else "")}


def from_hourly(rows, *, now=None, method="HOURLY_CUMULATIVE_VS_20_SESSIONS"):
    """Relatives Volumen aus Sitzungskerzen ``[(ts, volume, close), ...]`` (UTC-Sekunden).

    Vergleich: heutiges kumuliertes Volumen bis zur letzten abgeschlossenen
    Kerze gegen den Mittelwert des kumulierten Volumens bis zur gleichen
    Sitzungsminute der letzten 20 Sitzungen. Ohne 10 Vergleichssitzungen UNKNOWN.
    Die Rechnung ist von der Kerzenlaenge unabhaengig (Stunden- oder
    15-Minuten-Kerzen); ``method`` benennt die Quelle im Ergebnis.
    """
    now = time.time() if now is None else now
    sessions = {}
    for item in rows or []:
        try:
            ts, volume, close = float(item[0]), _number(item[1]), _number(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        if volume is None or ts > now:
            continue
        local = datetime.fromtimestamp(ts, NY)
        if local.weekday() >= 5:
            continue
        key = local.date().isoformat()
        sessions.setdefault(key, []).append((ts, local.hour * 60 + local.minute, volume, close))
    if not sessions:
        return {"status": "UNKNOWN", "rvol": None, "detail": "Keine Stundenkerzen"}
    today = datetime.fromtimestamp(now, NY).date().isoformat()
    if today not in sessions:
        return {"status": "NOT_IN_SESSION", "rvol": None, "detail": "Heute noch keine abgeschlossene Handelsstunde"}
    current = sorted(sessions[today])
    last_minute = current[-1][1]
    today_volume = sum(v for _, _, v, _ in current)
    history = []
    for key in sorted(k for k in sessions if k < today)[-LOOKBACK_SESSIONS:]:
        bars = sorted(sessions[key])
        history.append(sum(v for _, minute, v, _ in bars if minute <= last_minute))
    history = [h for h in history if h > 0]
    if len(history) < MIN_SESSIONS:
        return {"status": "UNKNOWN", "rvol": None, "sessions": len(history),
                "detail": f"Nur {len(history)} Vergleichssitzungen (mindestens {MIN_SESSIONS})"}
    expected = sum(history) / len(history)
    rvol = today_volume / expected if expected > 0 else None
    previous_close = None
    for key in sorted(k for k in sessions if k < today)[-1:]:
        closes = [c for _, _, _, c in sorted(sessions[key]) if c]
        previous_close = closes[-1] if closes else None
    last_close = next((c for _, _, _, c in reversed(current) if c), None)
    gain = (last_close / previous_close - 1) if last_close and previous_close else None
    if rvol is None:
        return {"status": "UNKNOWN", "rvol": None, "detail": "Vergleichsvolumen null"}
    return {"status": "OK", "rvol": rvol, "gain": gain, "sessions": len(history),
            "today_volume": today_volume, "expected_volume": expected, "last_bar_minute": last_minute,
            "method": method, "observed_at": current[-1][0],
            "detail": f"{rvol:.1f}x des kumulierten Volumens bis {last_minute//60:02d}:{last_minute%60:02d} NY "
                      f"({len(history)} Vergleichssitzungen)"
                      + (f", Kurs {gain*100:+.1f} % zum Vortagesschluss" if gain is not None else "")}


def classify(measure):
    """Ausloeser / Bestaetigung / nichts -- nur bei positivem Tageskurs."""
    if not measure or measure.get("status") != "OK" or measure.get("rvol") is None:
        return {"trigger": False, "confirm": False, "reason": (measure or {}).get("detail") or "Relatives Volumen unbekannt"}
    gain = measure.get("gain")
    positive = gain is None or gain > 0
    rvol = float(measure["rvol"])
    return {"trigger": positive and rvol >= TRIGGER_MULTIPLE,
            "confirm": positive and rvol >= CONFIRM_MULTIPLE,
            "reason": measure.get("detail", "") + ("" if positive else " -- Tageskurs nicht positiv")}


def scan_universe(rows_by_symbol, *, now=None):
    """Kandidaten mit Volumen-Ausloeser aus Stundenkerzen mehrerer Symbole."""
    now = time.time() if now is None else now
    out = []
    for symbol, rows in (rows_by_symbol or {}).items():
        measure = from_hourly(rows, now=now)
        verdict = classify(measure)
        if verdict["trigger"]:
            out.append({"symbol": str(symbol).upper(), "source": "volume_watch", "source_family": "market_volume",
                        "observed_at": measure.get("observed_at") or now, "rvol": measure["rvol"],
                        "gain": measure.get("gain"), "mentions": None, "mentions_24h_ago": None,
                        "url": "", "detail": measure["detail"],
                        "discovery": {"state": "VOLUMEN_AUSLOESER", "growth_score": 0.,
                                      "detail": "Relatives Volumen aus Stundenkerzen; Social-Belege werden getrennt gepruft."}})
    out.sort(key=lambda r: -r["rvol"])
    return out


INTRADAY_STORE_MAX_AGE = 2700  # 45 min: 15-min-Kerzen werden alle 15 min nachgeladen
INTRADAY_METHOD = "ETORO_15M_CUMULATIVE_VS_20_SESSIONS"


def intraday_from_store(symbol, *, now=None):
    """15-Minuten-Kerzen einer aktiven Karte aus dem eToro-Kerzenspeicher (10.8.0, kein Abruf).

    Liefert ``{"rows": [(ts, volume, close), ...], "intraday": [FMP-artige Zeilen],
    "saved_at": ..., "fresh": bool, "avg_day_volume": ...}``. ``fresh`` heisst:
    die juengste Kerze ist hoechstens 45 Minuten alt -- nur dann zaehlt sie vor
    dem FMP-Quote. ``avg_day_volume`` ist das mittlere Sitzungsvolumen der
    letzten 20 vollen Sitzungen DERSELBEN Quelle (mindestens 10; sonst None):
    eToro zaehlt Volumen nicht wie FMP, deshalb wird nie gegen den FMP-Tages-
    durchschnitt verglichen. Ohne Speicher, Reihe oder bei veralteter Reihe
    bleibt alles leer/False; es wird nichts geraten.
    """
    import logging
    now = time.time() if now is None else now
    leer = {"rows": [], "intraday": [], "saved_at": None, "fresh": False, "source": "ETORO_15M", "avg_day_volume": None}
    try:
        import etoro_chart_store as store
        data = store.lade_neueste(str(symbol).upper(), "15m", limit=25*30)
    except Exception as exc:
        logging.getLogger(__name__).debug("15-Minuten-Kerzen fuer %s nicht lesbar: %s", symbol, exc)
        return leer
    rows, intraday = [], []
    for c in data.get("candles") or []:
        try:
            ts = int(datetime.fromisoformat(c["zeit"]).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        if ts + 900 > now:
            continue  # nur abgeschlossene Kerzen
        rows.append((ts, c.get("volume"), c.get("close")))
        intraday.append({"date": datetime.fromtimestamp(ts, NY).isoformat(),
                         "open": c.get("open"), "high": c.get("high"), "low": c.get("low"),
                         "close": c.get("close"), "volume": c.get("volume")})
    if not rows:
        return leer
    fresh = now - rows[-1][0] <= INTRADAY_STORE_MAX_AGE + 900
    return {"rows": rows, "intraday": intraday, "saved_at": data.get("saved_at"), "fresh": fresh, "source": "ETORO_15M",
            "avg_day_volume": session_average_volume(rows, now=now)}


def session_average_volume(rows, *, now=None, sessions=LOOKBACK_SESSIONS, minimum=MIN_SESSIONS):
    """Mittleres Gesamtvolumen der letzten vollen Sitzungen (ohne heute); None unter ``minimum``."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, NY).date().isoformat()
    totals = {}
    for item in rows or []:
        try:
            ts, volume = float(item[0]), _number(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if volume is None or ts > now:
            continue
        local = datetime.fromtimestamp(ts, NY)
        key = local.date().isoformat()
        if local.weekday() >= 5 or key >= today:
            continue
        totals[key] = totals.get(key, 0.0) + volume
    history = [totals[k] for k in sorted(totals)[-sessions:] if totals[k] > 0]
    if len(history) < minimum:
        return None
    return sum(history) / len(history)


def hourly_rows_from_store(*, limit_symbols=60):
    """Stundenkerzen aller eToro-Aktien aus dem Kern-Kerzenspeicher (kein Abruf).

    Der Handelskern sichert bei jedem Scan die 60-Tage-Stundenreihe
    (etoro_chart_store); PULSAR liest sie nur. Ohne Datei: keine Ausloeser.
    """
    import logging
    try:
        import etoro_chart_store as store
        out = {}
        for account, environment, symbol in store.gespeicherte_reihen("1h")[:limit_symbols]:
            data = store.lade(account, environment, symbol, "1h", limit=25*8)
            out[symbol] = [(int(datetime.fromisoformat(c["zeit"]).timestamp()), c.get("volume"), c.get("close"))
                           for c in data.get("candles") or []]
        return out
    except Exception as exc:
        logging.getLogger(__name__).debug("Stundenkerzen fuer den Volumenscan nicht lesbar: %s", exc)
        return {}


__all__ = ["from_quote", "from_hourly", "classify", "scan_universe", "session_fraction", "hourly_rows_from_store",
           "intraday_from_store", "session_average_volume", "INTRADAY_METHOD", "TRIGGER_MULTIPLE", "CONFIRM_MULTIPLE"]
