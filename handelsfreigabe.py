"""Handelsfreigabe: jede Kaufsperre mit Grund, Reichweite, Ablauf und Aufloesung.

WARUM ES DIESES MODUL GIBT (10.7.0)
-----------------------------------
Bis 10.6.0 entschieden mindestens fuenf Stellen je Broker getrennt ueber
"darf kaufen": Risk-Manager, Bereitschaftsbedingungen, Buchungsabgleich,
Schnappschuss-Waechter und Kontostatus -- mit teils widerspruechlichen
Regeln fuer dieselbe Frage (eToro: der Buchungsabgleich sperrte ein
unbekanntes Ergebnis nur am Verkaufstag, der Risk-Manager fuer immer).
Niemand sah, welche Sperre gerade griff, wie weit sie reichte oder wann sie
enden wuerde. Neun Sperrfaelle in zehn Tagen waren die Folge.

Seit 10.7.0 stehen die Regeln an zwei zentralen Stellen:
  * ``okx_accounting.GAP_SCOPE``   -- Reichweite/Ablauf je Bestands-/Buchungsbeleg
  * ``risk_manager.offene_ergebnisse_heute`` -- Ergebnissperre nur am Verkaufstag

Dieses Modul liest alle Quellen und liefert EINE Liste. Jede Sperre traegt
vier Pflichtangaben:
  grund       woran es haengt (Kurzcode + Klartext)
  reichweite  SYMBOL (nur dieser Wert) oder DOMAIN (ganze Kontodomaene)
  ablauf      TAGESRESET, BELEG, ZEIT oder REPARATUR
  aufloesung  was die Sperre beendet

Die Liste ist Anzeige und Erklaerung. Die Entscheidung selbst faellt
weiterhin in den Kaufpfaden -- mit denselben Regeln, aus denselben Quellen.
Ein Test haelt beides deckungsgleich.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import logging
import re

logger = logging.getLogger(__name__)

SYMBOL, DOMAIN = "SYMBOL", "DOMAIN"
TAGESRESET, BELEG, ZEIT, REPARATUR = "TAGESRESET", "BELEG", "ZEIT", "REPARATUR"


@dataclass
class Sperre:
    broker: str
    grund: str
    reichweite: str
    ablauf: str
    aufloesung: str
    detail: str = ""
    symbol: str = ""
    quelle: str = ""

    def als_dict(self) -> dict:
        return asdict(self)


# Klassifikation der Risk-Manager-Gruende (Texte aus kaufsperre_grund /
# RiskPot.darf_kaufen). Reihenfolge = Prioritaet beim Erkennen.
_RISIKO_REGELN = (
    (r"Risikozustand nicht bestaetigt|RISK_PERSISTENCE|RISK_STATE_UNREADABLE", "RISIKOZUSTAND_UNBESTAETIGT", DOMAIN, REPARATUR,
     "Risikozustand persistierbar machen (Schreibfehler beheben)"),
    (r"RISK_EQUITY_BASIS_REVIEW|Tagesbasis", "TAGESBASIS_PRUEFUNG", DOMAIN, REPARATUR,
     "Kontobasis pruefen und bestaetigen"),
    (r"RISK_RESULT_PERIOD_REVIEW", "ERGEBNISPERIODE_WIDERSPRUCH", DOMAIN, REPARATUR,
     "Ergebnisperiodenbeleg pruefen"),
    (r"Tagesverlust", "TAGESVERLUSTGRENZE", DOMAIN, TAGESRESET, "naechster Handelstag"),
    (r"Equity-(Tages)?[Bb]remse", "EQUITY_BREMSE", DOMAIN, TAGESRESET, "naechster Handelstag oder Erholung"),
    (r"Ergebnisabgleich", "ERGEBNIS_OFFEN_HEUTE", DOMAIN, TAGESRESET,
     "Gebuehrenbeleg (Barbestand, Erwartungswert) oder naechster Handelstag"),
    (r"Cooldown|Abkuehlphase", "VERLUSTSERIE_PAUSE", DOMAIN, ZEIT, "Ablauf der Abkuehlphase"),
    (r"Kostenquote", "KOSTENQUOTE", DOMAIN, TAGESRESET, "naechster Handelstag"),
    (r"Trades pro Tag|Tageslimit", "TRADES_TAGESLIMIT", DOMAIN, TAGESRESET, "naechster Handelstag"),
    (r"Positionslimit|offene Positionen", "POSITIONSLIMIT", DOMAIN, BELEG, "Verkauf einer offenen Position"),
    (r"Handel fuer heute gestoppt", "HANDEL_GESTOPPT", DOMAIN, TAGESRESET, "naechster Handelstag"),
    (r"Kontowert unbekannt|Kontostand", "KONTOWERT_UNBEKANNT", DOMAIN, BELEG, "gueltiger Kontostand"),
)


def _risiko_sperre(broker: str, text: str) -> Sperre:
    for muster, code, reichweite, ablauf, aufloesung in _RISIKO_REGELN:
        if re.search(muster, text or ""):
            return Sperre(broker, code, reichweite, ablauf, aufloesung, detail=text, quelle="risikotopf")
    return Sperre(broker, "RISIKO_SONSTIGES", DOMAIN, BELEG, "siehe Detail", detail=text, quelle="risikotopf")


def okx_sperren(topf=None, broker=None, guard=None, accounting=None) -> list[Sperre]:
    """Alle OKX-Sperren aus Risikotopf, Buchhaltung und Schnappschuss-Waechter."""
    aus: list[Sperre] = []
    if topf is not None:
        try:
            darf, grund = topf.darf_kaufen()
        except Exception as exc:
            darf, grund = False, f"Risikotopf nicht pruefbar: {type(exc).__name__}"
        if not darf and grund and "Bestandsbelege offen" not in grund and "BROKER_STATE_UNKNOWN" not in grund:
            aus.append(_risiko_sperre("okx", grund))
    if accounting is None and broker is not None:
        try:
            from okx_accounting import broker_status
            accounting = broker_status(broker)
        except Exception as exc:
            logger.debug("OKX-Buchungsstatus fuer Sperrliste nicht lesbar", exc_info=True)
            accounting = {"complete": False, "gaps": [], "detail": f"nicht pruefbar: {type(exc).__name__}"}
    if accounting:
        for gap in accounting.get("gaps") or []:
            if not gap.get("sperrt", True):
                continue
            aus.append(Sperre(
                "okx", str(gap.get("kind") or "BUCHUNGSBELEG"), str(gap.get("scope") or DOMAIN),
                str(gap.get("ablauf") or BELEG), str(gap.get("aufloesung") or "Beleg"),
                detail=f"Trade {gap.get('trade_id')}" if gap.get("trade_id") else "",
                symbol=str(gap.get("base") or gap.get("symbol") or ""), quelle="buchhaltung"))
        if not accounting.get("complete") and not (accounting.get("gaps") or []):
            aus.append(Sperre("okx", "BUCHUNG_NICHT_PRUEFBAR", DOMAIN, REPARATUR,
                              "Ledger lesbar machen", detail=str(accounting.get("detail") or ""),
                              quelle="buchhaltung"))
    if guard:
        if not guard.get("valid", True):
            aus.append(Sperre("okx", "SCHNAPPSCHUSS_UNGUELTIG", DOMAIN, BELEG,
                              "gueltiger Guthaben-Schnappschuss", detail=str(guard.get("detail") or ""),
                              quelle="schnappschuss"))
        for symbol in guard.get("missing") or []:
            aus.append(Sperre("okx", "BESTAND_FEHLT_UNBESTAETIGT", SYMBOL, BELEG,
                              "zweite Messung mit Mindestabstand (dann Buchung) oder Bestand wieder da",
                              symbol=str(symbol).upper(), quelle="schnappschuss"))
        for eintrag in guard.get("exit_in_progress") or []:
            aus.append(Sperre("okx", "EXIT_IN_PROGRESS", SYMBOL, BELEG,
                              "Abrechnung des eigenen Schutz-Exits",
                              symbol=str(eintrag.get("symbol") or "").upper(), quelle="schnappschuss"))
    return _dedupe(aus)


def etoro_sperren(risk_state=None, domain: str = "", stock_bereitschaft=None) -> list[Sperre]:
    """Alle eToro-Sperren aus Risk-Manager, Buchungsabgleich und Bereitschaft."""
    aus: list[Sperre] = []
    if risk_state is not None:
        try:
            from risk_manager import kaufsperre_grund
            grund = kaufsperre_grund(risk_state)
        except Exception as exc:
            grund = f"Risikozustand nicht pruefbar: {type(exc).__name__}"
        if grund:
            aus.append(_risiko_sperre("etoro", grund))
    try:
        from etoro_reconciliation import pnl_unvollstaendig
        offen, detail = pnl_unvollstaendig(domain)
    except Exception as exc:
        offen, detail = False, f"nicht pruefbar: {type(exc).__name__}"
        logger.debug("eToro-Buchungsabgleich fuer Sperrliste nicht lesbar", exc_info=True)
    if offen:
        aus.append(Sperre("etoro", "PNL_INCOMPLETE", DOMAIN, TAGESRESET,
                          "Gebuehrenbeleg (Barbestand, Erwartungswert) oder naechster Handelstag",
                          detail=detail, quelle="buchungsabgleich"))
    if stock_bereitschaft is not None:
        try:
            darf, grund = stock_bereitschaft.darf_kaufen()
        except Exception:
            darf, grund = True, ""
        if not darf and grund and "PNL_INCOMPLETE" not in grund:
            aus.append(Sperre("etoro", "BEREITSCHAFT", DOMAIN, BELEG, "siehe Detail",
                              detail=grund, quelle="bereitschaft"))
    return _dedupe(aus)


def _dedupe(sperren: list[Sperre]) -> list[Sperre]:
    gesehen, out = set(), []
    for s in sperren:
        key = (s.broker, s.grund, s.symbol, s.detail)
        if key in gesehen:
            continue
        gesehen.add(key)
        out.append(s)
    return out


def als_dicts(sperren) -> list[dict]:
    return [s.als_dict() for s in sperren]


def zusammenfassung(sperren) -> dict:
    """Kompakt fuer Status und Oberflaeche."""
    domaene = [s for s in sperren if s.reichweite == DOMAIN]
    symbole = sorted({s.symbol for s in sperren if s.reichweite == SYMBOL and s.symbol})
    return {"domaene_gesperrt": bool(domaene), "gesperrte_symbole": symbole,
            "anzahl": len(sperren), "sperren": als_dicts(sperren)}


__all__ = ["BELEG", "DOMAIN", "REPARATUR", "SYMBOL", "Sperre", "TAGESRESET", "ZEIT",
           "als_dicts", "etoro_sperren", "okx_sperren", "zusammenfassung"]
