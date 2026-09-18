"""Startpunkt des TradingBot v8.1.1 NEXUS.

WAS HIER PASSIERT
=================
    1. Zugaenge pruefen und beide Broker verbinden
    2. Risikotoepfe, dynamisches Universum und KI-Router aufbauen
    3. Kryptomaschine starten -- laeuft rund um die Uhr
    4. Optional den bestehenden Aktienkern (live_trader) starten

WARUM ZWEI SCHLEIFEN
====================
Der Aktienkern und die Kryptomaschine laufen in EIGENEN Threads. Haengt ein
eToro-Aufruf, laeuft Krypto trotzdem weiter -- und umgekehrt. Genau diese
Trennung fehlte in v6, weshalb ein geschlossener Aktienmarkt den gesamten
Zyklus lahmgelegt hat.

AUFRUF
======
    python3 nexus_start.py                beide Seiten
    python3 nexus_start.py --nur-krypto   nur OKX
    python3 nexus_start.py --nur-aktien   nur eToro (wie v6)
    python3 nexus_start.py --einmal       genau ein Kryptozyklus (Diagnose)
"""

from __future__ import annotations

import venv_guard
venv_guard.sicherstellen()          # muss VOR allen schweren Importen stehen

import argparse                     # noqa: E402
import json                         # noqa: E402
import logging                      # noqa: E402
import os                           # noqa: E402
import signal                       # noqa: E402
import sys                          # noqa: E402
import threading                    # noqa: E402
import time                         # noqa: E402
from datetime import datetime, timezone   # noqa: E402
from pathlib import Path            # noqa: E402

import config                       # noqa: E402

logger = logging.getLogger(__name__)

_beenden = threading.Event()


def _signalbehandlung() -> None:
    def stoppe(signum, rahmen):
        print("\nBeenden angefordert -- laufende Zyklen werden sauber abgeschlossen.")
        _beenden.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, stoppe)
        except (ValueError, OSError):
            pass       # in Threads/Diensten nicht immer moeglich


def _melder(domaene: str = ""):
    """Die Meldefunktion einer Domaene.

    BIS v8.1.3 STAND HIER EIN HARTER FEHLER: ``from notifier import Notifier``
    -- diese Klasse gab es nie, ``notifier.py`` enthaelt nur Funktionen. Der
    ImportError wurde verschluckt, die Funktion gab ``None`` zurueck, und
    damit hatte die gesamte Kryptomaschine keinen Meldekanal. Kein Kauf, kein
    Verkauf, keine Sperre erreichte den Nutzer.

    Ab v8.1.4 gibt diese Funktion IMMER eine aufrufbare Meldefunktion
    zurueck. Ist Telegram nicht eingerichtet, landet die Meldung im
    Protokoll -- aber der Aufrufer bekommt nie ``None``.
    """
    from meldungen import melder as _baue_melder
    return _baue_melder(domaene)


def baue_laufzeit():
    """Erzeugt alle gemeinsam genutzten Bausteine."""
    from ai_router import AIRouter
    from broker.multi import BrokerHub
    from risk_pots import RiskPotManager
    from scheduler_v7 import Taktgeber
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    hub = BrokerHub.aus_config(config)
    risiko = RiskPotManager(["etoro", "okx"])
    ai = AIRouter(config) if bool(getattr(config, "AI_ROUTER_ENABLED", False)) else None
    universum = UniverseManager(
        UniverseZustand(getattr(config, "UNIVERSE_STATE_FILE", "universe_state.json")),
        cfg=config, ai_router=ai)
    takt = Taktgeber(cfg=config)
    return hub, risiko, universum, takt, ai


def starte_krypto(hub, risiko, universum, takt, ai, *, einmal: bool = False) -> bool:
    """Die 24/7-Kryptoschleife."""
    from crypto_engine import CryptoEngine

    if not bool(getattr(config, "OKX_ENABLED", False)):
        hinweis = getattr(config, "OKX_SETUP_HINWEIS", "")
        print("Krypto ist nicht aktiv." + (f" {hinweis}" if hinweis else
              " Bitte zuerst 'python3 nexus_setup.py' ausfuehren."))
        return False

    melden = _melder("KRYPTO")
    engine = CryptoEngine(hub=hub, risiko=risiko, universum=universum,
                          taktgeber=takt, ai_router=ai, melder=melden, cfg=config)
    verbunden = hub.verbinde("okx")
    broker = hub.broker("okx")
    if verbunden and broker is not None:
        print(f"Krypto aktiv: {broker.beschreibung()} | Kontowert "
              f"{broker.kontowert():.2f} USD")
    else:
        zustand = hub.zustaende().get("okx", {})
        print("OKX beim Start noch nicht verbunden: "
              f"{zustand.get('letzter_fehler', 'unbekannt')}. "
              "Krypto-Worker bleibt aktiv und versucht die Verbindung erneut.")
    from crypto_strategy_mode import signal_timeframe
    active_timeframe = signal_timeframe() or "pausiert"
    print(f"Takt: Kerzen {active_timeframe}, Scan alle "
          f"{config.CRYPTO_SCAN_INTERVAL_SECONDS // 60} min, Universum alle "
          f"{config.CRYPTO_UNIVERSE_REFRESH_SECONDS // 60} min")

    if einmal:
        ergebnis = engine.zyklus()
        print(_zyklusbericht(ergebnis))
        return not bool(ergebnis.get("hinweis"))

    # Schon der erste Offline-Zustand bekommt einen Heartbeat. Damit zeigt
    # die WebUI den Unterschied zwischen "Worker laeuft, Broker offline" und
    # einem beendeten Worker korrekt an.
    if not verbunden:
        engine.zyklus()
    last_health_ok = bool(verbunden)
    while not _beenden.is_set():
        try:
            health = hub.pruefe_gesundheit().get("okx", False)
            if not health:
                if last_health_ok and melden:
                    melden("🔴 OKX-REST-Verbindung nach mehreren Pruefungen nicht mehr "
                           "authentifiziert. Neue Orders sind gesperrt; automatische "
                           "Wiederverbindung laeuft. eToro bleibt unabhaengig aktiv.", wichtig=True)
                hub.wiederverbinde_gestoerte()
                health = bool(hub.broker("okx"))
            if health and not last_health_ok and melden:
                melden("🟢 OKX-REST-Verbindung wieder authentifiziert. Konto-, Order- "
                       "und WebSocket-Status werden getrennt weiter ueberwacht.", wichtig=True)
            last_health_ok = health
            ergebnis = engine.zyklus()
            if ergebnis.get("arbeiten"):
                print(_zyklusbericht(ergebnis))
        except Exception:
            logger.exception("Kryptozyklus mit unbehandeltem Fehler")
            _beenden.wait(20.0)
            continue
        # Nicht laenger schlafen, als der schnellste Takt erlaubt.
        _beenden.wait(5.0 if engine._scan_raster_sekunden() == 300 else max(5.0, takt.wartezeit(maximum=60.0)))
    return True


def _zyklusbericht(ergebnis: dict) -> str:
    zeit = datetime.now().strftime("%H:%M:%S")
    teile = [f"[{zeit}] Krypto:"]
    universum = ergebnis.get("universum") or {}
    if universum.get("ok"):
        teile.append(f"Universum {universum.get('aenderungen')} "
                     f"({universum.get('handelbar')} handelbar)")
    elif universum:
        teile.append(f"Universum FEHLER: {universum.get('grund')}")
    scan = ergebnis.get("scan") or {}
    if scan:
        teile.append(f"Scan {scan.get('gescannt', 0)} Werte")
        if scan.get("gekauft"):
            teile.append("KAUF " + ", ".join(scan["gekauft"]))
        if scan.get("hinweis"):
            teile.append(str(scan["hinweis"]))
    positionen = ergebnis.get("positionen") or {}
    if positionen.get("geschlossen"):
        teile.append("VERKAUF " + ", ".join(positionen["geschlossen"]))
    if positionen.get("geprueft"):
        teile.append(f"{positionen['geprueft']} Positionen geprueft")
    return " | ".join(teile)


# 9.5.8: Neustartverhalten des Aktienkerns.
#
# Bis 9.5.7 stand hier ein einzelner try/except OHNE Schleife. Ein
# unbehandelter Fehler in ``live_trader.run()`` beendete den Faden endgueltig
# -- der Aktienhandel stand danach bis zum Neustart von Hand still. Am
# 04.09.2026 um 15:58 UTC ist genau das passiert: eine nicht zuordenbare
# KO-Verkaufsmeldung liess die Ledgerbuchung fail-closed werfen, der Faden
# starb, und weil ``any(t.is_alive() ...)`` schon durch den lebenden
# Kryptofaden erfuellt war, lief der Prozess weiter, systemd startete nichts
# neu und der Watchdog blieb gruen. Der Ausfall war unsichtbar.
#
# Die Krypto-Seite hatte diese Schleife die ganze Zeit (siehe
# ``starte_krypto``). Die Asymmetrie war nie beabsichtigt.
# Die Basis ist bewusst groesser als der laengste haengende Telegram-Poll
# (Long-Poll bis ~35 s): startet der neue Kern zu frueh, holen zwei Poller
# gleichzeitig Updates und Telegram antwortet mit 409 Conflict.
AKTIEN_NEUSTART_BASIS = 45.0
# Die Obergrenze bleibt deutlich unter WatchdogSec (180 s im systemd-Dienst).
# Waehrend der Pause schreibt der Aktienkern keinen Herzschlag; ist er die
# einzige aktive Domaene, wuerde eine laengere Pause den Dienst per SIGABRT
# mitten in einem Brokeraufruf abschiessen.
AKTIEN_NEUSTART_MAXIMUM = 90.0
AKTIEN_STABIL_SEKUNDEN = 600.0      # so lange gelaufen => Wartezeit zuruecksetzen
# Nach so vielen Fehlern in Folge wird nur noch jede zehnte Meldung gesendet.
# Ein dauerhaft nicht startbarer Kern soll nicht endlos Telegram fluten -- die
# Logzeile bleibt bei jedem Versuch erhalten.
AKTIEN_MELDUNG_AB_HIER_SELTENER = 5


def starte_aktien() -> None:
    """Der Aktienkern -- mit Neustart nach einem unbehandelten Fehler.

    Ein Buchhaltungsfehler ist ein Grund, EINEN Vorgang abzubrechen, nicht den
    Handel einzustellen. Der Orderpfad bleibt davon unberuehrt fail-closed:
    ``live_trader`` haelt seine Pending-Intents persistent, und der Neustart
    laeuft durch dieselbe Broker-Reconciliation wie ein normaler Start. Es
    wird also kein zweiter Kauf und kein zweiter Verkauf ausgeloest -- der
    Kern nimmt seine Arbeit an genau der Stelle wieder auf, an der er sie
    verloren hat.
    """
    # Auch bei direktem Test-/Threadaufruf gilt: Worker melden niemals
    # systemd STOPPING. Nur ``laufe``/Supervisor darf den Dienstzustand setzen.
    os.environ["TRADINGBOT_SUPERVISED"] = "1"

    def _sag(text: str) -> None:
        """Melden und ausgeben -- darf die Schleife unter keinen Umstaenden
        beenden. Genau der Zustand, den sie verhindern soll, waere sonst
        wieder da, nur ohne jede Meldung."""
        try:
            _melder("AKTIEN")(text, wichtig=True)
        except Exception:
            logger.warning("Meldung zum Aktienkern nicht zustellbar", exc_info=True)
        try:
            print(text)
        except Exception:
            logger.debug("Ausgabe zum Aktienkern nicht moeglich", exc_info=True)

    try:
        import live_trader
    except Exception as exc:
        _sag(f"🔴 Aktienkern nicht ladbar: {exc}. Es findet kein Aktienhandel "
             "statt, bis das behoben ist.")
        return
    wartezeit = AKTIEN_NEUSTART_BASIS
    fehler_in_folge = 0
    while not _beenden.is_set():
        gestartet = time.monotonic()
        try:
            live_trader.run()
        except Exception as exc:
            laufzeit = time.monotonic() - gestartet
            logger.exception("Aktienkern mit unbehandeltem Fehler beendet")
            if laufzeit >= AKTIEN_STABIL_SEKUNDEN:
                # Lange stabil gelaufen: der Fehler ist ein Einzelfall, kein
                # Startproblem. Wartezeit zuruecksetzen.
                wartezeit = AKTIEN_NEUSTART_BASIS
                fehler_in_folge = 1
            else:
                fehler_in_folge += 1
            aktuelle_wartezeit = wartezeit
            text = (f"🔴 Aktienkern abgebrochen ({type(exc).__name__}: "
                    f"{str(exc)[:160]}). Neustart in {aktuelle_wartezeit:.0f} s "
                    f"(Fehler in Folge: {fehler_in_folge}). Offene "
                    "eToro-Positionen und Schutzorders bleiben beim Broker "
                    "bestehen.")
            if (fehler_in_folge <= AKTIEN_MELDUNG_AB_HIER_SELTENER
                    or fehler_in_folge % 10 == 0):
                _sag(text)
            else:
                logger.warning("%s (Meldung gedrosselt)", text)
            if _beenden.wait(aktuelle_wartezeit):
                return
            wartezeit = min(AKTIEN_NEUSTART_MAXIMUM, aktuelle_wartezeit * 2.0)
            continue
        # Ein sauberes Ende von run() ist ein gewollter Halt (KeyboardInterrupt
        # bzw. SIGTERM). Dann wird nicht neu gestartet.
        return


def starte_aktien_universum(universum, takt) -> None:
    """Der Aktien-Universumslauf -- eigener, langsamer Faden.

    Bewusst getrennt vom Aktienkern: der Lauf braucht keine Brokerverbindung
    und darf den Orderpfad unter keinen Umstaenden aufhalten. Er stellt den
    festen Kern sicher und sammelt Vorschlaege; aufgenommen wird eine
    dynamische Aktie weiterhin nur mit Georgs Freigabe.
    """
    try:
        from stock_universe_runner import StockUniverseRunner
    except Exception as exc:
        print(f"Aktien-Universumslauf nicht ladbar: {exc}")
        return
    runner = StockUniverseRunner(universum, taktgeber=takt, melder=_melder("AKTIEN"), cfg=config)
    while not _beenden.is_set():
        try:
            runner.zyklus()
        except Exception:
            logger.exception("Aktien-Universumslauf mit unbehandeltem Fehler")
        _beenden.wait(60.0)


def _runtime_domain_healthy(name: str, max_age: float = 150.0) -> bool:
    """Heartbeat genau einer Domaene lesen; keine Broker-/Netzabfrage."""
    root = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or
                Path(__file__).resolve().parent)
    path = root / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = str(data.get("last_heartbeat") or "").replace("Z", "+00:00")
        stamp = datetime.fromisoformat(raw)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return bool(data.get("running") and
                    (datetime.now(timezone.utc) - stamp).total_seconds() <= max_age)
    except Exception:
        return False


def laufe(*, nur_krypto: bool = False, nur_aktien: bool = False,
          kopfzeile: bool = True) -> int:
    """Startet beide Seiten und blockiert, bis eine Abbruchanforderung kommt.

    Wird sowohl von main() als auch vom systemd-Einstiegspunkt pi_service.py
    benutzt. Damit laeuft im Dienstbetrieb genau dasselbe wie beim Start von
    Hand -- und Krypto laeuft auch als Dienst rund um die Uhr.
    """
    if kopfzeile:
        _kopfzeile()
    # Nur der Supervisor spricht mit systemd. Worker-Runtimeobjekte schreiben
    # weiterhin ihre JSON-Heartbeats, aber kein READY/WATCHDOG/STOPPING.
    os.environ["TRADINGBOT_SUPERVISED"] = "1"
    hub, risiko, universum, takt, ai = baue_laufzeit()

    threads = []
    aktien_aktiv = bool(getattr(config, "ETORO_ENABLED", True))
    krypto_aktiv = bool(getattr(config, "OKX_ENABLED", False))
    if not nur_krypto and aktien_aktiv:
        t = threading.Thread(target=starte_aktien, name="aktienkern", daemon=True)
        t.start()
        threads.append(t)
        print("Aktienkern gestartet.")
        # Der Universumslauf der Aktienseite. Ohne ihn bliebe der
        # Aktien-Universumszustand leer -- der feste Kern kaeme nie an.
        # Bewusst NICHT in "threads": das ist ein Hilfsfaden, keine
        # Handelsdomaene. Er darf den Supervisor nicht am Leben halten,
        # wenn beide Handelsseiten beendet sind.
        threading.Thread(target=starte_aktien_universum, args=(universum, takt),
                         name="aktienuniversum", daemon=True).start()

    elif not nur_krypto:
        print("Aktienkern und Aktien-Referenzabrufe deaktiviert (eToro ist aus).")

    if not nur_aktien and krypto_aktiv:
        t = threading.Thread(
            target=starte_krypto, args=(hub, risiko, universum, takt, ai),
            name="kryptomaschine", daemon=True)
        t.start()
        threads.append(t)
    elif not nur_aktien:
        print("Kryptokern deaktiviert (OKX ist aus).")

    if not threads:
        print("Nichts zu starten.")
        return 1

    from systemd_notify import ready as sd_ready, stopping as sd_stopping, watchdog as sd_watchdog
    sd_ready("NEXUS-Supervisor aktiv; eToro/OKX starten unabhaengig")
    started = time.monotonic()
    last_watchdog = 0.0
    # 9.5.8: Eine gestorbene Handelsdomaene darf nicht mehr unsichtbar sein.
    # Bis 9.5.7 hielt schon ein einziger lebender Faden den Supervisor am
    # Leben, und das ``or`` in der Gesundheitspruefung liess den systemd-
    # Watchdog gruen, obwohl die andere Seite nicht mehr handelte. Beide
    # Fassaden zeigten "laeuft".
    gemeldet_tot: set[str] = set()
    melden_sup = _melder("NEXUS")
    try:
        while not _beenden.is_set() and any(t.is_alive() for t in threads):
            now = time.monotonic()
            for t in threads:
                # Beim gewollten Halt (Strg-C, SIGTERM, /shutdownbot) enden die
                # Faeden regulaer -- das ist keine Stoerung und darf keine
                # Alarmmeldung ausloesen.
                if (t.is_alive() or t.name in gemeldet_tot
                        or _beenden.is_set()):
                    continue
                gemeldet_tot.add(t.name)
                logger.error("Handelsdomaene '%s' ist beendet und handelt nicht mehr.",
                             t.name)
                try:
                    melden_sup(
                        f"🔴 Die Handelsdomaene '{t.name}' ist beendet und handelt "
                        "nicht mehr. Die andere Seite laeuft weiter. Bitte im "
                        "Logbuch nachsehen und NEXUS neu starten.", wichtig=True)
                except Exception:
                    logger.warning("Meldung zur toten Handelsdomaene nicht "
                                   "zustellbar", exc_info=True)
            if now - last_watchdog >= 20.0:
                startup_grace = now - started <= 600.0
                zustand = {
                    "aktienkern": _runtime_domain_healthy("runtime_status.json"),
                    "kryptomaschine": _runtime_domain_healthy("runtime_status_okx.json"),
                }
                # Nur GESTARTETE Domaenen zaehlen. Ist eine Seite in der
                # Konfiguration aus, darf ihr fehlender Heartbeat den Watchdog
                # nicht rot faerben.
                gestartet = {t.name for t in threads}
                relevant = {k: v for k, v in zustand.items() if k in gestartet}
                gesund = [k for k, v in relevant.items() if v]
                krank = [k for k, v in relevant.items() if not v]
                # Nach der Startphase muss JEDE konfigurierte Handelsdomaene
                # gesund sein. Eine gesunde OKX-Seite darf keinen toten
                # Aktienworker mehr verdecken (und umgekehrt).
                all_healthy = bool(relevant) and all(relevant.values())
                if startup_grace or all_healthy:
                    sd_watchdog(
                        "NEXUS: " + (", ".join(gesund) or "Startphase") + " gesund"
                        + (f"; ohne Herzschlag: {', '.join(krank)}" if krank else ""))
                last_watchdog = now
            _beenden.wait(1.0)
    except KeyboardInterrupt:
        _beenden.set()

    print("Beende ...")
    sd_stopping("NEXUS beendet Brokerdomaenen")
    hub.trenne_alle()
    risiko.speichern()
    return 0


def _kopfzeile() -> None:
    print()
    print("=" * 70)
    print(f"TRADINGBOT {getattr(config, 'VERSION_NEXUS', '8.1.1-NEXUS')}")
    print("=" * 70)
    print(f"Aktien  eToro  {'aktiv' if getattr(config, 'ETORO_ENABLED', True) else 'inaktiv'} "
          f"| {'PAPER' if config.PAPER_TRADING else 'LIVE'}")
    print(f"Krypto  OKX    {'aktiv' if getattr(config, 'OKX_ENABLED', False) else 'inaktiv'} "
          f"| {'LIVE' if getattr(config, 'OKX_LIVE_TRADING', False) else 'DEMO'}")
    if getattr(config, "OKX_SETUP_HINWEIS", ""):
        print(f"        {config.OKX_SETUP_HINWEIS}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=f"TradingBot {config.VERSION_NEXUS} starten")
    ap.add_argument("--nur-krypto", action="store_true", help="nur die OKX-Seite starten")
    ap.add_argument("--nur-aktien", action="store_true", help="nur den eToro-Kern starten")
    ap.add_argument("--einmal", action="store_true", help="einen Kryptozyklus ausfuehren und beenden")
    ap.add_argument("--status", action="store_true", help="Zustand anzeigen und beenden")
    args = ap.parse_args()

    from log_hygiene import configure_root_logging
    configure_root_logging(config.LOG_FILE, getattr(config, "LOG_LEVEL", "INFO"))
    _signalbehandlung()

    _kopfzeile()

    if args.status or args.einmal:
        hub, risiko, universum, takt, ai = baue_laufzeit()
        if args.status:
            import json
            print(json.dumps({
                "broker": hub.uebersicht(),
                "risiko": risiko.uebersicht(),
                "universum_krypto": universum.uebersicht("okx"),
                "takt": takt.plan(),
                "ki": ai.status() if ai else {"aktiv": False},
            }, ensure_ascii=False, indent=2, default=str))
            return 0
        return 0 if starte_krypto(hub, risiko, universum, takt, ai, einmal=True) else 1

    return laufe(nur_krypto=bool(args.nur_krypto), nur_aktien=bool(args.nur_aktien),
                 kopfzeile=False)


if __name__ == "__main__":
    sys.exit(main())
