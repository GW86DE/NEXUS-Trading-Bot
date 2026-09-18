# NEXUS 10.1.0 – Prüfbericht

**2584/2584 Tests und 251/251 Untertests bestanden.** Zusätzlich **59/59** Verhaltenstests der Diagnose 1.1.0. Es wurden keine echten Broker-, Provider-, GPT- oder Telegram-Anfragen ausgelöst. Die uneingeschränkte Livefreigabe bleibt wegen der in KNOWN_ISSUES.md genannten fehlenden Nachweise **NEIN**.

## Vollständiger gemeinsamer Lauf

- Lauf: `v101-release_366d9047`; eigener Quellsnapshot, eigener Zustand, entfernte Credentials, Netzsperre mit protokollierten Verstößen.
- Pytest: 2584 bestanden,0 fehlgeschlagen,0 übersprungen; 251 zusätzliche Untertests. Pytest-Laufzeit 63.39s.
- Release-Hygiene, echte Testabhängigkeiten/TestClient, Selbsttest, Compileall, hostneutrale Pi-Vorprüfung und Bash-Syntax: bestanden.
- Die Release-Hygiene blieb aktiviert. Ein Zufallstreffer des Alt-Broker-Textscans in komprimiertem Diagnose-Base64 wurde durch saubere Verpackung als lesbare Python-Datei plus kleinen Starter behoben; keine Prüfausnahme eingeführt. Der eigenständige externe Diagnosestarter bleibt separat byteverifiziert.
- 7 Warnungen: eine vorhandene Starlette/AnyIO-Abkündigung und sechs Hinweise des Testhosts zu Fork aus einem mehrthreadigen Prozess. In den Tests trat kein Deadlock auf; dies ist kein Langzeitnachweis für den Pi.
- Die Paketierung prüft, dass alle ausgelieferten Programm-/Test-/Assetdateien dem final getesteten Stand entsprechen. Berichte und Validierungsbelege werden danach ergänzt.

## Gruppen

Die folgende Gruppierung ist überschneidungsfrei nach Testdatei. Einzelne Integrationstests decken mehrere Module ab; sie werden hier nur einmal gezählt.

| Gruppe | Bestanden |
|---|---:|
| Datenquellen, PULSAR und GPT | 262/262 |
| Weitere bestehende Regressionen/Systemmodule | 1021/1021 |
| OKX, Kerzen, Strategien und Universum | 376/376 |
| eToro-spezifische Pfade | 126/126 |
| Risiko, Persistenz, Migration und Installation | 260/260 |
| Order-Lifecycle, Buchung und Reconciliation | 395/395 |
| WebUI, Status und Diagnose | 144/144 |
| **Gesamt** | **2584/2584** |

## Neue Verhaltenstests

| Testdatei | Bestanden |
|---|---:|
| test_v101_candle_observation.py | 31/31 |
| test_v101_etoro_protection_evidence.py | 23/23 |
| test_v101_evidence_packet.py | 27/27 |
| test_v101_fmp_context.py | 24/24 |
| test_v101_halt_diagnostics.py | 6/6 |
| test_v101_massive.py | 37/37 |
| test_v101_migration.py | 26/26 |
| test_v101_risk_basis_review.py | 15/15 |
| test_v101_webui_explanations.py | 24/24 |
| **Neue Tests** | **213/213** |

## Reale Fehler-Replays

- Fünf ursprüngliche GPT-Pakete: alter gespeicherter Precheck-Hash exakt reproduziert; korrigierte Ansicht enthält neueste vollständige OHLCV-Daten bis 11.09.2026 und Volumen.
- Alle 15 Massive-Nachrichten: vorhandene Beschreibung, URL und Zeitpunkt bis in den integrierten Worker erhalten; Rohbelege unverändert.
- Original NBIS-/MU-Antworten: Jahreslücke 2021→2025 nicht als Jahreswachstum; EV/EBITDA korrekt gemappt; Konflikte in den GPT-Eingaben sichtbar.
- Neun Original-Kerzenentscheidungen: Indikatoren, vollständige Instrumentidentität, Candle-Close, Cursor und NO_SIGNAL unverändert reproduziert.
- PEP-/Legacy-Risikozahlen: ohne tatsächliche Rundungs-/Kontobelege weiterhin reviewpflichtig. Eine hypothetische Rundungsregel im Test ist ausdrücklich kein echter Brokerbeleg.
- Massive-Sechser-Spitze: keine sechs HTTP-Aufrufe gleichzeitig; Prozesse/Keys teilen Reservierung und Cache. Timeout/Neustart/429/Uhrkorrektur können Budget nicht zurücksetzen.

## Migration und Persistenz

Strikte Fremdkonto-/Zielkonfigurationsprüfung, volle Trade-/Fill-/Ordertabellen trotz leerer decisions-Tabelle, SQLite-WAL-Übernahme, beschädigte Daten und belegte Zustandswiederholung wurden getestet. Neue Massive-DB und Schutzjournal bleiben erhalten. Die Hypothese einer fehlenden historischen Tagesbasis wird nicht als Migrationserfolg ausgegeben.

## WebUI

Tatsächlicher JavaScript-Renderer wurde unter Node ausgeführt. Belegte Genehmigung/Ablehnung, keine Ausführung, Teilfüllung, vollständiger Fill, unklarer Ausgang, fehlende Messwerte, lange Begründungen und HTML-Injektion wurden geprüft. Deutsche Hauptgründe, technische Details, historische Providerfehler, Risk+Marktsperre, Kontotrennung und read-only Datenanreicherung sind Bestandteil der Tests.

**Nicht durchgeführt:** echte visuelle/Desktop-/Tablet-/Smartphone-/Touchabnahme. Der Cloudbrowser blockierte die lokale Vorschau zunächst mit ERR_BLOCKED_BY_CLIENT und anschließend ausdrücklich per URL-Policy. Keine Umgehung und keine erfundenen Screenshots. Die vorbereitete Vorschau mit 390/820/1440 px ist allein kein Beleg für eine Sichtprüfung.

## Diagnose 1.1.0

59/59 bestanden. Standard 30 Minuten/Sofortexport, Unterbrechung mit Teilbericht, zwei Starts mit verschiedenen ZIP-Namen, sichere Maskierung einschließlich neuer Belege, read-only SQLite-Onlinebackup/WAL, Exportgrenzen, fehlende Dateien/Dienste, unveränderte PULSAR-Pakete nur einmal aufwendig maskieren, veränderte Pakete neu erfassen und unbekannte Providerzähler statt falscher Null.

Ein echter 30-Minuten-Lauf auf deinem Pi wurde hier nicht durchgeführt. Das Werkzeug ist dafür enthalten.

## Installer und ZIP

15/15 bestanden. Tatsächliche Bash-Datei, eingebettetes ZIP, Prüfsumme, Entpacken/Wiederholung, beschädigter Download, unveränderte Zustände, Versions-/Architektur-/Root-Guards, Updaterargumente und Exitcodes. Systemdienste wurden durch Fakes ersetzt, nicht auf dem Pi installiert.

Das separate PACKAGE_CHECK.json belegt zusätzlich alle Quellhashes nach Entpacken, Schutz des bisherigen Zustands bei Wiederholung, Abweisung veränderter Zieldateien und falscher Prüfsumme. Die tatsächliche ARM64-/systemd-Installation und neue Echtbroker-Ausführungen sind erst auf dem Zielsystem prüfbar.

## Zwischenstände / entdeckte Fehler

Die erste Gesamtsuite meldete drei noch alte Versionsannahmen, einen alten Logbuch-Literalvergleich und einen Hygiene-Verstoß. Die veralteten Assertions wurden passend zur neuen Version/präziseren Ausführungsanzeige aktualisiert; echte Verhaltenstests und der Schutz des 10.0.0-Ordners bleiben bestehen. Der stille Schreibfehlerpfad wurde diagnostizierbar gemacht. Die folgenden Volltests umfassen diese Korrekturen.

Zusätzlich wurden vor dem finalen Lauf ein UI-Einrückungsfehler, die falsche FMP-Zeitreferenz, fehlende Zähler als 0 sowie zwei ältere Migrationslücken korrigiert. Siehe Umsetzungsteilberichte mit konkreten Testnamen. Keine bekannte fehlgeschlagene Prüfung wurde unterdrückt.

## Belegdateien

`validation/FINAL_JUNIT.xml`, `FINAL_TEST_OUTPUT.txt`, `FINAL_RUN.json`, `TEST_GROUPS.json`, `DIAGNOSTIC_JUNIT.xml` und gegebenenfalls `INSTALLER_JUNIT.xml`; Quelländerungen und Snapshotidentität in `SOURCE_CHANGESET.json`. Frühere 10.0.0-Belege liegen ausschließlich als historischer Vergleich unter docs/history/10.0.0.
