"""Zentrale Log-Einrichtung fuer TradingBot 8.1.1 NEXUS.

HINTERGRUND (Fehler aus v6.0 Claude)
=================================
live_trader.py importierte configure_library_logging und configure_root_logging,
das Modul stellte aber nur configure_log_hygiene bereit. Der Bot liess sich
dadurch UEBERHAUPT NICHT starten -- ImportError beim Laden des Hauptmoduls.

Der Volltest fand das nicht, weil compileall nur die Syntax prueft und keine
Testreihe das Hauptmodul importierte. Beides ist in 6.0 behoben: Die Funktionen
existieren, und ein neuer Importtest laedt jedes Modul tatsaechlich.

configure_log_hygiene bleibt als Aliasname erhalten, damit aeltere Aufrufe
weiterhin funktionieren.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Bibliotheken, die im Normalbetrieb nur Rauschen erzeugen. urllib3 meldet
# sonst jede einzelne Verbindung, was auf dem Pi die SD-Karte belastet.
_LAUTE_BIBLIOTHEKEN = (
    "urllib3.connectionpool",
    "requests.packages.urllib3.connectionpool",
    "yfinance",
    "peewee",
    "matplotlib",
    "PIL",
)


class BerlinFormatter(logging.Formatter):
    """Zeitstempel unabhaengig von der Prozess-/systemd-Zeitzone ausgeben."""

    def formatTime(self, record, datefmt=None):
        try:
            zone = ZoneInfo(os.getenv("TRADINGBOT_TIMEZONE", "Europe/Berlin"))
        except Exception:
            zone = ZoneInfo("Europe/Berlin")
        value = datetime.fromtimestamp(record.created, tz=zone)
        return value.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def configure_library_logging(level: int = logging.WARNING) -> None:
    """Daempft gespraechige Fremdbibliotheken auf ein sinnvolles Mass."""
    for name in _LAUTE_BIBLIOTHEKEN:
        try:
            logging.getLogger(name).setLevel(level)
        except Exception:
            logging.getLogger(__name__).debug("Loggerpegel %s nicht setzbar", name)


def configure_root_logging(log_file: str,
                           level: str = "INFO",
                           *,
                           max_bytes: int = 5 * 1024 * 1024,
                           backups: int = 5,
                           stdout: bool = False) -> None:
    """
    Richtet die Protokollierung ein: rotierende Datei, optional zusaetzlich
    die Standardausgabe.

    Rotation ist auf dem Raspberry Pi wichtig: Ohne sie waechst die Datei
    unbegrenzt und fuellt die SD-Karte. Mit 5 MB und 5 Sicherungen ist der
    Verbrauch auf rund 30 MB begrenzt.

    stdout=False ist der Normalfall im Dienstbetrieb. Sonst wuerde jede Zeile
    zusaetzlich im systemd-Journal landen -- also doppelte Schreiblast.
    """
    pegel = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(pegel)

    # Vorhandene Handler entfernen, damit ein erneuter Aufruf keine
    # doppelten Ausgaben erzeugt.
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception as exc:
            # Ein bereits geschlossener Handler ist kein Problem -- der
            # Hinweis hilft nur bei der Fehlersuche.
            logging.getLogger(__name__).debug("Handler nicht schliessbar: %s", exc)

    formatter = BerlinFormatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ziel = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ".") / str(log_file or "trading_bot.log")
    try:
        ziel.parent.mkdir(parents=True, exist_ok=True)
        datei = logging.handlers.RotatingFileHandler(
            ziel, maxBytes=int(max_bytes), backupCount=int(backups), encoding="utf-8"
        )
        datei.setFormatter(formatter)
        datei.setLevel(pegel)
        root.addHandler(datei)
    except Exception as exc:
        # Ohne Schreibrecht lieber auf die Konsole ausweichen als abstuerzen.
        logging.basicConfig(level=pegel, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger(__name__).warning("Protokolldatei %s nicht nutzbar: %s", ziel, exc)
        return

    if stdout:
        konsole = logging.StreamHandler()
        konsole.setFormatter(formatter)
        konsole.setLevel(pegel)
        root.addHandler(konsole)

    configure_library_logging()


def configure_log_hygiene() -> None:
    """Alter Name aus v5.12 -- bleibt fuer Kompatibilitaet erhalten."""
    configure_library_logging()
