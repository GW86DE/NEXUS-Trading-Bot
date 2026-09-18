# Test Report – NEXUS 10.0.0

**Endgültiger Offline-Gesamtlauf: 2371/2371 pytest-Fälle und 244/244 Untertests bestanden. Keine fehlgeschlagenen oder übersprungenen Fälle, keine Netzwerkereignisse.** Prüfzeit der pytest-Gruppe: 65.52 Sekunden auf dem bereitgestellten Linux-x86_64-Rechner, kein Raspberry Pi.

**Releaseentscheidung bleibt NEIN.** Reale Pi-/Brokerabnahme und vollständige visuelle Desktop-/Tablet-/Smartphone-Prüfung fehlen. Der Code wurde umgesetzt und offline geprüft; dies ist kein Livehandel- oder Renditenachweis.

## Vollständiger Prüflauf

| Gate | Ergebnis |
|---|---|
| Manifest-/Releasehygiene vor Tests | Bestanden |
| Testabhängigkeiten und tatsächlicher FastAPI-TestClient | Bestanden |
| Alle alten und neuen pytest-Fälle | 2371/2371 |
| Untertests | 244/244 |
| Selftest | Bestanden |
| Compileall | Bestanden |
| Pi-Preflight im ausdrücklich hostneutralen Modus | Bestanden auf x86_64; kein ARM64-Nachweis |
| Shellsyntax aller vorgesehenen Start-/Installationsdateien | Bestanden |
| Netzwerk-Tripwire | Kein Ereignis |

Quelle: `validation/FINAL_TEST_OUTPUT.txt`, `validation/FINAL_JUNIT.xml` und `validation/TEST_GROUPS.json`. Der interne Laufname ist `final-v10-verified_fd706ee7`. Der Testmanifest-Hash identifiziert den geprüften Snapshot. Nach diesem Lauf werden ausschließlich Berichte und Prüfnachweise ergänzt; sämtliche Programm-, Test-, Frontend- und Startdateien werden vor Paketbildung nochmals bytegleich mit diesem Snapshot verglichen.

## Gruppierung des gesamten Bestands

Jede Testdatei ist genau einem Hauptthema zugeordnet. Themenüberschneidungen sind fachlich vorhanden; Zahlen werden deshalb nicht mehrfach gezählt. Die vollständige Dateizuordnung liegt in `validation/TEST_GROUPS.json`.

| Hauptthema | Bestanden / geprüft |
|---|---|
| PULSAR, GPT, Nachrichten und Researchquellen | 225/225 |
| Weitere Core-, Telegram-, Daten- und Systemregressionen | 832/832 |
| Kerzen, Strategien, Universum und Tradinglogik | 131/131 |
| eToro und genaue Positions-/Kontozuordnung | 103/103 |
| Migration, Paket, Installation und Versionsschutz | 188/188 |
| Order-Lifecycle, Ledger, Reconciliation und Recovery | 467/467 |
| Risiko und Persistenz | 82/82 |
| WebUI, Auth, Analysejobs und Anzeigequalität | 156/156 |
| OKX und Brokerverträge/Health | 187/187 |

Summe: 2371 pytest-Fälle. JUnit zählt zusätzlich die 244 Untertests in seiner Suite-Kopfzahl; diese werden hier separat ausgewiesen. Die JavaScript-Verhaltenssuite läuft in einem pytest-Wrapper und wird ebenfalls nicht zusätzlich zur pytest-Gesamtsumme addiert.

## Neue NEXUS-10-Prüfdateien

| Datei | Bestanden / geprüft |
|---|---|
| `test_v100_analysis_jobs.py` | 24/24 |
| `test_v100_broker_health.py` | 23/23 |
| `test_v100_etoro_risk_projection.py` | 9/9 |
| `test_v100_execution_recovery.py` | 37/37 |
| `test_v100_frontend.py` | 1/1 |
| `test_v100_risk_persistence.py` | 29/29 |
| `test_v100_strategy_pulsar.py` | 28/28 |
| `test_v100_webui_backend.py` | 25/25 |

Die Frontenddatei führt unter Node.js v24.19.0 die tatsächlichen lokalen JavaScript-Dateien mit kontrollierten DOM-/Datenfixtures aus. Die zugehörige Node-Suite enthält 19 Verhaltensfälle zu Scheduler, fehlenden Werten, Kontext, GPT-Phasen, Begrenzung und Escaping. Sie ersetzt keinen echten Browser-Layout-/Touchtest.

## Nachgewiesene Verhaltensfälle

- Orderannahme und -ablehnung, Teilfüllungen, Cancel, verspätetes ACK, verspäteter nativer Fill, doppelte/umgeordnete Events und unveränderte Idempotenzbelege.
- Native Order-/Client-/Fill-/Positionszuordnung getrennt nach Broker, Konto und Demo/Live; kein Symbol-only-Matching von Fremdbestand.
- Tatsächlicher lokaler Prozessabbruch vor Ledger/Lifecycle-Commit, anschließend Restart und genau eine wirtschaftliche Buchung nach Replay.
- Konkurrierende Trackerinstanzen/-prozesse, kumulierte Mengen/Werte/Gebühren, semantisch beschädigte JSON und Wiederherstellung ohne Löschung der Quelle.
- Datei-fsync-/Verzeichnis-fsync-Fehler einschließlich nach sichtbarem Replace; mehrere Fehlerorte nach bewiesenem OKX-Fill mit erhaltenem Schutzversuch und lokaler Einstiegssperre.
- Fehlgeschlagener P&L-Write, Nebenmutation, Restart, UNKNOWN-Vorbeleg und exakter Replay; keine Freigabe aus einem leeren Folgepoll.
- Tagesbasis bei wechselnden Positionen, bestehender Tagesstopp, Methoden-/Währungs-/Kontowechsel, Mitternacht und gebundene eToro-Aufrufstellen.
- Originalbackup vor Risikomigration, erneuter Lauf und Sicherungsfehler; kein Umgehen durch spätere normale Mutation.
- Fehlende statt explizit leere Brokerantwort, alte Instrumentantwort nach neuerem Fehler, GET-/POST-Retrytrennung und REST-/WS-Frische.
- Backtest-Endverkauf, offene Equity-Delle, Gapentry/-exit, Kosten und versionierte Ergebnisannahmen.
- Modell-/Prompt-/Schema-/Quellenzeit-/Toolcachebindung, Nichtstart, realer lokaler Versand, Timeout, spätere Usage und PULSAR-Precheckfehler.
- Authentifizierung, Passwortrotation, fehlende Credentials, CSRF, read-only Diagnose einschließlich Kaltimport ohne SQLite-Erzeugung, Scope-/Zukunftsheartbeatkonflikte, Suche und Fehlerzustände.
- Tatsächliche lokale Analysechilds mit Threadlimit, Timeout, Nachkommenabbruch, Ausgabelimit und zeitnah sichtbarer Fortschrittszeile; PID-Wiederverwendung und Kernel-Namespacebindung.

Vorhandene frühere NEXUS-Fixtures und Replaytests bleiben Bestandteil des Gesamtlaufs, darunter SUI-, eToro-, Dubletten-, Startup-, native Exit-, Wallet-/Ownership- und Teilbetragsfälle. Zusätzlich erzeugte Fehlerdaten sind synthetisch und werden nicht als aktuelle Kontodiagnose ausgegeben.

## Baseline und gefundene Regressionen

Die unveränderte FIX1-Basis bestand 2.195 pytest-Fälle und 243 Untertests. Sieben zusätzliche gezielte Gegenproben reproduzierten die ursprünglichen bekannten Lücken. Ausgangslog: `validation/BASELINE_TEST_OUTPUT.txt`.

Ein erster Integrationstest fand 11 Fehler bei 2.330 Fällen: acht Chart-/Encodingtesteinbettungen benötigten die tatsächlich gemeinsame JavaScript-Abhängigkeit, ein überholter Quelltext-Oracle verlangte einen nicht mehr zulässigen Recoveryzustand, und zwei Prozessidentitätstests deckten einen echten PID-Namespacefehler auf. Ein späterer Paketlauf bestätigte diese letzten zwei Fehler erneut; ein zwischenzeitlich grüner Einzeltest wurde ausdrücklich nicht als Entwarnung verwendet.

Die PID-Ursache wurde anhand von Kernelbelegen nachvollzogen und mit pidfd-Auflösung korrigiert. Weitere unabhängige Reviews fanden die fehlende eToro-Scopeverdrahtung, ein zu früh aufgehobenes Risikopersistenzgate, einen Migrationsumweg nach fehlgeschlagenem Backup, gepufferte Fortschrittsmeldungen, den kalten SQLite-Schreibimport und UI-Kontext-/Nullwertfehler. Für die tatsächlichen Produktkorrekturen wurden neue Verhaltenstests ergänzt. Details stehen im Implementierungsbericht und den Modulnachweisen. Die finalen Zahlen oben stammen ausschließlich vom nach diesen Korrekturen geprüften Stand.

## Testumgebung und Warnungen

Python 3.12.14, Node.js v24.19.0, Linux x86_64. Installierte Python-Paketversionen stehen vollständig in `validation/TEST_GROUPS.json`. Bestehende Produktionsabhängigkeiten wurden nicht um einen neuen Dienst, Message Broker oder UI-Frameworkstack erweitert.

3 Warnungen im Gesamtlauf: eine Deprecation-Warnung des Starlette-Testclients zur AnyIO-BlockingPortal-Aliasverwendung und zwei Python-Warnungen beim absichtlich prozessübergreifenden Fork-Test. Die tatsächlichen Childtests liefen erfolgreich; die Warnungen werden nicht als Hardware- oder Langzeitbeweis umgedeutet.

## Nicht durchgeführt

Keine echten Brokerorders, keine bezahlten GPT-Requests, keine Telegram-Nachricht, kein Zugriff auf aktuelle Kontozustände, kein realer Servicewechsel und kein Pi-Stromausfalltest. Die erreichbare Browserumgebung blockierte den lokalen Testserver mit `ERR_BLOCKED_BY_CLIENT`. Daher bleiben die visuelle Desktop-/Tablet-/Smartphone-Matrix, echte Fokus-/Touchbedienung und der Vergleich gerenderter Screenshots offen. HTML-Struktur, vorhandene Routen, responsive CSS-Regeln und kontrollierte Rendererfehler sind automatisiert geprüft.

Es wurden keine Pi-P95/P99-Werte, keine Prognosegüte und keine bessere Handelsrendite erfunden. Die offene Abnahme verhindert die Freigabe für unbeaufsichtigten Handel trotz bestandenem Offline-Gesamtlauf.

## Abschließende Paketprüfung

Nach Ergänzung der Berichte wurden 27/27 Paket-/Manifest-/Policytests erneut bestanden, ohne Netzwerkereignis. Das konkrete ZIP wurde mit dem ausgelieferten `release_unpack.py` entpackt: alle 702 Manifestdateien stimmen, eine Wiederholung erhält zusätzlich vorhandenen lokalen Zustand unverändert, ein verändertes Ziel wird abgewiesen und eine falsche Archivprüfsumme ebenfalls. Der Programm-/Test-/Frontendstand ist bytegleich mit dem oben geprüften Gesamtlauf. Archiv-SHA256 und maschinenlesbare Ergebnisse liegen neben dem ZIP in `NEXUS_10.0.0_SHA256.txt` und `NEXUS_10.0.0_PACKAGE_CHECK.json`.
