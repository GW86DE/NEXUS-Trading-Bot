# TradingBot NEXUS 9.0.1 installieren oder aktualisieren

## Wichtig vor dem Update

NEXUS 9.0.1 wird in einen **neuen Ordner** installiert. Den laufenden
9.0-Ordner nicht überschreiben. Die Migration übernimmt Zustände und setzt
eToro weiterhin auf PAPER sowie OKX auf DEMO; LIVE wird nie automatisch
aktiviert.

## 1. Alte Dienste stoppen und sichern

```bash
cd ~/Georg/TradingBot_v9.0_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
sudo systemctl stop tradingbot-webui.service 2>/dev/null || true
cp -a . ~/Georg/Backup_TradingBot_v9.0_$(date +%Y%m%d_%H%M)
```

## 2. Neue Version entpacken

```bash
cd ~/Georg
unzip TradingBot_v9.0.1_NEXUS.zip
cd TradingBot_v9.0.1_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
```

## 3. Installieren und migrieren

```bash
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Übernommen werden insbesondere Zugangsdaten, Entscheidungsdatenbank,
Trade-Ledger, offene Positionen, Reconciliation, Krypto-Strategiemodus,
Universumszustand sowie die neue DYNAMIC_30-Mitgliedschaft und deren lokale
Tageshistorie. Beim ersten Update von 9.0 existieren die beiden DYNAMIC_30-
Dateien noch nicht; sie werden beim nächsten erfolgreichen OKX-Universumslauf
automatisch angelegt.

## 4. Offline prüfen

```bash
cat VERSION.txt
./.venv/bin/python volltest.py
./.venv/bin/python self_test.py
./Nexus_Einrichten.sh --test
./Pi_Service_Unit_Pruefen.sh
```

Erwartete Version: `9.0.1-NEXUS`. Diese Prüfungen senden keine echte Order.

## 5. Dienste starten

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Starten.sh
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

In der WebUI prüfen:

- Kopfzeile zeigt 9.0.1;
- eToro steht auf PAPER und OKX auf DEMO;
- unter **Universum** sind 20 feste Kernwerte und bis zu 30 dynamische Werte
  getrennt erklärt;
- neue dynamische Werte stehen zunächst in BEOBACHTUNG;
- unter **Trades** sind Ledgerdaten und für OKX abgeschlossene Kerzen mit
  Kauf-/Verkaufsmarkierung sichtbar.

## 6. Verhalten nach Neustart

Die vorhandene 15-minütige Anlaufsperre bleibt aktiv. In dieser Zeit laufen
Abgleich, Stops, Schutzorders und Verkäufe weiter; neue Kryptoeinstiege sind
gesperrt. Unvollständige Kerzen, fehlende Bid-/Ask-Werte, ein zu großer
Ausführungs-Spread oder fehlende Handelsregeln blockieren einen Kauf weiterhin.

## Rückweg

Vor einem Rückweg auf 9.0 zuerst `/cryptopause` verwenden, offene Orders und
Positionen beim Broker prüfen und den vollständigen 9.0.1-Ordner sichern. Eine
ältere Version kennt die neue DYNAMIC_30-Historie und Trade-Webseite nicht,
verändert aber nicht automatisch Brokerpositionen.
