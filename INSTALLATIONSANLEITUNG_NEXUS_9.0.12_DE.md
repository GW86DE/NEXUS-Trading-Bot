# Update auf NEXUS 9.0.12

NEXUS 9.0.12 korrigiert das OKX-Universum, die Trennung von Konto-Assets und
Botpositionen, die SOL-/ETH-Legacyanzeige, Telegram und die Logbuchzeiten. Der
bisherige Ordner bleibt als Rückfallkopie erhalten.

```bash
cd ~/Georg/TradingBot_v9.0.11_NEXUS
./Pi_Service_Stoppen.sh

cd ~/Georg
unzip TradingBot_v9.0.12_NEXUS.zip
cd TradingBot_v9.0.12_NEXUS
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

Erwartete Version: `9.0.12-NEXUS`.

## Kontrolle des OKX-Universums

Nach dem ersten vollständigen OKX-Lauf muss die Diagnose getrennt nennen:

- öffentlich sichtbare OKX-SPOT-Märkte;
- für dein konkretes Demo-/Live-Konto verfügbare SPOT-Instrumente;
- geeignete Basiswerte;
- Kern `x/20`, dynamischen Anteil und aktiven Pool;
- finanzierte EUR-, USD- und USDC-Kanäle.

Die öffentliche Zahl ist keine Handelsfreigabe. Nur der authentifizierte
Kontokatalog und `tradeQuoteCcyList` bestimmen, was dein Bot bestellen darf.
Kleine positive Umsätze werden jetzt gerankt statt pauschal unter 250.000 EUR
ausgeschlossen.

## SOL/ETH einmalig bereinigen

Öffne in der WebUI **Trades → Klärung nötig**. Für jeden unbelegten alten
SOL-/ETH-Eintrag gibt es:

1. **Erneut mit OKX prüfen** – zuerst verwenden, wenn der Brokerbeweis noch
   nachkommen könnte.
2. **Als Konto-Asset bestätigen** – wenn der Coin im Konto liegt, aber kein
   vom Bot verwalteter Trade ist.
3. **Eintrag löschen** – wenn der lokale Hinweis nicht mehr existieren soll.

Nach der Bestätigung verarbeitet der OKX-Kern die Aktion im nächsten Zyklus.
Die Aktion verändert niemals dein OKX-Guthaben und sendet keine Order. Vor
einer lokalen Bereinigung wird unter `state_backups/` eine Sicherung angelegt.
Bestaetigte Botpositionen, echte Fill-Beweise und offene eigene Orders sind
gegen diese Funktion gesperrt.

## Erwartete Anzeige

- **Offene Trades:** nur Broker-/Fill-bestätigte Bottrades.
- **OKX Konto-Assets:** vorhandene BTC/ETH/SOL/XRP-Bestände, die nicht vom Bot
  verwaltet oder verkauft werden.
- **Klärung nötig:** nur noch tatsächlich unbelegte lokale Hinweise.
- **Logbuch:** 15 Entscheidungen je Seite, Zeit in Europe/Berlin.
- **Telegram:** keine doppelte `BLOCKED`-Ausgabe und keine Warnung wegen eines
  normalen ETH-Kontoüberhangs.

eToro bleibt PAPER und OKX bleibt DEMO, bis der kontrollierte Praxistest ohne
ungeklärte Orders, falsche Positionen oder Währungsabweichungen abgeschlossen
ist.
