# NEXUS 10 – Order-/Fillprojektion und atomare Ledgerquittung

Arbeitsbasis: unveränderte NEXUS 9.9.0-FIX1, extrahiert aus dem bereitgestellten Installer. Umsetzung ausschließlich im Arbeitsbaum `TradingBot_v10.0.0_NEXUS`. Berichtsempfehlungen N03, N05 und N06 wurden nochmals gegen den tatsächlichen Code geprüft.

## Ergebnis und Abgrenzung

**N03 umgesetzt und offline getestet. N05 bewusst auf einen gemeinsamen Ledger-/Lifecyclecommit verkleinert; eine allgemeine Risiko-/Journal-Outbox wurde nicht eingeführt. N06 um konkrete Replay-, Parallelitäts- und Prozessabbruchtests ergänzt.**

Keine neue Brokeranbindung, keine API-Schreiboperation, kein GPT-Aufruf, kein neues Workerframework, keine Umschreibung bestehender Handelsdaten und keine neue Datenbanktabelle. `broker_exit_journal.py` bleibt unverändert. Die bestehenden Risk-/Exitjournal-/Positions-Recoverymechanismen bleiben in Betrieb; sie werden durch diesen Teilauftrag nicht als atomar mit dem Ledger behauptet.

## Verifizierte Ausgangsfehler

Vier neue identische Verhaltensprüfungen wurden zuerst gegen eine separate, unveränderte 9.9.0-FIX1-Quellkopie ausgeführt. Alle vier scheiterten an der erwarteten fachlichen Invariante, ohne Netzwerkereignis:

| Fehler | Ergebnis der Ausgangsversion | Erwartetes Verhalten |
|---|---|---|
| C02: Vollständiger terminaler Orderbeleg, danach schwächere Meldung gleicher Menge | `evidence_complete` fällt auf 0; starkes Evidence-JSON wird ersetzt | Vorhandene validierte native Fakten bleiben gültig |
| C03: Zwei vorher geladene Trackerinstanzen bestätigen A und B | Datei enthält nur B | Beide bereits verarbeiteten Fill-IDs bleiben erhalten |
| Zusätzliche Zustandsregression: Partialfill, danach verspätetes ACK | Status wird OPEN | Ausgeführte Teilmenge und PARTIALLY_FILLED bleiben sichtbar |
| Zusätzliche Checkpointregression: Menge 10/Wert 1060/Gebühr 3, danach alter Commit 4/400/1 | Alter Commit überschreibt den höheren Fortschritt | Der vollständige höhere Checkpoint bleibt erhalten |

Beleg: `implementation/test_runs/execution-baseline-counterexamples_90d1d9f4/{result.json,junit.xml,output.txt}`. Die vier roten Tests sind bewusst erzeugte Ausgangsbelege, keine verbleibenden Fehler des neuen Codes.

## Änderungen

### NEXUS-IMP-N03A – Monotone, faktenbasierte Orderprojektion

Datei: `execution_lifecycle.py`, Funktionen `accepted`, `observe`.

Ein nachträgliches ACK verändert einen bereits vorhandenen Partial-/Ausführungsstatus nicht mehr. Die Broker-ID kann weiterhin nachgetragen werden.

`observe` prüft weiterhin Konto, Umgebung, Client-ID, Instrument, primäre Brokerorder-ID, positive native Fillmenge und Preis sowie die Unveränderlichkeit bereits gespeicherter Ausführungsfakten. Zusätzlich darf die Summe nativer Fills die bekannte kumulierte Menge nicht übersteigen. Sinkende kumulierte Mengen werden weiterhin als veraltete Beobachtung behandelt.

Bei derselben bereits vollständig belegten Menge verliert eine schwächere REST-/WS-Zusammenfassung weder die bekannte Vollständigkeit noch den belegten Nettobestand und das stärkere Evidence-JSON. Das Ereignisjournal markiert `preserved_stronger_evidence` und die tatsächlich beobachtete Vollständigkeit. Dies ist kein pauschales Maximum über Boolesche Werte: Eine größere Ausführungsmenge erbt die alte Vollständigkeit ausdrücklich nicht. Eine neue vollständig validierte Gebührenergänzung kann den Nettobestand verändern und setzt dann die alte Buchungsquittung zurück.

Cancel plus verspäteter nativer Fill darf anhand passender einzelner Ausführungsbelege fortgeschrieben werden. Die bekannte Terminalität bleibt erhalten; die Menge wird als terminaler Partialfill oder vollständig ausgeführt dargestellt. Eine zuvor eindeutig abgelehnte Order mit widersprechendem Fill bleibt ein expliziter Konflikt. Alter, Retryzahl und fehlende Antworten erzeugen keine künstliche Terminalität.

Numerisch identische Mengen wie `10.0` und `10` verlieren ihre Accountingquittung nicht mehr allein wegen unterschiedlicher Textdarstellung.

### NEXUS-IMP-N03B – Trackerzustand unter einer gemeinsamen Sperre

Datei: `fill_tracker.py`, insbesondere `_refresh_locked`, `_merge_cumulative`, `save`, `seed`, `prepare`, `commit`, `recover_from_ledger`.

Reload, Merge und Schreiben verwenden dieselbe bereits vorhandene Prozess-/Threadsperre. Native Fill-IDs werden vereinigt. Kumulierte Menge, Orderwert und kumulierte Gebühren werden gemeinsam behandelt: Ein alter kleinerer Checkpoint kann weder eine höhere Menge noch deren zugehörigen Wert beziehungsweise Gebühren überschreiben. Derselbe Mengenstand mit widersprüchlichem Wert/Gebührenbeleg wird abgewiesen.

Ein spät initialisiertes Objekt kann einen bereits initialisierten Tracker nicht neu mit aktuellen Brokerfills seeden. Dadurch werden während eines Neustarts eingegangene neue Fills nicht versehentlich als schon verarbeitet markiert. Ein fehlgeschlagener Commit beziehungsweise Seed bestätigt den Fill nicht im Arbeitsspeicher. Recovery bleibt ausschließlich aus dem Ledger erlaubt; beschädigte Quelldateien werden vor Reparatur weiterhin gesichert. Ein zwischenzeitlich durch einen anderen Writer reparierter Tracker wird erneut eingelesen.

Die bisherige lexikografische Begrenzung auf die letzten 5000 IDs wurde entfernt. Die alphabetische Sortierung konnte keine sichere Replay-Aufbewahrung begründen. Bestehende IDs werden nicht verworfen.

Pi-Aufwand: Bei unveränderter Datei wird das JSON nicht für jeden historischen Fill neu geparst. Ein Fingerprint aus Inode, Größe, mtime und ctime erkennt Änderungen unter der Sperre. Der Fingerprint beim Laden stammt aus `fstat` desselben geöffneten Dateideskriptors; ein paralleles `replace` kann deshalb nicht alte Bytes mit dem neuen Dateifingerprint verknüpfen. Der Test mit 100 bekannten Fills weist null erneute Checkpoint-Leseoperationen nach. Diese Optimierung überspringt keine neu geschriebenen Checkpoints.

### NEXUS-IMP-N05A – Ledger und Lifecyclequittung in einer Transaktion

Dateien: `trade_ledger.py` und `execution_lifecycle.py`.

Die Ausgangsversion schrieb den wirtschaftlichen Ledgerbeleg und bestätigte dessen Accountingwirkung anschließend über eine neue Verbindung. Die beiden Datenbestände liegen bereits in derselben SQLite-Datenbank. Für diesen konkreten Nachlauf ist deshalb ein gemeinsamer Commit einfacher und enger als eine zusätzliche Outbox.

Neue interne Schnittstelle: `execution_lifecycle.confirm_accounted_on(con, *, broker, account, environment, order_id, client_id="")`. Sie erstellt keine Verbindung, führt kein DDL aus, committet nicht und verlangt eine aktive Transaktion. Die existierende öffentliche Funktion `confirm_accounted(...)` bleibt für Restart-/Broker-Recovery kompatibel und verwendet dieselbe Fachlogik in einer eigenen Transaktion.

`trade_open` und `trade_close` einschließlich ihrer Fillmerge-, Partial-, Replay- und Alias-Erfolgspfade bestätigen den exakt passenden Lifecycle vor demselben SQLite-COMMIT. `PRAGMA synchronous=FULL` wird vor dem Transaktionsbeginn gesetzt. Ein Fehler nach der internen Quittierung, aber vor dem Commit, hinterlässt weder eine neue wirtschaftliche Buchung noch eine isolierte Lifecyclefreigabe. Erfolgreiche Rückkehr bedeutet, dass beide Fakten gemeinsam committet wurden. Die separate nachträgliche Wrapperquittung entfällt.

Bei fehlender Brokerorder-ID wird für einen Einstieg nur die exakt gespeicherte Client-ID akzeptiert. Leere Brokerorder-IDs werden nicht wie eine gemeinsame Identität sämtlicher Trades desselben Symbols behandelt. Bei einem Exit ohne eigene Brokerorder-ID bleibt die Lifecyclequittung offen; die Entry-Client-ID wird nicht zweckentfremdet.

**Nicht enthalten:** Eine atomare Gesamttransaktion über getrennte Risiko-JSON-, Positions-JSON- und Exitjournal-Dateien. Ebenso keine garantierte Exactly-once-Nachricht an Telegram oder Exactly-once-Netzübermittlung. Diese bereits bestehenden Grenzen werden nicht durch eine zusätzliche unbelegte Zusage ersetzt.

## Migration und Kompatibilität

- Keine neue Tabelle, kein neues Schemafeld, keine geänderte Broker-/Trade-/Fill-ID.
- Trackerformat bleibt Schema 2. Vorhandene Werte werden unter Lock vereinigt; vorhandene native IDs bleiben erhalten.
- Die bestehenden `execution_orders`, `execution_fills`, `execution_events` und `trades` bleiben lesbar. Verwendete Statusnamen existierten bereits.
- Ein altes Ledger ohne Lifecycle-Tabelle bleibt gültig; die Transaktionsquittung erstellt daraus keine fiktive Orderhistorie.
- Die vorhandenen Accountalias-, Partialrest-, Gebührenqualitäts- und externen Positionseigenschaften bleiben unverändert.
- Restore/Rollback verlangt wie bisher einen gestoppten Writer und einen zusammengehörigen Zustandsstand. Ein laufender Prozess darf nicht mit unabhängig zurückgesetzten Ledger-/Trackerdateien gemischt werden.

## Tests und tatsächliche Ergebnisse

Endgültiger fachlicher Lauf: `implementation/test_runs/execution-v10-complete_41b678bd`.

**282/282 bestanden, 0 Fehler, 0 übersprungene Tests, kein registrierter Netzwerkversuch. Laufzeit 8,76 Sekunden in der isolierten Linux-Testumgebung.**

| Gruppe / Testdatei | Ergebnis | Abgedeckte Risiken |
|---|---:|---|
| `test_v100_execution_recovery.py` | 28/28 | Vier Ausgangsgegenfälle, Vollständigkeits-/Nettofortschreibung, verspätetes ACK, Cancel/Fill-Rennen, Duplikate, vertauschte Reihenfolge, zwei Prozesse/mehrere Threads, Checkpointfehler, echte Prozessbeendigung, atomare Entry-/Exitquittung, Demo-/Live-Trennung, fehlende Broker-ID, alter Ledger |
| `test_v980_execution_lifecycle.py` | 27/27 | Reservierung, ID-/Domänentrennung, Partialfills, Native-Fill-Konflikte, Accountinggate, Upgrade-Snapshot |
| `test_v980_order_pipeline.py` | 20/20 | SUI-IOC-Partialpfad, HTTP-Timeout, kein zweiter POST, eToro-Mehrfachclose, Fremd-/Basegebühren, unklare Ergebniskosten |
| `test_v981_sqlite_startup.py` | 14/14 | Gleichzeitige SQLite-Schemastarts und bestehende Migrationen |
| `test_v982_acceptance.py` | 58/58 | Vorhandene Abnahmefälle mit Order-/Broker-/Zustandsbelegen |
| `test_v951_fill_commit_pipeline.py` | 10/10 | Ledger→Journal→Reconciliation→Tracker, nicht zuordenbare und technische Fehler |
| `test_v951_etoro_closed_migration.py` | 10/10 | Historische eToro-Abschluss-/Migrationsfälle |
| `test_v988_accounting.py` | 55/55 | Accounting-/Gebühren-/Reservierungsschutz |
| `test_v981_native_exit.py` | 30/30 | Native eToro-/OKX-Ausstiegsbelege und zugehörige Zuordnung |
| `test_v971_installation_regressions.py` | 24/24 | Kumulative Werte/Gebühren, unbekannte Kosten, Partialrest und widersprüchliche Alternativbelege |
| `test_v973_critical_regressions.py` | 6/6 | Geschlossene Entrylineage, verspätete Fills nach Partialverkauf und Trackerrecovery |

Der neue Crashfall startet tatsächlich einen isolierten Python-Kindprozess. Dieser wird mit `os._exit(73)` nach der Lifecycleaktualisierung, aber vor dem gemeinsamen Commit beendet. Der anschließend erneut geöffnete Zustand enthält einen offenen ursprünglichen Ledgertrade, keine abgeschlossene Exitbuchung und `accounted=0`. Derselbe Brokerbeleg wird danach genau einmal gebucht; das anschließende Replay erzeugt keinen weiteren wirtschaftlichen Effekt und keinen zweiten Exitdatensatz.

Die Prozess-/Threadtests prüfen Checkpointverlust. Sie sind keine Freigabe, zwei beliebige unabhängige wirtschaftliche Consumer parallel zwischen `prepare` und `commit` arbeiten zu lassen. Für solche Consumer bleibt die eindeutige Ledgerquittung erforderlich.

## Risiken, Restpunkte und bewusste Entscheidungen

| Punkt | Bewertung | Umgang |
|---|---|---|
| Allgemeine N05-Outbox über alle Projektionen | Bewusst nicht umgesetzt | In dieser Version wurde die konkret vermeidbare gemeinsame DB-Commitlücke geschlossen. Bestehende Risiko-/Journal-Recovery bleibt notwendig und muss in der Gesamtabnahme geprüft werden. |
| Native IDs ohne willkürlichen 5000er-Abwurf | Mittleres langfristiges Ressourcenrisiko | Datei kann mit der Handelsdauer wachsen. Keine unbewiesene Retention; unveränderte History-Polls parsen sie nicht erneut. Eine spätere, aus Ledger-/Archivbelegen begründete Kompaktierung bleibt separat. |
| Tracker als Checkpoint, keine Verarbeitungslease | Architekturgrenze | Kein neuer paralleler Order-/Fill-Writer wurde eingeführt. Ledger-Unique-Constraints schützen die wirtschaftliche Buchung weiterhin. |
| Reale Hardware-Dauerhaftigkeit | Nicht vollständig validiert | Prozessabbruch und injizierte I/O-Fehler sind getestet. Kein physischer Stromausfall, keine SD-Karten- oder Pi-5-Latenzmessung. |
| Aktuelle Brokerkonten | Nicht live validiert | Keine Live-/Demokontoanfrage. SUI-/eToro-Pipeline-Regressionsfälle sind vorhandene Offlinefixtures; damit wird kein nachträglicher Kontoabschluss behauptet. |
| Schema-Backup | Für diese Teiländerung nicht erforderlich | Keine Schemaänderung. Versionsübergreifende Gesamtmigration/Backup bleibt Aufgabe des Releasepakets. |
| Gesamtfreigabe | Durch diesen Teilbericht nicht ausgesprochen | 282 erfolgreiche Teiltests ersetzen weder Gesamttest noch Pi-/Brokerabnahme. |

## Übernommene Konzepte

- Nautilus: Ausführungsbelege, Duplikatprüfung und erlaubte Zustandsfortschreibung sind getrennte fachliche Fakten. Übertragen als kleine Erweiterung des existierenden NEXUS-Reducers. Quelle: [Orderzustände und OrderCore](https://github.com/nautechsystems/nautilus_trader/blob/e9848a98ca14b32f52ef42e2c2e608f2e9182101/crates/model/src/orders/mod.rs).
- LEAN: Geordnete wirtschaftliche Verarbeitung vor nachgelagerten Verbrauchern. Übertragen als gemeinsamer lokaler Ledger-/Lifecyclecommit; kein Portfolioimport fremder/manueller Positionen. Quelle: [BrokerageTransactionHandler](https://github.com/QuantConnect/Lean/blob/6eb389012d73c364547d61546ff822fc8432dee2/Engine/TransactionHandlers/BrokerageTransactionHandler.cs).
- Jesse: Transaktionale Historienaktualisierung als Anlass, vermeidbare lokale Commitlücken zu reduzieren. Keine Übernahme des Live-Moduls. Quelle: [Untersuchter Stand mit transaktionaler History-Reconciliation](https://github.com/jesse-ai/jesse/commit/60f882e88c2d28e0f8cbc7f916758434e6ab7ce6).
- NEXUS-eigenes `state_lock.py` und `order_ownership.py`: kompletter Reload/Merge/Commit unter derselben Prozess-/Threadsperre statt ausschließlich atomarer Dateischreibung.

Es wurden Konzepte passend zur vorhandenen Architektur implementiert; keine fremden Klassen, Frameworks oder visuellen Vorlagen kopiert.

## Ergänzung aus unabhängiger Gegenprüfung

Eine zweite Codeprüfung fand eine zuvor bereits vorhandene semantische JSON-Lücke im Tracker: `seen_ids="native-fill"` wurde als Zeichenmenge eingelesen; `initialized="false"` wurde als True interpretiert. Diese strukturell lesbare, fachlich beschädigte Datei konnte deshalb echte bisherige Fill-IDs verlieren. Die gehärtete Ladefunktion akzeptiert jetzt ausdrücklich Schema 1/2, eine Liste nichtleerer String-IDs, einen booleschen Initialisierungsstatus sowie endliche numerische kumulierte Werte. Fehlende Legacy-Metadaten bleiben kompatibel. Ungültige Originaldateien bleiben unverändert und verlangen Ledger-Recovery.

Neun zusätzliche Verhaltensfälle prüfen beschädigte ID-/Initialisierungs-/Schema-/Wertstrukturen und einen gültigen alten Tracker ohne neue Metadaten. `test_v100_execution_recovery.py` umfasst damit **37 Fälle**. Nachprüfung: **101/101 bestanden**, kein Netzwerkereignis, `implementation/test_runs/execution-review-loader_0757157f` (4,19 Sekunden). Die oben dokumentierten 282 erfolgreichen umfassenderen Regressionen beziehen sich auf den vorherigen Stand mit 28 neuen Fällen; die abschließende Gesamtsuite muss den ergänzten Stand enthalten.
