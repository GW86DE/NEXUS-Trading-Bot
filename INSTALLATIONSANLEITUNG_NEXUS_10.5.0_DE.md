# NEXUS 10.5.0 – Installation auf dem Raspberry Pi 5

**Build:** `10.5.0-PULSAR-SOURCES-MEASUREMENT` · Basis: 10.4.0-NEXUS (Rev 2) · Diagnose 1.9.0

PULSAR bekommt Quellen ohne Reddit-Schlüssel (StockTwits, FINRA Short Interest,
relatives Volumen aus Quote und Stundenkerzen, eigene Reddit-Basislinie), eine
neue Bewertung (Auslöser plus zwei von drei Bestätigungen) und eine
Vorwärtsmessung, die jeden Auslöser nach 1, 3, 5 und 10 Handelstagen nachmisst.
Die PULSAR-Seite zeigt das Messergebnis zuerst. Die Diagnose enthält die
Messungen, und jede abgeschlossene Diagnose lässt sich in der WebUI als
aufbereiteter Bericht öffnen.

## Phase 1 – Paketpruefung

```bash
bash "$HOME/Downloads/NEXUS_10.5.0_Installieren.sh" --paket-pruefen
```

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.5.0_Installieren.sh"
```

Der Installer entpackt, führt den vollständigen Volltest aus und startet die
Dienste nur, wenn alle Tests bestehen. Bestehende Daten werden nicht umgebucht;
die Tabelle `attention_outcomes` entsteht beim ersten Zugriff in
`pulsar_research.sqlite`.

## Phase 3 – PULSAR einstellen

WebUI → PULSAR → Betrieb:

- **Aus**: keine Quellenabrufe, keine Messung, keine Nominierung.
- **Beobachten und messen**: Quellen, Messung, Telegram-Hinweise, keine Nominierung.
- **Messen und handeln (persönliche Freigabe)**: zusätzlich ein Kaufplan je Tag zur
  persönlichen Bestätigung; höchstens ein offener Trade, Zeitstopp 10 Handelstage.

Für „messen und gleich handeln" wählst du den dritten Modus. Die Messung läuft in
beiden aktiven Modi gleich.

**Neue Quellen einschalten:** Setze die Haken **„StockTwits nutzen"** und
**„FINRA Short Interest nutzen"** und klicke „Übernehmen". Beide sind wie
Tradestie und GPT-Websuche bewusst opt-in: ausgeschaltet bleiben ihre Felder auf
den Karten UNKNOWN, und es gibt keinen externen Abruf. Der Volumen-Auslöser aus
Quote und Stundenkerzen braucht keinen Schalter.

## Phase 4 – Abnahme

1. **PULSAR-Seite**: oben die Plakette „ZU WENIG DATEN" (normal in den ersten
   Wochen), darunter „0 Auslöser gemessen" bzw. nach dem ersten Auslöser eine
   Zeile; unter „Quellen und Budget" Chips für ApeWisdom, Tradestie, StockTwits,
   FINRA und Volumen. Ein roter StockTwits-Chip mit „backoff" bedeutet meist,
   dass der DNS-Server des Pi `api.stocktwits.com` nicht auflöst (siehe
   KNOWN_ISSUES); NEXUS führt das als UNKNOWN weiter.
2. **Kandidaten**: jede Karte zeigt Auslöser, drei Bestätigungs-Chips
   (n von 3), Existenzrisiko, Squeeze-Merkmal und die StockTwits-Zeile.
3. **Diagnose**: Sofortdiagnose starten; nach Abschluss erscheint „Bericht
   öffnen". Der Bericht zeigt Laufabschluss, Bereitschaft je Broker,
   PULSAR-Messung, Befunde nach Stufe, Blocker und Quellen. In der ZIP liegt
   zusätzlich `ZUSAMMENFASSUNG.json`.
4. **Systemprotokoll**: Zeilen „PULSAR-Messung <SYMBOL>: Ausloeser …" nach dem
   ersten Auslöser.

## Optionen (unveraendert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
