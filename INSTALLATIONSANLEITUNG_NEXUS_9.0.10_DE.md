# Update auf NEXUS 9.0.10

NEXUS 9.0.10 behebt die falsche OKX-Equity-Bremse aus 9.0.9. Das Update muss
als eigener Ordner installiert werden; der bisherige Ordner bleibt als
Rueckfallkopie erhalten.

```bash
cd ~/Georg/TradingBot_v9.0.9_NEXUS
./Pi_Service_Stoppen.sh

cd ~/Georg
unzip TradingBot_v9.0.10_NEXUS.zip
cd TradingBot_v9.0.10_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python volltest.py
./Pi_Service_Starten.sh
```

Anschliessend pruefen:

```text
/version
/health
/crypto status
/decisions
```

Erwartet wird `9.0.10-NEXUS`. Beim ersten OKX-Zyklus wird eine alte oder
abweichende Equity-Berechnungsbasis kontrolliert auf den aktuellen Wert neu
gesetzt. Es werden keine Zugangsdaten, Trades oder Kontobestaende geloescht.

