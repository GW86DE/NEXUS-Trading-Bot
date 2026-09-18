"""Asset-bewusster Taktgeber (v8.1.1 NEXUS).

DER FEHLER AUS v6.0
===================
Es gab genau EINE Hauptschleife. Ihr Takt richtete sich nach dem
Aktienmarkt, und alle Arbeiten haengen an derselben Kalenderpruefung.
Sobald die Boerse zu war, blieb faktisch der gesamte Zyklus stehen --
Krypto inklusive. Am Wochenende hat der Bot Krypto deshalb ueberhaupt
nicht mehr abgefragt.

DIE LOESUNG IN v7
=================
Zwei voneinander unabhaengige Takte:

    KRYPTO   laeuft 24/7, unabhaengig von jedem Boersenkalender
             Kerzen 15m, Scan alle 5 Minuten, Universum alle 15 Minuten
    AKTIEN   laeuft nur bei geoeffnetem Markt (plus Vorlaufpuffer)
             Kerzen 1 Tag, Scan alle 5 Minuten, Universum alle 45 Minuten

Die Positionsueberwachung laeuft in BEIDEN Faellen schneller als der Scan.
Ein Stop-Loss darf nie darauf warten, dass ein Scanzyklus fertig wird.

TESTBARKEIT
===========
Die Klasse rechnet ausschliesslich mit uebergebener Zeit. Dadurch laesst
sich ein ganzer Handelstag in Millisekunden durchsimulieren, ohne zu warten.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Arbeitsarten
SCAN = "scan"
UNIVERSUM = "universum"
POSITIONEN = "positionen"
NEWS = "news"
GESUNDHEIT = "gesundheit"
BERICHT = "bericht"


@dataclass
class AssetTakt:
    """Alle Intervalle einer Anlageklasse in Sekunden."""
    asset_type: str
    scan_sekunden: float = 300.0
    universum_sekunden: float = 900.0
    positionen_sekunden: float = 60.0
    news_sekunden: float = 600.0
    kerzengroesse: str = "15 mins"
    historie_dauer: str = "3 D"
    rund_um_die_uhr: bool = True

    @classmethod
    def krypto(cls, cfg=None) -> "AssetTakt":
        import config as _config
        cfg = cfg or _config
        return cls(
            asset_type="crypto",
            scan_sekunden=float(getattr(cfg, "CRYPTO_SCAN_INTERVAL_SECONDS", 300)),
            universum_sekunden=float(getattr(cfg, "CRYPTO_UNIVERSE_REFRESH_SECONDS", 900)),
            positionen_sekunden=float(getattr(cfg, "CRYPTO_POSITION_CHECK_SECONDS", 60)),
            news_sekunden=float(getattr(cfg, "CRYPTO_NEWS_INTERVAL_SECONDS", 900)),
            kerzengroesse=str(getattr(cfg, "CRYPTO_BAR_SIZE", "15 mins")),
            historie_dauer=str(getattr(cfg, "CRYPTO_HISTORY_DURATION", "3 D")),
            rund_um_die_uhr=True,
        )

    @classmethod
    def aktien(cls, cfg=None) -> "AssetTakt":
        import config as _config
        cfg = cfg or _config
        return cls(
            asset_type="stock",
            scan_sekunden=float(getattr(cfg, "STOCK_SCAN_INTERVAL_SECONDS", 300)),
            universum_sekunden=float(getattr(cfg, "STOCK_UNIVERSE_REFRESH_SECONDS", 2700)),
            positionen_sekunden=float(getattr(cfg, "STOCK_POSITION_CHECK_SECONDS", 120)),
            news_sekunden=float(getattr(cfg, "STOCK_NEWS_INTERVAL_SECONDS", 900)),
            kerzengroesse=str(getattr(cfg, "STOCK_BAR_SIZE", "1 hour")),
            historie_dauer=str(getattr(cfg, "STOCK_HISTORY_DURATION", "60 D")),
            rund_um_die_uhr=False,
        )


class Marktfenster:
    """Beantwortet: darf diese Anlageklasse JETZT arbeiten?"""

    def __init__(self, cfg=None):
        import config as _config
        self.cfg = cfg or _config
        self._letzter_kalenderfehler = ""

    def krypto_offen(self, jetzt: Optional[datetime] = None) -> tuple[bool, str]:
        """Krypto ist immer offen -- ohne Ausnahme, ohne Kalender.

        Dieser Rueckgabewert ist bewusst konstant. Jede Kalenderabfrage an
        dieser Stelle waere ein potenzieller Weg, wie der Aktienkalender
        doch wieder den Kryptohandel abschaltet.
        """
        return True, "Krypto handelt rund um die Uhr"

    def aktien_offen(self, jetzt: Optional[datetime] = None) -> tuple[bool, str]:
        if not bool(getattr(self.cfg, "MARKET_CALENDAR_ENABLED", True)):
            return True, "Boersenkalender deaktiviert"
        try:
            from market_calendar import darf_arbeiten
            puffer = int(getattr(self.cfg, "MARKET_PREOPEN_BUFFER_MINUTES", 20))
            offen, grund = darf_arbeiten(puffer_minuten=puffer)
            self._letzter_kalenderfehler = ""
            return bool(offen), str(grund or "")
        except Exception as exc:
            # Kalenderfehler duerfen den Aktienhandel nicht dauerhaft blockieren,
            # aber auch nicht still eroeffnen. Konservativ: geschlossen melden,
            # Grund sichtbar machen.
            self._letzter_kalenderfehler = str(exc)[:200]
            logger.warning("Boersenkalender nicht auswertbar: %s", exc)
            return False, f"Kalenderpruefung fehlgeschlagen: {exc}"

    def offen(self, asset_type: str, jetzt: Optional[datetime] = None) -> tuple[bool, str]:
        if str(asset_type).lower() == "crypto":
            return self.krypto_offen(jetzt)
        return self.aktien_offen(jetzt)

    def status(self) -> dict:
        krypto_offen, krypto_grund = self.krypto_offen()
        aktien_offen, aktien_grund = self.aktien_offen()
        return {
            "crypto": {"offen": krypto_offen, "grund": krypto_grund},
            "stock": {"offen": aktien_offen, "grund": aktien_grund},
            "kalenderfehler": self._letzter_kalenderfehler,
        }


class Taktgeber:
    """Merkt sich, wann eine Arbeit zuletzt lief, und meldet Faelligkeit."""

    def __init__(self, takte: Optional[dict[str, AssetTakt]] = None, *,
                 marktfenster: Optional[Marktfenster] = None, cfg=None):
        import config as _config
        self.cfg = cfg or _config
        self.takte = takte or {
            "crypto": AssetTakt.krypto(self.cfg),
            "stock": AssetTakt.aktien(self.cfg),
        }
        self.fenster = marktfenster or Marktfenster(self.cfg)
        self._letzter_lauf: dict[tuple[str, str], float] = {}
        self._laeufe: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    # -- Intervalle ---------------------------------------------------------
    def intervall(self, asset_type: str, arbeit: str) -> float:
        takt = self.takte.get(str(asset_type).lower())
        if takt is None:
            return 300.0
        return {
            SCAN: takt.scan_sekunden,
            UNIVERSUM: takt.universum_sekunden,
            POSITIONEN: takt.positionen_sekunden,
            NEWS: takt.news_sekunden,
        }.get(arbeit, 300.0)

    # -- Faelligkeit --------------------------------------------------------
    def faellig(self, asset_type: str, arbeit: str, *, jetzt: Optional[float] = None,
               markt_pruefen: bool = True) -> tuple[bool, str]:
        """Ist diese Arbeit jetzt dran?

        Positionsueberwachung laeuft AUCH bei geschlossenem Markt weiter:
        eine offene Aktienposition muss ueber Nacht beobachtbar bleiben,
        auch wenn keine neuen Kaeufe moeglich sind.
        """
        asset_type = str(asset_type).lower()
        jetzt = float(jetzt if jetzt is not None else time.monotonic())
        schluessel = (asset_type, arbeit)

        if markt_pruefen and arbeit in (SCAN, UNIVERSUM, NEWS):
            offen, grund = self.fenster.offen(asset_type)
            if not offen:
                return False, grund

        with self._lock:
            zuletzt = self._letzter_lauf.get(schluessel)
        if zuletzt is None:
            return True, "erster Lauf"
        abstand = jetzt - zuletzt
        soll = self.intervall(asset_type, arbeit)
        if abstand >= soll:
            return True, f"faellig nach {abstand:.0f}s (Intervall {soll:.0f}s)"
        return False, f"naechster Lauf in {soll - abstand:.0f}s"

    def rasterlauf_beginnen(self, asset_type: str, arbeit: str, *,
                            raster_sekunden: float) -> dict:
        """Den Rasterpunkt VOR dem Lauf festlegen (v9.2).

        Rueckgabe ist ein Lauf-Token. Es haelt fest, auf welche abgeschlossene
        Kerze sich dieser Lauf bezieht -- unabhaengig davon, wie lange er
        dauert und ob er eine Rastergrenze ueberschreitet. Alle Instrumente
        eines Scans arbeiten damit auf derselben Kerze.
        """
        raster = max(0.0, float(raster_sekunden or 0.0))
        wand = time.time()
        monoton = time.monotonic()
        if raster <= 0:
            return {"raster_sekunden": 0.0, "rasterpunkt": 0.0,
                    "monoton": monoton, "kerzenschluss_utc": ""}
        versatz = wand % raster
        rasterpunkt = wand - versatz
        return {
            "raster_sekunden": raster,
            "rasterpunkt": rasterpunkt,
            # Der monotone Zeitpunkt, der dem Rasterpunkt entspricht.
            "monoton": monoton - versatz,
            "kerzenschluss_utc": datetime.fromtimestamp(
                rasterpunkt, timezone.utc).isoformat(),
        }

    def rasterlauf_abschliessen(self, asset_type: str, arbeit: str,
                                token: dict) -> None:
        """Den Lauf dem VOR Beginn festgelegten Rasterpunkt zuordnen."""
        if not token or float(token.get("raster_sekunden") or 0.0) <= 0:
            self.markiere(asset_type, arbeit)
            return
        self.markiere(asset_type, arbeit, jetzt=float(token["monoton"]))

    def markiere(self, asset_type: str, arbeit: str, *, jetzt: Optional[float] = None,
                 raster_sekunden: float = 0.0) -> None:
        """Einen Lauf als erledigt vermerken.

        ``raster_sekunden`` rastet den Vermerk auf ein Zeitraster ein (v9.1).

        Warum das noetig ist: Ohne Raster laeuft der Takt frei. Der Vermerk
        entsteht NACH dem Lauf, die Periode ist also Intervall + Laufzeit, und
        der Abstand zum Kerzenschluss wandert bei jedem Durchgang. Gemessen
        ueber vier Stunden: Versatz zwischen 2 und 297 Sekunden, und bei 30 s
        Scandauer wurden 4 von 48 Kerzen komplett uebersprungen -- deren
        Einstiegssignale hat der Bot nie gesehen.

        Mit Raster wird der Lauf dem Rasterpunkt zugeordnet, zu dem er
        gehoert. Der naechste Lauf ist dann wieder kurz nach dem naechsten
        Kerzenschluss faellig, unabhaengig davon, wie lange dieser Durchgang
        gedauert hat. Freqtrade verlangt genau das: entscheiden auf der eben
        abgeschlossenen Kerze, ausfuehren so nah wie moeglich am naechsten
        Kerzen-Open.
        """
        schluessel = (str(asset_type).lower(), arbeit)
        zeitpunkt = float(jetzt if jetzt is not None else time.monotonic())
        raster = max(0.0, float(raster_sekunden or 0.0))
        if raster > 0:
            # Rueckwaerts auf den zuletzt passierten Rasterpunkt einrasten.
            # Die Wanduhr bestimmt das Raster, die monotone Uhr den Vermerk --
            # so bleibt der Takt auch bei einer Zeitumstellung stabil.
            #
            # v9.2: Der Rasterpunkt wird jetzt VOR dem Lauf bestimmt und ueber
            # ``rasterpunkt`` hereingereicht. Vorher wurde er hier aus der
            # AKTUELLEN Wanduhr berechnet -- ein Scan von 12:04:59 bis 12:05:10
            # bekam damit den Rasterpunkt 12:05:00, obwohl er auf der
            # 12:00-Kerze entschieden hatte. Der naechste Lauf war erst um
            # 12:10 faellig, und die 12:05-Kerze sah der Bot nie. Genau die
            # Luecke, die das Raster schliessen sollte.
            versatz = time.time() % raster
            zeitpunkt -= min(versatz, raster)
        with self._lock:
            self._letzter_lauf[schluessel] = zeitpunkt
            self._laeufe[schluessel] = self._laeufe.get(schluessel, 0) + 1

    def erzwinge(self, asset_type: str, arbeit: str) -> None:
        """Naechste Pruefung meldet auf jeden Fall 'faellig' (manueller Scan)."""
        with self._lock:
            self._letzter_lauf.pop((str(asset_type).lower(), arbeit), None)

    # -- Schlafzeit ---------------------------------------------------------
    def wartezeit(self, *, jetzt: Optional[float] = None, maximum: float = 60.0) -> float:
        """Wie lange darf die Hauptschleife schlafen, ohne etwas zu verpassen?

        Es gilt immer die naechste faellige Arbeit ueber ALLE Anlageklassen.
        Sonst wuerde ein langsamer Aktientakt den schnellen Kryptotakt
        ausbremsen -- genau der Fehler aus v6.
        """
        jetzt = float(jetzt if jetzt is not None else time.monotonic())
        kandidaten = []
        for asset_type in self.takte:
            offen, _ = self.fenster.offen(asset_type)
            for arbeit in (SCAN, UNIVERSUM, POSITIONEN, NEWS):
                if not offen and arbeit in (SCAN, UNIVERSUM, NEWS):
                    continue
                with self._lock:
                    zuletzt = self._letzter_lauf.get((asset_type, arbeit))
                if zuletzt is None:
                    return 0.0
                rest = self.intervall(asset_type, arbeit) - (jetzt - zuletzt)
                kandidaten.append(max(0.0, rest))
        if not kandidaten:
            return min(maximum, 30.0)
        return max(0.5, min(float(maximum), min(kandidaten)))

    # -- Uebersicht ---------------------------------------------------------
    def plan(self) -> dict:
        """Was steht als naechstes an? Fuer Dashboard und Telegram-Status."""
        jetzt = time.monotonic()
        out: dict[str, dict] = {}
        for asset_type, takt in self.takte.items():
            offen, grund = self.fenster.offen(asset_type)
            arbeiten = {}
            for arbeit in (SCAN, UNIVERSUM, POSITIONEN, NEWS):
                with self._lock:
                    zuletzt = self._letzter_lauf.get((asset_type, arbeit))
                    laeufe = self._laeufe.get((asset_type, arbeit), 0)
                soll = self.intervall(asset_type, arbeit)
                arbeiten[arbeit] = {
                    "intervall_sekunden": soll,
                    "laeufe": laeufe,
                    "sekunden_seit_lauf": round(jetzt - zuletzt, 1) if zuletzt else None,
                    "faellig_in": (round(max(0.0, soll - (jetzt - zuletzt)), 1)
                                   if zuletzt else 0.0),
                }
            out[asset_type] = {
                "offen": offen,
                "grund": grund,
                "kerzengroesse": takt.kerzengroesse,
                "rund_um_die_uhr": takt.rund_um_die_uhr,
                "arbeiten": arbeiten,
            }
        return {"zeit": datetime.now(timezone.utc).isoformat(), "takte": out}


class AssetSchleife:
    """Optionaler eigener Thread je Anlageklasse.

    Wird gebraucht, damit ein haengender Aktienzyklus (z.B. eToro antwortet
    nicht) den Kryptotakt nicht blockiert. Beide Schleifen teilen sich nur
    den Broker-Hub und den Risikomanager -- beide sind threadsicher.
    """

    def __init__(self, name: str, arbeit: Callable[[], None], *,
                 taktgeber: Taktgeber, asset_type: str):
        self.name = name
        self.arbeit = arbeit
        self.taktgeber = taktgeber
        self.asset_type = asset_type
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.letzter_fehler = ""
        self.laeufe = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._schleife, name=self.name, daemon=True)
        self._thread.start()
        logger.info("Taktschleife %s gestartet.", self.name)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def laeuft(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _schleife(self) -> None:
        while not self._stop.is_set():
            try:
                self.arbeit()
                self.laeufe += 1
                self.letzter_fehler = ""
            except Exception as exc:
                self.letzter_fehler = f"{type(exc).__name__}: {exc}"
                logger.exception("Taktschleife %s: unbehandelter Fehler", self.name)
                # Nach einem Fehler bewusst laenger warten: ein Dauerfehler
                # soll nicht das Protokoll und die API-Grenzen fluten.
                self._stop.wait(15.0)
                continue
            self._stop.wait(self.taktgeber.wartezeit(maximum=30.0))


__all__ = [
    "Taktgeber", "AssetTakt", "Marktfenster", "AssetSchleife",
    "SCAN", "UNIVERSUM", "POSITIONEN", "NEWS", "GESUNDHEIT", "BERICHT",
]
