# NEXUS 8.2.2 installieren – sichere Kurz-Anleitung

Diese Korrekturversion ersetzt 8.2.1. Sie wird in einen **neuen Ordner**
entpackt; WireGuard bleibt unveraendert.

## 1. Alte TradingBot-Dienste anhalten

```bash
sudo systemctl stop tradingbot-pi5.service tradingbot-webui.service
```

## 2. ZIP neu entpacken und vollständig testen

```bash
cd ~/Georg
unzip TradingBot_v8.2.2_NEXUS.zip
cd TradingBot_v8.2.2_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
```

Weiter nur, wenn der Installer mit **„INSTALLATION UND TESTS ERFOLGREICH“**
endet. Bei einem Testfehler keine vorhandenen Desktop-Starter ausfuehren.

## 3. Einstellungen übernehmen und WebUI einschalten

```bash
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python webui_setup.py
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

Die Dienstpfad-Ausgabe muss `TradingBot_v8.2.2_NEXUS` zeigen; die WebUI zeigt
`TradingBot 8.2.2 NEXUS`.

Wenn die WebUI-Unit fehlt, erstellt `Pi_WebUI_Aktivieren.sh` sie aus der
Vorlage dieser Version. Falls eine vorhandene Unit noch auf einen alten Ordner
zeigt, startet sie bewusst nicht – dann den Installer in diesem Ordner erneut
vollstaendig ausfuehren.

## 4. Verbindungen testen, dann bewusst aktivieren

```bash
./Nexus_Einrichten.sh --test
./Nexus_Starten.sh --einmal
./Pi_Service_Aktivieren.sh
```

Der Einmal-Zyklus ist eine Prüfung und erzeugt keine Live-Order, solange LIVE
nicht separat bewusst aktiviert und freigegeben wurde.
