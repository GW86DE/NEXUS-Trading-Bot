# NEXUS 9.8.2 — Korrekturen und Prüfbericht

Stand: 9. September 2026. Grundlage sind der Bericht zu 9.8.0, dessen eigener Gegencheck an 9.8.1, die Übergabe, Logs, Datenbankkopie und Brokerbelege. 9.8.2 ersetzt die noch nicht installierte 9.8.1. Der vorgesehene Ausgangsstand auf Georgs Pi ist 9.8.0.

## Ergebnis

Die fünf Hauptbefunde und die zusätzlich reproduzierten Geldpfad-, Nachrichten- und KI-Fehler wurden im Code bearbeitet und mit Akzeptanztests geprüft. Die BTC-Preisbandkorrektur bleibt enthalten. Auch die manuelle Verkaufskette wird jetzt tatsächlich bis zur Buchung beziehungsweise zur belegten Sperre ausgeführt. Die alten Diagnosefälle, die fehlerhaftes Verhalten absichtlich als Sollzustand behaupteten, wurden nicht als Freigabenachweis übernommen.

Dies ist ein offline geprüftes vollständiges Korrekturpaket für die DEMO-Abnahme. Es ist kein Nachweis eines schon erfolgreichen BTC-Verkaufs auf dem Pi und keine Garantie, dass jede Börsenorder ausgeführt wird. Kein Broker erhielt Testorders; die hochgeladenen Originalzustände wurden nicht überschrieben.

## Die fünf Hauptbefunde

| Befund | Änderung in 9.8.2 | Nachweis / Grenze |
| --- | --- | --- |
| Manueller OKX-Verkauf blockiert sich selbst | Manuelle Anforderung und begonnene Übermittlung sind getrennt. Nur die zugehörige einmalig beanspruchte Bedienanfrage darf ihren vorbereiteten Zustand weiterführen. Kurslesen erfolgt vor Änderung der Verwaltung. | Echte Queue-/Engine-/Ledger-Tests: No-Fill, Kursfehler, Voll-/Teilfüllung, unklarer Auftrag, fehlgeschlagene Buchung und Abschluss nach Absturz. Kein zweiter SELL bei Unklarheit. |
| Python 3.11 scheitert an f-String | Die inkompatible Meldungszeile wurde korrigiert. | Vollständiger Lauf mit tatsächlichem Python 3.11.16, zusätzlich 3.12.14. ARM64/Python 3.13.5 des Pi hier nicht ausgeführt. |
| Unbekanntes Netto lässt neue Käufe zu | Ungeklärte Ergebnisquittungen sperren beide Kauf-Risikogates. Schutz und Ausstiege bleiben möglich. Späte, eindeutige Belege werden genau einmal nachgebucht. | Bekannte Werte werden Konto/Umgebung/Währung zugeordnet. Die Sperre gilt konservativ für den betroffenen Brokerrisikotopf; eine neue Dateistruktur mit eigenen Risikotöpfen je Unterkonto wurde nicht eingeführt. |
| Allgemeine News werden OR/NOW zugeordnet | Strukturierte Anbieterkennung, ausdrücklich gekennzeichneter Ticker oder belastbarer Firmenname erforderlich. Allgemeine Wörter begründen keinen Aktienbezug. | Gegenproben OR, NOW, ALL, A, IT; positive Zuordnung mit eindeutiger Kennung. Makroquelle allein erklärt Einzelwert-News nicht für geprüft. |
| KI OFF und Budget sind unwirksam | OFF zentral vor Cache/Anfrage und vor späterer Verwendung prüfen. Budget vor HTTP dauerhaft und über Prozesse hinweg reservieren; Nullgrenze sperrt. | Cache-/Transport-Gegenproben, gleichzeitige Instanzen/Threads und vier unabhängige Prozesse, beschädigte und entfernte Budgetdatei. Reservierte unbekannte Kosten werden nicht voreilig freigegeben. |

## Weitere Verkaufs- und Zuordnungsfehler

| Fall | Umgesetzte Regel |
| --- | --- |
| Schutzstorno vor Reservierung | Dauerhafte `PREPARED`-Reservierung vor jedem Storno; finale Menge und Preis werden vor POST atomar als `SUBMITTING` festgeschrieben. Schreibfehler vor Reservierung lässt Schutz unberührt. |
| Hängende Order / leere Statusantwort | Detailstatus, begrenzte Historie und Archiv anhand exakter Order-/Client-ID abgleichen. Ergebnis und Prüfzeit persistieren. Leere Antwort, Zeitablauf oder gleichbleibendes Guthaben geben keine neue Order frei. Unklare Käufe und Verkäufe sperren symmetrisch konkurrierende Aufträge desselben Instruments. |
| Alte selbst erzeugte manuelle Sperre | Nur nachweislich nie gestartete eigene Anforderung freigeben. Bei alten Anfragen ohne gespeicherte vorherige Verwaltung bleibt MANUELL; eine neue ausdrückliche Verkaufsanfrage ist danach möglich. Keine automatische Rückstellung unbekannter Altverwaltung auf AUTO. |
| Menge 0 / negativ / NaN | Ledger lehnt diese Werte sowie fehlende oder unendliche Mengen ohne Buchung ab. Keine automatische Komplettschließung bei fehlender Fillmenge. |
| DEMO/LIVE / Entry-Replay | Umgebung bei Suche und expliziter Trade-ID prüfen. Fill-Claims und Duplikatschranken erhalten dieselbe Domäne. Geschlossene Entry-Order ohne neue Fillanker wird nicht neu eröffnet. |
| Manueller Verkauf nach Absturz | Bereits belegter Ledgerabschluss darf den offenen Bedienauftrag abschließen. Die zugehörige Wiedereinstiegssperre ist idempotent und verlängert sich durch Wiederholung nicht. |
| Lokale Buchung fehlgeschlagen | `ACCOUNTING_PENDING` hält Auftrag und Position zur Nachklärung fest. Nur Buchhaltung wiederholen, keinen zweiten Verkauf senden. |
| Fehlende Gebühren | Verfügbare OKX-Ausstiegsgebühren ausschließlich aus passenden vollständigen Order-/Fillbelegen ergänzen. Unbekannte Einstiegsgebühren oder historische FX bleiben offen. Brutto wird kein angeblich bestätigtes Netto. |
| Gleichzeitiger erster Datenbankzugriff | WAL-Initialisierung aus 9.8.1 beibehalten; zusätzlich parallele Schemaerweiterung des Ausführungsjournals serialisieren. Keine pauschale Wiederholung von Order-POSTs. |

Eine Orderreservierung und eine Buchungsquittung sind unterschiedliche Zustände. Erst terminaler Brokerstatus plus vollständige Fillbelege und dauerhafte Buchung erlauben den Abschluss. Bei Teilfüllung muss die verbleibende Menge weiterhin geführt und ihr Schutz abgeglichen werden. Hummingbot beschreibt Tracking bereits vor dem API-Aufruf; Freqtrade beschreibt die Pflege von Brokerstopps nach Fills. Diese Abläufe wurden in die vorhandene NEXUS-Struktur integriert. [Hummingbot Order Lifecycle](https://hummingbot.org/connectors/connectors/architecture/order_lifecycle/), [Freqtrade Stoploss](https://www.freqtrade.io/en/stable/stoploss/).

OKX bewahrt stornierte Orders ohne Fills nicht unbegrenzt in allen Historien auf. Daher ist ein fehlender Archiveintrag kein sicherer No-Fill-Nachweis. NEXUS lässt einen solchen ungeklärten Fall gesperrt und nennt den letzten erfolglosen Abgleich. [OKX API-Dokumentation](https://my.okx.com/docs-v5/en/).

## BTC bleibt die erste Abnahme

Der Screenshot belegt Best Bid 79.419, gesendetes Limit 78.624 und OKX-Fehler 51138 mit Verkaufsuntergrenze 79.027. Die eigene Preisgrenze lag außerhalb des Börsenbandes. NEXUS berücksichtigt das aktuelle instrumentgebundene `sellLmt`, rundet tickgenau und prüft Orderbuch/Preisband vor Storno sowie nochmals nach Bestandsfreigabe. Für Käufe gilt entsprechend `buyLmt`. Eigene Preis- und Slippagegrenzen werden nicht gelockert. 79.027 ist ausschließlich ein historischer Testwert.

Eindeutige Preisbandablehnung erhält eine kurze Wiedervorlage, frühestens nach 30 Sekunden und tatsächlich im nächsten regulären Engine-Takt. Unklare Annahme wird davon getrennt. Alte lange Wartezeiten werden nur mit exakt passendem No-Fill-Journalbeleg verkürzt. Käufe bleiben FOK, Verkäufe preisbegrenzte IOC; keine automatische unlimitierte Market-Ersatzorder. Der erste SUI-FOK-Ausfall ist damit nicht nachträglich als identischer BTC-Fehler bewiesen.

## Nachrichtenfoto und Änderungen

Das Foto vermischt einen alten Prüfstand, tatsächlich erreichbare Quellen, Anbieterdrosselung und Kontotarifprobleme. „STALE 10 Stunden“ beweist keinen zehnstündigen Ausfall. Eine erreichbare Quelle muss auch nicht gerade eine neue Meldung veröffentlicht haben.

| Beobachtung | Änderung / notwendige Einordnung |
| --- | --- |
| SEC, Yahoo, Google als STALE | Letzte Prüfung, letzte erfolgreiche Antwort, Meldungszeit und Fehlerzustand getrennt anzeigen. Der Quellencheck prüft aktiv mit Cache und respektiert Pausen. |
| GDELT 429 | Drosselung als Pause darstellen; `Retry-After`, Wartezeit und gemeinsamen Cache berücksichtigen. Kein zusätzliches Dauerschleifen-Polling durch die WebUI. |
| Alpha-Vantage-Fehler nennt Schlüssel | Schlüssel aus Anbietertexten, persistentem Status und historischen WebUI-Analyseausgaben maskieren. Der bereits im alten Foto sichtbare Schlüssel ist dadurch nicht rückwirkend geheim und sollte ersetzt werden. |
| FMP 402 / deaktiviert | FMP und Konfiguration bleiben erhalten. Fehlende Tarifberechtigung verständlich ausweisen; sie lässt sich nicht durch einen Codefix freischalten. Aktivierung/Zugangsdaten bleiben unter Einstellungen steuerbar. |
| MASSIVE funktioniert | Anbieter, Einstellungen und Nachrichtenpfad bleiben erhalten. |
| FMP Symbol Search in Quellenzahl | Referenzsuche als eigene Fähigkeit kennzeichnen; nicht als zusätzliche funktionierende Nachrichtenquelle zählen. |
| Earnings unbekannt | Fehlender Schlüssel oder fehlerhafte Antwort bedeutet Kalender nicht verfügbar. Historische SEC-Daten beweisen keine fehlenden bevorstehenden Termine. |

Neu ist der offizielle **Fed-Monetary-RSS-Feed ohne API-Key**, als zusätzliche Makroquelle. Der Endpoint antwortete hier tatsächlich mit HTTP 200 und XML. Ein älterer letzter Beitrag ist kein Providerfehler; außerhalb des News-Zeitfensters werden null frische Beiträge verwendet. Der Feed ersetzt keine unternehmensbezogene Vollversorgung. [Fed RSS-Angebot](https://www.federalreserve.gov/feeds/feeds.htm), [Monetary RSS](https://www.federalreserve.gov/feeds/press_monetary.xml).

Die bereits vorhandenen Quellen ohne Schlüssel bleiben nutzbar: SEC EDGAR, Nasdaq-Halts sowie die bestehenden Yahoo-/Google-RSS-Wege. SEC verlangt einen geeigneten User-Agent; Nasdaq nennt eine begrenzte Abruffrequenz. Der Nasdaq-Liveabruf lief hier in ein Timeout. Der strukturierte Parser ist mit XML-Testdaten geprüft, eine erfolgreiche reale Nasdaq-Antwort wird nicht behauptet. [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Nasdaq Halt RSS](https://www.nasdaqtrader.com/Trader.aspx?id=TradeHaltRSS).

Weitere Korrekturen: fehlende/ungültige/zukünftige Datumsangaben gelten nicht als frisch; Nasdaq-Halts benötigen eindeutigen Ticker, Halt-/Wiederaufnahmezustand und Zeitbezug. Wiederholte Signalbegriffe sind begrenzt. Kauf-, Underdog-, Verkaufs-, Markt- und Wiederholungsschwelle sind unter erweiterten News-Einstellungen sichtbar und fließen mit ihren tatsächlich verwendeten Werten in den Strategie-Hash ein. Nachrichtenqualität und Klassifikationsfehler lassen sich damit begrenzen, nicht vollständig ausschließen.

## KI und WebUI

Second Opinion hat jetzt einen wirksamen kurzen Gesamtzeitrahmen; höchstens zwei Providerworker dürfen gleichzeitig laufen. Verspätete Antworten dürfen nach OFF beziehungsweise Timeout keine Entscheidung oder gültigen Cacheeintrag mehr erzeugen. Leere oder schemawidrige Antworten werden abgewiesen. Der KI-Testknopf verwendet vorhandene Statusfunktionen und das richtige Kostenfeld. Budgetbuchungen werden über Prozesse gesperrt und atomar geschrieben. Die lokale Kostenreservierung basiert weiterhin auf den eingestellten Schätzpreisen und ist keine vom Anbieter bestätigte Rechnung. Bereits gesendete HTTP-Anfragen lassen sich durch OFF nicht rückwirkend zurückrufen.

Die fünf Hauptseiten bleiben Übersicht, Handel, Analyse, Einstellungen und Logbuch. Kerzenchart und sämtliche bisherigen Einstellungsfunktionen bleiben vorhanden; News ergänzen sechs Felder. Quellen erscheinen als kompakte Statuskarten mit aufklappbaren Details. Für ungeklärte Ausführungen werden Ursache und letzter Abgleich angezeigt. Auch bestätigte offene OKX-Positionen können einen lesenden Belegabgleich anfordern; das berechtigt nicht zum Löschen einer belegten Position.

Die Ergebnisgrafik aus 9.8.1 bleibt: Heute/Gesamt, Geld/Prozent, Tagesbalken/kumulierte Linie und 30/90/365 sichtbare Tage. Jede Gruppe entspricht Broker + Konto + DEMO/LIVE + Währung. Prozent bedeutet bestätigtes Netto geteilt durch das Einstiegskapital derselben bewerteten abgeschlossenen Trades einschließlich zugehöriger Einstiegsgebühr. Einzelprozente werden nicht addiert; die Anzeige ist keine Kontorendite. Fehlende Gebühren/FX erzeugen Teilsummen beziehungsweise Diagrammlücken. Offene Buchgewinne/-verluste bleiben getrennt auf der Handelsseite.

## Ausgeführte Prüfungen

Der gesamte Lauf erfolgt über `volltest.py`: Release-Hashes, isolierte private Quellkopie, keine geerbten Schlüssel, gesperrte HTTP-/Socket-Verbindungen. Die Tests prüfen Release-Hygiene, Abhängigkeiten und WebUI-TestClient, Regressionen, Selbsttest, Kompilierbarkeit, hostneutrales Pi-Preflight sowie Shellsyntax. Der Originalquellstand muss während des Laufs unverändert bleiben.

Der Abschlussnachweis enthält die tatsächlichen Rohprotokolle beider Python-Versionen und des entpackten Pakets. Die Freigabe verlangt **1.728 bestandene Python-Tests und 222 bestandene Untertests pro vollständigem Lauf**, alle sieben Prüfschritte und einen unverletzten Netzwerkguard. Die vorhandene Starlette-/AnyIO-Abkündigungswarnung ist keine Handelsfehlermeldung.

Zusätzlich ausgeführt: 56 JavaScript-Logikprüfungen zur Ergebnisanzeige; UI-Prüfungen für Tabs/Tastatur, Pagination, Text-Escaping, Kerzenchart, überholte Antworten und sichtbare API-Fehler. Breiteneingaben 320/390/768/834/1024/1440 Pixel je nach Test. JavaScript-Syntaxprüfung aller Release-Skripte. Das sind DOM-Testmodelle, keine tatsächliche CSS-/Touchprüfung.

Die neuen Regressionen decken insbesondere ab: manuelles SELL bis Fill und Ledger, Null-/Negativ-/NaN-Menge, falsche Kontodomäne, Umgebung bei Replay, Storno plus Persistenzfehler, Reservierungskonkurrenz, verspätete Ergebnisbelege, fehlende Archiveinträge, alte manuelle Wartezustände, Wiederanlauf ohne doppelte Sperre, normale Wörter als Ticker, Datums-/Haltfehler, Schlüsselmaskierung, Providerpause, leerer Kalender, KI OFF, leere Antwort, Zeitgrenze und atomare Budgets.

## Probe mit Georgs Daten

Die bereitgestellte SQLite-Datei wurde ausschließlich in einer temporären konsistenten Kopie bearbeitet. Dort wurde der tatsächliche manuelle SUI-Verkaufsbeleg angewandt und identisch wiederholt: weiterhin 52 Trades, nur Trade 45 nachgepflegt, alle übrigen 51 unverändert, zwei exakte Fill-Claims, keine Doppelbuchung, SQLite `quick_check` erfolgreich.

Einstieg bleibt USDC, Verkaufswährung EUR. Der native Netto-Verkaufserlös beträgt 84,9180658695 EUR; dieser Betrag ist kein Gewinn. Fehlender historischer USDC/EUR-Wechselkurs bleibt unbekannt. SHA-256 des unveränderten Originals: `d253b134adfc86eb8fdf276eabb54f27025ce640327c500e3da608500957a820`. Keine Ersatzdatenbank wird ausgeliefert, da der Pi seit dem Export weitergehandelt haben kann.

## Offen bleibt die reale Abnahme

- **Broker/Pi:** Hier keine echten OKX-/eToro-Orders, kein ARM64- oder Pi-Python-3.13.5-Test. Paket und Tests werden beim Update auf dem Pi nochmals geprüft. Erst beobachtete DEMO-Fills samt Ledger, Restschutz und Wiederanlauf belegen den dortigen Ablauf.
- **Geräte:** Der Browser blockierte die lokale Testseite mit `ERR_BLOCKED_BY_CLIENT`. Kein bestätigter visueller Safari-/iPhone-/iPad-/PC-Browserlauf. Responsive Regeln und JS sind vorhanden und logisch geprüft; echte Hoch-/Querformat- und Touchabnahme bleibt erforderlich.
- **Historie:** Fehlende FX, Einstiegsgebühren, uneindeutige Registry-Aliase oder abgelaufene Brokerbelege sind nicht durch Schätzung reparierbar. Sie können neue Käufe bewusst sperren. Die SUI-Nachpflege bleibt ein ausdrücklich anzuwendender eigener Vorgang.
- **Anbieter:** FMP-Tarifberechtigung, Providerquoten und die laufende Erreichbarkeit externer Feeds bleiben anbieterabhängig. Eine erfolgreiche Fed-Antwort ist keine Verfügbarkeitsgarantie.

Für das direkte Update gilt `INSTALLATIONSANLEITUNG_NEXUS_9.8.2_DE.md`. 9.8.1 nicht zuerst installieren. Historische Berichte im Paket dokumentieren frühere Stände und ersetzen diesen Bericht nicht.
