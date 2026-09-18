# NEXUS 10.2.0 – Installation auf dem Raspberry Pi 5

**Build:** `10.2.0-STRATEGY-EXPANSION-AND-RESILIENCE` · Basis: 10.1.10-NEXUS (Rev. 2) · Diagnose 1.8.1

Der Installer ist selbstenthaltend und prueft das eingebettete Quellpaket (SHA256) sowie jede einzelne Datei gegen das Manifest, bevor irgendetwas veraendert wird. Neu in 10.2.0: Der Installer meldet sich sofort mit einem Banner (eine leere/abgeschnittene Datei bricht sichtbar ab), archiviert einen nachweislich inaktiven alten Zielstand automatisch mit `ALT_`-Praefix, und ein Fehlschlag im Offline-Test raeumt das eigene Staging selbst weg. Der Updateablauf sichert den bestehenden Stand, uebernimmt Einstellungen, Positionen, Orders/Fills, Risiko-, PULSAR- und Budgetzustaende und fuehrt den Volltest aus; erst danach starten die Dienste. LIVE wird nicht automatisch freigegeben.

## Phase 1 – Paketpruefung (keine Dienste, keine Brokeraktion)

```bash
bash "$HOME/Downloads/NEXUS_10.2.0_Installieren.sh" --paket-pruefen
```

Erwartete Ausgabe: `PAKETPRUEFUNG OK: NEXUS 10.2.0, ... Quellhashes geprueft.`

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.2.0_Installieren.sh"
```

Der Ablauf stoppt die Dienste, sichert den alten Stand, entpackt nach `~/Georg/TradingBot_v10.2.0_NEXUS`, uebernimmt die Daten, fuehrt den Volltest aus und startet Core und WebUI neu. Schlaegt ein Schritt fehl, bricht das Update ab und der alte Stand bleibt lauffaehig.

## Phase 3 – passive 30-Minuten-Diagnose (Abnahme)

```bash
cd "$HOME/Georg/TradingBot_v10.2.0_NEXUS"
./NEXUS_10.2.0_Diagnose_Starten.sh
```

Alternativ startet die Diagnose in der WebUI unter „Diagnose". Die fertige ZIP liegt unter `~/Downloads/NEXUS_Diagnosen/`.

## Phase 4 – Abnahmefragen

1. Zeigen die Einstellungen beide Strategie-Schalter (OKX mit 6 Modi, neuer Abschnitt „eToro Aktien-Strategiemodus"), Standard jeweils NEXUS Standard?
2. Bleibt bei zwei finanzierten Lanes (USDC+USD) der Risikoblock mit nativer Reihe gefuellt (`native_equity_ccy` z. B. `USD+USDC`), und loest reine Kursbewegung keinen Halt aus?
3. Fuehrt ein teilerfuellter eigener Take-Profit zu `EXIT_IN_PROGRESS` (nur der betroffene Coin gesperrt) statt zur OKX-weiten Kaufsperre?
4. Waechst `etoro_fee_snapshots.json` mit Belegen der offenen eToro-Positionen?

## Die sechs neuen Strategien ausprobieren (Demo)

1. WebUI → **Backtest** → Abschnitt **„Strategien einfach erklaert"** lesen (jede Strategie mit Kauf-/Verkaufsregel, Idee und Schwaeche).
2. Erst backtesten: „Backtest starten" — die Rangliste bewertet alle 11 Strategien auf deinem aktuellen Universum.
3. Dann schalten: **Einstellungen** → „OKX Krypto-Strategiemodus" bzw. „eToro Aktien-Strategiemodus" → Strategie anklicken → exakte Phrase `STRATEGIE AKTIVIEREN` eingeben.
4. Der Schalter gilt **nur fuer neue Einstiege** und wirkt sofort, ohne Neustart. Offene Positionen behalten ihre Einstiegsstrategie und werden von genau dieser wieder verkauft. Zurueck zu „NEXUS Standard" geht jederzeit ohne Phrase.
5. Alle Schutzmechanismen (Stop/Take-Profit beim Broker, Risiko-, Kosten-, News-Gates, Tagesbremse) bleiben unveraendert aktiv — die Strategien liefern nur das Signal.

## Optionen (unveraendert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
