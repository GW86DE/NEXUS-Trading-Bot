# NEXUS 10.2.0 – Pruefbericht

**Build:** `10.2.0-STRATEGY-EXPANSION-AND-RESILIENCE`
**Stand:** 17. September 2026
**Pruefumgebung der Paketierung:** Windows 11 x86_64, Python 3.14.7, mit pandas 3.0.5, numpy 2.5.3, requests, fastapi, pytest, scikit-learn (deutlich breiter als bei 10.1.10).

## Ergebnis der Offline-Pruefung bei Paketierung

- **Syntax:** Alle Python-Dateien des Pakets per AST geparst, 0 Fehler; keine UTF-8-BOMs.
- **Neue automatisierte Release-Checkliste** (`volltest._release_checklist`): 0 Befunde (BOM-Scan, keine alten versionsgebundenen Diagnose-Einstiege, alle RiskState-Felder klassifiziert, alle settings.html-Felder im Vertragstest).
- **Gezielte 10.2.0-Regressionen: 37 Tests, alle gruen** (auf Windows ausgefuehrt):
  - Zusatzstrategien (16): **Signal-Identitaet Bot == Backtest V6** (Funktionen aus dem paketierten V6-Skript extrahiert und auf gemeinsamen Daten verglichen), nachrechenbare Parameter-Hashes (6 verschiedene), Tageskerzen-Bewertung inkl. Verwerfen der laufenden Kerze und Mindestkerzen-Ablehnung, beide Laufzeitschalter (OKX 6 Modi, eToro 4; defekte eToro-Modusdatei faellt auf NEXUS Standard zurueck, fremde Modi abgelehnt), Bestaetigungsphrase `STRATEGIE AKTIVIEREN` erzwungen, WebUI-/Engine-Verdrahtung.
  - EXIT_IN_PROGRESS (6): der reale DOGE-Fall (Teil-Fill 1.575,48/7.334,18) wird durch eigene SELL-Fuellmengen erklaert und gibt die Domaene frei; unerklaerte Fehlmenge, Kauforder statt Verkaufsorder, nicht lesbare Orderauskunft und zu kleine Fills sperren unveraendert alles.
  - Mehrlagen-Equity-Bremse (10): reiner FX-Verfall zweier Lanes haelt nicht, nativer Handelsverlust haelt trotz FX-Maskierung, Tagesstartkurse bleiben eingefroren, Lane-Set-Wechsel startet die Basis ohne Phantom-Drawdown neu, fehlender Kurs liefert keine native Reihe, Einzellane bit-identisch zu 10.1.10, Alt-Checkpoints bleiben ladbar/gueltig, kaputte Kursdaten werden abgelehnt.
  - Gebuehren-Snapshot (5): Sicherung, ehrliches `KEINE_GEBUEHRENFELDER_GELIEFERT`, Erstsichtung stabil, Fehler erreichen nie den Handelspfad, Broker-Hook verankert.
- **Breiter lokaler Bestand:** pytest ueber die gesamte Suite (ohne 8 POSIX-`fcntl`-Module): **285 Tests + 518 Subtests bestanden**; die verbleibenden Fehlschlaege sind ausnahmslos belegte Windows-Plattform-Artefakte (fcntl fehlt, Symlink-Privileg WinError 1314, SQLite-Dateisperren WinError 32, MAX_PATH bei CreateProcess) — keine Fachfehler. Zusaetzlich die 27 gezielten 10.1.10-Regressionen erneut gruen.
- **Browser-Abnahme:** Einstellungen mit beiden Strategie-Schaltern (OKX 6 Buttons, eToro 4 Buttons, Zustandsanzeige, Phrasenpflicht) und Backtest-Seite „Strategien einfach erklaert" gerendert und bedient.
- **V6-Selbsttest:** kompletter synthetischer Lauf gruen (Bericht + ZIP, Lookahead aller 6 neuen Strategien BESTANDEN, Strategie-Infofenster in Bericht und WebUI).

## Ausdrueckliche Grenzen dieses Pruefstands

- Der **vollstaendige Volltest** (inkl. der 8 POSIX-gebundenen Suiten, Self-Test, compileall, Pi-Preflight, Shell-Syntax) laeuft wie immer **waehrend der Pi-Installation** und muss bestehen, bevor Dienste starten; ein Fehlschlag bricht ab und laesst 10.1.10 unangetastet — neu: der Updater archiviert sein Staging dann selbst.
- Keine reale Brokerorder, keine Testorder, kein kostenpflichtiger GPT-/X-Aufruf, keine Telegram-Nachricht bei der Paketierung.
- Laufzeitabnahmen nach Installation: 30-Minuten-Diagnose 1.8.1; Verhalten der Mehrlagen-Bremse und EXIT_IN_PROGRESS am echten Konto; erster echter Zusatzstrategie-Einstieg im Demo-Modus.
- Die Zusatzstrategien sind veroeffentlichte, dokumentierte Regelwerke — **kein Renditeversprechen**. Empfehlung: vor dem Live-Schalten den Backtest V6 auf dem eigenen Universum ansehen.

## Erste Pi-Volltestrunde (17.09.2026, Paket-Revision 1)

Der erste Installationsversuch auf dem Raspberry Pi 5 fuehrte den Volltest korrekt aus: **3.192 Tests bestanden, 2 schlugen fehl, das Update brach vertragsgemaess ab und liess 10.1.10 unveraendert lauffaehig.** Die neue Selbstarchivierung arbeitete dabei erstmals real: das fehlgeschlagene Staging wurde automatisch nach `ALT_FEHLGESCHLAGEN_20260917T013941_TradingBot_v10.2.0_NEXUS` verschoben — kein manuelles Aufraeumen vor dem zweiten Versuch mehr noetig. Beide Fehler waren Testvertrags-/Politikfragen, keine Fehler der neuen Fachlogik:

1. `test_v980_webui` pinnt neben den `data-field`-Einstellungen auch die **Anzahl der Settings-Panels** (12); der neue Abschnitt „eToro Aktien-Strategiemodus" macht 13. Behoben: Erwartung auf 13 fortgeschrieben und `setEtoroStrategy(` in den Aktionsvertrag aufgenommen. (Die automatisierte Release-Checkliste prueft die Feldliste — der Panelzaehler ist als weiterer Vertragsbestandteil notiert.)
2. `test_v988_cold_migration_fix2` (Preflight-Pollution): Der v9.8.8-Vertrag verlangt, dass **unerwartete Dateien im Ziel als Beweismittel AM ORT liegen bleiben** — die Selbstarchivierung hatte das ganze Staging samt der unerwarteten Datei umbenannt. Behoben: Archiviert wird nur ein SAUBERES Staging (keine unerwarteten Dateien ausser Journal/Log/Lock), dessen Offline-Test scheiterte; jede Pollution und jeder Pruefzweifel laesst alles unangetastet. Genau der Fall der heutigen Pi-Runde (sauberes Staging, Testfehlschlag) archiviert weiterhin.

Revision 2 enthaelt genau diese zwei Korrekturen. Der massgebliche Nachweis bleibt der Pi-Volltest der Installation.

Maschinenlesbarer Nachweis: `validation/NEXUS_10.2.0_TEST_EVIDENCE.json`.
