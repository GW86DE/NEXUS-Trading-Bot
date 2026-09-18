# Installation und Update auf NEXUS 9.0.5

## Vor dem Update

1. Bot und WebUI stoppen.
2. Den bisherigen NEXUS-Ordner vollständig sichern.
3. Auf dem Raspberry Pi prüfen:

```bash
timedatectl status
```

`System clock synchronized: yes` und `NTP service: active` müssen sichtbar
sein. Bei falscher Uhr zuerst NTP/Netzwerk reparieren; nicht die API-Schlüssel
wechseln.

## Installation

ZIP in einen neuen Ordner entpacken und dort ausführen:

```bash
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
./Nexus_Einrichten.sh --test
./Pi_Service_Aktivieren.sh
```

Die Migration übernimmt Konfiguration und Positionsdaten nach den bestehenden
Sicherheitsregeln. Demo/Live wird nicht still umgeschaltet. API-Schlüssel
gehören nicht in das ZIP, sondern werden lokal über die Einrichtung/WebUI
gespeichert.

## Kontrolle nach dem Start

- Dashboard: **Worker aktiv**, **Broker authentifiziert**, frischer Heartbeat.
- Logbuch: regelmäßige OKX-Guthaben-/Positions- oder Kryptozyklusmeldungen.
- Einstellungen: „Demo API einmalig testen“ darf grün sein; entscheidend für
  den Dauerbetrieb ist zusätzlich der Workerstatus auf dem Dashboard.
- Bei einer Zeitabweichung bleibt der Worker aktiv und meldet den Fehler, bis
  NTP wieder stimmt; eToro läuft unabhängig weiter.

## Rückkehr zur Vorversion

Bot stoppen und den vollständig gesicherten alten Ordner wieder einsetzen.
Keine einzelne alte `crypto_positions.json` in die neue Version kopieren, da
Ledger, Broker-Fingerabdruck und Positionsbuch zusammengehören.

