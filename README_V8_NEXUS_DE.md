# Historische README zu NEXUS 8.0

Für das aktuelle Release bitte `README_V8_1_1_NEXUS_DE.md` und
`INSTALLATIONSANLEITUNG_NEXUS_8.1.1_DE.md` verwenden. Dieses Dokument bleibt
nur als Architektur-/Migrationsreferenz der Vorgängerversion erhalten.

# TradingBot 8.0 NEXUS

NEXUS betreibt zwei getrennte Handelsdomaenen gleichzeitig:

- **eToro Aktien:** nur waehrend des US-Handelsfensters, 75 feste Kernwerte plus bis zu 25 per Telegram freigegebene dynamische Werte.
- **OKX EEA Krypto:** Spot/Cash, 24/7, EUR primaer und USDC als Ausweichpaar, Kern BTC/ETH/SOL plus automatisch gepflegtes dynamisches Universum.

Die WebUI ist die primaere Oberflaeche. Die bestehende Tkinter-GUI bleibt als lokaler Wartungs-Fallback erhalten. Der Trading-Core und die WebUI laufen als getrennte Dienste; ein Browser-/WebUI-Fehler stoppt die Handels- und Schutzlogik nicht.

## Sicherheitsmodell

- Erstinstallation und jede Einstellungsuebernahme starten eToro und OKX in Paper/Demo.
- OKX LIVE erfordert einen Live-Key nur mit **Read + Trade**, niemals Withdraw.
- Die dauerhafte Live-Moduswahl allein reicht nicht: neue OKX-LIVE-Einstiege brauchen eine maximal 60 Minuten, standardmaessig 15 Minuten gueltige lokale Freigabe.
- Das Arming gilt nur fuer Einstiege. Ausstiege und Schutzorders bleiben erlaubt.
- Krypto nutzt ausschliesslich Spot/Cash (`tdMode=cash`), kein Margin, keine Futures, Swaps oder Hebel-Token.
- Kontostand und Orderereignisse kommen bevorzugt ueber den privaten
  OKX-EEA-WebSocket. Ist er getrennt oder veraltet, faellt der Broker
  automatisch auf REST zurueck; Orders selbst laufen weiterhin nur ueber
  den geprueften REST-Geldpfad.
- KI kann Aufmerksamkeit priorisieren und Quellen dokumentieren, aber kein Handels-Gate freigeben oder blockieren.
- Aktienfavoriten umgehen die Telegram-Freigabe nicht.
- Die WebUI bietet keine Order- und keine Live-Arming-Route.

## Installation auf Raspberry Pi 5 (8 GB)

```bash
chmod +x Pi_Installieren.sh
./Pi_Installieren.sh
```

Der Installer startet danach noch nichts. Anschliessend:

```bash
./.venv/bin/python settings_migration.py --auto
./.venv/bin/python webui_setup.py
./Nexus_Einrichten.sh
./Nexus_Einrichten.sh --test
./Pi_Service_Aktivieren.sh
```

Die Migration uebernimmt vorhandene Schluessel, Profile, Favoriten und Historie,
setzt aber beide Broker auf Paper/Demo und entfernt alte Live-Freigaben. Das
Ergebnis wird ohne geheime Werte in `migration_report.json` protokolliert.

## WebUI

Lokal: `http://127.0.0.1:8780`

Fuer iPhone/Laptop bitte [WIREGUARD_VPN_EINRICHTUNG_DE.md](WIREGUARD_VPN_EINRICHTUNG_DE.md) verwenden. Die WebUI lehnt `0.0.0.0`/`::` bewusst ab.

Seiten:

- Dashboard: Broker-/Marktstatus, Botzustand, Positionen, Entscheidungen und echte/passive API-Gesundheit.
- Einstellungen: eToro, OKX, Telegram, OpenAI/Luna/Terra, Finnhub, FMP, Alpha Vantage, MASSIVE und VPN-Bind.
- Logbuch: serverseitige Filter nach Broker, Anlageklasse, Status, Quelle, Symbol und Datum sowie ein separates Systemprotokoll.

Der Login ist lokal, gesalzen und PBKDF2-geschuetzt. Nach fuenf Fehlversuchen
innerhalb von zehn Minuten wird die Quell-IP voruebergehend gedrosselt. Die
WebUI bindet nur an Loopback- oder private/VPN-Adressen.

## KI-Router

Alle intelligenten Rollen laufen ueber einen zentralen, kostenbewussten Router:

- **Luna:** haeufige, klar strukturierte Aufgaben wie Fokus-Ranking,
  Batch-Einstufung, News-Relevanz und Duplikaterkennung.
- **Terra:** seltene, anspruchsvolle Aufgaben wie Anomalien, Widersprueche,
  Krypto-Events, Wochen-Research und Strategieauswertung.

Cache, Tagesbudgets, Kostenprotokoll und Terra-zu-Luna-Fallback begrenzen die
Ausgaben. KI darf weder Orders ausloesen noch ein deterministisches
Sicherheits-Gate freigeben oder blockieren.

## Live-Freigabe OKX

Nach erfolgreichem Demo-/Paper-Test:

```bash
./.venv/bin/python broker_live_arming.py okx arm --minutes 15
```

Status bzw. sofort sperren:

```bash
./.venv/bin/python broker_live_arming.py okx status
./.venv/bin/python broker_live_arming.py okx disarm
```

Der eToro-Live-Modus und sein separates Arming werden weiterhin ueber
`handelsmodus.py` verwaltet. Eine Freigabe gilt niemals brokeruebergreifend.

## Tests

```bash
./.venv/bin/python volltest.py
./Nexus_Einrichten.sh --test
./Nexus_Starten.sh --einmal
```

Offline-Tests senden keine Orders, Telegram-Nachrichten oder KI-Anfragen. Erst der ausdrueckliche Verbindungstest nutzt reale APIs.
