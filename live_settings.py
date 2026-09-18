"""Sofort wirksame News- und Datenquellen-Einstellungen (neu in v8.1.2).

DAS PROBLEM
===========
config.py liest die Zugangsdateien EINMAL beim Import. Die WebUI schreibt
einen umgelegten Schalter zwar korrekt in die Datei, aber kein laufender
Prozess sieht das:

    Datei      enabled.finnhub = true      (WebUI hat gespeichert)
    Prozess    Finnhub konfiguriert = False (config-Wert vom Start)

Fuer den Nutzer sieht das so aus, als liesse sich die Quelle nicht
einschalten -- und der Testknopf antwortet "nicht aktiviert oder Zugang
fehlt", obwohl Schluessel und Schalter gesetzt sind. Genau daran sind die
News in v8.1.1 gescheitert; keine der drei APIs war defekt.

DIE LOESUNG
===========
Dieses Modul liest die betroffenen Dateien bei jedem Zugriff frisch, mit
einem kurzen Zwischenspeicher gegen Dauerlast. Aenderungen wirken damit
sofort, ohne Neustart von Core oder WebUI.

BEWUSSTE ABGRENZUNG
===================
Hier laufen AUSSCHLIESSLICH Nachrichten- und Referenzquellen durch:

    news_sources_credentials.json   Finnhub, FMP, SEC, Schalter aller Quellen
    alpha_vantage_credentials.json  Alpha Vantage
    massive_credentials.json        MASSIVE

Broker-Zugangsdaten, Handelsmodus, LIVE-Arming, Risikogrenzen und
Universumsparameter werden hier NICHT angefasst. Diese Werte duerfen sich
nicht mitten in einem Handelszyklus aendern -- eine Positionsgroesse, die
zwischen Berechnung und Order eine andere Grenze bekommt, waere ein
Sicherheitsproblem. Sie bleiben deshalb bewusst beim Neustart-Verhalten.

AUSNAHME SEIT v8.1.5: Datenfrische-Schwellen
============================================
    handel_settings.json            erlaubtes Kursalter (Aktien, Krypto)

Diese Schwellen sind bewusst zugelassen, weil sie zu keiner der obigen
Gefahren gehoeren: sie entscheiden nur, ob ein Kurs alt genug ist, um ihn
NICHT zu benutzen. Eine Aenderung kann einen Einstieg zusaetzlich sperren
oder wieder erlauben -- sie kann keine bereits berechnete Position
vergroessern, keinen Stop verschieben und keine Risikogrenze aufweichen.
Der schlimmste Fall ist ein Zyklus mit der alten und der naechste mit der
neuen Grenze. Wer hier weitere Schluessel ergaenzen will, muss zuerst diese
Frage beantworten koennen: Kann der neue Wert eine Order groesser, teurer
oder ungeschuetzter machen? Wenn ja, gehoert er nicht hierher.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from credential_store import load_credentials, secure_path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent

# So lange gilt ein einmal gelesener Dateistand als frisch. Kurz genug,
# dass ein Klick in der WebUI sofort wirkt; lang genug, dass ein Scan ueber
# 300 Instrumente die Datei nicht 300-mal liest.
CACHE_SEKUNDEN = 2.0

# Standardzustand jeder Quelle, wenn in der Datei nichts steht.
STANDARD_QUELLEN = {
    "sec_edgar": True,
    "gdelt": True,
    "yahoo_finance": True,
    "google_news": True,
    "nasdaq_halts": True,
    "federal_reserve": True,
    "alpha_vantage": False,
    "finnhub": False,
    "fmp": False,
}

# Alle Quellenschluessel, die die Oberflaeche schalten darf.
ALLE_QUELLEN = tuple(STANDARD_QUELLEN) + ("massive",)

_CACHE: dict[str, tuple[float, float, dict]] = {}
_LOCK = threading.RLock()


def _mtime(name: str) -> float:
    """Aenderungszeit der Datei -- auch der verschluesselten Windows-Variante."""
    neuste = 0.0
    for pfad in (ROOT / name, secure_path(ROOT / name)):
        try:
            neuste = max(neuste, pfad.stat().st_mtime)
        except OSError:
            continue
    return neuste


def lies(name: str) -> dict:
    """Liest eine Zugangsdatei mit kurzem Zwischenspeicher.

    Der Zwischenspeicher wird sowohl nach Zeit als auch bei geaenderter
    Dateizeit verworfen. Dadurch wirkt ein Speichern in der WebUI sofort,
    selbst innerhalb der Cachezeit.
    """
    import time
    jetzt = time.time()
    aktuelle_mtime = _mtime(name)
    with _LOCK:
        eintrag = _CACHE.get(name)
        if eintrag is not None:
            gelesen_am, gesehene_mtime, daten = eintrag
            if (jetzt - gelesen_am) < CACHE_SEKUNDEN and gesehene_mtime == aktuelle_mtime:
                return daten
    daten = load_credentials(ROOT / name, {}) or {}
    if not isinstance(daten, dict):
        daten = {}
    with _LOCK:
        _CACHE[name] = (jetzt, aktuelle_mtime, daten)
    return daten


def verwerfe_cache() -> None:
    """Erzwingt beim naechsten Zugriff ein frisches Lesen (fuer Tests/Speichern)."""
    with _LOCK:
        _CACHE.clear()


def telegram_runtime() -> dict:
    """Aktueller Telegram-Zustand aus Datei/Umgebung ohne Prozessneustart.

    Explizite Umgebungsvariablen behalten Vorrang. Ansonsten ist die von der
    WebUI atomisch geschriebene Zugangsdatei die Laufzeitquelle.
    """
    import config
    daten = lies(getattr(config, "TELEGRAM_CREDENTIALS_FILE",
                         "telegram_credentials.json"))

    def env_oder_datei(env_name: str, file_name: str, fallback: str = "") -> str:
        if env_name in os.environ:
            return str(os.environ.get(env_name) or "").strip()
        return str(daten.get(file_name, fallback) or "").strip()

    token = env_oder_datei("TELEGRAM_BOT_TOKEN", "bot_token",
                           getattr(config, "TELEGRAM_BOT_TOKEN", ""))
    chat_id = env_oder_datei("TELEGRAM_CHAT_ID", "chat_id",
                             getattr(config, "TELEGRAM_CHAT_ID", ""))
    user_id = env_oder_datei(
        "TELEGRAM_ALLOWED_USER_ID", "user_id",
        getattr(config, "TELEGRAM_ALLOWED_USER_ID", "") or chat_id)
    mode = env_oder_datei(
        "TELEGRAM_NOTIFICATION_MODE", "notification_mode",
        getattr(config, "TELEGRAM_NOTIFICATION_MODE", "ON")).upper()
    if mode not in {"ON", "SILENT", "OFF"}:
        mode = "ON"
    if "NOTIFY_TELEGRAM" in os.environ:
        enabled = os.environ.get("NOTIFY_TELEGRAM", "").strip().lower() in {
            "1", "true", "yes", "on"}
    elif "enabled" in daten:
        enabled = bool(daten.get("enabled"))
    else:
        enabled = bool(getattr(config, "NOTIFY_TELEGRAM", False))
    return {"enabled": enabled, "token": token, "chat_id": chat_id,
            "user_id": user_id or chat_id, "notification_mode": mode,
            "configured": bool(token and chat_id)}


# ---------------------------------------------------------------------------
# Schalter
# ---------------------------------------------------------------------------
# Wenn die Zugangsdatei zu einer Quelle nichts sagt, gilt der Wert aus
# config.py. Damit bleiben Umgebungsvariablen, Dienst-Konfigurationen und
# bestehende Tests unveraendert gueltig -- die Datei hat nur Vorrang, WENN
# sie eine Aussage enthaelt.
_CONFIG_SCHALTER = {
    "sec_edgar": "NEWS_SOURCE_SEC_ENABLED",
    "gdelt": "NEWS_SOURCE_GDELT_ENABLED",
    "yahoo_finance": "NEWS_SOURCE_YAHOO_ENABLED",
    "google_news": "NEWS_SOURCE_GOOGLE_NEWS_ENABLED",
    "nasdaq_halts": "NEWS_SOURCE_NASDAQ_HALTS_ENABLED",
    "federal_reserve": "NEWS_SOURCE_FEDERAL_RESERVE_ENABLED",
    "alpha_vantage": "NEWS_SOURCE_ALPHA_VANTAGE_ENABLED",
    "finnhub": "NEWS_SOURCE_FINNHUB_ENABLED",
    "fmp": "NEWS_SOURCE_FMP_ENABLED",
    "massive": "MASSIVE_ENABLED",
}


def _aus_config(schluessel: str, standard: bool) -> bool:
    name = _CONFIG_SCHALTER.get(schluessel)
    if not name:
        return standard
    try:
        import config
    except Exception:
        return standard
    return bool(getattr(config, name, standard))


def quelle_aktiv(schluessel: str) -> bool:
    """Ist diese Nachrichtenquelle eingeschaltet? Sofort wirksam.

    Reihenfolge: Zugangsdatei (falls sie etwas sagt) vor config.py.
    """
    schluessel = str(schluessel).strip().lower()
    # v8.2: finanzen.net blockiert automatisierte RSS-Aufrufe wiederholt.
    # Ein aus einer Vorgängerversion übernommener Schalter darf ihn deshalb
    # nicht versehentlich wieder in den Scan bringen.
    if schluessel == "finanzen_net":
        return False
    if schluessel == "massive":
        daten = lies("massive_credentials.json")
        if "enabled" in daten:
            return bool(daten.get("enabled"))
        return _aus_config("massive", False)

    daten = lies("news_sources_credentials.json")
    schalter = daten.get("enabled", {})
    if not isinstance(schalter, dict):
        schalter = {}
    if schluessel in schalter:
        return bool(schalter[schluessel])
    return _aus_config(schluessel, bool(STANDARD_QUELLEN.get(schluessel, False)))


def alle_schalter() -> dict[str, bool]:
    return {name: quelle_aktiv(name) for name in ALLE_QUELLEN}


# ---------------------------------------------------------------------------
# Schluessel
# ---------------------------------------------------------------------------
def _schluessel(env_name: str, wert: Any, config_name: str) -> str:
    """Reihenfolge: Umgebungsvariable, dann Zugangsdatei, dann config.py.

    Der config-Rueckfall ist wichtig: er haelt Dienst-Setups und bestehende
    Tests gueltig, in denen der Schluessel gar nicht aus einer Datei kommt.
    """
    aus_umgebung = os.getenv(env_name, "").strip()
    if aus_umgebung:
        return aus_umgebung
    aus_datei = str(wert or "").strip()
    if aus_datei:
        return aus_datei
    try:
        import config
    except Exception:
        return ""
    return str(getattr(config, config_name, "") or "").strip()


def finnhub_key() -> str:
    return _schluessel("FINNHUB_API_KEY",
                       lies("news_sources_credentials.json").get("finnhub_api_key", ""),
                       "FINNHUB_API_KEY")


def fmp_key() -> str:
    return _schluessel("FMP_API_KEY",
                       lies("news_sources_credentials.json").get("fmp_api_key", ""),
                       "FMP_API_KEY")


def alpha_vantage_key() -> str:
    return _schluessel("ALPHAVANTAGE_API_KEY",
                       lies("alpha_vantage_credentials.json").get("api_key", ""),
                       "ALPHAVANTAGE_API_KEY")


def massive_key() -> str:
    return _schluessel("MASSIVE_API_KEY",
                       lies("massive_credentials.json").get("api_key", ""),
                       "MASSIVE_API_KEY")


def sec_kontakt() -> str:
    return _schluessel("SEC_USER_AGENT_EMAIL",
                       lies("news_sources_credentials.json").get("sec_contact_email", ""),
                       "SEC_USER_AGENT_EMAIL")


def massive_tageslimit() -> int:
    try:
        return max(0, int(lies("massive_credentials.json").get("daily_limit", 0) or 0))
    except (TypeError, ValueError):
        return 0


def ai_second_opinion() -> dict:
    """Einstellungen der GPT Second Opinion -- sofort wirksam.

    Wie die Nachrichtenschalter seit v8.1.2 liest diese Funktion die Datei
    frisch (mit kurzem Zwischenspeicher), damit eine Aenderung in der WebUI
    ohne Neustart greift.
    """
    werte = lies("second_opinion_settings.json")
    if not werte:
        return {}
    aus = {}
    if "mode" in werte:
        aus["AI_SECOND_OPINION_MODE"] = str(werte["mode"])
    if "critical_requires_approval" in werte:
        aus["AI_CRITICAL_HUMAN_GATE"] = bool(werte["critical_requires_approval"])
    for datei_name, config_name in (
            ("timeout_seconds", "AI_SECOND_OPINION_TIMEOUT_SECONDS"),
            ("daily_limit", "AI_SECOND_OPINION_DAILY_LIMIT"),
            ("cooldown_minutes", "AI_SECOND_OPINION_COOLDOWN_MINUTES"),
            ("pending_expiry_minutes", "AI_PENDING_EXPIRY_MINUTES"),
            ("max_drift_pct", "AI_PENDING_MAX_DRIFT_PCT")):
        if datei_name in werte:
            aus[config_name] = werte[datei_name]
    return aus


def handelsschwellen() -> dict:
    """Schwellen, die Georg ohne Neustart nachziehen koennen muss (v8.1.5).

    Ausloeser war der 25.08.2026: eToro lieferte fuer GOOGL einen 5 h alten
    Kurs, der Bot sperrte den Einstieg korrekt -- aber die Grenze von 180 s
    stand nur in einer Umgebungsvariablen. Wer sie anpassen wollte, musste an
    die Datei und neu starten. Jetzt steht sie in der Oberflaeche.
    """
    werte = lies("handel_settings.json")
    if not werte:
        return {}
    aus = {}
    for datei_name, config_name in (
            ("market_session_quote_max_age_seconds", "MARKET_SESSION_QUOTE_MAX_AGE_SECONDS"),
            ("crypto_ticker_max_age_seconds", "CRYPTO_TICKER_MAX_AGE_SECONDS")):
        if datei_name in werte:
            try:
                aus[config_name] = float(werte[datei_name])
            except (TypeError, ValueError):
                logger.warning("Ungueltiger Wert fuer %s in handel_settings.json: %r",
                               config_name, werte[datei_name])
    return aus


def kursalter_grenze() -> float:
    """Erlaubtes Kursalter fuer Aktien-Neueinstiege, in Sekunden."""
    wert = handelsschwellen().get(
        "MARKET_SESSION_QUOTE_MAX_AGE_SECONDS",
        getattr(_config_modul(), "MARKET_SESSION_QUOTE_MAX_AGE_SECONDS", 180.0))
    try:
        return max(5.0, float(wert))
    except (TypeError, ValueError):
        return 180.0


def _config_modul():
    import config as _c
    return _c


def fmp_tageslimit() -> int:
    """Anfragen pro Tag. Der FMP-Gratistarif erlaubt 250."""
    daten = lies("news_sources_credentials.json")
    try:
        wert = int(daten.get("fmp_daily_limit", 0) or 0)
    except (TypeError, ValueError):
        wert = 0
    return wert if wert > 0 else 250


# ---------------------------------------------------------------------------
# Uebersicht
# ---------------------------------------------------------------------------
def schluessel_vorhanden() -> dict[str, bool]:
    return {
        "finnhub": bool(finnhub_key()),
        "fmp": bool(fmp_key()),
        "alpha_vantage": bool(alpha_vantage_key()),
        "massive": bool(massive_key()),
        "sec_edgar": bool(sec_kontakt()),
    }


def snapshot() -> dict:
    """Kompletter Zustand fuer Oberflaeche und Diagnose -- ohne Schluessel."""
    return {
        "schalter": alle_schalter(),
        "schluessel_vorhanden": schluessel_vorhanden(),
        "fmp_tageslimit": fmp_tageslimit(),
        "massive_tageslimit": massive_tageslimit(),
        "cache_sekunden": CACHE_SEKUNDEN,
    }


__all__ = [
    "quelle_aktiv", "alle_schalter", "schluessel_vorhanden", "snapshot",
    "finnhub_key", "fmp_key", "alpha_vantage_key", "massive_key", "sec_kontakt",
    "massive_tageslimit", "fmp_tageslimit", "lies", "verwerfe_cache",
    "ALLE_QUELLEN", "STANDARD_QUELLEN", "CACHE_SEKUNDEN",
]


NEWS_RULE_DEFAULTS = {"NEWS_BLOCK_SCORE": 5, "NEWS_BLOCK_SCORE_UNDERDOG": 3,
                      "NEWS_EXIT_THRESHOLD": 4, "NEWS_MARKET_THRESHOLD": 8,
                      "NEWS_MAX_SIGNAL_REPETITIONS": 2}

def news_rules() -> dict:
    raw = lies("news_rules_settings.json")
    result = {}
    for name, default in NEWS_RULE_DEFAULTS.items():
        value = raw.get(name, getattr(_config_modul(), name, default))
        try:
            number = int(value)
            if isinstance(value, bool) or float(value) != number or not 1 <= number <= 100:
                raise ValueError(name)
            result[name] = number
        except (TypeError, ValueError, OverflowError):
            result[name] = default
    return result

def news_rule(name, default):
    return news_rules().get(name, getattr(_config_modul(), name, default))
