# NEXUS 10 – WebUI-Umsetzungsnachweis

## Grundlage und Integrationsentscheidung

Die vollständige Inventur vor Änderungen steht in `webui_baseline.md`. Der ergänzende Quellenvergleich steht in `../ui_research/WEBUI_RESEARCH.md`. Umgesetzt wurden nach dieser Inventur und nach der Quellenprüfung besonders UI-P0/P1-relevante Konzepte; keine fremden Komponenten, Templates, Logos oder Stylesheets wurden übernommen.

Die bestehenden Seiten Übersicht, Handel, Analyse, Universum, Underdogs, PULSAR, Einstellungen und Logbuch bleiben erhalten. `/positions` bleibt die Weiterleitung auf den vorhandenen Handelstab. Es gibt keine neue Hauptseite. Logo und `app.css` sind bytegleich mit der Ausgangsversion. `nexus.css` wurde ausschließlich um ergänzende Regeln mit bestehenden Farbvariablen erweitert. Bestehende Karten-, Panel-, Navigations-, Dialog- und responsive Klassen bleiben maßgebend.

## Änderungen und Testnachweise

| ID | Problem/Ursache | Umsetzung und Dateien | Risiko / Nachweis |
|---|---|---|---|
| UI-001 | „Verbunden“ vermischte Handelskern, REST, WebSocket und Kaufbereitschaft; Verkaufsstatus fehlte | `dashboard.js`, `common.js`: Kauf und Verkauf getrennt, jeweils konservative Backendprojektion aus `operations.brokers`. REST, WebSocket, Abgleich und Positionsschutz als einzelne aufklappbare Zustände samt Zeitbeleg, Code und Wirkung. Kein pauschales „Verkauf aktiv“ aus erfolgreichem REST | Neue Backendfelder fehlen bei Altständen → sichtbar unbekannt. Node-Test mit REST online/WS offline/Kauf blockiert, keine Sammelbehauptung |
| UI-002 | Nur aktive Orderzusammenfassung; `evidence_complete` vorhanden, aber unsichtbar | Neu `execution_details.js`, Integration `trades.js`/`trades.html`: Brokerabschluss, native Belegvollständigkeit und Mengenbuchung getrennt. Lesen von Order/Fills/Events per genauer Broker/Konto/Umgebung/Client-ID. Gespeicherte History 20 pro Seite, Detailabruf nur auf Klick, keine neuen Schreibaktionen | Verwechslung identischer Client-ID in anderem Konto verhindern: Scopeprüfung vor Abruf und nochmals gegen Antwort. Fehlende Gebühren bleiben unbekannt. XSS-Escaping getestet. Backendfehler niemals gesunde Leeranzeige |
| UI-003 | Leere Klärungsliste konnte als positiver Vollabgleich gelesen werden | Bestehender Klärungstab erhalten; explizite Begrenzung: keine offene lokale Order ≠ vollständiger Brokerabgleich. Backend-`complete:false`/`error` führt sichtbare Warnung. Ergebnisfälle bleiben von verkaufbaren Positionen getrennt | Keine neue generische MATCHED-Klassifikation aus einem bloßen Listenvergleich. Vorhandene strikte Eigentums-/Gebührensemantik unverändert |
| UI-004 | GPT-Fehler, nicht gestartete Nachprüfung und Cache waren mit „ausstehend“ unscharf | Neu `ai_diagnostics.js`, `pulsar.js/html`: echte Executionphase, Request-dispatched-Flag, Modell, Start/Ende, Dauer, lokale/Provider-ID, Errorcode. Kompakter Vorprüfungsstatus auf Karte; vollständige Details aufklappbar. Globale lesende GPT-Diagnose aus vorhandenem Audit, maximal 30 dargestellte Datensätze; gemeinsamer Batch anhand identischer Request-ID erklärt | Alte Daten ohne Telemetrie bleiben „Ausführungsbeleg fehlt“. Kein HTTP-/Provider-Erreichbarkeitstest ausgelöst; keine Prompts/Modellgedanken angezeigt. Tests: NOT_STARTED/Timeout/Cache/Legacy/Historylimit |
| UI-005 | Community-Belege mussten aus Fließtext erkannt werden; fehlende Budgetwerte wurden zu 0 | `pulsar.js`: vorhandener `community_status` explizit, Provider mit tatsächlich vorliegendem Beleg; neue Backend-Messkriterien werden mit tatsächlich belegtem Wert, Schwelle und Einzelstatus gezeigt. Keine erfundenen Autoren-/Communities-/Duplikatzahlen. Fehlende Kosten, Beobachtungstage, Symbolzählung oder Umfeld bleiben unbekannt; doppelte identische Lückenmeldungen zusammengefasst | „Beleg vorhanden“ ist ausdrücklich keine Vollabdeckung/Erreichbarkeitszusage. Bestehende Checks, Claims und Originalquellen erhalten. Community- und Nullfiktionstests |
| UI-006 | Mehrere `setInterval`-Schleifen konnten sich überlappen und liefen im Hintergrundtab weiter | `common.js:nexusPoll` wartet Abschluss je Abonnement ab, dann Timer; verborgene Tabs werden nicht abgefragt. Dashboard 15 s, Handels-/Positionsdaten 20 s, Kontext 30 s, Performance/PULSAR 60 s. GPT-Diagnose 60 s nur bei geöffnetem Bereich. Analysejob im Hintergrund keine HTTP-Abfrage, sondern spätere Prüfung. Sichtbare Fehl-/Altdatenwarnung | Keine sekündlichen Brokerprobes, keine Nachholwarteschlange. Manuelle Aktualisierungen bleiben möglich. Produktionsscheduler mit kontrolliert verzögerter Promise, hidden/visible-Wechsel und stop() getestet |
| UI-007 | Entscheidungssuche bot keine explizite allgemeine Text-/ID-, Strategie- oder Grundsuche | `logbook.js/html`: aufklappbare Zusatzfilter `search`, `strategy`, `reason`; bestehende Broker/Symbol/Datum/Statusfilter bleiben. Root-Backend erweitert parametergebundene Lesefilter | Keine neue Ereignisidentität oder Handelserlaubnis im Frontend. Servervalidierung und API-Filtertests gesondert im Backendnachweis |
| UI-008 | Favorit-Badge vorhanden, aber kein Filter; `letzte_pruefung` im Backend ungenutzt | `universe.js/html`: Favoritenfilter und Zeitpunkt der letzten belegten Prüfung; explizite Erklärung Favorit = Beobachtungspriorität. Universum-/Underdog-Routen und Katalogsicht bleiben | Kein neues Watchlist-CRUD ohne eigenen Backendentwurf, keine Scorehistorie oder PULSAR-Metrik erfunden. Filtertest prüft Auswahl, Unbekanntwerte und Prüfzeit |
| UI-009 | Unbekannter Trade-`paper`-Wert wurde an zwei Stellen als LIVE gerendert | `logbook.js`, `pulsar.js`: gemeinsame belegorientierte Kontextauflösung, fehlende Metadaten → Umgebung unbekannt. Existierende P&L-Konto-/Währungsgruppen und Gebührenqualität unverändert; Performancepolling reduziert | Keine neue Drawdown-/Win-Rate-Kachel ohne passende Daten. Tests für missing/false/true und vorhandene Kontextregressionen |
| UI-010 | Keine vollständig konsistente Risiko-/Reserveprojektion für neue Übersicht | Bewusst keine neue Risikokarte. Bestehende Profilwerte, Kaufblockaden und Schutzdetails erhalten; korrigierter Core liefert weiterhin seine tatsächlichen Gründe | Vorläufige Reserve/Equity-Mischung wäre irreführend. Neue Karte bleibt bis zu einem eindeutigen, validierten Backendvertrag offen |
| UI-011 | Systemsuche erklärte IDs nicht; CRITICAL nicht auswählbar | `logbook.html`: konkrete Text/Symbol/Trade-ID/Order-ID-Beschriftung und CRITICAL-Level. Bereits bestehende serverseitige 300-Zeilenbegrenzung erhalten | Keine aus Tickern geratenen Brokerfarben; vorhandene Logger-/Brokerzuordnung bleibt. Backendfilter separat geprüft |
| UI-012 | CPU/freie Ressourcen im Backend vorhanden, ungenutzt; fehlende Laufzeit erschien als 0 min | `dashboard.js`: CPU, freier RAM und freier Speicher in existierendem Systemdetail; fehlende Laufzeit „unbekannt“ | Keine neue Monitoringbibliothek, keine Systemmetriken auf Hauptkarten. Uptime/Nullwerte geprüft; reale Pi-Sensoren hier nicht gemessen |
| UI-013 | Kritische UI-Aktion könnte leichtfertig als bestätigter Trade interpretiert werden | Bestehende Bestätigung/CSRF/Identitätsübergabe unverändert. Neue UI nur lesend, kein neuer Start/Stop/Emergency-/Sellpfad; Details sagen ausdrücklich Broker-/Buchungszustand getrennt | Keine neue globale Freigabe; keine Fremd-UI-„HTTP 200 = position closed“-Semantik übernommen. Bestehende WebUI-Sicherheits-/API-Tests bestanden |
| UI-014 | Neue Daten könnten Mobilansicht und Navigation überladen | Vorhandene Navigation und Kartenstruktur erhalten, vorhandene Tab-/Dialogmechanik genutzt. Neue Grids einspaltig unter 560 px, lange IDs umbrechen, Filltabellen eigene Scrollregion. Hauptkarte → Details → Belegdialog | Browserprüfung Desktop/Tablet/Mobil erfolgt separat. Node-Tests sind kein Ersatz für visuelle Layout-/Touchprüfung |

## Weitere begrenzte Verbesserungen

PULSAR-Archiv wird in 20er-Seiten im DOM angezeigt; der JSON-Export bleibt vollständig. Geöffnete benannte Broker-/PULSAR-Details bleiben bei manuellen Aktualisierungen erhalten. Die Handelskarte öffnet den passenden Brokerfilter. Falsche Annahmen über vollständig identische Analyse-/Live-Regeln im pauschalen Analysetext wurden korrigiert; die Einzelsimulation nennt ihr Modell. Analyseprozessstatus `TIMED_OUT`, `OUTPUT_LIMIT` und `UNKNOWN` werden dargestellt. Bei `blocked:true` steht „PRÜFUNG ERFORDERLICH“, und neue Analysebuttons sind gesperrt.

## Regressionen und Grenzen

Beim Abgleich der tatsächlichen API wurde entdeckt, dass `truncated` ein Objekt mit zwei Flags ist. Eine allgemeine Truthy-Prüfung hätte auch bei `{fills:false,events:false}` weitere abgeschnittene Belege behauptet. Das wurde vor Übergabe korrigiert und mit eigenem Test für beide Fälle abgesichert. Ein fehlender Fake-DOM-Knoten führte einmal zu einem Fehltest des neuen Favoritenfilters; das Testsetup wurde korrigiert, keine Produktlogik gelockert.

Im Volltest wurden außerdem acht alte Charttests mit einem unvollständigen isolierten JavaScript-Kontext gefunden: sie luden `performance.js` allein und stellten lediglich `setInterval` bereit. Nach Einführung des gemeinsamen Schedulers wird jetzt im Test wie im echten Template zuerst das produktive `common.js` geladen; der Datenabruf bleibt die vorhandene deterministische API-Fixture, spätere Timer sind stillgelegt. Sämtliche bisherigen SVG-, P&L-, Nullwert- und UTF-8-Erwartungen blieben unverändert. Ein erster Zwischenlauf hatte noch die Fixture-API vor dem Laden von common.js gesetzt; das neue Laden überschrieb sie. Die Fixture wird nun danach gesetzt. Der abschließende komplette 55er-Testlauf dieser Dateien bestand.

Ein unabhängiger Code-Review fand zwei zusätzliche Unknown→0-Anzeigen im vorhandenen Dashboard: eine fehlende Positionsliste wurde als null Positionen gezählt, und unbekannter verbrauchter Speicher bei bekannter Gesamtkapazität ergab 0 %. Beide Anzeigen wurden korrigiert und durch konkrete Renderer-/Zahlentests abgesichert. Im geprüften neuen Detailcode wurde keine konkrete XSS-Lücke oder Vermischung der exakten Kontokontexte gefunden. Single-flight gilt pro automatischem Abonnement, nicht als globale Sperre aller manuellen Aktualisierungsaufrufe.

Die neuen Anzeigen lesen persistente Projektionen. Sie bestätigen keine Livebroker-Erreichbarkeit außerhalb der tatsächlich gespeicherten Messung, reparieren keine historische Gebühr und simulieren keinen vollständigen Depotabgleich. Bei fehlendem/ungültigem Datenfeld wird keine Finanzkennzahl nachgeschätzt. HTTP-Antworten und JavaScript-Tests allein sind keine Freigabe für Echtgeldbetrieb. Die Gesamtreleaseentscheidung wird im Hauptbericht getroffen.

## Durchgeführte Validierung

* `node --check` für alle zwölf Produktions-JavaScriptdateien: erfolgreich.
* `node --test tests/frontend_v100.test.js`: 19/19 reale Renderer-/Scheduler-Verhaltenstests (durch Python-Wrapper ebenfalls ausführbar).
* Isolierter Regressionstestlauf `frontend-webui-final_d915cca5`: **53/53 pytest-Tests bestanden**, einschließlich des Wrappers für diese 19 Node-Tests. Keine Netzwerkereignisse. Keine doppelte Zählung als 72 pytest-Tests.
* Enthaltene bestehende Gruppen: `test_v980_webui.py`, `test_v972_webui_contexts.py`, `test_v814_webui_und_diagnose.py`, `test_v941_webui_analysis.py`; dazu `test_v100_frontend.py`.
* Abschließender Chart-/Encoding-Regressionslauf `frontend-chart-final_0cb9a929`: **55/55 pytest-Tests bestanden**, inklusive der 19 Node-Verhaltenstests im gemeinsamen Wrapper; keine Netzwerkereignisse. Dateien `test_v988_import_migration_ui.py`, `test_v988_encoding_fix1.py` und `test_v100_frontend.py`. Beide Läufe überschneiden sich im Wrapper und sind deshalb keine disjunkte Gesamtsumme.
* Bestehende Seitenrouten, eindeutige IDs, lokale Assets, Einstellungen-Steuerelemente, vier zugängliche Handelstabs und Konto-/Umgebungsgrenzen durch diese Regressionstests geprüft.
* Logo SHA-256 gegen extrahierte FIX1-Basis verglichen: identisch. `app.css` unverändert. Keine fremden Styles, Templates oder Logos.
* Browser-/Screenshot-QA: der zentrale Versuch wurde mit `ERR_BLOCKED_BY_CLIENT` abgewiesen; daher keine vollständige visuelle Abnahme behauptet. DOM-/Viewport-/CSS-Prüfungen im Hauptnachweis können diese Grenze eingrenzen, ersetzen reale iOS-Touchbedienung und Pi-Leistungsmessung aber nicht.

## Status für den finalen Maßnahmenabgleich

| ID | Status dieses Teilpakets |
|---|---|
| UI-001 | 🟡 umgesetzt; Renderer und Backendvertrag geprüft, reale Brokerzustände separat |
| UI-002 | 🟡 umgesetzt; Scope-/Beleg-/Fehlerverhalten getestet, Browserintegration separat |
| UI-003 | 🟠 Anzeige verbessert; keine neue pauschale Snapshot-MATCHED-Engine |
| UI-004 | 🟡 umgesetzt; Executionphasen getestet, echte Providerrequests hier nicht ausgeführt |
| UI-005 | 🟡 umgesetzt; Datenlage und unbekannte Belege korrekt gerendert, Livequellen hier nicht geprüft |
| UI-006 | ✅ umgesetzt und Schedulerverhalten getestet |
| UI-007 | 🟡 UI umgesetzt; Backendfiltervalidierung im Hauptpaket |
| UI-008 | 🟠 Favoritenfilter/Prüfzeit umgesetzt; CRUD und neue Verlaufssysteme bewusst nicht ergänzt |
| UI-009 | ✅ falsche LIVE-Anzeige korrigiert und getestet; bestehende P&L-Trennung erhalten |
| UI-010 | ⛔ neue Risikokarte bewusst nicht umgesetzt; vollständiger Backendvertrag fehlt |
| UI-011 | 🟡 Filterdarstellung verbessert, Backendfilterprüfung separat |
| UI-012 | 🟡 vorhandene Telemetrie dargestellt, tatsächliche Pi-Sensoren nicht hier validiert |
| UI-013 | ✅ keine neuen Steuerrechte; bestehende Schutzkontrollen in Regression geprüft |
| UI-014 | 🟡 responsive Regeln/Navigation umgesetzt, visuelle Prüfung separat |
