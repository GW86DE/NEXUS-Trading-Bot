# Installation und Update auf NEXUS 9.0.6

## Sicheres Update

1. Bot und WebUI stoppen.
2. Den vollständigen bisherigen NEXUS-Ordner sichern.
3. Das ZIP in einen neuen Ordner entpacken.
4. Installation und Migration ausführen:

```bash
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
./Nexus_Einrichten.sh --test
./Pi_Service_Aktivieren.sh
```

Nicht nur eine einzelne alte `crypto_positions.json` austauschen. Positionsbuch,
Ledger, Orderregister und Broker-Fingerabdruck bilden zusammen den Nachweis.
Die Migration übernimmt sie nach den vorhandenen Sicherheitsregeln.

## Kontrolle nach dem Start

- eToro: Ein neuer Kauf darf während kurz verzögerter Depotdarstellung
  `AWAITING_POSITION_CONFIRMATION` melden, aber nicht sofort „extern“.
- OKX: BTC, ETH, SOL und XRP stehen unter Konto-Guthaben als **KONTO-ASSET**.
- OKX Bot-Positionen: Nur neue, exakt belegte AUTO-Positionen erscheinen dort.
- Logbuch: regelmäßige Guthaben-/Kryptozyklusmeldungen; veraltete Accountdaten
  dürfen nicht durch Ping/Pong als frisch gelten.
- Telegram lässt sich weiterhin ohne Botneustart in der WebUI schalten.

Demo/Live wird durch das Update nicht automatisch umgestellt. Zugangsdaten
liegen ausschließlich lokal und sind nicht Bestandteil des Release-ZIP.

