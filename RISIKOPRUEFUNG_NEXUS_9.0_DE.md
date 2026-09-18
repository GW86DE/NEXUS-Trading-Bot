# Risikoprüfung NEXUS 9.0

## Ergebnis

Der neue Modus ist technisch vom eToro-Handel und von der bisherigen
NEXUS-Kryptostrategie getrennt. Er ist zur Laufzeit abschaltbar und bindet jede
Position dauerhaft an ihre Einstiegsstrategie. Er darf dennoch zunächst nur
im OKX-Demomodus beurteilt werden: Die offizielle SampleStrategy ist eine
Beispielvorlage und besitzt keinen belegten Gewinnvorteil für dein Universum.

## Erkannte Risiken und Gegenmaßnahmen

| Risiko | Gegenmaßnahme in NEXUS 9.0 | Restrisiko |
|---|---|---|
| 5m-Signale erzeugen häufige Trades | Spread-, Gebühren-, Edge-, Liquiditäts- und Orderbuchprüfung bleiben aktiv | Seitwärtsphasen können trotz Filtern Verluste und Gebühren erzeugen |
| fester Stop von −10 % ist relativ weit | zentraler Risikotopf berechnet die Positionsgröße aus dem Abstand zum Stop; brokerseitiger Stop sofort nach Fill | Gaps, Slippage und technische Brokerprobleme können den Verlust vergrößern |
| ROI sinkt nach 30/60 Minuten | Position wird in jedem Zyklus netto nach Ein-/Ausstiegsgebühren mit ihrem gespeicherten ROI-Plan geprüft | bei Pi-/Netzausfall greifen 2 %/1 % nicht; nur −10 % und das gebührenbereinigte 4-%-Brokerziel bleiben |
| Moduswechsel verändert offene Trades | unveränderlicher Strategie-Snapshot je Position | unvollständige Altpositionen müssen beobachtet oder manuell geklärt werden |
| falsche Strategieversion nach Update | exakter Versions- und Hashvergleich, sonst `BEOBACHTEN` + Telegram | die Position wird dann nicht mehr aktiv durch die Strategie verkauft |
| GPT/News könnten indirekt beeinflussen | im SampleStrategy-Pfad kein GPT, keine News, keine AI-Attention und keine Second Opinion | Universums-/Marktdaten selbst können fehlerhaft oder verspätet sein |
| Umschalter beschädigt/Datei korrupt | fail-closed auf `CRYPTO_PAUSED`; eToro bleibt unabhängig | vorhandene Positionen benötigen weiterhin Broker- und Verbindungszugriff |
| Doppelorder oder unklarer Brokerstatus | bestehende Ownership-, Idempotenz-, Pending-Order- und Reconciliation-Sperren bleiben aktiv | externe manuelle Änderungen können eine automatische Zuordnung verhindern |
| Backtest wirkt besser als Realität | nächstes Kerzen-Open, Gebühren, Slippage, konservative Stop-Priorität | OHLC kennt keine exakte Intrabar-Reihenfolge oder echte Orderbuchtiefe |
| SampleStrategy ändert sich upstream | NEXUS speichert seine feste Strategieversion und Parameter-Hash | spätere offizielle Änderungen werden nicht automatisch übernommen |

## Notfallverhalten

`/cryptopause` oder **Krypto pausieren** in der WebUI stoppt neue OKX-Einstiege
sofort. Bereits vorhandene OKX-Positionen behalten Broker-Schutz,
Reconciliation und ihren positionsgebundenen Ausstieg. eToro läuft unabhängig
weiter. Ein kompletter Verkauf aller Kryptopositionen wird bewusst nicht durch
den Pausenbefehl ausgelöst.

## Bedingungen vor einem späteren Live-Einsatz

1. mehrere Wochen stabiler OKX-Demobetrieb ohne ungeklärte Orders;
2. Backtest und anschließender Forward-Test auf genau den zugelassenen Paaren;
3. Auswertung getrennt nach `entry_strategy_mode`, Strategieversion und
   Ausstiegsgrund;
4. Prüfung von Gebühren, Slippage, maximalem Drawdown und Verlustserien;
5. kontrollierter Neustarttest mit offenen Standard- und Sample-Positionen;
6. Live-Aktivierung weiterhin separat und zeitlich begrenzt freigeben.
