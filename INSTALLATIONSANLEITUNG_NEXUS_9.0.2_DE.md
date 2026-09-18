# TradingBot NEXUS 9.0.2 installieren oder aktualisieren

## 1. Aktuelle Version sichern

NEXUS 9.0.2 wird in einen neuen Ordner installiert. Der bisherige Ordner wird
nicht überschrieben.

```bash
cd ~/Georg/TradingBot_v9.0.1_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
sudo systemctl stop tradingbot-webui.service 2>/dev/null || true
cp -a . ~/Georg/Backup_TradingBot_v9.0.1_$(date +%Y%m%d_%H%M)
```

## 2. Paket entpacken

```bash
cd ~/Georg
unzip TradingBot_v9.0.2_NEXUS.zip
cd TradingBot_v9.0.2_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
```

## 3. Installieren und Zustände übernehmen

```bash
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration übernimmt insbesondere Zugangsdaten, Entscheidungsdatenbank,
Trade-Ledger, Positionszustände, Reconciliation, Krypto-Strategiemodus sowie
Universums- und Risikozustände. eToro bleibt PAPER und OKX bleibt DEMO; LIVE
wird niemals automatisch aktiviert.

## 4. Offline prüfen

```bash
cat VERSION.txt
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
./Nexus_Einrichten.sh --test
./Pi_Service_Unit_Pruefen.sh
```

Erwartete Version: `9.0.2-NEXUS`. Die Prüfungen senden keine echten Orders und
keine echten Telegram-Nachrichten.

## 5. Dienste starten

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Starten.sh
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

Danach prüfen:

- Kopfzeile zeigt `9.0.2`;
- eToro steht auf PAPER und OKX auf DEMO;
- unter **Universum** bleiben 20 Kernwerte und bis zu 30 dynamische Werte;
- USD-Paare werden nicht mehr mit „nur EUR, USDC erlaubt“ verworfen;
- **Trades** zeigt keine nach zwei bestätigten Leersnapshots verwaisten offenen
  OKX-Einträge mehr;
- vorhandene ungeklärte Coin-Bestände werden als Restbestand sichtbar und
  nicht automatisch verkauft.

## Rückweg

Vor einem Rückweg `/cryptopause` verwenden, offene Orders und Positionen bei
beiden Brokern prüfen und den vollständigen 9.0.2-Ordner sichern. Anschließend
kann der gesicherte 9.0.1-Ordner wieder aktiviert werden.
