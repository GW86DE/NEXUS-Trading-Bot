# TradingBot NEXUS 9.0.3 installieren oder aktualisieren

## 1. Laufende Version sichern

```bash
cd ~/Georg/TradingBot_v9.0.2_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
sudo systemctl stop tradingbot-webui.service 2>/dev/null || true
cp -a . ~/Georg/Backup_TradingBot_v9.0.2_$(date +%Y%m%d_%H%M)
```

## 2. Neues Paket entpacken

```bash
cd ~/Georg
unzip TradingBot_v9.0.3_NEXUS.zip
cd TradingBot_v9.0.3_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
```

## 3. Installieren und Zustände übernehmen

```bash
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration übernimmt unter anderem Zugangsdaten, Entscheidungsdatenbank,
Trade-Ledger, Positionsbücher, Order-/Fill-Zuordnung, Reconciliation,
Risikozustände, Krypto-Strategiemodus und Universumszustände. eToro bleibt
PAPER und OKX bleibt DEMO. LIVE wird nicht automatisch aktiviert.

## 4. Offline prüfen

```bash
cat VERSION.txt
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
./Nexus_Einrichten.sh --test
./Pi_Service_Unit_Pruefen.sh
```

Erwartete Version: `9.0.3-NEXUS`. Die Offlineprüfungen senden keine echten
Orders und keine echten Telegram-, GPT- oder News-Anfragen.

## 5. Dienste starten

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Starten.sh
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

Danach prüfen:

- Kopfzeile zeigt `9.0.3`;
- eToro steht auf PAPER und OKX auf DEMO;
- auf Handy/Tablet erscheint oben das Dropdown **Menü**;
- auf Laptop/Desktop bleibt die normale Menüleiste sichtbar;
- Dashboard zeigt **Aktien-Handelsbereitschaft** zunächst gesperrt;
- neue Aktienkäufe werden frühestens 15 Minuten nach Start freigegeben;
- **Trades** trennt bestätigte offene Positionen von **Klärung nötig**;
- OKX- und eToro-Brokerbestände stimmen mit den bestätigten offenen Trades
  überein, bevor neue Käufe freigegeben werden.

## Rückweg

Vor dem Rückweg `/cryptopause` verwenden und offene Orders und Positionen bei
beiden Brokern direkt prüfen. Den vollständigen 9.0.3-Ordner sichern. Danach
kann der gesicherte 9.0.2-Ordner wieder aktiviert werden. Eine neuere
9.0.3-Datenbank sollte nicht rückwärts über die ältere Version kopiert werden.
