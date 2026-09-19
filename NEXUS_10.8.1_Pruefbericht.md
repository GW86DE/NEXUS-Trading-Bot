# NEXUS 10.8.1 – Prüfbericht

**Build:** `10.8.1-RESTZEILEN-SCHNAPPSCHUSS-UND-KERZENCURSOR` · Stand: 19.09.2026 · Basis 10.8.0

## Vor dem Bau: dieselben Schritte wie der Pi-Volltest, plus Je-Test-Vergleich

- **Statische Hygiene komplett** (`volltest._static_hygiene()`): 0 Befunde;
  Release-Checkliste leer, alle `REQUIRED_RELEASE_FILES` vorhanden.
- **Node-Frontendsuite**: 30/30 (keine JS-Änderung in 10.8.1).
- **Netzwerkwächter** über PULSAR-Worker-Modulen, den 10.6.0/10.7.0/10.7.1/10.8.0-Suiten
  und `tests/test_v1081_restzeilen.py`: kein `http`-, kein `dns`-Ereignis.
- **Je-Test-Vergleich mit POSIX-Shims** gegen das ausgelieferte 10.8.0-Paket
  (`NEXUS_10.8.0_Quellpaket.zip`): dieselben Module wie auf dem Pi; jeder neu
  rote Test bricht den Bau ab (Ergebniszeile im Bauprotokoll).
- **Kompletter Testbestand isoliert, zweimal** (232 Module je Prozess, 6 parallel):
  - Lauf 1 (Code fertig, Pins/Wrapper/Baseline 10.8.1, vor den Begleitdokumenten):
    **3.321 bestanden**, 60 fehlgeschlagen, 7 Fehler, 9 übersprungen; 0 Netzversuche;
    **neu rot gegenüber 10.8.0 (Lauf 2): keine; neu grün: keine.**
    3.321 = 3.291 (10.8.0) + 30 neue Tests (Stand Lauf 1).
  - Lauf 2 (finaler Baum mit Begleitdokumenten, Kopierweg der Journal-Migration,
    31 neue Tests): **3322 bestanden**, 60 fehlgeschlagen,
    7 Fehler, 9 übersprungen; 0 Netzversuche;
    neu rot gegenüber Lauf 1: keine; gegenüber 10.8.0: keine.
    Die 26 roten Module sind die dokumentierten Windows-Artefakte (fcntl, flock,
    Symlinks, systemd, Installerpfade, Manifest nur zur Bauzeit), identisch mit 10.8.0.
  - Zahlen: `validation/NEXUS_10.8.1_TEST_EVIDENCE.json`.

## Was die neuen Tests belegen (31, `tests/test_v1081_restzeilen.py`)

| Korrektur | Beleg |
|---|---|
| 1 Schnappschuss | Nicht lesbarer Guthabenstand: Takt abgebrochen (`ok False`, `abgebrochen`), kein Ledgerabgleich, Position ohne Fehlmessung, kein Beleg, kein gesperrter Coin, Bereitschaft `reconciliation` offen mit „abgebrochen"; `positionen()`-Fehler ebenso; vollständiger Takt gibt frei; leerer gültiger Schnappschuss bleibt eine Messung (10.6.0-Verhalten) |
| 2 Zwei Messungen | Ledgerzeile ohne Position: 1. Messung → `BESTAND_FEHLT_UNBESTAETIGT`, kein Beleg, Konto vollständig; 2. Messung nach 9 s → nichts; nach 31 s → Beleg PENDING + BROKER_STATE_UNKNOWN, Zeile bleibt offen, SUI gesperrt; Wiedersicht setzt den Zähler zurück; Frist 0 verlangt zwei Messungen |
| 3 Rückweg | Restzeile mit offenem Beleg (Trade-85-Lage): 1. Wiedersicht → RESIDUAL_EXPOSURE mit `bestandsbeleg PENDING`, nichts geschlossen; 2. nach 121 s → Beleg `RESOLVED` mit `BALANCE_RESTORED:…:ledgerzeile;snapshots=2`, Rest als RESIDUAL belegt abgeschlossen (kein Ausstiegspreis, kein Netto), SUI frei, Telegram-Text; nach 9 s nichts; halbe Botmenge nichts (Zähler zurück); `pending_balance_gap` liest nur |
| 4 Staubregel | Rest 0,001765 SUI bei 5.758,70 SUI Fremdbestand (bis 10.8.0: 4.780 USDC „Exposure") wird als Staub belegt abgeschlossen; Rest 4,77 SUI (3,96 USDC) bleibt RESIDUAL_EXPOSURE, nur der Beleg heilt, Zeile bleibt offen |
| 5 Fill-Historie | Registrierter EXIT mit fremder `entry_order_id` → keine Buchung, keine Warnung; Altregistrierung ohne Linie, Fill an Trade 90 gebunden (`gebuchte_exit_fills`) → ausgeschlossen, keine Warnung; Ledger lehnt ab → ein Versuch, eine Warnung ohne Traceback, `(trade, order)` gemerkt |
| 6 Kerzencursor / Journal | `_abschluss` liefert `decision_id` und `vor_signal` (True ohne Kerzenzeit, False mit); Vor-Signal-Sperre: Cursor auf `cutoff − 5 min`, zweiter Scan derselben Kerze entscheidet nichts; Nach-Signal-Sperre kein Cursor; ohne decision_id kein Cursor; Spiegel rotiert ab der Obergrenze nach `.1.jsonl` (Minimum 1 MB, kaputter Wert → 20 MB); Migration kürzt auf ganze Zeilen und meldet; Kopierweg übernimmt nur den Schwanz, kleine Spiegel 1:1; Verdrahtung in `migrate_from` vor `_force_paper`; Diagnose kennt `.1.jsonl` |
| Pins | `DECISION_JOURNAL_MAX_MB 20`, `OKX_POSITION_MISSING_CONFIRM_SECONDS 30`, `OKX_POSITION_RESTORED_CONFIRM_SECONDS 120`; Engine kennt die vier neuen Zähler |

Angepasst: `tests/test_v1017_okx_boundary.py::test_ledger_only_missing_never_closed`
(zwei Messungen, Frist 0 – die Aussage „nie geschlossen" bleibt), Baseline-Versionspin
in `tests/test_v1080_architektur.py`, Versionspins `test_v1019`/`test_v976`.

## Architektur: gemessen

| Kennzahl (eigener AST-Graph, ohne Tests) | 10.8.0 | 10.8.1 |
|---|---:|---:|
| Module / Importkanten | 336 / 1.090 | 336 / 1.092 |
| Import-Zyklus-Komponenten / Module darin | 2 / 21 | 2 / 21 |
| größte Komponente | 11 | 11 |
| Schichtverstöße | 93 | 93 |
| Adapter → Kern / WebUI → Broker / PULSAR → außen | 6 / 0 / 35 | 6 / 0 / 35 |
| Module > 1.500 Zeilen | 9 | 9 |
| crypto_engine (Zeilen) | 5.939 | 6.228 (+4,9 %) |

Die zwei neuen Kanten: `okx_accounting → decision_analytics` (`db_pfad`, damit
`pending_balance_gap` keine Datenbank anlegt) und `settings_migration → config`
(Journal-Obergrenze) – beide innerhalb der bestehenden Schichtregeln, keine neue
Zykluskante. Die Leitplanke `test_v1080_architektur` (33 Tests) ist grün auf der
nachgezogenen Baseline.

## Bewiesen vs. offen

**Bewiesen (offline, Testdoubles):** Die sechs Mechanismen aus der Analyse sind
an der Wurzel korrigiert und einzeln belegt; kein bestehender Test hat sich in
seiner Aussage geändert. Kein Verkauf wird erfunden; Restzeilen werden nie
automatisch verkauft; ein fehlender Bestand ist kein Verkaufsbeleg.

**Offen (nur am Pi prüfbar):** die tatsächliche Aufloesung von Trade 85 (hängt
an der belegten Abstammungslinie von Trade 81), die Staubklassifizierung der
ETH-Reste 86/91, die Entscheidungsfrequenz je Kerze im Betrieb, die
Migrationszeile zum Journal-Spiegel.

## Invarianten

UNKNOWN bleibt UNKNOWN; keine Gebühren als 0; kein synthetisches Kerzenvolumen;
kein TLS-Bypass; kein Umgehen von Anbietersperren; PULSAR/X/GPT/StockTwits ohne
Orderbefugnis; CSP `script-src 'self'`; keine Klarnamen in Diagnose-Exporten;
versiegelter Baum, kein Handpatchen.
