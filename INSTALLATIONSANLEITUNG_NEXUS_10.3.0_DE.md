# NEXUS 10.3.0 – Installation auf dem Raspberry Pi 5

**Build:** `10.3.0-PULSAR-HYPE-LANE` · Basis: 10.2.2-NEXUS · Diagnose 1.8.1

Diese Version macht zwei Dinge:

1. **OKX-Freigabe (Prio):** Die Diagnose vom 17.09. zeigte zwei Rest-Blocker.
   Der Risiko-Abgleich kannte die neue Composite-Beweismethode nicht (Trade 73
   blieb „offen" trotz gespeicherter EUR-Bewertung), und die
   Bestandsklassifizierung ordnete das OKX-Demo-Startguthaben (1 BTC / 10 ETH)
   zwei winzigen Staub-Restzeilen zu und sperrte alle Käufe. Beides sind reine
   Lesart-Korrekturen — es wird nichts umgebucht oder verkauft. Nach dem Update
   verschwinden die Meldungen „Risiko-Ergebnisabgleich ledger:73 bleibt offen"
   und „NEUE EINSTIEGE GESPERRT: BTC/ETH …"; **OKX kauft wieder (Demo)**.
2. **PULSAR 2.0 (Hype-Spur):** Schnelle Squeeze-Erkennung mit Telegram-Alarm,
   1 offener Trade, 1 Nominierung/Tag, Zeitstop 10 Handelstage,
   Universums-Zubringer und X-Budget-Umschichtung — Details im CHANGELOG.
   PULSAR bleibt über AUS / BEOBACHTEN / FREIGABE abschaltbar; keine Order
   ohne deine zweistufige Telegram-Bestätigung.

## Phase 1 – Paketpruefung

```bash
bash "$HOME/Downloads/NEXUS_10.3.0_Installieren.sh" --paket-pruefen
```

Erwartet: `PAKETPRUEFUNG OK: NEXUS 10.3.0, ... Quellhashes geprueft.`

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.3.0_Installieren.sh"
```

Der laufende 10.2.2-Stand ist die Datenquelle; alle Buchungen bleiben erhalten.

## Phase 3 – Abnahme (wenige Minuten nach dem Start)

1. Die Log-Meldung „Risiko-Ergebnisabgleich ledger:73 bleibt offen" kommt
   NICHT mehr (höchstens einmal direkt beim Start, falls der Abgleich noch
   läuft — danach still).
2. Die Meldung „NEUE EINSTIEGE GESPERRT: BTC …; ETH …" verschwindet;
   stattdessen zählt die Guthabenzeile BTC/ETH als freie Konto-Assets.
3. OKX-Käufe sind wieder möglich (Demo).
4. PULSAR-Seite in der WebUI zeigt „Hype-Spur" mit den neuen Kriterien.

Optional danach die 30-Minuten-Diagnose: `./NEXUS_10.3.0_Diagnose_Starten.sh`
im neuen Quellordner.

## Hinweise zur Hype-Spur

- Im Modus BEOBACHTEN gibt es nur Telegram-Alarme, keine Nominierungen.
- Im Modus FREIGABE folgt auf einen Alarm ein Kaufplan zur persönlichen
  zweistufigen Bestätigung; ohne Bestätigung passiert nichts.
- Fehlsignale (Pump&Dump) sind möglich — deshalb Demo, 1 Trade,
  0,25 % Risiko, 30-Tage-Verlustbremse.

## Optionen (unveraendert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
