# TradingBot NEXUS 9.0.4 installieren oder aktualisieren

## 1. Alte Version stoppen und vollständig sichern

```bash
cd ~/Georg/TradingBot_v9.0.3_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
sudo systemctl stop tradingbot-webui.service 2>/dev/null || true
cp -a . ~/Georg/Backup_TradingBot_v9.0.3_$(date +%Y%m%d_%H%M)
```

Offene Positionen und Orders vorher direkt bei eToro und OKX prüfen. Die alte
Version nicht löschen, bevor 9.0.4 vollständig geprüft wurde.

## 2. Neues Paket entpacken

```bash
cd ~/Georg
unzip TradingBot_v9.0.4_NEXUS.zip
cd TradingBot_v9.0.4_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
```

## 3. Installieren und Zustände übernehmen

```bash
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration übernimmt Zugangsdaten, Entscheidungsdatenbank, Trade-Ledger,
Positionsbücher, Order-/Fill-Zuordnung, Reconciliation, Risikozustände,
Strategiemodus und Universumszustände. Sie aktiviert weder eToro LIVE noch OKX
LIVE. Die neue Installation bleibt zunächst PAPER/DEMO.

Die Datei `crypto_positions.json` nicht manuell aus einer anderen Version
ersetzen. Gerade der SOL-Altfall benötigt die gemeinsam migrierten Ledger- und
Positionsdaten, damit NEXUS ihn nach zwei vollständigen OKX-Abgleichen sicher
einordnet.

## 4. Offline prüfen

```bash
cat VERSION.txt
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
./Nexus_Einrichten.sh --test
./Pi_Service_Unit_Pruefen.sh
```

Erwartete Version: `9.0.4-NEXUS`. Der Volltest muss `VOLLTEST OK` melden. Die
Offlineprüfungen senden keine echten Orders, Telegram-, GPT- oder News-Anfragen.

## 5. Dienste kontrolliert starten

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Starten.sh
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

Danach in der WebUI prüfen:

- Kopfzeile zeigt `9.0.4`;
- eToro steht auf PAPER und OKX auf DEMO;
- Handy/Tablet zeigen das Dropdown **Menü**, Desktop die Menüleiste;
- unter **Einstellungen** lässt sich Telegram ohne Neustart schalten;
- eToro führt keinen lokalen MSFT-Trade ohne exakte aktuelle `positionId`;
- OKX-Universum zeigt bei Unified-USD-Instrumenten die tatsächlich gewählte
  Handelswährung;
- SOL erscheint nach dem sicheren Alt-Ledger-Abgleich als externer Bestand und
  wird weder als Cash noch als automatisch verwalteter Trade behandelt;
- neue OKX-Trades besitzen positive Stop-/Zielwerte mit
  `Stop < Einstieg < Ziel`.

## 6. Telegram-Laufzeitschalter

Unter **Einstellungen → Telegram** zuerst Token und Chat-ID speichern. Danach
kann Telegram mit dem Laufzeitschalter aktiviert oder deaktiviert werden. Die
Änderung gilt sofort für Versand und Befehlsabfrage. Identische automatische
Meldungen werden 30 Sekunden gesammelt und nur einmal versendet.

## Rückweg

Vor einem Rückweg Krypto-Neueinstiege pausieren und offene Brokerorders direkt
prüfen. Den vollständigen 9.0.4-Ordner sichern. Danach kann der gesicherte
9.0.3-Ordner wieder aktiviert werden. Die neuere Datenbank und Zustandsdateien
nicht rückwärts über 9.0.3 kopieren.
