# Aktueller Stand: NEXUS 10.1.5

Siehe `INSTALLATIONSANLEITUNG_NEXUS_10.1.5_DE.md` und `docs/NEXUS_10.1.5_Aenderungen.md`. Die folgenden Abschnitte dokumentieren frühere Versionen.

# NEXUS 10.1.4 – Umsetzung und Gegenprüfung

Stand: 14. September 2026. Grundlage ist die lokale Quellfassung 10.1.3. Diese Version korrigiert wichtige Befunde und führt X als automatische Kandidatenquelle für PULSAR ein. Es wurden keine Brokerorders, kostenpflichtigen Anbieterabrufe oder Telegram-Nachrichten ausgelöst.

## 1. PEP: was belegt ist und was der Befund übersieht

Die Prüfung des Berichts `Befund_PEP_Kaufsperre_10_1_3.md` bestätigt die fehlende eigenständige Verarbeitung des Broker-Historienergebnisses und die Lücke beim Auffinden nativer Abschlusskosten. Seine vorgeschlagene Freigabe allein durch die Rechnung `Kursdifferenz minus fees = netProfit` ist jedoch nicht ausreichend.

| Beleg | Nachgewiesener Inhalt |
|---|---|
| PEP-Historie, Position 3597440106 | 109 Einheiten, Einstieg 136,35 USD, Abschluss 138,59 USD am 14.09.2026 um 13:32:26.867 UTC |
| Historienfelder | `fees = 0`, `netProfit = 244.16` |
| Tatsächlicher v2-Einstiegsbeleg, Order 380127623 | Gebühren 1 USD, Steuern 0 USD, Gesamtkosten 1 USD |
| Alter Sonntagsauftrag 380995258 | Darf nicht als tatsächliche Montags-TP-Ausführung verbucht werden |

Die reine Kursdifferenz ergibt bereits 244,16 USD. Das gleich hohe als `netProfit` bezeichnete Historienfeld belegt somit nicht, dass die separat bestätigten Einstiegskosten vollständig darin enthalten sind. Zwei zusammenpassende Felder einer Antwort sind keine zwei unabhängigen Quellen. Der neue Replay-Test mit Originalbelegen ergibt **BROKER_HISTORY_COST_SCOPE_CONFLICT** und erhält die bekannten 1 USD Einstiegskosten unverändert.

Umgesetzt sind ein eigener Broker-Historienbeleg mit Konto-/Umgebungs-/Positionsbindung, Prüfsumme und eigenständigen Ergebnisfeldern sowie die Anzeige neben dem NEXUS-Netto. Vorhandene vollständige Historien werden beim regulären Abgleich ausgewertet. Eine rechnerische Übereinstimmung allein erzeugt keine Kostenfreigabe. Die echte Abschlussorder kann künftig über einen dauerhaft gespeicherten privaten Schließhinweis gefunden werden. Erst ein exakt passender, vollständig ausgeführter v2-REST-Beleg mit Kostenumfang kann die Abschlusskosten bestätigen. Die exakt belegte native Abschluss-ID wird atomar am Trade und in einem eigenen Auditbeleg gespeichert; abweichende vorhandene IDs werden nicht überschrieben. Ein Gegenbeispieltest mit bekannten Einstiegskosten 1 USD und Abschlusskosten 1,20 USD ergibt aus 20 USD brutto genau 17,80 USD netto.

**Nicht als erledigt ausgegeben:** Eine endgültige Aufhebung der aktuellen PEP-Kaufsperre ist mit den vorliegenden Kostenbelegen nicht nachgewiesen. Der tatsächliche Abschluss ist belegt; der Umfang der Abschlussabrechnung bleibt offen. Die Diagnose nennt die konkrete Lücke, statt pauschal sämtliche Belege als fehlend zu behandeln. Eine neue Risikoperiode, erfundene Nullgebühren oder die Wiederholung des Sonntagsauftrags wären keine sachgerechte Reparatur.

Die Originalprüfung verwendet das vorliegende Abschlussbelegpaket vom 14.09.2026, 17:55 UTC und die zuvor bereitgestellte Nachmittagshistorie. Der spätere 19:46-Export wird in dieser Gegenprüfung nicht als erneut eingelesene Originalquelle ausgegeben.

## 2. eToro-Kommunikation

REST-Abfragen aller lokalen Nutzer desselben authentifizierten Zugangs teilen persistente Abruffenster: konservativ 54 Lesezugriffe, 18 Ausführungs-, 18 Handelbarkeits- und 18 Kostenabfragen pro Minute. Jeder tatsächliche Versuch reserviert Kapazität. `Retry-After` wird als Sekundenwert oder HTTP-Datum vollständig berücksichtigt. Ein länger wartender Aufruf darf mit einer Fehlermeldung enden; er darf die Sperrfrist nicht verkürzen. Schreibende Order-POSTs werden nicht automatisch wiederholt.

Private WebSocket-Ereignisse werden vor der Zuordnung bereinigt, kontogebunden und dauerhaft gespeichert. Unpassende Konto-/Umgebungsdaten werden ausgeschlossen. Noch nicht zuordenbare Hinweise können nach einem Neustart erneut geprüft werden. Eingang/Authentifizierung allein buchen keine Verkaufsmenge. Historie, tatsächliche Ausführung und Kosten müssen weiterhin zusammenpassen. Fehler eines optionalen Ereignisspeichers verhindern den regulären REST-Abgleich nicht.

Die bisherige Prüfung instrumentbezogener Handelbarkeit und frischer Brokerkurse bleibt bestehen. Es gibt keine pauschale Beschränkung der Schutzüberwachung auf US-Kernzeiten und keinen erfundenen Wochenendgebührenfaktor. Broker-SL/TP werden durch dieses Update nicht verschoben. Ein nativer TP kann unabhängig von NEXUS auslösen; ein Mindestnettogewinn wird dadurch nicht garantiert.

## 3. Automatische X-Kandidatensuche

Der Nutzer muss keine Aktien auswählen. Drei begrenzte Suchbereiche wechseln nach UTC-Zeit:

| Zeitfenster | Suche | Zweck |
|---|---|---|
| 00–08 UTC | `realDonaldTrump`, `WhiteHouse`, `POTUS`, optionale Ergänzungen | Politische Aussagen und mögliche Marktthemen |
| 08–16 UTC | `USTreasury`, `federalreserve`, `SECGov` | Finanzpolitik, Aufsicht, regulatorische Anlässe |
| 16–24 UTC | Offene Suche nach Umsatz-/Gewinn-/Prognose-, Zulassungs-, Übernahme- und Auftragshinweisen | Neue Aktien außerhalb des bisherigen Universums entdecken |

Jeder Bereich wird höchstens einmal täglich abgefragt, mit maximal zehn Posts. Die Konten sind ausgewählte Recherchequellen; ihre Inhalte erhalten keine automatische Wahrheitsfreigabe. Quellenrollen und öffentliche Auswahlbelege stehen in der WebUI. Es handelt sich um Suchstichproben, nicht um vollständiges Einlesen jeder Timeline.

Explizite Cashtags liefern neue, zunächst ungeprüfte Aktienhinweise. Spam, Kopien und veraltete Einträge werden gefiltert. Regierungsposts ohne Ticker liefern Themenhinweise; es werden daraus keine Aktienzuordnungen erfunden. Erst ein frisches, eindeutig passendes typisiertes FMP-Einzelaktienprofil berechtigt einen Fund zur automatischen Tageszählung. Zwölf ungeprüfte Spam-Ticker können deshalb nicht das Beobachtungsbudget belegen.

PULSAR übernimmt die neuen Hinweise vor seiner bestehenden Kandidatenprüfung. Fünf bestehende Rechercheplätze werden grundsätzlich als drei Reddit- und zwei X-Plätze verteilt; freie Plätze können geteilt werden. Gemeinsame Ticker werden zusammengeführt. FMP/Finanzberichte, belastbare Primäranlässe, Kurs-/Liquiditätsprüfung und die bestehenden KI- und Freigabeschritte bleiben maßgeblich. Ausschließlich auf X beruhende KI-Ablehnungen oder Bevorzugungen werden als Beobachtung behandelt; das ursprüngliche Urteil bleibt dokumentiert.

X erweitert damit die Kandidatenbasis von PULSAR. Es schreibt keine ungeprüften Aktien direkt ins Handelsuniversum. Für NEXUS bleiben makroökonomische und Krisenhinweise Recherchekontext; **X allein löst weder Kauf, Verkauf noch Krisensperre aus**. Eine darüber hinausgehende automatische Zuordnung politischer Aussagen zu Branchen/Aktien und ein eigenständiger X-Krisenhandel sind nicht umgesetzt.

## 4. Zusammenspiel der Quellen

| Quelle | Rolle | Grenze |
|---|---|---|
| X | Frühzeitige Kandidaten, Themen und gemessene Aufmerksamkeit | Kein alleiniger Handels-/Krisenentscheider; Stichprobe ist keine Vollzählung |
| Reddit-Aggregatoren | Ergänzende Aufmerksamkeit und Kandidaten | Überlappende Toplisten, keine unabhängigen vollständigen Autorenbelege |
| FMP | Eindeutige Aktienidentität, Finanzdaten und Nachrichtenhinweise | Profil allein bestätigt keinen Kursanlass |
| Nachrichten/Originalmeldungen | Wirtschaftlichen Anlass und Risiken prüfen | Gleicher Artikel über zwei Anbieter ist keine zweite Quelle |
| Broker/Kurs-/Volumendaten | Handelbarkeit, frische Preise, Risiko- und Ausführungsbelege | Verbindungsstatus allein beweist keine korrekte Buchhaltung |
| Bestehende GPT-Prüfungen | Strukturierte Prüfung innerhalb vorhandener Budgets | Kein Ersatz für fehlende belegte Daten |

Artikel werden nach bereinigter URL und Text zusammengeführt. Die globale Nachrichten-Krisenpause benötigt mehrere frische Veröffentlichungsursprünge mit überlappender Risikokategorie. Ein einzelner Hinweis bleibt sichtbar, ohne allein die globale Pause auszulösen. Dies ist eine vorsorgliche Mehrquellen-Risikoprüfung, keine Behauptung eines verifizierten gemeinsamen Ereignisses. Bekannte X-Reposts können über andere Anbieter keine zweite Bestätigung vortäuschen. Kurs-SL und unabhängige Börsen-Handelsaussetzungen bleiben davon getrennt.

## 5. Gegenprüfung des X-Prüfberichts

| Befund | Umsetzung 10.1.4 |
|---|---|
| B1: Zensierte Toplistentage unsichtbar | Gemessene, in beobachteter Teilmenge fehlende und unbekannte Tage in PULSAR/WebUI/Diagnose sichtbar |
| B1: Fehlende Tage als Basis? | Keine erfundenen Nullwerte; eine eigene statistische Methode für zensierte Werte wurde nicht stillschweigend eingeführt |
| B2: Unbemerkter Normierungsverlust bei Listenwechsel | Hinweis vor dem Speichern, Beginn/Grund/Prüfsumme der neuen Vergleichsgruppe sichtbar; stabile automatische Gruppe |
| B3: Unvollständige Kontrollreihe | Eigene Normierungszustände und Anzahl passender Vergleichstage; Rohzählung bleibt getrennt |
| B4: Symbolzählung größer als Kontrollzählung | Eigener Inkonsistenzzustand statt unspezifischem Leerwert |
| B5: Aufbauzeit | Kandidaten werden schon im Aufbau recherchiert; 28-Tage-Normierung benötigt weiterhin reale vollständige Daten |
| B6: Konservative Suchkosten | Unverändert: zehn Posts reserviert, auch wenn weniger eintreffen; ausdrücklich keine Anbieterrechnung |
| C: API lässt stille Tage weg | Fehlende Buckets und wiederholte unvollständige Abrufe werden als Abdeckungsproblem angezeigt; fehlend bleibt unbekannt |

Eine eigenständige PULSAR-Zählbasis für X kann nach mindestens 14 vollständigen vorherigen Tagen derselben Abfrage und passenden Werktag-/Wochenendvergleichen verwendet werden. Das ist von der strengeren 28-Tage-Marktnormierung getrennt. Eine Stichprobe von drei Posts wird nicht zu drei vollständigen Tageserwähnungen umgedeutet.

## 6. Kosten und Grenzen

Die veröffentlichte X-Preisgrundlage nennt 0,005 USD pro gelesenem Post und 0,005 USD pro Recent-Counts-Abfrage. Der Sammler reserviert konservativ 0,0065 EUR je Einheit (USD/EUR gleichgesetzt plus 30 % Puffer). Bei höchstens 13 Zählabfragen und drei Zehn-Post-Suchen täglich ergibt das für 31 Tage **8,6645 EUR**. Das lokale Monatslimit bleibt maximal **15 EUR**. Reservierungen, Fehler und Parallelabrufe sind dauerhaft begrenzt; Preisbestätigung verfällt nach 30 Tagen. Die Anbieterrechnung und andere Programme sind nicht darin enthalten. Im X-Portal ist zusätzlich ein passendes Ausgabenlimit möglich. Quelle: [X-Preise und Ausgabenlimits](https://docs.x.com/x-api/getting-started/pricing), geprüft am 14.09.2026.

Neue X-Kandidaten nutzen die bereits budgetierten GPT-Rechercheplätze. Es gibt keine zusätzliche regelmäßige Luna-Websuchserie und keine Erhöhung der GPT-Budgets. Eine politische Meldung ohne Cashtag wird aus Kostengründen auch nicht durch einen zusätzlichen Modellaufruf in spekulative Aktiennamen übersetzt.

## 7. WebUI, Diagnose und Installation

**Quellen & X** zeigt den automatischen Plan, Konten, gefundene/verarbeitete/recherchierte Kandidaten, empfangene Daten, Kostenreservierungen, Tageslücken und Normierungswechsel. **PULSAR** zeigt Stichprobe versus Tagesmessung und die Rollen/Freigabelücken seiner Quellen. **Handel** zeigt Broker-Historienergebnis und vollständig abgerechnetes NEXUS-Ergebnis getrennt.

Die Diagnose 1.5.0 integriert dieselben Belege sowie begrenzte kontogebundene Stream-Metadaten. Sie liest passiv, exportiert keine X-Rohtexte oder privaten Stream-Inhalte und verursacht keine zusätzlichen Anbieterabrufe. Sofort-/30-Minuten-Modus, lokale ZIP und der getrennte Telegram-Versand bleiben bestehen.

Installation und ausführbare Diagnosebefehle stehen in `INSTALLATIONSANLEITUNG_NEXUS_10.1.4_DE.md`. Der Testbericht dokumentiert die abschließenden Prüfergebnisse. Die Oberfläche konnte in dieser Umgebung nicht visuell über den Cloud-Browser abgenommen werden: Der Zugriff auf die lokale Testseite wurde blockiert. Die tatsächlichen JavaScript-Renderer, HTML-Schnittstellen und geschützten WebUI-Endpunkte werden durch lokale Regressionen geprüft. Keine Installation oder Live-Abnahme auf dem Raspberry Pi wird behauptet.
