# NEXUS 9.0.9 – Brokerwahrheit, Währung und Diagnose

## Verbindliche Grundregel

Die Broker-API ist die Quelle für Orders und Ausführungen, aber ein einzelner
Kontosaldo ist kein Trade. NEXUS trennt deshalb drei Sachverhalte:

| Brokerdaten | Bedeutung | Automatische Verwaltung |
|---|---|---|
| OKX `availBal` / `cashBal` / `eq` | Konto-Asset oder Cash | nein |
| OKX `clOrdId/tag → ordId → tradeId` | nachgewiesene Bot-Teilmenge | ja, bei vollständigem Beweis |
| eToro `referenceId/orderId → positionId` | nachgewiesene Botposition | ja, nach Depot-/Historienbeweis |

Damit verschwinden die falschen offenen SOL-/ETH-Trades. Ein bereits
vorhandener Coinbestand bleibt sichtbar, wird aber nicht verkauft. Kauft der
Bot später 0,1 ETH und sind 0,996348 ETH bereits im Konto, zeigt NEXUS exakt
0,1 ETH als `BOT_MANAGED` und den Rest als `ACCOUNT_ASSET`.

## Handelswährungen auf OKX

NEXUS arbeitet nicht mit dem in USD ausgedrückten Gesamtvermögen als
Order-Cash. Verwendet wird nur das tatsächlich freie Guthaben der für den Bot
erlaubten Abrechnungswährung:

- primär EUR;
- zusätzlich USDC;
- USD nur dann, wenn es künftig ausdrücklich konfiguriert und vom konkreten
  Instrument in `tradeQuoteCcyList` erlaubt ist;
- USDT und SOL sind keine Bot-Cashwährungen.

Für einen Markt wie `UNI-USD` entscheidet nicht der Text `USD` allein.
Verbindlich sind die Werte aus dem authentifizierten Kontoinstrumentkatalog.
Eine Umrechnung wird aus beobachteten Spotkursen berechnet; es gibt keine
angenommene Stablecoin-Parität.

## Offizielle API-Grundlagen

- OKX: `clOrdId` ist der kundenseitige eindeutige Orderanker; `ordId` oder
  `clOrdId` können den Orderstatus abfragen. `tradeQuoteCcy` muss aus der
  kontoseitig gelieferten `tradeQuoteCcyList` stammen. Transaktionsdetails
  liefern die eindeutige `tradeId`, `ordId`, Fillmenge, Fillpreis und Gebühr.
- eToro: Der asynchrone Auftrag wird getrennt über genau einen Anker
  `orderId` oder `referenceId` nachgeschlagen. Ausführungen liefern die
  entstandenen `positionId`-Werte. Der private Stream meldet `RequestGuid`,
  `OrderID`, `StatusID` und Positionsereignisse, ersetzt aber nicht den
  abschließenden REST-/Depotbeweis.

Referenzen:

- https://www.okx.com/docs-v5/en/
- https://api-portal.etoro.com/api-reference/trading--real/get-order-information-and-position-details
- https://api-portal.etoro.com/core/websocket/example-code
- https://api-portal.etoro.com/core/websocket/notifications/websockets/private-portfolio-updates
- https://api-portal.etoro.com/core/getting-started/rate-limits

## Warum mehrere Fills keine Doppelorder sein müssen

Eine Market-Order kann gegen mehrere Gegenorders ausgeführt werden. Mehrere
`tradeId` mit derselben `ordId` sind Teilfüllungen einer einzigen Order. NEXUS
summiert ihre Menge, bildet den gewichteten Einstand und speichert jede
Brokergebühr. Erst zwei verschiedene `ordId` für denselben Kaufwunsch wären
ein Doppelorder-Indiz; davor schützt die vorab persistierte `clOrdId`.

## Handelbarer Kern

Die Zahl 20 ist eine Obergrenze, kein Grund, ungeeignete Werte aufzunehmen.
Der neue Kern wird ausschließlich aus für dieses OKX-Konto ausführbaren
Spotinstrumenten gebildet. Ein Wert muss zusätzlich die bestehenden harten
Qualitätsfilter bestehen. Die Oberfläche zeigt Ziel, Istzahl und Filtergründe.

## Logbuch

Das Entscheidungslog verwendet nun explizite DOM-Elemente und unterscheidet
`NO_SIGNAL`, echte Blockaden, technische Fehler und unklare Ausführungen. Für
Krypto-Signale sind die geprüften Regeln samt Messwerten sichtbar. Damit lässt
sich unterscheiden, ob ein Coin nicht handelbar, der Markt zu dünn, das
Risiko zu hoch oder schlicht kein Einstiegssignal vorhanden war.

