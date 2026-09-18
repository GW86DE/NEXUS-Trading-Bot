# PULSAR in NEXUS 9.8.4

PULSAR beobachtet auffällige Aktien und zeigt fünf Kandidaten auf einer eigenen
Seite. Die Reihenfolge beruht auf wachsender Aufmerksamkeit mit einer Mindestmenge
an Erwähnungen. Ein Rang ist weder eine Kaufempfehlung noch eine Gewinnwahrscheinlichkeit.

## Quellen und Start

Nach dem Update ist PULSAR aus. Unter **PULSAR → Beobachten → Übernehmen** beginnt
der laufende Handelskern mit dem Datenaufbau. Die Weboberfläche erzeugt keine
Brokerorder und startet keinen zweiten Handelsprozess.

| Quelle | Verwendung | Voraussetzung |
|---|---|---|
| ApeWisdom | Aktien-Erwähnungen, Rang, Veränderung zu 24 Stunden | Öffentliche API, kein eigener Schlüssel |
| Tradestie | Ergänzende WallStreetBets-Stimmung | Optional aktivieren; Fehler bleiben sichtbar |
| FMP | Unternehmensdaten, Kurse, Tages-/15-Minuten-Kerzen, Nachrichten | Vorhandener eingerichteter Zugang |
| MASSIVE | Zusätzliche Nachrichten | Bereits eingerichteter und aktivierter Zugang |
| SEC | Veröffentlichte Unternehmenszahlen | Vorhandene SEC-Kontaktkonfiguration |
| Luna / Terra | Vorprüfung, These und unabhängige Gegenprüfung | Vorhandener aktivierter KI-Router und Budget |

Die Implementierung benötigt kein Stocktwits-, Reddit- oder LunarCrush-Abo.
Bestehende Zugänge werden nicht durch neue Abos ersetzt. Kostenpflichtige Aufrufe
bleiben innerhalb der vorhandenen Routergrenzen und der zusätzlichen PULSAR-Grenzen.

Offizielle Schnittstellen: [ApeWisdom](https://apewisdom.io/api/),
[Tradestie](https://tradestie.com/apps/reddit/api/),
[FMP 15-Minuten-Kerzen](https://site.financialmodelingprep.com/developer/docs/stable/intraday-15-min).
Die Erreichbarkeit auf Georgs Pi ist noch im Beobachtungsbetrieb zu prüfen.

## Was die Daten aussagen können

Die Aggregatfeeds liefern keine vollständigen Einzelbeiträge und keine eindeutigen
Autoren. Sie belegen damit weder 20 unterschiedliche Autoren noch drei eigenständige
Argumente aus zwei Communities. Diese Pflichtbedingungen aus dem Konzept bleiben
offen. Die 14-/28-Tage-Aufmerksamkeitsbasis entsteht erst während des Betriebs.
Überlappende Foren von ApeWisdom und Tradestie zählen als korrelierte Quellen.

Der Belegscore trennt Katalysator (30), Community (25), Kurs/Volumen (20),
Finanzen (15) und Gegenprüfung (10). Fehlende Komponenten bleiben unbekannt.
Auch ein hoher Erwähnungswert wird nicht in einen vollständigen Score umgedeutet.
Die aktuelle Kombination der Aggregatquellen erzeugt deshalb keine Kaufnominierung.
Luna/Terra dürfen Quellenlücken benennen, aber nicht mit erfundenen Fakten schließen.

## Eingebaute Freigabe- und Handelsregeln

Die Freigabeinfrastruktur ist vorhanden und offline geprüft. Sie bleibt gesperrt,
bis die Pflichtbelege tatsächlich vorliegen. Ein Wechsel auf „Persönliche Freigabe“
umgeht diese Bedingungen nicht.

- Eine Nominierung je ISO-Woche in Europe/Berlin, installationsweit; Ablehnung oder
  Ablauf verbraucht die Woche ebenfalls. Höchstens zwei offene oder schwebende Trades.
- Nur echte, ungehebelte Aktien über den bestehenden eToro-Kern. Keine separaten
  Social-Kauforders. Normale Konto-, Risiko-, Cash-, Gebühren- und Handelszeitprüfungen gelten weiter.
- Maximal 0,25 % Kontorisiko, 3 % Positionskapital, 6 % PULSAR-Gesamtkapital.
  Einstand und Stop werden bei der persönlichen Freigabe festgehalten.
- Zwei Bestätigungen in derselben persönlichen Telegram-Nachricht: zunächst
  innerhalb 30 Minuten, anschließend innerhalb 60 Sekunden. Bindung an Nutzer,
  Chat, Nachricht, Sitzung, Konto, Umgebung, Woche und unveränderten Plan.
- Nach Neustart oder Modusänderung sind alte ungesendete Freigaben ungültig.
  Bereits gesendete oder unklare Orders bleiben gespeichert und werden nicht wiederholt.
- Einstiege ab 30 Minuten nach Börsenöffnung bis 60 Minuten vor Schluss.
  Fehlender oder naher Earnings-Termin sperrt den Kauf. Der Abstand wird derzeit
  konservativ mit vier Kalendertagen abgesichert; die genaue Zwei-Handelstage-Regel
  wird damit nicht für jeden Feiertagsfall ausgeschöpft.
- Fester ursprünglicher Risikobetrag R; ab +1R Stop mindestens auf den tatsächlichen
  Einstand; vorab zugelassener Drittelverkauf ab +2R, sofern Stückzahl und Kosten passen.
  Danach Nachlauf anhand abgeschlossener 15-Minuten-Hochs und dreifacher Tages-ATR.
- Prüfung nach zehn, maximal zwanzig Handelstagen. Client-Stops benötigen einen
  laufenden Kern und frische Kurse. Ein unbekannter Teilorderausgang wird nicht erneut gesendet.
- 30-Tage-Verlustgrenze 0,75 %; offene Ergebnisbelege blockieren neue PULSAR-Käufe.

## Verbrauch und Darstellung

Social-Abfragen werden normalerweise stündlich zwischengespeichert. Der Worker
prüft werktags zwischen 07 und 19 Uhr New Yorker Zeit, am Wochenende höchstens
zweimal täglich. Separate Grenzen: 80 Social- und insgesamt 200 Datenaufrufe am
Tag; 400 bzw. 1.000 pro Woche. Fehler zählen mit; HTTP 429 löst eine gespeicherte Pause aus.

Zusätzliche KI-Grenzen: 1 USD pro Tag, 2 USD pro Woche, 10 USD pro Monat gemäß den
konfigurierten Modellpreisen. Reservierungen bleiben bei unklarem Ausgang belastet.
Der aktuelle Router liefert keinen verifizierbaren Tokenbeleg; deshalb wird eine
konservative Obergrenze weiter belastet und nicht als exakte Rechnung ausgegeben.

Kurszeiträume verwenden abgeschlossene Kerzen. Fehlende Kurse erscheinen als
Datenlücke. Kauf-/Verkaufsmarkierungen stammen aus dem vorhandenen Finanzledger.
Ergebnisse und Export bleiben nach Konto, Umgebung und Währung getrennt. Alte
Ergebnisse ohne vollständige Gebühren werden nicht als bestätigtes Netto angezeigt.

Die Quellen- und Freigabelogik wurde mit lokalen Daten und Testdoubles geprüft.
Keine Telegram-Nachricht, externe KI-Anfrage oder Brokerorder wurde bei der
Entwicklung dieser Version versendet. Echte Demoabnahme steht nach Installation aus.
