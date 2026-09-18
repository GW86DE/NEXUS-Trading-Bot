# Installation und Update auf NEXUS 9.0.9

## Sicheres Update auf dem Raspberry Pi 5

1. Die laufende Version stoppen und deaktivieren:

```bash
cd ~/Georg/TradingBot_v9.0.8_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
```

2. NEXUS 9.0.9 daneben entpacken und installieren:

```bash
cd ~/Georg
unzip TradingBot_v9.0.9_NEXUS.zip
cd TradingBot_v9.0.9_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh
./Pi_Installieren.sh
```

3. Einstellungen und lokale Zustände übernehmen:

```bash
./.venv/bin/python settings_migration.py --auto
```

Die Migration aktiviert weder eToro LIVE noch OKX LIVE. Alte
`crypto_positions.json`-Zeilen ohne echte OKX-`tradeId` bleiben vorhanden,
werden aber sicher als `BEOBACHTEN/ACCOUNT_ASSET` behandelt.

4. Vor dem Dienststart offline prüfen:

```bash
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
./Nexus_Einrichten.sh --test
```

5. Dienste aktivieren:

```bash
./Pi_Service_Aktivieren.sh
```

## Kontrolle nach dem Start

- Unter **Positionen/Trades** dürfen SOL und alte ETH-Mengen ohne echte
  `clOrdId → ordId → tradeId`-Kette nicht mehr als offene Bot-Trades stehen.
- Im Log muss `OKX-Guthaben` die Klassen `ACCOUNT_ASSET`, `BOT_MANAGED` und
  `CASH` getrennt nennen.
- Im Universum muss `Kern X/20` erscheinen. X darf kleiner als 20 sein, wenn
  das Konto oder die Qualitätsfilter aktuell weniger Werte zulassen.
- Im Entscheidungslog muss ein Krypto-HOLD als `NO_SIGNAL` mit konkreten
  Regelwerten erscheinen. Systemprotokoll und Entscheidungslog müssen beide
  Inhalte laden.
- Bei einer Demo-Order müssen `clOrdId`, `ordId`, mindestens eine echte
  `tradeId`, Nettomenge und Gebühren im Entscheidungs-/Trade-Ledger stehen.
- Zwei Teilfills mit derselben `ordId` sind korrekt eine Order. Zwei
  verschiedene `ordId` für dieselbe Entscheidung wären ein Fehler.
- eToro darf einen unklaren Auftrag nicht als globalen Verbindungsverlust
  melden. Der private Stream ist nur Beschleuniger; der REST-Abgleich muss die
  `positionId` bestätigen.

Bis diese Punkte mit echten Demo-Kontodaten geprüft sind, eToro auf PAPER und
OKX auf DEMO belassen.

