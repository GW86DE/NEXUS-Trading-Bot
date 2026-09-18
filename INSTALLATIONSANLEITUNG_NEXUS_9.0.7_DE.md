# Installation und Update auf NEXUS 9.0.7

## Sicheres Update auf dem Raspberry Pi 5

1. Aktienbot, Kryptoworker und WebUI kontrolliert stoppen.
2. Den vollständigen bisherigen NEXUS-Ordner sichern. Insbesondere
   `etoro_reconciliation.json`, Positionsbücher, Ledger, Orderregister und
   Zugangsdaten nicht einzeln verwerfen.
3. Das 9.0.7-ZIP in einen neuen Ordner entpacken.
4. Installation, Migration und Offline-Tests ausführen:

```bash
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
./Nexus_Einrichten.sh --test
```

5. Erst danach die Dienste aktivieren:

```bash
./Pi_Service_Aktivieren.sh
```

## Erwartete Einstellungen

- eToro: PAPER/DEMO
- OKX: DEMO und SPOT/Cash
- OKX primäre Quote: EUR
- OKX zusätzliche Ausführungswährung: USDC
- USD, USDT und TRY: keine Standard-Bot-Cashwährungen

Alte `allowed_quote_ccy`-Einträge aus 9.0.x werden beim Laden auf EUR/USDC
bereinigt. Zugangsdaten bleiben lokal und sind nicht im ZIP enthalten.

## Kontrolle nach dem ersten Start

- Bei einem verzögerten eToro-Kauf steht im Log „ORDER ANGENOMMEN, STATUS NOCH
  OFFEN“, nicht „VERBINDUNG VERLOREN“.
- Währenddessen ist kein zweiter eToro-Kauf erlaubt.
- Nach Brokerbestätigung erscheint genau eine BOT/AUTO-Position mit der
  richtigen Menge und `positionId`.
- OKX zeigt BTC/ETH/SOL/XRP aus dem Altbestand als Konto-Assets.
- Nur nachgewiesene neue Botmengen stehen unter Bot-Positionen.
- Ein Unified-USD-Instrument nennt im Audit getrennt Marktquote,
  `tradeQuoteCcy` und beobachteten Umrechnungskurs.

Das Update aktiviert weder eToro LIVE noch OKX LIVE automatisch.
