# Korrekturen in NEXUS 9.0.2

## Was unverändert bleibt

Die vorhandene WebUI mit der Seite **Trades**, das Universum aus 20 festen und
bis zu 30 dynamischen Kryptowerten sowie die Profile **Konservativ**,
**Ausgewogen** und **Offensiv** bleiben erhalten. Auch der separat schaltbare
Freqtrade-SampleStrategy-Modus wird nicht fachlich verändert. Eine offene
Position behält weiterhin den beim Einstieg gespeicherten Strategiemodus.

## OKX: EUR, USD und USDC

NEXUS akzeptiert bei OKX-Spot jetzt drei Cash-Quotes:

- `EUR`
- `USD`
- `USDC`

Damit können feste Kernwerte über ihr tatsächlich verfügbares USD-Paar
beobachtet und – nach allen normalen Sicherheitsprüfungen – gehandelt werden.
Der Bot sucht nicht frei über beliebige Gegenwerte: USDT-, BTC- oder andere
Krypto-Quotes bleiben für Orders ausgeschlossen.

Pro Coin wird ein live verfügbares Paar gewählt. Für die Volumenrangfolge
werden unterschiedliche Quotes nur mit einem beobachteten Umrechnungskurs
verglichen. Direkt vor einer Order prüft NEXUS weiterhin Instrumentstatus,
abgeschlossene Kerzen, Bid/Ask, Spread, Orderbuch, Gebühren, Cash, Risiko,
Mindestgröße und Anlaufsperre.

## Alte Einträge unter „Offene Trades“

Eine Ledger-Zeile wird nicht allein deshalb gelöscht, weil sie nicht im
Positionsbuch steht:

1. NEXUS liest den echten OKX-Bestand.
2. Existiert noch Coin-Bestand, wird er als ungeklärter Rest sichtbar gemacht.
3. Neue Krypto-Einstiege bleiben dann gesperrt; es erfolgt kein automatischer
   Verkauf und keine geratene Übernahme.
4. Nur wenn zwei vollständige Abgleiche hintereinander keinen Bestand zeigen,
   wird der verwaiste offene Historieneintrag beendet.
5. Fehlen historischer Verkaufskurs oder Ergebnis, bleiben diese Werte
   **unbekannt** und werden nicht als `0,00` gespeichert.

## Telegram-Dopplungen

Die erste Nachricht wird weiterhin sofort gesendet. Vorher erhält sie nun
einen atomaren Queue-Claim. Ein zweiter Sender kann denselben Eintrag nicht
parallel verarbeiten. Zusätzlich wird ein identischer Inhalt innerhalb von
25 Sekunden unterdrückt. Unterschiedliche Statusfolgen wie „Kauf ungeklärt“
und „Kauf wiederhergestellt“ bleiben eigenständige Meldungen.

## eToro-Stop und Take-Profit

Eine Schließung aus der eToro-Historie wird nur dann als Bot-Schutzexit
zugeordnet, wenn eine eigene Position im Modus `AUTO` vorhanden ist und der
Brokergrund oder der Ausführungspreis zum gespeicherten Stop beziehungsweise
Ziel passt. Andernfalls erscheint sie ausdrücklich als manueller
Broker-Verkauf. Dadurch werden beispielsweise Verkäufe direkt am geplanten
Stop nicht mehr mit dem Grund `UNBEKANNT` gespeichert.

## Verhalten nach Installation

Beim ersten Start gelten weiterhin die vorhandenen Anlaufsperren. Fehlende
Kerzen oder ein noch unvollständiger Universumszustand lösen keinen Kauf aus.
Die ersten zwei vollständigen OKX-Positionsabgleiche bereinigen ausschließlich
belegte verwaiste Historieneinträge; vorhandene Guthaben werden nicht verkauft.
