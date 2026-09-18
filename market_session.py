"""Handelssitzungs-Schutz fuer neue Einstiege.

Ziel dieser Schicht ist NICHT, Brokeroeffnungszeiten zu erraten. Sie trennt
bewusst drei Dinge:

1. regulaere Boersenzeit (RTH) des zugrunde liegenden Marktes,
2. moegliche Extended-/24/5-Handelbarkeit beim Broker,
3. Krypto, das grundsaetzlich 24/7 gehandelt wird.

Bei eToro wird zusaetzlich der aktuelle Rates-Datensatz abgefragt. Falls die
API einen eindeutigen Status liefert, wird er verwendet. Da die Public-API-
Dokumentation keinen fuer jedes Instrument garantierten ``isOpen``-Schalter
verspricht, gibt es einen fail-safe Fallback auf die regulaere Exchange-Zeit.
Ein frischer eToro-Quote kann Extended-Hours-Handelbarkeit plausibilisieren,
macht aus Extended Hours aber niemals regulaere Boersenzeit.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo
from typing import Any


@dataclass(frozen=True)
class MarketSessionStatus:
    asset_type: str
    session: str
    regular_open: bool
    broker_tradable: bool | None
    quote_fresh: bool | None
    source: str
    detail: str
    checked_at_utc: str
    # -- ab v8.1.5: das, was fuer die Meldung gebraucht wird ----------------
    # Am 25.08.2026 stand in der Oberflaeche "Markt geschlossen", waehrend der
    # US-Handel lief. Der wahre Grund -- ein 18741 s alter GOOGL-Kurs -- lag im
    # Rohdatensatz, kam aber nie bis zur Meldung. Deshalb wandern Symbol,
    # Kalenderlage, Kursalter und Herabstufungsgrund jetzt mit.
    symbol: str = ""
    scheduled_session: str = ""     # was der Kalender sagt, VOR Herabstufung
    downgrade_grund: str = ""       # "" | "stale_quote" | "broker_closed"
    quote_age_seconds: float | None = None
    quote_max_age_seconds: float | None = None
    naechste_oeffnung_lokal: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def kalender_offen(self) -> bool:
        """Ist laut Boersenkalender regulaere Handelszeit?

        Unabhaengig davon, ob ein Datenproblem den Status herabgestuft hat --
        genau diese Unterscheidung hat in der alten Meldung gefehlt.
        """
        return (self.scheduled_session or self.session) == "REGULAR"

    def short(self) -> str:
        if self.asset_type == "crypto":
            return "Krypto 24/7"
        label = {
            "REGULAR": "REGULAR offen",
            "EXTENDED": "Extended Hours",
            "CLOSED": "geschlossen",
            "UNKNOWN": "Status unbekannt",
        }.get(self.session, self.session)
        if self.downgrade_grund and self.kalender_offen:
            # "geschlossen" waere hier schlicht falsch.
            return f"{label} (Kalender offen, {_grundwort(self.downgrade_grund)})"
        return label


def _grundwort(grund: str) -> str:
    return {"stale_quote": "Kurs veraltet",
            "broker_closed": "Broker sperrt"}.get(grund, grund)


def altersklartext(sekunden) -> str:
    """Ein Kursalter so, dass man es ohne Kopfrechnen einordnen kann."""
    try:
        s = float(sekunden)
    except (TypeError, ValueError):
        return "unbekannt"
    if s != s or s < 0:
        return "unbekannt"
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    stunden = int(s // 3600)
    minuten = int((s % 3600) // 60)
    return f"{stunden} h {minuten:02d} min" if minuten else f"{stunden} h"


def _norm_exchange(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _stock_schedule(instrument) -> tuple[ZoneInfo, dtime, dtime, dtime, dtime, str]:
    """Return timezone, regular open/close, extended open/close, schedule label.

    The current eToro adapter only trades unambiguous USD stocks, therefore the
    US schedule is by far the relevant path. A few common European exchanges are
    still mapped for broker-neutral diagnostics.
    """
    ex = _norm_exchange(getattr(instrument, "exchange", ""))
    cur = str(getattr(instrument, "currency", "USD") or "USD").upper()

    if cur == "USD" or ex in {
        "SMART", "NYSE", "NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM",
        "ARCA", "AMEX", "BATS", "CBOE", "IEX", "NYSEARCA",
    }:
        return ZoneInfo("America/New_York"), dtime(9, 30), dtime(16, 0), dtime(4, 0), dtime(20, 0), "US"
    if ex in {"LSE", "LSEETF", "LONDON"} or cur == "GBP":
        return ZoneInfo("Europe/London"), dtime(8, 0), dtime(16, 30), dtime(8, 0), dtime(16, 30), "LSE"
    # Xetra/Euronext/most continental core exchanges. This fallback is used
    # only for session safety; broker market-quality checks remain mandatory.
    return ZoneInfo("Europe/Berlin"), dtime(9, 0), dtime(17, 30), dtime(8, 0), dtime(22, 0), "EU"


def _scheduled_stock_session(instrument, now_utc: datetime) -> tuple[str, bool, str]:
    tz, r_open, r_close, e_open, e_close, label = _stock_schedule(instrument)
    local = now_utc.astimezone(tz)
    if local.weekday() >= 5:
        return "CLOSED", False, f"{label}: Wochenende"
    t = local.time().replace(tzinfo=None)
    if r_open <= t < r_close:
        return "REGULAR", True, f"{label}: regulaere Handelszeit {r_open.strftime('%H:%M')}-{r_close.strftime('%H:%M')} {tz.key}"
    if e_open <= t < e_close:
        return "EXTENDED", False, f"{label}: ausserhalb RTH, innerhalb Extended-Zeitfenster"
    return "CLOSED", False, f"{label}: ausserhalb Handelszeitfenster"


def naechste_regulaere_oeffnung(instrument, now_utc: datetime) -> str:
    """Wann oeffnet die regulaere Boersenzeit als naechstes? (Ortszeit)

    "Markt geschlossen" ohne Uhrzeit laesst offen, ob man in zehn Minuten
    oder erst am Montag wieder schauen muss.
    """
    from datetime import timedelta
    tz, r_open, _r_close, _e_open, _e_close, _label = _stock_schedule(instrument)
    local = now_utc.astimezone(tz)
    for tag in range(0, 8):
        kandidat = (local + timedelta(days=tag)).replace(
            hour=r_open.hour, minute=r_open.minute, second=0, microsecond=0)
        if kandidat.weekday() >= 5 or kandidat <= local:
            continue
        hier = kandidat.astimezone()
        if kandidat.date() == local.date():
            return f"{hier:%H:%M} Uhr"
        return f"{hier:%a %d.%m., %H:%M} Uhr"
    return ""


def market_session_status(broker, instrument, *, now: datetime | None = None,
                          quote_max_age_seconds: float = 180.0) -> MarketSessionStatus:
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    asset = str(getattr(instrument, "asset_type", "") or "").lower()
    checked = now_utc.isoformat()
    symbol = str(getattr(instrument, "name", "") or getattr(instrument, "symbol", "") or "")
    grenze = max(1.0, float(quote_max_age_seconds))
    if asset == "crypto":
        # eToro lists crypto trading as 24/7. A broker-specific live status, if
        # available, may still report a temporary maintenance restriction.
        broker_state = None
        source = "asset_schedule"
        detail = "Krypto: 24/7; temporaere Broker-Wartung bleibt moeglich"
        try:
            probe = getattr(broker, "market_session_status", None)
            if callable(probe):
                raw = probe(instrument)
                if isinstance(raw, dict):
                    broker_state = raw.get("broker_tradable")
                    source = str(raw.get("source") or source)
                    if raw.get("detail"):
                        detail += f"; {raw.get('detail')}"
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        return MarketSessionStatus(asset, "CRYPTO_24_7", broker_state if broker_state is not None else True,
                                   broker_state, None, source, detail, checked,
                                   symbol=symbol, scheduled_session="CRYPTO_24_7")

    if asset != "stock":
        return MarketSessionStatus(asset or "unknown", "UNKNOWN", False, None, None,
                                   "unsupported_asset", "Keine Sitzungsregel fuer diesen Asset-Typ", checked,
                                   symbol=symbol)

    scheduled_session, regular_open, schedule_detail = _scheduled_stock_session(instrument, now_utc)
    broker_tradable: bool | None = None
    quote_fresh: bool | None = None
    quote_age: float | None = None
    source = "exchange_schedule"
    details = [schedule_detail]

    try:
        probe = getattr(broker, "market_session_status", None)
        if callable(probe):
            raw = probe(instrument)
            if isinstance(raw, dict):
                if raw.get("broker_tradable") is not None:
                    broker_tradable = bool(raw.get("broker_tradable"))
                age = raw.get("quote_age_seconds")
                if age is not None:
                    try:
                        quote_age = float(age)
                        if quote_age != quote_age:      # NaN
                            quote_age, quote_fresh = None, None
                        else:
                            quote_fresh = quote_age <= grenze
                    except Exception:
                        quote_age, quote_fresh = None, None
                source = str(raw.get("source") or source)
                if raw.get("detail"):
                    details.append(str(raw.get("detail")))
    except Exception as exc:
        details.append(f"Broker-Status nicht verfuegbar: {type(exc).__name__}")

    # During scheduled RTH a broker explicitly reporting 'closed' or a stale
    # quote is enough to fail closed (holiday, halt, maintenance, stale feed).
    # v8.1.5: der Grund wird festgehalten, nicht nur die Folge. Sonst steht am
    # Ende "Markt geschlossen", obwohl der Markt offen und nur der Kurs alt ist.
    session = scheduled_session
    downgrade = ""
    if regular_open and broker_tradable is False:
        session, regular_open, downgrade = "CLOSED", False, "broker_closed"
        details.append("Broker meldet Instrument nicht handelbar")
    elif regular_open and quote_fresh is False:
        session, regular_open, downgrade = "CLOSED", False, "stale_quote"
        details.append(f"Kurs waehrend regulaerer Handelszeit "
                       f"{altersklartext(quote_age)} alt (Grenze "
                       f"{altersklartext(grenze)}); Feiertag/Halt/Feedproblem moeglich")

    # Outside RTH an explicit/fresh broker signal may indicate eToro 24/5
    # availability, but the session remains EXTENDED and is not upgraded to RTH.
    if not regular_open and scheduled_session == "EXTENDED" and broker_tradable is False:
        session = "CLOSED"
        details.append("Broker meldet Extended-Handel nicht verfuegbar")

    return MarketSessionStatus("stock", session, regular_open, broker_tradable,
                               quote_fresh, source, "; ".join(details), checked,
                               symbol=symbol, scheduled_session=scheduled_session,
                               downgrade_grund=downgrade, quote_age_seconds=quote_age,
                               quote_max_age_seconds=grenze,
                               naechste_oeffnung_lokal=(
                                   "" if scheduled_session == "REGULAR"
                                   else naechste_regulaere_oeffnung(instrument, now_utc)))


# ---------------------------------------------------------------------------
# Herabstufung waehrend regulaerer Handelszeit melden (v8.1.5)
# ---------------------------------------------------------------------------
# Wenn der Kalender offen sagt und der Bot trotzdem nicht kauft, ist das eine
# Stoerung und keine Routine. Georg soll davon erfahren -- aber einmal je
# Symbol und Tag, nicht bei jedem Zyklus. Am 25.08. haette diese eine
# Nachricht die ganze Fehlersuche erspart.
_gemeldete_herabstufungen: set[tuple[str, str, str]] = set()


def melde_herabstufung(status: MarketSessionStatus, melder) -> bool:
    """Einmal je Symbol, Grund und Tag melden. True = wurde gemeldet."""
    if not status.downgrade_grund or not status.kalender_offen or melder is None:
        return False
    tag = str(status.checked_at_utc or "")[:10]
    schluessel = (status.symbol or "?", status.downgrade_grund, tag)
    if schluessel in _gemeldete_herabstufungen:
        return False
    _gemeldete_herabstufungen.add(schluessel)

    if status.downgrade_grund == "stale_quote":
        text = (f"{status.symbol or 'Instrument'}: Die Boerse ist offen, aber der "
                f"Kurs ist {altersklartext(status.quote_age_seconds)} alt "
                f"(erlaubt {altersklartext(status.quote_max_age_seconds)}). "
                "Neue Kaeufe pausieren fuer diesen Wert, bis wieder frische "
                "Kurse kommen. Bestehende Positionen, Stops und Verkaeufe "
                "laufen normal weiter.")
    else:
        text = (f"{status.symbol or 'Instrument'}: Die Boerse ist offen, aber der "
                "Broker meldet das Instrument als nicht handelbar "
                "(Handelsaussetzung oder Wartung). Neue Kaeufe pausieren fuer "
                "diesen Wert. Bestehende Positionen, Stops und Verkaeufe laufen "
                "normal weiter.")
    try:
        melder("MARKT OFFEN, ABER KEIN HANDEL", text)
    except Exception:
        __import__("logging").getLogger(__name__).debug(
            "Herabstufungsmeldung nicht zustellbar", exc_info=True)
        # Nicht erneut versuchen: eine nicht zustellbare Nachricht darf keinen
        # Dauerlauf ausloesen.
    return True


def entry_allowed(status: MarketSessionStatus, *, regular_hours_only: bool = True) -> tuple[bool, str]:
    """Darf jetzt ein Aktien-Neueinstieg stattfinden -- und warum nicht?

    Die Begruendung nennt ab v8.1.5 die tatsaechliche Ursache. Vorher stand
    dort in allen Faellen sinngemaess "ausserhalb regulaerer Boersenzeit".
    Am 25.08.2026 hat Georg genau das gemeldet: "Markt geschlossen. Macht
    aber kein Sinn weil er noch offen ist (Amerika)." Er hatte recht -- die
    Sperre war richtig, die Begruendung falsch. Blockiert hat ein 5 h 12 min
    alter GOOGL-Kurs.
    """
    if status.asset_type == "crypto":
        if status.broker_tradable is False:
            return False, f"Krypto 24/7, aber Broker meldet aktuell nicht handelbar ({status.detail})"
        return True, "Krypto 24/7"
    if status.asset_type != "stock":
        return False, "Unbekannter Marktstatus"
    if status.regular_open:
        return True, "Regulaere Boersenzeit"

    name = f"{status.symbol}: " if status.symbol else ""

    # Fall 1: Der Kalender sagt offen -- ein Datenproblem hat herabgestuft.
    # Das ist KEINE Boersenzeit-Sperre und darf auch nicht so heissen.
    if status.downgrade_grund == "stale_quote":
        return False, (
            f"{name}Kein Einstieg, weil der Kurs "
            f"{altersklartext(status.quote_age_seconds)} alt ist "
            f"(erlaubt: {altersklartext(status.quote_max_age_seconds)}). "
            "Die Boerse ist laut Kalender offen -- das ist ein Datenproblem, "
            "keine Handelszeit. Ohne aktuellen Kurs waeren Stop und "
            "Positionsgroesse geraten."
        )
    if status.downgrade_grund == "broker_closed":
        return False, (
            f"{name}Kein Einstieg, weil der Broker das Instrument als nicht "
            "handelbar meldet (Handelsaussetzung oder Wartung). Die Boerse ist "
            "laut Kalender offen."
        )

    # Fall 2: Es ist wirklich keine regulaere Boersenzeit.
    wann = (f" Regulaerer Handel wieder ab {status.naechste_oeffnung_lokal}."
            if status.naechste_oeffnung_lokal else "")
    if regular_hours_only:
        lage = ("Vor-/Nachboerse" if status.scheduled_session == "EXTENDED"
                else "Boerse geschlossen")
        return False, (
            f"{name}Kein Einstieg: {lage}.{wann} "
            "Ausserhalb der regulaeren Handelszeit sind Spreads groesser und "
            "die Liquiditaet duenner; Krypto bleibt davon unberuehrt."
        )
    if status.session == "EXTENDED" and status.broker_tradable is not False:
        return True, "Extended-Hours-Einstieg explizit erlaubt; Live-Spread und Broker-What-if-Kosten bleiben Pflicht"
    return False, f"{name}Kein Einstieg: Boerse geschlossen.{wann} ({status.detail})"
