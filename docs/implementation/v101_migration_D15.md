# D15 / P0 – Migrationsprüfung: Kontovermischung und verlorene Trade-Ledger verhindern

## Unabhängig gefundene Probleme

Beide Fälle bestanden bereits in NEXUS 10.0.0 und wurden bei der Prüfung der 10.1-Migration mit synthetischen Dateien reproduziert. Repro-Ergebnisse: `analysis/v101_migration_review_repro.json`.

1. **Strict-Import akzeptierte ein Ziel mit fremden Zugangsdaten.** Die Vorprüfung erkannte nur bestimmte Handelszustände. Ein Ziel mit OKX-Konto B, aber noch ohne Trade-DB, wurde zugelassen. Der Import übernahm die Trade-DB aus Konto A, übersprang die vorhandenen Konto-B-Zugangsdaten und meldete Erfolg. Demo/Paper allein verhindert diese Kontovermischung nicht.
2. **Nicht-strikter Import konnte ein gefülltes Ledger überschreiben.** Bei `overwrite=False` galt die Ziel-DB bereits als leer, wenn ihre Tabelle `decisions` leer war oder die Abfrage fehlschlug. Andere Tabellen konnten ausgeführte Trades, Fills oder offene Orders enthalten. Der reproduzierte Ziel-Trade 999 ging beim Backup der Quelldatenbank verloren.

## Gezielt implementierte Korrektur

Betroffene Dateien: `settings_migration.py`, `tests/test_v101_migration.py`.

- Strict-Import erfordert einen tatsächlich frischen Zielzustand: alle übernehmbaren persistenten Dateien, Zugangsdaten einschließlich DPAPI-Dateien, Handelsmodus, Profil, Einstellungen und Live-Freigaben werden vor dem ersten Schreibzugriff erkannt und abgelehnt. Alle SQLite-WAL-/SHM-Dateien werden ebenfalls berücksichtigt. Ein Ziel mit vorhandenen Kontodaten wird weder still überschrieben noch mit einer anderen Quelle vermischt.
- Der bestehende Sonderfall einer bereits von der WebUI initialisierten leeren Ziel-DB bleibt möglich, **ausschließlich wenn alle Benutzertabellen nachweislich leer sind**. Die Prüfung erfolgt read-only in einer Transaktion einschließlich committed WAL. Ein Schema-/Lese-/Berechtigungsfehler liefert UNKNOWN und verhindert den Override.
- Tabellenbezeichner werden korrekt gequotet. Der Ausschluss der SQLite-internen Tabellen verwendet einen exakten `sqlite_`-Präfix, damit eine legale Benutzertabelle wie `sqliteXtrades` nicht versehentlich ignoriert wird.
- Normale Import-Sicherungen, Source-State und der sichere Demo/Paper-Reset bleiben erhalten. Keine zusätzliche automatische Reparatur von Kontoidentitäten.

## Regressionstests

Neue finanzielle Verhaltenstests prüfen:

- fremde eToro-/OKX-Credentials, DPAPI-Credentials, Handelsmodus/Profil, persistente Einstellungen und SQLite-Sidecars: strikte Ablehnung vor jeder Zieländerung;
- `decisions` leer, aber Trades/Fills/Orders/Exit-Intents/Metadaten gefüllt: bestehende Ledger bleiben erhalten;
- Tabellen mit Anführungszeichen und `sqliteX`-Präfix werden tatsächlich geprüft;
- beschädigte/unbekannte Ziel-DB wird nicht überschrieben;
- vollständig leere UI-Datenbank bleibt übernehmbar;
- noch im WAL liegende bestätigte Ziel-Trades bleiben erhalten;
- zuvor vorhandene Tests für atomare Kaltmigration, Quellenintegrität, sichere Modusübernahme, Broker-/Zustandspersistenz werden mit ausgeführt.

Finaler isolierter Lauf `implementation/test_runs/migration_D15_final_7d2072e0/`: **128/128 Tests bestanden**, 17,64 Sekunden, keine Netzwerkereignisse. `test_v101_migration.py`: 26 Tests. Eine bestehende Starlette/AnyIO-Deprecation-Warnung im WebUI-Zugriffstest, kein Testfehler. JUnit, Ergebnis-JSON und Konsolenausgabe liegen im genannten Laufordner.

## Grenzen

Die vollständige Migration setzt weiterhin voraus, dass Quell- und Zielschreiber durch den aufrufenden Installationsablauf gestoppt sind. Die Leerheitsprüfung ist kein Ersatz für das Anhalten einer laufenden Zielinstanz. Der nicht-strikte generische Import ist weiterhin eine bewusste Zusammenführung; der neue Installer verwendet den strengeren frischen Zielpfad. Keine reale Brokerkontoumschaltung wurde getestet oder ausgeführt.
