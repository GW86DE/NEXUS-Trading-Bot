# NEXUS 9.8.7 — Befundabgleich und Prüfbericht

Stand: 11. September 2026. Basis: **das tatsächliche 9.8.6-Release**, nicht 9.7.5. Regelstand PULSAR 1.2.

Basis-ZIP SHA-256: `92d4bbdd222cae70d143aa8402d6ae05047a834a20a8d85cb885648af79b3233`.

## 1. Ergebnis und Freigabegrenze

Die Änderungen sind implementiert und in isolierten Kopien geprüft. Das neue ZIP und der selbstenthaltende Installer enthalten denselben Quellstand. Der gesicherte vorhandene Updateweg bleibt erhalten. **Dies ist keine Bestätigung, dass Georgs aktuelle 19 Klärungsfälle bereits aufgelöst, alle Schutzorders heute aktiv oder alle Quellen auf dem Pi erreichbar sind.** Es wurden keine Orders, Schutzänderungen oder Nachrichten an echte Broker-/Telegram-Konten gesendet.

Die aktuelle private Entscheidungsdatenbank, aktuelle Brokerantworten und Pi-Dienstzustände standen für diesen Build nicht zur Verfügung. Die vorhandenen Übergaben liefern historische Fallbelege, keine heutige Kontoinventur. Neue Tests verwenden gekennzeichnete synthetische Belege und echte lokale SQLite-Transaktionen. Die lokalen Testkopien wurden nicht als reparierte produktive Kontodaten ausgeliefert.

## 2. Was an den beiden Befunden zutrifft

| Aussage aus den Unterlagen | Prüfung am tatsächlichen Code / Konsequenz |
|---|---|
| eToro-Kursbedingung wird durch mindestens 30 Kerzen grün und verfällt nicht | Bestätigt. Nicht nur ein Textproblem: alte Kerzen konnten einen dauerhaft grünen Aktualitätsstatus erzeugen. Jetzt getrennte Sitzung, echte Quote mit Ablaufzeit und frische Signalhistorie. |
| Um 12 Uhr ist wahrscheinlich nur die US-Börse geschlossen | Plausible Erklärung des Screenshots, kein Beweis des aktuellen Pi-Zustands. Andere Buchungs-/Risikosperren bleiben separat wirksam. Kein Versprechen automatischer Entsperrung um 15:30 Uhr. |
| Die offene Aktie dürfte manueller Bestand ohne Position-ID sein | Nicht durch die Bilder belegt. Die jüngere Übergabe beschreibt PEP mit belegtem BOT-Eigentum und konkreter Position-/Order-ID, aber offener Schutzbestätigung. Diese Ebenen bleiben getrennt. |
| Für OKX existiert überhaupt kein Ergebnisnachlauf | Zu pauschal: `risk_result_recovery._enrich_okx` enthält bereits einen begrenzten Gebührenpfad. Ein vollständiger Nachlauf für bereits geschlossene, preislose BOT-/Algo-Abschlüsse fehlte aber. Dafür gibt es jetzt einen eigenen Pfad. |
| DOGE wurde sicher durch die bestätigte Schutzorder verkauft | Nicht belegt. Eine bestätigte Schutz-Algo-ID beweist deren Annahme/Rücklesen, nicht ihre Ausführung. Der neue Pfad verlangt konkrete primäre Kindorders und deren Fills. |
| Die 19 Fälle entsprechen sechs eToro- und ungefähr 13 OKX-Fällen | Nicht als aktuelle Aufteilung bewiesen. Historische sechs eToro-Gebührenlücken und ein heutiger Gesamtzähler sind keine vollständige Einzelinventur. Der lokale Read-only-Export kann den aktuellen Bestand erfassen. |
| Der Auffangtext beweist, dass DOGE nur der Preis und keine Gebühr fehlt | Zu stark. Der Text ist kein unabhängiger Finanzbeleg; mehrere Qualitätsfelder und fehlende Nachweise müssen geprüft werden. Die Anzeige nennt jetzt die konkreten Lücken und den letzten Ergebnisabruf. |
| Einfach den bestehenden `_trade_close` für geschlossene Zeilen aufrufen | Nicht ausreichend: dessen geschlossene-Replay-Verträge dürfen nicht umgangen werden. Neue Nachpflege mit eigener atomarer Prüfung, Original-/Belegjournal, konkurrierender Zustandsprüfung und exklusiven Fill-Claims. |
| `ordId` fehlt beim Fill-Abruf; allgemeine Historienvollständigkeit kann eine vollständige Order blockieren | Durch Gegenproben bestätigt. Serverseitiger Filter, lokale Identitätsprüfung, positive Ordervollständigkeit und negative No-Fill-Evidenz werden getrennt. Die historische Stunde DOGE-Verzögerung ist damit nicht rückwirkend ursächlich bewiesen. |
| Schutzstorno verwendet nicht konsistent das konkrete Algo-Instrument | Bestätigt; exakte Algo-ID, Client-Widersprüche und Instrument aus der Brokerzeile werden zusammen geprüft. Keine blinde Marktumbenennung. |
| PULSAR kann neue Aktien strukturell nie entdecken/nominieren | Entdecken und recherchieren waren bereits ohne 14 Tage möglich. Nominierung ohne Aufmerksamkeit ist mit höchstens 75 statt 80 Punkten ausgeschlossen; ausreichende Historie kann später entstehen. Jetzt klar getrennte Stufen und ein begrenzter Cold-Start-Rechercheplatz. |
| Nicht in einer Topliste bedeutet nachweislich still | Ohne nachgewiesene damalige Instrumentabdeckung falsch. Kein ungeprüfter Abwesenheits-Patch, keine erfundenen 19 früheren Tage. Quellenfilter und tatsächliche Messzeitpunkte bleiben getrennt. |
| Community-Anzeige und unbegrenzte Rohdatenhistorie sind problematisch | Bestätigt. Community erhält drei Zustände; Rohbeobachtungen werden begrenzt und verdichtet. Langfristige Audits und Budgets bleiben erhalten, daher keine garantierte konstante Gesamtdateigröße. |
| Nur Reddit selbst liefert geeignete Einzeldaten | Als Marktbehauptung nicht belegt. Es wird kein Anbieterabo abgeschlossen und kein unzugänglicher Rohdatenadapter als produktiv eingebaut ausgegeben. |

## 3. Umgesetzte Handelskorrekturen

### Kurs- und Marktstatus

`stock_readiness.py` unterscheidet US-Regelhandel, echte Brokerquote und Signalhistorie. Die zeitlich begrenzte Quote wird unabhängig von einem BUY-Signal geprüft. Ein ausreichend langer DataFrame allein setzt keine Quote mehr auf aktuell. Fehlende/naive/unsortierte Zeitindizes, ungültige Schlusskurse und zu alte oder noch nicht abgeschlossene letzte Kerzen werden abgewiesen. Geschlossener Markt erzeugt einen verständlichen Wartestatus, aber verdeckt weder Operator-Pause noch Moduskonflikt oder weitere offene Bedingungen.

### OKX-Belegabruf und Identität

Aktuelle und archivierte Fill-Abfragen senden `ordId`. Archive bleiben auf 20 Seiten begrenzt; wiederholte volle Seiten zählen nicht als vollständiger negativer Nachweis. Widersprüchliche Doppelbelege werden bereits innerhalb der Archivsammlung erkannt, nicht erst nach einer verlustbehafteten Deduplizierung. Das tatsächliche `fillTime` hat Vorrang vor dem späteren Bereitstellungszeitpunkt `ts`.

Eine exakt identifizierte terminale Order mit vollständiger Menge und Gebühren kann positiv abgeschlossen werden, ohne dass die gesamte übrige Instrumenthistorie vollständig gelesen wurde. Umgekehrt bleiben fehlende oder unvollständige History-Antworten ungeeignet, einen Nichtkauf oder eine Nullausführung zu beweisen. Konto, Umgebung und Abrechnung bleiben unverändert gebunden.

### Geschlossene Ergebnisnachpflege

`okx_closed_reconciliation.py` arbeitet mit einer getrennten GET-only-HTTP-Sitzung und gemeinsamen Rate-Limitern. Ein fälliger Fall pro Lauf, höchstens ein Thread je Konto/Umgebung, frühestens nach 60 Sekunden neu; 24 direkte Anfragen und ein 60-Sekunden-Lesebudget begrenzen die Recherche. Einzelne laufende HTTP-Operationen können bis zu ihrem Timeout benötigen; dies ist keine harte Echtzeitgarantie. Der Positions-/Schutzzyklus wartet nicht auf diesen Thread.

Der Leser prüft primäre Kauf- und Verkaufsorders, gegebenenfalls exakt nachgewiesene Algo-Kinder, vollständige Fillmengen, Einstand, Gebühren, Ausführungszeiten und Währung. Nur eine ungeteilte belegte Einstiegskette wird automatisch nachgetragen. Teilallokationen, native Fremdwährungsabschlüsse, fehlende Gebühren und widersprüchliche Werte bleiben offen. Ein Rundungsrest unterhalb der Losgröße erhält seine verbleibende Kostenbasis im Beleg, wird aber nicht als zusätzlicher Verkauf oder Verlust gebucht.

Originalzeile, Hash, vollständiger Nachbeleg, exklusive Fill-Claims, Ereignisse und bestehende Finanzzeile werden in einer SQLite-Transaktion geschrieben. Konkurrierende Änderungen oder fremde Fillansprüche brechen ab. Derselbe Beleg ist bei Wiederholung wirkungslos. `TRADE_CLOSED` bezeichnet den lokalen Abschluss, nicht die behauptete Stornierung jeder übrigen Broker-Algoorder.

Bereits als UNKNOWN vorgemerkte Risikoergebnisse werden anhand des bestätigten Nachbelegs auf dem richtigen Ausführungstag vervollständigt. Ein verspäteter gestriger Abschluss wird nicht als heutiger Gewinn oder zusätzlicher Trade gezählt. Andere Risikodomänen und Währungen werden nicht zusammengeführt.

## 4. PULSAR 1.2

Unbekannte Vortagszahlen sind kein gemessener Anstieg. Ein Kandidat mit ausreichend großer aktueller Stichprobe kann einen der fünf Rechercheplätze erhalten, ohne einen historischen Wachstumsschub vorzutäuschen. Abgeleitetes Wachstum alter Caches wird aus den tatsächlichen Zählwerten neu berechnet. Es gibt keinen sechsten Kandidaten und keine zusätzliche Handelsfreigabe.

Die 14 tatsächlich beobachteten Vergleichstage, 80 Punkte, Originalereignis, Gegenprüfung, persönliche Bestätigungen und Core-Regeln bleiben für Nominierung/Handel bestehen. Die neue Version beseitigt fehlende Geschichte nicht durch eine Regelumgehung. Alte 1.1-Bewertungen zählen nicht als neue 1.2-Bestätigung. Wochenverbräuche, Budgets und bestehende Trades bleiben erhalten.

Die Baseline nutzt je Quelle/Filter die letzte tatsächliche Stichprobe jeder UTC-Stunde, daraus den Median beobachteter Stunden pro UTC-Datum und anschließend den Median dieser Datumswerte. Fehlende Stunden oder Tage bleiben unbekannt. **Die Auswahlverzerrung einer Topliste ist damit nicht verschwunden:** Ruhige, nicht gelieferte Instrumente lassen sich ohne zusätzlichen Abdeckungsbeleg nicht rückwirkend messen. Diese Grenze wird angezeigt.

Rohbeobachtungen bleiben sieben Tage; kompakte Stichproben 35 Tage. Die laufende Historie wird in begrenzten Transaktionen verdichtet; vorhandene echte Rohdaten bleiben während der Umstellung auswertbar. Fehlerhafte noch relevante Messungen werden nicht heimlich zu Null oder gültigen Tagen. Abgelaufene Cacheeinträge werden begrenzt bereinigt. Keine automatische Löschung von Assessments, Kostenreservierungen, Freigaben, Kauf-/Verkaufsjournalen oder Risikodaten; kein laufendes VACUUM und keine Zusage sofortiger physischer SQLite-Verkleinerung.

Community wird als „nicht prüfbar“, „geprüft, Kriterien verfehlt“ oder „Messkriterien erfüllt“ dargestellt. Aggregatzahlen und SEC-Belege attestieren keine Autoren. Selbst erfüllte Account-Kriterien beweisen keine unabhängigen Menschen oder organische Aufmerksamkeit. Ein neuer genehmigter Rohdatenadapter ist nicht Teil dieses Releases.

Zusätzlich wurde ein konkreter SEC-Fehler korrigiert: Die Einheit muss aus der tatsächlich ausgewählten Datenreihe stammen, nicht aus der ersten Einheit des Faktentyps. CIK und Symbolzuordnung müssen passen; Gewinn und Cashflow benötigen dieselbe aktuelle USD-Berichtsperiode. Auch USD-Kurs-/Umsatzschwellen gelten nur für ein passend zugeordnetes USD-Profil. Alte SEC-Caches werden anhand der neuen Schemaversion erneuert.

Der bestehende begrenzte GPT-Web-/Originalquellenpfad bleibt verfügbar, ohne von einer Reddit-Genehmigung abhängig zu sein. Recherchephasen sind sichtbar. Lange Quellenaufrufe werden nicht durch einen erfundenen Freigabe-Heartbeat verdeckt. Bezahlte Modellaufrufe und tatsächliche Zugangsrechte wurden hier nicht live getestet.

## 5. Tatsächliche Prüfungen

| Prüfung | Tatsächliches Ergebnis |
|---|---|
| Unveränderte Basis 9.8.6 | 1.848 Tests + 225 Untertests; alle sieben Gruppen bestanden |
| Neue Broker-Gegenproben an 9.8.6 | 7 fehlgeschlagen, 2 bestanden; gezielte alte Fehler reproduziert |
| Neuer vollständiger 9.8.7-Quellstand | **1.937 Tests + 228 Untertests**, keine fehlgeschlagenen oder übersprungenen Tests; alle sieben Gruppen bestanden |
| Mehrumfang gegenüber dem Basislauf | 89 zusätzliche Haupttests und 3 zusätzliche Untertests |
| Finale ZIP-Wiederholung | Derselbe Gesamtvertrag wird nach erneutem Entpacken geprüft; verbindliches vollständiges Log im Prüfnachweis-Paket: `volltest_final_zip.log` |
| Releaseumfang | 625 manifestierte Quell-/Test-/Dokumentationsdateien, dazu das Manifest; keine Nutzerzustände im ZIP |
| WebUI | 24 DOM-Layoutfälle bestanden; dazu echte lokale HTTP-Anmeldung, 401 ohne Anmeldung und gelesene API-Testdaten. Keine JavaScript-Seitenfehler und kein horizontaler Seitenüberlauf in diesen Fällen |

Der vollständige Quelllauf dauerte 75,8 Sekunden. Laufzeit und Paketprüfung des endgültigen ZIP/SH stehen zusätzlich in den beigefügten Originalprotokollen. Die unten genannten Grenzen gelten auch bei grünen Prüfgruppen.

**Umgebung:** Linux x86_64, Python 3.13.5. Die Release-Pins wurden nicht verändert. Hier waren yfinance nicht installiert und FastAPI 0.128.2, Uvicorn 0.48.0, python-multipart 0.0.29 sowie websocket-client 1.9.0 vorhanden, abweichend von den Release-Pins. pandas/numpy/scikit-learn/joblib/requests entsprachen den Pins. Alle tatsächlichen Versionen stehen in `environment.json`. Die Prüfung ersetzt deshalb keine frische Installation sämtlicher Pins auf ARM64. Kein Fake-yfinance und kein Ersatzdatenfeed wurden eingesetzt.

Die sieben Gruppen umfassen Quell-/Release-Hygiene, Testabhängigkeiten und echten ASGI-TestClient, Regressionen/Geldpfad, Selbsttest, vollständige Python-Kompilation, host-neutralen Pi-Preflight und Syntax sämtlicher Shellskripte. Tests laufen mit privatem HOME/Zustand, ohne geerbte Zugangsdaten und mit Netzsperre.

Zusätzliche UI-Prüfung: echte lokale HTTP-Anmeldung und API-Antworten aus dem WebUI-Backend; Chromium-DOM-Prüfung von acht Seiten bei 390, 1024 und 1440 Pixeln, also 24 Layoutfälle. Fünf PULSAR-Karten, unbekannte Community-Breite, Klärungsdetails, keine Verkaufsaktion am geschlossenen DOGE-Testfall und horizontale Überläufe wurden geprüft. Die Oberfläche wurde visuell an ausgewählten mobilen Screenshots kontrolliert.

**UI-Grenze:** Die lokale Chromium-Verwaltungsrichtlinie blockiert direkte Browsernavigation zum Loopback-Server. Sie wurde nicht umgangen. Für die DOM-Prüfung wurden HTML/CSS/JavaScript inline geladen, der Seitenpfad im Prüfharness simuliert und GET-Aufrufe über eine Python-Brücke an das echte lokale HTTP-Backend vermittelt. Das ist keine vollständige Browser-Netzwerk-E2E- oder Safari-Abnahme. Der erste blockierte Versuch und die erfolgreiche begrenzte Prüfung sind getrennt dokumentiert.

**Quellen-Grenze:** Ein tatsächlicher öffentlicher ApeWisdom-Abruf aus dieser Python-Umgebung scheiterte an DNS-Auflösung; die anschließende gespeicherte Abrufpause wurde korrekt wirksam. Das diagnostiziert nicht Georgs Pi. Die öffentliche JSON-Struktur konnte separat per Webzugriff gelesen werden; es ersetzt keinen erfolgreichen kompletten Providerlauf mit den Pi-Zugängen. Keine Aussage „alle Quellen live erfolgreich“.

## 6. Regressionen und Testvertragsänderungen

Die unveränderte 9.8.6-Basis wurde erneut vollständig geprüft. Gezielte neue Broker-Gegenproben scheiterten dort siebenmal und bestanden zweimal. Das zeigt: Ein grüner alter Gesamttest allein deckte diese Lücken nicht ab.

Neue Tests prüfen unter anderem Orderfilter, positive/negative History-Evidenz, Seitenwiederholungen, widersprüchliche Fills, Algo-/Stornoidentität, echte SQLite-Nachpflege und Rollback, konkurrierende Änderungen, Gebühren/Rebates/Basisgebühren, Restkosten, richtige Ausführungstage, getrennte HTTP-Sitzungen, Read-only-/Requestbudgets, Quote-TTL, Börsenschluss, Cold-Start, historische Verdichtung, Community-Zustände, USD-/CIK-/Periodenregeln, alte Wachstums-Caches und WAL-lesende Diagnose.

Bestehende Tests wurden nicht entfernt, deaktiviert oder mit xfail kaschiert. Transport-Doubles akzeptieren jetzt den zusätzlichen `ord_id`-Filter; ein Algo-Double liefert die angefragte statt einer widersprechenden anderen ID. Positive Finanzfixtures enthalten explizite USD-Einheit, CIK und passende Berichtsperioden. Versionsassertionen wurden auf 9.8.7 angepasst. Neue Gegenfälle prüfen die jeweils strengeren Verträge. Die Änderungen sind im Quell-Diff sichtbar.

Zwischenläufe waren nicht alle grün: ältere Doubles passten noch nicht zum Orderfilter, die PULSAR-Steuerung trug zunächst noch Regel 1.1 und der zuerst gepunktete Name des neuen Diagnoseskripts war nicht regulär importierbar. Diese Ursachen wurden korrigiert und vollständig nachgeprüft. Auch ein zeitabhängiges neues Community-Fixture wurde auf einen tatsächlich vergangenen Messzeitpunkt korrigiert. Die finale Importprüfung bleibt vollständig aktiv. Beim ersten entpackten ZIP-Lauf wurde zudem ein bestehender zeitabhängiger 9.8.1-Test gefunden: Sein angeblich 60 Sekunden zukünftiger Preisband-Zeitstempel wurde bereits bei pytest-Collection erzeugt und war bei Ausführung nach über 60 Sekunden nicht mehr zukünftig. Das Fixture erzeugt den Zukunftswert jetzt unmittelbar im Test; die unveränderte Produktionsprüfung und die Ablehnungserwartung bleiben erhalten. Der fehlgeschlagene Lauf ist als `volltest_zip_before_time_fixture_fix.log` dokumentiert; danach wurde das ZIP neu gebaut und vollständig erneut geprüft.

## 7. Verbleibende externe oder bewusst nicht automatisierte Fälle

Die historischen sechs eToro-Abschlüsse ADBE/CRM/JPM/KO/TXN/AMD benötigen weiterhin passende Gebührenbelege, soweit sie im aktuellen Konto noch fehlen. Bestehende eToro-Backfills wurden nicht durch erfundene Nullgebühren ersetzt. PEP-Eigentum und Schutzbestätigung bleiben getrennt.

Die ältere DOGE-51634-Supportantwort und der spätere bestätigte Schutzauftrag wurden berücksichtigt. Keine pauschale USD-Sperre oder ungeprüfte Umbenennung auf USDC/EUR. Die genaue damalige Ablehnungsursache und einstündige Übernahmeverzögerung sind durch diesen Build nicht rückwirkend bewiesen.

SUI-Verkauf in EUR: nativer Erlös ist nicht gleich Gewinn in USDC. Ohne historischen FX-Beleg bleibt der vorhandene native Nachpflegeweg erforderlich. Eine ältere Datenbank wird nicht über neue Trades kopiert. Teilverkaufsketten oder nicht mehr abrufbare Orders dürfen eine gesonderte Allokation beziehungsweise einen historischen Originalbeleg verlangen.

Ein vollständiger Umbau aller wirtschaftlichen Zustände in ein einziges Freqtrade-artiges Projektionsmodell ist nicht Bestandteil dieser gezielten Stabilisierung. Die neuen Transaktionen lösen nicht automatisch jede historische Architektur- oder Datenlücke.

## 8. Installation und anschließende Abnahme

`START_HIER_9.8.7.txt` beschreibt den vorhandenen gesicherten Installer. Nur als georg ausführen; keine zwei Cores parallel, kein manuelles Entpacken über die aktive Installation, kein Zurückspielen alter Handelsdaten nach möglichem neuem Handel. Die vorhandene LIVE-Sperre des automatischen Updates bleibt erhalten.

Danach tatsächliche Dienstpfade und Versionen beider Dienste prüfen. Die neue Klärungskarte zeigt konkrete fehlende Belege und den letzten Nachlauf. `NEXUS_9_8_7_Statuspruefung.py` liest die aktuelle lokale Datenbank einschließlich WAL im Read-only-Modus und erzeugt keine leere Datenbank bei falschem Pfad. Sein Export enthält technische Kontokennungen, aber keine geladenen API-Schlüssel.

Echte ARM64-/systemd-Neustarts, Pi-/Safari-Bedienung, frische Broker-Schutzbestätigungen, erlaubte Providerzugriffe, Telegram-Freigabe und eine vollständige DEMO-Ausführungskette müssen im Nutzerbetrieb noch abgenommen werden. Kein Test hier ist eine Echtgeldfreigabe.

## 9. Quellen und Belegpaket

Lokale Grundlagen: `Befund_Kaufsperre_und_Klaerung_9_8_6.md`, `PULSAR_Quellen_Befund_9_8_6.md`, die Übergabe 9.8.6 vom 11.09.2026 einschließlich dokumentierter DOGE-/Support-/PEP-/SUI-Fälle, tatsächliches Basis-ZIP und dessen Tests. Die ältere 9.7.5-Übergabe wurde nicht als aktueller Laufzeitstand verwendet.

Offizielle Nachschlagestellen für die Schnittstellenprüfung (Abruf 11.09.2026):

- OKX EEA API, Fills/History/Algo-Kinder/Kontoinstrumente: https://my.okx.com/docs-v5/en/
- ApeWisdom API und Methodik: https://apewisdom.io/api/ und https://apewisdom.io/methodology/
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- OpenAI Websuche: https://platform.openai.com/docs/guides/tools-web-search
- SQLite VACUUM: https://www.sqlite.org/lang_vacuum.html

Das separate Prüfnachweis-ZIP enthält vollständige Basis-/Final-/Paketlogs, gezielte Gegenproben, tatsächliche Umgebungsinformationen, Quell-Diff und Dateiliste, UI-Ergebnisse und ausgewählte Screenshots sowie die transparente fehlgeschlagene öffentliche Python-Quellenprobe. Die Screenshots zeigen synthetische Testkonten, nicht Georgs aktuelle Konten.
