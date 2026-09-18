# NEXUS 9.0.8 – eToro-Scanner-Patch

## Fehlerbild aus 9.0.7

Im Log standen nacheinander:

1. `BrokerFehler: eToro Ratenfenster ... nicht rechtzeitig frei`
2. `NameError: name 'OrderStatusUnklar' is not defined`

Der zweite Fehler war der eigentliche Absturz. Der in 9.0.7 ergänzte
`except OrderStatusUnklar`-Zweig war vorhanden, die Exceptionklasse aber nicht
in `live_trader.py` importiert. Python konnte deshalb bei der Behandlung des
vorherigen Brokerfehlers den Ausnahmezweig nicht auswerten.

## Korrektur

- `OrderStatusUnklar` wird aus der zentralen Broker-Schnittstelle importiert.
- Ein eigener Regressionstest lädt `live_trader.py` und verifiziert exakt
  diesen Namen.
- Das eToro-Depot wird nicht mehr für jedes gescannte Instrument vor der
  Signalauswertung erneut geladen.
- Erst ein Kandidat, der den BUY-Pfad erreicht, erhält eine frische
  Positionsprüfung. Die abschließende Prüfung direkt vor der Order bleibt
  unverändert aktiv.
- Die Ratenbegrenzer verteilen API-Aufrufe gleichmäßig über das Zeitfenster.
  Dadurch entstehen keine schnellen Aufrufbursts mit anschließender langer
  Blockade.

## Sicherheitswirkung

Die Reduzierung der Depotabfragen schwächt den Doppelorder-Schutz nicht. Jeder
tatsächliche BUY-Kandidat wird weiterhin beim Broker geprüft und unmittelbar
vor dem Submit erneut durch das vollständige Money-Path-Gate geführt.

Ein normaler Ratenlimitfehler gilt außerdem nicht als Verbindungsabbruch. Der
Scanner überspringt den betroffenen Datenabruf kontrolliert, während die
Brokerverbindung bestehen bleibt.

Die OKX-Bestandsanzeige im selben Log bestätigt die neue 9.0.7-Zuordnung:
`ACCOUNT_ASSET`, `BOT_MANAGED` und `CASH` werden getrennt ausgewiesen.

