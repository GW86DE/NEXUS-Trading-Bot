# NEXUS 10 – WebUI-Deep-Dive und begründeter Maßnahmenkatalog

Stand: 13.09.2026. Technische Ausgangsbasis: NEXUS 9.9.0 FIX1, separat erhaltene Quelldateien. Der Umsetzungszweig 10.0.0 wurde zur Abstimmung gelesen. Dieser Bericht dokumentiert die Auswahl vor der abschließenden Implementierungsbewertung; ob eine Empfehlung tatsächlich umgesetzt und getestet wurde, entscheidet ausschließlich der IMPLEMENTATION_REPORT zusammen mit dem TEST_REPORT.

## 1. Ergebnis und Grenzen

NEXUS benötigt kein neues Dashboard und kein fremdes Frontend-Framework. Den höchsten Nutzen bieten präzisere Zustände, eine überprüfbare Auftragsdetailansicht, nachvollziehbare GPT-Aufrufphasen und sichtbare Datenaktualität. Die vorhandene Broker-, Konto-, Demo/Live- und Gebührenabgrenzung ist ein wesentlicher Vorteil und muss erhalten bleiben.

Vertieft wurden fünf öffentlich einsehbare UI-Codebasen: FreqUI, das bisherige Hummingbot Dashboard, dessen aktueller Nachfolger Condor, OctoBot einschließlich klassischer und neuer Node-Oberfläche sowie NOFX. Jesse wurde an den aktuellen API-Controllern, ausgelieferten Frontend-Artefakten und der Aussage des Maintainers geprüft. Lumibot und TradingAgents dienen als ergänzende Gegenproben für Reporting beziehungsweise KI-Prozessanzeigen. Nicht jedes Trading-Framework besitzt eine eigenständige WebUI.

75 konkrete Dateien wurden für die abgegrenzte Prüfung abgerufen: Frontend-Komponenten, Stores, Backend-Routen, ausgewählte Tests und Dokumentation. Der SOURCE_MANIFEST.json benennt Pfad, Commit und Quelllink. Wichtige Dateien wurden vollständig, große Stores und API-Dateien entlang der untersuchten Daten- und Fehlerpfade gelesen. Dies ist keine Behauptung, sämtliche Zeilen aller Fremdprojekte geprüft zu haben. Die Fremdframeworks wurden weder installiert noch gegen Broker ausgeführt; deren vorhandene Tests sind Quellbelege, keine von uns ausgeführten Tests. Ein NOFX-Screenshot wurde visuell geprüft; er zeigt Beispieldaten aus Dezember 2025 und ist kein Livezustand oder Performancebeleg. Die offizielle FreqUI-Dokumentation enthält Screenshots; der zusätzliche lokale Bildabruf war nicht erfolgreich. Authentifizierte Demos und Brokeraktionen waren nicht erforderlich.

## 2. NEXUS heute: vollständige Ausgangsinventur

Die ausführliche Codeinventur liegt in **docs/v10/webui_baseline.md**: alle acht HTML-Templates, zehn JavaScript-Dateien, beide Stylesheets sowie WebUI-Routen und Zustandsprojektionen. Maßgebliche Dateien sind webui/app.py, webui/state.py, webui/static/common.js, dashboard.js, trades.js, positions.js, pulsar.js, universe.js, performance.js, logbook.js, analysis.js und settings.js.

| Bereich | Tatsächlich vorhanden | Relevante Lücke |
|---|---|---|
| Hauptnavigation | Übersicht, Handel, Analyse, Einstellungen, Logbuch; common.js ergänzt Universum, Underdogs und PULSAR, einschließlich mobiler Navigation | Bei Änderungen dürfen weder Ergänzungen noch aktive Route und Mobilmenü verschwinden |
| Dashboard | Zwei getrennte Brokerkarten, Kaufsteuerung, letzte Entscheidungen, Gebühren-/Buchungsfälle, Performance, System- und Quellen-Details | „Verbunden“ verdichtet Worker und Broker; nach Abruffehler können andere alte Karten sichtbar bleiben |
| Brokeridentität | Konto, beobachtete Umgebung, konfigurierte Umgebung und Moduswechsel | REST, Broker-WS, Worker und Browser-API nicht in einer Ampel zusammenfassen |
| Handel und Positionen | Offen/Verkäufe/Klärung/Guthaben, Eigentums- und Verwaltungsstatus, Stop/TP/ROI, Kursquelle und Exitinformationen, 12er-Kartenseiten | Haltedauer nicht durchgängig sichtbar; vollständige Ausführungskette fehlt in einer Detailansicht |
| Orders | /api/executions liefert aktive Lifecycle-Zeilen | terminal, evidence_complete und accounted nicht gleich gut sichtbar; native Fills und Ereignisse fehlen im UI-Detail |
| Reconciliation | Eigener Klärungstab, ungeklärte Aufträge, Bestands- und Buchungsfälle | Leere Fehlerliste beweist keinen aktuellen vollständigen Brokerabgleich |
| Performance | Nach Broker/Konto/Umgebung/Währung getrennte Ergebnisgruppen; bestätigte Gebühren, vorläufige Ergebnisse und offene Fälle | Keine vollständige historische Mark-to-market-Equitybasis für neue Drawdown-/Sharpe-Kacheln |
| Risiko | Risikoprofile und Kaufbedingungen, OKX-Kapital-/Exposureinformationen im Backend | Konsistente sichtbare Tagesbasis, Reserven, Auswirkung und Haltgrund fehlen |
| Universum | Aktive und beobachtete Instrumente, Rang, Score, Aufnahme/Abgang/Sperrgrund, getrennte Broker | Vorhandene letzte Prüfung noch nicht sichtbar; Favoritenmerkmal ohne eigenen Filter |
| Underdogs/Favoriten | Underdogs als spezialisierte bestehende Seite, Watchlistmerkmale und Priorität | Keine einheitliche Scorezeitreihe; WebUI-Favoritenbearbeitung wäre eine neue Schreibfunktion |
| PULSAR | Quellenbelege, Claims, Checks, wirtschaftliches Originalereignis, Qualifizierung, Budgetreservierungen, Trades/Archiv | Fehlgeschlagenes und noch nicht gestartetes KI-Stadium erscheinen teilweise beide als „ausstehend“; fehlende Budgetwerte teilweise als 0 |
| GPT | Testfunktion und PULSAR-Analyseantworten | Antwortstatus allein beweist keinen neuen Providerrequest; Modell, Start, Cache und Timeout brauchen konsistente Telemetrie |
| Entscheidungen/Logs | Serverseitige Seiten, Broker/Asset/Status/Quelle/Symbol/Datum, Signalchecks, Metriken und Strategiekennung; begrenzte Systemlogs | ID-Suche nicht erklärt, CRITICAL fehlt als Auswahl; unvollständige Altmetadaten dürfen nicht automatisch LIVE werden |
| Analyse | Getrennte Researchjobs, Status und Output, kein eigener Orderzugriff | Forschungsmodell nicht pauschal mit jeder Core-Ausführungsregel gleichsetzen |
| Pi/Telegram/Health | Temperatur, RAM, Disk, Uptime, Queue und Telegramsteuerung; zusätzliche CPU-/Modell-/Throttlewerte im Backend | Fehlende Uptime darf nicht 0 Minuten heißen; eingerichtetes Telegram ist kein Zustellnachweis |
| Bedienung/Sicherheit | CSRF, Login, bestehende Bestätigungen, Kontoidentität, mobile Breakpoints, native SVG-Charts | Abruffehler, lange Belege und Touch-Details systematisch testen; Diagnose darf keine neue Handelsautorität erhalten |

Bereits vorhandene Funktionen werden nicht erneut als Neuerfindung verkauft: NEXUS hat schon Entscheidungsfilter, Chartmarker, Seiten für Universum/Underdogs, eine Klärungsansicht, Kapitalgruppen und Beleglinks. Insbesondere bleibt ein geschlossener Trade mit fehlenden Gebühren geschlossen. Eine Buchungsklärung darf keinen neuen Verkaufsbutton erzeugen.

## 3. Vergleichsstände und Wartung

| Projekt | Prüfstand | Lizenz/Umfang | Bedeutung für die Auswahl |
|---|---|---|---|
| FreqUI | d8591ace9eeb387c0976a8e66ad466ca2ab5fc7e, 10.09.2026 | GPL-3.0, Quellcode verfügbar | Geeignet für Details, mobile Navigation und selektive Aktualisierung |
| Hummingbot Dashboard | ee42595bec85518eda0b9fe1a56046d09a3e36c6, 27.10.2025 | Apache-2.0 | Offizielle Dokumentation bezeichnet es ausdrücklich als nicht mehr aktiv gepflegt |
| Hummingbot API | 7e86651b69b13b39e0cb3e1afbf080eedd461e8d | Separater Backend-Prüfstand | API-Verträge zusätzlich zum Dashboardclient kontrolliert |
| Condor | 1890c0871694c8b1c7c93ecdadb6350cd20eab6a, 07.09.2026 | MIT | Aktueller Hummingbot-Web-/Telegram-Nachfolger; größerer Funktionsumfang ist kein Grund zum Nachbau |
| OctoBot | dc0efc8ec36c138bd619272b2b59042778668408, 10.08.2026 | Root GPL-3.0; einzelne klassische Webdateien tragen LGPL-Hinweise | Klassisches Trading-UI und Node-UI getrennt beurteilt |
| NOFX | 638d4042118995fbf1a38d3822b1139aa3c6b467, 05.09.2026 | AGPL-3.0 | Gute Entscheidungskarten, aber Ausführungserfolg kritisch prüfen |
| Jesse | 60f882e88c2d28e0f8cbc7f916758434e6ab7ce6, 12.09.2026 | MIT-Core; aktueller GUI-Originalquellcode geschlossen | Aktuelle Backendverträge verwendbar, vollständiger Frontend-Deep-Dive nicht öffentlich möglich |
| Lumibot | f987da3e2c81a5ae2cb6edf35457d2aa54aacf8b | GPL-3.0-LICENSE; hier Tearsheet-Reporting | Kein gleichwertiges vollständiges Brokerkontroll-Dashboard im geprüften Core |
| TradingAgents | be952b8eccb49720509af544c6675233bc1f10d0 | Apache-2.0; CLI/Graph | Keine eigene WebUI im geprüften Hauptrepo; Prozessanzeige als Konzeptquelle |

Die Lizenzspalte ist keine pauschale Erlaubnis zur Codeübernahme. Es werden fachliche Konzepte neu in NEXUS implementiert; keine Fremdlogos, Stylesheets, Templates, Komponenten oder vollständigen Klassen werden übernommen. Repoaktivität, eine ausgelieferte Demo und eine Wartungszusage sind verschiedene Dinge. Die [Hummingbot-Dokumentation](https://hummingbot.org/dashboard/) nennt Condor ausdrücklich als Nachfolger.

## 4. Was die fremden Oberflächen tatsächlich tun

### 4.1 FreqUI: nachvollziehbare Trades und getrennte Aktualisierung

Die [TradeDetail-Komponente](https://github.com/freqtrade/frequi/blob/d8591ace9eeb387c0976a8e66ad466ca2ab5fc7e/src/components/ftbot/TradeDetail.vue) zeigt Trade-ID, Einstiegszeit, Entry-Tag, Menge, Preis und Ergebnis; Stop-Loss, dessen Aktualisierungszeit sowie Gebührenwährung stehen in separaten Abschnitten. Orders werden aufgeklappt und enthalten Zeitpunkt, Richtung, Preis sowie ausgeführte und verbleibende Menge. Das ist für NEXUS ein gutes Detailkonzept: Der Nutzer erkennt zuerst die Position, danach ihre Schutz- und Ausführungsbelege. NEXUS erweitert dies um Client-/Broker-ID, Belegvollständigkeit und Buchungszustand, die in dieser FreqUI-Komponente nicht als gleichwertiger vollständiger Reconciliationnachweis vorliegen.

[BotControls](https://github.com/freqtrade/frequi/blob/d8591ace9eeb387c0976a8e66ad466ca2ab5fc7e/src/components/ftbot/BotControls.vue) unterscheidet vollständiges Stoppen und Pause/StopBuy. Pause lässt die Verwaltung offener Trades weiterlaufen. Der Stop-Dialog erklärt, dass der Botloop anhält. NEXUS soll seine vorhandene Kaufpause deshalb nicht mit einem unpräzisen „Not-Aus“ umbenennen. Ein Stop aller Prozesse und ein bestätigter Verkauf aller Positionen sind fachlich andere Vorgänge. ForceEntry ist für NEXUS keine empfohlene Zusatzfunktion.

[MobileTradesList](https://github.com/freqtrade/frequi/blob/d8591ace9eeb387c0976a8e66ad466ca2ab5fc7e/src/components/ftbot/MobileTradesList.vue) ersetzt auf kleinen Geräten die gleichzeitige Listen-/Detaildarstellung durch Liste, Detail und Zurück. Dieses Prinzip passt in vorhandene NEXUS-Karten. Es erfordert keine Übernahme von Vue, Nuxt oder Tailwind.

Der [Botstore](https://github.com/freqtrade/frequi/blob/d8591ace9eeb387c0976a8e66ad466ca2ab5fc7e/src/stores/ftbot.ts) liest aktive Trades über /status, Historie über paginierte Trades, Sperren über /locks, Logs über /logs und Jobs über /background/{jobId}. Bei großer Historie werden Seiten ausdrücklich seriell geladen, um SQLite nicht mit parallelen Anfragen zu belasten. [ftbotwrapper](https://github.com/freqtrade/frequi/blob/d8591ace9eeb387c0976a8e66ad466ca2ab5fc7e/src/stores/ftbotwrapper.ts) trennt schnelle fünf Sekunden und langsame 60 Sekunden. Für NEXUS sind eher 15/30/60 Sekunden sinnvoll; der konkrete Takt wird nicht kopiert. Ein Fehler im schnellen Refresh wird teilweise nur geloggt: NEXUS braucht zusätzlich sichtbare Alterung.

Das offene [Issue 2565](https://github.com/freqtrade/frequi/issues/2565) ist besonders lehrreich. Der Maintainer erklärt, warum Summen über Dry-run/Live und unterschiedliche Währungen irreführend wären und warum häufiges Logabholen Last erzeugt. Teilfunktionen wurden laut Diskussion umgesetzt, die pauschale Fehlerzählung bewusst nicht einfach ergänzt. NEXUS besitzt die passende Kontotrennung bereits. Die offizielle [Walletkurven-Erklärung](https://www.freqtrade.io/en/stable/freq-ui/#wallet-balance) kennzeichnet den Beginn echter Erfassung, weil ältere rekonstruierte Daten nicht gleich zuverlässig sind. Für NEXUS ist eine solche Abdeckungsangabe nützlicher als ein weiterer unbelegter Prozentwert.

### 4.2 Hummingbot Dashboard: gute Trennung, problematische Null- und Erfolgssemantik

[Instances](https://github.com/hummingbot/dashboard/blob/ee42595bec85518eda0b9fe1a56046d09a3e36c6/frontend/pages/orchestration/instances/app.py) zeigt pro Bot aktive, gestoppte und fehlerhafte Controller. Ein Controllerfehler wird separat angezeigt. Allgemeine Logs und Fehlerlogs liegen in aufklappbaren Bereichen mit höchstens 50 Zeilen. Aktualisierung erfolgt als Streamlit-Fragment alle zehn Sekunden und lässt sich pausieren. Das stützt die NEXUS-Aufteilung in Übersicht und technische Details.

[Trading](https://github.com/hummingbot/dashboard/blob/ee42595bec85518eda0b9fe1a56046d09a3e36c6/frontend/pages/orchestration/trading/app.py) enthält Candlesticks, Volumen, Ausführungsmarker, offene Orders und Historie. Die Grenze ist wesentlich: get_positions/get_active_orders liefern bei Ausnahmen nach einer Fehlermeldung eine leere Liste zurück. Eine leere Darstellung kann somit sowohl „nichts vorhanden“ als auch „nicht erhoben“ bedeuten. Außerdem heißt status=submitted beim Platzieren bereits erfolgreich platziert; beim Cancel erscheint ein Erfolgstext, ohne dass die UI selbst den endgültigen Brokerzustand belegt.

Der separate [Trading-API-Router](https://github.com/hummingbot/hummingbot-api/blob/7e86651b69b13b39e0cb3e1afbf080eedd461e8d/routers/trading.py) trennt /orders/active aus Connectorzustand von /orders/search aus Historie. Konto und Connector sind Filterdimensionen. Teilfehler einzelner Connectoren werden jedoch übersprungen und geloggt. Für NEXUS folgt daraus: GET-Status muss Verfügbarkeit, Vollständigkeit, Scope und Zeitpunkt transportieren. Sonst ist auch eine sauber gestaltete Tabelle fachlich nicht belastbar.

[Portfolio](https://github.com/hummingbot/dashboard/blob/ee42595bec85518eda0b9fe1a56046d09a3e36c6/frontend/pages/orchestration/portfolio/app.py) bietet Konto-/Exchangefilter und Zeitaggregation; NEXUS darf daraus keinen botverwalteten Spotbestand aus jedem Walletasset ableiten. Öffentlich/private Seiten in permissions.py sind zudem kein vollständiger rollenbasierter Schreibschutzvertrag für NEXUS.

Die offenen Issues [279](https://github.com/hummingbot/dashboard/issues/279), [280](https://github.com/hummingbot/dashboard/issues/280) und [281](https://github.com/hummingbot/dashboard/issues/281) vom Juli 2026 beschreiben nicht passende Frontend/API-Endpunkte beziehungsweise generische 500er nach Validierungsfehlern. Das sind Nutzerberichte, keine hier reproduzierten Fehler. Zusammen mit dem offiziellen Wartungshinweis begründen sie, Infrastruktur und API-Client nicht zu übernehmen. Kleine NEXUS-Vertragstests müssen dagegen sicherstellen, dass reale HTML-/JS-Felder zur ausgelieferten Backendantwort passen.

### 4.3 Condor: aktuelle KI-Arbeit sichtbar machen, ohne Historie als Livezustand auszugeben

Condor ergänzt den alten Dashboardvergleich, weil [Hummingbot es inzwischen empfiehlt](https://hummingbot.org/condor/). Die Oberfläche ist erheblich umfangreicher als NEXUS und dient auch Agenten, Chat, Routinen, mehreren Servern und Flotten. Dieser Umfang wäre auf dem Pi unnötig.

[agentStatus.ts](https://github.com/hummingbot/condor/blob/1890c0871694c8b1c7c93ecdadb6350cd20eab6a/frontend/src/components/agent/agentStatus.ts) stuft eine laufende Entität mit ausschließlich Dry-run/Run-once-Instanzen gesondert ein. Ein lebender Prozess allein soll nicht als echter Livehandel erscheinen. NEXUS überträgt das Prinzip auf beobachtetes Konto, Demo/Live, Worker und tatsächliche Kaufbedingungen. Ein unbekannter Zustand darf dabei nicht still zum freundlichen IDLE werden, wie die generische StatusBadge-Fallbackdarstellung nahelegt.

[ActivityFeed](https://github.com/hummingbot/condor/blob/1890c0871694c8b1c7c93ecdadb6350cd20eab6a/frontend/src/components/agent/ActivityFeed.tsx) unterscheidet Hintergrundarbeiten, Konsultationen und Codeausführungen. Filterung erfolgt serverseitig, damit eine kleine geladene Stichprobe nicht als gesamte Historie ausgegeben wird. Es wird nur bei laufenden Arbeiten alle fünf Sekunden aktualisiert. Dauer und Toolanzahl sind Details, kein Ersatz für einen nachgewiesenen erfolgreichen Auftrag. Für NEXUS passt eine viel kleinere GPT-Phasenliste mit Aufgabe, Modell, Request-ID, Start, Dauer, Cache und Fehler.

[meta.py](https://github.com/hummingbot/condor/blob/1890c0871694c8b1c7c93ecdadb6350cd20eab6a/condor/web/routes/meta.py) liefert authentifizierte Build-/Plattforminformationen und erklärt ausdrücklich den Fall „neues Frontend auf Platte, alter Backendprozess noch aktiv“. NEXUS braucht keinen automatischen Updateapparat, kann aber Laufzeitversion und verfügbaren Dateistand nachvollziehbar ausgeben.

[confirmations.py](https://github.com/hummingbot/condor/blob/1890c0871694c8b1c7c93ecdadb6350cd20eab6a/condor/web/routes/confirmations.py) macht ausstehende Bestätigungen per GET erreichbar und bindet Antworten an Nutzer und Kennung. Dadurch übersteht die Bedienung einen Browserreload. Die [Registry](https://github.com/hummingbot/condor/blob/1890c0871694c8b1c7c93ecdadb6350cd20eab6a/condor/runtime/confirmations.py) liegt ausdrücklich nur im Speicher: Das ist keine dauerhafte Order-Recovery. Für NEXUS werden bestehende persistente Handelsaufträge und serverseitige Prüfungen weiter benutzt; kein LLM darf über einen neuen UI-Pfad Orders autorisieren.

[useWebSocket](https://github.com/hummingbot/condor/blob/1890c0871694c8b1c7c93ecdadb6350cd20eab6a/frontend/src/hooks/useWebSocket.ts) teilt eine Verbindung und verwaltet Abonnements pro Komponente; [websocket.ts](https://github.com/hummingbot/condor/blob/1890c0871694c8b1c7c93ecdadb6350cd20eab6a/frontend/src/lib/websocket.ts) benutzt begrenztes Reconnect-Backoff. NEXUS muss deshalb keinen Browser-WebSocket neu einführen. Ein gemeinsamer, begrenzter Pollingmechanismus ist für die derzeitige Architektur einfacher.

Gegenbelege sind wichtig: Das offene [Issue 238](https://github.com/hummingbot/condor/issues/238) beschreibt gültige Konten, die vorübergehend als nur lesbar erscheinen; [Issue 234](https://github.com/hummingbot/condor/issues/234) beschreibt historische Controller als aktuelle Positionen. Diese Berichte wurden nicht live reproduziert. Die aktuelle [Konsolidierung von Positionen](https://github.com/hummingbot/condor/blob/1890c0871694c8b1c7c93ecdadb6350cd20eab6a/condor/web/routes/positions.py) bestätigt jedenfalls ein verwandtes Darstellungsrisiko: fehlende Zahlen werden teilweise 0 und fehlgeschlagene Teilabfragen leere Listen. NEXUS behält explizites UNKNOWN und Buchungsqualität bei.

### 4.4 OctoBot: Verbindungsebenen und historische Diagnose unterscheiden

Im aktuellen Monorepo gibt es klassische Flask-/JavaScript-Webflächen und eine neue React-Node-Oberfläche. Die alte separate Tentacles-Ablage darf nicht als aktueller Gesamtstand behandelt werden.

Die klassische [bot_connection.js](https://github.com/Drakkar-Software/OctoBot/blob/dc0efc8ec36c138bd619272b2b59042778668408/packages/tentacles/Services/Interfaces/web_interface/static/js/common/bot_connection.js) fragt nach einem Browser-WebSocket-Disconnect per HTTP-Ping nach, bevor die Oberfläche als getrennt gilt. Benachrichtigungen werden auf zehn begrenzt; nach Reconnect existieren Aktualisierungscallbacks. Dieses fachliche Prinzip passt direkt: Browser→NEXUS, NEXUS→Broker-REST und Broker-WebSocket sind unterschiedliche Kanäle. Ein funktionierendes Dashboard beweist keinen Brokerkontakt; ein unterbrochener Browserkanal beweist keinen Handelsstopp.

[Dashboard-WebSocket](https://github.com/Drakkar-Software/OctoBot/blob/dc0efc8ec36c138bd619272b2b59042778668408/packages/tentacles/Services/Interfaces/web_interface/websockets/dashboard.py), [Dashboardmodell](https://github.com/Drakkar-Software/OctoBot/blob/dc0efc8ec36c138bd619272b2b59042778668408/packages/tentacles/Services/Interfaces/web_interface/models/dashboard.py) und [Trading-API](https://github.com/Drakkar-Software/OctoBot/blob/dc0efc8ec36c138bd619272b2b59042778668408/packages/tentacles/Services/Interfaces/web_interface/api/trading.py) wurden als Datenkette geprüft. Candlestick-/PNL-Funktionen liefern hilfreiche fachliche Beispiele, sind aber kein Grund, NEXUS-SVGs durch ein großes Chartpaket zu ersetzen.

Die Node-UI kennzeichnet im [ImportedDebugSnapshotBanner](https://github.com/Drakkar-Software/OctoBot/blob/dc0efc8ec36c138bd619272b2b59042778668408/packages/tentacles/Services/Interfaces/node_web_interface/src/components/Debug/ImportedDebugSnapshotBanner.tsx) die Ansicht ausdrücklich als statischen, nur lesbaren Snapshot mit Quelle, Zeitpunkt und Schemaversion. NEXUS kann dieselbe Ehrlichkeit bei Diagnosepaketen und alten Brokerzuständen verwenden. Eine kleine JSON-Detailansicht ist sinnvoller als Rohdaten auf dem Hauptdashboard.

[StopAutomationDialog](https://github.com/Drakkar-Software/OctoBot/blob/dc0efc8ec36c138bd619272b2b59042778668408/packages/tentacles/Services/Interfaces/node_web_interface/src/components/OctoBots/StopAutomationDialog.tsx) nennt die betroffenen Instanzen, sperrt erneutes Absenden während der Anfrage, verarbeitet mehrere Stopanträge seriell und berichtet Teilfehler. Der Erfolgstext lautet sinngemäß „Stop angefordert“; das ist für NEXUS wesentlich genauer als „alle Positionen geschlossen“.

Offene Issues [3671](https://github.com/Drakkar-Software/OctoBot/issues/3671) und [3632](https://github.com/Drakkar-Software/OctoBot/issues/3632) melden Probleme beim Start beziehungsweise der Erreichbarkeit der Node-/Bot-Oberfläche. [3639](https://github.com/Drakkar-Software/OctoBot/issues/3639) betrifft fehlende Kraken-Kerzen trotz WS-Abonnement. Diese Fälle sprechen für separate Kanal- und Datenaltersanzeigen. Sie belegen keine entsprechenden Fehler im NEXUS-OKX-Adapter.

### 4.5 NOFX: Entscheidungen lesbar, Erfolg kritisch formulieren

Die [DecisionCard](https://github.com/NoFxAiOS/nofx/blob/638d4042118995fbf1a38d3822b1139aa3c6b467/web/src/components/trader/DecisionCard.tsx) zeigt einen Zyklus mit Zeitpunkt, einzelne Aktionen und Fehler sowie getrennte Ausführungsprotokolle. Details lassen sich aufklappen. Das ist ein guter Ausgangspunkt für „Warum handeln / nicht handeln?“. NEXUS benötigt dafür gespeicherte technische Checks, Risikogründe und qualifizierte PULSAR-Ergebnisse. Es soll keine Begründung nachträglich erfinden.

NOFX zeigt zusätzlich Eingabeprompts und als CoT bezeichnete Texte. NEXUS soll weder Rohprompts mit Kontodaten noch vermeintliche vollständige interne Gedankengänge als notwendige Diagnose etablieren. Eine kurze fachliche Zusammenfassung, verifizierbare Checks, Quellen und Requestmetadaten sind wartbarer und leichter zu beurteilen.

[TraderDashboardPage](https://github.com/NoFxAiOS/nofx/blob/638d4042118995fbf1a38d3822b1139aa3c6b467/web/src/pages/TraderDashboardPage.tsx) fragt vor manuellen Verkäufen nach und verarbeitet Sammelverkäufe bewusst seriell. Dies vermeidet zugleich Kontononce-/Ratelimitkonflikte. Trotzdem darf die Erfolgsformulierung nicht kopiert werden: Nach dem API-Aufruf wird „Position closed“ angezeigt. Der [Backendhandler](https://github.com/NoFxAiOS/nofx/blob/638d4042118995fbf1a38d3822b1139aa3c6b467/api/handler_trader_status.go) antwortet nach CloseLong/CloseShort; fehlgeschlagener nachgelagerter Orderabgleich wird protokolliert. Damit ist die Antwort kein universeller Nachweis einer vollständig bestätigten und verbuchten Schließung. Ein Lighter-Sonderpfad schätzt Gebühren und schreibt direkt FILLED. Das ist ausdrücklich keine geeignete Vorlage für NEXUS-Buchungsqualität.

[PositionHistory](https://github.com/NoFxAiOS/nofx/blob/638d4042118995fbf1a38d3822b1139aa3c6b467/web/src/components/trader/PositionHistory.tsx) und [ChartWithOrders](https://github.com/NoFxAiOS/nofx/blob/638d4042118995fbf1a38d3822b1139aa3c6b467/web/src/components/charts/ChartWithOrders.tsx) zeigen den Nutzen von Historienfiltern und Ausführungsmarkern. NEXUS hat diese Konzepte schon teilweise. Zusätzliche Indikatoren oder ein externes TradingView-Widget bleiben nachrangig.

Das offene [Issue 1345](https://github.com/NoFxAiOS/nofx/issues/1345) beschreibt widersprüchliche Anzeigen des verfügbaren Guthabens; [Issue 1492](https://github.com/NoFxAiOS/nofx/issues/1492), inzwischen geschlossen, meldete einen Frontend-Syntaxfehler nach einer Änderung. Daraus folgen für NEXUS Datenvertragstests und echte JavaScript-/Browserprüfung, keine Aussage, dass diese konkreten NOFX-Probleme heute noch bestehen.

### 4.6 Jesse, Lumibot und TradingAgents: sinnvolle Begrenzung

Jesses aktueller [Ordercontroller](https://github.com/jesse-ai/jesse/blob/60f882e88c2d28e0f8cbc7f916758434e6ab7ce6/jesse/controllers/order_controller.py) stellt Einzelorderdetails und gefilterte Livehistorie bereit: Order-ID, Status, Symbol, Datum, Seite, Limit und Offset. Der [Livecontroller](https://github.com/jesse-ai/jesse/blob/60f882e88c2d28e0f8cbc7f916758434e6ab7ce6/jesse/controllers/live_controller.py) trennt Sessions, Orders, Logs, Strategycharts und Equitykurve. Eine bestehende gestartete Session wird bei erneutem Start mit derselben ID zurückgewiesen. Übertragbar sind eindeutig adressierte Details und serverseitige Filter. Der Maintainer erklärt in [Issue 527](https://github.com/jesse-ai/jesse/issues/527), dass der aktuelle GUI-Originalcode geschlossen ist und nur minifizierte Artefakte ausgeliefert werden. Ein vollständiger aktueller Komponentenvergleich wird deshalb ausdrücklich nicht behauptet.

Lumibot empfiehlt einen typisierten [Tearsheet-Metrikexport](https://github.com/Lumiwealth/lumibot/blob/f987da3e2c81a5ae2cb6edf35457d2aa54aacf8b/docs/TEARSHEET_METRICS.md) als gemeinsame Grundlage für HTML, APIs und Agenten. NEXUS sollte ebenso eine serverseitige Kennzahlendefinition benutzen, statt Summen im JavaScript anders zu berechnen. Der Report ist keine Brokerhealth-UI und kein Grund, QuantStats auf dem Pi in den Livezyklus einzubauen.

TradingAgents besitzt im geprüften Hauptrepo keine WebUI. Die [CLI](https://github.com/TauricResearch/TradingAgents/blob/be952b8eccb49720509af544c6675233bc1f10d0/cli/main.py) unterscheidet pending/in_progress/completed/error, führt Nachrichten und Toolcalls in begrenzten Queues und zählt einen Bericht erst nach Abschluss des zuständigen Stadiums. Dieses Konzept ist für PULSAR nützlich. Eine fremde Agentenrolle oder ein fertiger Text beweist jedoch keinen NEXUS-Providerrequest und keine Handelsfreigabe.

### 4.7 Nutzerfeedback richtig gewichten

Der [Reddit-Bericht zur LAN-Erreichbarkeit von FreqUI](https://www.reddit.com/r/firewalla/comments/1qok6je/impossible_to_expose_frequi_to_lan_from/) vom Januar 2026 beschreibt eine funktionierende lokale UI, die von anderen Geräten aus nicht erreichbar ist. Das ist ein nicht reproduzierter Einzelfall aus WSL/Docker und kein Beweis eines NEXUS-Fehlers. Er bestärkt lediglich die Trennung von Netzwerkzugang, WebUI-Prozess und Brokerstatus. Ein älterer [Freqtrade-Deployment-Thread](https://www.reddit.com/r/algotradingcrypto/comments/nqmbcx/freqtrade_bot_deployments_stay_organized_with/) von 2021 erklärt bereits die integrierte FreqUI; er ist kein Beleg für aktuelle Funktionalität. Neue Maßnahmen stützen sich auf den heutigen Code und die konkreten Issues, nicht auf Werbeberichte oder unbelegte Renditeerzählungen.

## 5. Funktionsvergleich und Bewertung

Skala: Nutzen 10 = besonders hilfreich für Georgs Betrieb; Aufwand 10 = groß; Risiko 10 = hohes Regressions-/Handelsrisiko. Die Bewertungen sind fachliche Einschätzungen, keine Messwerte oder Aufwandsschätzungen in Tagen. „Angepasst übernehmen“ bedeutet eigenständige kleine Umsetzung im NEXUS-Stil.

| Funktion | NEXUS heute | FreqUI | Hummingbot/Condor | OctoBot/NOFX/Jesse | Nutzen / Aufwand / Risiko | Empfehlung |
|---|---|---|---|---|---|---|
| Getrennte Brokerzustände | Brokerkarten und Kontokontext | Pro Bot eigener Store | Controller/Server getrennt | Pro Instanz/Konto | 10 / 4 / 3 | Angepasst übernehmen, UI-001 |
| REST/WS/Worker/Aktualität | Noch teilweise verdichtet | Ping + UI-WS | Gemeinsame WS-Verbindung, eigene Serverdaten | OctoBot prüft Disconnect mit HTTP | 10 / 5 / 3 | UI-001, unbekannte Kanäle ehrlich lassen |
| Kaufpause versus Positionsverwaltung | Vorhandene Kaufpause | StopBuy explizit anders als Stop | Controllerstop teils schließend | Stopantrag mit Details | 10 / 2 / 2 | UI-013, bestehende Semantik schützen |
| Lifecycle/Fill/Verbuchen | Backend vorhanden, Detail begrenzt | Tradeorders mit filled/remaining | Activeorders getrennt von Historie | Jesse Einzelorder-API | 10 / 5 / 4 | UI-002 |
| Reconciliation mit Scope | Klärung vorhanden | Keine gleichwertige Kontobeweiskette in geprüfter UI | Condor kennt Ergebnisresiduen | Diagnose-Snapshots | 10 / 5 / 4 | UI-003, kein positives MATCHED erfinden |
| Eigentum versus Wallet | Bereits differenziert | Wallet und Trades getrennt | Portfolioaggregation | Unterschiedliche Positionsmodelle | 10 / 2 / 2 | NEXUS-Regeln bewahren |
| Warum hält der Bot? | Exit-/Managementgrund teilweise vorhanden | Entrytag und Schutzdetails | Controllerzustand | DecisionCard + Aktionen | 8 / 4 / 3 | Gespeicherten Grund anzeigen; sonst unbekannt |
| Entscheidung versus Ausführung | Bereits getrennt im Logbuch | Trade-/Ordermodell | submitted nicht final | NOFX success zu grob | 10 / 3 / 3 | Bestehende Trennung überall konsistent |
| Entscheidungsfilter/IDs | Viele Filter vorhanden | Trades/Tags | Condor serverseitige Aktivitätsfilter | Jesse ID-/Datum-/Statusfilter | 9 / 3 / 2 | UI-007 gezielt ergänzen |
| PULSAR-Quellen und Community | Belege/Claims schon stark | Kein gleichwertiges Konzept | Agentenaktivität | TA Phasen, keine WebUI | 9 / 4 / 3 | UI-005, keine erfundenen Autoren |
| GPT tatsächlich ausgeführt | Nicht durchgehend belegbar | Jobphasen | ActivityFeed | NOFX Ausführungslog | 10 / 5 / 3 | UI-004, neue Telemetrie vor Darstellung |
| Kosten versus Reservierungen | Budget vorhanden | Nicht vergleichbar | Agentennutzung | Separates AI-Kosten-API | 9 / 3 / 3 | Explizite Wertequalität statt 0 |
| Universum und Aufnahmegründe | Schon vorhanden | Pairlist/Sperren | Connector-/Paarwahl | Strategiekonfiguration | 7 / 2 / 1 | UI-008, letzte Prüfung ergänzen |
| Favoriten | Prioritätsmerkmal vorhanden | Paarwahl, keine NEXUS-Semantik | Kein gleichwertiger Ersatz | Watchlistähnliche Funktionen | 6 / 2 / 1 | Filter jetzt; CRUD separat begründen |
| Underdog-Scoreverlauf | Keine lückenlose Zeitreihe | Indikatorcharts | Researchcharts | Entscheidungszyklen | 4 / 6 / 4 | Später, keine interpolierte Historie |
| Candles + Entry/Exit | SVG bereits vorhanden | Gute Detailcharts | Candles/Volumen/Marker | NOFX/Jesse Charts | 7 / 3 / 2 | Bestehende Charts verbessern |
| Orderbuch/Chartwall | Kein eigener Terminal | Frei konfigurierbare Charts | Orderbook/Tradingterminal | Große NOFX-Marketsicht | 2 / 8 / 6 | Nicht übernehmen |
| Performancequalität | Starke Konto-/Gebührentrennung | Erfassungsbeginn kenntlich | Getrennte realisierte/unrealisierte Werte | Lumibot typisierter Report | 10 / 3 / 3 | UI-009 |
| Profitfactor/Drawdown/Sharpe | Datenbasis teilweise unzureichend | Backendmetriken | Controllerkennzahlen | Tearsheet für Research | 5 / 7 / 6 | Nur nach validierter Definition |
| Risiko/Reserve/Tagesbasis | Daten verstreut | Stop-Loss/At-risk | Riskmanagement pro Controller | Gridrisk | 9 / 4 / 3 | UI-010 ohne synthetischen Score |
| Logfilter und Fehlerauswirkung | Serverseitig begrenzt | Manueller Logabruf | Filter/Details | Bounded Notifications | 9 / 3 / 2 | UI-011 |
| Pi/System/Telegram | Grunddaten vorhanden | Botstatus | Ressourcen-/Buildendpoints | Node-Betrieb | 7 / 2 / 1 | UI-012, Diagnosebereich |
| Mobile Details | Responsive vorhanden | Liste→Detail→Zurück | Condor-Drawer | Anpassbare Dialoge | 9 / 3 / 2 | UI-014 |
| Polling | Unterschiedliche Intervalle, teils überlappend | Schnell/langsam und serielle Seiten | Nur aktive Jobs schnell | WS-Subscriptions | 9 / 3 / 3 | UI-006 |
| Zusätzliche Hauptseiten | Bereits ausreichende Gliederung | Umfangreiches Menü | Sehr großer Agenten-/Serverumfang | Mehrere Oberflächen | 3 / 7 / 5 | Keine neue Hauptseite erforderlich |

## 6. Konkreter Maßnahmenkatalog

| ID / Priorität | Problem und gewählte Änderung | Backendbedarf und Dateien | Nutzen / Aufwand / Risiko | Nachweisbedarf |
|---|---|---|---|---|
| UI-001 / P0 | „Verbunden“ ersetzt keine Aussage zu Kauf, Exit, Worker, REST und WS. Bestehende Brokerkarte zeigt getrennte Zeilen und Auswirkung | webui/state.py, app.py, dashboard.js; belegte Laufzeitfelder, Alter, Scope, Kanalzustände; keine Liveabfrage pro Render | 10 / 4 / 3 | eToro offline beeinflusst OKX nicht; alte Daten niemals grün |
| UI-002 / P1 | Orders als Detail unter Handel; erlaubte Statuskette, tatsächlich vorhandene Fills und Events, terminal/Belege/Buchung getrennt | Read-only Lifecycleprojektion, feste ID+Broker+Konto+Umgebung, serverseitiges Limit; trades.js | 10 / 5 / 4 | Duplicateevents, Partial/Cancel, alte Order, Scopefehlversuch |
| UI-003 / P0 | Bestehenden Klärungstab mit Auswirkung, Alter, Grund und fehlendem Beleg präzisieren | accounting/lifecycle/reconciliation lesen; keine automatische Bestandsänderung im UI | 10 / 5 / 4 | Gebührenlücke erzeugt keinen offenen Trade; leere Liste kein MATCHED |
| UI-004 / P0 | GPT-Phasen nicht gestartet, Cache, angefragt, laufend, Timeout, Fehler, erfolgreich getrennt anzeigen | Router/PULSAR-Metadaten: request_id, task, model, started_at, duration, origin, error_code; pulsar.js | 10 / 5 / 3 | Cache erzeugt keinen neuen Aufruf; Fehler vor Versand bleibt nicht gestartet |
| UI-005 / P1 | Quellenstatus, Communitybelege und Grenzen aufklappbar; fehlgeschlagene Stadien nicht „ausstehend“ | vorhandene checks/sources/verified_evidence verwenden; verlässliche Counts nur wenn geliefert | 9 / 4 / 3 | Quelle leer/Timeout/unzulänglich/erfolgreich; fehlende Autoren kein 0 |
| UI-006 / P1 | Ein laufender Abruf pro Ressource, Hintergrundtabs pausieren; Datenalter sichtbar | common.js Scheduler; lokale Snapshots und begrenzte Endpunkte | 9 / 3 / 3 | Langsamer Request, Ausfall, Sichtbarkeitswechsel, keine Requestschlange |
| UI-007 / P1 | Bestehendes Entscheidungslog um verständliche ID-/Statusanzeige und Filterpräzision ergänzen | decision_log/filter, logbook.js; keine nachträgliche LLM-Begründung | 9 / 3 / 2 | Signal abgelehnt vs Brokerrejected vs Timeout; Brokerfilter über alle Seiten |
| UI-008 / P2 | Letzte Prüfung und Favoritenfilter; keine neue Kaufwirkung | universe.js nutzt letzte_pruefung/favorit; keine Datenmigration für Anzeige | 7 / 2 / 1 | Favorit bleibt nur Priorität; Underdogs-/Universumsnavigation erhalten |
| UI-009 / P0 für Korrektheit, P2 für neue Kennzahlen | Bestehende bestätigte Performance mit Zeit-/Abdeckungsangabe; fehlend bleibt unbekannt | Ein zentraler Performancevertrag; performance.js bleibt Darsteller | 10 / 3 / 3 | Fehlende Gebühren, mehrere Währungen, Demo/Live, 0 echte Trades |
| UI-010 / P1 | Risiko nicht als Gesamtampel; Tageslimit, Bezugsgröße, Reserven und Sperrgrund im Brokerdetail | Nach Corefix konsistente Risikoprojektion; keine Freigabelogik im Browser | 9 / 4 / 3 | Baselinewechsel, ausgelöster Halt, eToro/OKX getrennt |
| UI-011 / P1 | CRITICAL, Broker/Komponente und ID-Suche; Fehlertext mit Handelsauswirkung | vorhandene begrenzte Logendpunkte; strukturierte Ereignisse wo vorhanden | 9 / 3 / 2 | Lange Meldung, fehlende Metadaten, Injectiontext, Filtergrenzen |
| UI-012 / P2 | Kompakte Systemdetails um vorhandene CPU-/Modellwerte ergänzen; fehlende Uptime unbekannt | pi_system + Laufzeitstatus; keine Hardwarepflicht, keine neue Monitoringplattform | 7 / 2 / 1 | Nicht-Pi, Sensor fehlt, Diskwarnung, Telegram nur konfiguriert |
| UI-013 / P0 | Bestehende kritische Aktionen bestätigen, Doppelklick sperren, „angefordert“ korrekt formulieren | Bestehende CSRF-/Konto-/Serverprüfungen erhalten; Zustand nachladen | 10 / 3 / 4 | Ablehnen/Bestätigen, stale Konto, doppelte Anfrage, Timeout ohne POSTretry |
| UI-014 / P1 | Bestehende Karten/Details mobil bedienbar; lange IDs umbrechen, Fokus und leere Zustände | nexus.css nur ergänzend, bestehende Klassen und Navigation; kein Frameworkwechsel | 9 / 3 / 2 | Desktop/Tablet/Smartphone, Tastatur, 0/viele Zeilen, lange Fehler |

UI-P3 bleibt bewusst klein: zusätzliche Chartkonfiguration, Scorezeitreihen und komfortable Favoritenbearbeitung erst bei belegtem Bedarf. Diese Wünsche werden nicht unter Zeitdruck als P0 ausgegeben. Die Tabelle dokumentiert die Auswahl; die verbindliche Abschlusstabelle muss pro ID zwischen umgesetzt/getestet, unvollständig validiert, teilweise, nicht umgesetzt und bewusst nicht umgesetzt unterscheiden.

## 7. Darstellungskonzept im bestehenden NEXUS-Stil

### Brokerkarte

Heute: Brokername, „Verbunden“, Kauffreigabe, Zahlen und aufklappbare Bedingungen.

Problem: Eine einzelne Ampel kann nicht ausdrücken, dass der Worker lebt, der REST-Abruf alt ist und ein Broker-WS fehlt.

Vorschlag: dieselbe Panelkarte, dieselbe Farbwelt und derselbe Platz im Dashboard. Sofort sichtbar sind Broker/Konto/Umgebung, neue Käufe und notwendige Aufmerksamkeit. Darunter steht eine kurze separate Aussage zur Positionsverwaltung. „Verbindung & Bedingungen“ zeigt Worker, REST, Broker-WS, Datenalter und letzten belegten Kontakt. Ein unbekannter Kanal wird so bezeichnet. Der Nutzer klickt bei Auftragsproblemen direkt in Handel/Klärung. Zusätzliche Backenddaten dürfen nur aus echten Laufzeitereignissen stammen; fehlende Telemetrie wird nicht ergänzt, indem man einen erfolgreichen WebUI-GET als Brokererfolg wertet.

### Orderdetail und Reconciliation

Heute: aktive Lifecycle-Zeilen und Klärungstab.

Vorschlag: Details einer bestimmten Order im bestehenden Handel aufklappen. Oben Symbol, Broker, Umgebung, Auftragsstatus und Zeit. Danach drei fachliche Aussagen: Broker terminal? Ausführungsbelege vollständig? Verbucht? Darunter Fills mit nativer Menge, Preis, Gebührenwährung sowie chronologische tatsächlich gespeicherte Ereignisse. Eine visuelle Ablaufleiste darf nur belegte Übergänge markieren. „Signal“ und „Position erstellt“ dürfen nicht allein aus einem Orderdatensatz erfunden werden.

Reconciliation ergänzt Ursache, letzten Versuch und betroffene Handelsdomäne. MATCHED setzt einen vollständigen, aktuellen Vergleich mit passender Identität voraus. BROKER_ONLY darf nicht automatisch als NEXUS-Eigentum erscheinen. INTERNAL_ONLY bei unvollständiger Brokersnapshotantwort bleibt zunächst unklar. Eine bloße Gebührenlücke ist ein Buchungsfall, kein neuer Positionsexit.

### PULSAR und GPT

Heute: Quellen- und Analyseinformationen liegen teilweise nebeneinander; ein fehlgeschlagenes Stadium kann „ausstehend“ heißen.

Vorschlag: dieselben Kandidatenkarten. Ebene 1 zeigt Qualifizierung und wichtigste Beleglücke. Ebene 2 zeigt Quellen und Phasen: Datenabruf, Vorprüfung, GPT-Websuche, Vertiefung, Gegenprüfung. Ebene 3 liefert Requestmetadaten und einzelne Referenzen. Drei gezielte Begriffe helfen: „nicht gestartet“, „aus Cache“ und „Anfrage gestartet, Ausgang …“. Provider erreichbar ist nur das Resultat einer konkreten zeitlich benannten Prüfung. Ein alter erfolgreicher Request belegt keine aktuelle Erreichbarkeit.

Community-Breite wird aus belegten Beiträgen, Autorenkennungen, Communities und Plattformen erklärt. Ohne diese Daten steht „nicht bestimmbar“. Treffer-, Feed- und Autorenanzahl sind keine austauschbaren Größen; syndizierte News, Crossposts und Duplikate dürfen keine unabhängigen Stimmen vortäuschen. Ein Backend kann Mengen nur dann liefern, wenn es sie tatsächlich erhoben und dedupliziert hat. Eine Diagrammquote wie „Quellenqualität 97 %“ bleibt ausgeschlossen.

### Entscheidungs- und Positionsansicht

Die vorhandenen Signalchecks und Ablehnungsgründe bleiben entscheidend. Ein prägnanter Satz lautet etwa „Signal vorhanden; Kauf durch Liquiditätsprüfung abgelehnt“, wenn genau diese Felder gespeichert wurden. Indikatorwerte erscheinen mit Analysezeit und Strategiekennung im Detail. Für „Warum hält NEXUS?“ wird der letzte gespeicherte Exit-/Managementgrund ausgegeben; fehlen entsprechende Daten, heißt es „letzte Haltebegründung nicht protokolliert“. Ein generisches KI-Narrativ wird nicht nachträglich als damalige Entscheidung ausgegeben.

## 8. Informationshierarchie, Raten und Backendvertrag

| Bestehende Seite | Ebene 1: sofort | Ebene 2: Entscheidung | Ebene 3: Diagnose |
|---|---|---|---|
| Übersicht | Broker, Kaufzustand, Aufmerksamkeit | Positionen und bestätigte Ergebnisse | Kanäle, Alter, Pi, Telegram |
| Handel | Offen, geschlossen, Klärung | Schutz, Restmenge, Exitgrund | IDs, Lifecycle, Fills, Belege |
| PULSAR | Kandidat und Qualifizierung | wichtigste Lücke, tatsächliche KI-Phase | Quellen, Communitygrundlage, Requestmetadaten |
| Universum/Underdogs | Status und Kaufblock | Aufnahme-/Abgangsgrund, Favorit | letzte Prüfung und Auswahlabdeckung |
| Analyse | laufender Job und Ergebnis | Datengrundlage, Modellgrenzen | Ausgabe und technische Fehler |
| Logbuch | Fehler/Ablehnung im gewählten Scope | fachlicher Grund und Wirkung | vollständige gespeicherte IDs und Ereignisse |
| Einstellungen | gewählter Bereich | konkrete Änderung und Wirkung | Verbindungstest, Systemdetails |

| Ressource | Empfohlener Takt bei sichtbarer Seite | Grenze |
|---|---|---|
| Broker-/Kauf-/Auftragsübersicht | 15–20 s, manuell aktualisierbar | Maximal ein laufender Abruf je Ressource; keine Brokeranfrage aus Rendern |
| Geöffnete Orderdetails | Beim Öffnen und gezielter Aktualisierung | Begrenzte Fills/Events; keine gesamte DB pro Intervall |
| Performance | 60 s oder bei Filteränderung | Serverseitige Aggregation; keine Chartneuberechnung bei identischen Daten |
| PULSAR | 30 s bei Sichtbarkeit | Geöffnete Belegdetails und Formulare nicht zurücksetzen |
| System/Health | 60 s oder bei geöffneter Diagnose | Kein CPU-Dauersampling durch Browser |
| Universum/Logs/Historie | Bei Aufruf/Filter; optional 60 s für sichtbare Übersicht | Serverseitige Limits, Filterscope nennen |
| Laufender Analysejob | Bestehende 2 s, nur solange aktiv | Stoppen bei terminalem Zustand, kein dauerhafter Gesamtscan |

Ein Statusvertrag braucht mindestens Komponente, Broker, Konto, Umgebung, Zustand, Zeitpunkt, Herkunft und Grund. Für Listen kommen Vollständigkeit/Begrenzung und Filter hinzu. Geldwerte brauchen Währung und Qualitätsstatus. Ein einzelner status=ok-Wert reicht nicht aus. Neue Endpunkte bleiben authentifiziert und lesend; POST-Aktionen behalten CSRF, bestätigte Kontoidentität und Corevalidierung. Ein Browserfehler darf weder Risikosperren zurücksetzen noch einen Orderretry erzeugen.

## 9. Bewusst nicht übernehmen

1. Keine fremde Farbpalette, Logos, CSS-Strukturen, Adminvorlagen oder vollständigen Layouts. Das NEXUS-Logo, vorhandene Designvariablen, Karten und Hauptnavigation bleiben maßgebend.
2. Keine Hummingbot-/Condor-Microservice-, Docker-, Flotten- oder Agentenplattform auf dem Pi. Einzelne Diagnosekonzepte rechtfertigen keine zusätzliche Betriebsarchitektur.
3. Keine Quick-Buy-/ForceEntry-Schaltfläche und kein generisches „alles liquidieren“. Neue Handelsautorität ist kein UI-Komfortdetail.
4. Kein HTTP-Erfolg als Fillnachweis; kein „geschlossen“ nach bloß akzeptiertem Verkaufsantrag.
5. Keine Nullwerte bei ausgefallenen Datenquellen; keine positive Abgleichampel aus einer leeren Fehlerliste.
6. Keine gemischte Summe über eToro/OKX, mehrere Konten, Demo/Live oder verschiedene Währungen ohne separat begründete Umrechnung.
7. Kein vollständiger Rohprompt-/Gedankengangviewer als Standarddiagnose; keine Geheimnisse oder unnötigen Kontodaten in Logs.
8. Kein neuer GPT-Aufruf allein zum Erklären jeder einzelnen WebUI-Zeile. Vorhandene Begründungen werden dargestellt.
9. Kein Chartterminal mit Orderbuch, Heatmaps und vielen gleichzeitigen Charts ohne konkreten Überwachungsnutzen.
10. Keine scheinpräzisen Community-, Health- oder Risikoprozentzahlen; keine rückwirkend erfundenen Scoreverläufe.
11. Kein automatisches Beseitigen von Reconciliationfällen per UI. Ein Befund muss durch echte Broker-/Buchungsbelege geklärt werden.
12. Keine neue Hauptseite für jedes technische Subsystem. Die bestehenden fachlichen Seiten reichen für die ausgewählten Änderungen.

## 10. Prüfplan für die tatsächliche Umsetzung

Diese Tabelle beschreibt erforderliche Prüfungen, keine bereits bestandenen Tests. Die tatsächlichen Ergebnisse gehören in TEST_REPORT.md.

| Fall | Erwartbares Verhalten |
|---|---|
| Desktop 1440, Tablet 768/1024, Smartphone 390/430 | Navigation vollständig, Karten bedienbar, lange IDs umbrechbar, Tabellen horizontal nutzbar |
| eToro offline, OKX online | Nur eToro verliert aktuelle Freigabe; OKX behält seinen eigenen belegten Status |
| REST erfolgreich, Broker-WS getrennt | Beide Kanäle sichtbar; Handelswirkung aus Corezustand, nicht aus CSS |
| Browserrequest fehlgeschlagen | Letzte Daten klar als alt/unklar markiert; kein scheinbar frischer Zeitstempel |
| GPT vor Request abgelehnt | „nicht gestartet“ plus Vorfilter-/Budget-/Precheckgrund |
| GPT tatsächlich Timeout | Modell, Start/Dauer und Timeout; keine Darstellung als nie aufgerufen |
| Cachetreffer | Kein zusätzlicher Providerrequest behauptet; Cacheherkunft erkennbar |
| Partial Fill, Cancel, späterer Fill | Menge und Verlauf korrekt; Orderende, Belegvollständigkeit und Verbuchung unabhängig |
| 0 Trades und nicht verfügbare Daten | Unterschiedliche Leerzustände; unbekannt ist keine Nullperformance |
| Viele Trades/Events | Limits/Pagination; keine unbegrenzte DOM-/DB-Arbeit, keine überlappenden Nachholanfragen |
| Fehlende Gebühren | Netto offen oder bestätigte Teilsumme; geschlossener Trade bleibt geschlossen |
| Reconciliation unvollständig | Kein MATCHED; betroffene Domäne und fehlender Beleg sichtbar |
| Favoriten/Underdogs | Priorität ändert keine Kaufregel; bestehende Seiten und Filter bleiben |
| Lange Fehler, HTML-/Scripttext | Als Text dargestellt; kein Layoutbruch oder Scriptausführung |
| Kritische Aktion abgelehnt/Timeout/Doppelklick | Keine Ausführung bei Abbruch, kein blinder POSTretry, Button während Versand gesperrt |
| Neue UI gegen alte Daten | Fehlende Felder ergeben unbekannt; keine Crashseite und kein fälschliches LIVE |
| Pi-Sensor fehlt | „nicht verfügbar“; übrige UI bleibt bedienbar |
| Telegram eingerichtet, Zustellung fehlt | Einrichtung und tatsächliche Zustellung werden getrennt |

Nach jedem größeren Schritt werden reale APIs und die tatsächlichen JavaScriptdateien geprüft, nicht nur statische Existenztests. Für Raspberry-Pi-CPU/RAM und echte Brokerkanäle ersetzt ein Desktopbrowser keine Pi-/Kontovalidierung. Ein erfolgreicher UI-Test beweist keine Handelskorrektheit; umgekehrt beweist ein bestandener Coretest keine nutzbare mobile Ansicht.

## 11. Antworten auf A–J

**A. Was machen andere besser?** FreqUI macht Trade-/Orderdetails mobil gut zugänglich; Hummingbot gliedert Controller und Logs; Condor zeigt laufende Arbeiten und Buildabweichungen; OctoBot unterscheidet statische Diagnose von Liveansicht; NOFX trennt Aktionen und Ausführungsprotokolle sichtbar.

**B. Was macht NEXUS bereits besser?** Die für diesen Bot notwendige Trennung nach Broker, Konto, Umgebung und Währung, die Unterscheidung eigener Positionen von Wallet-/Fremdbestand, vorsichtige Gebührenqualität, geschlossene Buchungsklärungen und belegte PULSAR-Quellen. Das ist eine Aussage über die geprüften Pfade, kein umfassendes Qualitätsranking aller Projekte.

**C/D. Was übernehmen?** Fachliche Details, klare Statusdimensionen, aktuelle Zeitstände, begrenzte Historien, serverseitige Filter, echte Aufrufmetadaten und kleine mobile Drilldowns. Fast alles wird angepasst neu implementiert.

**E. Was nicht?** Fremddesigns, Plattformwechsel, neue manuelle Handelswege, fehlertolerante Nullfiktionen, HTTP-Erfolg als Fill und ungesicherte Finanz-/Communitykennzahlen.

**F. Welche Seiten verbessern?** Übersicht, Handel/Klärung, PULSAR, Logbuch und bestehende Universums-/Systemdetails. Einstellungen und Analyse bleiben in ihrer bisherigen Rolle.

**G. Neue Seiten?** Für die ausgewählten Maßnahmen keine neue Hauptseite notwendig. Eine spätere eigenständige Diagnosefläche wäre erst bei nachgewiesener Überlastung der vorhandenen Details sinnvoll.

**H. Backendänderungen?** Read-only Statusprojektion mit Herkunft/Alter/Scope; adressierte Lifecycle-/Filldetails; verlässliche GPT-Telemetrie; konsistente Risiko- und Datenqualitätsfelder. Kein zweiter Orderwriter.

**I. Prioritäten?** UI-P0 beseitigt irreführende Zustände und Erfolgsbehauptungen; UI-P1 schafft Auftrags-/Broker-/GPT-Nachvollziehbarkeit und belastbares Aktualisieren; UI-P2 verbessert Filter und vorhandene Analysen; UI-P3 bleibt optional und klein.

**J. Wie bleibt NEXUS erkennbar?** Gleiche Routen, Navigation, Farben, Logo und Panels. Zusätzliche Wahrheit steckt in präziseren Texten und aufklappbaren Details, nicht in einem neuen Design.
