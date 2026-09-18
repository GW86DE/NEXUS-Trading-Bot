# NEXUS 8.3.0 auf Raspberry Pi 5 installieren

8.3.0 wird in einen neuen Ordner entpackt. Das alte Release und dessen Daten
bleiben als Rueckweg erhalten. WireGuard wird nicht veraendert. Der Installer
startet nach erfolgreicher Installation keinen Handelsdienst.

## 1. Dienste stoppen und 8.2.2 sichern

```bash
cd ~/Georg
sudo systemctl stop tradingbot-pi5.service tradingbot-webui.service
cp -a TradingBot_v8.2.2_NEXUS TradingBot_v8.2.2_NEXUS_Sicherung_$(date +%Y%m%d-%H%M)
```

## 2. Neues ZIP entpacken und installieren

```bash
cd ~/Georg
unzip TradingBot_v8.3.0_NEXUS.zip
cd TradingBot_v8.3.0_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
```

`Pi_Installieren.sh` niemals mit `sudo` aufrufen. Nur wenn die letzte Zeile
`INSTALLATION UND TESTS ERFOLGREICH - BOT NOCH NICHT GESTARTET` lautet,
weiterarbeiten. Bei einem Testfehler werden Dienstdateien und Desktop-Starter
nicht auf 8.3.0 umgestellt.

## 3. Einstellungen und Daten uebernehmen

```bash
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python crypto_diagnose.py --voll
./Pi_Service_Unit_Pruefen.sh all
```

Die Datenbankmigration ist idempotent: vorhandene Entscheidungen und die
sieben ungeklärten Alt-Trades bleiben erhalten und werden nicht geraten einer
Entscheidung zugeordnet. Vorhandene Demo-Coins bleiben externe Bestaende und
werden weder verkauft noch automatisch uebernommen.

## 4. WebUI pruefen und Dienste bewusst aktivieren

```bash
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

In Status und WebUI muessen `8.3.0` und der Pfad
`TradingBot_v8.3.0_NEXUS` erscheinen. Danach Einstellungen, Brokerprofil und
den Schalter fuer die Freigabe bei Terra `kritisch` pruefen. Erst anschliessend:

```bash
./Pi_Service_Aktivieren.sh
```

LIVE-Handel bleibt eine separate bewusste Freigabe. Diagnose und Installation
fuehren keine Brokerorder aus.

## Rueckweg

```bash
sudo systemctl stop tradingbot-pi5.service tradingbot-webui.service
cd ~/Georg/TradingBot_v8.2.2_NEXUS_Sicherung_YYYYMMDD-HHMM
./Pi_Installieren.sh
```

Nur nach dessen erfolgreichem Volltest und eigener Kontrolle wieder mit
`./Pi_Service_Aktivieren.sh` aktivieren. Eine unter 8.3.0 erweiterte
`decision_history.sqlite` nicht ueber eine aeltere Datenbank kopieren; beide
Release-Ordner getrennt sichern.
