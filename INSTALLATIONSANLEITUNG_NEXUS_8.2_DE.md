# NEXUS 8.2 installieren – Kurzleitfaden

NEXUS 8.2 ist ein Update auf **8.1.5**. Die WebUI und die
Einstellungsübernahme bleiben erhalten. Der Installer ändert weder WireGuard
noch dessen Schlüssel, Peers oder Portfreigaben.

## 1. Alte Version sichern und stoppen

```bash
cd ~/Georg
cp -a TradingBot_v8.1.5_NEXUS TradingBot_v8.1.5_NEXUS_Sicherung_$(date +%Y%m%d-%H%M)
cd TradingBot_v8.1.5_NEXUS
./Pi_Service_Stoppen.sh
```

Die alte Version nicht löschen. Sie bleibt die Rückfallmöglichkeit.

## 2. Paket entpacken und installieren

```bash
cd ~/Georg
unzip TradingBot_v8.2_NEXUS.zip
cd TradingBot_v8.2_NEXUS
chmod +x Pi_Installieren.sh Nexus_*.sh Pi_*.sh
./Pi_Installieren.sh
```

`Pi_Installieren.sh` niemals mit `sudo` starten. Der Installer führt den
Offline-Volltest aus und lässt den Handelsdienst danach absichtlich gestoppt.

## 3. Einstellungen übernehmen und prüfen

```bash
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python webui_setup.py
./Pi_WebUI_Aktivieren.sh
./Nexus_Einrichten.sh --test
./.venv/bin/python crypto_diagnose.py --voll
```

In der WebUI kontrollieren:

- eToro steht auf **PAPER** und OKX auf **DEMO**;
- primäre OKX-Quote ist **EUR** (USDC ist zusätzlich erlaubt);
- unter **Positionen → OKX · Konto-Guthaben** sind Demo-Startwerte als
  Guthaben/„Externer Bestand“ sichtbar, nicht als offene Bot-Orders;
- Telegram, MASSIVE und die gewünschten Newsquellen sind eingerichtet.

Die Migration übernimmt bestehende Einstellungen, entfernt aber den nicht mehr
verwendeten `finanzen.net`-Schalter. Die geprüften Alternativen im Standardpfad
sind SEC EDGAR, Nasdaq Halts, Yahoo Finance, Google News, GDELT und – bei
eingerichtetem Key – MASSIVE.

Die optionale GPT-Zweitmeinung bleibt rein informativ: Auch bei „kritisch“
kommt nur eine Telegram-Warnung. Sie kann keinen Kauf freigeben, stoppen oder
auslösen; Verkauf, Stop und Schutzorder warten nie auf KI.

## 4. Erst danach bewusst starten

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

Nützlich für die Abnahme:

```bash
sudo journalctl -u tradingbot-pi5.service -f
```

Nach einem normalen Neustart darf keine zweite Nachricht „OKX:
handelsbereit“ erscheinen. Sie erscheint nur beim ersten erfolgreichen Start
oder nachdem die Handelsbereitschaft wirklich verloren ging und zurückkehrt.

## Rückfall

Wenn Installation oder Diagnose nicht sauber durchlaufen: neuen Dienst
deaktiviert lassen, in den Sicherungsordner wechseln und dort den bisherigen
Dienst starten. Keine OKX-Keys oder Passphrasen in Telegram, Screenshots oder
einen Chat kopieren.
