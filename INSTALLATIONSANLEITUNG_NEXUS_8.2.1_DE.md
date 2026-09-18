# NEXUS 8.2.1 installieren – sichere Kurz-Anleitung

Diese Version ersetzt den fehlgeschlagenen 8.2.0-Updateversuch. Sie wird in
einen **neuen Ordner** installiert. WireGuard wird dabei nicht veraendert.

## 1. Alte Dienste sicher anhalten

```bash
sudo systemctl stop tradingbot-pi5.service tradingbot-webui.service
```

## 2. ZIP neu entpacken und installieren

```bash
cd ~/Georg
unzip TradingBot_v8.2.1_NEXUS.zip
cd TradingBot_v8.2.1_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
```

Der Installer muss mit **„INSTALLATION UND TESTS ERFOLGREICH“** enden. Bei
einem Testfehler nichts starten: Die vorhandenen Desktop-Symbole koennen dann
noch auf den alten Ordner zeigen.

## 3. Einstellungen uebernehmen und WebUI einschalten

```bash
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python webui_setup.py
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

In der Dienstpfad-Ausgabe muss `TradingBot_v8.2.1_NEXUS` stehen. Die WebUI
zeigt anschliessend `TradingBot 8.2.1 NEXUS`.

## 4. Verbindungen und einen Kryptozyklus pruefen

```bash
./Nexus_Einrichten.sh --test
./Nexus_Starten.sh --einmal
./Pi_Service_Status.sh
```

Der Einmal-Zyklus ist eine Prüfung. Er erzeugt keine Live-Order, solange der
Handelsmodus nicht bewusst auf LIVE gestellt und separat freigegeben wurde.

## 5. Erst danach dauerhaft aktivieren

```bash
./Pi_Service_Aktivieren.sh
```

## Favoriten

In der lokalen GUI ist `auto` die normale Wahl. Beispiele:

- `AAPL` → eToro-Aktie
- `BTC`, `XRP`, `SOL` oder `BTC-EUR` → OKX-Spot-Krypto

Ein Favorit wird zuerst nur bevorzugt geprüft. Ob er ins Universum kommt und
später gekauft werden darf, entscheiden weiterhin die normalen Broker-,
Liquiditäts-, Ranking-, Bewährungs-, Signal-, Kosten- und Risikoregeln.
