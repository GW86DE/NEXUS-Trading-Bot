# NEXUS 9.0.7 – API-, Reconciliation- und Währungskorrektur

## Verbindliche Währungsregel

| Ebene | Bedeutung |
|---|---|
| EUR | Primäre OKX-Bot-Cash- und Risikowährung |
| USDC | Zusätzlich erlaubte Bot-Cash-/Ausführungswährung |
| USD | Mögliche Marktquote eines Unified-USD-Instruments, aber nicht automatisch Bot-Cash |
| USDT / TRY | Keine Standard-Handelswährung dieses Bots |
| BTC / ETH / SOL / XRP | Spot-Konto-Assets, bis eine eigene Bot-Order exakt bewiesen ist |

USD und USDC sind nicht austauschbar. Für ein Instrument wie `ADA-USD`
müssen drei Dinge gleichzeitig stimmen:

1. Das Instrument steht im authentifizierten OKX-Kontokatalog auf `live`.
2. Seine `tradeQuoteCcyList` enthält EUR oder USDC.
3. Für die USD-Marktpreise existiert ein beobachteter Spot-Kreuzkurs zur
   tatsächlichen Ausführungswährung. Eine 1:1-Annahme ist unzulässig.

## eToro-Zustandsfolge

1. NEXUS persistiert Kaufabsicht und `X-Request-Id` vor dem POST.
2. Der v3-Endpunkt nimmt die Order asynchron mit HTTP 202 an.
3. NEXUS fragt den Status getrennt per `orderId` oder `referenceId` ab.
4. Fill und `positionId` bleiben `AWAITING_POSITION_CONFIRMATION`, bis dieselbe
   ID im Depot oder in der vollständigen Historie bewiesen ist.
5. Erst dann wird die Position BOT/AUTO und der Fill in Geldpfad/Ledger
   übernommen. Ein bereits importierter OBSERVE-Datensatz wird hochgestuft,
   nicht addiert.

`OrderStatusUnklar` ist kein Netzausfall. Der Scanner meldet daher nicht mehr
„VERBINDUNG VERLOREN“, sendet keinen zweiten Kauf und lässt die persistente
Kontosperre aktiv, bis der Hintergrundworker den Fall geklärt hat.

## OKX-Eigentumsregel

Ein Spot-Saldo ist keine Position. BOT_MANAGED entsteht ausschließlich aus
der Beweiskette `clOrdId/tag → ordId → tradeId → ausgeführte Menge`. Der Anteil
des Kontos, der diese bewiesene Menge übersteigt, bleibt ACCOUNT_ASSET. Nur ein
Fehlbestand unterhalb der bewiesenen Botmenge oder eine offene, ungeklärte
Botorder sperrt neue Einstiege.

## Abgleich mit der offiziellen Dokumentation

- eToro v3 Open Order: https://api-portal.etoro.com/api-reference/trading--demo/submit-an-order-for-asynchronous-processing
- eToro Eligibility: https://api-portal.etoro.com/api-reference/trading--real/check-instrument-trading-eligibility
- eToro Rate Limits: https://api-portal.etoro.com/core/getting-started/rate-limits
- OKX API v5: https://www.okx.com/docs-v5/en/

Verwendete Regeln: eToro 202 ist nur Annahme, Status 3/5 sind ausgeführt,
4/10 terminal abgelehnt bzw. teilweise ausgeführt; `orderId` und
`referenceId` werden getrennt abgefragt. OKX verlangt in den betroffenen
Regionen `tradeQuoteCcy` aus der kontospezifischen `tradeQuoteCcyList`; bei
Spot ist `volCcy24h` in der Markt-Quotewährung angegeben. Der Orders-WebSocket
liefert keinen verlässlichen Startvollstand, deshalb bleibt REST der
autoritative Start- und Kontrollsnapshot.
