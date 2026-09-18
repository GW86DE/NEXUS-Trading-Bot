# Update auf NEXUS 9.0.13

NEXUS 9.0.13 behebt den DOGE-Sofortverkauf, die ONDO/LINK-Fill-Kollision,
falsche Verbindungsabbruch-Meldungen und blockierende Rundungsreste. Der alte
Ordner bleibt als Rückfallkopie erhalten.

## 1. Alte Version stoppen

```bash
cd ~/Georg/TradingBot_v9.0.12_NEXUS
./Pi_Service_Stoppen.sh
```

## 2. Neue Version installieren

```bash
cd ~/Georg
unzip TradingBot_v9.0.13_NEXUS.zip
cd TradingBot_v9.0.13_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration übernimmt Einstellungen und Laufzeitdateien. Die Datenbank wird
beim ersten Zugriff um die neue Instrument-/Order-/Fill-Identität erweitert.
Bestehende Historien werden nicht gelöscht.

## 3. Vor dem Start vollständig testen

```bash
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
```

Erwartet: `SELF TEST OK` und `VOLLTEST OK` für `9.0.13-NEXUS`.

## 4. Dienste starten

```bash
./Pi_Service_Starten.sh
```

## 5. Kontrolle

In Telegram:

```text
/version
/health
/crypto status
/decisions
```

In der WebUI prüfen:

- **Offene Trades:** BNB, LINK, XLM und ONDO nur dann, wenn die belegten
  Positionen noch im aktuellen OKX-Demokonto vorhanden sind.
- **DOGE:** kein offener Trade und keine Klärung wegen 0,06235 DOGE Staub.
- **Grund:** bei offenen Trades steht der Zustand des OKX-Schutzes.
- **Klärung nötig:** kein ONDO-Eintrag wegen einer LINK-Fill-ID-Kollision.
- **Logbuch:** lokale Europe/Berlin-Zeit und 15 Einträge je Seite.

Im Systemprotokoll sollten beim Start einmalig Hinweise zur Ledgermigration
und zum Schutzabgleich erscheinen. Ein einzelner OKX-Timeout darf nur
`DEGRADED` melden; erst mehrere Fehler in Folge führen zu `OFFLINE`.

## 6. Kontrollierter DEMO-Test

eToro bleibt **PAPER**, OKX bleibt **DEMO**. Beobachte mindestens einen
vollständigen Kryptozyklus. Vor einer echten Kaufgelegenheit müssen die
Entscheidungen weiterhin alle fünf Minuten protokolliert werden.

Bei einem OKX-Demokauf muss gelten:

1. Kauforder ist `FOK` mit Preisgrenze.
2. Es gibt genau eine bestätigte Einstiegsorder.
3. Positionsbuch und Trade-Ledger enthalten dieselbe ordId/clOrdId/Fill-Kette.
4. Danach existiert genau eine Schutzorder mit algoId.
5. Die WebUI meldet `OKX-Schutz aktiv`.

Erst nach einem fehlerfreien DEMO-/PAPER-Lauf über LIVE sprechen.

