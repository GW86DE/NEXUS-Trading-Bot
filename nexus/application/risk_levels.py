"""Einsatzstufe je Broker -- zur Laufzeit umstellbar, ohne Neustart.

WARUM ES DIESES MODUL GIBT (10.6.0)
-----------------------------------
Bis 10.5.0 bestimmte das Risikoprofil (konservativ/ausgewogen/offensiv) den
Einsatz je Trade fuer BEIDE Broker gemeinsam -- theoretisch. Praktisch galt
es nur fuer Aktien:

``TopfGrenzen.fuer_broker()`` bevorzugt einen Wert mit Brokerpraefix vor dem
allgemeinen Wert. In config.py stehen feste ``OKX_RISK_PER_TRADE_PCT`` und
``OKX_MAX_POSITION_PCT``. Das Profil schreibt aber nur ``RISK_PER_TRADE_PCT``
und ``CRYPTO_RISK_PER_TRADE_PCT``. Der OKX-Topf hat den Profilwert damit nie
gesehen: Ein Wechsel auf OFFENSIV meldete 2,00 % je Trade und handelte
weiter mit 0,3 %. Die Krypto-Engine kennt weder ``ACTIVE_PROFILE`` noch
``CRYPTO_RISK_PER_TRADE_PCT`` -- beide Namen kommen dort nicht vor.

Dieses Modul macht den Einsatz zu einer eigenen, je Broker getrennten
Entscheidung mit genau drei Stufen. Es ersetzt fuer Einsatz und
Positionsdeckel die Profilwerte; alles andere (ATR-Abstaende, RSI-Schwellen,
Tagesverlustgrenze, Positions- und Tradelimits) bleibt beim Profil.

KEINE STILLE VERHALTENSAENDERUNG
--------------------------------
Solange niemand eine Stufe waehlt, aendert dieses Modul GAR NICHTS. Es
meldet dann ``chosen=False``, und Topf und Aktienpfad rechnen unveraendert
mit ihren bisherigen Werten aus Konfiguration und Profil. Erst eine
ausdrueckliche Wahl setzt Einsatz und Positionsdeckel neu.

Damit die Oberflaeche nicht behauptet, es sei eine Stufe aktiv, wenn keine
gewaehlt wurde, liefert ``effective()`` immer die TATSAECHLICH wirksamen
Zahlen -- gewaehlte Stufe oder Basiswert. Anzeige und Entscheidung lesen
dieselbe Funktion.

FAIL-SAFE
---------
Eine unlesbare oder nachtraeglich verschwundene Datei fuehrt NIE zu einem
groesseren Einsatz. In diesem Fall gilt die kleinste Stufe. Das ist die
einzige Richtung, in der ein Fehler nicht teuer wird. Gesperrt wird nichts:
Der Handel laeuft weiter, nur kleiner.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading

import config
from nexus.pfade import zustandsordner
from safe_persistence import atomic_write_json

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()

VORSICHTIG = "vorsichtig"
MITTEL = "mittel"
ERHOEHT = "erhoeht"
VALID_LEVELS = (VORSICHTIG, MITTEL, ERHOEHT)
VALID_BROKERS = ("okx", "etoro")

# Reihenfolge = Rangfolge. Der erste Eintrag ist die kleinste Stufe und damit
# der Rueckfall bei jedem Zweifel.
LEVELS: dict[str, dict[str, dict]] = {
    "okx": {
        VORSICHTIG: {
            "label": "Vorsichtig",
            "risiko_pro_trade_pct": 0.003,
            "max_position_pct": 0.05,
            "beschreibung": (
                "0,3 % Risiko je Trade, hoechstens 5 % je Coin. Der bisherige "
                "Wert. Ein Fehlschlag kostet wenig."),
        },
        MITTEL: {
            "label": "Mittel",
            "risiko_pro_trade_pct": 0.006,
            "max_position_pct": 0.10,
            "beschreibung": (
                "0,6 % Risiko je Trade, hoechstens 10 % je Coin. Doppelter "
                "Einsatz: Gewinn und Verlust wachsen im selben Verhaeltnis."),
        },
        ERHOEHT: {
            "label": "Erhoeht",
            "risiko_pro_trade_pct": 0.012,
            "max_position_pct": 0.20,
            "beschreibung": (
                "1,2 % Risiko je Trade, hoechstens 20 % je Coin. Vierfacher "
                "Einsatz. Nur sinnvoll, wenn die Strategie nachweislich "
                "funktioniert -- sonst verliert sie nur schneller."),
        },
    },
    "etoro": {
        VORSICHTIG: {
            "label": "Vorsichtig",
            "risiko_pro_trade_pct": 0.005,
            "max_position_pct": 0.03,
            "beschreibung": (
                "0,5 % Risiko je Trade, hoechstens 3 % je Aktie. Entspricht "
                "dem Profil KONSERVATIV."),
        },
        MITTEL: {
            "label": "Mittel",
            "risiko_pro_trade_pct": 0.01,
            "max_position_pct": 0.05,
            "beschreibung": (
                "1,0 % Risiko je Trade, hoechstens 5 % je Aktie. Entspricht "
                "dem Profil AUSGEWOGEN."),
        },
        ERHOEHT: {
            "label": "Erhoeht",
            "risiko_pro_trade_pct": 0.02,
            "max_position_pct": 0.15,
            "beschreibung": (
                "2,0 % Risiko je Trade, hoechstens 15 % je Aktie. Entspricht "
                "dem Profil OFFENSIV. Vier Fehlschlaege in Folge kosten rund "
                "8 % des Kontos."),
        },
    },
}

# Welche Stufe entspricht welchem Profil? Nur fuer den Erststart, solange
# niemand eine Stufe gewaehlt hat.
_PROFIL_STUFE = {"konservativ": VORSICHTIG, "ausgewogen": MITTEL, "offensiv": ERHOEHT}

# Die Basis, auf die sich die Prozente beziehen. Sie unterscheidet sich je
# Broker und ist der haeufigste Grund fuer Missverstaendnisse, deshalb steht
# sie ausdruecklich in der Oberflaeche.
BEZUGSGROESSE = {
    "okx": ("freies Guthaben der Waehrung, in der gekauft wird "
            "(EUR-Kauf rechnet nur mit EUR-Cash, nicht mit dem Gesamtkonto)"),
    "etoro": "Kontowert des eToro-Kontos",
}


def _root() -> Path:
    # 10.8.0: Modul liegt in nexus/application/; die Stufendatei bleibt im
    # Projektordner (siehe nexus/pfade.py).
    return zustandsordner()


def path() -> Path:
    return _root() / getattr(config, "RISK_LEVEL_FILE", "risiko_stufen.json")


def _markierung() -> Path:
    """Merkt, dass die Stufendatei schon einmal geschrieben wurde."""
    return _root() / ".risiko_stufen.seen"


def _je_geschrieben() -> bool:
    try:
        return _markierung().exists()
    except OSError:
        return False


def normalise_broker(broker: str) -> str:
    key = str(broker or "").strip().lower()
    if key not in VALID_BROKERS:
        raise ValueError(f"unbekannter Broker fuer Einsatzstufe: {broker!r}")
    return key


def profil_stufe(broker: str) -> str:
    """Die Stufe, die den bisherigen Werten dieses Brokers entspricht.

    Nur ein Vorschlag fuer die Oberflaeche. Sie wird NICHT automatisch
    aktiv: OKX hatte eigene, profilunabhaengige Werte (entspricht
    ``vorsichtig``), bei eToro wirkte das Profil.
    """
    key = normalise_broker(broker)
    if key == "okx":
        return VORSICHTIG
    profil = str(getattr(config, "ACTIVE_PROFILE", "") or "").strip().lower()
    return _PROFIL_STUFE.get(profil, MITTEL)


def _leer() -> dict:
    return {"schema": 1, "broker": {}, "history": []}


def _lies() -> dict:
    """Rohzustand der Datei. Wirft nie; meldet Fehler ueber 'error'."""
    if not path().exists():
        if _je_geschrieben():
            # Dieselbe Vorsicht wie beim Strategiemodus: Wer die Datei
            # entfernt, hebt keine Entscheidung still auf. Anders als dort
            # wird hier nicht pausiert, sondern verkleinert.
            return {**_leer(), "error": "Stufendatei fehlt, war aber vorhanden"}
        return _leer()
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("broker"), dict):
            raise ValueError("ungueltiger Aufbau")
        return {**_leer(), **data}
    except Exception as exc:
        return {**_leer(), "error": f"Stufendatei unlesbar: {type(exc).__name__}"}


def status(broker: str) -> dict:
    """Gewaehlte Stufe eines Brokers -- oder die Auskunft, dass keine gilt.

    ``chosen=False`` heisst ausdruecklich: dieses Modul greift nicht ein.
    Wer die wirksamen Zahlen braucht, nimmt ``effective()``.
    """
    key = normalise_broker(broker)
    with _LOCK:
        roh = _lies()
    fehler = str(roh.get("error") or "")
    eintrag = roh.get("broker", {}).get(key)
    if isinstance(eintrag, dict) and str(eintrag.get("level")) in VALID_LEVELS:
        stufe = str(eintrag["level"])
        quelle = str(eintrag.get("source") or "")
        revision = int(eintrag.get("revision") or 0)
    else:
        stufe = ""
        quelle = "" if not fehler else "fail-safe"
        revision = 0
    if fehler:
        # Unlesbar oder nachtraeglich entfernt: kleinste Stufe, niemals mehr
        # Einsatz. Der Handel laeuft weiter, nur kleiner.
        stufe = VORSICHTIG
        quelle = "fail-safe"
    werte = LEVELS[key][stufe] if stufe else None
    return {
        "broker": key,
        "level": stufe,
        "label": (werte["label"] if werte else "keine Stufe gewaehlt"),
        "risiko_pro_trade_pct": (float(werte["risiko_pro_trade_pct"]) if werte else None),
        "max_position_pct": (float(werte["max_position_pct"]) if werte else None),
        "beschreibung": (werte["beschreibung"] if werte else ""),
        "bezugsgroesse": BEZUGSGROESSE[key],
        "chosen": bool(stufe) and not fehler,
        "vorschlag": profil_stufe(key),
        "revision": revision,
        "source": quelle,
        "updated_at_utc": str((eintrag or {}).get("updated_at_utc") or ""),
        "error": fehler,
    }


def effective(broker: str, basis_risiko_pct: float,
              basis_max_position_pct: float) -> dict:
    """Die tatsaechlich wirksamen Zahlen -- eine Quelle fuer alle Leser.

    Ohne gewaehlte Stufe kommen die uebergebenen Basiswerte unveraendert
    zurueck. Anzeige, Sizing und Diagnose rufen genau diese Funktion, damit
    keine Oberflaeche etwas anderes behauptet als der Kaufpfad rechnet.
    """
    row = status(broker)
    if row["chosen"] or row["error"]:
        return {"risiko_pro_trade_pct": float(row["risiko_pro_trade_pct"]),
                "max_position_pct": float(row["max_position_pct"]),
                "level": row["level"], "label": row["label"],
                "quelle": ("fail-safe" if row["error"] else "stufe"),
                "chosen": bool(row["chosen"]), "error": row["error"]}
    return {"risiko_pro_trade_pct": float(basis_risiko_pct),
            "max_position_pct": float(basis_max_position_pct),
            "level": "", "label": "Vorgabe (keine Stufe gewaehlt)",
            "quelle": "vorgabe", "chosen": False, "error": ""}


def current_level(broker: str) -> str:
    """Die gewaehlte Stufe, oder leer, wenn keine gewaehlt wurde."""
    return str(status(broker)["level"])


def set_level(broker: str, level: str, *, source: str, reason: str = "",
              notify: bool = True) -> dict:
    """Stufe atomar setzen. Wirkt ab dem naechsten Pruefzyklus, ohne Neustart."""
    key = normalise_broker(broker)
    gewaehlt = str(level or "").strip().lower()
    if gewaehlt not in VALID_LEVELS:
        raise ValueError(f"ungueltige Einsatzstufe: {level!r}")
    with _LOCK:
        roh = _lies()
        vorher = status(key)
        jetzt = datetime.now(timezone.utc).isoformat()
        revision = int(vorher.get("revision") or 0) + 1
        ereignis = {"broker": key, "revision": revision, "changed_at_utc": jetzt,
                    "previous": str(vorher.get("level") or ""), "level": gewaehlt,
                    "source": str(source or "unbekannt")[:80],
                    "reason": str(reason or "")[:300]}
        broker_rows = dict(roh.get("broker") or {})
        broker_rows[key] = {"level": gewaehlt, "revision": revision,
                            "updated_at_utc": jetzt,
                            "source": ereignis["source"],
                            "reason": ereignis["reason"]}
        neu = {"schema": 1, "broker": broker_rows,
               "history": list(roh.get("history") or [])[-99:] + [ereignis]}
        atomic_write_json(path(), neu)
        try:
            _markierung().write_text(jetzt, encoding="utf-8")
        except OSError:
            logger.debug("Stufen-Markierung nicht schreibbar", exc_info=True)
    ergebnis = status(key)
    if notify:
        try:
            from notifier import send_telegram
            send_telegram(
                f"⚖️ Einsatzstufe {key.upper()}: "
                f"{vorher['label']} → {ergebnis['label']}. "
                f"{ergebnis['risiko_pro_trade_pct'] * 100:.2f} % Risiko je Trade, "
                f"hoechstens {ergebnis['max_position_pct'] * 100:.0f} % je Position. "
                "Gilt ab dem naechsten Pruefzyklus und nur fuer NEUE Einstiege; "
                "offene Positionen behalten ihre Groesse.",
                event_id=f"risk-level:{key}:{ergebnis['revision']}:{ergebnis['updated_at_utc']}",
            )
        except Exception:
            logger.debug("Stufenwechsel-Telegram nicht zustellbar", exc_info=True)
    return ergebnis


def basiswerte(broker: str) -> tuple[float, float]:
    """Die Werte, die ohne gewaehlte Stufe gelten -- aus Konfiguration/Profil.

    Genau die Quelle, aus der auch der Risikotopf liest. Fuer eToro sind das
    die Profilwerte, fuer OKX die festen OKX_-Werte.
    """
    key = normalise_broker(broker)
    from risk_grenzen import TopfGrenzen
    grenzen = TopfGrenzen.fuer_broker(key)
    return (float(grenzen.risiko_pro_trade_pct), float(grenzen.max_position_pct))


def overview() -> dict:
    """Alle Broker fuer Anzeige und Diagnose -- eine einzige Quelle."""
    rows = {}
    for key in VALID_BROKERS:
        row = status(key)
        try:
            basis_risiko, basis_max = basiswerte(key)
        except Exception:
            logger.debug("Basiswerte fuer %s nicht lesbar", key, exc_info=True)
            basis_risiko, basis_max = (0.0, 0.0)
        wirksam = effective(key, basis_risiko, basis_max)
        row["wirksam"] = wirksam
        row["basis_risiko_pro_trade_pct"] = basis_risiko
        row["basis_max_position_pct"] = basis_max
        row["options"] = [
            {"level": name,
             "label": LEVELS[key][name]["label"],
             "beschreibung": LEVELS[key][name]["beschreibung"],
             "risiko_pro_trade_pct": float(LEVELS[key][name]["risiko_pro_trade_pct"]),
             "max_position_pct": float(LEVELS[key][name]["max_position_pct"]),
             "aktiv": bool(name == row["level"])}
            for name in VALID_LEVELS
        ]
        rows[key] = row
    return rows


def klartext(broker: str) -> str:
    key = normalise_broker(broker)
    row = status(key)
    try:
        basis_risiko, basis_max = basiswerte(key)
    except Exception:
        basis_risiko, basis_max = (0.0, 0.0)
    wirksam = effective(key, basis_risiko, basis_max)
    hinweis = " | FAIL-SAFE: " + row["error"] if row["error"] else ""
    return (f"{key.upper()} Einsatz: {wirksam['label']} | "
            f"Risiko/Trade {wirksam['risiko_pro_trade_pct'] * 100:.2f} % | "
            f"max. Position {wirksam['max_position_pct'] * 100:.0f} % | "
            f"Basis: {BEZUGSGROESSE[key]}{hinweis}")


__all__ = [
    "BEZUGSGROESSE", "ERHOEHT", "LEVELS", "MITTEL", "VALID_BROKERS",
    "VALID_LEVELS", "VORSICHTIG", "basiswerte", "current_level", "effective",
    "klartext", "normalise_broker", "overview", "path", "profil_stufe",
    "set_level", "status",
]
