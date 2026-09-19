"""Lokale Rueckfall-Einrichtung fuer v8-Zugaenge.

Primaer werden die Zugangsdaten in der WebUI gepflegt. Dieses Werkzeug bleibt
fuer Wartung ohne Browser erhalten. Alle Werte landen verschluesselt bzw. auf 0600
gesetzten Dateien wie die bestehenden Zugaenge -- und werden von der
Einstellungsuebernahme kuenftiger Versionen automatisch mitgenommen.

WARUM DIE EINGABE VON SECRETS UNSICHTBAR IST
============================================
Passphrase, Secret und API-Schluessel werden ohne Bildschirmausgabe
eingelesen. Das ist Absicht: sonst stuenden sie im Terminalprotokoll, im
Scrollback und auf jedem Foto des Bildschirms.

Weil das aussieht, als wuerde die Eingabe nicht ankommen, bestaetigt dieses
Werkzeug nach jeder verdeckten Eingabe die Zeichenzahl und zeigt eine
maskierte Vorschau. Wer die Eingabe trotzdem sehen will, startet mit
--sichtbar (nur allein vor dem Bildschirm sinnvoll).

Aufruf:
    python3 nexus_setup.py            interaktives Menue
    python3 nexus_setup.py --status   nur den aktuellen Zustand anzeigen
    python3 nexus_setup.py --test     Verbindungen pruefen
    python3 nexus_setup.py --sichtbar Eingaben sichtbar tippen
"""

from __future__ import annotations

import venv_guard
venv_guard.sicherstellen()          # muss VOR allen schweren Importen stehen

import argparse                     # noqa: E402
import getpass                      # noqa: E402
import json                         # noqa: E402
import re                           # noqa: E402
import sys                          # noqa: E402
from pathlib import Path            # noqa: E402

from credential_store import load_credentials, save_credentials  # noqa: E402

ROOT = Path(__file__).resolve().parent

OKX_DATEI = "okx_credentials.json"
MASSIVE_DATEI = "massive_credentials.json"
OPENAI_DATEI = "openai_ai_settings.json"
AI_DATEI = "ai_router_settings.json"

# Eingaben sichtbar tippen? Wird ueber --sichtbar gesetzt.
SICHTBAR = False

# Muster, an denen ein versehentlich eingefuegtes Geheimnis erkannt wird.
GEHEIMNISMUSTER = re.compile(r"(sk-|sk_live|sk_test|xoxb-|ghp_|AIza)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Ein- und Ausgabe
# ---------------------------------------------------------------------------
def maskiere(wert: str) -> str:
    """Zeigt genug zum Wiedererkennen, zu wenig zum Missbrauchen."""
    wert = str(wert or "")
    if not wert:
        return "-- nicht gesetzt --"
    if len(wert) <= 8:
        return "*" * len(wert) + f" ({len(wert)} Zeichen)"
    return f"{wert[:3]}...{wert[-3:]} ({len(wert)} Zeichen)"


def ist_geheimnisverdaechtig(wert: str) -> bool:
    """Sieht dieser Wert aus wie ein API-Schluessel?"""
    text = str(wert or "").strip()
    if GEHEIMNISMUSTER.search(text):
        return True
    # Lange Zeichenketten ohne Leerzeichen sind in Modellfeldern nie richtig.
    return len(text) > 40 and " " not in text


def _frage(text: str, standard: str = "") -> str:
    zusatz = f" [{standard}]" if standard else ""
    try:
        wert = input(f"{text}{zusatz}: ").strip()
    except EOFError:
        return standard
    return wert or standard


def _geheim(text: str, *, vorhanden: str = "", pflicht: bool = False) -> str:
    """Liest ein Geheimnis ein und bestaetigt sichtbar, dass es ankam.

    Leere Eingabe bedeutet immer: den bisherigen Wert behalten. So kann man
    ein einzelnes Feld aendern, ohne alle anderen neu tippen zu muessen.
    """
    hinweis = " (bereits gesetzt, Enter = unveraendert)" if vorhanden else ""
    while True:
        print(f"  {text}{hinweis}")
        if SICHTBAR:
            print("    Eingabe ist SICHTBAR.")
            try:
                wert = input("    > ").strip()
            except EOFError:
                wert = ""
        else:
            print("    Die Eingabe bleibt unsichtbar -- das ist Absicht.")
            print("    Einfach tippen oder einfuegen und Enter druecken.")
            try:
                wert = getpass.getpass("    > ").strip()
            except (EOFError, KeyboardInterrupt):
                raise
            except Exception as exc:
                # Manche Terminals koennen keine verdeckte Eingabe.
                print(f"    Verdeckte Eingabe nicht moeglich ({type(exc).__name__}).")
                print("    Achtung: die naechste Eingabe ist SICHTBAR.")
                wert = input("    > ").strip()

        if not wert:
            if vorhanden:
                print("    -> unveraendert uebernommen.")
                return vorhanden
            if not pflicht:
                print("    -> leer gelassen.")
                return ""
            print("    Dieses Feld wird benoetigt. Bitte noch einmal.")
            continue

        print(f"    -> uebernommen: {maskiere(wert)}")
        return wert


def _ja_nein(text: str, standard: bool = False) -> bool:
    vorgabe = "J/n" if standard else "j/N"
    try:
        antwort = input(f"{text} ({vorgabe}): ").strip().lower()
    except EOFError:
        return standard
    if not antwort:
        return standard
    return antwort in ("j", "ja", "y", "yes")


# ---------------------------------------------------------------------------
# OKX
# ---------------------------------------------------------------------------
def okx_einrichten() -> None:
    daten = load_credentials(OKX_DATEI, {}) or {}
    print()
    print("=" * 70)
    print("OKX-ZUGANG EINRICHTEN")
    print("=" * 70)
    print("Demo und Live sind bei OKX ZWEI getrennte Schluesselsaetze.")
    print("Ein Demo-Schluessel funktioniert im Livekonto nicht -- und umgekehrt.")
    print("Den Demo-Satz erzeugst du im OKX-Demokonto unter 'Demo Trading -> API'.")
    print()
    print("Jeder Satz besteht aus DREI Werten:")
    print("  API-Key      sichtbar, wird beim Anlegen angezeigt")
    print("  Secret       nur EINMAL beim Anlegen sichtbar")
    print("  Passphrase   die hast du beim Anlegen selbst vergeben")
    print()

    if _ja_nein("Demo-Zugang jetzt eintragen/aendern?", not daten.get("demo_api_key")):
        print("\n-- Demo --")
        daten["demo_api_key"] = _geheim("Demo API-Key", vorhanden=daten.get("demo_api_key", ""))
        daten["demo_api_secret"] = _geheim("Demo Secret", vorhanden=daten.get("demo_api_secret", ""))
        daten["demo_passphrase"] = _geheim("Demo Passphrase", vorhanden=daten.get("demo_passphrase", ""))

    if _ja_nein("Live-Zugang jetzt eintragen/aendern?", False):
        print("\n-- Live --")
        print("  HINWEIS: Der Live-Schluessel braucht NUR die Berechtigung 'Handel'.")
        print("           'Abheben' darf NICHT gesetzt sein -- der Bot hebt nie ab.")
        daten["live_api_key"] = _geheim("Live API-Key", vorhanden=daten.get("live_api_key", ""))
        daten["live_api_secret"] = _geheim("Live Secret", vorhanden=daten.get("live_api_secret", ""))
        daten["live_passphrase"] = _geheim("Live Passphrase", vorhanden=daten.get("live_passphrase", ""))

    print()
    quote = _frage("Primaere Quotewaehrung (EUR, USD oder USDC)",
                   daten.get("quote_ccy", "EUR")).upper()
    if quote not in {"EUR", "USD", "USDC"}:
        print("Nur EUR, USD oder USDC sind erlaubt; EUR wird verwendet.")
        quote = "EUR"
    daten["quote_ccy"] = quote
    daten["allowed_quote_ccy"] = [quote] + [
        x for x in ("EUR", "USD", "USDC") if x != quote]
    daten["enabled"] = _ja_nein("OKX-Anbindung aktivieren?", bool(daten.get("enabled", True)))

    live = _ja_nein("LIVE handeln (echtes Geld)? Nein = Demo", bool(daten.get("live_trading", False)))
    if live:
        print()
        print("!" * 70)
        print("ACHTUNG: LIVE bedeutet echtes Geld auf dem OKX-Konto.")
        print("!" * 70)
        bestaetigung = input("Zum Bestaetigen 'LIVE' eintippen: ").strip()
        live = bestaetigung == "LIVE"
        if not live:
            print("-> Nicht bestaetigt. OKX bleibt im Demo-Modus.")
    daten["live_trading"] = live

    save_credentials(OKX_DATEI, daten)
    print(f"\nGespeichert. Modus: {'LIVE' if live else 'DEMO'}")
    if live:
        print("Neue LIVE-Einstiege sind noch NICHT freigegeben. Kurzzeitig freigeben mit:")
        print("  python3 broker_live_arming.py okx arm --minutes 15")
    _okx_uebersicht(daten, live)


def _okx_uebersicht(daten: dict, live: bool) -> None:
    satz = (("live_api_key", "live_api_secret", "live_passphrase") if live
            else ("demo_api_key", "demo_api_secret", "demo_passphrase"))
    beschriftung = {"api_key": "API-Key", "api_secret": "Secret", "passphrase": "Passphrase"}
    print("\nAktiver Schluesselsatz:")
    fehlend = []
    for name in satz:
        kurz = name.split("_", 1)[1]
        wert = str(daten.get(name, "") or "")
        print(f"  {beschriftung.get(kurz, kurz):12s} {maskiere(wert)}")
        if not wert:
            fehlend.append(beschriftung.get(kurz, kurz))
    if fehlend:
        print("\nWARNUNG: Es fehlt noch: " + ", ".join(fehlend))
        print("OKX bleibt deaktiviert, bis alle drei Werte gesetzt sind.")
    else:
        print("\nAlle drei Werte sind gesetzt. Mit '5' kannst du die Verbindung testen.")


# ---------------------------------------------------------------------------
# MASSIVE
# ---------------------------------------------------------------------------
def massive_einrichten() -> None:
    daten = load_credentials(MASSIVE_DATEI, {}) or {}
    print()
    print("=" * 70)
    print("MASSIVE-ZUGANG EINRICHTEN")
    print("=" * 70)
    print("MASSIVE liefert Tickerreferenz (inkl. Delisting-Erkennung),")
    print("Tagesumsaetze fuer die Aktienbewertung und Nachrichten.")
    print()
    daten["api_key"] = _geheim("MASSIVE API-Key", vorhanden=daten.get("api_key", ""))
    daten["daily_limit"] = int(_frage("Tagesbudget an Anfragen (0 = kein eigenes Limit)",
                                      str(daten.get("daily_limit", 0))) or 0)
    daten["enabled"] = _ja_nein("MASSIVE aktivieren?", bool(daten.get("enabled", True)))
    save_credentials(MASSIVE_DATEI, daten)
    print("Gespeichert.")


# ---------------------------------------------------------------------------
# OpenAI und KI-Router
# ---------------------------------------------------------------------------
def _modellfeld(text: str, standard: str) -> str:
    """Liest einen Modellnamen und weist einen Schluessel zurueck.

    Genau hier ist in der ersten Fassung ein API-Schluessel gelandet, weil
    das Menue kein eigenes Feld dafuer hatte. Das Feld verweigert solche
    Eingaben jetzt ausdruecklich.
    """
    while True:
        wert = _frage(text, standard)
        if not ist_geheimnisverdaechtig(wert):
            return wert
        print("  Das sieht nach einem API-Schluessel aus, nicht nach einem Modellnamen.")
        print("  Der Schluessel gehoert in das Feld 'OpenAI API-Key' weiter oben.")
        print(f"  Beispiel fuer einen Modellnamen: {standard}")


def ai_einrichten() -> None:
    openai_daten = load_credentials(OPENAI_DATEI, {}) or {}
    pfad = ROOT / AI_DATEI
    router = {}
    if pfad.exists():
        try:
            router = json.loads(pfad.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Hinweis: {AI_DATEI} war nicht lesbar ({exc}); es werden Standardwerte benutzt.")
            router = {}

    print()
    print("=" * 70)
    print("OPENAI UND KI-ROUTER (LUNA / TERRA)")
    print("=" * 70)
    print("Luna uebernimmt haeufige, einfache Aufgaben (guenstig).")
    print("Terra nur komplexe Sonderfaelle (Anomalien, Exploits, Widersprueche).")
    print()

    print("-- Zugang --")
    openai_daten["api_key"] = _geheim("OpenAI API-Key (beginnt mit sk-)",
                                      vorhanden=str(openai_daten.get("api_key", "") or ""))
    save_credentials(OPENAI_DATEI, openai_daten)

    print("\n-- Modelle --")
    print("Hier stehen MODELLNAMEN, kein Schluessel.")
    router["luna_model"] = _modellfeld("Luna-Modell", router.get("luna_model", "gpt-5.6-luna"))
    router["terra_model"] = _modellfeld("Terra-Modell", router.get("terra_model", "gpt-5.6-terra"))

    print("\n-- Budget --")
    router["enabled"] = _ja_nein("KI-Router aktivieren?", bool(router.get("enabled", True)))
    router["luna_max_calls_per_day"] = int(_frage(
        "Luna-Anfragen pro Tag", str(router.get("luna_max_calls_per_day", 200))))
    router["terra_max_calls_per_day"] = int(_frage(
        "Terra-Anfragen pro Tag", str(router.get("terra_max_calls_per_day", 8))))
    router["max_cost_per_day_usd"] = float(_frage(
        "Tageskostenbudget in USD", str(router.get("max_cost_per_day_usd", 0.50))).replace(",", "."))
    router["terra_fallback_to_luna"] = _ja_nein(
        "Bei leerem Terra-Budget auf Luna ausweichen?",
        bool(router.get("terra_fallback_to_luna", True)))

    pfad.write_text(json.dumps(router, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        pfad.chmod(0o600)
    except OSError:
        pass
    print("\nGespeichert.")
    print(f"  OpenAI-Key   {maskiere(str(openai_daten.get('api_key', '')))}")
    print(f"  Luna         {router['luna_model']}")
    print(f"  Terra        {router['terra_model']}")


def aufraeumen() -> int:
    """Sucht versehentlich in Modellfeldern gelandete Schluessel und entfernt sie."""
    gefunden = 0
    pfad = ROOT / AI_DATEI
    if not pfad.exists():
        return 0
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except Exception:
        return 0
    standard = {"luna_model": "gpt-5.6-luna", "terra_model": "gpt-5.6-terra"}
    for feld, ersatz in standard.items():
        if ist_geheimnisverdaechtig(str(daten.get(feld, ""))):
            print(f"WARNUNG: In '{feld}' steht ein Wert, der wie ein API-Schluessel aussieht.")
            print(f"         Er wird durch den Standard '{ersatz}' ersetzt.")
            print("         Bitte diesen Schluessel bei OpenAI widerrufen -- er stand")
            print("         moeglicherweise im Klartext auf dem Bildschirm.")
            daten[feld] = ersatz
            gefunden += 1
    if gefunden:
        pfad.write_text(json.dumps(daten, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            pfad.chmod(0o600)
        except OSError:
            pass
    return gefunden


# ---------------------------------------------------------------------------
# Status und Test
# ---------------------------------------------------------------------------
def status_anzeigen() -> None:
    import importlib
    import config
    importlib.reload(config)

    print()
    print("=" * 70)
    print("ZUSTAND DER v8-ZUGAENGE")
    print("=" * 70)
    modus = "LIVE" if getattr(config, "OKX_LIVE_TRADING", False) else "DEMO"
    print(f"OKX             {'aktiv' if config.OKX_ENABLED else 'inaktiv'} | Modus {modus} "
          f"| Quotes {', '.join(config.OKX_ALLOWED_QUOTE_CCY)}")
    if modus == "LIVE":
        from broker_live_arming import status as arm_status
        armed, detail, _ = arm_status("okx")
        print(f"  Neue Einstiege {'FREIGEGEBEN' if armed else 'GESPERRT'} -- {detail}")
    if modus == "LIVE":
        print(f"  Live-Key      {maskiere(config.OKX_API_KEY)}")
        print(f"  Live-Secret   {maskiere(config.OKX_API_SECRET)}")
        print(f"  Passphrase    {maskiere(config.OKX_API_PASSPHRASE)}")
    else:
        print(f"  Demo-Key      {maskiere(config.OKX_DEMO_API_KEY)}")
        print(f"  Demo-Secret   {maskiere(config.OKX_DEMO_API_SECRET)}")
        print(f"  Passphrase    {maskiere(config.OKX_DEMO_API_PASSPHRASE)}")
    if getattr(config, "OKX_SETUP_HINWEIS", ""):
        print(f"  HINWEIS       {config.OKX_SETUP_HINWEIS}")

    print(f"MASSIVE         {'aktiv' if config.MASSIVE_ENABLED else 'inaktiv'} "
          f"| Key {maskiere(config.MASSIVE_API_KEY)}")
    print(f"OpenAI          Key {maskiere(getattr(config, 'OPENAI_API_KEY', ''))}")
    print(f"KI-Router       {'aktiv' if config.AI_ROUTER_ENABLED else 'inaktiv'} "
          f"| Luna {config.AI_LUNA_MODEL} | Terra {config.AI_TERRA_MODEL}")
    print(f"  Budget        {config.AI_LUNA_MAX_CALLS_PER_DAY} Luna / "
          f"{config.AI_TERRA_MAX_CALLS_PER_DAY} Terra pro Tag, "
          f"max. {config.AI_MAX_COST_PER_DAY_USD:.2f} USD")
    print()
    print(f"Krypto-Universum  max. {config.CRYPTO_UNIVERSE_ACTIVE_LIMIT} Werte, "
          f"Focus {config.CRYPTO_UNIVERSE_FOCUS_LIMIT}, "
          f"Neuberechnung alle {config.CRYPTO_UNIVERSE_REFRESH_SECONDS // 60} min")
    from crypto_strategy_mode import signal_timeframe
    active_timeframe = signal_timeframe() or "pausiert"
    print(f"Krypto-Takt       Kerzen {active_timeframe}, "
          f"Scan alle {config.CRYPTO_SCAN_INTERVAL_SECONDS // 60} min, "
          f"Positionen alle {config.CRYPTO_POSITION_CHECK_SECONDS} s")

    from settings_migration import fehlende_eingaben
    offen = fehlende_eingaben()
    if offen:
        print()
        print("Noch offen:")
        for zeile in offen:
            print(f"  - {zeile}")


def verbindungen_testen() -> bool:
    import importlib
    import config
    importlib.reload(config)

    print()
    print("=" * 70)
    print("VERBINDUNGSTEST")
    print("=" * 70)

    ok = True
    if not config.OKX_ENABLED:
        print("OKX      uebersprungen (nicht aktiviert oder Schluessel unvollstaendig)")
        if getattr(config, "OKX_SETUP_HINWEIS", ""):
            print(f"         {config.OKX_SETUP_HINWEIS}")
        ok = False
    else:
        try:
            from broker.okx import OKXBroker
            broker = OKXBroker()
            broker.connect()
            wert = broker.kontowert()
            cash = broker.verfuegbares_cash()
            anzahl = len(broker.client.instruments())
            print(f"OKX      OK -- {broker.beschreibung()}")
            frei = "unbekannt" if cash is None else f"{cash:.2f}"
            print(f"         Kontowert {wert:.2f} USD, frei {frei} {config.OKX_QUOTE_CCY}, "
                  f"{anzahl} Spot-Instrumente")
        except Exception as exc:
            print(f"OKX      FEHLER -- {type(exc).__name__}: {exc}")
            ok = False

    if not config.MASSIVE_ENABLED:
        print("MASSIVE  uebersprungen (kein Key)")
    else:
        from massive_api import MassiveClient
        ergebnis = MassiveClient().verbindungstest()
        print(f"MASSIVE  {'OK' if ergebnis.get('ok') else 'FEHLER'} -- {ergebnis.get('detail')}")
        ok = ok and bool(ergebnis.get("ok"))

    if not config.AI_ROUTER_ENABLED:
        print("KI       uebersprungen (nicht aktiviert oder kein OpenAI-Schluessel)")
    else:
        from ai_router import AIRouter
        zustand = AIRouter().status()
        # Bewusst nur Modellnamen und Zaehler -- niemals Schluessel.
        print(f"KI       Router aktiv | Luna {zustand['luna_modell']} | "
              f"Terra {zustand['terra_modell']}")
        print(f"         verbraucht heute: {zustand['budget']['luna']['anfragen']} Luna / "
              f"{zustand['budget']['terra']['anfragen']} Terra")
    return ok


# ---------------------------------------------------------------------------
# Menue
# ---------------------------------------------------------------------------
def menue() -> None:
    while True:
        print()
        print("=" * 70)
        print("TRADINGBOT v8.2 NEXUS -- EINRICHTUNG")
        print("=" * 70)
        print("  1  OKX-Zugang (Demo und Live)")
        print("  2  MASSIVE API-Key")
        print("  3  OpenAI-Schluessel und KI-Router (Luna/Terra)")
        print("  4  Zustand anzeigen")
        print("  5  Verbindungen testen")
        print("  6  Einstellungen aus Vorgaengerversion uebernehmen")
        print("  0  Beenden")
        try:
            wahl = input("Auswahl: ").strip()
        except EOFError:
            return
        if wahl == "1":
            okx_einrichten()
        elif wahl == "2":
            massive_einrichten()
        elif wahl == "3":
            ai_einrichten()
        elif wahl == "4":
            status_anzeigen()
        elif wahl == "5":
            verbindungen_testen()
        elif wahl == "6":
            from settings_migration import auto_migrate, fehlende_eingaben
            quelle, kopiert, _ = auto_migrate()
            if quelle:
                print(f"Uebernommen aus: {quelle}")
                print("Kopiert:", ", ".join(kopiert))
            else:
                print("Keine Vorgaengerversion gefunden.")
            for zeile in fehlende_eingaben():
                print("  noch offen:", zeile)
        elif wahl in ("0", "q", "quit", "exit"):
            return
        else:
            print("Unbekannte Auswahl.")


def main() -> int:
    global SICHTBAR
    ap = argparse.ArgumentParser(description="Einrichtung der v8-Zugaenge")
    ap.add_argument("--status", action="store_true", help="nur den Zustand anzeigen")
    ap.add_argument("--test", action="store_true", help="Verbindungen pruefen")
    ap.add_argument("--sichtbar", action="store_true",
                    help="Geheimnisse sichtbar tippen (nur ohne Zuschauer)")
    args = ap.parse_args()
    SICHTBAR = bool(args.sichtbar)

    aufraeumen()

    if args.status:
        status_anzeigen()
        return 0
    if args.test:
        return 0 if verbindungen_testen() else 1
    try:
        menue()
    except (KeyboardInterrupt, EOFError):
        print("\nAbgebrochen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
