# NEXUS 10 – Strategie, Backtest und PULSAR/GPT

## Grundlage und Abgrenzung

Quellstand: unveränderte NEXUS 9.9.0 FIX1 als Ausgangspunkt. Arbeitsbaum ist ausschließlich `implementation/TradingBot_v10.0.0_NEXUS`. Verbindliche Analyse: Maßnahmen N07, N09 und die Abgrenzung N10. Keine Broker- oder GPT-Verbindung wird für die Prüfung hergestellt. Alle HTTP-Antworten in den Tests sind ausdrücklich lokale Fakes. Reale erfolgreiche GPT-Anfragen auf Georgs Pi lassen sich damit nicht behaupten.

## Baseline

`strategy_baseline_79f14d49` enthält vier tatsächlich fehlschlagende Verhaltensprüfungen des unveränderten Codes:

1. Schlussverkauf 10000 → 9500; Drawdown blieb fälschlich 0 statt 5 Prozent.
2. Offene Bewertung 10000 → 9200 → 10000; Equity-Drawdown blieb 0 statt 8 Prozent.
3. Fehlende echte 16:45-Kerze; simulierte Order wurde auf dem künstlichen Gap-Bar ausgeführt.
4. Wechsel von Modell A auf B; die zweite Antwort stammte unverändert aus Modell A.

Diese Proben sind synthetische reproduzierbare Gegenfälle, keine nachgespielten Liveverluste. Frühere unveränderte Diagnosebelege dienen als Kontext. Es wurden keine fehlenden Pi-, Konto- oder Usagebelege erfunden.

## NEXUS-IMP-N07 – Backtest-Ausführung und Kennzahlen

**Ursache:** Der Drawdown wurde nur nach realisierten Zwischenverkäufen aktualisiert. Endverkauf und offene Wertänderungen fehlten. Normalisierte Gap-Bars wurden ohne Herkunftsunterscheidung als ausführbarer Preis verwendet.

**Änderung:** `freqtrade_sample_backtest.py` liefert getrennt `realized_max_drawdown_pct` und `equity_max_drawdown_pct`. `max_drawdown_pct` ist ab Ergebnisschema 2 ausdrücklich der Equity-Drawdown. Die Equity wird zu beobachteten Schlusskursen als geschätzter Netto-Liquidationswert einschließlich Einstiegsgebühr, angenommener Ausstiegsgebühr und Slippage bewertet. Der Schlussverkauf wird beiden Metriken zugerechnet. Die vollständige Kurve ist auf ausdrücklichen Aufruf `include_equity_curve=True` bzw. `--equity-curve` verfügbar; Standardantworten werden nicht um große Zeitreihen vergrößert.

Standardmodell ist `observed_bars`, Revision `nexus-backtest-observed-bars-v2`: keine Ausführungen, Signalentscheidungen oder Stop/ROI-Fills auf synthetischen Kerzen. Bereits offene Aufträge werden bis zum nächsten tatsächlich beobachteten Open aufgeschoben. Ein Kurssprung unter den Stop wird an diesem Open, nicht am unerreichbaren alten Stopkurs ausgeführt. Auch bereits normalisiert übergebene Gap-Marker bleiben für die Simulation erhalten.

Das frühere Gap-Fillmodell kann ausdrücklich mit `execution_mode="legacy_gap_fill"` bzw. `--execution-mode legacy_gap_fill` verwendet werden. Es wird als altes Modell bezeichnet. Korrekte Metriken gelten auch dort. Rohdatendigest, Metrikrevision, Ausführungsrevision und Anzahl synthetischer Kerzen stehen im Ergebnis.

**Unverändert:** Liveindikatoren, 500-Bar-Fenster, Startup-Anforderung, UTC-Candle-Vertrag, Signalcursor, Order-Identität, Stop/ROI-Livelogik und Broker-Schutzausführung. Ein Backtestfix ist keine Änderung realer Orders.

**Risiken/Grenzen:** Neue Drawdowns können historische Rankings verändern. Die neue Gap-Policy ist eine transparent benannte Modellannahme; sie beweist weder Liquidität noch reale Fillpreise. Intrabar-Equity bleibt unbekannt; kein Orderbuch-, Latenz-, Queue- oder Partial-Fillmodell. Alte Ergebnisdateien werden nicht umgerechnet oder überschrieben. Nur neue Ergebnisse tragen Schema 2. Forschungsansichten müssen die ausgewiesene Methodik mitanzeigen.

## NEXUS-IMP-N09a – Gemeinsame Analyseidentität

**Ursache:** Der allgemeine Routercache wurde vor Routing/Modellauswahl gelesen; sein Schlüssel enthielt kein Modell. PULSAR hatte bereits stärkere Textschlüssel, aber Websuche und Ereignisklassifikation waren unvollständig an Modell, Schema und Prompt gebunden.

**Änderung des Routers:** `analysis_cache_key` bildet einen namespacegebundenen SHA256-Digest über Aufgabe, effektives Modell und Tier, tatsächlichen Prompt einschließlich Standardprompt, Schema, vollständigen Input, zusätzliche Quelldatenidentität, Modell-/Promptrevision und Toolpolicy. Routing, Aktivierung und vorhandene Rechte-/Budgetprüfungen erfolgen vor Übernahme eines generischen Cachetreffers. Ein Cachetreffer nennt sein Modell und Tier; er wird nicht mehr anonym mit leerem Modell ausgegeben.

**Migration:** Neue Schlüssel liegen im Namespace `nexus-analysis-cache-v2`. Bestehende Dateien und Vollbelege bleiben erhalten; ältere Antwortschlüssel werden nicht als kompatibel angenommen und laufen regulär aus. Beim ersten zulässigen Abruf kann erneute Analyse nötig werden. Tages-/Wochen-/Monatsbudgets bleiben unverändert bindend. Kein Cachezustand aktiviert ausgeschaltete KI oder erweitert Schreib-/Handelsrechte.

## NEXUS-IMP-N09b – Belegte Ausführungsdiagnose

`ai_router.AIAntwort.als_dict()` ergänzt `execution` mit Phase, lokaler Korrelations-ID, ausschließlich aus einer Antwort übernommenen Provider-ID, lokalen Request-/Start-/Endzeiten, Dauer, Cacheverwendung, Antwortempfang, Fehlercode und `request_dispatched`.

Werte bedeuten:

| Feld/Phase | Bedeutung |
|---|---|
| `NOT_STARTED` | Lokal nachweislich nicht gesendet, etwa deaktiviert oder Transport vollständig belegt nicht gestartet |
| `CACHE_HIT` | Diese Nutzung löste keinen neuen HTTP-Aufruf aus |
| `REQUEST_STARTED` | HTTP-Worker hat den lokalen Aufruf begonnen; keine Bestätigung der Serverannahme |
| `SUCCEEDED` | Eine Antwort wurde empfangen, vom Router gelesen und am Schema geprüft |
| `TIMED_OUT` | Zeitgrenze überschritten; fehlende rechtzeitige Antwort beweist nicht Nichtausführung |
| `FAILED` | Fehler nach Ausführungsversuch, etwa HTTP-Fehler oder ungültige Ausgabe |
| `DISCARDED` | Antwort wegen geändertem Steuerungszustand nicht übernommen |
| `request_dispatched=true` | Lokaler HTTP-Aufruf begonnen; kein aktueller Erreichbarkeitstest |
| `request_dispatched=false` | Nachweisliche Nichtsendung oder reine Cachenutzung |
| `request_dispatched=null` | Transportabschluss unbekannt; keine positive oder negative Behauptung |

START und Abschluss werden im vorhandenen Audit mit derselben lokalen ID geschrieben. Verspäteter Verbrauch darf weiter ausschließlich Budgets schließen; er ersetzt weder Text, Cache noch Ergebnisphase. Der begrenzte Statusleser verarbeitet höchstens 64 KiB Auditende und höchstens 50 Einträge. Alte Audits ohne Ausführungsfelder begründen keine Behauptung „GPT nicht ausgeführt“. Das Audit bleibt Diagnose und ist keine Orderautorität.

Zusätzliche enge Härtung: Ein lokal fehlgeschlagener HTTP-Threadstart gibt seinen Semaphore-Slot frei und gilt als nachgewiesene Nichtsendung. Ein ungültiger lokaler Timeoutparameter lässt keine kostenpflichtige Reservierung stehen, da der Transport noch nicht betreten wurde.

## N10 – Bewusst verschoben

Keine Änderung des Live-ML-Filters, kein neues ML-Modell, kein ungeprüfter Split-/Purgehelper. `USE_ML_FILTER=False` bleibt erhalten. Für N10 fehlen robuste gemeinsame historische Datensätze mit Veröffentlichungs-/Wissenszeit und ausreichend unabhängiger Validierung. Erforderlich sind gemeinsame zeitliche Schnittpunkte für alle Symbole, Horizon-Purge, nur auf Training angepasste Normalisierung, versionierte Daten-/Feature-/Label-Artefakte und kostenbereinigte wiederholte Out-of-sample-/Shadowauswertung. Eine synthetische neue Hilfsfunktion allein würde diesen Erkenntnisgewinn nicht liefern und unnötige Komplexität hinzufügen.

## PULSAR-Integration nach dem gemeinsamen P0/P1-Gate

Die Integration erfolgte nach dem bestandenen gemeinsamen Core-Gate `core-p0-p1-gate_00c4bf2c` (72/72). Betroffene zusätzliche Dateien: `pulsar/analysis.py`, `pulsar/sources.py`, `pulsar/worker.py`, `pulsar/evidence.py` und das kleine reine Projektionsmodul `pulsar/diagnostics.py`.

Textanalysen und gemeinsame Vorprüfung verwenden den gemeinsamen Analyseschlüssel. Die Vorprüfung bindet nun auch ihr tatsächlich dynamisch erzeugtes Schema und ihren vollständigen speziellen Prompt ein. Gegencheck und normale Analyse haben getrennte Aufgaben- und Promptidentität. Revision ist `pulsar-cited-reviews-v6`.

Websuche bindet Modell, Suchprompt, Schema, zugelassene Domains, Werkzeuggrenze und ein festes 6-Stunden-Wissensfenster. Die genaue `as_of`-Zeit des tatsächlich ausgeführten Aufrufs bleibt erhalten. Das Fenster verhindert einen neuen bezahlten Aufruf wegen jeder Sekundenänderung, kann aber eine frühere Invalidierung beim Fensterwechsel verursachen. Vorhandene Quoten bleiben bindend. Die Ereigniseinordnung bindet zusätzlich vollständige Quelldatendigests und Veröffentlichungszeiten ein, auch wenn nur ein Auszug an GPT geht. Das ist ein reproduzierbarer Cachevertrag, keine Behauptung einer vollständigen historischen Point-in-time-Datenbank.

Eine fehlgeschlagene gemeinsame Vorprüfung erhält auf allen betroffenen Karten denselben echten Batchbeleg. Vertiefung und Gegencheck tragen explizit „nicht gestartet, Vorprüfung fehlgeschlagen“. Erneute Abrufpause ist eine lokale Nichtausführung und kein weiterer Timeout. Cachenutzung erhält ihren eigenen `CACHE_HIT`-Status; der frühere Aufrufbeleg liegt separat in `source_execution`. Unerwartete Ausnahmen ohne zurückgegebenen Beleg bleiben unbekannt. Sie werden niemals nachträglich in eine beweislose Nichtsendung umgedeutet.

PULSAR-Quellenanzeigen erhalten `community_checks` mit den bestehenden Anforderungen, tatsächlich belegten Werten und `UNKNOWN`, `MET` oder `NOT_MET`. Es gibt keine neuen Quellen, Autorenhochrechnungen, erfundenen Spam-/Duplikatzahlen oder geänderten Freigabegewichte. Aggregierte Erwähnungen können weiterhin keine Einzelautoren oder unabhängigen Menschen bestätigen. Die Community-Breite bleibt optional.

Vorhandene Revisions-/AUS-Prüfungen, Reservierungen, begrenzte Workerzahl, selektive Vorfilterung und reine Late-Usage-Abrechnung bleiben erhalten. Wenn ein vorhandener Unternehmensoriginalbeleg die zusätzliche GPT-Websuche erspart, wird dies als nachgewiesene Nichtausführung mit `PULSAR_SOURCE_HINT_SUFFICIENT` ausgewiesen.

## Testbelege

`strategy_router_7781c673`: 88/88 bestanden; `strategy_extended_c6c75158`: 99/99; `pulsar_integration_3d5468d0`: 150/150; `strategy_pulsar_complete_def2febe`: 183/183; finaler Lauf nach den letzten Metadatenhärtungen `strategy_final_d2caae8c`: **183/183 bestanden** (davon **28 neue Tests**). Jeder Lauf erfolgte mit eigenem Quellsnapshot und eigenem Zustandsverzeichnis; es gab keine ausgelösten Netzwerkversuche. Diese Zahlen sind überlappende Läufe, keine Summe unabhängiger Tests. Eine Starlette/AnyIO-DeprecationWarning betrifft eine künftige Bibliotheksschnittstelle, keinen fehlgeschlagenen Test.

Die neue Datei `tests/test_v100_strategy_pulsar.py` prüft beobachtetes Verhalten: Schluss-DD, offene Delle/Erholung, Gap-Entry/-Exit, Gap unter Stop, alte Gap-Policy, erhaltene Gap-Marker, Gebühren und Kurvenwerte, Modell-/Prompt-/Schema-/Datendigest-/Wissenszeitidentität, Erfolg/Cache/AUS, unsichere versus bewiesene Nichtsendung, Timeout mit später Usage ohne Textübernahme, 429 ohne automatischen Retry, ungültiger Antwortkörper, lokaler Threadstartfehler, gemeinsame Batchidentität, echte Abrufpause, modellgebundene Ereignis-/Webcaches und ausschließlich belegte Communityzahlen. Zusätzlich werden unzulässige Symbol-/Quellenreferenzen und Orderverben im Analysevertrag verworfen.

Die Quellenvertragstests sind deterministische lokale Gegenfälle. Sie messen keine reale LLM-Präzision, keine Profitabilität und keine vollständige semantische Widerstandsfähigkeit gegen Promptinjektion. Ein semantisch irreführendes, aber formal passendes Zitat benötigt weiterhin einen größeren unabhängig etikettierten Belegdatensatz und eine ausdrücklich budgetierte Modellevaluation. Dieser Teil von N09b bleibt offen.

Nicht durchgeführt: Live-/Demo-Orderverkehr, echte GPT-Abrechnung, Kontorechteprüfung, Pi-Lastmessung, physischer Stromausfall sowie echte verspätete Usage über einen Prozessneustart. Ein während eines Prozesses verspäteter Verbrauch wird geprüft; eine neue automatische Provider-Recovery nach Neustart wird nicht behauptet. Gesamtfreigabe und Pi-/Brokerprüfung obliegen dem gemeinsamen Release-Gate; diese Teilnotiz behauptet keine Livefreigabe.
