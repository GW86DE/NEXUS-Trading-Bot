# Installation und Update auf NEXUS 9.0.8

## Sicheres Update auf dem Raspberry Pi 5

1. Die laufenden Dienste der Version 9.0.7 stoppen:

```bash
cd ~/Georg/TradingBot_v9.0.7_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
```

2. NEXUS 9.0.8 neben dem alten Ordner entpacken und installieren:

```bash
cd ~/Georg
unzip TradingBot_v9.0.8_NEXUS.zip
cd TradingBot_v9.0.8_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh
./Pi_Installieren.sh
```

3. Einstellungen und lokale Zustände übernehmen und offline prüfen:

```bash
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
./Nexus_Einrichten.sh --test
```

4. Erst anschließend die Dienste aktivieren:

```bash
./Pi_Service_Aktivieren.sh
```

## Kontrolle im Logbuch

- Es darf kein `NameError: OrderStatusUnklar is not defined` mehr erscheinen.
- Ein eToro-Datenabruf darf nicht als globaler Verbindungsverlust erscheinen.
- Der Scanner kann wegen der gleichmäßigen API-Taktung etwas länger brauchen;
  das ist beabsichtigt und schützt vor HTTP 429.
- Die OKX-Zeile soll Konto-Assets, Botpositionen und Cash getrennt ausweisen.

Das Update übernimmt die 9.0.7-Währungsregeln unverändert: EUR ist primär,
USDC zusätzlich erlaubt. eToro bleibt PAPER und OKX bleibt DEMO.

