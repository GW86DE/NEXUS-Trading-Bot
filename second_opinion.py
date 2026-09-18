"""Optionale KI-Zweitmeinung, ohne eigene Handelsrechte.

Deterministische Pruefungen bleiben verbindlich. Bei einer kritischen Antwort
kann das aktivierte menschliche Freigabegate einen Kauf zurueckstellen. Eine
Freigabe wird vor Ausfuehrung erneut auf Kurs, Menge, Cash und Risiko geprueft.
Ausfaelle erzeugen einen sichtbaren Hinweis; Schutz und Ausstiege warten nicht
auf eine KI-Antwort. OFF sperrt alle Routeraufrufe und die Nutzung alter Antworten.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

UNAUFFAELLIG = "unauffaellig"
VORSICHTIG = "vorsichtig"
KRITISCH = "kritisch"
NICHT_ERREICHBAR = "nicht_erreichbar"

# Betriebsarten des Schalters in der WebUI.
AUS = "aus"
NUR_LIVE = "nur_live"
IMMER = "immer"

WARTEDATEI = "ai_pending_orders.json"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["einschaetzung", "begruendung"],
    "properties": {
        "einschaetzung": {"type": "string",
                          "enum": [UNAUFFAELLIG, VORSICHTIG, KRITISCH]},
        "begruendung": {"type": "string", "maxLength": 400},
        "chancen": {"type": "array", "items": {"type": "string"}},
        "risiken": {"type": "array", "items": {"type": "string"}},
        "widersprueche": {"type": "array", "items": {"type": "string"}},
        "nachrichtenlage": {"type": "string"},
    },
}

ANWEISUNG = (
    "Du bewertest einen Kaufkandidaten, den ein regelbasierter Handelsbot "
    "bereits vollstaendig freigegeben hat. Technik, Nachrichten, Risiko, "
    "Kosten und Cash sind geprueft. Deine Aufgabe ist AUSSCHLIESSLICH eine "
    "zweite Meinung: Nenne Chancen, Risiken, widerspruechliche Signale und "
    "eine auffaellige Nachrichtenlage. "
    "Du gibst KEINE Handelsempfehlung, KEINE Freigabe und KEINE Ablehnung. "
    "Stufe ein: 'unauffaellig' wenn nichts dagegen spricht, 'vorsichtig' bei "
    "erkennbaren Schwaechen, 'kritisch' NUR wenn ein konkreter, benennbarer "
    "Grund gegen genau diesen Einstieg spricht. "
    "Keine Websuche. Antworte knapp und pruefbar."
)


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------
def _cfg(name: str, standard):
    import live_settings
    hole = getattr(live_settings, "ai_second_opinion", None)
    if callable(hole):
        try:
            werte = hole() or {}
            if name in werte:
                return werte[name]
        except Exception:
            logger.debug("Second-Opinion-Einstellungen nicht lesbar", exc_info=True)
    import config
    return getattr(config, name, standard)


def modus() -> str:
    wert = str(_cfg("AI_SECOND_OPINION_MODE", AUS) or AUS).strip().lower()
    return wert if wert in (AUS, NUR_LIVE, IMMER) else AUS


def aktiv(*, live: bool) -> bool:
    """Soll die zweite Meinung fuer diese Order eingeholt werden?"""
    art = modus()
    if art == IMMER:
        return True
    if art == NUR_LIVE:
        return bool(live)
    return False


def human_gate_aktiv() -> bool:
    """Die KI entscheidet nicht; bei kritisch entscheidet der Mensch."""
    return bool(_cfg("AI_CRITICAL_HUMAN_GATE", True))


def _state_root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                or Path(__file__).resolve().parent)


# ---------------------------------------------------------------------------
# Das Urteil
# ---------------------------------------------------------------------------
@dataclass
class Urteil:
    """Das Ergebnis einer zweiten Meinung."""
    einschaetzung: str = NICHT_ERREICHBAR
    begruendung: str = ""
    chancen: list = field(default_factory=list)
    risiken: list = field(default_factory=list)
    widersprueche: list = field(default_factory=list)
    nachrichtenlage: str = ""
    modell: str = ""
    kosten_usd: float = 0.0
    dauer_s: float = 0.0
    gefragt: bool = False

    @property
    def blockiert(self) -> bool:
        """Die KI erteilt keine Handelsfreigabe; das menschliche Freigabegate bleibt separat."""
        return False

    @property
    def kritisch(self) -> bool:
        """Kennzeichnung fuer Warnung und Protokoll, kein Handels-Gate."""
        return self.einschaetzung == KRITISCH

    @property
    def kurztext(self) -> str:
        if not self.gefragt:
            return ""
        if self.einschaetzung == NICHT_ERREICHBAR:
            return f"KI nicht erreichbar ({self.begruendung})" if self.begruendung \
                else "KI nicht erreichbar"
        return f"{self.einschaetzung}: {self.begruendung}"

    def als_dict(self) -> dict:
        return {
            "einschaetzung": self.einschaetzung, "begruendung": self.begruendung,
            "chancen": list(self.chancen), "risiken": list(self.risiken),
            "widersprueche": list(self.widersprueche),
            "nachrichtenlage": self.nachrichtenlage, "modell": self.modell,
            "kosten_usd": round(self.kosten_usd, 6),
            "dauer_s": round(self.dauer_s, 2), "gefragt": self.gefragt,
        }


def hole(router, fakten: dict) -> Urteil:
    """Eine zweite Meinung einholen. Wirft nie.

    Faellt die KI aus, kommt ein Urteil mit ``NICHT_ERREICHBAR`` zurueck --
    und der Kauf laeuft weiter. Das ist Absicht: Die feste Kaskade hat ihn
    bereits freigegeben; eine nicht abgegebene Warnung ist keine Warnung.
    """
    urteil = Urteil()
    if router is None:
        urteil.begruendung = "KI-Router nicht aktiv"
        return urteil

    start = time.monotonic()
    urteil.gefragt = True
    try:
        # An already scheduled review can reuse optional FMP facts. Never issue
        # extra provider/GPT calls or replace the supplied broker price.
        prepared = dict(fakten)
        try:
            from fmp_data import research_context
            context = research_context(prepared.get("symbol", ""))
            if context:
                prepared["fmp_reference_context"] = context
                from fmp_service import record_use
                from pulsar.research import facts as stable_facts
                record_use("NEXUS_ZWEITMEINUNG_MIT_FMP",prepared.get("symbol", ""),stable_facts(context))
        except Exception as exc:
            logger.info("FMP-Zusatzkontext nicht verfuegbar (%s)", type(exc).__name__)
        antwort = router.frage("second_opinion", prepared, SCHEMA,
                               anweisung=ANWEISUNG, cache_erlaubt=False,
                               timeout_seconds=float(_cfg("AI_SECOND_OPINION_TIMEOUT_SECONDS", 8)))
    except Exception as exc:
        urteil.begruendung = f"{type(exc).__name__}: {exc}"[:200]
        urteil.dauer_s = time.monotonic() - start
        logger.warning("Second Opinion fehlgeschlagen: %s", exc)
        return urteil

    urteil.dauer_s = time.monotonic() - start
    if not getattr(antwort, "ok", False):
        urteil.begruendung = str(getattr(antwort, "grund", ""))[:200]
        return urteil

    daten = getattr(antwort, "daten", None) or {}
    einstufung = str(daten.get("einschaetzung", "")).strip().lower()
    if einstufung not in (UNAUFFAELLIG, VORSICHTIG, KRITISCH):
        urteil.begruendung = f"Antwort passt nicht ins Schema: {einstufung!r}"
        return urteil

    urteil.einschaetzung = einstufung
    urteil.begruendung = str(daten.get("begruendung", ""))[:400]
    urteil.chancen = [str(x)[:200] for x in (daten.get("chancen") or [])][:5]
    urteil.risiken = [str(x)[:200] for x in (daten.get("risiken") or [])][:5]
    urteil.widersprueche = [str(x)[:200] for x in (daten.get("widersprueche") or [])][:5]
    urteil.nachrichtenlage = str(daten.get("nachrichtenlage", ""))[:300]
    urteil.modell = str(getattr(antwort, "modell", ""))
    urteil.kosten_usd = float(getattr(antwort, "kosten", getattr(antwort, "kosten_usd", 0.0)) or 0.0)
    return urteil


# ---------------------------------------------------------------------------
# Wartende Orders
# ---------------------------------------------------------------------------
_lock = threading.RLock()


def _laden() -> dict:
    try:
        datei = _state_root() / WARTEDATEI
        if not datei.exists():
            return {}
        return json.loads(datei.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("Wartende Orders nicht lesbar -- starte leer.", exc_info=True)
        return {}


def _speichern(daten: dict) -> None:
    try:
        from safe_persistence import best_effort_json
        best_effort_json(_state_root() / WARTEDATEI, daten,
                         label="wartende KI-Orders", durable=True)
    except Exception:
        logger.warning("Wartende Orders nicht speicherbar", exc_info=True)


def verfallszeit_minuten() -> float:
    """Wie lange eine Order hoechstens wartet.

    Nach oben begrenzt: eine Freigabe, die tagelang gilt, ist keine Freigabe
    fuer diese Entscheidung mehr.
    """
    try:
        wert = float(_cfg("AI_PENDING_EXPIRY_MINUTES", 15.0))
    except (TypeError, ValueError):
        wert = 15.0
    return min(240.0, max(1.0, wert))


def max_kursabweichung() -> float:
    """Zulaessige Kursdrift zwischen Pruefung und Absenden.

    Nach oben begrenzt: eine versehentlich eingetragene 100 wuerde die
    Kurspruefung stilllegen.
    """
    try:
        wert = float(_cfg("AI_PENDING_MAX_DRIFT_PCT", 0.003))
    except (TypeError, ValueError):
        wert = 0.003
    return min(0.05, max(0.0, wert))


def stelle_zurueck(*, broker: str, symbol: str, urteil: Urteil,
                   fakten: dict) -> dict:
    """Eine Order in den Wartezustand legen. Genau eine je Symbol."""
    eintrag = {
        "broker": str(broker).lower(),
        "symbol": str(symbol).upper(),
        "erstellt": datetime.now(timezone.utc).isoformat(),
        "verfaellt_um": time.time() + verfallszeit_minuten() * 60.0,
        "urteil": urteil.als_dict(),
        "fakten": fakten,
        "freigegeben": False,
    }
    schluessel = f"{eintrag['broker']}:{eintrag['symbol']}"
    with _lock:
        daten = _laden()
        ersetzt = schluessel in daten
        daten[schluessel] = eintrag
        _speichern(daten)
    eintrag["ersetzt_vorherige"] = ersetzt
    return eintrag


def offene(broker: str = "") -> list[dict]:
    """Alle noch gueltigen Wartezustaende. Abgelaufene werden entfernt."""
    jetzt = time.time()
    with _lock:
        daten = _laden()
        gueltig = {k: v for k, v in daten.items()
                   if float(v.get("verfaellt_um", 0) or 0) > jetzt}
        if len(gueltig) != len(daten):
            _speichern(gueltig)
    eintraege = list(gueltig.values())
    if broker:
        eintraege = [e for e in eintraege if e.get("broker") == str(broker).lower()]
    return eintraege


def abgelaufene() -> list[dict]:
    """Wartezustaende, die gerade verfallen sind -- fuer die Meldung."""
    jetzt = time.time()
    with _lock:
        daten = _laden()
        alt = [v for v in daten.values()
               if float(v.get("verfaellt_um", 0) or 0) <= jetzt]
        if alt:
            _speichern({k: v for k, v in daten.items()
                        if float(v.get("verfaellt_um", 0) or 0) > jetzt})
    return alt


def gib_frei(broker: str, symbol: str) -> Optional[dict]:
    """Eine wartende Order freigeben. Kauft NICHT -- hebt nur die Warnung auf."""
    schluessel = f"{str(broker).lower()}:{str(symbol).upper()}"
    with _lock:
        daten = _laden()
        eintrag = daten.get(schluessel)
        if eintrag is None:
            return None
        if float(eintrag.get("verfaellt_um", 0) or 0) <= time.time():
            daten.pop(schluessel, None)
            _speichern(daten)
            return None
        eintrag["freigegeben"] = True
        eintrag["freigegeben_am"] = datetime.now(timezone.utc).isoformat()
        daten[schluessel] = eintrag
        _speichern(daten)
    return eintrag


def verwerfe(broker: str, symbol: str) -> bool:
    schluessel = f"{str(broker).lower()}:{str(symbol).upper()}"
    with _lock:
        daten = _laden()
        weg = daten.pop(schluessel, None)
        if weg is not None:
            _speichern(daten)
    return weg is not None


def freigegebene(broker: str, symbol: str) -> Optional[dict]:
    """Liegt fuer dieses Symbol eine gueltige, freigegebene Order vor?"""
    schluessel = f"{str(broker).lower()}:{str(symbol).upper()}"
    with _lock:
        eintrag = _laden().get(schluessel)
    if not eintrag or not eintrag.get("freigegeben"):
        return None
    if float(eintrag.get("verfaellt_um", 0) or 0) <= time.time():
        verwerfe(broker, symbol)
        return None
    return eintrag


def grundlage_noch_gueltig(eintrag: dict, *, preis: float, cash: float,
                           bestand_vorhanden: bool, risiko_offen: bool,
                           menge: float | None = None,
                           stop: float | None = None) -> tuple[bool, str]:
    """Stimmen die Tatsachen noch, auf denen die Freigabe beruht?

    Die Freigabe hebt die KI-Warnung auf. Sie kann aber keine Tatsache
    aufheben, die sich inzwischen geaendert hat.
    """
    fakten = eintrag.get("fakten") or {}
    alter_preis = float(fakten.get("preis", 0) or 0)
    if alter_preis > 0 and preis > 0:
        drift = abs(preis - alter_preis) / alter_preis
        grenze = max_kursabweichung()
        if drift > grenze:
            return False, (f"Kurs ist um {drift * 100:.2f} % gewandert "
                           f"(erlaubt {grenze * 100:.2f} %): geprueft bei "
                           f"{alter_preis:g}, jetzt {preis:g}")
    benoetigt = float(fakten.get("wert", 0) or 0)
    if benoetigt > 0 and cash < benoetigt:
        return False, (f"Guthaben reicht nicht mehr: benoetigt {benoetigt:.2f}, "
                       f"frei {cash:.2f}")
    if bestand_vorhanden:
        return False, "Das Symbol wird inzwischen bereits gehalten"
    if not risiko_offen:
        return False, "Der Risikotopf laesst inzwischen keinen neuen Einstieg zu"
    alter_stop = float(fakten.get("stop", 0) or 0)
    if alter_stop > 0 and preis > 0 and preis <= alter_stop:
        return False, (f"Kurs {preis:g} liegt bereits unter dem geplanten Stop "
                       f"{alter_stop:g}")

    # Die Freigabe galt einer BESTIMMTEN Order. Der Nutzer hat eine Menge und
    # einen Gegenwert gesehen -- unter derselben Freigabe darf keine groessere
    # Order rausgehen, nur weil inzwischen mehr Cash oder mehr Tiefe da ist.
    alte_menge = float(fakten.get("menge", 0) or 0)
    if menge is not None and alte_menge > 0:
        jetzt = float(menge)
        if jetzt > alte_menge * 1.01:
            return False, (f"Menge waere jetzt {jetzt:g} statt der freigegebenen "
                           f"{alte_menge:g} -- bitte neu freigeben")
    if stop is not None and alter_stop > 0:
        neuer_stop = float(stop)
        if neuer_stop > 0 and abs(neuer_stop - alter_stop) / alter_stop > 0.01:
            return False, (f"Stop liegt jetzt bei {neuer_stop:g} statt bei "
                           f"{alter_stop:g} -- die Grundlage hat sich geaendert")
    return True, ""


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------
def rueckfragetext(eintrag: dict) -> str:
    """Die Telegram-Rueckfrage bei ``kritisch``."""
    fakten = eintrag.get("fakten") or {}
    urteil = eintrag.get("urteil") or {}
    waehrung = str(fakten.get("waehrung", ""))
    verfaellt = datetime.fromtimestamp(
        float(eintrag.get("verfaellt_um", 0) or 0)).astimezone().strftime("%H:%M")

    zeilen = [
        f"⚠️ KAUF WARTET AUF FREIGABE · {str(eintrag.get('broker', '')).upper()} "
        f"· {eintrag.get('symbol', '?')}",
        "",
        f"GPT-Einschaetzung: {str(urteil.get('einschaetzung', '')).upper()}",
        f"\"{urteil.get('begruendung', '')}\"",
        "",
        f"Menge {fakten.get('menge', '?')} zu {fakten.get('preis', '?')} {waehrung} "
        f"= {fakten.get('wert', '?')} {waehrung}",
        f"Stop {fakten.get('stop', '?')} · Ziel {fakten.get('ziel', '?')} "
        f"· CRV {fakten.get('chance_risiko', '?')}",
    ]
    kosten = fakten.get("kosten") or {}
    if kosten:
        zeilen.append(f"Kostenhuerde {kosten.get('huerde_pct', '?')} % · "
                      f"erwartete Bewegung {kosten.get('erwartete_bewegung_pct', '?')} %")
    risiken = urteil.get("risiken") or []
    if risiken:
        zeilen.append("")
        zeilen.extend(f"• {r}" for r in risiken[:3])
    zeilen += [
        "",
        "Die feste Kaskade hat diesen Kauf freigegeben.",
        f"Verfaellt um {verfaellt} Uhr, wenn du nicht antwortest.",
        "",
        "Freigeben:  /kaufen " + str(eintrag.get("symbol", "")),
        "Ablehnen:   /verwerfen " + str(eintrag.get("symbol", "")),
    ]
    return "\n".join(zeilen)


def warntext(urteil: Urteil, fakten: dict) -> str:
    """Kompakter, rein informativer Telegram-Hinweis bei KI-"kritisch"."""
    broker = str((fakten or {}).get("broker", "OKX")).upper()
    symbol = str((fakten or {}).get("symbol", "?")).upper()
    zeilen = [f"⚠️ KI-HINWEIS · {broker} · {symbol}",
              f"Einschätzung: KRITISCH – {urteil.begruendung or 'kein Detail'}"]
    risiken = list(urteil.risiken or []) + list(urteil.widersprueche or [])
    for risiko in risiken[:3]:
        zeilen.append(f"• {risiko}")
    zeilen.append("Die feste Kaufkaskade bleibt maßgeblich; diese Warnung hält die Order nicht auf.")
    return "\n".join(zeilen)


__all__ = [
    "UNAUFFAELLIG", "VORSICHTIG", "KRITISCH", "NICHT_ERREICHBAR",
    "AUS", "NUR_LIVE", "IMMER", "SCHEMA", "ANWEISUNG",
    "Urteil", "hole", "aktiv", "modus", "human_gate_aktiv",
    "stelle_zurueck", "offene", "abgelaufene", "gib_frei", "verwerfe",
    "freigegebene", "grundlage_noch_gueltig", "rueckfragetext", "warntext",
    "verfallszeit_minuten", "max_kursabweichung",
]
