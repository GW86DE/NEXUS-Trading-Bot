# Update auf NEXUS 9.0.11

NEXUS 9.0.11 erweitert das ausführbare OKX-Spotuniversum um einen vollständig
getrennten USD-Handelskanal und führt die breite dynamische Marktfindung ein.
Der bisherige Ordner bleibt als Rückfallkopie erhalten.

```bash
cd ~/Georg/TradingBot_v9.0.10_NEXUS
./Pi_Service_Stoppen.sh

cd ~/Georg
unzip TradingBot_v9.0.11_NEXUS.zip
cd TradingBot_v9.0.11_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python volltest.py
./Pi_Service_Starten.sh
```

Anschließend prüfen:

```text
/version
/health
/crypto status
/decisions
```

Erwartete Version: `9.0.11-NEXUS`.

Beim ersten OKX-Universumslauf wird die dynamische Liste einmalig auf die neue
Währungsrichtlinie migriert. Vorhandene Trades, Bot-Eigentumsnachweise,
Zugangsdaten und Konto-Assets werden nicht gelöscht. Die Bestände BTC, ETH,
SOL und XRP werden dadurch nicht zu Bot-Trades.

In der Universumsdiagnose sollten anschließend getrennt erscheinen:

- öffentliche OKX-SPOT-Märkte;
- für das konkrete Konto verfügbare Instrumente;
- durch Sicherheitsfilter geeignete Basen;
- aktive EUR-, USD- und USDC-Handelskanäle samt freiem Guthaben.

USD wird nur genutzt, wenn OKX es in `tradeQuoteCcyList` des Instruments meldet
und tatsächlich freies USD vorhanden ist. USDT bleibt deaktiviert.
