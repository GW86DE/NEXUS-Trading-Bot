# NEXUS: SUI-Verkauf und Korrektur 9.7.6

Stand: 8. September 2026. Basis: das tatsächlich beigefügte Originalpaket 9.7.5.

## Ergebnis

**Der Code prüft Demo-Verkäufe mit Marktdaten ohne Demo-Kennung. Das ist ein reproduzierbarer Fehler in der Ausführungsstrecke.** Ein angezeigtes ausreichendes Live-Orderbuch kann deshalb keinen ausreichenden Demohandel belegen. Die Korrektur bindet Orderbuch, Ticker und Kerzen an die gewählte Handelsumgebung und sichert zusätzliche unklare Verkaufszustände ab.

Der konkrete Matching-Grund jedes historischen SUI-Stornos bleibt ohne dessen damalige Demo-Orderbuchantwort offen. Die Korrektur ist kein nachträglicher Nachweis, dass OKX einen bestimmten historischen Auftrag hätte ausführen müssen. Der aktuelle Screenshot belegt weiterhin fehlgeschlagene Verkäufe, keinen erfolgreichen SUI-Abschluss.

## Welche Übergabe wurde geprüft?

Das komplette aktuelle Übergabeprotokoll einschließlich Vorgeschichte, SUI/KO-Fällen, Schutzregeln, Installerfehler und offenen Aufgaben wurde gelesen. Hinzu kommen strukturierter Status, die SUI-/Installerberichte, der bereitgestellte Quellbaum und der Freqtrade-Quellcode. Der neue Screenshot zeigt eine WebUI mit Version 9.7.5. Das aktualisiert die ältere Übergabe, die zuletzt nur den gescheiterten Wechsel von 9.7.4 kannte; es beweist allein weder den konkreten FIX1-Hash noch beide systemd-Dienstpfade.

SHA-256 der unveränderten Eingabe-ZIP:

`be296d064073935bb9c2bb66051a7a4d9e0ec38e3b4a71693d4e5e5d3e9e86fe`

Alle 534 Einträge ihres Quellmanifests stimmten. Das ist genau das in der Übergabe genannte Originalpaket, einschließlich des bekannten ausgelieferten Zustandsmarkers. Nutzer-Datenbanken und aktuelle vollständige Laufzeit-JSONs sind in den hier beigefügten Übergabe-ZIPs nicht enthalten. Historische Auswertungen daraus wurden als Berichte behandelt, nicht als neue Datenbankprüfung ausgegeben.

Die zusätzliche OKX-PDF enthält 977 gescannte Seiten ohne extrahierbaren Text. Für die aktuellen API-Verträge wurden deshalb die offiziellen durchsuchbaren OKX-Unterlagen herangezogen. Es wurde keine vollständige OCR-/Sichtprüfung aller 977 Seiten behauptet.

## Was der neue Screenshot tatsächlich zeigt

| Beobachtung | Bedeutung |
|---|---|
| SUI-USDC, geplante gerundete Ordermenge 124,77 SUI | Passt zum historischen Nettobestand 124,771765 nach Basisgebühr. |
| Best Bid 0,8175; benötigter letzter Bid 0,8174; Verkaufslimit 0,8093 | Das Limit liegt unter beiden angezeigten Geldkursen. Eine zu hohe Verkaufspreisforderung erklärt diese Zahlen nicht. |
| Ausführung 0; `cancelSource=13` | OKX meldet die Stornierung einer nicht vollständig ausführbaren FOK-Order. Das ist kein Verkaufsfill. |
| Versuch 4, nächste Frist 09:34:54 UTC nach Fehler um 10:54:54 MESZ | Der vorhandene Backoff erzeugt 40 Minuten Wartezeit: 5 × 2³. Das ist programmierte Wartezeit. |
| „Broker-Schutz im letzten Abgleich bestätigt“ | Eine gespeicherte Abgleichsaussage; kein hier neu gelesener Börsenschutz. |

OKX unterscheidet FOK-Storno 13 und IOC-Reststorno 14. Ein Storno darf nur zusammen mit Ausführungsmenge und passenden Fillbelegen bewertet werden. [OKX API-Änderungsdokumentation](https://www.okx.com/docs-v5/log_en/), [OKX Order-Lifecycle](https://www.okx.com/docs-v5/trick_en/).

## Nachgewiesene Fehler und umgesetzte Änderungen

### 1. Live-Marktdaten vor Demo-Order

In 9.7.5 setzt `OKXClient._headers()` die Demo-Kennung ausschließlich bei privaten Konto-/Orderaufrufen. `orderbook()`, `ticker()`, `tickers()` und `candles()` sind öffentliche Aufrufe und erhalten sie nicht. `execution_quote()` nutzt genau dieses Orderbuch für Preis und Menge. Der anschließende SELL bekommt dagegen die Demo-Kennung.

9.7.6 setzt sie auch für `/api/v5/market/…`, ohne dabei Zugangsdaten an öffentliche Aufrufe anzuhängen. Live bleibt Live. Der vorhandene öffentliche Referenzkatalog und die Zeitschnittstelle behalten ihren bisherigen Weg; kontobezogene handelbare Instrumentregeln kommen weiterhin aus dem privaten Instrumentabruf. Es gibt keinen automatischen Rückfall auf Live-Preise bei einem Demo-Marktdatenfehler.

OKX dokumentiert die Demo-Kennung für Demo-Anfragen und bietet die Umgebungswahl auch im MarketData-Beispiel an. [OKX Demo-Vertrag](https://my.okx.com/docs-v5/en/#overview-demo-trading-services), [OKX MarketData-Beispiel](https://my.okx.com/docs-v5/en/#market-data-get-tickers).

Die Gegenprobe verwendet einen künstlichen Live-Bid von 0,8175 und einen ausdrücklich synthetischen Demo-Bid von 0,8050. Der unveränderte Code erzeugt daraus einen nicht ausführbaren Demo-FOK; die Korrektur benutzt das passende Buch. **0,8050 ist ein Testwert und kein behaupteter historischer OKX-Demokurs.** Eine zweite Gegenprobe bietet nur im Demo-Buch zu wenig Menge: jetzt wird schon vor dem Schutzstorno abgebrochen.

### 2. Bereits vergebene Verkaufs-ID wurde wie Ablehnung behandelt

Der Kaufpfad behandelt OKX-Code 51016 bereits durch Suche nach der bestehenden Client-ID. Dem Verkaufspfad fehlte diese Behandlung. Ein solcher Fehler konnte deshalb in den normalen Fehler-/Schutzerneuerungspfad gelangen, obwohl eine frühere Order existieren kann.

9.7.6 sucht ausschließlich die bestehende Order. Bleibt sie unklar, geht der Vorgang in `UNCLEAR`. Eine neue Verkaufs-ID oder zusätzliche Schutz-SELL-Order wird dadurch nicht freigegeben.

### 3. Fehler nach Orderannahme wurden nicht durchgehend als unklar behandelt

Ein Fehler beim Verarbeiten der nachfolgenden Ausführungsbelege konnte als gewöhnlicher `BrokerFehler` nach außen gelangen. Für die Engine war er damit nicht ausreichend von einer bestätigten Ablehnung getrennt.

Nach angenommener beziehungsweise möglicherweise angenommener Übermittlung hält 9.7.6 Order-ID und Client-ID fest und meldet einen unklaren Zustand. Widersprüchliche Order-ID, Client-ID, Instrument oder Seite werden nicht als passender Verkaufsbeleg übernommen. Ebenso werden widersprüchliche Fill-Identitäten abgefangen. Es erfolgt keine neue Order aus diesem Fehlerpfad.

### 4. Preis- und Datenprüfung

Ein Randfall in der Tickrundung konnte eine bereits gerundete harte Untergrenze wieder unterschreiten. Gibt es keinen zulässigen Preis-Tick innerhalb der Grenze, wird jetzt abgebrochen. Nicht endliche oder unzulässige Buchwerte werden abgewehrt. Diese Gegenfälle sind zusätzliche Codebefunde, keine behauptete Ursache des gezeigten SUI-Stornos.

### 5. Nachvollziehbare Ausführungsbelege

Gespeichert werden zusätzlich die angeforderte Marktdatenumgebung, Buchzeit, gelesene Preis-/Mengenlevels und der genaue abgeschickte Orderkörper. Die Engine speichert diese erweiterten Ergebnisse bereits über ihren vorhandenen Orderstatistikpfad. Authentifizierungsheader werden nicht in diese Belege aufgenommen.

Das neue `Nexus_SUI_Diagnose.py` kann den tatsächlichen Dienstordner, Quellhashes, lokale SUI-Zustände und passende Order-/Ledgerzeilen exportieren. Es importiert keinen Botcode, liest keine Credential-Dateien, sendet keine Brokeranfrage und startet oder stoppt keinen Dienst. Die SQLite-Abfrage ist read-only und transaktional. Die separat gelesenen JSON-Dateien sind ausdrücklich kein gemeinsamer atomarer Laufzeitsnapshot.

### 6. Passende Marktdaten auch in der Trade-Anzeige

Die Trade-Anzeige hatte einen fest auf Demo eingestellten Client, der wegen der alten Headerregel bislang trotzdem öffentliche Live-Daten las. Nach der Headerkorrektur wäre daraus eine falsche Demo-Abfrage für Live-Trades geworden. Deshalb wird der Anzeigeclient jetzt vor der ersten Abfrage an die gespeicherte Trade-Umgebung gebunden. Auch der Kurscache trennt Live und Demo. Fehlende oder widersprüchliche Umgebungsangaben erzeugen keinen geratenen aktuellen Kurs. Die reine Research-/Backtest-Analyse darf weiterhin bewusst öffentliche Live-Daten lesen; sie ist kein Nachweis ausführbarer Demo-Liquidität.

## Was Freqtrade und Hummingbot beitragen

| Referenz | Für NEXUS nutzbares Prinzip | Grenze |
|---|---|---|
| Freqtrade `Trade.orders` und `recalc_trade_from_orders()` | Mehrere getrennte Orders gehören zu einem Trade; Bestand und Ergebnisse werden aus ihren Ausführungen abgeleitet. | Die vollständige Zentralisierung aller NEXUS-Risiko- und Positionsspeicher ist mit dieser Korrektur nicht abgeschlossen. |
| Freqtrade SampleStrategy | Die bereitgestellte Referenz nutzt GTC für Ein- und Ausstieg. | `FREQTRADE_SAMPLE` in NEXUS übernimmt Signale, nicht automatisch Freqtrades kompletten Ordermanager. |
| Freqtrade `dry_run` | Lokale simulierte Orders auf Basis gelesener Marktdaten. | Kein Beleg dafür, dass Live-Marktdaten zum OKX-Demomatching passen. |
| Hummingbot ClientOrderTracker | Client-ID vor der Übermittlung, getrennte Status- und Fill-Verarbeitung, Nachverfolgung der konkreten Order. | Nicht jeden fremden Fehler-Fallback ungeprüft übernehmen. |
| Hummingbot InFlightOrder/OKX-Connector | Fill-ID deduplizieren, reale Ausführungsmenge, Preis und Gebühr je Fill übernehmen. | Eine Orderannahme ist weiterhin kein vollständiger Buchungsbeleg. |

Freqtrades Dokumentation bestätigt, dass Dry-run keine Orders an die Börse sendet. Hummingbot verlangt den Beginn der Orderverfolgung vor dem API-Aufruf und unterscheidet Fill- von Abschlussereignissen. [Freqtrade Dry-run](https://www.freqtrade.io/en/stable/configuration/#considerations-for-dry-run), [Hummingbot Order-Lifecycle](https://hummingbot.org/connectors/connectors/architecture/order_lifecycle/).

Geprüfte Hummingbot-Quelldateien: [ClientOrderTracker](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/connector/client_order_tracker.py), [InFlightOrder](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/core/data_type/in_flight_order.py), [OKX-Connector](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/connector/exchange/okx/okx_exchange.py). Der heruntergeladene Stand wird im Nachweispaket mit Dateihashes dokumentiert; `master` ist kein unveränderlicher Versionsanker.

Es wurden keine Fremdprojekte eingebaut oder große Quellblöcke kopiert. Besonders Freqtrades generischer synthetischer Nullfill-Fallback bei nicht mehr lesbaren stornierten Orders wäre für die bestehenden NEXUS-Eigentumsregeln keine geeignete pauschale Übernahme. Auch Hummingbot hat im gelesenen Tracker einen zeitlich begrenzten Wartepfad für verspätete Fills; seine Existenz ist keine Garantie vollständiger Brokerbelege.

## Warum jetzt keine pauschale Umstellung von FOK auf IOC/GTC?

Die Umstellung würde den nachgewiesenen Umgebungsfehler bestehen lassen. IOC benötigt vollständig erprobte Teilfill-/Restmengenbehandlung; GTC zusätzlich das Management länger liegender Orders, Storno-/Fill-Rennen und Schutz für verbleibende Mengen. Eine Market-Order würde die bestehende Preisbegrenzung ändern.

Der erste konkrete Schritt ist deshalb die konsistente Umgebung samt belastbarer Orderverfolgung. FOK und die konfigurierten Slippagegrenzen bleiben erhalten. **Auch korrektes FOK garantiert keine Ausführung bei unzureichender oder zwischenzeitlich verschwundener Liquidität.** Ein bestätigter Nullfill bleibt offen sichtbarer Fehlversuch und darf nicht als geschlossener Trade erscheinen.

## Installationsumfang und verbleibende Abnahme

Das Paket 9.7.6 verwendet einen neuen Zielordner. Die dokumentierte FIX1-Regel gegen Runtime-Dateien im Quellmanifest ist übernommen; der alte Auslieferungsmarker ist entfernt. Der Installer liest seine Version jetzt aus `VERSION.txt`. Der frühere spezielle Reparaturhelfer für einen abgebrochenen 9.7.5-Zielordner wird nicht nachgebaut; der neue Installer übernimmt aus dem tatsächlich ermittelten aktuellen Dienstordner.

Bestehende Konto-/Demo-/Live-Trennung, Kaufidentitäten, Netto-SUI-Bestand, Pending-/Retry-Schutz, eToro-Code und Strategieregeln werden nicht durch eine Datenbereinigung ersetzt. Die historische 9.7.5-Reparatur bleibt beleggestützt und idempotent. Kein echter Börsenaufruf oder Pi-Dienststart wurde hier ausgeführt.

Die Freigabe im eigenen DEMO-Betrieb braucht noch die tatsächliche Kette: passendes Demo-Buch → konkrete Order → terminaler Status und Fills → richtige Restmenge → zugehöriger Ledgerabschluss → passende Anzeige und Schutz. Ein historischer oder neuer Nullfill ist keine Echtgeldabnahme. Die Zentralisierung der gesamten wirtschaftlichen Buchhaltung bleibt eine separate, größere Architekturaufgabe.

Die tatsächlich ausgeführten Testzahlen und Umgebungsdaten stehen im beigefügten Prüfprotokoll. Frühere grüne Testzahlen wurden nicht als neue Abnahme übernommen.
