# NEXUS 9.0.6 - Broker-Konsistenz und API-Abgleich

## eToro: Warum CRM und NVDA als extern erschienen

Die Orderauskunft enthielt bereits die exakten eToro-IDs und den Fill. Direkt
danach war dieselbe `positionId` jedoch noch nicht in der aktuellen
Depotantwort oder Trade-Historie sichtbar. NEXUS 9.0.5 deutete diese wenige
Sekunden lange, nicht atomare Brokerdarstellung sofort negativ. Beim naechsten
Depotabgleich sah der Bot zwar die Aktie, sein eigener Reconciliation-Datensatz
war aber bereits terminal. Dadurch erschien ein echter Botkauf als extern.

9.0.6 behandelt die drei API-Sichten getrennt:

1. `orderId`/`referenceId` beweisen die zugehoerige Orderauskunft.
2. `positionExecutions.positionId` beweist den exakten Fill-Bezug.
3. Depot oder Historie beweisen, ob diese Position aktuell offen oder bereits
   geschlossen ist.

Nach einem Fill wartet der Bot bis zu 300 Sekunden und verlangt mindestens
drei vollstaendige negative Snapshots, bevor er `UNPROVABLE` setzen darf.
Waehren dieser Zeit bleibt die Domaene gesperrt. Es wird weder erneut gekauft
noch anhand von Symbol und Menge eine Position erraten.

## OKX: Was der Account-Kanal wirklich liefert

Die beigefuegte OKX-V5-Dokumentation trennt den privaten `account`-Kanal vom
`orders`-Kanal. `cashBal`, `availBal` und `frozenBal` sind Salden eines Unified-
Kontos. Bei Spot gibt es keine separate offene Brokerposition wie bei einem
CFD-/Aktienbroker. Deshalb gilt:

| OKX-Datum | Bedeutung in NEXUS 9.0.6 |
|---|---|
| `cashBal` / `availBal` | Konto-Asset bzw. verfuegbarer Saldo |
| `frozenBal` | durch Orders gebundener Anteil des Saldos |
| `orders`-Event + `ordId`/`clOrdId` | Orderzustand, noch kein lokaler Strategieeintrag |
| eigener Fill + persistierte Strategie | aktiv verwaltete Botposition |
| alter `BEOBACHTEN`-/Legacy-Eintrag | Herkunftshinweis, keine offene Botposition |

Damit werden BTC, ETH, SOL und XRP aus dem gezeigten OKX-Konto als
**KONTO-ASSET** geführt. Sie stehen im Konto zur Verfügung, sind aber keine
offenen Bottrades. SOLs historischer Eintrag mit Stop/Ziel 0 bleibt
nachvollziehbar, taucht aber nicht mehr unter aktiven Botpositionen auf und
wird nicht automatisch verkauft.

## WebSocket-Abgleich

Die OKX-Dokumentation unterscheidet Transport-Lebendigkeit von fachlichen
Account-Events. 9.0.6 speichert deshalb getrennte Zeitpunkte für letzte
Nachricht, letzten Account-Stand und letztes Orderereignis. Ein Text-`pong`
beweist nur die Leitung und kann keine Kontodaten auffrischen.

Ein `eventType=snapshot` ersetzt den lokalen Accountcache vollständig;
Updates werden als Delta verarbeitet und Nullsalden entfernt. Zusätzlich
liest NEXUS regelmäßig einen autoritativen REST-Snapshot. So bleiben bei
partiellen WebSocket-Nachrichten keine alten Währungen im Cache.

## Unveränderte Sicherheitsgrenzen

- Spot und `tdMode=cash`; kein Margin-, Future- oder Swap-Handel.
- Keine automatische Wiederholung einer unklaren Geldorder.
- Exakte `clOrdId`, Order-ID und Fill-ID für Wiederherstellung.
- Fremde oder nur beobachtete Assets werden niemals automatisch verkauft.
- Fehlende oder veraltete Brokerdaten sperren neue Käufe fail-closed.

