# PULSAR in NEXUS 9.8.6

Diese Version setzt den bestätigten Quellenansatz mit gezielter GPT-Websuche um.
PULSAR ist im Hauptmenü und im mobilen Auswahlmenü erreichbar. Universum und
Underdogs bleiben eigene Menüpunkte. PULSAR liefert fünf Beobachtungskandidaten;
der bestehende Trading-Core entscheidet weiterhin über tatsächliche Ausführungen.

## Datenfluss

| Stufe | Umsetzung | Grenze |
|---|---|---|
| Aufmerksamkeit | ApeWisdom all-stocks, maximal drei Seiten; zusätzlich stocks und investing, jeweils erste Seite | Bis zu 500 gelieferte Zeilen, meist weniger unterschiedliche Aktien; keine vollständige Reddit-Suche |
| Zusätzliche Stimmung | Tradestie auf Wunsch | Gleiche Reddit-Quellenfamilie; kein unabhängiger zweiter Beleg |
| Marktdaten | Vorhandene FMP-Anbindung: Profil, Tages-/15-Minuten-Kerzen, Nachrichten und Earnings-Kalender; optional MASSIVE | Vorhandene Zugänge und Anbieterlimits gelten |
| Vorauswahl | Wachstum mit Mindeststichprobe; fünf Kandidaten, je eine Luna-These und drei Risiken | Keine Kaufentscheidung aus Erwähnungen |
| Ereignisrecherche | Ein vorausgewählter Kandidat pro Zyklus: aktuelle SEC-Submissions plus Originaldokument/ggf. Exhibit 99.1 | SEC benötigt die vorhandene Kontakt-E-Mail; maximal eine aktuelle 8-K/6-K-Einreichung plus ein Exhibit |
| GPT-Websuche | Eigene Responses-API-Aufgabe mit verpflichtendem web_search, Domainfilter und vollständiger Quellenliste | Höchstens zwei Suchwerkzeugaufrufe pro Recherche; Modellzugang muss Websuche unterstützen |
| Originalprüfung | Unternehmensmeldung wird von der durch FMP zugeordneten Domain abgerufen; Name, Veröffentlichungsdatum und Wortlaut geprüft | Fremde IR-Domains, PDFs, undatierte Seiten und unklare Identitäten bleiben ungeklärt |
| Einordnung | Luna ordnet den Originaltext einem wirtschaftlichen Anlass zu; exaktes Zitat muss im geladenen Text vorkommen | Eine technische Wortlautprüfung bestätigt nicht automatisch die wirtschaftliche Deutung |
| Vertiefung | Terra prüft das Quellenpaket, danach separate Terra-Gegenprüfung | Gegenprüfung erhält Quelldaten, nicht das Urteil der ersten Analyse |
| Freigabe | Deterministischer Belegscore und zeitlich getrennte Bewertungen, danach bestehende persönliche Freigabe und Core-Prüfung | Zwei persönliche Telegram-Bestätigungen bleiben erforderlich |

ApeWisdom-Foren und Seiten werden nach Ticker zusammengeführt. Die aggregierte
Anzahl hat Vorrang; überlappende Zahlen werden nicht addiert. Ausfälle und die
reale Quellenabdeckung stehen auf der Seite. Ein neuer Abruf identischer Fakten
behält dieselbe Belegidentität, wird aber als eigener historischer Messpunkt
aufgezeichnet. Die Vergleichsbasis wird je Feed getrennt geführt.

## GPT sucht ausdrücklich im Web

Die neue Aufgabe `pulsar_web_research` benutzt das konfigurierte Luna-Modell mit
`tools: [{type: "web_search"}]`, `tool_choice: "required"`, `max_tool_calls: 2`
und `include: ["web_search_call.action.sources"]`. Gesucht wird nach neuen
Ergebnissen, Prognosen, Verträgen, Zulassungen, Finanzierung und Gegenbelegen.
Zugelassen sind SEC, FDA und die zugeordnete Unternehmensdomain. FDA-Treffer
bleiben in dieser Version Suchhinweise; nur SEC- oder Unternehmensdokumente
können den implementierten Primärbeleg liefern.

Nur URLs aus tatsächlichen API-Werkzeugbelegen werden zur weiteren Prüfung
zugelassen. Eine URL, die nur im GPT-Antworttext steht, reicht nicht. Auch eine
Suchzusammenfassung erzeugt keinen Ereignisbeleg. Der Bot lädt das Original
separat, prüft Herkunft, Veröffentlichungsdatum (höchstens 72 Stunden alt),
Unternehmenszuordnung und Zitat und bindet die Einordnung an den Dokumenthash.
Bei SEC gilt das Annahmedatum der Einreichung als Meldungsdatum; dieses belegt
nicht automatisch das Datum des zugrunde liegenden wirtschaftlichen Vorgangs.
GPT und die Gegenprüfung sollen rückblickende/recycelte Aussagen ausdrücklich
verwerfen. Fehlende oder widersprüchliche Belege erzeugen keine Freigabe.

Die Websuche lässt sich auf der PULSAR-Seite gesondert ausschalten. Dadurch
werden allgemeine GPT-Webaufgaben im übrigen Bot weder aktiviert noch geändert.
Bei PULSAR „Aus“ erfolgen keine neuen Quellen- oder KI-Aufrufe.

## Bestätigte Regeländerung

„Community bestätigt“ ist ein **optionales Zusatzmerkmal**. ApeWisdom und
Tradestie liefern keine eindeutigen Einzelautoren, Beiträge oder Argumentgruppen.
Deshalb wird das Merkmal mit diesen Anschlüssen nicht als bestätigt angezeigt.
Sein Fehlen allein blockiert keine Nominierung mehr. Die frühere Beschreibung
in PULSAR_9.8.5_DE.md dokumentiert den alten Stand; für diese Version gilt hier
PULSAR 1.1.

| Pflichtkomponente | Punkte |
|---|---:|
| Aktueller wirtschaftlicher Anlass mit geprüftem Originalbeleg | 30 |
| Auffällige Aufmerksamkeit gegenüber eigener historischer Basis | 25 |
| Kurs und Volumen | 20 |
| SEC-Finanzdaten | 15 |
| Separate Gegenprüfung | 10 |

Die Aufmerksamkeit benötigt mindestens 14 beobachtete Tage, mindestens
40 aktuelle Erwähnungen und mindestens das Zweifache der eigenen Vergleichsbasis
(mit einer Mindestbasis von 20). Es werden keine 14 Tage aus stündlichen
Messpunkten erzeugt. Die 28-Tage-Historie wird weiter aufgebaut. Neue Installationen
können deshalb nicht sofort eine Handelsnominierung erzeugen.

Mindestens 80/100 Punkte, vollständige Pflichtdaten, keine Ablehnung, zwei
geeignete Bewertungen mindestens 60 Minuten auseinander und derselbe
Originalbeleg sind erforderlich. Fehlende Teilwerte bleiben unbekannt und werden
nicht auf 100 hochgerechnet. Alte Bewertungen aus PULSAR 1.0 zählen nicht als
neue Freigabebewertungen. Ein anderer Ereignisbeleg beginnt die zeitliche
Bestätigung erneut. Kurse, Konto, Handelsfenster, Earnings-Abstand, Gebühren,
Handelbarkeit, Positionsgröße und Schutz prüft der Core unmittelbar erneut.

Die bestehenden Grenzen bleiben: 0,25 % Kontorisiko je Trade, maximal 3 %
Positionskapital, insgesamt maximal 6 % PULSAR-Kapital, höchstens zwei offene
oder schwebende Trades, eine Nominierung pro ISO-Woche (Europe/Berlin).
Neustart oder Modusänderung widerrufen offene ungesendete Freigaben. Ein
ungeklärter Orderausgang wird nicht einfach erneut gesendet.

## Verbrauch und Ausfälle

- Social-Cache: eine Stunde; GPT-Suchergebnisse und gültige Textprüfungen: sechs Stunden.
- Maximal zwei neue GPT-Webrecherchen pro Tag und acht pro Woche; je maximal zwei Suchaufrufe.
- Alle PULSAR-KI-Aufgaben teilen 1 USD/Tag, 2 USD/Woche und 10 USD/Monat.
- Berechnung anhand konfigurierter Modellpreise plus 0,01 USD je bestätigtem Websuchaufruf. Abgerufene Suchinhalte zählen zusätzlich zu den Modell-Tokenkosten.
- Vorherige konservative Reservierung berücksichtigt bis zu drei Modellschritte im dokumentierten 128k-Suchkontext. Ein bestätigter Verbrauchsbeleg gibt ungenutzte Reservierung frei. Ohne Verbrauchsbeleg bleibt die Reservierung stehen.
- Zusätzlich maximal 80 Social-/200 Datenaufrufe pro Tag und 400/1.000 pro Woche; neue Kandidaten: höchstens 20 pro Tag.
- Fehler erzeugen eine Abrufpause; keine unbegrenzte Wiederholung. Die WebUI zeigt Kosten einschließlich offener Reservierungen und Quellenlücken.

Diese Grenzen sind anwendungsseitige Sperren anhand der eingestellten Preise,
keine Preisgarantie des Anbieters. Nicht unterstützte Modelle, fehlender
API-Zugang, ausgeschöpftes Budget und nicht abrufbare Dokumente werden als offen
angezeigt. Es wird kein kostenpflichtiger Ersatzanbieter automatisch gebucht.

## Einrichtung und Abnahme auf dem Pi

1. Über die neue Installationsdatei installieren, danach WebUI vollständig neu laden und Kopfzeile 9.8.6 prüfen.
2. Vorhandenen GPT-Router/Luna/Terra-Zugang sowie FMP prüfen; SEC-Kontakt-E-Mail in den Einstellungen eintragen, falls sie fehlt. Keinen Schlüssel an Dritte schicken.
3. PULSAR auf „Beobachten“ stellen, „GPT-Websuche nutzen“ eingeschaltet lassen. Tradestie optional zuschalten.
4. Während des Recherchefensters (werktags 07–19 Uhr New York) ersten Durchlauf abwarten. Automatischer Zyklus: 15 Minuten; am Wochenende höchstens zweimal täglich.
5. Quellenabdeckung, Datenstand, GPT-Suchstatus, Originalbeleg, individuelle Texte und Kosten prüfen. Eine fehlende Freigabe während Historienaufbau oder bei Datenlücken ist erwartbar.

Offline-Tests prüfen mit künstlichen Anbieterantworten den tatsächlichen
Programmfluss. Sie bestätigen weder die Erreichbarkeit der Dienste vom Pi noch
die Qualität jeder echten KI-Auslegung oder eine erfolgreiche Brokerorder.

## Geprüfte Anbieterunterlagen

- [ApeWisdom API](https://apewisdom.io/api/) und [Methodik](https://apewisdom.io/methodology/)
- [Tradestie API](https://tradestie.com/apps/reddit/api/)
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenAI API-Preise](https://developers.openai.com/api/docs/pricing)

Stand der Dokumentation und Implementierung: 11. September 2026.
